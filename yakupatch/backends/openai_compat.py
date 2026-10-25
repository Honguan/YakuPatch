"""Any OpenAI-compatible /chat/completions endpoint (F5): DeepSeek, OpenAI, OpenRouter, Ollama, LM Studio, vLLM...

Prompt and answer format are Codex's json_ids (ids keep lines apart even when a model merges or splits them). The API
key never goes to config.json: it lives in the system keyring per API base, or comes from YAKUPATCH_API_KEY."""
import ipaddress
import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import AuthRequired, BackendCaps, ConfigError, Refused, TooLong, Transient, UsageLimit
from .codex import INSTRUCTIONS, RULES

KEYRING_SERVICE = "YakuPatch OpenAI-compatible"
ENV_KEY = "YAKUPATCH_API_KEY"
FORMAT = '只輸出一個 JSON 物件，格式為 {"translations":[{"id":"0","translation":"譯文"}]}，不要輸出其他文字。'
_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass(frozen=True)
class OpenAIConfig:
    api_base: str = ""
    model: str = ""
    output_script: str = "zh-Hant"
    temperature: float = 0.3
    max_tokens: int = 4096
    parallel: int = 4
    timeout: float = 300
    retries: int = 3
    max_items: int = 40
    max_input_tokens: int = 3000
    json_mode: bool = True

    def __post_init__(self):
        url = urlsplit(self.api_base)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment or url.username:
            raise ValueError("API 位址必須是無帳密的 HTTP(S) 位址，例如 https://api.deepseek.com/v1")
        if self.output_script not in INSTRUCTIONS:
            raise ValueError("輸出字體只能是 zh-Hant 或 zh-Hans")
        if not 0 <= self.retries <= 5 or self.parallel < 1 or self.max_items < 1 or self.max_input_tokens < 200:
            raise ValueError("重試次數須為 0–5，平行數、每批句數須大於零，輸入預算至少 200")
        if not all(math.isfinite(x) for x in (self.timeout, self.temperature)) or self.timeout <= 0 \
                or not 0 <= self.temperature <= 2 or self.max_tokens < 1:
            raise ValueError("逾時、輸出上限或溫度不合法")


def base_url(api_base: str) -> str:
    """A bare host gets /v1; a URL with a path (…/v1, …/api/v1, …/v1beta/openai) is used as given."""
    base = api_base.rstrip("/")
    return base if urlsplit(base).path else base + "/v1"


def is_local(api_base: str) -> bool:
    """Loopback or private-network host: no metered quota and no API key exposure over plain HTTP."""
    host = urlsplit(api_base).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _keyring(module=None):
    if module is not None:
        return module
    import keyring
    return keyring


def get_key(api_base: str, keyring_module=None) -> str:
    if os.environ.get(ENV_KEY):
        return os.environ[ENV_KEY]
    try:
        return _keyring(keyring_module).get_password(KEYRING_SERVICE, base_url(api_base)) or ""
    except Exception:                       # no usable keyring backend: same as no key
        return ""


def set_key(api_base: str, key: str, keyring_module=None) -> None:
    _keyring(keyring_module).set_password(KEYRING_SERVICE, base_url(api_base), key)


def clear_key(api_base: str, keyring_module=None) -> None:
    try:
        _keyring(keyring_module).delete_password(KEYRING_SERVICE, base_url(api_base))
    except Exception:                       # nothing stored
        pass


def clean(text: str) -> str:
    """Drop reasoning blocks and code fences; keep the outermost JSON object."""
    text = _THINK.sub("", text)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text.strip()


class NoJsonMode(ConfigError):
    """The server rejected response_format; the request is repeated without it."""


class OpenAICompat:
    prompt_version = "o1"

    def __init__(self, config: OpenAIConfig, key: str | None = None, opener=urlopen):
        self.config = config
        self.base = base_url(config.api_base)
        self.key = get_key(config.api_base) if key is None else key
        self.model = config.model
        self.style = ""                          # style.md (F18), set per job by pipeline.use_style
        self._json_mode = config.json_mode
        self._open = opener
        if self.key and urlsplit(self.base).scheme == "http" and not is_local(self.base):
            raise ValueError("API 金鑰不能以未加密的 http 送到外部位址，請改用 https")
        self.caps = BackendCaps(io_format="json_ids", max_input_tokens=config.max_input_tokens, max_items=config.max_items,
                                max_parallel=config.parallel, output_script=config.output_script, style=True, summary=True,
                                metered=not is_local(self.base))

    @property
    def by(self):
        return f"openai:{self.model or 'auto'}-{self.prompt_version}"

    @property
    def cache_identity(self):
        cfg = asdict(self.config)
        for key in ("parallel", "timeout", "retries"):   # do not change the translation
            cfg.pop(key)
        return {"by": self.by, "config": cfg, "style": self.style}

    def prepare(self):
        try:
            models = self.list_models()
        except ConfigError:
            models = []                          # some providers have no /models; the chat call still checks the model
        if not self.model:
            if not models:
                raise ConfigError("請在設定填入模型名稱（這個 API 沒有提供模型清單）")
            self.model = models[0]
        self.caps = replace(self.caps, max_parallel=self.config.parallel)

    def list_models(self) -> list[str]:
        try:
            return [m["id"] for m in self._request(self.base + "/models")["data"]]
        except (KeyError, TypeError):
            raise ConfigError("API 的 /models 回應格式錯誤") from None

    def messages(self, works, glossary, context=True):
        terms = {k: v for w in works for k, v in w.terms.items()}
        body = {"glossary": [{"src": s, "dst": d, **({"note": n} if n else {})}
                             for s, (d, n) in sorted(terms.items(), key=lambda kv: -len(kv[0]))],
                **({"summary": works[0].summary} if works[0].summary else {}),
                "context": works[0].context if context else [],
                "items": [{"id": str(i), "text": w.text, **({"speaker": w.speaker} if w.speaker else {})}
                          for i, w in enumerate(works)]}
        style = f"\n翻譯風格（使用者指定；與上面規則衝突時以規則為準）：\n{self.style}" if self.style else ""
        if works[0].summary:                                   # F14
            style = "\nsummary 是前面劇情的摘要，只供理解人物、人稱與語氣，不要翻譯。" + style
        return [{"role": "system", "content": INSTRUCTIONS[self.config.output_script] + "\n" + RULES + "\n" + FORMAT + style},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]

    def complete(self, messages, temperature=None):
        cfg = self.config
        payload = {"model": self.model, "messages": messages, "stream": False, "max_tokens": cfg.max_tokens,
                   "temperature": cfg.temperature if temperature is None else temperature}
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            obj = self._request(self.base + "/chat/completions", payload)
        except NoJsonMode:
            if not self._json_mode:
                raise
            self._json_mode = False              # ask in plain text from now on; clean() still finds the JSON
            return self.complete(messages, temperature)
        try:
            choice = obj["choices"][0]
            if choice.get("finish_reason") == "length":
                raise TooLong("輸出被截斷")
            if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                raise Refused("模型拒絕翻譯")
            result = choice["message"]["content"]
            if not isinstance(result, str):
                raise TypeError
            return clean(result)
        except (KeyError, IndexError, TypeError):
            raise Transient("API 回應格式錯誤") from None

    def _request(self, url, payload=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        try:
            with self._open(Request(url, data=data, headers=headers), timeout=self.config.timeout) as response:
                return json.load(response)
        except HTTPError as ex:
            try:
                detail = ex.read().decode("utf-8", "replace").lower()
            except HTTPException:
                detail = ""
            status = ex.code
        except (HTTPException, URLError, TimeoutError, OSError):
            raise Transient("API 連線失敗或逾時") from None
        except (ValueError, UnicodeError):
            raise Transient("API 回應不是有效 JSON") from None
        raise_for(status, detail)                # outside `except`: the HTTPError (URL, headers) is not chained


def raise_for(status: int, detail: str) -> None:
    """Map an HTTP error to §6.4 categories. Server text is inspected, never logged or shown."""
    if status in (401, 403):
        raise AuthRequired("API 金鑰無效或沒有權限，請在設定檢查金鑰")
    if status == 402 or any(w in detail for w in ("insufficient_quota", "insufficient balance", "quota", "billing")):
        raise UsageLimit("API 額度或餘額不足，進度已保留；儲值後重跑即可續翻")
    if status == 413 or any(w in detail for w in ("context_length", "context length", "too long", "maximum context")):
        raise TooLong("輸入超過模型的 context")
    if status == 429 or status >= 500:
        raise Transient(f"API HTTP {status}")
    message = f"API 拒絕請求（HTTP {status}），請檢查 API 位址與模型名稱"
    raise (NoJsonMode if "response_format" in detail or "json_object" in detail else ConfigError)(message)
