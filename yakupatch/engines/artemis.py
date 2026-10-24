"""Artemis: .ast scenarios and .tbl tables in .pfs archives (§10). The game mounts <name>.pfs, then
<name>.pfs.000, .001, ... by number; a later archive overrides files of the same name, so the translation goes into
a new archive numbered after the last one and the originals stay untouched.
An .ast is a Lua table written one item per line: a message is an item of `text = { ja = { {...} } }` holding
`name = {"話者"}` and one string per displayed line ({"rt2"} breaks between them); choices are the strings of
`select = { ja = {...} }`. Only `ja` is read: other languages (en / cn / tw) mean the game ships its own."""
import re
from pathlib import Path

from .. import vfs
from ..backup import Backup
from ..encoding import decode, encode
from .base import EnginePlugin, LangPack, Segment

_LIT = r'"(?:[^"\\\n]|\\.)*"|\[(=*)\[.*?\]\2\]'                       # Lua string literal on one line
_OPEN = re.compile(r'^\s*(?:(\[?"?\w+"?\]?)\s*=\s*)?\{\s*$')
_CLOSE = re.compile(r"^\s*\},?\s*$")
_STR = re.compile(rf"^\s*({_LIT}),?\s*$")
_NAME = re.compile(rf"^\s*name\s*=\s*\{{\s*({_LIT})")
_TBL = re.compile(rf"^\s*\w+\s*=\s*({_LIT}),?\s*$")
_OTHER_LANG = re.compile(r"^\s*(cn|tw|zh\w*)\s*=\s*\{", re.M)
_JA = re.compile(r"[぀-ヿ一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}
_NUM = re.compile(r"\.pfs(?:\.(\d+))?$", re.I)


def _value(lit: str) -> str:
    if lit.startswith("["):
        return lit[lit.index("[", 1) + 1:lit.rindex("]", 0, -1)]
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(0)), lit[1:-1])


def _literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n") + '"'


def _number(name: str) -> int:
    m = _NUM.search(name)
    return -1 if m.group(1) is None else int(m.group(1))


class Artemis(EnginePlugin):
    name = "artemis"
    version = "1.0.0"
    default_encoding = "utf-8"

    def _archives(self, game_dir: Path) -> list[str]:
        """Archives in mount order, without the one we wrote ourselves."""
        backup, found = Backup(game_dir), []
        for p in game_dir.glob("*.pfs*"):
            if p.is_file() and _NUM.search(p.name) and not backup.is_created(p.name):
                try:
                    with open(p, "rb") as f:
                        if f.read(3) in (b"pf6", b"pf8"):
                            found.append(p.name)
                except OSError:
                    continue
        return sorted(found, key=lambda n: (n[:_NUM.search(n).start()].lower(), _number(n)))

    def detect(self, game_dir: Path) -> float:
        return 1.0 if any(_number(n) < 0 for n in self._archives(game_dir)) else 0.0

    def _members(self, game_dir: Path) -> dict[str, str]:
        found = {}
        for name in self._archives(game_dir):
            for inner in vfs.Pfs(game_dir / name).files:
                if inner.lower().endswith((".ast", ".tbl")):
                    found[inner.lower()] = f"{name}{vfs.SEP}{inner}"          # later archives win
        return found

    def locate(self, game_dir: Path) -> list[str]:
        rels = sorted(self._members(game_dir).values())
        if not rels:
            raise ValueError("找不到 Artemis 的 .ast 腳本")
        return rels

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        for rel in [r for r in self.locate(game_dir) if r.lower().endswith(".ast")][:20]:
            text = decode(vfs.read_current(game_dir, rel), self.default_encoding)[0]
            m = _OTHER_LANG.search(text)
            if m:
                return [LangPack("zh", rel, f"劇本內有中文（{m.group(1)}），請在遊戲設定切換語言")]
        return []

    def _patch_name(self, game_dir: Path) -> str:
        archives = self._archives(game_dir)
        base = next(n for n in archives if _number(n) < 0)
        last = max(_number(n) for n in archives if n.lower().startswith(base.lower()))
        if last >= 999:
            raise ValueError("pfs 封包編號已用完")
        return f"{base}.{last + 1:03d}"

    def parse(self, rel: str, text: str) -> list[Segment]:
        lines = text.split("\n")
        if rel.lower().endswith(".tbl"):
            return [Segment(f"L{i}", "ui", _value(m.group(1))) for i, line in enumerate(lines)
                    if (m := _TBL.match(line)) and _KANA.search(m.group(1))]
        out, stack, group, speaker = [], [], None, None
        for i, line in enumerate(lines):
            if m := _OPEN.match(line):
                stack.append(m.group(1) or "")
                if stack[-3:] == ["text", "ja", ""]:
                    group, speaker = f"g{i}", None
                continue
            if _CLOSE.match(line):
                if stack:
                    stack.pop()
                continue
            if stack[-3:] == ["text", "ja", ""]:
                if m := _NAME.match(line):
                    speaker = _value(m.group(1))
                    out.append(Segment(f"L{i}n", "name", speaker))
                elif (m := _STR.match(line)) and _JA.search(m.group(1)):
                    out.append(Segment(f"L{i}", "dialogue", _value(m.group(1)), speaker, group))
            elif stack[-2:] == ["select", "ja"] and (m := _STR.match(line)) and _JA.search(m.group(1)):
                out.append(Segment(f"L{i}", "choice", _value(m.group(1))))
        return out

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        lines = text.split("\n")
        for loc, value in repl.items():
            i = int(loc[1:].rstrip("n"))
            pattern = _NAME if loc.endswith("n") else _TBL if rel.lower().endswith(".tbl") else _STR
            m = pattern.match(lines[i])
            lines[i] = lines[i][:m.start(1)] + _literal(value) + lines[i][m.end(1):]
        return "\n".join(lines)

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]]:
        files, n = {}, 0
        for rel, r in repl.items():
            info = self.decode(rel, vfs.read_current(game_dir, rel), game_dir)[1]
            files[vfs.split(rel)[1]] = encode(self.render(rel, texts[rel], r), info)
            n += len(r)
        if not files:
            return {}
        magic = vfs.Pfs(game_dir / self._archives(game_dir)[0]).magic
        return {self._patch_name(game_dir): (vfs.Pfs.write(files, magic), n)}
