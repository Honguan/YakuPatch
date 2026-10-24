"""Wolf RPG Editor 2.x: loose files under Data/BasicData and Data/MapData (§10). Layouts follow WolfTrans.
Only unencrypted loose data is read; .wolf archives and encrypted .dat files are refused, never decrypted.
Text is CP932: characters CP932 has are written as they are; the others take the code of a kanji the game never
uses, and a copy of a Windows Chinese font with those kanji drawn as the Chinese characters becomes the game font."""
import io
import json
import re
import struct
from pathlib import Path

from ..backup import Backup
from ..encoding import cp932_substitutes
from .base import EnginePlugin, LangPack, Segment
from .unity import _system_font

_MAP = b"\x00" * 10 + b"WOLFM\x00"
_COMMON = bytes.fromhex("00570000 4f4c0046 43008f")
_DB = bytes.fromhex("00570000 4f4c0046 4d00c1")
_GAME = bytes.fromhex("00570000 4f4c0046 4d00")
_DATABASES = ("DataBase", "CDataBase")
_JA = re.compile(r"[぀-ヿ一-鿿]")
_PATH = re.compile(r"[/\\]|\.\w{2,4}$")
_HALF_KANA = re.compile(r"[｡-ﾟ]")
_BROKEN = re.compile(r"[-�]")
_FONT = "Data/YakuPatch.ttf"
_FAMILY = "YakuPatch"


class _Reader:
    """Walks one file; `found` keeps (start, end, text, kind) of every string worth translating."""

    def __init__(self, raw: bytes):
        self.raw, self.p, self.found, self.gbk = raw, 0, [], 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.raw):
            raise ValueError("檔案結尾不完整")
        self.p += n
        return self.raw[self.p - n:self.p]

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def expect(self, data: bytes) -> None:
        if self.take(len(data)) != data:
            raise ValueError(f"格式不符（位置 {self.p - len(data)}）")

    def string(self, kind: str | None = None) -> str:
        start, n = self.p, self.u32()
        data = self.take(n) if n else b""
        if not n or data[-1]:
            raise ValueError(f"字串格式不符（位置 {start}）")
        text = data[:-1].decode("cp932", "replace")
        # Text another tool already wrote in GBK reads as half-width kana and private-use characters in CP932.
        garbled = _BROKEN.search(text) or len(_HALF_KANA.findall(text)) * 2 > len(re.sub(r"[ -~]", "", text))
        self.gbk += bool(kind and garbled)
        if kind and _JA.search(text) and not garbled and not (kind == "other" and _PATH.search(text)):
            self.found.append((start, self.p, text, kind))
        return text

    def strings(self, n: int) -> None:
        for _ in range(n):
            self.string()

    def route(self) -> None:
        for _ in range(self.u32()):
            self.u8()
            self.take(4 * self.u8())
            self.expect(b"\x01\x00")

    def commands(self) -> None:
        for _ in range(self.u32()):
            args = [0] * (self.u8() - 1)
            cid = self.u32()
            args = [self.u32() for _ in args]
            self.u8()                                                    # indent
            n = self.u8()
            if cid == 101:                                               # message
                kinds = ["dialogue"]
            elif cid == 102:                                             # choices
                kinds = ["choice"] * n
            elif cid == 150 and args and (args[0] >> 4) & 7 == 2:        # picture showing a string
                kinds = ["ui"]
            elif cid == 122:                                             # set string variable
                kinds = ["other"]
            else:
                kinds = []
            for i in range(n):
                self.string(kinds[i] if i < len(kinds) else None)
            end = self.u8()
            if end == 1:                                                 # move route
                self.take(6)
                self.route()
            elif end:
                raise ValueError(f"指令結尾不符（位置 {self.p - 1}）")

    def done(self) -> "_Reader":
        if self.p != len(self.raw):
            raise ValueError(f"檔案未完整解析（停在 {self.p}／{len(self.raw)}）")
        return self


def _map(raw: bytes) -> _Reader:
    r = _Reader(raw)
    r.expect(_MAP)
    r.take(9)
    r.string()
    r.u32()                                                              # tileset
    width, height = r.u32(), r.u32()
    r.take(4 + width * height * 12)
    while (mark := r.u8()) == 0x6F:
        r.expect(b"\x39\x30\x00\x00")
        r.u32()
        r.string()
        r.take(12)
        r.expect(b"\x00" * 4)
        while (page := r.u8()) == 0x79:
            r.u32()
            r.string()
            r.take(4 + 37 + 4 + 2)
            r.route()
            r.commands()
            r.expect(b"\x03\x00\x00\x00")
            r.take(3)
            r.expect(b"\x7a")
        if page != 0x70:
            raise ValueError("事件頁結尾不符")
    if mark != 0x66:
        raise ValueError("事件結尾不符")
    return r.done()


def _common(raw: bytes) -> _Reader:
    r = _Reader(raw)
    r.expect(_COMMON)
    for _ in range(r.u32()):
        r.expect(b"\x8e")
        r.take(15)
        r.string()
        r.commands()
        r.strings(2)
        r.expect(b"\x8f\x0a\x00\x00\x00")
        r.strings(10)
        r.expect(b"\x0a\x00\x00\x00")
        r.take(10)
        r.expect(b"\x0a\x00\x00\x00")
        for _ in range(10):
            r.strings(r.u32())
        r.expect(b"\x0a\x00\x00\x00")
        for _ in range(10):
            r.take(4 * r.u32())
        r.take(0x1D)
        r.strings(100)
        r.expect(b"\x91")
        r.string()
        if r.u8() == 0x92:
            r.string()
            r.u32()
            r.expect(b"\x92")
    r.expect(b"\x8f")
    return r.done()


def _project(raw: bytes) -> list[list[int]]:
    """Field types of each database type (0 = plain value; others are lists, files and the like)."""
    r, types = _Reader(raw), []
    for _ in range(r.u32()):
        r.string()
        fields = r.u32()
        r.strings(fields)
        r.strings(r.u32())                                               # data names
        r.string()
        size = r.u32()
        kinds = list(r.take(size))[:fields]
        r.strings(r.u32())
        for _ in range(r.u32()):
            r.strings(r.u32())
        for _ in range(r.u32()):
            r.take(4 * r.u32())
        r.take(4 * r.u32())
        types.append(kinds)
    r.done()
    return types


def _database(raw: bytes, types: list[list[int]]) -> _Reader:
    r = _Reader(raw)
    r.expect(_DB)
    if r.u32() != len(types):
        raise ValueError("資料庫 .project 與 .dat 的類型數不符")
    for kinds in types:
        r.expect(b"\xfe\xff\xff\xff")
        r.u32()
        info = [r.u32() for _ in range(r.u32())]
        strings = [i for i, v in enumerate(info) if v >= 0x7D0]
        for _ in range(r.u32()):
            r.take(4 * (len(info) - len(strings)))
            for i in strings:
                r.string("ui" if i < len(kinds) and kinds[i] == 0 else None)
    r.expect(b"\xc1")
    return r.done()


def _game(raw: bytes) -> tuple[int, int, str]:
    """Game.dat: (start, end) of the font name, and the name."""
    r = _Reader(raw)
    r.expect(_GAME)
    r.take(r.u32())
    r.u32()
    r.string()
    r.string()
    r.take(r.u32())
    start = r.p
    font = r.string()
    return start, r.p, font


def _encoded(text: str) -> bytes:
    data = text.encode("cp932") + b"\x00"
    return struct.pack("<I", len(data)) + data


def _splice(raw: bytes, spans: list[tuple[int, int, bytes]]) -> bytes:
    out, pos = [], 0
    for start, end, data in sorted(spans):
        out += [raw[pos:start], data]
        pos = end
    return b"".join(out) + raw[pos:]


def _font(base: bytes, glyphs: dict[str, str], family: str) -> bytes:
    """The Windows font with each stand-in kanji drawn as its Chinese character, renamed so the game can pick it."""
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(base), fontNumber=0)
    best = font.getBestCmap()
    for table in font["cmap"].tables:
        if table.isUnicode():
            for stand_in, ch in glyphs.items():
                if ord(ch) in best and (table.format != 4 or ord(stand_in) <= 0xFFFF):
                    table.cmap[ord(stand_in)] = best[ord(ch)]
    for rec in font["name"].names:
        if rec.nameID in (1, 3, 4, 6, 16):
            rec.string = family
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


class Wolf(EnginePlugin):
    name = "wolf"
    version = "1.0.0"
    default_encoding = "cp932"
    # \c[2] \f[20] \self[0] \cdb[1:2:3] \E \. <C> and the like
    control_codes = [r"\\[A-Za-z_]+\[[^\]\n]*\]", r"\\[A-Za-z]+", r"\\[^A-Za-z\n]", r"<[CRL]>"]

    def detect(self, game_dir: Path) -> float:
        data = game_dir / "Data"
        return 1.0 if (game_dir / "Game.exe").is_file() and (
            (data / "BasicData").is_dir() or (game_dir / "Data.wolf").is_file() or any(data.glob("*.wolf"))) else 0.0

    def locate(self, game_dir: Path) -> list[str]:
        basic = game_dir / "Data" / "BasicData"
        if not basic.is_dir():
            raise ValueError("BasicData 包在 .wolf 封包內（多為加密），目前只支援散檔的 Wolf 遊戲")
        rels = [f"Data/BasicData/{n}.dat" for n in ("CommonEvent", *_DATABASES) if (basic / f"{n}.dat").is_file()]
        rels += sorted(p.relative_to(game_dir).as_posix() for p in (game_dir / "Data" / "MapData").glob("*.mps"))
        for rel in rels:
            if (game_dir / rel).read_bytes()[:1] != b"\x00":
                raise ValueError(f"{rel} 已加密，無法翻譯")
        return rels

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        rel = "Data/BasicData/CommonEvent.dat"
        maps = sorted((game_dir / "Data" / "MapData").glob("*.mps"))
        gbk = sum(_map(Backup(game_dir).original_bytes(p.relative_to(game_dir).as_posix())).gbk for p in maps)
        if (game_dir / rel).is_file():
            gbk += _common(Backup(game_dir).original_bytes(rel)).gbk
        if gbk >= 20:
            return [LangPack("zh", rel, f"已有約 {gbk} 句 GBK 中文（他人漢化，需以中文語系執行）；這些句子不會翻譯")]
        return []

    def title(self, game_dir: Path, read) -> str:
        try:
            r = _Reader(read("Data/BasicData/Game.dat"))
            r.expect(_GAME)
            r.take(r.u32())
            r.u32()
            return r.string()
        except (OSError, KeyError, ValueError):
            return ""

    def decode(self, rel: str, raw: bytes, game_dir: Path | None = None) -> tuple[str, dict]:
        if rel.endswith(".mps"):
            found = _map(raw)
        elif rel.endswith("CommonEvent.dat"):
            found = _common(raw)
        else:
            project = Backup(game_dir).original_bytes(rel[:-4] + ".project")
            found = _database(raw, _project(project))
        found = found.found
        entries = [{"loc": f"@{start}", "kind": kind, "text": text} for start, _, text, kind in found]
        return json.dumps(entries, ensure_ascii=False), {"encoding": "utf-8", "bom": False, "newline": "\n"}

    def parse(self, rel: str, text: str) -> list[Segment]:
        return [Segment(e["loc"], e["kind"], e["text"]) for e in json.loads(text)]

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError("Wolf 以 outputs() 重寫整個檔案")

    def wrap_rule(self, kind: str) -> bool:
        return kind == "dialogue"

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]]:
        backup = Backup(game_dir)
        game_rel = "Data/BasicData/Game.dat"
        game = backup.original_bytes(game_rel)
        used = set()
        for p in (game_dir / "Data").rglob("*"):
            if p.suffix.lower() in (".dat", ".project", ".mps") and p.is_file():
                used.update(backup.original_bytes(p.relative_to(game_dir).as_posix()).decode("cp932", "ignore"))
        subs = cp932_substitutes({ch for r in repl.values() for v in r.values() for ch in v}, used)
        out = {}
        for rel in texts:
            raw, r = backup.original_bytes(rel), repl.get(rel, {})
            spans = []
            for e in json.loads(texts[rel]):
                if e["loc"] in r:
                    start = int(e["loc"][1:])
                    end = start + 4 + struct.unpack_from("<I", raw, start)[0]
                    spans.append((start, end, _encoded("".join(subs.get(ch, ch) for ch in r[e["loc"]]))))
            out[rel] = (_splice(raw, spans), len(r))
        if not subs:
            out[game_rel] = (game, 0)
            return out
        start, end, name = _game(game)
        base = _system_font(set(subs), [name])
        if base is None:
            raise ValueError("找不到 Windows 中文字型（微軟正黑體／雅黑體），無法顯示 CP932 沒有的字")
        # The game crashes when the font name changes length (tested on Wolf 2.2x), so keep its byte length.
        n = end - start - 5
        if n < 4:
            raise ValueError("Game.dat 的字型名稱太短，無法改用替換字型")
        family = (_FAMILY + "_" * n)[:n]
        out[game_rel] = (_splice(game, [(start, end, _encoded(family))]), 0)
        out[_FONT] = (_font(base, {v: k for k, v in subs.items()}, family), 0)
        return out
