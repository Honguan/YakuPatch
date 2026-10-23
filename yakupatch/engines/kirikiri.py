"""KiriKiri (KAG3): .ks scenarios in unencrypted XP3 archives; the translation goes into a new patch archive (§10).
Initialize.tjs mounts patch.xp3, patch2.xp3, ... after the other archives, by file name only (flat)."""
import codecs
import dataclasses
import re
from pathlib import Path

from .. import vfs
from ..backup import Backup
from ..encoding import decode
from .base import Segment
from .tyrano import Tyrano

_PATCH = re.compile(r"^patch(\d*)\.xp3$", re.I)
_SPEAKER = re.compile(r"^【\s*([^【】\[\]]+?)\s*】\s*(?:\[[^\[\]]*\]\s*)*$")      # 【 美優 】[r]
_NAME_LOC = re.compile(r"^L(\d+)【$")


def _number(name: str) -> int:
    m = _PATCH.match(name)
    return 0 if not m else int(m.group(1) or 1)


class KiriKiri(Tyrano):
    name = "kirikiri"
    version = "1.0.0"
    default_encoding = "cp932"

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

    def locate(self, game_dir: Path) -> list[str]:
        ours = _number(self._patch_name(game_dir))
        found: dict[str, tuple[str, int]] = {}         # file name -> (rel, archive number); patches go by name
        clash: set[str] = set()
        for name, k in self._archives(game_dir):
            for inner in vfs.Xp3(game_dir / name).files:
                if not inner.lower().endswith(".ks"):
                    continue
                key = inner.rsplit("/", 1)[-1].lower()
                if k == 0 and key in found:
                    clash.add(key)                     # one patch file would replace both: leave them alone
                found[key] = (f"{name}{vfs.SEP}{inner}", k)
        rels = [rel for key, (rel, k) in found.items() if (k or key not in clash) and k < ours]
        if not rels:
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

    def decode(self, rel: str, raw: bytes) -> tuple[str, dict]:
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
