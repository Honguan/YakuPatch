"""Sakura via urllib. Prompt/term format follows BallonsTranslator trans_sakura.py (GPL-3.0)."""
import json
import math
from dataclasses import asdict, dataclass, replace
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import AuthRequired, BackendCaps, ConfigError, Refused, TooLong, Transient

SYSTEM = "你是一个轻小说翻译模型，可以流畅通顺地以日本轻小说的风格将日文翻译成简体中文，并联系上下文正确使用人称代词，不擅自添加原文中没有的代词。"
GALTRANSL = "你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成简体中文，并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，不要擅自添加原文中没有的代词，也不要擅自增加或减少换行。"


@dataclass(frozen=True)
class SakuraConfig:
    api_base: str = "http://127.0.0.1:8080/v1"
    version: str = "1.0"
    model: str = ""
    timeout: float = 999
    parallel: int | None = None
    retries: int = 3
    max_tokens: int = 1024
    temperature: float = 0.1
    top_p: float = 0.3
    frequency_penalty: float = 0.05
    force_glossary: bool = False
    enlarge_kana: bool = False
    repeat_threshold: int = 20

    def __post_init__(self):
        url = urlsplit(self.api_base)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment or url.username:
            raise ValueError("Sakura API 位址必須是無帳密的 HTTP(S) 位址")
        if self.version not in ("0.9", "1.0", "galtransl-v1"):
            raise ValueError("不支援的 Sakura 模型版本")
        if not 0 <= self.retries <= 5 or (self.parallel is not None and self.parallel < 1):
            raise ValueError("重試次數須為 0–5，平行數須大於零")
        if self.repeat_threshold < 2:
            raise ValueError("重複偵測門檻須至少為 2")
        if not all(math.isfinite(x) for x in (self.timeout, self.temperature, self.top_p, self.frequency_penalty)):
            raise ValueError("Sakura 參數必須是有限數值")
        if self.timeout <= 0 or self.max_tokens < 1 or not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("Sakura 逾時、輸出上限或取樣參數不合法")


class Sakura:
    prompt_version = "p1"

    def __init__(self, config: SakuraConfig = SakuraConfig()):
        self.config = config
        base = config.api_base.rstrip("/")
        self.base = base if base.endswith("/v1") else base + "/v1"
        self.root = self.base[:-3]
        self.model = config.model
        self.caps = BackendCaps(max_parallel=config.parallel or 1)

    @property
    def by(self):
        return f"sakura:{self.model or 'auto'}/{self.config.version}-{self.prompt_version}"

    @property
    def cache_identity(self):
        return {"by": self.by, "config": asdict(self.config)}

    def prepare(self):
        self._request(self.root + "/health")
        props = self._request(self.root + "/props")
        try:
            slots = int(props["total_slots"])
            context = int(props["default_generation_settings"]["n_ctx"])
            if slots < 1 or context <= self.config.max_tokens + 128:
                raise ValueError
            self.caps = replace(self.caps, max_parallel=min(self.config.parallel or slots, slots),
                                max_input_tokens=min(600, context - self.config.max_tokens - 128))
            if not self.model:
                self.model = self._request(self.base + "/models")["data"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ConfigError("Sakura /props 或 /models 缺少有效的槽數、context 或模型資訊") from None

    def messages(self, works, glossary, context=True):
        terms = {k: v for w in works for k, v in w.terms.items()}
        if self.config.force_glossary:
            terms = glossary
        table = "\n".join(f"{src}->{dst}" + (f" #{note}" if note else "")
                          for src, (dst, note) in sorted(terms.items(), key=lambda kv: -len(kv[0])))
        prefix = works[0].context if context else []
        raw = "\n".join([*prefix, *(w.text for w in works)])
        if self.config.enlarge_kana:
            raw = raw.translate(str.maketrans("ぁぃぅぇぉゃゅょっァィゥェォヵヶャュョッ", "あいうえおやゆよつアイウエオカケヤユヨツ"))
        system, user = SYSTEM, "将下面的日文文本翻译成中文：\n" + raw
        if self.config.version != "0.9" and table:
            system = GALTRANSL if self.config.version == "galtransl-v1" else SYSTEM
            user = f"根据以下术语表（可以为空）：\n{table}\n将下面的日文文本根据对应关系和备注翻译成中文：\n{raw}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def complete(self, messages, temperature=None):
        cfg = self.config
        obj = self._request(self.base + "/chat/completions", {
            "model": self.model, "messages": messages, "stream": False,
            "temperature": cfg.temperature if temperature is None else temperature,
            "top_p": cfg.top_p, "frequency_penalty": cfg.frequency_penalty,
            "max_tokens": cfg.max_tokens, "seed": -1})
        try:
            choice = obj["choices"][0]
            if choice.get("finish_reason") == "length":
                raise TooLong("輸出被截斷")
            if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                raise Refused("模型拒絕翻譯")
            result = choice["message"]["content"]
            if not isinstance(result, str):
                raise TypeError
            return result
        except (KeyError, IndexError, TypeError):
            raise Transient("Sakura 回應格式錯誤") from None

    def _request(self, url, payload=None):
        request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                return json.load(response)
        except HTTPError as ex:
            # Inspect errors for classification only; never put server text/game text in logs.
            try:
                detail = ex.read().decode("utf-8", "replace").lower()
            except HTTPException:
                raise Transient("Sakura 連線中斷") from None
            if ex.code in (401, 403):
                raise AuthRequired("Sakura 需要授權，進度已保留") from None
            if ex.code == 429 or ex.code >= 500:
                raise Transient(f"Sakura HTTP {ex.code}") from None
            if ex.code == 413 or (ex.code == 400 and any(x in detail for x in ("context", "too long", "too large"))):
                raise TooLong("輸入超過 context") from None
            raise ConfigError(f"Sakura HTTP {ex.code}，請檢查模型與設定") from None
        except (HTTPException, URLError, TimeoutError, OSError):
            raise Transient("Sakura 連線失敗或逾時") from None
        except (ValueError, UnicodeError):
            raise Transient("Sakura 回應不是有效 JSON") from None
