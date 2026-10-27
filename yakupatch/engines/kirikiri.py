"""KiriKiri (KAG3): .ks scenarios in unencrypted XP3 archives; the translation goes into a new patch archive (§10).
Initialize.tjs mounts patch.xp3, patch2.xp3, ... after the other archives, by file name only (flat)."""
import codecs
import dataclasses
import re
import struct
import zlib
from pathlib import Path

from .. import vfs
from ..backup import Backup
from ..encoding import decode
from .base import LangPack, Segment
from .tyrano import Tyrano

_PATCH = re.compile(r"^patch(\d*)\.xp3$", re.I)
_SPEAKER = re.compile(r"^【\s*([^【】\[\]]+?)\s*】\s*(?:\[[^\[\]]*\]\s*)*$")      # 【 美優 】[r]
_NAME_LOC = re.compile(r"^L(\d+)【$")
# TJS strings the player sees in standard KAG3 system scripts; others hold font names, kinsoku tables or debug text.
_TJS = re.compile(r'(new\s+KAGMenuItem\s*\(\s*this\s*,\s*|\bcaption\s*=\s*|askYesNo\s*\(\s*|System\.title\s*=\s*)'
                  r'"((?:[^"\\\n]|\\.)*)"')
_TJS_LOC = re.compile(r"^L(\d+):(\d+)$")
_TJS_BODY = re.compile(r'(?:[^"\\\n]|\\.)*')
_JA = re.compile(r"[぀-ヿ一-鿿]")
_ZH_PATH = re.compile(r"(?:^|[/_.-])(?:zh(?:[-_](?:cn|tw|hans|hant))?|chs|cht|[st]?chinese)(?=$|[/_.-])", re.I)
_LANG_TAGS = re.compile(r'^\s*\.multiLangLanguageTags\s*=\s*\[([^\]]*)\]\s*;', re.M)
_ZH_TAG = re.compile(r'^["\'](?:tw|cn|zh(?:[-_](?:cn|tw|hans|hant))?|chs|cht|[st]?chinese)["\']$', re.I)
_LANG_KEYS = re.compile(r'^[ \t#▽▼■◆●*=-]*(?:語言切換|语言切换|language\s+(?:switch|selection))[^\n]*\n'
                        r'(?:[^\n]*\n){0,6}?[・•*\s-]*[A-Z]\s*[:：]\s*'
                        r'(?:(?:繁體|简体|簡體|繁体)?中文|(?:Traditional|Simplified)\s+Chinese)\s*(?:\n|$)', re.I | re.M)
_CONFIG_LIMIT = 256 * 1024


def _config_text(raw: bytes) -> str:
    """Read language declarations only; never execute configuration scripts."""
    if len(raw) > _CONFIG_LIMIT:
        raise ValueError("Configuration too large")
    if not raw.startswith(b"\xfe\xfe"):
        return decode(raw)[0]
    if len(raw) < 5 or raw[3:5] != b"\xff\xfe":
        raise ValueError("Invalid text header")
    mode, body = raw[2], raw[5:]
    # KiriKiri TextStream.cpp: modes 0/1 scramble UTF-16; mode 2 compresses it.
    if mode == 2:
        if len(body) < 16:
            raise ValueError("Truncated text header")
        compressed, size = struct.unpack("<QQ", body[:16])
        if compressed != len(body) - 16 or size > _CONFIG_LIMIT:
            raise ValueError("Invalid text size")
        stream = zlib.decompressobj()
        body = stream.decompress(body[16:], _CONFIG_LIMIT + 1)
        if len(body) != size or not stream.eof or stream.unused_data:
            raise ValueError("Invalid compressed text")
    elif mode in (0, 1) and len(body) % 2 == 0:
        values = (c for c, in struct.iter_unpack("<H", body))
        if mode == 0:
            values = (c ^ (((c & 0xfe) << 8) ^ 1) if c >= 0x20 else c for c in values)
        else:
            values = (((c & 0xaaaa) >> 1) | ((c & 0x5555) << 1) for c in values)
        body = b"".join(struct.pack("<H", c) for c in values)
    else:
        raise ValueError("Unsupported text mode")
    return body.decode("utf-16le")


def _number(name: str) -> int:
    m = _PATCH.match(name)
    return 0 if not m else int(m.group(1) or 1)


class KiriKiri(Tyrano):
    name = "kirikiri"
    version = "1.0.0"
    default_encoding = "cp932"
    control_codes = Tyrano.control_codes + [r"\(&[A-Za-z0-9]\)", r"\\[tn]"]       # menu accelerators, TJS escapes

    def detect(self, game_dir: Path) -> float:
        try:
            with open(game_dir / "data.xp3", "rb") as f:
                return 1.0 if f.read(11) == vfs.XP3_MAGIC else 0.0
        except OSError:
            return 0.0

    def _archives(self, game_dir: Path) -> list[tuple[str, int]]:
        """Archives in mount order, without the patch archive we wrote ourselves."""
        backup = Backup(game_dir)
        names = [p.name for p in game_dir.glob("*.xp3") if p.is_file() and not backup.is_created(p.name)]
        return sorted(((n, _number(n)) for n in names), key=lambda t: (t[1], t[0].lower()))

    def _patch_name(self, game_dir: Path) -> str:
        taken = {n for n, k in self._archives(game_dir) if k}
        k = 1
        while (f"patch{k}.xp3" if k > 1 else "patch.xp3").lower() in {t.lower() for t in taken}:
            k += 1
        return f"patch{k}.xp3" if k > 1 else "patch.xp3"

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        packs = []
        for path in sorted(game_dir.glob("*")):
            if not path.name.lower().startswith("readme") or path.suffix.lower() != ".txt":
                continue
            try:
                with path.open("rb") as f:
                    raw = f.read(_CONFIG_LIMIT + 1)
                if len(raw) > _CONFIG_LIMIT:
                    continue
                for encoding in ("utf-8-sig", "utf-16", "cp950", "gb18030"):
                    try:
                        if _LANG_KEYS.search(raw.decode(encoding)):
                            return [LangPack("zh", path.name, "遊戲說明列有中文切換快捷鍵")]
                    except UnicodeError:
                        continue
            except OSError:
                continue
        configs = {}
        for name, _ in self._archives(game_dir):
            archive = vfs.Xp3(game_dir / name)
            for inner in archive.files:
                base = inner.rsplit("/", 1)[-1].lower()
                if base in ("default.tjs", "config.tjs", "appconfig.tjs"):
                    configs[base] = (archive, inner, f"{name}{vfs.SEP}{inner}")
                if inner.lower().endswith((".ks", ".scn")) and _ZH_PATH.search(f"{name}/{inner}"):
                    rel = f"{name}{vfs.SEP}{inner}"
                    packs.append(LangPack("zh", rel, "遊戲內有中文劇本，請在遊戲設定切換語言"))
                    break
        for archive, inner, rel in configs.values():
            if sum(s[2] for s in archive.files[inner]) > _CONFIG_LIMIT:
                continue
            try:
                text = _config_text(archive.read(inner))
            except (OSError, ValueError, zlib.error):
                continue
            text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
            declarations = _LANG_TAGS.findall(text)
            if declarations and any(_ZH_TAG.fullmatch(tag.strip()) for tag in declarations[-1].split(",")):
                packs.append(LangPack("zh", rel, "遊戲設定宣告可選中文，請在遊戲內切換語言"))
        return packs

    def locate(self, game_dir: Path) -> list[str]:
        ours = _number(self._patch_name(game_dir))
        found: dict[str, tuple[str, int]] = {}         # file name -> (rel, archive number); patches go by name
        clash: set[str] = set()
        for name, k in self._archives(game_dir):
            for inner in vfs.Xp3(game_dir / name).files:
                if not inner.lower().endswith((".ks", ".tjs")):
                    continue
                key = inner.rsplit("/", 1)[-1].lower()
                if k == 0 and key in found:
                    clash.add(key)                     # one patch file would replace both: leave them alone
                found[key] = (f"{name}{vfs.SEP}{inner}", k)
        rels = [rel for key, (rel, k) in found.items() if (k or key not in clash) and k < ours]
        compiled = [rel for rel in rels if rel.lower().endswith(".tjs")
                    and vfs.read_original(game_dir, rel).startswith(b"TJS2100\0")]
        rels = [rel for rel in rels if rel not in compiled]
        if not rels:
            if compiled:
                raise ValueError("只有已編譯 TJS，無法提取文本；請先檢查遊戲內是否可切換中文")
            raise ValueError("找不到 KiriKiri 的 .ks 腳本（可能使用 .scn 或已加密）")
        return sorted(rels)

    def title(self, game_dir: Path, read) -> str:
        for name, _ in self._archives(game_dir):
            try:
                text = decode(read(f"{name}{vfs.SEP}system/Config.tjs"), self.default_encoding)[0]
            except (OSError, KeyError, ValueError, UnicodeDecodeError):
                continue
            m = re.search(r"^\s*;?\s*System\.title\s*=\s*(.*?)\s*;?\s*$", text, re.M)
            if m:
                return m.group(1).strip("\"'")
        return ""

    def decode(self, rel: str, raw: bytes, game_dir: Path | None = None) -> tuple[str, dict]:
        if raw.startswith(b"TJS2100\0"):
            raise ValueError(f"腳本是已編譯 TJS，無法提取文本；請先檢查遊戲內是否可切換中文：{rel}")
        if raw[:2] == b"\xfe\xfe":
            raise ValueError(f"腳本使用吉里吉里簡易加密，尚未支援：{rel}")
        try:
            text, info = decode(raw, self.default_encoding)
        except UnicodeDecodeError:
            raise ValueError(f"腳本無法解碼，封包可能已加密：{rel}") from None
        # Written back as UTF-16LE with BOM (KiriKiri reads it natively), so any Chinese character fits.
        return text, {**info, "encoding": "utf-16-le", "bom": True}

    def attr_kind(self, tag: str, attr: str) -> str | None:
        # KAG games define their own choice macros; a "text" attribute is what the player reads ([ruby] is a reading).
        if attr == "text" and tag != "ruby":
            return "choice" if re.search(r"select|link|button|choice", tag, re.I) else "ui"
        return super().attr_kind(tag, attr)

    def parse(self, rel: str, text: str) -> list[Segment]:
        lines = text.split("\n")
        if rel.lower().endswith(".tjs"):
            return [Segment(f"L{i}:{m.start(2)}", "system" if m.group(1).startswith("askYesNo") else "ui", m.group(2))
                    for i, line in enumerate(lines) if not line.lstrip().startswith("//")
                    for m in _TJS.finditer(line) if _JA.search(m.group(2))]
        out, speakers = [], {}
        for seg in super().parse(rel, text):
            line = lines[int(seg.loc[1:])].strip() if re.fullmatch(r"L\d+", seg.loc) else ""
            m = _SPEAKER.match(line)
            if line.startswith("//"):                  # developer notes left in scripts, never reached as text
                continue
            if m:
                out.append(Segment(f"{seg.loc}【", "name", m.group(1)))
                speakers[seg.group] = m.group(1)
            elif seg.kind == "dialogue" and seg.group in speakers:
                out.append(dataclasses.replace(seg, speaker=speakers[seg.group]))
            else:
                out.append(seg)
        return out

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        lines = text.split("\n")
        if rel.lower().endswith(".tjs"):
            for loc, value in sorted(repl.items(), key=lambda kv: -int(_TJS_LOC.match(kv[0]).group(2))):
                i, col = map(int, _TJS_LOC.match(loc).groups())
                end = _TJS_BODY.match(lines[i], col).end()
                value = re.sub(r'(?<!\\)"', r'\\"', value.replace("\r", "").replace("\n", "\\n"))
                lines[i] = lines[i][:col] + value + lines[i][end:]
            return "\n".join(lines)
        rest = {}
        for loc, value in repl.items():
            m = _NAME_LOC.match(loc)
            if not m:
                rest[loc] = value
                continue
            i = int(m.group(1))
            lines[i] = re.sub(r"(【\s*)[^【】\[\]]+?(\s*】)", lambda g: g.group(1) + value + g.group(2), lines[i], count=1)
        return super().render(rel, "\n".join(lines), rest)

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]]:
        files, n = {}, 0
        for rel, r in repl.items():
            name = vfs.split(rel)[1].rsplit("/", 1)[-1]
            files[name] = codecs.BOM_UTF16_LE + self.render(rel, texts[rel], r).encode("utf-16-le")
            n += len(r)
        return {self._patch_name(game_dir): (vfs.Xp3.write(files), n)} if files else {}
