import struct
import tempfile
import unittest
from pathlib import Path

from yakupatch import core, engines
from yakupatch.engines.godot import Godot, _gdc, _Pck
from yakupatch.exchange import Store
from yakupatch.extract import project_dir

from helpers import snapshot

TSCN = """[gd_scene load_steps=2 format=2]

[node name="Title" type="Control"]

[node name="手をはなす" type="Button" parent="."]
hint_tooltip = "手をはなす"
text = "「はい」\\"と\\"
二行目"

[node name="Label" type="Label" parent="."]
text = "Start"
"""


def gdc(idents: list[str], consts: list) -> bytes:
    out = b"GDSC" + struct.pack("<5I", 13, len(idents), len(consts), 1, 1)
    for s in idents:
        b = s.encode() + b"\0"
        b += b"\0" * (-len(b) % 4)
        out += struct.pack("<I", len(b)) + bytes(x ^ 0xB6 for x in b)
    for c in consts:
        if isinstance(c, str):
            b = c.encode()
            out += struct.pack("<II", 4, len(b)) + b + b"\0" * (-len(b) % 4)
        else:
            out += struct.pack("<Ii", 2, c)
    return out + struct.pack("<II", 0, 1) + b"\x01\x02\x03\x04"           # one line entry, one token


def pck(files: dict[str, bytes], fmt: int = 1) -> bytes:
    head = b"GDPC" + struct.pack("<4I", fmt, 3, 3, 0) + (struct.pack("<Iq", 0, 0) if fmt == 2 else b"") + b"\0" * 64
    names = [("res://" + n).encode() + b"\0" * 4 for n in files]
    names = [n + b"\0" * (-len(n) % 4) for n in names]
    size = len(head) + 4 + sum(4 + len(n) + 32 + (4 if fmt == 2 else 0) for n in names)
    directory, body = struct.pack("<I", len(files)), b""
    for n, data in zip(names, files.values()):
        directory += struct.pack("<I", len(n)) + n + struct.pack("<qq", size + len(body), len(data)) + b"\0" * 16
        directory += struct.pack("<I", 0) if fmt == 2 else b""
        body += data
    return head + directory + body


SCRIPT = gdc(["_ready", "hint_tooltip"], ["手をはなす", "キスをやめる", 3, "res://画像/絵.png"])
PROJECT = b"ECFG" + struct.pack("<I", 1) + struct.pack("<I", 23) + b"application/config/name" + \
    struct.pack("<III", 20, 4, 9) + "テスト".encode() + b"\0" * 3


class GodotFormat(unittest.TestCase):
    def test_pck_read_and_patch_both_formats(self):
        for fmt in (1, 2):
            p = _Pck(pck({"a.tscn": b"abc", "b.gdc": b"xyz"}, fmt))
            self.assertEqual((p.read("a.tscn"), p.read("b.gdc")), (b"abc", b"xyz"))
            q = _Pck(p.patch({"a.tscn": b"longer text"}))
            self.assertEqual((q.read("a.tscn"), q.read("b.gdc")), (b"longer text", b"xyz"))

    def test_gdc_constants(self):
        idents, consts = _gdc(SCRIPT)
        self.assertEqual(idents, ["_ready", "hint_tooltip"])
        self.assertEqual([c[2] for c in consts], ["手をはなす", "キスをやめる", None, "res://画像/絵.png"])


class GodotPatch(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="yakupatch-test-"))
        self.game, self.home = tmp / "games" / "gd", tmp / "home"
        self.game.mkdir(parents=True)
        (self.game / "game.pck").write_bytes(pck({
            "scene/Title.tscn": TSCN.encode(), "scene/a.gdc": SCRIPT, "project.binary": PROJECT,
            "Font/mplus.ttf": b"FONT"}, 2))
        (self.game / "game.exe").write_bytes(b"MZ")
        self.plugin = engines.detect(self.game)[0]
        self.store = Store(project_dir(self.game, self.plugin, self.home))
        core.extract(self.game, self.plugin, self.store, force=True)

    def test_extract_apply_restore(self):
        self.assertIsInstance(self.plugin, Godot)
        self.assertEqual(self.plugin.title(self.game, lambda r: (self.game / r).read_bytes()), "テスト")
        # The node name 手をはなす may be looked up by a script, so the script constant stays; the tooltip is a property.
        got = sorted((r["src"]) for r in self.store.records.values())
        self.assertEqual(got, sorted(["手をはなす", "「はい」\"と\"\n二行目", "キスをやめる"]))
        before = snapshot(self.game)
        self.store.update_many("t", [(r["id"], {"dst": "譯" + r["src"], "status": "translated"})
                                     for r in self.store.records.values()])
        core.apply(self.game, core.preview(self.game, self.store, self.plugin))
        p = _Pck((self.game / "game.pck").read_bytes())
        scene = p.read("scene/Title.tscn").decode()
        self.assertIn('hint_tooltip = "譯手をはなす"', scene)
        self.assertIn('text = "譯「はい」\\"と\\"\n二行目"', scene)
        self.assertIn('[node name="手をはなす"', scene)
        self.assertEqual([c[2] for c in _gdc(p.read("scene/a.gdc"))[1]], ["手をはなす", "譯キスをやめる", None, "res://画像/絵.png"])
        self.assertNotEqual(p.read("Font/mplus.ttf"), b"FONT")                 # CJK font in its place
        core.restore(self.game)
        after = snapshot(self.game)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith("_yakupatch")}, before)


if __name__ == "__main__":
    unittest.main()
