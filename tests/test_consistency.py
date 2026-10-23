import json
import re
import tempfile
import unittest
from pathlib import Path

from yakupatch import core, engines, qa, writeback
from yakupatch.backends.codex import Codex, CodexConfig
from yakupatch.exchange import Store
from yakupatch.pipeline import Work, use_style

MZ = engines.PLUGINS[0]
CODES = re.compile("|".join(MZ.control_codes))


def rec(rid, src, dst="", status="translated", kind="dialogue", file="data/Map001.json", flags=None):
    return {"id": rid, "kind": kind, "speaker": None, "src": src, "dst": dst, "status": status, "by": "",
            "refs": [{"file": file, "locs": [f"/{rid}"]}], "flags": flags or [], "note": ""}


def store_with(*records) -> Store:
    store = Store(Path(tempfile.mkdtemp(prefix="yakupatch-test-")))
    store.replace({r["id"]: r for r in records}, {"files": {}})
    return store


class ConsistencyQA(unittest.TestCase):
    def test_planted_name_variant_term_and_gap_are_flagged(self):
        store = store_with(
            rec("n1", "リリア", "莉莉娅", kind="name"),
            rec("d1", "リリアは来た。", "莉莉娅来了。"),
            rec("d2", "リリアが笑う。", "莉莉亚笑了。"),                 # planted: same name, different translation
            rec("d3", "はい。", "是的。"),
            rec("d4", "「はい！」", "滚开！"),                           # same source but a very different translation
            rec("d5", "失敗した", "", status="failed"),
            rec("d6", "次へ", "下一个"))
        n = qa.check(store, {"王国": "王国"})
        flagged = {r["id"]: r["qa"] for r in store.records.values() if "inconsistent" in r["flags"]}
        self.assertEqual(flagged, {"d2": ["term:リリア"], "d3": ["variant:d4"], "d4": ["variant:d3"], "d5": ["gap"]})
        self.assertEqual(n, 4)
        self.assertEqual(core.history(store), [])                      # QA passes are not translation runs

        core.edit(store, MZ, "d2", "莉莉娅笑了。")                     # fixing it clears the flag on the next pass
        qa.check(store, {})
        self.assertNotIn("inconsistent", store.records["d2"]["flags"])
        self.assertNotIn("qa", store.records["d2"])


class LineWrap(unittest.TestCase):
    def test_wrap_respects_codes_and_closing_punctuation(self):
        self.assertEqual(writeback.width("ab\\C[1]あ", CODES), 2.0)
        self.assertEqual(writeback.wrap("\\C[2]あいうえおかきくけこ。さ\\C[0]", 5, CODES),
                         "\\C[2]あいうえお\nかきくけこ。\nさ\\C[0]")
        self.assertEqual(writeback.wrap("短い", 5, CODES), "短い")

    def test_limit_learned_from_source_lines_only_for_unwrapped_kinds(self):
        lines = [rec(f"d{i}", "あ" * 10, "中" * 30) for i in range(25)] + [rec("c", "あ" * 40, kind="choice")]
        store = store_with(*lines)
        self.assertEqual(writeback.wrap_limits(store, MZ), {"dialogue": 10})
        self.assertEqual(writeback.wrap_limits(store, engines.base.EnginePlugin()), {})


class Style(unittest.TestCase):
    def test_style_reaches_codex_prompt_and_cache_identity(self):
        store = store_with(rec("d1", "はい"))
        backend = Codex(CodexConfig())
        before = json.dumps(backend.cache_identity)
        core.save_style(store, "主角自稱「我」")
        use_style(store, backend)
        work = Work("d1", "はい", "はい", {}, ("", ""), [], None, {})
        self.assertIn("主角自稱「我」", backend.messages([work], {})[0]["content"])
        self.assertNotEqual(json.dumps(backend.cache_identity), before)
        core.save_style(store, "  ")
        self.assertEqual(core.style(store), "")


if __name__ == "__main__":
    unittest.main()
