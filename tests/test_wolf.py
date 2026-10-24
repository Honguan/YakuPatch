import json
import struct
import tempfile
import unittest
from pathlib import Path

from yakupatch import core, engines
from yakupatch.encoding import cp932_substitutes
from yakupatch.engines import wolf
from yakupatch.engines.wolf import Wolf
from yakupatch.exchange import Store
from yakupatch.extract import project_dir

from helpers import snapshot


def i32(*values: int) -> bytes:
    return b"".join(struct.pack("<I", v) for v in values)


def s(text: str) -> bytes:
    data = text.encode("cp932") + b"\x00"
    return i32(len(data)) + data


def cmd(cid: int, args=(), strings=(), move: bool = False) -> bytes:
    out = bytes([len(args) + 1]) + i32(cid, *args) + b"\x00" + bytes([len(strings)]) + b"".join(map(s, strings))
    # A move route: 6 bytes, then one route command with one argument.
    return out + (b"\x01" + bytes(6) + i32(1) + b"\x05\x01" + i32(3) + b"\x01\x00" if move else b"\x00")


COMMANDS = [cmd(101, (), ["「こんにちは\n　元気？」"]), cmd(102, (2,), ["はい", "いいえ"]), cmd(201, (1,), move=True),
            cmd(150, (0x20, 1), ["\\f[16]ステータス"]), cmd(150, (0x00, 1), ["Picture/立ち絵.png"]),
            cmd(122, (1,), ["個"]), cmd(122, (1,), ["SE/決定.ogg"]), cmd(103, (), ["メモ"]), cmd(0)]
BLOCK = i32(len(COMMANDS)) + b"".join(COMMANDS)
PAGE = b"\x79" + i32(0) + s("") + bytes(4 + 37 + 4 + 2) + i32(0) + BLOCK + i32(3) + bytes(3) + b"\x7a"
MPS = (wolf._MAP + i32(0, 0x64) + b"\x65" + s("なし") + i32(1, 1, 1, 1) + bytes(12)
       + b"\x6f" + b"\x39\x30\x00\x00" + i32(0) + s("EV001") + i32(0, 0, 1) + bytes(4) + PAGE + b"\x70" + b"\x66")
COMMON = (wolf._COMMON + i32(1) + b"\x8e" + bytes(15) + s("共通") + BLOCK + s("") + s("") + b"\x8f"
          + i32(10) + s("") * 10 + i32(10) + bytes(10) + i32(10) + i32(0) * 10 + i32(10) + i32(0) * 10
          + bytes(0x1D) + s("") * 100 + b"\x91" + s("") + b"\x91" + b"\x8f")
PROJECT = (i32(1) + s("アイテム") + i32(3) + s("値段") + s("名前") + s("画像") + i32(1) + s("薬草") + s("")
           + i32(0x64) + bytes([0, 0, 3]) + bytes(0x61) + i32(0) * 4)
DAT = wolf._DB + i32(1) + b"\xfe\xff\xff\xff" + i32(0) + i32(3, 0x3E8, 0x7D0, 0x7D1) + i32(1, 5) + s("薬草") + s("Icon/薬.png") + b"\xc1"


def game_dat(font: str) -> bytes:
    body = (wolf._GAME + i32(2) + b"\x00\x00" + i32(9) + s("テスト") + s("0000-0000") + i32(0)
            + s(font) + s("") * 3 + s("") + s("1.00"))
    return body + i32(len(body) + 4 + 3 - 1) + b"end"


class WolfFormat(unittest.TestCase):
    def test_parse_maps_common_events_and_database(self):
        texts = [(e["kind"], e["text"]) for e in json.loads(Wolf().decode("a.mps", MPS)[0])]
        expected = [("dialogue", "「こんにちは\n　元気？」"), ("choice", "はい"), ("choice", "いいえ"),
                    ("ui", "\\f[16]ステータス"), ("other", "個")]
        self.assertEqual(texts, expected)
        self.assertEqual([(e["kind"], e["text"]) for e in json.loads(Wolf().decode("CommonEvent.dat", COMMON)[0])],
                         expected)
        found = wolf._database(DAT, wolf._project(PROJECT)).found
        self.assertEqual([(k, t) for _, _, t, k in found], [("ui", "薬草")])     # the file field (type 3) is skipped

    def test_gbk_text_is_left_alone(self):
        gbk = "\\E你好世界".encode("gbk").decode("cp932", "replace")
        raw = MPS.replace(s("「こんにちは\n　元気？」"), i32(len(gbk.encode("cp932", "replace")) + 1)
                          + gbk.encode("cp932", "replace") + b"\x00")
        r = wolf._map(raw)
        self.assertEqual((r.gbk, r.found[0][2]), (1, "はい"))

    def test_substitutes_are_unused_cp932_kanji(self):
        subs = cp932_substitutes(set("這你嗎說"), used={"纊"})
        self.assertEqual(sorted(subs), ["你", "嗎", "說"])                       # 這 is in CP932
        for stand_in in subs.values():
            stand_in.encode("cp932")
        self.assertNotIn("纊", subs.values())


class WolfPatch(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        self.game, self.home = tmp / "games" / "wolf", tmp / "home"
        basic = self.game / "Data" / "BasicData"
        basic.mkdir(parents=True)
        (self.game / "Data" / "MapData").mkdir()
        (self.game / "Game.exe").write_bytes(b"MZ")
        (self.game / "Data" / "MapData" / "Map001.mps").write_bytes(MPS)
        (basic / "CommonEvent.dat").write_bytes(COMMON)
        (basic / "DataBase.project").write_bytes(PROJECT)
        (basic / "DataBase.dat").write_bytes(DAT)
        (basic / "Game.dat").write_bytes(game_dat("ＭＳ ゴシック"))
        self.plugin = engines.detect(self.game)[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store, force=True)

    def test_apply_with_font_and_restore(self):
        self.assertIsInstance(self.plugin, Wolf)
        before = snapshot(self.game)
        self.store.update_many("t", [(r["id"], {"dst": "你" + r["src"], "status": "translated"})
                                     for r in self.store.records.values() if r["status"] == "pending"])
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        stand_in = cp932_substitutes({"你"}, set())["你"]
        texts = [e["text"] for e in json.loads(self.plugin.decode("a.mps", (self.game / "Data/MapData/Map001.mps").read_bytes())[0])]
        self.assertTrue(texts and all(t.startswith(stand_in) for t in texts))
        game = (self.game / "Data/BasicData/Game.dat").read_bytes()
        self.assertEqual((wolf._game(game)[2], len(game)), ("YakuPatch____", len(game_dat("ＭＳ ゴシック"))))
        self.assertTrue((self.game / "Data/YakuPatch.ttf").is_file())
        core.restore(self.game)
        after = snapshot(self.game)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_yakupatch")}, before)

    def test_encrypted_data_is_refused(self):
        (self.game / "Data/BasicData/CommonEvent.dat").write_bytes(b"\x01" + COMMON[1:])
        with self.assertRaises(ValueError):
            self.plugin.locate(self.game)


if __name__ == "__main__":
    unittest.main()
