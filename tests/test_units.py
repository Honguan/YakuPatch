import json
import unittest

from yakupatch import engines, exchange, translate, writeback
from yakupatch.backup import BACKUP_DIR, Backup
from yakupatch.encoding import decode, encode
from yakupatch.exchange import Store
from yakupatch.extract import LanguageGateStop, Unit, classify, extract, project_dir
from yakupatch.fsutil import sha256
from yakupatch.jsonspan import replace_strings, string_spans
from yakupatch.langdetect import probe, sentence_is_chinese
from yakupatch.safety import UnsafePath, safe_path

from helpers import copy_game, fill_batches, snapshot

MZ = engines.PLUGINS[0]


class Units(unittest.TestCase):
    def test_jsonspan(self):
        text = '{"a":[1,2.5e3,"x\\"y",null],"b/c":{"d":"日本"}, "e":true}'
        spans = string_spans(text)
        self.assertEqual(set(spans), {"/a/2", "/b~1c/d"})
        out = replace_strings(text, {"/b~1c/d": '中"文'})
        self.assertEqual(json.loads(out)["b/c"]["d"], '中"文')
        self.assertEqual(out.replace('"中\\"文"', '"日本"'), text)

    def test_plugin_command_nested_args(self):
        args = {"messageText": "どこから？", "choices": json.dumps([json.dumps({"label": "見る", "switchId": "0"}, ensure_ascii=False)], ensure_ascii=False)}
        text = json.dumps([{"list": [{"code": 357, "parameters": ["P", "c", "", args]}]}], ensure_ascii=False)
        segs = {s.loc: (s.kind, s.text) for s in MZ.parse("data/CommonEvents.json", text)}
        nested = "/0/list/0/parameters/3/choices~j/0~j/label"
        self.assertEqual(segs, {"/0/list/0/parameters/3/messageText": ("system", "どこから？"),
                                nested: ("system", "見る"), "/0/list/0/parameters/3/choices~j/0~j/switchId": ("other", "0")})
        out = MZ.render("data/CommonEvents.json", text, {nested: '看"一下'})
        choice = json.loads(json.loads(json.loads(out)[0]["list"][0]["parameters"][3]["choices"])[0])
        self.assertEqual(choice, {"label": '看"一下', "switchId": "0"})
        self.assertEqual(MZ.render("data/CommonEvents.json", out, {nested: "見る"}), text)

    def test_encoding_roundtrip(self):
        for raw in ["\ufeff{\"a\":\"あ\"}".encode("utf-8"), "あいう".encode("cp932"), "x".encode("utf-16")]:
            text, info = decode(raw)
            self.assertEqual(encode(text, info), raw)

    def test_safe_path(self):
        root = copy_game("mz_min")[0]
        for bad in ["../x", "..\\x", "C:/Windows/x", "/etc/passwd", "data/../../x"]:
            with self.assertRaises(UnsafePath):
                safe_path(root, bad)
        self.assertEqual(safe_path(root, "data/System.json"), (root / "data" / "System.json").resolve())

    def test_mask_validate(self):
        masked, masks = translate.mask("\\C[2]こんにちは\\C[0]\\{", MZ.control_codes)
        self.assertEqual(masked, "<0>こんにちは<1>")
        self.assertEqual(masks["<1>"], "\\C[0]\\{")
        self.assertEqual(translate.validate(masked, "<0>你好<1>", masks), [])
        self.assertIn("placeholders", translate.validate(masked, "<0>你好", masks))
        self.assertIn("kana_residue", translate.validate("今日はいい天気", "今日はいい天气", {}))
        self.assertIn("newlines", translate.validate("あ\nい", "啊", {}))
        self.assertIn("extra_text", translate.validate("こんにちは", "翻译：你好", {}))
        self.assertIn("repetition", translate.validate("はい", "是是是是是", {}))
        self.assertEqual(translate.validate("コンティニュー", "继续", {}), [])          # katakana loanword shrinks
        self.assertIn("length_ratio", translate.validate("今日はいい天気ですね", "嗯", {}))
        self.assertIn("length_ratio", translate.validate("コンティニュー", "继续" * 12, {}))
        alt, alt_masks = translate.mask("\\ct<1>番目", MZ.control_codes)
        self.assertEqual(alt, "⟦0⟧<1>番目")      # literal <1> in source switches placeholder style
        args, args_masks = translate.mask("%1の%2を %3 奪った！", MZ.control_codes)
        self.assertEqual(args, "<0>の<1>を <2> 奪った！")
        self.assertIn("placeholders", translate.validate(args, "<0>夺走了<1>！", args_masks))
        art, _ = translate.mask("[num]のデータを削除します。", engines.Artemis.control_codes)
        self.assertEqual(art, "<0>のデータを削除します。")

    def test_copied_kana_line_or_tag(self):
        self.assertIn("kana_residue", translate.validate("おじさん\n「やった！」", "おじさん\n「太好了！」", {}))
        self.assertIn("kana_residue", translate.validate("<0><おじさん>「おや」", "<0><おじさん>「哎呀」", {"<0>": "\\N"}))
        self.assertEqual(translate.validate("ひより\n「はい」", "日和\n「是」", {}), [])
        self.assertEqual(translate.validate("っ……\n「はい」", "っ……\n「是」", {}), [])     # one kana is not a copied line
        self.assertEqual(translate.validate("回想を見ますか？\nCG担当：もりの", "要看回想吗？\nCG担当：もりの", {}), [])

    def test_ruby_reading_dropped_and_code_text_uncertain(self):
        unity = engines.Unity()
        self.assertEqual(unity.fit("dialogue", '弄湿了<ruby="リラ">妹妹</ruby>的'), "弄湿了妹妹的")
        self.assertEqual(unity.fit("dialogue", '<ruby="Lila">妹</ruby>'), '<ruby="Lila">妹</ruby>')
        code = Unit("a", ["0"], "dialogue", ["//床装飾を塗る[0~1.0]", "floorPaintRate:0.4,", "", "//水場"], None)
        self.assertEqual(classify(code), ("pending", ["uncertain"]))
        self.assertEqual(classify(Unit("a", ["0"], "dialogue", ["//眠そうに"], None)), ("pending", ["uncertain"]))
        self.assertEqual(classify(Unit("a", ["0"], "dialogue", ["HP:回復した、", "よかった"], None)), ("pending", []))

    def test_language(self):
        self.assertFalse(sentence_is_chinese("魔法陣"))
        self.assertFalse(sentence_is_chinese("田中"))
        self.assertTrue(sentence_is_chinese("你好，这是中文。"))
        self.assertTrue(sentence_is_chinese("你說什麼呢"))
        self.assertEqual(probe(["这是简体中文的句子，我们今天去学校了。" * 5])["primary"], "zh-Hans")
        self.assertEqual(probe(["這是繁體中文的句子，我們今天去學校了。" * 5])["primary"], "zh-Hant")
        self.assertEqual(probe(["今日はいい天気ですね。魔法陣を描こう。" * 5])["primary"], "ja")

    def test_store_journal_replay_undo(self):
        game, home = copy_game("mz_min")
        store = Store(project_dir(game, MZ, home))
        store.replace({"a": {"id": "a", "dst": "", "status": "pending"}}, {"format": "x"})
        store.update_many("r1", [("a", {"dst": "甲", "status": "translated"})])
        with open(store.dir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write('{"run":"r2","id":"a","set":{"dst"')          # torn last line
        again = Store(store.dir)
        self.assertEqual(again.records["a"]["dst"], "甲")
        again.compact()
        self.assertEqual(Store(store.dir).undo("r1"), 1)
        self.assertEqual(Store(store.dir).records["a"]["status"], "pending")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.game, home = copy_game("mz_min")
        self.store = Store(project_dir(self.game, MZ, home))
        self.original = snapshot(self.game)

    def translate_all(self):
        extract(self.game, MZ, self.store)
        exchange.export_batches(self.store, MZ.control_codes)
        n = fill_batches(self.store.dir)
        files = sorted((self.store.dir / "batches" / "in").glob("*.jsonl"))
        ok, bad = exchange.import_batches(self.store, MZ.control_codes, files, exchange.new_run())
        self.assertEqual((ok, bad), (n, {}))

    def test_share_round_trip_without_source_text(self):
        from yakupatch import core
        self.translate_all()
        share = self.store.dir.parent / "game.yakupatch.jsonl"
        n = core.export_share(self.store, MZ, share)
        self.assertGreater(n, 0)
        body = share.read_text("utf-8")
        self.assertFalse(any(r["src"] in body for r in self.store.records.values() if len(r["src"]) > 2))
        other_game, home = copy_game("mz_min")                       # a second copy of the same game elsewhere
        other = Store(project_dir(other_game, MZ, home))
        extract(other_game, MZ, other)
        lines = body.splitlines()
        first = json.loads(lines[1])
        lines[1] = json.dumps({**first, "sha": "0" * 16}, ensure_ascii=False)   # source changed in this version
        share.write_text("\n".join(lines) + "\n", "utf-8")
        run, ok, bad = core.import_batches(other, MZ, [share])
        self.assertEqual((ok, bad), (n - 1, {"unknown_id": 1}))
        for rid, rec in other.records.items():
            if rid != first["id"] and rec["status"] == "translated":
                self.assertEqual(rec["dst"], self.store.records[rid]["dst"])
        self.assertEqual(other.undo(run), n - 1)
        with self.assertRaises(ValueError):
            exchange.import_share(other, "Wolf RPG", MZ.control_codes, share, "r")

    def test_extract_classification(self):
        extract(self.game, MZ, self.store)
        recs = {r["src"]: r for r in self.store.records.values()}
        self.assertEqual(recs["\\C[2]こんにちは！\\C[0]\n\n今日は\\N[1]と\\V[3]回目の冒険だね。\\{"]["speaker"], "リリア")
        self.assertEqual(recs["你好，这是中文。\n12345"]["status"], "skip")
        self.assertEqual((recs["ありがとう"]["kind"], recs["ありがとう"]["flags"]), ("system", []))  # 357 text arg
        self.assertIn("uncertain", recs["立ち絵_リリア"]["flags"])     # 357 file-like arg stays off by default
        self.assertEqual(recs["見る"]["kind"], "system")               # label inside a JSON-encoded struct arg
        self.assertEqual(recs["魔法陣"]["status"], "pending")
        self.assertEqual(len(recs["はい"]["refs"]), 2)               # 102 choice and 402 branch share one record
        srcs = set(recs)
        self.assertNotIn("エディタ用の名前", srcs)                     # MapInfos is editor-only
        self.assertNotIn("イベント完了", srcs)                         # System.switches is editor-only
        self.assertNotIn("text = ありがとう", srcs)                    # 657 is editor-only display
        self.assertTrue(any("$gameVariables" in s for s in srcs) is False)   # 355 script never extracted

    def test_full_cycle_apply_restore(self):
        self.translate_all()
        plan = writeback.plan(self.game, self.store, MZ)
        self.assertEqual(plan.warnings, [])
        writeback.commit(self.game, plan)
        mp = json.loads((self.game / "data" / "Map001.json").read_text("utf-8"))
        lines = [c["parameters"][0] for c in mp["events"][1]["pages"][0]["list"] if c["code"] == 401]
        self.assertEqual(lines[:3], ["\\C[2]一丁丂七丄丅\\C[0]", "", "一丁丂\\N[1]一\\V[3]一丁丂七丄丅丆万\\{"])
        self.assertTrue((self.game / "data" / "System.json").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertTrue((self.game / BACKUP_DIR / "manifest.json").exists())
        # Re-extract after apply still sees the originals, so nothing becomes orphan.
        extract(self.game, MZ, self.store)
        self.assertFalse(any(r["status"] == "orphan" for r in self.store.records.values()))
        self.assertEqual(writeback.plan(self.game, self.store, MZ).files, [])
        results = Backup(self.game).restore()
        self.assertTrue(all(r == "restored" for _, r in results))
        self.assertEqual(snapshot(self.game), self.original)

    def test_restore_skips_game_update(self):
        self.translate_all()
        writeback.commit(self.game, writeback.plan(self.game, self.store, MZ))
        (self.game / "data" / "Skills.json").write_bytes(b'[\nnull\n]\n')     # official update
        results = dict(Backup(self.game).restore())
        self.assertEqual(results["data/Skills.json"], "stale")
        self.assertEqual((self.game / "data" / "Skills.json").read_bytes(), b'[\nnull\n]\n')
        self.assertEqual(results["data/Map001.json"], "restored")

    def test_apply_refuses_changed_game(self):
        self.translate_all()
        (self.game / "data" / "Map001.json").write_text('{"events":[]}', "utf-8")
        with self.assertRaises(writeback.GameChanged):
            writeback.plan(self.game, self.store, MZ)

    def test_import_rejects_broken_lines(self):
        extract(self.game, MZ, self.store)
        exchange.export_batches(self.store, MZ.control_codes)
        part = next((self.store.dir / "batches" / "out").glob("part-*.jsonl"))
        rows = [json.loads(l) for l in part.read_text("utf-8").splitlines()]
        row = next(r for r in rows if "<0>" in r["src"])
        bad = self.store.dir / "bad.jsonl"
        bad.write_text(json.dumps(dict(row, dst="你好")) + "\n" + json.dumps(dict(row, id="nope", dst="x")) + "\n",
                       "utf-8")
        ok, rejected = exchange.import_batches(self.store, MZ.control_codes, [bad], "r")
        self.assertEqual((ok, rejected), (0, {"placeholders": 1, "unknown_id": 1}))

    def test_language_gate_stops_chinese_game(self):
        mp = self.game / "data" / "Map001.json"
        text = mp.read_text("utf-8")
        for ja, zh in [("こんにちは！", "你好！这是我们的村子。"), ("今日は", "今天是"), ("回目の冒険だね。", "次冒险吧。"),
                       ("番目の扉を開けた。", "号门打开了。"), ("遠い昔の物語。", "很久以前的故事。"),
                       ("それは静かに始まった。", "它静静地开始了。"), ("はじまりの村", "起始之村"),
                       ("はい", "是的"), ("いいえ", "不是"), ("ありがとう", "谢谢"), ("リリア", "莉莉娅"),
                       ("\\\\N[1]と", "\\\\N[1]和")]:
            text = text.replace(ja, zh)
        mp.write_text(text, "utf-8")
        for f in ["Actors.json", "Skills.json", "CommonEvents.json", "System.json"]:
            (self.game / "data" / f).unlink()
        with self.assertRaises(LanguageGateStop) as cm:
            extract(self.game, MZ, self.store)
        self.assertEqual(cm.exception.probe["primary"], "zh-Hans")


if __name__ == "__main__":
    unittest.main()
