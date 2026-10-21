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


def _info(encoding: str, bom: bool, text: str) -> dict:
    return {"encoding": encoding, "bom": bom, "newline": "\r\n" if "\r\n" in text else "\n"}
