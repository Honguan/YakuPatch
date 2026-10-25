"""Godot: text inside .pck archives (§10). A logical file is one .pck (the game loads every one of them, later
ones override); its decoded text is a JSON list of the strings our rules pick: player-facing properties of text
scenes and resources (.tscn / .tres) and sentence-like string constants of Godot 3 compiled scripts (.gdc).
The pck is rewritten whole (outputs()): changed members are appended and their directory entries point at them,
so no other data moves; the original stays in the backup. Fonts (.ttf / .otf) become a Windows CJK font.
Not supported: encrypted packs, packs embedded in the .exe, Godot 4 binary scenes (.scn) and scripts."""
import hashlib
import json
import re
import struct
from pathlib import Path

from ..backup import Backup
from .base import EnginePlugin, LangPack, Segment
from .unity import _PATH, _system_font

_MAGIC = b"GDPC"
_JA = re.compile(r"[぀-ヿ一-鿿]")
_CJK = re.compile(r"[一-鿿]")
# Properties the player reads; Godot 3 names first, then Godot 4 renames.
_PROPS = "text|bbcode_text|hint_tooltip|placeholder_text|dialog_text|window_title|tooltip_text"
_PROP = re.compile(rf'^({_PROPS}) = "((?:[^"\\]|\\.)*)"', re.M)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_FONT = (".ttf", ".otf")
_ZH = re.compile(r"\.zh(?:_\w+)?\.translation$", re.I)


class _Pck:
    """GDPC: [magic][u32 format][u32 major minor patch]; format >= 2 adds [u32 flags][u64 file base] (flags&1 =
    encrypted directory), format 3 adds [u64 directory offset]; 16 reserved u32. The directory is a u32 count of
    [u32 name length][name, NUL padded][u64 offset][u64 size][16 md5] (format >= 2: [u32 flags], 1 = encrypted).
    Format 1 offsets are absolute, later ones relative to the file base."""

    def __init__(self, raw: bytes):
        if raw[:4] != _MAGIC:
            raise ValueError("不是 Godot pck 封包")
        fmt, = struct.unpack_from("<I", raw, 4)
        if fmt > 3:
            raise ValueError(f"不支援的 pck 版本 {fmt}")
        p, self.base, flags = 20, 0, 0
        if fmt >= 2:
            flags, self.base = struct.unpack_from("<Iq", raw, p)
            p += 12
        if flags & 1:
            raise ValueError("pck 目錄已加密，不支援且不嘗試解密")
        if fmt >= 3:
            p, = struct.unpack_from("<q", raw, p)
        else:
            p += 16 * 4
        self.raw, self.fmt = raw, fmt
        self.files: dict[str, tuple[int, int, int, bool]] = {}      # name -> (entry position, offset, size, encrypted)
        count, = struct.unpack_from("<I", raw, p)
        p += 4
        for _ in range(count):
            n, = struct.unpack_from("<I", raw, p)
            name = raw[p + 4:p + 4 + n].rstrip(b"\0").decode("utf-8")
            p += 4 + n
            offset, size = struct.unpack_from("<qq", raw, p)
            enc = fmt >= 2 and struct.unpack_from("<I", raw, p + 32)[0] & 1
            self.files[name.removeprefix("res://")] = (p, offset + self.base, size, bool(enc))
            p += 32 + (4 if fmt >= 2 else 0)

    def read(self, name: str) -> bytes:
        _, offset, size, enc = self.files[name]
        if enc:
            raise ValueError(f"pck 檔案已加密：{name}")
        return self.raw[offset:offset + size]

    def patch(self, changes: dict[str, bytes]) -> bytes:
        out = bytearray(self.raw)
        for name, data in changes.items():
            out += b"\0" * (-len(out) % 16)
            entry = self.files[name][0]
            struct.pack_into("<qq16s", out, entry, len(out) - self.base, len(data), hashlib.md5(data).digest())
            out += data
        return bytes(out)


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(0)), s, flags=re.S)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')       # text resources keep line breaks literally


def _gdc(raw: bytes) -> tuple[list[str], list[tuple[int, int, str | None]]]:
    """Godot 3 compiled script (bytecode 12/13): [GDSC][u32 version][u32 identifiers, constants, lines, tokens],
    identifiers as [u32 length][bytes ^ 0xb6], then constants as encoded Variants. Returns the identifiers and every
    constant's (start, end, string or None)."""
    if raw[:4] != b"GDSC":
        raise ValueError("not a compiled script")
    version, n_id, n_const = struct.unpack_from("<3I", raw, 4)
    if version not in (12, 13):
        raise ValueError(f"unsupported script version {version}")
    p, idents = 24, []
    for _ in range(n_id):
        n, = struct.unpack_from("<I", raw, p)
        idents.append(bytes(b ^ 0xB6 for b in raw[p + 4:p + 4 + n]).rstrip(b"\0").decode("utf-8"))
        p += 4 + n
    consts = []
    for _ in range(n_const):
        start = p
        t, = struct.unpack_from("<I", raw, p)
        p += 4
        kind, value = t & 0xFFFF, None
        if kind in (1, 2, 3):                                  # bool, int, real; flag 1<<16 = 64 bit
            p += 8 if t & (1 << 16) else 4
        elif kind == 4:
            n, = struct.unpack_from("<I", raw, p)
            value = raw[p + 4:p + 4 + n].decode("utf-8")
            p += 4 + n + (-n % 4)
        elif kind != 0:
            raise ValueError(f"unsupported constant type {kind}")
        consts.append((start, p, value))
    return idents, consts


def _variant(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<II", 4, len(b)) + b + b"\0" * (-len(b) % 4)


def _entries(pck: _Pck) -> list[dict]:
    scenes, scripts = {}, {}
    for name, (_, _, _, enc) in pck.files.items():
        if enc:
            continue
        if name.endswith((".tscn", ".tres")):
            scenes[name] = pck.read(name).decode("utf-8", "replace")
        elif name.endswith(".gdc"):
            try:
                scripts[name] = _gdc(pck.read(name))
            except (ValueError, struct.error, UnicodeDecodeError):
                continue                                        # Godot 4 scripts and anything else we cannot read
    out, used = [], set()
    for name, text in scenes.items():
        for k, m in enumerate(_PROP.finditer(text)):
            value = _unescape(m.group(2))
            if _JA.search(value):
                out.append({"loc": f"{name}#{k}", "kind": "ui", "text": value})
        # Every other quoted string (node names, animation names, paths) may be looked up by a script.
        used.update(_unescape(q) for q in _QUOTED.findall(_PROP.sub("", text)))
    for idents, _ in scripts.values():
        used.update(idents)
    for name, (_, consts) in scripts.items():
        for k, (_, _, value) in enumerate(consts):
            if value and _JA.search(value) and value not in used and not _PATH.search(value):
                out.append({"loc": f"{name}@{k}", "kind": "ui", "text": value})
    return out


def _packs(game_dir: Path) -> list[Path]:
    found = []
    for p in sorted(game_dir.glob("*.pck")):
        try:
            with open(p, "rb") as f:
                if f.read(4) == _MAGIC:
                    found.append(p)
        except OSError:
            continue
    return found


class Godot(EnginePlugin):
    name = "godot"
    version = "1.0.0"
    control_codes = [r"\[/?\w+(?:=[^\[\]]*)?\]", r"%[-+0-9.]*[sdfx]", r"\{\w*\}"]   # BBCode, format arguments

    def detect(self, game_dir: Path) -> float:
        return 1.0 if _packs(game_dir) else 0.0

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        backup, out = Backup(game_dir), []
        for p in _packs(game_dir):
            rel = p.name
            try:
                names = _Pck(backup.original_bytes(rel)).files
            except (ValueError, OSError, struct.error):
                continue
            out += [LangPack("zh", f"{rel}!{n}", f"遊戲內有中文翻譯 {n}，請在遊戲設定切換語言")
                    for n in names if _ZH.search(n)]
        return out

    def locate(self, game_dir: Path) -> list[str]:
        backup, found = Backup(game_dir), []
        for p in _packs(game_dir):
            rel = p.name
            if json.loads(self.decode(rel, backup.original_bytes(rel), game_dir)[0]):
                found.append(rel)
        if not found:
            raise ValueError("找不到可翻譯的 Godot 文字（可能是 Godot 4 二進位場景或封包已加密）")
        return found

    def title(self, game_dir: Path, read) -> str:
        for p in _packs(game_dir):
            try:
                cfg = _Pck(read(p.name)).read("project.binary")
            except (ValueError, KeyError, OSError, struct.error):
                continue
            key = b"application/config/name"
            i = cfg.find(key)
            if i >= 0:
                t, n = struct.unpack_from("<II", cfg, i + len(key) + 4)
                if t == 4:
                    return cfg[i + len(key) + 12:i + len(key) + 12 + n].decode("utf-8", "replace")
        return ""

    def decode(self, rel: str, raw: bytes, game_dir: Path | None = None) -> tuple[str, dict]:
        try:
            entries = _entries(_Pck(raw))
        except struct.error:
            raise ValueError(f"pck 封包損壞：{rel}") from None
        return json.dumps(entries, ensure_ascii=False), {"encoding": "utf-8", "bom": False, "newline": "\n"}

    def parse(self, rel: str, text: str) -> list[Segment]:
        return [Segment(e["loc"], e["kind"], e["text"]) for e in json.loads(text)]

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError("Godot 以 outputs() 重寫整個 pck")

    def _apply(self, pck: _Pck, repl: dict[str, str], need: set[str]) -> bytes:
        by_member: dict[str, dict[str, str]] = {}
        for loc, value in repl.items():
            name, sep, k = re.match(r"(.*)([#@])(\d+)$", loc).groups()
            by_member.setdefault(name, {})[sep + k] = value
        changes = {}
        for name, r in by_member.items():
            raw = pck.read(name)
            if name.endswith(".gdc"):
                _, consts = _gdc(raw)
                parts, pos = [], consts[0][0] if consts else 0
                for k, (start, end, _) in enumerate(consts):
                    parts.append(_variant(r[f"@{k}"]) if f"@{k}" in r else raw[start:end])
                changes[name] = raw[:pos] + b"".join(parts) + raw[consts[-1][1]:]
            else:
                text = raw.decode("utf-8")
                hits = list(_PROP.finditer(text))
                for k in sorted((int(key[1:]) for key in r), reverse=True):
                    m = hits[k]
                    text = text[:m.start(2)] + _escape(r[f"#{k}"]) + text[m.end(2):]
                changes[name] = text.encode("utf-8")
        if need:
            # shortcut: every font becomes the Windows CJK font, even Latin-only ones; check coverage if games mind.
            for name in pck.files:
                if name.lower().endswith(_FONT):
                    font = _system_font(need, [Path(name).stem])
                    if font:
                        changes[name] = font
        return pck.patch(changes) if changes else pck.raw

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]]:
        backup = Backup(game_dir)
        need = {ch for r in repl.values() for v in r.values() for ch in v if _CJK.match(ch)}
        # Every pck we may have patched: one without translations still gets the font, or goes back to the original.
        return {rel: (self._apply(_Pck(backup.original_bytes(rel)), repl.get(rel, {}), need), len(repl.get(rel, {})))
                for rel in texts}
