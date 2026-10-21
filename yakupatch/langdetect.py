"""Language gate and Simplified/Traditional detection (PROJECT_PLAN §5.1). Never converts text."""
import re
from functools import lru_cache

import opencc

KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[一-鿿]")
# Common in Chinese, rare in Japanese text.
_ZH_PARTICLES = set("吗嗎呢你妳们們这這哦吧")
_S2T, _T2S = opencc.OpenCC("s2t"), opencc.OpenCC("t2s")


@lru_cache(maxsize=None)
def _script(c: str) -> str:
    if _S2T.convert(c) != c:
        return "simp"
    if _T2S.convert(c) != c:
        return "trad"
    return ""


@lru_cache(maxsize=None)
def _not_japanese(c: str) -> bool:
    try:
        c.encode("cp932")
        return False
    except UnicodeEncodeError:
        return True


def sentence_is_chinese(text: str) -> bool:
    """Kanji-only lines (names, skill names) stay Japanese unless there is Chinese-only evidence."""
    if KANA.search(text):
        return False
    han = HAN.findall(text)
    return any(c in _ZH_PARTICLES or (_script(c) and _not_japanese(c)) for c in han)


def probe(texts: list[str]) -> dict:
    joined = "".join(texts)
    kana, han = len(KANA.findall(joined)), HAN.findall(joined)
    total = kana + len(han)
    uniq = set(han)
    simp = sum(1 for c in uniq if _script(c) == "simp")
    trad = sum(1 for c in uniq if _script(c) == "trad")
    lines = [t for t in texts if KANA.search(t) or HAN.search(t)]
    zh_ratio = sum(map(sentence_is_chinese, lines)) / len(lines) if lines else 0.0
    if total < 50:
        primary = "unknown"
    elif kana / total > 0.05:
        primary = "mixed" if zh_ratio >= 0.10 else "ja"
    elif simp > trad * 2:
        primary = "zh-Hans"
    elif trad > simp * 2:
        primary = "zh-Hant"
    else:
        primary = "zh"
    return {"primary": primary, "kana_ratio": round(kana / total, 3) if total else 0.0,
            "simp_only": simp, "trad_only": trad, "zh_sentence_ratio": round(zh_ratio, 3)}
