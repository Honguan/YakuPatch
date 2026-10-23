"""Codex (ChatGPT subscription) backend, experimental: the endpoint is not a public API (PROJECT_PLAN §6.6, §16).

Endpoints, OAuth parameters and the Responses request shape are ported from BallonsTranslator
`ballontranslator/modules/codex.py` (GPL-3.0). Credentials live only in the system keyring and never
touch the Codex CLI's ~/.codex/auth.json.
"""
import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, replace
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from . import AuthRequired, BackendCaps, ConfigError, Refused, TooLong, Transient, UsageLimit

API_URL = "https://chatgpt.com/backend-api/codex"
AUTH_URL = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# shortcut: fixed client version (BallonsTranslator polls GitHub releases hourly); bump it if the endpoint rejects old clients.
CLIENT_VERSION = "0.159.0"
KEYRING_SERVICE = "YakuPatch Codex"
_CHUNK = 1000          # Windows Credential Manager stores at most 2560 bytes (UTF-16) per entry
_PORTS = (1455, 1457)  # redirect URIs registered for this client id

INSTRUCTIONS = {
    "zh-Hant": "你是遊戲翻譯。把 items 裡每個 text 從日文翻譯成自然流暢的繁體中文（台灣用語）。",
    "zh-Hans": "你是游戏翻译。把 items 里每个 text 从日文翻译成自然流畅的简体中文。",
}
RULES = """規則：
1. <0>、<1>、⟦0⟧ 這類標記是遊戲控制碼，原樣保留，數量、編號與位置對應原文，不可增減。
2. text 有幾個換行，譯文就要有幾個換行。
3. glossary 裡的詞一律使用指定譯名；speaker 是說話者，context 是前文，只供參考，不要翻譯或輸出。
4. 每個 item 都要回傳，id 與輸入相同；只輸出譯文，不加說明、引號或註解。"""
SCHEMA = {"name": "translations", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["translations"],
    "properties": {"translations": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "translation"],
        "properties": {"id": {"type": "string"}, "translation": {"type": "string"}}}}}}}


@dataclass(frozen=True)
class CodexConfig:
    model: str = ""
    effort: str = "low"
    parallel: int = 4
    timeout: float = 300
    retries: int = 3
    output_script: str = "zh-Hant"
    max_items: int = 80
    max_input_tokens: int = 4000

    def __post_init__(self):
        if self.output_script not in INSTRUCTIONS:
            raise ValueError("Codex 輸出字體只能是 zh-Hant 或 zh-Hans")
        if not 0 <= self.retries <= 5 or self.parallel < 1 or self.max_items < 1 or self.max_input_tokens < 200:
            raise ValueError("重試次數須為 0–5，平行數、每批句數須大於零，輸入預算至少 200")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Codex 逾時必須是正數")


class Credentials:
    """Token set in the keyring, split into chunks; a pointer entry flips only after the new chunks are written."""

    def __init__(self, keyring_module=None):
        self._keyring = keyring_module
        self.lock = threading.Lock()   # serialises refresh: a used refresh token is invalidated by the server

    @property
    def keyring(self):
        if self._keyring is None:
            import keyring
            self._keyring = keyring
        return self._keyring

    def load(self) -> dict | None:
        pointer = self.keyring.get_password(KEYRING_SERVICE, "current")
        if not pointer:
            return None
        try:
            gen, count = pointer.split(":")
            data = json.loads("".join(self.keyring.get_password(KEYRING_SERVICE, f"{gen}-{i}") for i in range(int(count))))
        except (ValueError, TypeError):
            raise AuthRequired("Codex 憑證損毀，請重新登入：yakupatch codex login") from None
        if not _valid(data):
            raise AuthRequired("Codex 憑證不完整，請重新登入：yakupatch codex login")
        return data

    def save(self, data: dict) -> None:
        if not _valid(data):
            raise AuthRequired("Codex 回傳的憑證不完整，請重新登入")
        old = self.keyring.get_password(KEYRING_SERVICE, "current")
        gen, body = secrets.token_hex(4), json.dumps(data)
        parts = [body[i:i + _CHUNK] for i in range(0, len(body), _CHUNK)]
        for i, part in enumerate(parts):
            self.keyring.set_password(KEYRING_SERVICE, f"{gen}-{i}", part)
        self.keyring.set_password(KEYRING_SERVICE, "current", f"{gen}:{len(parts)}")
        if self.keyring.get_password(KEYRING_SERVICE, "current") != f"{gen}:{len(parts)}":
            raise AuthRequired("系統認證管理員沒有保存 Codex 憑證")
        if old:
            self._delete(old)

    def clear(self) -> None:
        old = self.keyring.get_password(KEYRING_SERVICE, "current")
        if old:
            self.keyring.delete_password(KEYRING_SERVICE, "current")
            self._delete(old)

    def _delete(self, pointer: str) -> None:
        try:
            gen, count = pointer.split(":")
            for i in range(int(count)):
                self.keyring.delete_password(KEYRING_SERVICE, f"{gen}-{i}")
        except Exception:   # stale chunks are harmless; the pointer already moved on
            pass


def _valid(data) -> bool:
    return (isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k]
                                           for k in ("access_token", "refresh_token", "account_id"))
            and isinstance(data.get("expires_at"), (int, float)) and not isinstance(data["expires_at"], bool))


def _claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data if isinstance(data, dict) else {}
    except (ValueError, IndexError, UnicodeDecodeError):
        return {}


def token_set(payload: dict, previous: dict | None = None) -> dict:
    """Merge a token endpoint answer into stored credentials (account id, email and expiry come from the JWTs)."""
    data = dict(previous or {})
    for key in ("access_token", "refresh_token", "id_token"):
        if isinstance(payload.get(key), str) and payload[key]:
            data[key] = payload[key]
    ident, access = _claims(data.get("id_token", "")), _claims(data.get("access_token", ""))
    auth, access_auth = ident.get("https://api.openai.com/auth", {}), access.get("https://api.openai.com/auth", {})
    data["account_id"] = auth.get("chatgpt_account_id") or access_auth.get("chatgpt_account_id") or data.get("account_id", "")
    data["email"] = ident.get("email") or data.get("email", "")
    exp = access.get("exp")
    data["expires_at"] = exp if isinstance(exp, (int, float)) else time.time() + float(payload.get("expires_in") or 3600)
    return data


def _post_form(url: str, form: dict, timeout: float = 30) -> dict:
    request = Request(url, data=urlencode(form).encode("ascii"),
                      headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as ex:
        if ex.code in (400, 401, 403):
            raise AuthRequired("Codex 登入已失效，請重新登入：yakupatch codex login") from None
        raise Transient(f"Codex 登入伺服器 HTTP {ex.code}") from None
    except (HTTPException, URLError, TimeoutError, OSError, ValueError):
        raise Transient("無法連線到 Codex 登入伺服器") from None


def login(credentials: Credentials, open_url=webbrowser.open, timeout: float = 180) -> dict:
    """OAuth PKCE sign-in: the browser returns to http://localhost:<port>/auth/callback on 127.0.0.1 only."""
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            ok = url.path == "/auth/callback" and hmac.compare_digest(query.get("state", [""])[0], state)
            if ok and query.get("code", [""])[0]:
                result["code"] = query["code"][0]
            elif ok and query.get("error"):
                result["error"] = True
            body = ("登入完成，可以關閉此頁回到 YakuPatch。" if "code" in result else "登入未完成。").encode("utf-8")
            self.send_response(200 if "code" in result else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):   # never log the callback URL (it carries the code)
            pass

    server = None
    for port in _PORTS:
        try:
            server = HTTPServer(("127.0.0.1", port), Callback)
            break
        except OSError:
            continue
    if server is None:
        raise ConfigError("Codex 登入需要本機埠 1455 或 1457，請關閉其他登入視窗後重試")
    redirect = f"http://localhost:{port}/auth/callback"
    with server:
        server.timeout = 1
        open_url(AUTH_URL + "/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect,
            "scope": "openid profile email offline_access", "code_challenge": challenge,
            "code_challenge_method": "S256", "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true", "state": state, "originator": "codex_cli_rs"}))
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in result:
        raise AuthRequired("Codex 登入被取消或逾時，請重新登入")
    payload = _post_form(AUTH_URL + "/oauth/token", {"grant_type": "authorization_code", "client_id": CLIENT_ID,
                                                     "redirect_uri": redirect, "code": result["code"],
                                                     "code_verifier": verifier})
    data = token_set(payload)
    with credentials.lock:
        credentials.save(data)
    return data


class Codex:
    prompt_version = "c1"

    def __init__(self, config: CodexConfig = CodexConfig(), credentials: Credentials | None = None, opener=urlopen):
        self.config = config
        self.credentials = credentials or Credentials()
        self.model = config.model
        self.models: dict[str, list[str]] = {}
        self.cache_key = secrets.token_hex(16)   # one prompt_cache_key per translation job
        self._open = opener
        self.caps = BackendCaps(io_format="json_ids", max_input_tokens=config.max_input_tokens, max_items=config.max_items,
                                max_parallel=config.parallel, output_script=config.output_script, summary=True,
                                style=True, metered=True)

    @property
    def by(self):
        return f"codex:{self.model or 'auto'}/{self.config.effort}-{self.prompt_version}"

    @property
    def cache_identity(self):
        cfg = asdict(self.config)
        for key in ("parallel", "timeout", "retries"):   # do not change the translation
            cfg.pop(key)
        return {"by": self.by, "config": cfg}

    def account(self) -> dict:
        data = self.credentials.load()
        if data is None:
            raise AuthRequired("尚未登入 Codex，請先執行：yakupatch codex login")
        return {"email": data.get("email", ""), "account_id": data["account_id"]}

    def prepare(self):
        self.account()
        self.models = self.list_models()
        if not self.model:
            if not self.models:
                raise ConfigError("Codex 沒有可用的模型")
            self.model = next(iter(self.models))
        if self.model not in self.models:
            raise ConfigError(f"Codex 沒有這個模型：{self.model}（可用：{', '.join(self.models)}）")
        efforts = self.models[self.model]
        if efforts and self.config.effort not in efforts:
            raise ConfigError(f"模型 {self.model} 不支援推理等級 {self.config.effort}（可用：{', '.join(efforts)}）")
        self.caps = replace(self.caps, max_parallel=self.config.parallel)

    def list_models(self) -> dict[str, list[str]]:
        obj = json.loads(self._send(API_URL + "/models?" + urlencode({"client_version": CLIENT_VERSION}), None))
        try:
            return {m["slug"]: [e["effort"] for e in m.get("supported_reasoning_levels") or [] if isinstance(e, dict)]
                    for m in obj["models"] if isinstance(m, dict) and m.get("visibility", "list") == "list"}
        except (KeyError, TypeError):
            raise ConfigError("Codex 模型清單格式錯誤") from None

    def messages(self, works, glossary, context=True):
        terms = {k: v for w in works for k, v in w.terms.items()}
        body = {"glossary": [{"src": s, "dst": d, **({"note": n} if n else {})}
                             for s, (d, n) in sorted(terms.items(), key=lambda kv: -len(kv[0]))],
                "context": works[0].context if context else [],
                "items": [{"id": str(i), "text": w.text, **({"speaker": w.speaker} if w.speaker else {})}
                          for i, w in enumerate(works)]}
        return [{"role": "system", "content": INSTRUCTIONS[self.config.output_script] + "\n" + RULES},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]

    def complete(self, messages, temperature=None):
        """`temperature` is ignored: reasoning models reject it; the hot retry is just a fresh attempt."""
        payload = {"model": self.model, "instructions": messages[0]["content"],
                   "input": [{"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": messages[1]["content"]}]}],
                   "tools": [], "tool_choice": "none", "store": False, "stream": True,
                   "prompt_cache_key": self.cache_key, "reasoning": {"effort": self.config.effort},
                   "text": {"format": {"type": "json_schema", **SCHEMA}}}
        return final_text(self._send(API_URL + "/responses", payload, stream=True))

    def _tokens(self, rejected: str = "") -> dict:
        with self.credentials.lock:
            data = self.credentials.load()
            if data is None:
                raise AuthRequired("尚未登入 Codex，請先執行：yakupatch codex login")
            if data["expires_at"] <= time.time() + 60 or data["access_token"] == rejected:
                data = token_set(_post_form(AUTH_URL + "/oauth/token", {
                    "grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": CLIENT_ID}), data)
                self.credentials.save(data)   # store the rotated refresh token before using it
            return data

    def _send(self, url, payload, stream=False) -> str:
        rejected = ""
        for attempt in range(2):
            tokens = self._tokens(rejected)
            headers = {"Authorization": "Bearer " + tokens["access_token"], "ChatGPT-Account-Id": tokens["account_id"],
                       "originator": "codex_cli_rs", "version": CLIENT_VERSION,
                       "User-Agent": f"codex_cli_rs/{CLIENT_VERSION} (YakuPatch)", "Accept": "application/json"}
            if stream:
                headers.update({"Accept": "text/event-stream", "Content-Type": "application/json",
                                "OpenAI-Beta": "responses=experimental", "session-id": self.cache_key,
                                "x-client-request-id": self.cache_key})
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
            try:
                with self._open(Request(url, data=data, headers=headers), timeout=self.config.timeout) as response:
                    return read_events(response) if stream else response.read().decode("utf-8")
            except HTTPError as ex:
                if ex.code == 401 and attempt == 0:
                    rejected = tokens["access_token"]
                    continue
                try:
                    status, detail = ex.code, ex.read().decode("utf-8", "replace")
                except HTTPException:
                    status, detail = ex.code, ""
            except (HTTPException, URLError, TimeoutError, OSError):
                raise Transient("Codex 連線失敗或逾時") from None
            raise_for(status, detail)       # outside `except`: the HTTPError (URL, headers) is not chained
        raise AuthRequired("Codex 登入已失效，請重新登入：yakupatch codex login")


def raise_for(status: int | None, detail: str) -> None:
    """Map a Codex error to §6.4 categories. Server text is inspected, never logged or shown."""
    text = detail.lower()
    if status == 401 or any(w in text for w in ("invalid_token", "token_expired", "refresh_token", "authentication")):
        raise AuthRequired("Codex 登入已失效，請重新登入：yakupatch codex login")
    if any(w in text for w in ("usage_limit", "usage limit", "quota", "insufficient", "rate_limit_exceeded_for_plan")):
        raise UsageLimit("Codex 額度已用完，進度已保留；額度恢復後重跑 translate 即可續翻")
    if any(w in text for w in ("context_length", "context window", "too long", "max_output_tokens")) or status == 413:
        raise TooLong("Codex 輸入或輸出超出限制")
    if status in (429, None) or (status or 0) >= 500:
        raise Transient(f"Codex HTTP {status}")
    if status == 403:
        raise ConfigError("這個 ChatGPT 帳號沒有 Codex 權限")
    raise ConfigError(f"Codex 拒絕請求（HTTP {status}），請檢查模型與推理等級")


def read_events(response) -> str:
    """Read the SSE stream until the final response object; returns it as JSON text."""
    items, data = [], []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
            continue
        if line or not data:
            continue
        chunk, data = "\n".join(data), []
        if chunk == "[DONE]":
            break
        try:
            event = json.loads(chunk)
        except ValueError:
            raise Transient("Codex 串流格式錯誤") from None
        kind = event.get("type")
        if kind == "response.output_item.done":
            items.append(event.get("item") or {})
        elif kind in ("response.completed", "response.done") and (event.get("response") or {}).get("status") == "completed":
            result = event["response"]
            result["output"] = result.get("output") or items
            return json.dumps(result, ensure_ascii=False)
        elif kind == "response.incomplete" or (event.get("response") or {}).get("status") == "incomplete":
            raise TooLong("Codex 輸出被截斷")
        elif kind in ("response.failed", "error") or (event.get("response") or {}).get("status") == "failed":
            body = event.get("response", event)
            raise_for(event.get("status") or event.get("status_code"), json.dumps(body.get("error", body), ensure_ascii=False))
    raise Transient("Codex 串流在完成前中斷")


def final_text(response_json: str) -> str:
    result = json.loads(response_json)
    messages = [i for i in result.get("output") or [] if i.get("type") == "message" and i.get("role") == "assistant"]
    final = [i for i in messages if i.get("phase") == "final_answer"] or messages
    parts = [p for i in final for p in i.get("content") or []]
    if any(p.get("type") == "refusal" for p in parts):
        raise Refused("Codex 拒絕翻譯")
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "output_text")
    if not text:
        raise Transient("Codex 回應沒有譯文")
    return text
