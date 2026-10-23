import os
import pickle
import struct
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path

from yakupatch import core, engines
from yakupatch.engines.renpy import LANG_FILE, OUT, RenPy, encode_say_string
from yakupatch.exchange import Store
from yakupatch.extract import project_dir
from helpers import snapshot


def _fake_renpy():
    """Classes that pickle as renpy.ast.* like the real engine's AST."""
    mod = types.ModuleType("renpy.ast")

    class Node:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class PyCode:
        def __init__(self, source):
            self.source = source

        def __getstate__(self):
            return (1, self.source, ("game/script.rpy", 1), "exec")

    for cls in (Node, PyCode):
        cls.__module__ = "renpy.ast"
    names = ("Label", "Translate", "TranslateSay", "Say", "UserStatement", "Menu", "Define", "Init")
    classes = {n: type(n, (Node,), {"__module__": "renpy.ast", "__qualname__": n}) for n in names}
    classes["PyCode"] = PyCode
    PyCode.__qualname__ = "PyCode"
    for n, cls in classes.items():
        setattr(mod, n, cls)
    return types.SimpleNamespace(**classes), mod


def rpyc(stmts) -> bytes:
    data = zlib.compress(pickle.dumps(({"version": 5003000}, stmts), protocol=2))
    head = b"RENPY RPC2"
    start = len(head) + 12 * 3
    return head + struct.pack("<III", 1, start, len(data)) + struct.pack("<III", 2, start, len(data)) + \
        struct.pack("<III", 0, 0, 0) + data


def rpa(files: dict[str, bytes], key: int = 0x42424242) -> bytes:
    body, index = b"", {}
    header_len = 34
    for name, data in files.items():
        index[name] = [(header_len + len(body) ^ key, len(data) ^ key, b"")]
        body += data
    offset = header_len + len(body)
    return f"RPA-3.0 {offset:016x} {key:08x}\n".encode() + body + zlib.compress(pickle.dumps(index, protocol=2))


class RenPyScripts(unittest.TestCase):
    def setUp(self):
        self.a, mod = _fake_renpy()
        renpy_pkg = types.ModuleType("renpy")
        renpy_pkg.ast = mod
        self.mods = {"renpy": renpy_pkg, "renpy.ast": mod}
        old = {k: sys.modules.get(k) for k in self.mods}
        sys.modules.update(self.mods)
        self.addCleanup(lambda: [sys.modules.pop(k) if v is None else sys.modules.__setitem__(k, v)
                                 for k, v in old.items()])

    def say(self, who, what, **kw):
        return self.a.Say(who=who, what=what, with_=kw.get("with_"), interact=kw.get("interact", True),
                          attributes=kw.get("attributes"), temporary_attributes=None, arguments=None)

    def script(self):
        a = self.a
        return [
            a.Define(store="store", varname="e", code=a.PyCode('Character("エリ", color="#fff")')),
            a.Label(name="start", block=[
                a.Translate(identifier="start_1a2b3c4d", language=None, block=[
                    a.UserStatement(line='voice "v/001.ogg"'),
                    self.say("e", '「おはよう」\n  "朝"です', attributes=("happy",), with_="dissolve")]),
                a.Menu(items=[("行く", "True", [a.TranslateSay(identifier="start_5e6f7a8b", language=None,
                                                               who=None, what="出発！", with_=None, interact=True,
                                                               attributes=None, arguments=None)]),
                              ("やめる", "True", [])]),
                a.Translate(identifier="start_9c0d1e2f", language="chinese",       # an existing translation
                            block=[self.say("e", "早安")]),
            ]),
        ]

    def test_entries_and_outputs(self):
        p = RenPy()
        text, info = p.decode("game/script.rpyc", rpyc(self.script()))
        got = [(s.loc, s.kind, s.text) for s in p.parse("game/script.rpyc", text)]
        self.assertEqual(sorted(got), sorted([
            ("start_1a2b3c4d", "dialogue", '「おはよう」\n  "朝"です'), ("start_5e6f7a8b", "dialogue", "出発！"),
            (got[[g[2] for g in got].index("エリ")][0], "name", "エリ"),
            (got[[g[2] for g in got].index("行く")][0], "choice", "行く"),
            (got[[g[2] for g in got].index("やめる")][0], "choice", "やめる")]))
        locs = {s.text: s.loc for s in p.parse("game/script.rpyc", text)}
        out = p.outputs({"game/script.rpyc": text}, {"game/script.rpyc": {
            "start_1a2b3c4d": '「早安」\n  "晨"', locs["エリ"]: "艾莉", locs["行く"]: "走"}})
        script = out[OUT + "script.rpy"][0].decode("utf-8")
        self.assertIn('translate yakupatch start_1a2b3c4d:\n    voice "v/001.ogg"\n'
                      '    e happy "「早安」\\n \\ \\"晨\\"" with dissolve\n', script)
        self.assertNotIn("start_5e6f7a8b", script)                                # untranslated: Ren'Py keeps it
        strings = out[OUT + "strings.rpy"][0].decode("utf-8")
        self.assertIn('    old "エリ"\n    new "艾莉"\n', strings)
        self.assertIn('config.language = "yakupatch"', out[LANG_FILE][0].decode("utf-8"))
        self.assertEqual(out[OUT + "script.rpy"][1], 1)
        self.assertEqual(encode_say_string('a\\b  c'), '"a\\\\b \\ c"')

    def test_refuses_code_in_pickles(self):
        class Evil:
            def __reduce__(self):
                return (os.system, ("echo pwned",))

        with self.assertRaises(ValueError):
            RenPy().decode("game/x.rpyc", rpyc([Evil()]))

    def test_apply_reapply_and_restore(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        game, home = tmp / "games" / "rp", tmp / "home"
        (game / "renpy").mkdir(parents=True)
        (game / "game").mkdir()
        options = rpyc([self.a.Define(store="store.config", varname="name", code=self.a.PyCode('_("テスト物語")'))])
        packed = rpa({"script.rpyc": rpyc(self.script()), "options.rpyc": options, "tl/chinese/script.rpyc": options})
        (game / "game" / "scripts.rpa").write_bytes(packed)
        plugin = engines.detect(game)[0]
        self.assertIsInstance(plugin, RenPy)
        self.assertEqual([p.path for p in plugin.language_packs(game)], ["game/tl/chinese"])
        self.assertEqual(plugin.title(game, lambda r: core.vfs.read_original(game, r)), "テスト物語")
        self.assertEqual(plugin.locate(game), ["game/scripts.rpa!options.rpyc", "game/scripts.rpa!script.rpyc"])
        store = Store(project_dir(game, plugin, home))
        core.extract(game, plugin, store, force=True)
        before = snapshot(game)
        store.update_many("t", [(r["id"], {"dst": "譯" + r["src"], "status": "translated"})
                                for r in store.records.values() if r["status"] == "pending"])
        plan = core.preview(game, store, plugin)
        self.assertEqual(sorted(f.rel for f in plan.files), sorted([LANG_FILE, OUT + "script.rpy", OUT + "strings.rpy"]))
        core.apply(game, plan)
        self.assertEqual((game / "game" / "scripts.rpa").read_bytes(), before["game\\scripts.rpa"
                         if "game\\scripts.rpa" in before else "game/scripts.rpa"])           # original untouched
        (game / OUT / "strings.rpyc").write_bytes(b"compiled by the game")              # as Ren'Py would
        store.update_many("u", [(r["id"], {"dst": "", "status": "pending"})
                                for r in store.records.values() if r["kind"] != "dialogue"])
        core.apply(game, core.preview(game, store, plugin))
        self.assertFalse((game / OUT / "strings.rpy").exists())
        self.assertFalse((game / OUT / "strings.rpyc").exists())
        self.assertTrue((game / OUT / "script.rpy").exists())
        (game / LANG_FILE).with_suffix(".rpyc").write_bytes(b"compiled")
        core.restore(game)
        self.assertEqual({k: v for k, v in snapshot(game).items() if not k.startswith("_yakupatch")}, before)
        self.assertFalse((game / "game" / "tl").exists())


if __name__ == "__main__":
    unittest.main()
