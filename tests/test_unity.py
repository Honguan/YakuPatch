import unittest

import base64
import json
import struct

from pathlib import Path
from types import SimpleNamespace

from yakupatch.clr import Assembly
from yakupatch.engines.unity import _Context, _catalog, _dll_entries, _entries, _set, _table_entries

LINES = (Path(__file__).parent / "fixtures" / "clr" / "Lines.dll").read_bytes()    # source: Lines.cs


def sheet(*rows):
    return {"rows": [{"strings": list(r)} for r in rows], "headerRow": 0}


class UtageRules(unittest.TestCase):
    def test_book_takes_text_column_only(self):
        book = {"importGridList": [sheet(
            ["", "Command", "Arg1", "Text", "English"],
            ["", "", "ヒカリコ", "「おはよう」", "「おはよう」(e)"],
            ["", "", "", "雨が降ってきた。", ""],
            ["", "SelectionB", "*label", "服を脱がす", ""],
            ["", "Jump", "*ジャンプ先", "", ""],
            ["", "Dialog", "", "解放されました！", ""])]}
        got = [(e["loc"], e["kind"], e["text"], e["speaker"]) for e in _entries("AdvImportBook", book, 7)]
        self.assertEqual(got, [("7:/importGridList/0/rows/1/strings/3", "dialogue", "「おはよう」", "ヒカリコ"),
                               ("7:/importGridList/0/rows/2/strings/3", "dialogue", "雨が降ってきた。", None),
                               ("7:/importGridList/0/rows/3/strings/3", "choice", "服を脱がす", None),
                               ("7:/importGridList/0/rows/5/strings/3", "dialogue", "解放されました！", None)])

    def test_chapter_settings_columns(self):
        chapter = {"settingList": [sheet(["", "CharacterName", "NameText", "Pattern"], ["", "ヒカリコ", "ヒカリコ", "通常"]),
                                   sheet(["Key", "Japanese", "English"], ["name_key", "ヒカリコ", "Hikariko"]),
                                   sheet(["", "Label", "FileName"], ["", "ラベル", "ファイル"])]}
        got = [(e["loc"], e["kind"]) for e in _entries("AdvChapterData", chapter, 1)]
        self.assertEqual(got, [("1:/settingList/0/rows/1/strings/2", "name"), ("1:/settingList/1/rows/1/strings/1", "ui")])

    def test_ui_components_and_unknown_scripts(self):
        self.assertEqual(_entries("TextMeshProUGUI", {"m_text": "セーブ"}, 3),
                         [{"loc": "3:/m_text", "kind": "ui", "text": "セーブ"}])
        self.assertEqual(_entries("Text", {"m_Text": "Save"}, 3), [])
        self.assertEqual(_entries("CharacterData", {"name": "ヒカリコ"}, 3), [])     # keys may be Japanese: no rule
        data = {"m_Name": "メッセージデータ一覧", "id": "あいさつ",
                "List": [{"Messages": ["よ、よろしくお願いします…", "うう～"]}, {"Messages": ["こんにちは"]}]}
        self.assertEqual([(e["loc"], e["text"]) for e in _entries("MessageData", data, 4)],
                         [("4:/List/0/Messages/0", "よ、よろしくお願いします…"), ("4:/List/0/Messages/1", "うう～"),
                          ("4:/List/1/Messages/0", "こんにちは")])
        self.assertEqual(_entries("CubismDisplayInfoParameterName", {"Name": "左胸の揺れ、Y方向のパラメータ"}, 4), [])

    def test_localization_table_and_set(self):
        text = "﻿Key\tJapanese\tEnglish\r\nYes\tはい\tYes\r\nOk\tOK\tOK\r\n"
        self.assertEqual(_table_entries(text, 9), [{"loc": "9:L1:1", "kind": "ui", "text": "はい"}])
        self.assertEqual(_table_entries("a\tb\nc\tテスト\n", 9), [])
        tree = {"importGridList": [{"rows": [{"strings": ["", "a"]}]}]}
        _set(tree, "/importGridList/0/rows/0/strings/1", "譯")
        self.assertEqual(tree["importGridList"][0]["rows"][0]["strings"], ["", "譯"])

    def test_naninovel_script(self):
        def ref(cls, **fields):
            return {"type": {"class": cls}, "data": {k: {"value": v, "hasValue": 1} for k, v in fields.items()}}
        script = {"references": {"RefIds": [ref("PrintText", Text="「おはよう」", AuthorId="リラ"),
                                            ref("PrintText", Text="雨が降ってきた。", AuthorId="Lila"),
                                            ref("AddChoice", ChoiceSummary="中に出す"),
                                            ref("PlayBgm", Text="なし")]}}
        got = [(e["loc"], e["kind"], e["text"], e.get("speaker")) for e in _entries("Script", script, 5)]
        self.assertEqual(got, [("5:/references/RefIds/0/data/Text/value", "dialogue", "「おはよう」", "リラ"),
                               ("5:/references/RefIds/0/data/AuthorId/value", "name", "リラ", None),
                               ("5:/references/RefIds/1/data/Text/value", "dialogue", "雨が降ってきた。", "Lila"),
                               ("5:/references/RefIds/2/data/ChoiceSummary/value", "choice", "中に出す", None)])

    def test_catalog_crc_zeroed_in_place(self):
        extra = b"\x07\x00" + '{"m_Hash":"ab","m_Crc":3423817957,"m_Timeout":0}'.encode("utf-16-le")
        raw = json.dumps({"m_KeyDataString": "x", "m_ExtraDataString": base64.b64encode(extra).decode()}).encode()
        out = base64.b64decode(json.loads(_catalog(raw))["m_ExtraDataString"])
        self.assertEqual(len(out), len(extra))
        self.assertEqual(json.loads(out[2:].decode("utf-16-le")), {"m_Hash": "ab", "m_Crc": 0, "m_Timeout": 0})


class MonoStrings(unittest.TestCase):
    def test_reads_ldstr_strings(self):
        asm = Assembly(LINES)
        self.assertEqual(sorted({asm.strings[off] for _, off in asm.ldstr}),
                         sorted(["よ、よろしくお願いします…", "初めてなのに、こんなの嫌ぁ…っ", "ここが検査室…",
                                 "Resources/立ち絵/girl", "ok", "", "教頭に怒られてしまった…", "おまんこ"]))

    def test_sentences_only(self):
        self.assertEqual(sorted(e["text"] for e in _dll_entries(LINES)),
                         sorted(["よ、よろしくお願いします…", "初めてなのに、こんなの嫌ぁ…っ", "ここが検査室…",
                                 "教頭に怒られてしまった…"]))
        self.assertEqual(_dll_entries(b"MZ not a dll"), [])

    def test_rewrite_moves_every_ldstr(self):
        asm = Assembly(LINES)
        first = {s: off for off, s in asm.strings.items()}
        longer = "请多指教……这是比原文更长的一句译文，用来测试字串区变长的情况"
        out = Assembly(asm.save({first["よ、よろしくお願いします…"]: longer, first["おまんこ"]: "短"}))
        self.assertEqual([out.strings[off] for _, off in out.ldstr],
                         [longer if s == "よ、よろしくお願いします…" else "短" if s == "おまんこ" else s
                          for s in (asm.strings[off] for _, off in asm.ldstr)])
        self.assertEqual(Assembly(out.save({})).strings, out.strings)       # an already patched file reads back


class TypeTree(unittest.TestCase):
    def test_scan_fingerprint_detects_content_and_backup_changes(self):
        import tempfile
        import os
        from unittest.mock import patch
        from yakupatch.backup import Backup
        from yakupatch.engines.unity import Unity
        with tempfile.TemporaryDirectory() as tmp, patch("yakupatch.engines.unity._contexts", {}), \
                patch("yakupatch.engines.unity._scan_fingerprints", {}):
            game = Path(tmp)
            data = game / "Game_Data"
            data.mkdir()
            (data / "globalgamemanagers").write_bytes(b"assets")
            dll = game / "GameAssembly.dll"
            dll.write_bytes(b"one")
            plugin = Unity()
            first = plugin.scan_fingerprint(game)
            self.assertEqual(plugin.scan_fingerprint(game), first)
            stat = dll.stat()
            dll.write_bytes(b"two")
            os.utime(dll, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed = plugin.scan_fingerprint(game)
            self.assertNotEqual(changed, first)           # same size and timestamp still invalidates
            from yakupatch.engines.unity import _contexts
            _contexts[str(game.resolve())] = object()
            self.assertEqual(plugin.scan_fingerprint(game), changed)
            self.assertIn(str(game.resolve()), _contexts)
            extra = data / "new.assets"
            extra.write_bytes(b"new")
            self.assertNotEqual(plugin.scan_fingerprint(game), changed)
            self.assertNotIn(str(game.resolve()), _contexts)
            extra.unlink()
            self.assertEqual(plugin.scan_fingerprint(game), changed)
            backup = Backup(game)
            rel = "Game_Data/globalgamemanagers"
            backup.ensure(rel, b"assets")
            (game / rel).write_bytes(b"patched")
            backup.set_patched(rel, b"patched")
            backup.save()
            backed = plugin.scan_fingerprint(game)
            (backup.dir / "files" / rel).write_bytes(b"edited original")
            self.assertNotEqual(plugin.scan_fingerprint(game), backed)

    def test_stripped_reference_types_preserve_metadata_and_reject_bad_payloads(self):
        from unittest.mock import Mock
        from UnityPy.files.SerializedFile import SerializedType
        from UnityPy.streams import EndianBinaryWriter
        from yakupatch.engines.unity import _type_nodes
        def node(level, typ, name):
            return dict(m_Level=level, m_Type=typ, m_Name=name, m_MetaFlag=0)
        schema = _type_nodes([node(0, "MonoBehaviour", "Base"), node(1, "int", "prefix"),
                              node(1, "ManagedReferencesRegistry", "references")])
        generated = [node(0, "Move", "Base")] + [node(1, "int", name) for name in
                     ("m_GameObject", "m_Enabled", "m_Script", "m_Name", "value")]
        writer = EndianBinaryWriter(endian="<")
        for v in (7, 2, 2):
            writer.write_int(v)
        for rid, value in ((123, 42), (124, -3)):
            writer.write_long(rid)
            for name in ("Move", "Animation", "Game"):
                writer.write_aligned_string(name)
            writer.write_int(value)
        raw = writer.bytes
        def fixture(payload):
            ref = object.__new__(SerializedType)
            ref.__attrs_init__(114, is_stripped_type=False, script_type_index=0,
                               script_id=b"s" * 16, old_type_hash=b"h" * 16)
            sf = SimpleNamespace(_enable_type_tree=False, header=SimpleNamespace(version=22), ref_types=[ref])
            obj = SimpleNamespace(assets_file=sf, reader=SimpleNamespace(endian="<"), get_raw_data=lambda: payload)
            ctx = _Context.__new__(_Context)
            ctx.reference_nodes = {}
            gen = SimpleNamespace(get_nodes_as_json=Mock(return_value=json.dumps(generated)))
            ctx._fallback_generator = Mock(return_value=gen)
            return ctx, obj, ref, gen
        ctx, obj, ref, gen = fixture(raw)
        before = EndianBinaryWriter(endian="<")
        ref.write(obj.assets_file, before, True)
        ctx._restore_references(obj, schema)
        self.assertEqual((ref.m_ClassName, ref.m_NameSpace, ref.m_AssemblyName), ("Move", "Animation", "Game"))
        self.assertEqual([n.m_Name for n in ref.node.m_Children], ["value"])
        ctx._restore_references(obj, schema)
        gen.get_nodes_as_json.assert_called_once_with("Game", "Animation.Move")
        after = EndianBinaryWriter(endian="<")
        ref.write(obj.assets_file, after, True)
        self.assertEqual(before.bytes, after.bytes)
        # A null registry entry has no payload and consumes no stripped type slot.
        for rid in (-1, -2, 123):
            payload = raw[:8] + struct.pack("<i", 3) + struct.pack("<qiii", rid, 0, 0, 0) + raw[12:]
            ctx, obj, ref, gen = fixture(payload)
            if rid in (-1, -2):
                ctx._restore_references(obj, schema)
                self.assertEqual(ref.m_ClassName, "Move")
            else:
                with self.assertRaisesRegex(ValueError, "缺少 managed reference"):
                    ctx._restore_references(obj, schema)
                self.assertIsNone(ref.node)
        ctx, obj, ref, gen = fixture(raw)
        ctx.patched_generator, ctx.gen = True, gen
        ctx._restore_references(obj, schema)
        ctx._fallback_generator.assert_not_called()
        self.assertEqual(ref.m_ClassName, "Move")
        for bad in (raw[:-1], raw + b"extra", raw[:4] + struct.pack("<i", 1) + raw[8:],
                    raw[:8] + struct.pack("<i", -1) + raw[12:]):
            with self.subTest(payload=bad):
                ctx, obj, ref, _ = fixture(bad)
                with self.assertRaises((ValueError, EOFError, struct.error)):
                    ctx._restore_references(obj, schema)
                self.assertIsNone(ref.node)
                self.assertIsNone(ref.m_ClassName)
        ctx, obj, ref, gen = fixture(raw)
        gen.get_nodes_as_json.side_effect = AssertionError("missing type")
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "無法重建"):
                ctx._restore_references(obj, schema)
        gen.get_nodes_as_json.assert_called_once()
        self.assertIsNone(ref.node)

    def test_metadata_recovers_xor_only_after_validation(self):
        from yakupatch.engines.unity import _metadata
        raw = bytearray(128)
        struct.pack_into("<8I", raw, 0, 0xFAB11BAF, 29, 32, 8, 40, 56, 96, 8)
        raw[96:104] = b"A.dll\0x\0"
        raw[102] = 0x80                           # obfuscated identifiers may contain non-UTF-8 bytes
        plain = bytes(raw)
        self.assertIs(_metadata(plain), plain)
        for key in (b"x", b"wanzg", b"12345678"):
            encoded = bytes(v ^ key[i % len(key)] for i, v in enumerate(plain))
            self.assertEqual(_metadata(encoded), plain)
        for invalid in (b"", b"bad header", bytes(128), encoded[:100]):
            with self.assertRaises(ValueError):
                _metadata(invalid)

    def test_empty_reference_roundtrip_and_nonempty_rejection(self):
        from unittest.mock import Mock, patch
        from UnityPy.helpers.TypeTreeHelper import read_typetree, write_typetree
        from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter
        from yakupatch.engines.unity import _type_nodes
        nodes = []
        def node(level, typ, name, flag=0):
            nodes.append(dict(m_Level=level, m_Type=typ, m_Name=name, m_MetaFlag=flag))
        node(0, "MonoBehaviour", "Base")
        node(1, "ManagedReferencesRegistry", "references")
        node(2, "int", "version")
        node(2, "vector", "RefIds")
        node(3, "Array", "Array")
        node(4, "int", "size")
        node(4, "ReferencedObject", "data")
        node(5, "SInt64", "rid")
        node(5, "ReferencedManagedType", "type")
        for field in ("class", "ns", "asm"):
            node(6, "string", field, 16384)
            node(7, "Array", "Array")
            node(8, "int", "size")
            node(8, "char", "data")
        node(5, "ReferencedObjectData", "data")
        tree = {"references": {"version": 2, "RefIds": [
            {"rid": -2, "type": {"class": "", "ns": "", "asm": ""}, "data": {}}]}}
        schema = _type_nodes([dict(n) for n in nodes], True)
        writer = EndianBinaryWriter(endian="<")
        write_typetree(tree, schema, writer)
        raw = writer.bytes
        self.assertEqual(read_typetree(schema, EndianBinaryReader(raw, endian="<"), byte_size=len(raw)), tree)
        ctx = _Context.__new__(_Context)
        ctx.version, ctx.fallback_nodes = "2021.3.21f1", {}
        ctx._load_generator = Mock()
        obj = SimpleNamespace(assets_file=SimpleNamespace(ref_types=[]), read_typetree=lambda n: tree)
        with patch("yakupatch.engines.unity._fallback", (None, None)), \
                patch("UnityPy.helpers.TypeTreeGenerator.TypeTreeGenerator") as factory:
            factory.return_value.get_nodes_as_json.return_value = json.dumps(nodes)
            for _ in range(2):
                self.assertEqual(ctx._fallback_tree(obj, ("A", "", "C"), "A.dll", "C")[0], tree)
            factory.return_value.get_nodes_as_json.assert_called_once_with("A", "C")
            ctx._load_generator.assert_called_once()
            tree["references"]["RefIds"][0]["type"]["class"] = "Unknown"
            with self.assertRaisesRegex(ValueError, "managed reference"):
                ctx._fallback_tree(obj, ("A", "", "C"), "A.dll", "C")
        # Mixed null/non-null registries must survive UnityPy's read/write asymmetry.
        null = tree["references"]["RefIds"][0]
        null["type"]["class"] = ""
        null.pop("data")
        tree["references"]["RefIds"].append(
            {"rid": 7, "type": {"class": "Value", "ns": "Game", "asm": "Asm"}, "data": {"value": 42}})
        value_node = _type_nodes([dict(m_Level=0, m_Type="Value", m_Name="Base", m_MetaFlag=0),
                                  dict(m_Level=1, m_Type="int", m_Name="value", m_MetaFlag=0)])
        sf = SimpleNamespace(_enable_type_tree=True, ref_types=[SimpleNamespace(
            m_ClassName="Value", m_NameSpace="Game", m_AssemblyName="Asm", node=value_node)])
        schema = _type_nodes(nodes)
        obj.assets_file = sf
        ctx._read_tree(obj, schema)
        writer = EndianBinaryWriter(endian="<")
        write_typetree(tree, schema, writer, sf)
        expected = EndianBinaryWriter(endian="<")
        expected.write_bytes(struct.pack("<iiqiii", 2, 2, -2, 0, 0, 0))
        expected.write_long(7)
        for value in ("Value", "Game", "Asm"):
            expected.write_aligned_string(value)
        expected.write_int(42)
        self.assertEqual(writer.bytes, expected.bytes)

    def test_failed_fallback_invalidates_native_owner_and_is_not_retried(self):
        from unittest.mock import Mock, patch
        import yakupatch.engines.unity as unity
        ctx = _Context.__new__(_Context)
        ctx.version, ctx.fallback_nodes = "2021.3.21f1", {}
        ctx._load_generator = Mock(side_effect=AssertionError("bad metadata"))
        obj = SimpleNamespace(assets_file=SimpleNamespace(ref_types=[]))
        with patch.object(unity, "_fallback", (object(), object())), \
                patch("UnityPy.helpers.TypeTreeGenerator.TypeTreeGenerator"):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    ctx._fallback_tree(obj, ("A", "", "C"), "A.dll", "C")
            ctx._load_generator.assert_called_once()
            self.assertEqual(unity._fallback, (None, None))

    def test_il2cpp_uses_instance_owned_assemblies(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            env = SimpleNamespace(objects=[SimpleNamespace(assets_file=SimpleNamespace(unity_version="2021.3.21f1"))], files={})
            with patch("UnityPy.Environment", return_value=env), patch("yakupatch.engines.unity._files", return_value=[]), \
                    patch.object(_Context, "_load_generator"), \
                    patch("UnityPy.helpers.TypeTreeGenerator.TypeTreeGenerator") as generator:
                _Context(game)
                generator.assert_called_with("2021.3.21f1", generator="AssetsTools")
                (game / "GameAssembly.dll").touch()
                _Context(game)
                generator.assert_called_with("2021.3.21f1", generator="AssetStudio")

    def test_unicode_type_name_uses_utf8_and_releases_native_memory(self):
        import ctypes
        from unittest.mock import Mock, patch
        from yakupatch.engines.unity import _nodes_json
        payload = ctypes.create_string_buffer(b'[]')
        def generate(ptr, assembly, full, out):
            self.assertEqual(assembly, b"Assembly-CSharp.dll")
            self.assertEqual(full, "C_.せいせい".encode("utf-8"))
            ctypes.cast(out, ctypes.POINTER(ctypes.c_char_p))[0] = ctypes.cast(payload, ctypes.c_char_p)
            return status
        dll = SimpleNamespace(TypeTreeGenerator_generateTreeNodesJson=generate, FreeCoTaskMem=Mock())
        with patch("TypeTreeGeneratorAPI.TypeTreeGenerator.DLL", dll):
            status = 0
            self.assertEqual(_nodes_json(SimpleNamespace(ptr=1), "Assembly-CSharp.dll", "C_.せいせい"), "[]")
            status = -1
            with self.assertRaises(AssertionError):
                _nodes_json(SimpleNamespace(ptr=1), "Assembly-CSharp.dll", "C_.せいせい")
        self.assertEqual(dll.FreeCoTaskMem.call_count, 2)

    def test_generator_failure_skips_the_object(self):
        class Gen:
            calls = 0
            def get_nodes_as_json(self, asm, cls):
                self.calls += 1
                raise AssertionError("failed to dump nodes as json")
        ctx = _Context.__new__(_Context)
        ctx.gen, ctx.nodes = Gen(), {}
        ms = SimpleNamespace(m_ClassName="Talk", m_AssemblyName="Assembly-CSharp.dll", m_Namespace="")
        ctx.script = lambda obj: ms
        for _ in range(20):
            with self.assertRaises(ValueError):              # callers skip an object on ValueError
                ctx.tree(SimpleNamespace(serialized_type=None))
        self.assertEqual(ctx.gen.calls, 1)
        stored = {"m_Text": "既有型別樹"}
        obj = SimpleNamespace(serialized_type=SimpleNamespace(node=object()), read_typetree=lambda: stored)
        self.assertEqual(ctx.tree(obj), ("Talk", stored, None))
        ctx.gen = None                                       # the game's IL2CPP code could not be loaded
        with self.assertRaises(ValueError):
            ctx.tree(SimpleNamespace(serialized_type=None))

    def test_normalizes_assembly_and_keeps_other_classes_readable(self):
        from unittest.mock import Mock
        ctx = _Context.__new__(_Context)
        ctx.nodes = {}
        nodes = [{"m_Level": 0, "m_Type": "MonoBehaviour", "m_Name": "Base", "m_MetaFlag": 0}]
        ctx.gen = SimpleNamespace(get_nodes_as_json=Mock(side_effect=[AssertionError("missing"), json.dumps(nodes)]))
        ms = SimpleNamespace(m_ClassName="Missing", m_AssemblyName="Assembly-CSharp", m_Namespace="Game")
        ctx.script = lambda obj: ms
        obj = SimpleNamespace(serialized_type=None, read_typetree=lambda nodes: {"text": "hello"})
        with self.assertRaises(ValueError):
            ctx.tree(obj)
        ms.m_AssemblyName = "Assembly-CSharp.dll"
        with self.assertRaises(ValueError):
            ctx.tree(obj)
        ms.m_ClassName = "Talk"
        self.assertEqual(ctx.tree(obj)[1], {"text": "hello"})
        self.assertEqual(ctx.tree(obj)[1], {"text": "hello"})
        self.assertEqual(ctx.gen.get_nodes_as_json.call_count, 2)
        self.assertEqual(ctx.gen.get_nodes_as_json.call_args_list[0].args, ("Assembly-CSharp.dll", "Game.Missing"))


if __name__ == "__main__":
    unittest.main()
