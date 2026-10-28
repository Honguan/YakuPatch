"""Native regressions; build_typetree.ps1 builds the DLL and synthetic assembly first."""
import json
import unittest
from pathlib import Path

from yakupatch.engines.unity import _NATIVE, _nodes_json

FIXTURE = Path(__file__).resolve().parents[1] / "build/typetree-fixture/GenericFields.dll"


@unittest.skipUnless(_NATIVE.is_file() and FIXTURE.is_file(), "run scripts/build_typetree.ps1 first")
class NativeTypeTrees(unittest.TestCase):
    def test_missing_mono_types_report_not_found(self):
        import ctypes
        from TypeTreeGeneratorAPI.TypeTreeGenerator import init_dll
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        init_dll(str(_NATIVE))
        from TypeTreeGeneratorAPI.TypeTreeGenerator import DLL
        gen = TypeTreeGenerator("2022.3.0f1", generator="AssetsTools")
        for assembly in sorted(FIXTURE.parent.glob("*.dll")):
            gen.load_dll(assembly.read_bytes())
        for assembly, cls in (("BroAudioEditor.dll", "Missing"), (".dll", "Missing"),
                              ("GenericFields.dll", "Fixture.Missing"), ("GenericFields.dll", "")):
            with self.subTest(assembly=assembly, cls=cls):
                result = ctypes.c_char_p()
                status = DLL.TypeTreeGenerator_generateTreeNodesJson(
                    gen.ptr, assembly.encode(), cls.encode(), ctypes.byref(result))
                self.assertEqual(status, -2)
                self.assertIsNone(result.value)

    def test_nested_generics_and_reference_collections(self):
        from TypeTreeGeneratorAPI.TypeTreeGenerator import init_dll
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        init_dll(str(_NATIVE))
        gen = TypeTreeGenerator("2022.3.0f1", generator="AssetStudio")
        for assembly in sorted(FIXTURE.parent.glob("*.dll")):
            gen.load_dll(assembly.read_bytes())
        for cls, expected in (("Numbers", "SInt32"), ("Words", "string"), ("Numbers", "SInt32")):
            with self.subTest(cls=cls):
                nodes = json.loads(_nodes_json(gen, "GenericFields.dll", "Fixture." + cls))
                self.assertEqual([n["m_Type"] for n in nodes if n["m_Name"] == "content"], [expected])
                self.assertEqual(sum(n["m_Type"] == "managedReference" for n in nodes), 1)
                self.assertEqual(sum(n["m_Type"] == "ManagedReferencesRegistry" for n in nodes), 1)
        nodes = json.loads(_nodes_json(gen, "GenericFields.dll", "Fixture.Nested"))
        self.assertEqual(sum(n["m_Type"] == "managedReference" for n in nodes), 1)
        self.assertEqual(sum(n["m_Type"] == "ManagedReferencesRegistry" for n in nodes), 1)
        nodes = json.loads(_nodes_json(gen, "GenericFields.dll", "Fixture.ObjectReference"))
        self.assertEqual([n["m_Type"] for n in nodes if n["m_Name"] == "target"], ["PPtr<Object>"])
        nodes = json.loads(_nodes_json(gen, "GenericFields.dll", "Fixture.Rebound"))
        self.assertEqual(sum(n["m_Type"] == "SInt32" for n in nodes), 4)
        nodes = json.loads(_nodes_json(gen, "GenericFields.dll", "Fixture.DirectBox"))
        self.assertEqual(sum(n["m_Type"] == "SInt32" for n in nodes), 4)
