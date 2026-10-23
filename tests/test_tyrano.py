import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from yakupatch import core, engines, vfs
from yakupatch.engines.tyrano import Tyrano
from yakupatch.exchange import Store
from yakupatch.extract import project_dir
from helpers import snapshot

KS = """*start
[cm]
#あかり:smile
今日はいい天気ですね。[l][r]
散歩に行きませんか？[p]
; comment
#
[glink text="行く" target=*go]
[ptext text=&f.name layer=1]
[iscript]
f.x = "スクリプト";
[endscript]
/*
ブロックコメント
*/
@chara_new name=akari jname="あかり"
*go
#  ゆう
うん、行こう！[p]
""".replace("#  ゆう", "#  ゆう  ")


def asar(files: dict[str, bytes], integrity: bool = True) -> bytes:
    tree, data = {"files": {}}, b""
    for inner, body in files.items():
        node = tree
        for part in inner.split("/")[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        entry = {"size": len(body), "offset": str(len(data))}
        if integrity:
            entry["integrity"] = vfs._integrity(body)
        node["files"][inner.rsplit("/", 1)[-1]] = entry
        data += body
    js = json.dumps(tree, separators=(",", ":")).encode()
    pickle = struct.pack("<II", 4 + len(js) + (-len(js) % 4), len(js)) + js + b"\0" * (-len(js) % 4)
    return struct.pack("<II", 4, len(pickle)) + pickle + data


class TyranoParse(unittest.TestCase):
    def test_segments_skip_code_and_round_trip(self):
        t = Tyrano()
        segs = t.parse("a.ks", KS)
        got = [(s.loc, s.kind, s.text) for s in segs]
        self.assertEqual(got, [("L2#", "name", "あかり"), ("L3", "dialogue", "今日はいい天気ですね。[l][r]"),
                               ("L4", "dialogue", "散歩に行きませんか？[p]"), ("L7@0.text", "choice", "行く"),
                               ("L15@0.jname", "name", "あかり"), ("L17#", "name", "ゆう"),
                               ("L18", "dialogue", "うん、行こう！[p]")])
        self.assertEqual(segs[1].group, segs[2].group)                    # one message until [p]
        self.assertEqual(segs[1].speaker, "あかり")
        self.assertEqual(t.render("a.ks", KS, {s.loc: s.text for s in segs}), KS)   # identity keeps the bytes
        crlf = KS.replace("\n", "\r\n")
        repl = {"L17#": "優", "L7@0.text": '說"走"', "L4": t.fit("dialogue", "#要不要\n去散步？[p]")}
        out = t.render("a.ks", crlf, repl)
        self.assertIn("#  優  \r\n", out)                                  # spacing and CRLF kept
        self.assertIn("[glink text='說\"走\"' target=*go]", out)          # quote style switched, not broken
        self.assertIn("\r\n＃要不要[r]去散步？[p]\r\n", out)               # cannot start a line with #
        back = {s.loc: s.text for s in t.parse("a.ks", out)}
        self.assertEqual(back["L4"], "＃要不要[r]去散步？[p]")


class TyranoAsar(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        self.game, self.home = tmp / "games" / "ty", tmp / "home"
        (self.game / "resources").mkdir(parents=True)
        self.members = {"data/scenario/first.ks": KS.encode("utf-8"), "data/scenario/other.ks": "*a\n終わり。[p]\n".encode(),
                        "data/system/Config.tjs": ";System.title = テスト;\n".encode(), "tyrano/libs.js": b"//"}
        self.packed = asar(self.members)
        (self.game / "resources" / "app.asar").write_bytes(self.packed)
        self.plugin = engines.detect(self.game)[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store, force=True)

    def translate(self, pick=lambda rec: True):
        self.store.update_many("t", [(r["id"], {"dst": "譯" + r["src"], "status": "translated"})
                                     for r in self.store.records.values() if r["status"] == "pending" and pick(r)])

    def test_patch_header_in_place_and_restore(self):
        self.assertIsInstance(self.plugin, Tyrano)
        self.assertEqual(self.plugin.title(self.game, lambda r: vfs.read_current(self.game, r)), "テスト")
        self.assertEqual(sorted(self.store.manifest["files"]), ["resources/app.asar!data/scenario/first.ks",
                                                               "resources/app.asar!data/scenario/other.ks"])
        before = snapshot(self.game)
        self.translate(lambda r: r["refs"][0]["file"].endswith("first.ks"))
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        path = self.game / "resources" / "app.asar"
        self.assertEqual(len(path.read_bytes()), len(self.packed))                  # no data moved
        self.assertEqual(path.read_bytes()[-40:], self.packed[-40:])
        a = vfs.Asar(path)
        entry = a.files["data/scenario/first.ks"]
        new = a.read("data/scenario/first.ks")
        self.assertTrue(entry["unpacked"])
        self.assertIn("譯今日はいい天気ですね。", new.decode("utf-8"))
        if "integrity" in entry:
            self.assertEqual(entry["integrity"]["hash"], hashlib.sha256(new).hexdigest())
        self.assertNotIn("unpacked", a.files["data/scenario/other.ks"])
        self.assertEqual(a.read("tyrano/libs.js"), b"//")
        unpacked = self.game / "resources" / "app.asar.unpacked" / "data" / "scenario" / "first.ks"
        self.assertEqual(unpacked.read_bytes(), new)
        # Untranslating every line reverts the member: its unpacked copy goes, the header is the original again.
        self.store.update_many("u", [(r["id"], {"dst": "", "status": "pending"})
                                     for r in self.store.records.values() if r["status"] == "translated"])
        self.translate(lambda r: r["refs"][0]["file"].endswith("other.ks"))
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        self.assertFalse(unpacked.exists())
        self.assertTrue(vfs.Asar(path).files["data/scenario/other.ks"]["unpacked"])
        core.restore(self.game)
        after = snapshot(self.game)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_yakupatch")}, before)
        self.assertFalse((self.game / "resources" / "app.asar.unpacked").exists())


if __name__ == "__main__":
    unittest.main()
