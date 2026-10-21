"""Locate every JSON string value by JSON Pointer, so writeback can replace strings in place.

Only the replaced strings change; everything else stays byte-identical to the original file.
"""
import json
import re

_WS = re.compile(r"[ \t\r\n]*")
_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"', re.S)
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
# Map/tileset data is mostly long number arrays; skip a run of "number," in one regex step.
_NUMBER_RUN = re.compile(r"(?:" + _NUMBER.pattern + r"[ \t\r\n]*,[ \t\r\n]*)+")


def escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def string_spans(text: str) -> dict[str, tuple[int, int]]:
    """Map JSON Pointer -> (start, end) of each string value, quotes included."""
    spans: dict[str, tuple[int, int]] = {}

    def ws(i: int) -> int:
        return _WS.match(text, i).end()

    def string(i: int) -> int:
        m = _STRING.match(text, i)
        if not m:
            raise ValueError(f"JSON 字串格式錯誤，位置 {i}")
        return m.end()

    def value(i: int, ptr: str) -> int:
        c = text[i:i + 1]
        if c == '"':
            end = string(i)
            spans[ptr] = (i, end)
            return end
        if c in ("{", "["):
            close = "}" if c == "{" else "]"
            i = ws(i + 1)
            if text[i:i + 1] == close:
                return i + 1
            n = 0
            while True:
                if c == "{":
                    key_end = string(i)
                    key = json.loads(text[i:key_end])
                    i = ws(key_end)
                    if text[i:i + 1] != ":":
                        raise ValueError(f"JSON 缺少冒號，位置 {i}")
                    i = value(ws(i + 1), f"{ptr}/{escape(key)}")
                else:
                    run = _NUMBER_RUN.match(text, i)
                    if run:
                        n += run.group().count(",")
                        i = run.end()
                    i = value(i, f"{ptr}/{n}")
                n += 1
                i = ws(i)
                d = text[i:i + 1]
                if d == ",":
                    i = ws(i + 1)
                elif d == close:
                    return i + 1
                else:
                    raise ValueError(f"JSON 結構錯誤，位置 {i}")
        for literal in ("true", "false", "null"):
            if text.startswith(literal, i):
                return i + len(literal)
        m = _NUMBER.match(text, i)
        if m and m.end() > i:
            return m.end()
        raise ValueError(f"JSON 無法解析的內容，位置 {i}")

    end = ws(value(ws(0), ""))
    if end != len(text):
        raise ValueError("JSON 結尾有多餘內容")
    return spans


def replace_strings(text: str, repl: dict[str, str]) -> str:
    """Replace the string values at the given pointers; raises KeyError for a pointer that is not a string."""
    if not repl:
        return text
    spans = string_spans(text)
    out, last = [], 0
    for start, end, new in sorted(spans[p] + (s,) for p, s in repl.items()):
        out += [text[last:start], json.dumps(new, ensure_ascii=False)]
        last = end
    out.append(text[last:])
    return "".join(out)
