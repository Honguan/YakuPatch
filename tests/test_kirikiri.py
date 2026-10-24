import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from yakupatch import core, engines, vfs
from yakupatch.engines.kirikiri import KiriKiri
from yakupatch.exchange import Store
from yakupatch.extract import project_dir
from helpers import snapshot

KS = """*start|開始
;【コメント】
[eval exp="sf.cgmode[1] = 1"]
【 美優 】[r]
「おはようございます。[r]
今日もいい天気ですね」[np]

[select_button left=290 text=……おはよう target=*a]
[ruby text="みゆ"]美優は笑った。[np]
//---------------
"""


def xp3_v2(files: dict[str, bytes]) -> bytes:
    """2.32+ layout: cushion header at 0x17 chaining to a zlib index; members zlib-compressed."""
    body, index = b"", b""
    start = 11 + 8 + 4 + 1 + 8 + 8
    for name, data in files.items():
        packed = zlib.compress(data)
        info = struct.pack("<IQQH", 0, len(data), len(packed), len(name)) + name.encode("utf-16-le")
        segm = struct.pack("<IQQQ", 1, start + len(body), len(data), len(packed))
        chunk = b"info" + struct.pack("<Q", len(info)) + info + b"segm" + struct.pack("<Q", len(segm)) + segm
        index += b"File" + struct.pack("<Q", len(chunk)) + chunk
        body += packed
    zindex = zlib.compress(index)
    return (vfs.XP3_MAGIC + struct.pack("<Q", 0x17) + struct.pack("<I", 1) + b"\x80" + struct.pack("<QQ", 0, start + len(body))
            + body + b"\x01" + struct.pack("<QQ", len(zindex), len(index)) + zindex)


class Xp3Format(unittest.TestCase):
    def test_read_v2_and_write_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        files = {"scenario/a.ks": "テスト".encode("cp932"), "system/Config.tjs": b";x"}
        (tmp / "v2.xp3").write_bytes(xp3_v2(files))
        a = vfs.Xp3(tmp / "v2.xp3")
        self.assertEqual({k: a.read(k) for k in a.files}, files)
        (tmp / "w.xp3").write_bytes(vfs.Xp3.write({"a.ks": b"abc", "b.ks": b""}))
        b = vfs.Xp3(tmp / "w.xp3")
        self.assertEqual({k: b.read(k) for k in b.files}, {"a.ks": b"abc", "b.ks": b""})


class KiriKiriParse(unittest.TestCase):
    def test_speaker_choice_and_round_trip(self):
        k = KiriKiri()
        segs = k.parse("data.xp3!scenario/a.ks", KS)
        got = [(s.loc, s.kind, s.text, s.speaker) for s in segs]
        self.assertEqual(got, [("L3【", "name", "美優", None),
                               ("L4", "dialogue", "「おはようございます。[r]", "美優"),
                               ("L5", "dialogue", "今日もいい天気ですね」[np]", "美優"),
                               ("L7@0.text", "choice", "……おはよう", None),
                               ("L8", "dialogue", '[ruby text="みゆ"]美優は笑った。[np]', None)])
        self.assertEqual(k.render("a.ks", KS, {s.loc: s.text for s in segs}), KS)
        out = k.render("a.ks", KS, {"L3【": "美優優", "L7@0.text": "……早安"})
        self.assertIn("【 美優優 】[r]\n", out)
        self.assertIn('text="……早安"', out)


    def test_tjs_whitelisted_strings_only(self):
        k = KiriKiri()
        tjs = ('\tmenu.add(this.m = new KAGMenuItem(this, "システム(&S)", 0, "", false));\r\n'
               '// x.caption = "コメント";\r\n\tsuper.onCloseQuery(askYesNo("終了しますか？"));\r\n'
               '\tfont.face = "ＭＳ Ｐ明朝";\r\n')
        segs = k.parse("data.xp3!system/Menus.tjs", tjs)
        self.assertEqual([(s.kind, s.text) for s in segs], [("ui", "システム(&S)"), ("system", "終了しますか？")])
        self.assertEqual(k.render("a.tjs", tjs, {s.loc: s.text for s in segs}), tjs)
        out = k.render("a.tjs", tjs, {segs[0].loc: "系統(&S)", segs[1].loc: '要"結束"嗎？\n'})
        self.assertIn('KAGMenuItem(this, "系統(&S)", 0', out)
        self.assertIn('askYesNo("要\\"結束\\"嗎？\\n")', out)            # stays one valid TJS string literal


class KiriKiriPatch(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        self.game, self.home = tmp / "games" / "kk", tmp / "home"
        self.game.mkdir(parents=True)
        (self.game / "data.xp3").write_bytes(xp3_v2({
            "scenario/first.ks": KS.encode("cp932"), "scenario/other.ks": "*a\n終わり。[np]\n".encode("cp932"),
            "others/first.ks": "同名。[np]\n".encode("cp932"),
            "system/Config.tjs": ";System.title = \"テスト\";\n".encode("cp932")}))
        # An existing game patch that replaces other.ks: it is what the game shows, so it is what we translate.
        (self.game / "patch.xp3").write_bytes(vfs.Xp3.write({"other.ks": "*a\n修正版。[np]\n".encode("cp932")}))
        self.plugin = engines.detect(self.game)[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store, force=True)

    def test_new_patch_archive_and_restore(self):
        self.assertIsInstance(self.plugin, KiriKiri)
        self.assertEqual(self.plugin.title(self.game, lambda r: vfs.read_current(self.game, r)), "テスト")
        # first.ks exists twice in data.xp3; one flat patch file would replace both, so neither is touched.
        self.assertEqual(sorted(self.store.manifest["files"]), ["data.xp3!system/Config.tjs", "patch.xp3!other.ks"])
        before = snapshot(self.game)
        self.store.update_many("t", [(r["id"], {"dst": "譯" + r["src"], "status": "translated"})
                                     for r in self.store.records.values() if r["status"] == "pending"])
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        self.assertEqual((self.game / "patch.xp3").read_bytes(), before["patch.xp3"])     # originals untouched
        p2 = vfs.Xp3(self.game / "patch2.xp3")
        self.assertEqual(p2.read("other.ks").decode("utf-16"), "*a\n譯修正版。[np]\n")
        self.assertEqual(self.plugin.locate(self.game), ["data.xp3!system/Config.tjs", "patch.xp3!other.ks"])  # ours is not a source
        core.restore(self.game)
        after = snapshot(self.game)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_yakupatch")}, before)


if __name__ == "__main__":
    unittest.main()
