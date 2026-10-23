"""TyranoScript / TyranoBuilder: data/scenario/**/*.ks, loose or inside Electron's resources/app.asar (§10)."""
import re
from pathlib import Path

from ..safety import UnsafePath
from .base import EnginePlugin, Segment

# Loose content roots; "resources/app.asar" may also be an already-extracted folder.
_ROOTS = ("", "resources/app/", "resources/app.asar/")
_TAG = re.compile(r"\[\[|\[[^\[\]]*\]")                       # "[[" is a literal bracket in text
_NAME = re.compile(r"^[\[@]\s*([\w-]+)")
_ATTR = re.compile(r"""(\w+)\s*=\s*("[^"]*"|'[^']*'|[^\s\]"']+)""")
_LOC = re.compile(r"^L(\d+)(?:(#)|@(\d+)\.(\w+))?$")
# Attributes shown to the player. Other attributes may hold file names or ids, so they are never touched.
_TEXT_ATTRS = {("glink", "text"): "choice", ("button", "text"): "choice", ("ptext", "text"): "ui",
               ("mtext", "text"): "ui", ("tb_ptext_show", "text"): "ui", ("dialog", "text"): "system",
               ("chara_new", "jname"): "name"}
_BLOCKS = {"iscript": "endscript", "html": "endhtml"}       # JavaScript / HTML: never parsed as text
_PAGE = ("[p]", "[cm]", "[er]")
_LINE_START = str.maketrans({";": "；", "*": "＊", "#": "＃", "@": "＠"})


def _root(game_dir: Path) -> str | None:
    """Prefix of the content root inside the game folder: "", "resources/app/", ... or "resources/app.asar!"."""
    for root in _ROOTS:
        base = game_dir / root
        if (base / "data" / "scenario").is_dir() and ((base / "tyrano").is_dir() or
                                                      (base / "data" / "system" / "Config.tjs").is_file()):
            return root
    asar = game_dir / "resources" / "app.asar"
    if asar.is_file():
        from ..vfs import Asar
        try:
            files = Asar(asar).files
        except (OSError, ValueError, UnsafePath, KeyError):
            return None
        if any(k.startswith("data/scenario/") for k in files) and any(k.startswith("tyrano/") for k in files):
            return "resources/app.asar!"
    return None


class Tyrano(EnginePlugin):
    name = "tyrano"
    version = "1.0.0"
    default_encoding = "cp932"
    control_codes = [r"\[\[", r"\[[^\[\]]*\]"]

    def detect(self, game_dir: Path) -> float:
        return 1.0 if _root(game_dir) is not None else 0.0

    def locate(self, game_dir: Path) -> list[str]:
        root = _root(game_dir)
        if root is None:
            raise ValueError("找不到 TyranoScript 的 data/scenario")
        if root.endswith("!"):
            from ..vfs import Asar
            files = Asar(game_dir / root[:-1]).files
            return sorted(root + k for k in files if k.startswith("data/scenario/") and k.lower().endswith(".ks"))
        base = game_dir / root / "data" / "scenario"
        return sorted(root + p.relative_to(game_dir / root).as_posix() for p in base.rglob("*.ks") if p.is_file())

    def title(self, game_dir: Path, read) -> str:
        try:
            text = read(_root(game_dir) + "data/system/Config.tjs").decode("utf-8-sig", "replace")
        except (OSError, KeyError, ValueError, TypeError):
            return ""
        m = re.search(r"^\s*;?\s*System\.title\s*=\s*(.*?)\s*;?\s*$", text, re.M)
        return m.group(1) if m else ""

    def parse(self, rel: str, text: str) -> list[Segment]:
        out: list[Segment] = []
        until = None            # end tag of an [iscript]/[html] block
        comment = False         # inside /* ... */
        speaker = group = None
        groups = 0
        for i, raw in enumerate(text.split("\n")):
            line = raw.rstrip("\r")
            s = line.strip()
            if comment:
                comment = "*/" not in s
                continue
            if until:
                if re.match(r"[\[@]\s*" + until + r"\b", s):
                    until = None
                continue
            if s.startswith("/*"):
                comment = "*/" not in s
                group = None
                continue
            if not s or s[0] in ";*":
                group = None if not s or s[0] == "*" else group
                continue
            if s[0] == "#":
                name = s[1:].split(":", 1)[0].strip()
                speaker = name or None
                group = None
                if name:
                    out.append(Segment(f"L{i}#", "name", name))
                continue
            tags = [s] if s[0] == "@" else [m.group(0) for m in _TAG.finditer(s)]
            for k, tag in enumerate(tags):
                m = _NAME.match(tag)
                if not m:
                    continue
                tag_name = m.group(1)
                if tag_name in _BLOCKS and not re.search(r"\[\s*" + _BLOCKS[tag_name] + r"\b", s):
                    until = _BLOCKS[tag_name]
                for a in _ATTR.finditer(tag[m.end():]):
                    kind = _TEXT_ATTRS.get((tag_name, a.group(1)))
                    if kind and _unquote(a.group(2))[:1] not in "&%":   # &expr / %macro_param are not text
                        out.append(Segment(f"L{i}@{k}.{a.group(1)}", kind, _unquote(a.group(2))))
            body = line[len(line) - len(line.lstrip()):]
            if s[0] != "@" and _TAG.sub("", s).strip():
                if group is None:
                    groups += 1
                    group = f"{rel}#{groups}"
                out.append(Segment(f"L{i}", "dialogue", body, speaker, group))
                if s.endswith(_PAGE):
                    group = None
            else:
                group = None
        return out

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        lines = text.split("\n")
        for loc, value in repl.items():
            m = _LOC.match(loc)
            i = int(m.group(1))
            line = lines[i]
            cr = "\r" if line.endswith("\r") else ""
            line = line[:len(line) - len(cr)]
            indent = line[:len(line) - len(line.lstrip())]
            if m.group(2):                                          # "#name:face"
                head, sep, face = line[len(indent) + 1:].partition(":")
                name = head.strip()                                 # keep the spaces around the name
                line = f"{indent}#{head[:len(head) - len(head.lstrip())]}{value}{head[len(head.rstrip()):]}{sep}{face}" \
                    if name else line
            elif m.group(3) is not None:
                line = _set_attr(line, int(m.group(3)), m.group(4), value)
            else:
                line = indent + value
            lines[i] = line + cr
        return "\n".join(lines)

    def fit(self, kind: str, text: str) -> str:
        # A translated line must stay one text line: keep it on one line and never start it with ; * # @.
        text = text.replace("\r\n", "[r]").replace("\n", "[r]")
        return text[:1].translate(_LINE_START) + text[1:] if kind == "dialogue" else text


def _unquote(v: str) -> str:
    return v[1:-1] if v[:1] in "\"'" and v[-1:] == v[:1] and len(v) >= 2 else v


def _set_attr(line: str, k: int, attr: str, value: str) -> str:
    s = line.lstrip()
    if s.startswith("@"):
        spans = [(len(line) - len(s), len(line))]
    else:
        spans = [(m.start(), m.end()) for m in _TAG.finditer(line)]
    start, end = spans[k]
    tag = line[start:end]
    head = _NAME.match(tag.lstrip())
    offset = len(tag) - len(tag.lstrip()) + head.end()
    for a in _ATTR.finditer(tag, offset):
        if a.group(1) == attr:
            old = a.group(2)
            if _unquote(old) == value:
                break
            q = old[0] if old[:1] in "\"'" else '"'
            if q in value:
                q = "'" if "'" not in value else '"'
                value = value.replace(q, "”" if q == '"' else "’")
            tag = tag[:a.start(2)] + q + value + q + tag[a.end(2):]
            break
    return line[:start] + tag + line[end:]
