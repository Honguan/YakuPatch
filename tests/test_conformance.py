"""Plugin conformance suite (PROJECT_PLAN §14.1). Every plugin + its fixture game must pass."""
import json
import re
import unittest

from yakupatch import engines
from yakupatch.encoding import decode, encode
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir
from yakupatch import writeback

from helpers import FIXTURES, copy_game, snapshot

CASES = [("rpgmaker-mz", "mz_min"), ("rpgmaker-mv", "mv_min")]


class Conformance(unittest.TestCase):
    def each(self):
        for name, fixture in CASES:
            plugin = next(p for p in engines.PLUGINS if p.name == name)
            yield plugin, FIXTURES / fixture

    def files(self, plugin, game):
        for rel in plugin.locate(game):
            raw = (game / rel).read_bytes()
            text, info = decode(raw, plugin.default_encoding)
            yield rel, raw, text, info

    def test_roundtrip_byte_identical(self):
        for plugin, game in self.each():
            for rel, raw, text, info in self.files(plugin, game):
                self.assertEqual(encode(plugin.render(rel, text, {}), info), raw, rel)

    def test_parse_render_inverse(self):
        for plugin, game in self.each():
            for rel, raw, text, info in self.files(plugin, game):
                segs = plugin.parse(rel, text)
                markers = {s.loc: f"@@{i}@@" for i, s in enumerate(segs)}
                back = {s.loc: s.text for s in plugin.parse(rel, plugin.render(rel, text, markers))}
                self.assertEqual(back, markers, rel)

    def test_locations_stable(self):
        for plugin, game in self.each():
            for rel, raw, text, info in self.files(plugin, game):
                self.assertEqual(plugin.parse(rel, text), plugin.parse(rel, text), rel)

    def test_control_codes_cover_fixture(self):
        for plugin, game in self.each():
            codes = re.compile("(?:" + "|".join(plugin.control_codes) + ")+")
            for rel, raw, text, info in self.files(plugin, game):
                for s in plugin.parse(rel, text):
                    self.assertNotIn("\\", codes.sub("", s.text), f"{rel} {s.loc}")

    def test_detect(self):
        for plugin, game in self.each():
            self.assertGreaterEqual(plugin.detect(game), 0.9)
            for name, other in CASES:
                if name == plugin.name:
                    continue
                self.assertLessEqual(plugin.detect(FIXTURES / other), 0.3)

    def test_writes_stay_inside_game_and_project(self):
        for plugin, fixture in [(p, f.name) for p, f in self.each()]:
            game, home = copy_game(fixture)
            outside = snapshot(game.parent.parent / "games")
            store = Store(project_dir(game, plugin, home))
            extract(game, plugin, store)
            plan = writeback.plan(game, store, plugin)
            self.assertEqual(plan.files, [], "no translations means no file changes")
            after = snapshot(game.parent.parent / "games")
            self.assertEqual(after, outside)
            created = {p.parent for p in home.rglob("*") if p.is_file()}
            self.assertTrue(all(store.dir in [c, *c.parents] for c in created))


if __name__ == "__main__":
    unittest.main()
