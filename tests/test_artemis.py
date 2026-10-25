import tempfile
import unittest
from pathlib import Path

from yakupatch import core, engines, vfs
from yakupatch.engines.artemis import Artemis
from yakupatch.exchange import Store
from yakupatch.extract import project_dir

from helpers import snapshot

AST = """astver = 2.0
ast = {
\tblock_00000 = {
\t\t{"bg", file="zbg06c", path=":bg/"},
\t\t{"text"},
\t\ttext = {
\t\t\tja = {
\t\t\t\t{
\t\t\t\t\tname = {"虎鉄"},
\t\t\t\t\t"「……そろそろか」",
\t\t\t\t\t{"rt2"},
\t\t\t\t\t[["引用"も]],
\t\t\t\t\t{"rt2"},
\t\t\t\t},
\t\t\t},
\t\t\ten = {
\t\t\t\t{
\t\t\t\t\t"English line",
\t\t\t\t},
\t\t\t},
\t\t},
\t\tlinknext = "block_00001",
\t},
\t["0001_00002"] = {
\t\t{"select", label="select1", exp="f.a=1", text="select"},
\t\tselect = {
\t\t\tja = {
\t\t\t\t"Ａ．信頼",
\t\t\t\t"Ｂ．愛情",
\t\t\t},
\t\t},
\t},
}
""".replace("\n", "\r\n")
TBL = ('tooltip = {\r\n\tlock = "システムメニューを固定します。",\r\n\tid = "lock",\r\n'
       '\tprohibit_head = "！？ァィゥェォッャュョ",\r\n}\r\n')


class PfsFormat(unittest.TestCase):
    def test_write_read_both_versions(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        files = {"script/a.ast": AST.encode(), "system/b.tbl": b""}
        for magic in (b"pf6", b"pf8"):
            (tmp / "a.pfs").write_bytes(vfs.Pfs.write(files, magic))
            a = vfs.Pfs(tmp / "a.pfs")
            self.assertEqual((a.magic, {k: a.read(k) for k in a.files}), (magic, files))


class ArtemisParse(unittest.TestCase):
    def test_ja_messages_names_choices_and_tables(self):
        k = Artemis()
        segs = k.parse("a.pfs!script/a.ast", AST)
        self.assertEqual([(s.loc, s.kind, s.text, s.speaker) for s in segs],
                         [("L8n", "name", "虎鉄", None), ("L9", "dialogue", "「……そろそろか」", "虎鉄"),
                          ("L11", "dialogue", '"引用"も', "虎鉄"), ("L27", "choice", "Ａ．信頼", None),
                          ("L28", "choice", "Ｂ．愛情", None)])
        self.assertEqual(segs[1].group, segs[2].group)
        self.assertEqual(k.render("a.ast", AST, {s.loc: s.text for s in segs}).count("\r\n"), AST.count("\r\n"))
        out = k.render("a.ast", AST, {"L8n": "虎鐵", "L11": '有"引號"\\'})
        self.assertIn('name = {"虎鐵"},\r\n', out)
        self.assertIn('\t"有\\"引號\\"\\\\",\r\n', out)
        self.assertEqual([s.text for s in k.parse("a.tbl", TBL)], ["システムメニューを固定します。"])


class ArtemisPatch(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        self.game, self.home = tmp / "games" / "art", tmp / "home"
        self.game.mkdir(parents=True)
        (self.game / "game.pfs").write_bytes(vfs.Pfs.write({"script/a.ast": AST.encode(), "system/x.tbl": TBL.encode()}))
        # A later archive overrides a.ast: it is what the game shows, so it is what we translate.
        (self.game / "game.pfs.003").write_bytes(vfs.Pfs.write({"script/a.ast": AST.replace("そろそろか", "まだか").encode()}))
        self.plugin = engines.detect(self.game)[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store, force=True)

    def test_new_archive_and_restore(self):
        self.assertIsInstance(self.plugin, Artemis)
        self.assertEqual(sorted(self.store.manifest["files"]), ["game.pfs!system/x.tbl", "game.pfs.003!script/a.ast"])
        before = snapshot(self.game)
        self.store.update_many("t", [(r["id"], {"dst": "譯" + r["src"].replace("\n", "\n譯"), "status": "translated"})
                                     for r in self.store.records.values() if r["status"] == "pending"])
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        self.assertEqual({k: (self.game / k).read_bytes() for k in before}, before)        # originals untouched
        patch = vfs.Pfs(self.game / "game.pfs.004")
        self.assertIn('"譯「……まだか」"', patch.read("script/a.ast").decode())
        self.assertIn('lock = "譯システム', patch.read("system/x.tbl").decode())
        self.assertEqual(self.plugin.locate(self.game), ["game.pfs!system/x.tbl", "game.pfs.003!script/a.ast"])
        core.restore(self.game)
        after = snapshot(self.game)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_yakupatch")}, before)

    def test_builtin_chinese_is_reported(self):
        (self.game / "game.pfs.005").write_bytes(vfs.Pfs.write({"script/a.ast": AST.replace("\ten = {", "\tcn = {").encode()}))
        self.assertEqual([p.lang for p in self.plugin.language_packs(self.game)], ["zh"])


if __name__ == "__main__":
    unittest.main()
