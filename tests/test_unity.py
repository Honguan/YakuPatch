import unittest

import base64
import json

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
    def test_generator_failure_skips_the_object(self):
        class Gen:
            def get_nodes_as_json(self, asm, cls):
                raise AssertionError("failed to dump nodes as json")
        ctx = _Context.__new__(_Context)
        ctx.gen, ctx.nodes = Gen(), {}
        ms = SimpleNamespace(m_ClassName="Talk", m_AssemblyName="Assembly-CSharp.dll", m_Namespace="")
        ctx.script = lambda obj: ms
        with self.assertRaises(ValueError):                  # callers skip an object on ValueError
            ctx.tree(SimpleNamespace(serialized_type=None))
        ctx.gen = None                                       # the game's IL2CPP code could not be loaded
        with self.assertRaises(ValueError):
            ctx.tree(SimpleNamespace(serialized_type=None))


if __name__ == "__main__":
    unittest.main()
