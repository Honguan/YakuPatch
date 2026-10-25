"""E4 Decoder and the matching encoder (PROJECT_PLAN §8.1)."""
import codecs

_BOMS = [
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]


def decode(data: bytes, default: str = "cp932") -> tuple[str, dict]:
    for bom, enc in _BOMS:
        if data.startswith(bom):
            text = data[len(bom):].decode(enc)
            return text, _info(enc, True, text)
    try:
        return data.decode("utf-8"), _info("utf-8", False, data.decode("utf-8"))
    except UnicodeDecodeError:
        # shortcut: no charset-normalizer cross-check yet; add it when CP932 engines land (I4).
        text = data.decode(default)
        return text, _info(default, False, text)


def encode(text: str, info: dict) -> bytes:
    bom = dict((enc, bom) for bom, enc in _BOMS)[info["encoding"]] if info["bom"] else b""
    return bom + text.encode(info["encoding"])


def cp932_substitutes(chars: set[str], used: set[str]) -> dict[str, str]:
    """CP932 strategy D (§8.2): each character CP932 lacks gets a kanji CP932 has that the game never uses
    (IBM extension kanji first, then JIS level 2 from the end); the engine's font draws it as the original."""
    need = sorted(ch for ch in chars if not _cp932(ch))
    free = (ch for ch in _rare_kanji() if ch not in used and ch not in chars)
    out = dict(zip(need, free))
    if len(out) < len(need):
        raise ValueError(f"CP932 沒有的字太多（{len(need)} 種），可替代的罕用漢字不夠")
    return out


def _cp932(ch: str) -> bool:
    try:
        ch.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


def _rare_kanji():
    leads = [*range(0xFA, 0xFD), *range(0xEA, 0x97, -1)]
    for lead in leads:
        trails = range(0x40, 0xFD) if lead >= 0xFA else range(0xFC, 0x3F, -1)
        for trail in trails:
            try:
                ch = bytes((lead, trail)).decode("cp932")
            except UnicodeDecodeError:
                continue
            if "一" <= ch <= "鿿" and ch.encode("cp932") == bytes((lead, trail)):
                yield ch


def _info(encoding: str, bom: bool, text: str) -> dict:
    return {"encoding": encoding, "bom": bom, "newline": "\r\n" if "\r\n" in text else "\n"}
