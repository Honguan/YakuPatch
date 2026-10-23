import gzip
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from yakupatch import core, dicts, engines, pipeline
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir

from helpers import copy_game

JMNEDICT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE JMnedict [
<!ELEMENT JMnedict (entry*)>
<!ENTITY fem "female given name or forename">
<!ENTITY place "place name">
]>
<!-- JMnedict created: 2026-10-01 -->
<JMnedict>
<entry><ent_seq>1</ent_seq><k_ele><keb>莉々亜</keb></k_ele><r_ele><reb>りりあ</reb></r_ele>
<trans><name_type>&fem;</name_type><trans_det>Riria</trans_det></trans></entry>
<entry><ent_seq>2</ent_seq><r_ele><reb>リリア</reb></r_ele><trans><name_type>&fem;</name_type></trans></entry>
<entry><ent_seq>3</ent_seq><k_ele><keb>東京</keb></k_ele><r_ele><reb>とうきょう</reb></r_ele>
<trans><name_type>&place;</name_type></trans></entry>
</JMnedict>"""


def fake_web(log):
    def opener(req, timeout=None):
        url, body = req.full_url, req.data
        log.append((url, body))
        if url.endswith("JMnedict.xml.gz"):
            return io.BytesIO(gzip.compress(JMNEDICT.encode("utf-8")))
        if url.endswith(".csv"):
            return io.BytesIO("りりあ,riria,莉々亜,里梨亜\nとう,tou,東\n".encode("utf-8"))
        if url.endswith("/vn"):
            return io.BytesIO(json.dumps({"results": [{"id": "v1", "title": "Lilia", "alttitle": "リリア物語"}]}).encode())
        if url.endswith("/character"):
            return io.BytesIO(json.dumps({"results": [{"name": "Lilia", "original": "リリア", "aliases": ["リリー"]},
                                                      {"name": "Kai", "original": "カイ", "aliases": []}],
                                          "more": False}).encode())
        if "wikidata" in url:
            query = parse_qs(body.decode("ascii"))["query"][0]
            assert '"リリア"@ja' in query
            rows = [{"label": {"value": "リリア"}, "zh": {"value": "莉莉亞", "xml:lang": "zh-tw"}},
                    {"label": {"value": "リリア"}, "zh": {"value": "莉莉娅", "xml:lang": "zh-hans"},
                     "desc": {"value": "架空の人物"}}]
            return io.BytesIO(json.dumps({"results": {"bindings": rows}}).encode("utf-8"))
        raise AssertionError(url)
    return opener


class Dictionaries(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"YAKUPATCH_HOME": tempfile.mkdtemp(prefix="yakupatch-test-")})
        env.start()
        self.addCleanup(env.stop)
        self.log = []
        web = patch.object(dicts, "urlopen", fake_web(self.log))
        web.start()
        self.addCleanup(web.stop)

    def test_downloads_convert_and_lookup_order(self):
        self.assertEqual(dicts.download("jmnedict", fake_web(self.log)), 3)
        self.assertEqual(dicts.download("names", fake_web(self.log)), 9)    # 3 files × 3 spellings
        info = {d["name"]: d for d in dicts.info()}
        self.assertEqual((info["jmnedict"]["entries"], info["jmnedict"]["version"]), (3, "2026-10-01"))
        self.assertEqual(dicts.lookup("東京")[0]["type"], "place name")
        self.assertEqual(dicts.suggest("莉々亜"), ("", "りりあ（female given name or forename）；りりあ（男性名）；りりあ（女性名）", "seed"))
        self.assertIsNone(dicts.suggest("無い"))
        dicts.remove("names")
        self.assertFalse(info["names"]["installed"] and dicts._path("names").exists())

    def test_user_vndb_wikidata_and_priority(self):
        tsv = Path(tempfile.mkdtemp()) / "mine.tsv"
        tsv.write_text("原文\t譯文\t備註\nリリア\t莉莉雅\t主角\n", "utf-8")
        self.assertEqual(dicts.import_user(tsv), 1)
        js = tsv.with_suffix(".json")
        js.write_text(json.dumps({"カイ": "凱"}, ensure_ascii=False), "utf-8")
        self.assertEqual(dicts.import_user(js), 1)
        self.assertEqual(len(dicts.fetch_vndb("リリア物語")), 3)
        self.assertEqual(dicts.fetch_wikidata(["リリア"]), 2)
        hits = dicts.lookup("リリア")
        self.assertEqual([h["source"] for h in hits], ["user", "wikidata", "wikidata", "vndb"])
        self.assertEqual(hits[1]["dst"], "莉莉亞")                         # Traditional label first
        self.assertEqual(dicts.suggest("リリア"), ("莉莉雅", "Lilia（角色（リリア物語））", "dict:user"))
        sent = [b for u, b in self.log if u.endswith("/vn")]
        self.assertEqual(json.loads(sent[0])["filters"], ["search", "=", "リリア物語"])    # only the title leaves

    def test_extract_seeds_suggestions_that_are_not_enforced_until_confirmed(self):
        game, home = copy_game("mz_min")
        plugin = engines.PLUGINS[0]
        store = Store(project_dir(game, plugin, home))
        extract(game, plugin, store)
        names = [r[0] for r in core.glossary(store)]
        user = Path(tempfile.mkdtemp()) / "u.csv"
        user.write_text(f"{names[0]},字典譯名\n", "utf-8")
        dicts.import_user(user)
        self.assertEqual(core.dict_fill(store), 1)
        row = next(r for r in core.glossary(store) if r[0] == names[0])
        self.assertEqual(row[1:4:2], ["字典譯名", "dict:user"])
        self.assertNotIn(names[0], pipeline.glossary(store))                # suggestion only
        core.save_glossary(store, [[*r[:3], "manual"] if r[0] == names[0] else r for r in core.glossary(store)])
        self.assertEqual(pipeline.glossary(store)[names[0]][0], "字典譯名")

        self.assertGreaterEqual(core.dict_vndb(game, plugin, store), 1)     # VNDB names become candidates
        self.assertIn("カイ", [r[0] for r in core.glossary(store)])


if __name__ == "__main__":
    unittest.main()
