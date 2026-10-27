import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yakupatch import core, engines
from yakupatch.exchange import Store
from yakupatch.extract import LanguageGateStop, project_dir
from helpers import copy_game, fake_translate, snapshot
from test_codex import IdsFake


class CoreApi(unittest.TestCase):
    def setUp(self):
        self.game, self.home = copy_game("mz_min")
        self.plugin = engines.PLUGINS[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store)
        env = patch.dict(os.environ, {"YAKUPATCH_HOME": str(self.home.parent / "yh")})
        env.start()
        self.addCleanup(env.stop)

    def test_batch_runs_each_game_and_reports_failures(self):
        other, _ = copy_game("mv_min")
        before = snapshot(other)
        missing = self.home.parent / "no-such-game"
        seen = []
        results = core.batch([self.game, missing, other], IdsFake, self.home, apply=True, progress=seen.append)
        self.assertEqual([r["status"] for r in results], ["ok", "error", "ok"])
        self.assertTrue(all(r["translated"] > 0 and r["files"] > 0 for r in (results[0], results[2])))
        self.assertNotEqual(snapshot(other), before)                       # extracted, translated and written back
        self.assertEqual({s["index"] for s in seen}, {0, 2})
        stages = [s["stage"] for s in seen if s["index"] == 2]
        self.assertEqual((stages[0], stages[1], stages[-1]), ("提取", "翻譯", "寫回"))    # self.game was extracted already
        self.assertEqual(core.batch([self.game], IdsFake, self.home)[0]["translated"], 0)   # nothing left to do

    def test_batch_stops_when_the_user_must_act(self):
        from yakupatch.backends import UsageLimit
        other, _ = copy_game("mv_min")
        quota = lambda: IdsFake(lambda texts, n: (_ for _ in ()).throw(UsageLimit("quota")))
        results = core.batch([self.game, other], quota, self.home)
        self.assertEqual([r["status"] for r in results], ["paused"])        # the second game is not started

    def coded(self):
        return next(r for r in self.store.records.values()
                    if r["status"] == "pending" and core.masked(r, self.plugin)[2])

    def test_edit_validates_marks_reviewed_and_undoes(self):
        rec = self.coded()
        src, dst, masks = core.masked(rec, self.plugin)
        self.assertEqual(dst, "")
        self.assertIn("placeholders", core.edit(self.store, self.plugin, rec["id"], "沒有佔位符的譯文"))
        self.assertEqual(self.store.records[rec["id"]]["status"], "pending")      # rejected edits change nothing
        good = fake_translate(src)
        self.assertEqual(core.edit(self.store, self.plugin, rec["id"], good), [])
        saved = self.store.records[rec["id"]]
        self.assertEqual((saved["status"], saved["by"]), ("reviewed", "manual"))
        self.assertTrue(all(code in saved["dst"] for code in masks.values()))      # control codes restored
        self.assertEqual(core.masked(saved, self.plugin)[1], good)                 # round-trips to the same tags
        run = core.history(self.store)[-1]["run"]
        core.undo(self.store, run)
        self.assertEqual(self.store.records[rec["id"]]["status"], "pending")

    def test_mark_reviewed_and_apply_same(self):
        a = next(r for r in self.store.records.values() if r["status"] == "pending")
        twin = {**a, "id": "twin", "kind": "other", "refs": a["refs"]}
        self.store.replace({**self.store.records, "twin": twin}, self.store.manifest)
        self.assertEqual(core.apply_same(self.store, a["id"]), 0)                   # nothing to copy yet
        core.edit(self.store, self.plugin, a["id"], fake_translate(core.masked(a, self.plugin)[0]))
        self.assertEqual(core.apply_same(self.store, a["id"]), 1)
        self.assertEqual(self.store.records["twin"]["dst"], self.store.records[a["id"]]["dst"])
        core.undo(self.store, core.history(self.store)[-1]["run"])
        self.store.update_many("t", [("twin", {"dst": "譯文", "status": "translated"})])
        self.assertEqual(core.mark_reviewed(self.store, ["twin", a["id"]]), 1)     # a is already reviewed
        self.assertEqual(self.store.records["twin"]["status"], "reviewed")

    def test_retranslate_selected_skips_cache_and_reviewed(self):
        ids = [r["id"] for r in self.store.records.values() if r["status"] == "pending"][:2]
        backend = IdsFake()
        core.translate(self.store, self.plugin, backend, only=ids)
        self.assertEqual({self.store.records[i]["status"] for i in ids}, {"translated"})
        calls = len(backend.calls)
        self.assertEqual(core.estimate(self.store, self.plugin, backend, only=ids, fresh=True)["cached"], 0)
        result = core.translate(self.store, self.plugin, backend, only=ids, fresh=True)
        self.assertEqual(result["translated"], 2)
        self.assertGreater(len(backend.calls), calls)                              # fresh: sent again
        core.mark_reviewed(self.store, ids[:1])
        self.assertEqual(core.translate(self.store, self.plugin, backend, only=ids, fresh=True)["total"], 1)

    def test_glossary_read_only_while_translating(self):
        rows = core.glossary(self.store)
        self.assertTrue(rows)
        rows[0][1:] = ["莉莉亞", "人名", "manual"]
        core.save_glossary(self.store, rows)
        self.assertEqual(core.glossary(self.store)[0], rows[0])
        seen = []

        class Probe(IdsFake):
            def complete(inner, messages, temperature=None):
                with self.assertRaises(ValueError):
                    core.save_glossary(self.store, rows)
                seen.append(True)
                return super().complete(messages, temperature)

        core.translate(self.store, self.plugin, Probe(), limit=1)
        self.assertTrue(seen)
        self.assertFalse(self.store.busy)
        core.save_glossary(self.store, rows)                                       # writable again
        with self.assertRaises(ValueError):
            core.save_glossary(self.store, rows + [rows[0]])                       # duplicate source

    def test_config_keeps_other_keys_and_validates(self):
        path = core.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sakura": {"api_base": "http://10.0.0.2:8080"}, "mine": 1}), "utf-8")
        core.save_config("codex", {"model": "m", "effort": "low", "output_script": "zh-Hans", "privacy_ack": True})
        data = json.loads(path.read_text("utf-8"))
        self.assertEqual((data["mine"], data["sakura"]["api_base"]), (1, "http://10.0.0.2:8080"))
        self.assertEqual(core.codex_config().output_script, "zh-Hans")
        self.assertEqual(core.sakura_config().api_base, "http://10.0.0.2:8080")
        with self.assertRaises(ValueError):
            core.save_config("codex", {"output_script": "ja"})
        with self.assertRaises(ValueError):
            core.save_config("sakura", {"nope": 1})
        self.assertEqual(json.loads(path.read_text("utf-8")), data)               # failed saves change nothing

    def test_library_scan_rows_and_remove_keeps_game(self):
        lib = core.Library()
        mv, _ = copy_game("mv_min")
        parent = Path(tempfile.mkdtemp(prefix="yakupatch-lib-"))
        for i, g in enumerate((self.game, mv)):
            (parent / str(i)).mkdir()
            os.replace(g, parent / str(i) / g.name)
        added = lib.scan(parent)
        self.assertEqual(len(added), 2)
        self.assertEqual(core.Library().games.keys(), lib.games.keys())           # persisted
        row = lib.row(added[0])
        self.assertEqual((row["lang"], row["status"], row["no_need"]), ("日文", "未處理", False))
        before = snapshot(parent)
        lib.remove(added[0])
        self.assertNotIn(added[0], core.Library().games)
        self.assertEqual(snapshot(parent), before)                                 # the game is untouched
        with self.assertRaises(ValueError):
            lib.add(parent / "missing-game")

    def test_library_remembers_folders_and_survives_a_bad_game(self):
        lib = core.Library()
        parent = Path(tempfile.mkdtemp(prefix="yakupatch-lib-")).resolve()
        (parent / "0").mkdir()
        os.replace(self.game, parent / "0" / self.game.name)
        seen = []
        with patch.object(core.extraction, "probe", side_effect=AssertionError("failed to load il2cpp")):
            added = lib.scan(parent, progress=seen.append)
        self.assertEqual(lib.row(added[0])["status"], "不支援")                    # listed, not a failed scan
        self.assertEqual(lib.games[added[0]]["unsupported"], "failed to load il2cpp")
        self.assertEqual([s["stage"] for s in seen], ["尋找遊戲", "掃描"])
        self.assertEqual(core.Library().folders, [str(parent)])
        lib.remove_many(added)
        self.assertEqual(lib.rescan(), added)                                      # the remembered folder again
        self.assertEqual(lib.games[added[0]]["unsupported"], "")
        lib.forget_folder(str(parent))
        self.assertEqual(core.Library().folders, [])

    def test_language_gate_force_and_launch_without_exe(self):
        with patch("yakupatch.extract.language_gate", return_value={"primary": "zh-Hans", "kana_ratio": 0,
                                                                    "simp_only": 9, "trad_only": 0}):
            with self.assertRaises(LanguageGateStop):
                core.extract(self.game, self.plugin, self.store)
            self.assertEqual(core.extract(self.game, self.plugin, self.store, force=True)["primary"], "zh-Hans")
        with self.assertRaises(ValueError):
            core.launch(self.game)

    def test_confirmed_builtin_chinese_survives_redetection(self):
        lib = core.Library()
        key = lib.add(self.game)[0]
        lib.set_builtin_chinese(key, True)
        lib.redetect(key)
        lib.scan(self.game.parent)
        row = core.Library().row(key, self.home)
        self.assertTrue(row["no_need"])
        self.assertEqual(row["status"], "不需翻譯")              # even with an old extraction manifest
        lib.set_builtin_chinese(key, False)
        self.assertFalse(core.Library().row(key, self.home)["no_need"])

    def test_confirmed_chinese_blocks_single_and_batch_without_backend(self):
        lib = core.Library()
        key = lib.add(self.game)[0]
        lib.set_builtin_chinese(key, True)
        before = snapshot(self.game)
        with patch.object(IdsFake, "prepare", side_effect=AssertionError("must not contact backend")):
            with self.assertRaisesRegex(ValueError, "不需翻譯"):
                core.translate(self.store, self.plugin, IdsFake())
            with self.assertRaises(LanguageGateStop):
                core.extract(self.game, self.plugin, self.store)
            rows = core.batch([self.game], IdsFake, self.home, apply=True)
        self.assertEqual(rows[0]["status"], "skip")
        self.assertEqual(snapshot(self.game), before)

    def test_cli_single_translation_respects_chinese_mark(self):
        from yakupatch import cli
        lib = core.Library()
        lib.set_builtin_chinese(lib.add(self.game)[0], True)
        with patch.object(cli, "_backend", side_effect=AssertionError("must not create backend")):
            self.assertEqual(cli.main(["--home", str(self.home), "translate", str(self.game)]), 1)
            self.assertEqual(cli.main(["--home", str(self.home), "extract", str(self.game)]), 2)

    def test_batch_skips_builtin_chinese_even_if_already_extracted(self):
        from yakupatch.engines.base import LangPack
        with patch.object(self.plugin, "language_packs", return_value=[LangPack("zh", "scenario/zh", "中文")]), \
                patch.object(core, "translate") as translate:
            rows = core.batch([self.game], IdsFake, self.home, apply=True)
        self.assertEqual(rows[0]["status"], "skip")
        translate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
