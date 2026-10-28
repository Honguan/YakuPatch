"""Unity (Mono / IL2CPP): text inside *_Data serialized files, read and written through UnityPy (§10).
A logical file is one serialized file; its decoded text is a JSON list of the strings our rules pick:
UI components, Utage books and settings, Utage localization tables, Naninovel script bundles, sentences in other
MonoBehaviours and in the Mono game assemblies (Assembly-CSharp*.dll, see clr.py). Files are rewritten
whole (outputs()), the originals stay in the backup; the Addressables catalog loses the CRCs of the bundles. Text is shown in one Windows
CJK font: the default font's dynamic fallback renders it at run time and the game's CJK fonts give up their letters,
kana and ideographs (symbols stay). Fonts embedded for the legacy UI Text get the Windows font's data the same way.
Games without such a fallback get their other CJK fonts in the same file as fallbacks instead."""
import json
import os
import re
import struct
import hashlib
from pathlib import Path
from threading import Lock

from .. import clr
from ..backup import Backup
from .base import EnginePlugin, Segment

_JA = re.compile(r"[぀-ヿ]")
_CJK = re.compile(r"[一-鿿]")
_RUBY = re.compile(r'<ruby="[^"<>]*[぀-ヿ][^"<>]*">(.*?)</ruby>', re.S)
_UI = {"TextMeshProUGUI": "m_text", "TextMeshPro": "m_text", "Text": "m_Text"}
# Utage sheet columns shown to the player; others are labels, file names or the unused English copy.
_COLUMNS = {"Text": None, "NameText": "name", "Japanese": "ui", "Title": "ui"}
_SKIP_SUFFIX = (".ress", ".resource", ".dll", ".json", ".txt", ".config", ".xml", ".info", ".dat", ".html")
_contexts: dict[str, "_Context"] = {}
_scan_fingerprints: dict[str, str] = {}
_generator_lock = Lock()
_fallback = (None, None)
_NATIVE = Path(__file__).resolve().parents[1] / "native" / "TypeTreeGeneratorAPI.dll"
_null_reference_reader_installed = False
_NANI = "StreamingAssets/aa/*/naninovel_assets_naninovel/scripts/*.bundle"
_CATALOG = "StreamingAssets/aa/catalog.json"
# Naninovel commands: field -> kind. PrintText.AuthorId is a free-form name unless it is a configured character ID.
_SERIF = re.compile(r"mincho|serif|ming|song|antique|kaisei|明朝", re.I)
_BOLD = re.compile(r"bold|black|heavy|[-_ ]b$|w[7-9]", re.I)
# Other classes: fields (list items count as one field) holding a string that reads as a sentence; names and keys
# are short and unpunctuated.
_SENTENCE = re.compile(r"[、。，！？!?…「」『』（）]")
_NOT_TEXT = re.compile(r"^(Cubism|TMP_)")                 # Live2D parameter / part names, font assets
_PATH = re.compile(r"[/\\]|\.\w{2,4}$")                 # resource paths and file names in code
# Characters handed to the one CJK font; symbols stay in the game's fonts, which often draw their own (┻ as a heart).
_UNIFIED = re.compile(r"[\u0020-\u007e\u3000-\u30ff\u4e00-\u9fff\uff00-\uffef]")
_NANI_TEXT = {"PrintText": {"Text": "dialogue", "AuthorId": "name"}, "AddChoice": {"ChoiceSummary": "choice"}}


def _data_dir(game_dir: Path) -> Path | None:
    return next((p for p in game_dir.glob("*_Data") if p.is_dir() and (p / "globalgamemanagers").is_file()), None)


def _nodes_json(gen, assembly: str, full: str) -> str:
    if assembly.isascii() and full.isascii():
        return gen.get_nodes_as_json(assembly, full)
    # TypeTreeGeneratorAPI 0.0.10's Python wrapper uses ASCII; its native API requires UTF-8.
    import ctypes
    from TypeTreeGeneratorAPI.TypeTreeGenerator import DLL
    result = ctypes.c_char_p()
    try:
        status = DLL.TypeTreeGenerator_generateTreeNodesJson(
            gen.ptr, assembly.encode("utf-8"), full.encode("utf-8"), ctypes.byref(result))
        if status != 0 or result.value is None:
            raise AssertionError("failed to dump nodes as json")
        return result.value.decode("utf-8")
    finally:
        if result.value is not None:
            DLL.FreeCoTaskMem(result)


def _metadata(raw: bytes) -> bytes:
    """Recover known metadata encodings only after validating their layout."""
    magic = b"\xaf\x1b\xb1\xfa"
    if raw.startswith(magic):
        return raw
    if len(raw) >= 32:
        for version in range(24, 32):
            prefix = magic + struct.pack("<I", version)
            key_bytes = bytes(a ^ b for a, b in zip(raw, prefix))
            for size in range(1, 9):
                key = key_bytes[:size]
                if any(v != key[i % size] for i, v in enumerate(key_bytes)):
                    continue
                header = bytes(v ^ key[i % size] for i, v in enumerate(raw[:32]))
                literals, count, data, length, strings, size_strings = struct.unpack_from("<6I", header, 8)
                if not (32 <= literals <= len(raw) and literals % 4 == 0 and count % 8 == 0
                        and literals + count <= data <= data + length <= strings
                        and 0 < size_strings <= len(raw) - strings):
                    continue
                heap = bytes(v ^ key[(strings + i) % size] for i, v in enumerate(raw[strings:strings + size_strings]))
                # Obfuscated identifiers need not be valid UTF-8, but assembly names remain terminated strings.
                if not heap.endswith(b"\0") or b".dll\0" not in heap:
                    continue
                return bytes(v ^ key[i % size] for i, v in enumerate(raw))
    if len(raw) >= 392:
        return _shuffled_metadata(raw)
    raise ValueError("IL2CPP metadata 格式無法識別，或 XOR 編碼內容已損壞")


def _shuffled_metadata(raw: bytes) -> bytes:
    """HypnoApp2's v29 layout: header permutation/biases and seven byte-XOR sections."""
    h = struct.unpack("<98I", bytes(v ^ ((i + 194) & 255) for i, v in enumerate(raw[:392])))
    # (offset field, offset bias, size field, XOR direction, record size), in standard v29 order.
    layout = (
        (0x44, -60, 0x3c, 1, 8), (0x15c, 20, 0x16c, 1, 1), (0x58, -20, 0xc4, -1, 1),
        (0x164, -52, 0x50, 0, 24), (0xcc, -44, 0x114, -1, 20), (0x138, -16, 0x10c, 1, 32),
        (0x90, -40, 0x70, 0, 12), (0x14, -40, 0xe0, 0, 12), (0xa8, -56, 0xd4, 0, 1),
        (0xd8, -20, 0x30, 0, 12), (0x17c, -52, 0xc8, 0, 12), (0x8c, -64, 0xec, -1, 12),
        (0xc, 44, 0x74, 0, 16), (0x1c, -56, 0x80, 0, 4), (0x88, 52, 0xb0, 0, 16),
        (0x24, 44, 0x12c, 0, 4), (0x78, 52, 0x148, 0, 4), (0xd0, -64, 0x34, 0, 4),
        (0x6c, 44, 0x178, 0, 8), (0x94, -28, 0x98, 0, 88), (0x140, -52, 0x4c, 0, 40),
        (4, -44, 0x150, 1, 64), (0x54, -20, 0x2c, 0, 8), (8, -24, 0x130, 0, 4),
        (0x144, 36, 0x7c, 0, 1), (0x84, 48, 0x108, 0, 8), (0x128, -56, 0x5c, 0, 4),
        (0xb4, 24, 0x60, 0, 8), (0x160, -28, 0x110, 0, 8), (0x134, 48, 0x104, 0, 1),
        (0xf8, 36, 0x40, 0, 4),
    )
    sections, end = [], 392
    error = "IL2CPP 重排中繼資料的區段、索引或編碼不符"
    for offset, bias, count, sign, stride in layout:
        start, size = h[offset // 4] + bias, h[count // 4]
        if not (end <= start <= end + 7 and start <= len(raw) and size <= len(raw) - start and size % stride == 0):
            raise ValueError(error)
        sections.append((start, size))
        end = start + size
    out = bytearray(raw)
    for (start, size), (_, _, _, sign, _) in zip(sections, layout):
        if sign:
            key = sign * (size - 127)
            out[start:start + size] = bytes(v ^ ((i + key) & 255) for i, v in enumerate(raw[start:start + size]))
    start, size = sections[2]
    heap = out[start:start + size]
    if not heap.endswith(b"\0") or b".dll\0" not in heap:
        raise ValueError(error)
    start, size = sections[20]
    images = list(struct.iter_unpack("<10i", out[start:start + size]))
    if not images or len(images) != sections[21][1] // 64:
        raise ValueError(error)
    for name, assembly, first, count, exported, exports, *_ in images:
        if not (0 <= name < len(heap) and 0 <= assembly < len(images)
                and count >= 0 and first >= -1 and (first >= 0 or count == 0) and first + count <= sections[19][1] // 88
                and exports >= 0 and exported >= -1 and (exported >= 0 or exports == 0)
                and exported + exports <= sections[30][1] // 4):
            raise ValueError(error)
    if max((r[4] + r[5] for r in images if r[4] >= 0), default=0) * 4 != sections[30][1]:
        raise ValueError(error)
    header = [0xFAB11BAF, 29, *(value for pair in sections for value in pair)]
    struct.pack_into("<64I", out, 0, *header)
    return bytes(out)


def _type_nodes(nodes: list, empty_refs: bool = False):
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    for i, n in enumerate(nodes[:-3]):
        # The generator names a list after its element type; UnityPy then reads one string.
        if nodes[i + 1]["m_Type"] == "Array" and nodes[i + 1]["m_Level"] == n["m_Level"] + 1 \
                and nodes[i + 3]["m_Type"] == n["m_Type"] and n["m_Type"] != "vector":
            n["m_Type"] = "vector"
    if empty_refs:
        # UnityPy cannot read/write null references without ref_types; validate their headers after reading.
        for n in nodes:
            if n["m_Type"] in ("ReferencedObject", "ReferencedObjectData"):
                n["m_Type"] = "Empty" + n["m_Type"]
    return TypeTreeNode.from_list([TypeTreeNode(n["m_Level"], n["m_Type"], n["m_Name"], 0, 0,
                                              m_MetaFlag=n["m_MetaFlag"]) for n in nodes])


class _Context:
    """Per game: MonoScripts of the main files and a TypeTree generator for builds without stored trees."""

    def __init__(self, game_dir: Path):
        global _fallback
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        data = _data_dir(game_dir)
        self.env = UnityPy.Environment()
        for p in _files(game_dir):                  # from bytes: an open file could not be replaced on Windows
            self.env.load_file(p.read_bytes(), name=str(p))
        if not self.env.objects:
            raise ValueError("讀不到任何 Unity 資源（檔案可能已加密或使用未支援的格式）")
        self.version = self.env.objects[0].assets_file.unity_version
        self.scripts = {(Path(f.name).name, o.path_id): o for f in self.env.files.values()
                        for o in getattr(f, "objects", {}).values() if o.type.name == "MonoScript"}
        # AssetsTools' IL2CPP path uses extensionless names and process-global metadata shared across games.
        backend = "AssetStudio" if (game_dir / "GameAssembly.dll").is_file() else "AssetsTools"
        self.game_dir = game_dir
        self.data = data
        self.il2cpp = backend == "AssetStudio"
        self.fallback_nodes = {}
        self.reference_nodes = {}
        self.assemblies = None
        # IL2CPP assembly conversion still uses global native state while loading.
        with _generator_lock:
            _fallback = (None, None)
            self.patched_generator = _NATIVE.is_file()
            if self.patched_generator:
                from TypeTreeGeneratorAPI.TypeTreeGenerator import init_dll
                init_dll(str(_NATIVE))
            self.gen = TypeTreeGenerator(self.version, generator=backend)
            try:
                self._load_generator(self.gen)
                self.assemblies = {name if name.endswith(".dll") else name + ".dll"
                                   for name in self.gen.get_loaded_dll_names()}
            except (AssertionError, ValueError, OSError):  # stored trees and TextAssets remain usable
                self.gen = None
        self.nodes: dict[tuple, object | None] = {}

    def _load_generator(self, gen):
        if self.il2cpp:
            gen.load_il2cpp((self.game_dir / "GameAssembly.dll").read_bytes(),
                           _metadata((self.data / "il2cpp_data/Metadata/global-metadata.dat").read_bytes()))
        else:
            gen.load_local_game(str(self.game_dir))

    def _fallback_generator(self):
        """Caller holds _generator_lock: AssetsTools reads process-global IL2CPP metadata."""
        global _fallback
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        if _fallback[0] is not self:
            _fallback = (None, None)
            gen = TypeTreeGenerator(self.version, generator="AssetsTools")
            self._load_generator(gen)
            _fallback = (self, gen)
        return _fallback[1]

    def _restore_references(self, obj, nodes):
        from UnityPy.helpers.TypeTreeHelper import TypeTreeConfig, read_value
        from UnityPy.streams import EndianBinaryReader
        sf = obj.assets_file
        slots = [r for r in sf.ref_types if r.node is None and r.m_ClassName is None]
        if sf._enable_type_tree or not slots:
            return
        if not nodes.m_Children or nodes.m_Children[-1].m_Type != "ManagedReferencesRegistry":
            return
        raw = obj.get_raw_data()
        reader = EndianBinaryReader(raw, endian=obj.reader.endian)
        config = TypeTreeConfig(True, sf, True)
        for field in nodes.m_Children[:-1]:
            read_value(field, reader, config)
        version, count = reader.read_int(), reader.read_int()
        if version != 2 or not 0 <= count <= (len(raw) - reader.Position) // 20:
            raise ValueError("不支援或已損壞的 managed reference registry")
        known = {(r.m_ClassName, r.m_NameSpace, r.m_AssemblyName): r.node for r in sf.ref_types if r.node is not None}
        pending = {}
        with _generator_lock:
            for _ in range(count):
                rid = reader.read_long()                 # references point to this ID, not a type-table index
                key = tuple(reader.read_aligned_string() for _ in range(3))
                cls, namespace, assembly = key
                if rid in (-1, -2) and not any(key):
                    continue
                if not cls:
                    raise ValueError("缺少 managed reference 類別名稱")
                node = known.get(key) or self.reference_nodes.get(key)
                if node is None:
                    full = f"{namespace}.{cls}" if namespace else cls
                    error = f"無法重建 managed reference {full}"
                    if key in self.reference_nodes:
                        raise ValueError(error)
                    try:
                        gen = self.gen if getattr(self, "patched_generator", False) else self._fallback_generator()
                        data = json.loads(_nodes_json(gen, assembly.removesuffix(".dll"), full))
                    except AssertionError as ex:
                        self.reference_nodes[key] = None
                        raise ValueError(error) from ex
                    node = _type_nodes(data)
                    if [n.m_Name for n in node.m_Children[:4]] != ["m_GameObject", "m_Enabled", "m_Script", "m_Name"]:
                        raise ValueError(f"managed reference {full} 的型別樹標頭不正確")
                    # Referenced classes are plain objects, without the generator's MonoBehaviour header.
                    node.m_Children = node.m_Children[4:]
                    self.reference_nodes[key] = node
                read_value(node, reader, config)
                if key not in known:
                    pending[key] = node
        if reader.Position != len(raw) or len(pending) > len(slots):
            raise ValueError("managed reference 資料長度或型別數目不符")
        # With type trees disabled these fields are runtime-only; keep table order, counts and hashes intact.
        for ref, (key, node) in zip(slots, pending.items()):
            ref.m_ClassName, ref.m_NameSpace, ref.m_AssemblyName = key
            ref.node = node

    def _fallback_tree(self, obj, key, assembly, full):
        empty_refs = not obj.assets_file.ref_types
        cache_key = (*key, empty_refs)
        if cache_key not in self.fallback_nodes:
            with _generator_lock:
                try:
                    nodes = json.loads(_nodes_json(self._fallback_generator(), assembly.removesuffix(".dll"), full))
                    self.fallback_nodes[cache_key] = _type_nodes(nodes, empty_refs)
                except (AssertionError, ValueError, OSError):
                    self.fallback_nodes[cache_key] = None
        nodes = self.fallback_nodes[cache_key]
        if nodes is None:
            raise ValueError(f"無法產生 {assembly} / {full} 的替代型別樹")
        return self._read_tree(obj, nodes), nodes

    def _read_tree(self, obj, nodes):
        global _null_reference_reader_installed
        if not nodes.m_Children or nodes.m_Children[-1].m_Type != "ManagedReferencesRegistry":
            return obj.read_typetree(nodes)
        empty_refs = not obj.assets_file.ref_types
        if not empty_refs:
            self._restore_references(obj, nodes)
        tree = obj.read_typetree(nodes)
        nulls = [ref for ref in (tree.get("references") or {}).get("RefIds", [])
                 if ref["rid"] in (-1, -2) and not any(ref["type"].values())]
        if nulls and not empty_refs:
            # UnityPy 1.25.2 omits null data while reading, but its writer requires a node and data.
            from UnityPy.helpers import TypeTreeHelper
            for ref in nulls:
                ref["data"] = {}
            with _generator_lock:
                if not _null_reference_reader_installed:
                    original = TypeTreeHelper.get_ref_type_node
                    empty = _type_nodes([dict(m_Level=0, m_Type="EmptyReference", m_Name="Base", m_MetaFlag=0)])
                    def reference_node(ref, sf):
                        if isinstance(ref["type"], dict) and ref["rid"] in (-1, -2) and not any(ref["type"].values()):
                            return empty
                        return original(ref, sf)
                    TypeTreeHelper.get_ref_type_node = reference_node
                    _null_reference_reader_installed = True
        if empty_refs:
            for ref in (tree.get("references") or {}).get("RefIds", []):
                if ref["rid"] not in (-1, -2) or any(ref["type"].values()):
                    raise ValueError("缺少非空 managed reference 的型別資料")
        return tree

    def script(self, obj):
        raw = obj.get_raw_data()
        fid, pid = struct.unpack_from("<iq", raw, 16)       # after m_GameObject (PPtr) and m_Enabled (aligned)
        name = Path(obj.assets_file.name).name if fid == 0 else Path(obj.assets_file.externals[fid - 1].path).name
        ms = self.scripts.get((name, pid))
        if ms is None and fid == 0:
            ms = obj.assets_file.objects.get(pid)
        return ms.read() if ms is not None else None

    def tree(self, obj) -> tuple[str, dict, object]:
        """(class name, tree, nodes for save_typetree); nodes None when the file stores its own tree."""
        ms = self.script(obj)
        cls = ms.m_ClassName if ms else ""
        if obj.serialized_type is not None and getattr(obj.serialized_type, "node", None) is not None:
            return cls, obj.read_typetree(), None
        if ms is None:
            raise ValueError("找不到 MonoScript")
        if self.gen is None:
            raise ValueError("無法載入遊戲的程式碼（IL2CPP），無法讀取此腳本")
        assembly = ms.m_AssemblyName
        if not assembly or not cls:
            raise ValueError("MonoScript 的組件名稱或類別名稱為空")
        if not assembly.endswith(".dll"):
            assembly += ".dll"
        if getattr(self, "assemblies", None) is not None and assembly not in self.assemblies:
            raise ValueError(f"遊戲未包含腳本組件 {assembly}（{ms.m_Namespace}.{cls}）")
        empty_refs = not getattr(getattr(obj, "assets_file", None), "ref_types", None)
        key = (assembly, ms.m_Namespace, cls, empty_refs)
        full = f"{ms.m_Namespace}.{cls}" if ms.m_Namespace else cls
        error = f"無法產生 {assembly} / {full} 的型別樹（類別或依賴缺失，或格式不支援）"
        if key in self.nodes and self.nodes[key] is None:
            raise ValueError(error)
        if key not in self.nodes:
            try:
                nodes = json.loads(_nodes_json(self.gen, assembly, full))
            except AssertionError as ex:                     # the generator asserts when it cannot build the class
                # Thousands of objects can share one unsupported class; do not retry its native generator.
                self.nodes[key] = None
                raise ValueError(error) from ex
            self.nodes[key] = _type_nodes(nodes, empty_refs)
        try:
            return cls, self._read_tree(obj, self.nodes[key]), self.nodes[key]
        except (ValueError, EOFError, IndexError, struct.error, UnicodeDecodeError):
            if not getattr(self, "il2cpp", False):
                raise
            tree, nodes = self._fallback_tree(obj, key, assembly, full)
            return cls, tree, nodes


def _context(game_dir: Path) -> _Context:
    key = str(Path(game_dir).resolve())
    if key not in _contexts:
        _contexts[key] = _Context(game_dir)
    return _contexts[key]


def _files(game_dir: Path) -> list[Path]:
    data = _data_dir(game_dir)
    return sorted(p for p in data.iterdir() if p.is_file() and p.suffix.lower() not in _SKIP_SUFFIX
                  and (p.suffix.lower() == ".assets" or p.name.startswith("level") or p.name == "globalgamemanagers"))


def _assemblies(game_dir: Path) -> list[Path]:
    return sorted((_data_dir(game_dir) / "Managed").glob("Assembly-CSharp*.dll"))


def _dll_entries(raw: bytes) -> list[dict]:
    """Sentences loaded with ldstr; short words and paths are often keys the code compares or loads by."""
    try:
        asm = clr.Assembly(raw)
    except (clr.ClrError, struct.error, IndexError):
        return []                           # unreadable or signed assemblies are left alone
    used = {off for _, off in asm.ldstr}
    return [{"loc": f"US:{off}", "kind": "dialogue", "text": s} for off, s in asm.strings.items()
            if off in used and _JA.search(s) and (_SENTENCE.search(s) or len(s) >= 6) and not _PATH.search(s)]


def _bundles(game_dir: Path) -> list[Path]:
    return sorted(_data_dir(game_dir).glob(_NANI))


def _load(rel: str, raw: bytes):
    """(file to save, serialized file holding the objects): the same file, or an AssetBundle and its first member."""
    import UnityPy
    root = next(iter(UnityPy.load(raw).files.values()))
    if hasattr(root, "objects"):
        root.name = Path(rel).name                               # externals resolve by file name
        return root, root
    return root, next(f for f in root.files.values() if hasattr(f, "objects"))


def _save(root, sf) -> bytes:
    return sf.save() if root is sf else root.save(packer="lz4")


def _catalog(raw: bytes) -> bytes:
    """Zero the bundle CRCs in an Addressables catalog, keeping every entry's length."""
    import base64
    text = raw.decode("utf-8")
    m = re.search(r'"m_ExtraDataString"\s*:\s*"([^"]*)"', text)
    if not m:
        return raw
    tag = '"m_Crc":'.encode("utf-16-le")                       # the options JSON is stored as UTF-16LE
    extra = re.sub(re.escape(tag) + rb"((?:[0-9]\x00)+)",
                   lambda g: tag + "0".rjust(len(g.group(1)) // 2).encode("utf-16-le"), base64.b64decode(m.group(1)))
    return (text[:m.start(1)] + base64.b64encode(extra).decode() + text[m.end(1):]).encode("utf-8")


def _sheet_entries(sheet: dict, base: str) -> list[dict]:
    rows = sheet.get("rows") or []
    head = sheet.get("headerRow", 0)
    if len(rows) <= head:
        return []
    header = rows[head]["strings"]
    cols = {i: _COLUMNS[h] for i, h in enumerate(header) if h in _COLUMNS}
    cmd = header.index("Command") if "Command" in header else None
    arg1 = header.index("Arg1") if "Arg1" in header else None
    out = []
    for r, row in enumerate(rows[head + 1:], head + 1):
        cells = row["strings"]
        for c, kind in cols.items():
            if c >= len(cells) or not _JA.search(cells[c]):
                continue
            speaker = None
            if kind is None:                     # scenario Text is always shown (games add custom commands for it)
                command = cells[cmd] if cmd is not None and cmd < len(cells) else ""
                kind = "choice" if command.startswith("Selection") else "dialogue"
                if command == "" and arg1 is not None and arg1 < len(cells) and cells[arg1]:
                    speaker = cells[arg1]
            out.append({"loc": f"{base}/rows/{r}/strings/{c}", "kind": kind, "text": cells[c], "speaker": speaker})
    return out


def _entries(cls: str, tree: dict, pid: int) -> list[dict]:
    if cls in _UI and isinstance(tree.get(_UI[cls]), str) and _JA.search(tree[_UI[cls]]):
        return [{"loc": f"{pid}:/{_UI[cls]}", "kind": "ui", "text": tree[_UI[cls]]}]
    out = []
    if cls == "AdvImportBook":
        for i, sheet in enumerate(tree.get("importGridList") or []):
            out += _sheet_entries(sheet, f"{pid}:/importGridList/{i}")
    elif cls == "AdvChapterData":
        for i, sheet in enumerate(tree.get("settingList") or []):
            out += _sheet_entries(sheet, f"{pid}:/settingList/{i}")
    elif cls == "Script":
        for i, ref in enumerate((tree.get("references") or {}).get("RefIds") or []):
            data = ref.get("data") or {}
            fields = _NANI_TEXT.get(ref.get("type", {}).get("class"), {})
            author = (data.get("AuthorId") or {}).get("value") if "AuthorId" in fields else None
            for field, kind in fields.items():
                v = data.get(field) or {}
                if v.get("hasValue") and isinstance(v.get("value"), str) and _JA.search(v["value"]):
                    e = {"loc": f"{pid}:/references/RefIds/{i}/data/{field}/value", "kind": kind, "text": v["value"]}
                    if kind == "dialogue" and author:
                        e["speaker"] = author
                    out.append(e)
    elif cls and cls not in _UI and not _NOT_TEXT.match(cls):
        found = [(p, s, re.sub(r"/\d+", "/#", p)) for p, s in _strings(tree) if _JA.search(s)]
        text = {f for _, s, f in found if _SENTENCE.search(s) or len(s) >= 6}
        out += [{"loc": f"{pid}:{p}", "kind": "dialogue", "text": s} for p, s, f in found if f in text]
    return out


def _strings(node, path: str = ""):
    """(JSON pointer, value) of every string in a tree except the object's own name."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if path or k != "m_Name":
                yield from _strings(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}/{i}")


def _table_entries(text: str, pid: int) -> list[dict]:
    """Utage localization TextAsset: tab separated, header "Key<TAB>Japanese<TAB>English"."""
    lines = text.split("\n")
    header = lines[0].lstrip("﻿").rstrip("\r").split("\t")
    if "Japanese" not in header:
        return []
    col = header.index("Japanese")
    out = []
    for i, line in enumerate(lines[1:], 1):
        cells = line.rstrip("\r").split("\t")
        if col < len(cells) and _JA.search(cells[col]):
            out.append({"loc": f"{pid}:L{i}:{col}", "kind": "ui", "text": cells[col]})
    return out


def _set(tree, pointer: str, value: str) -> None:
    parts = pointer.strip("/").split("/")
    node = tree
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _system_font(need: set[str], names: list[str]) -> bytes | None:
    """A font shipped with Windows for the script the translated characters lean to, in the style most of the game's
    CJK fonts have: Ming / SimSun for serif, JhengHei / YaHei (bold when they are) otherwise."""
    import opencc
    t2s, s2t = opencc.OpenCC("t2s"), opencc.OpenCC("s2t")
    trad = sum(1 for c in need if t2s.convert(c) != c) > sum(1 for c in need if s2t.convert(c) != c)
    serif = sum(1 for n in names if _SERIF.search(n)) * 2 > len(names)
    bold = sum(1 for n in names if _BOLD.search(n)) * 2 > len(names)
    if serif:
        name = "mingliu.ttc" if trad else "simsun.ttc"
    else:
        name = ("msjh" if trad else "msyh") + ("bd" if bold else "") + ".ttc"
    path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
    return path.read_bytes() if path.is_file() else None


def _font_chars(tree: dict) -> set[str]:
    return {chr(c["m_Unicode"]) for c in tree.get("m_CharacterTable") or [] if c.get("m_Unicode", 0) < 0x110000}


class Unity(EnginePlugin):
    name = "unity"
    version = "1.0.0"
    control_codes = [r"<[^<>]*>", r"\{[^{}]*\}"]          # TMP / Utage rich text tags, format arguments

    def detect(self, game_dir: Path) -> float:
        return 1.0 if _data_dir(game_dir) is not None and (game_dir / "UnityPlayer.dll").is_file() else 0.0

    def scan_fingerprint(self, game_dir: Path) -> str:
        from importlib.metadata import version
        import sys
        digest = hashlib.sha256()
        if _NATIVE.is_file():
            digest.update(hashlib.sha256(_NATIVE.read_bytes()).digest())
        digest.update(repr((sys.version_info[:2], version("UnityPy"), version("TypeTreeGeneratorAPI"))).encode())
        root = Path(__file__).resolve().parents[1]
        for source in sorted(root.rglob("*.py")):
            digest.update(source.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).digest())
        data = _data_dir(game_dir)
        paths = set(_files(game_dir) + _bundles(game_dir) + list((data / "Managed").glob("*.dll")))
        paths.update(p for p in (game_dir / "GameAssembly.dll", data / "il2cpp_data/Metadata/global-metadata.dat")
                     if p.is_file())
        backup = Backup(game_dir)
        for path in sorted(paths):
            rel = path.relative_to(game_dir).as_posix()
            digest.update(rel.encode() + b"\0")
            with path.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
            if rel in backup.files:
                digest.update(hashlib.sha256(backup.original_bytes(rel)).digest())
        fingerprint = digest.hexdigest()
        key = str(game_dir.resolve())
        if _scan_fingerprints.get(key) != fingerprint:
            _contexts.pop(key, None)               # metadata and MonoScripts must also refresh after a game update
            _scan_fingerprints[key] = fingerprint
        return fingerprint

    def locate(self, game_dir: Path) -> list[str]:
        ctx = _context(game_dir)
        backup = Backup(game_dir)
        found = []
        for p in _files(game_dir) + _bundles(game_dir) + _assemblies(game_dir):
            rel = p.relative_to(game_dir).as_posix()
            if json.loads(self.decode(rel, backup.original_bytes(rel), game_dir)[0]):
                found.append(rel)
        if not found:
            raise ValueError("找不到可翻譯的 Unity 文字（可能在 AssetBundle 內或未支援的框架）")
        return found

    def title(self, game_dir: Path, read) -> str:
        data = _data_dir(game_dir)
        app = data / "app.info" if data else None
        try:
            return app.read_text("utf-8").splitlines()[-1].strip() if app and app.is_file() else ""
        except (OSError, IndexError):
            return ""

    def decode(self, rel: str, raw: bytes, game_dir: Path | None = None) -> tuple[str, dict]:
        info = {"encoding": "utf-8", "bom": False, "newline": "\n"}
        if rel.endswith(".dll"):
            return json.dumps(_dll_entries(raw), ensure_ascii=False), info
        ctx = _context(game_dir)
        _, sf = _load(rel, raw)
        out = []
        for pid, obj in sf.objects.items():
            t = obj.type.name
            try:
                if t == "TextAsset":
                    data = obj.read()
                    if isinstance(data.m_Script, str):
                        out += _table_entries(data.m_Script, pid)
                elif t == "MonoBehaviour":
                    cls, tree, _ = ctx.tree(obj)
                    out += _entries(cls, tree, pid)
            except (ValueError, KeyError, EOFError, IndexError, struct.error, UnicodeDecodeError):
                continue                    # objects our rules cannot read are left alone
        return json.dumps(out, ensure_ascii=False), info

    def parse(self, rel: str, text: str) -> list[Segment]:
        return [Segment(e["loc"], e["kind"], e["text"], e.get("speaker")) for e in json.loads(text)]

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError("Unity 以 outputs() 重寫整個資產檔")

    def fit(self, kind: str, text: str) -> str:
        # A kana reading over translated Chinese is wrong, so keep only the ruby base text.
        return _RUBY.sub(r"\1", text)

    def _apply(self, game_dir: Path, rel: str, raw: bytes, repl: dict[str, str]) -> bytes:
        if rel.endswith(".dll"):
            return clr.Assembly(raw).save({int(loc[3:]): value for loc, value in repl.items()})
        ctx = _context(game_dir)
        root, sf = _load(rel, raw)
        by_obj: dict[int, dict[str, str]] = {}
        for loc, value in repl.items():
            pid, _, pointer = loc.partition(":")
            by_obj.setdefault(int(pid), {})[pointer] = value
        for pid, changes in by_obj.items():
            obj = sf.objects[pid]
            if obj.type.name == "TextAsset":
                data = obj.read()
                lines = data.m_Script.split("\n")
                for pointer, value in changes.items():
                    i, c = map(int, pointer[1:].split(":"))
                    cr = "\r" if lines[i].endswith("\r") else ""
                    cells = lines[i][:len(lines[i]) - len(cr)].split("\t")
                    cells[c] = value.replace("\t", " ").replace("\r", "").replace("\n", "\\n")
                    lines[i] = "\t".join(cells) + cr
                data.m_Script = "\n".join(lines)
                data.save()
            else:
                _, tree, nodes = ctx.tree(obj)
                for pointer, value in changes.items():
                    _set(tree, pointer, value)
                obj.save_typetree(tree, nodes) if nodes is not None else obj.save_typetree(tree)
        return _save(root, sf)

    def _fonts(self, game_dir: Path, sources: dict[str, bytes], need: set[str]) -> dict[str, bytes]:
        """Give TMP fonts that lack translated characters the game's other CJK fonts in the same file as fallbacks."""
        ctx = _context(game_dir)
        out = {}
        for rel, raw in sources.items():
            if rel.endswith((".bundle", ".dll")):
                continue
            _, sf = _load(rel, raw)
            fonts = {}
            for pid, obj in sf.objects.items():
                if obj.type.name != "MonoBehaviour":
                    continue
                try:
                    cls, tree, nodes = ctx.tree(obj)
                except (ValueError, KeyError, EOFError, IndexError, struct.error):
                    continue
                if cls == "TMP_FontAsset":
                    fonts[pid] = (obj, tree, nodes, _font_chars(tree))
            cjk = [pid for pid, f in fonts.items() if sum(1 for ch in f[3] if _CJK.match(ch)) >= 2000]
            changed = False
            for pid in cjk:
                obj, tree, nodes, chars = fonts[pid]
                table = tree.setdefault("m_FallbackFontAssetTable", [])
                linked = {(p["m_FileID"], p["m_PathID"]) for p in table}
                missing = need - chars
                # Fonts covering more of the missing characters come first.
                for other in sorted((o for o in cjk if o != pid), key=lambda o: -len(missing & fonts[o][3])):
                    if (0, other) not in linked and missing & fonts[other][3]:
                        table.append({"m_FileID": 0, "m_PathID": other})
                        changed = True
                if changed:
                    obj.save_typetree(tree, nodes) if nodes is not None else obj.save_typetree(tree)
            if changed:
                out[rel] = sf.save()
        return out

    def _dynamic(self, game_dir: Path, get, need: set[str]) -> dict[str, bytes]:
        """Point the dynamic fallbacks of TMP_Settings' default font at a CJK font file, list them as global fallbacks
        and empty the character tables of the game's CJK fonts (also in AssetBundles), so every character falls through
        to the one font. Returns every file involved: changed when there is something to show, else as get() gives it;
        empty when the game has no such fallback."""
        ctx = _context(game_dir)
        data = _data_dir(game_dir).relative_to(game_dir).as_posix()
        files = {Path(f.name).name: f for f in ctx.env.files.values() if hasattr(f, "objects")}

        def ref(sf, p):
            return Path(sf.name).name if p["m_FileID"] == 0 else Path(sf.externals[p["m_FileID"] - 1].path).name

        def read(sf, pid):
            try:
                return ctx.tree(sf.objects[pid])
            except (ValueError, KeyError, EOFError, IndexError, struct.error):
                return "", {}, None
        settings = next(((n, pid) for n, f in files.items() for pid, o in f.objects.items()
                         if o.type.name == "MonoBehaviour" and read(f, pid)[0] == "TMP_Settings"), None)
        if settings is None:
            return {}
        sname, spid = settings
        default = read(files[sname], spid)[1].get("m_defaultFontAsset") or {}
        if not default.get("m_PathID"):
            return {}
        fname = ref(files[sname], default)
        targets = []                            # dynamic fallbacks of the default font, kept in its own file
        for p in read(files[fname], default["m_PathID"])[1].get("m_FallbackFontAssetTable") or []:
            tree = read(files[fname], p["m_PathID"])[1] if p["m_FileID"] == 0 else {}
            src = tree.get("m_SourceFontFile") or {}
            if tree.get("m_AtlasPopulationMode") == 1 and src.get("m_FileID") == 0 and src.get("m_PathID"):
                targets.append((p["m_PathID"], src["m_PathID"]))
        if not targets:
            return {}
        frel, srel = f"{data}/{fname}", f"{data}/{sname}"
        cjk: dict[str, list[tuple[int, str]]] = {}      # rel -> (path id, name) of static fonts with many CJK glyphs
        stream = _data_dir(game_dir) / "StreamingAssets"
        bundles = [b for b in stream.rglob("*.bundle") if b.stat().st_size < 64 << 20 and "/scripts/" not in b.as_posix()]
        for path in _files(game_dir) + bundles:
            rel = path.relative_to(game_dir).as_posix()
            _, sf = _load(rel, get(rel))
            for pid, obj in sf.objects.items():
                if obj.type.name != "MonoBehaviour":
                    continue
                try:
                    tree = ctx.tree(obj)[1]
                except (ValueError, KeyError, EOFError, IndexError, struct.error):
                    continue
                if tree.get("m_AtlasPopulationMode") == 0 and \
                        sum(1 for ch in _font_chars(tree) if _CJK.match(ch)) >= 2000:
                    cjk.setdefault(rel, []).append((pid, tree.get("m_Name", "")))
        out = {rel: get(rel) for rel in [frel, srel, *cjk]}
        font = _system_font(need, [n for fonts in cjk.values() for _, n in fonts]) if need else None
        if font is None:
            return out
        for rel in out:
            root, sf = _load(rel, out[rel])
            for pid, _ in cjk.get(rel, []):
                obj = sf.objects[pid]
                _, tree, nodes = ctx.tree(obj)
                tree["m_CharacterTable"] = [c for c in tree.get("m_CharacterTable") or []
                                            if not _UNIFIED.match(chr(min(c.get("m_Unicode", 0), 0x10FFFF)))]
                obj.save_typetree(tree, nodes) if nodes is not None else obj.save_typetree(tree)
            if rel == frel:
                for pid, src in targets:
                    obj = sf.objects[pid]
                    _, tree, nodes = ctx.tree(obj)
                    size = 2048                 # the atlas is created at run time; more and larger pages for CJK
                    tree.update(m_IsMultiAtlasTexturesEnabled=1, m_AtlasWidth=size, m_AtlasHeight=size,
                                m_UsedGlyphRects=[],
                                m_FreeGlyphRects=[{"m_X": 0, "m_Y": 0, "m_Width": size - 1, "m_Height": size - 1}])
                    obj.save_typetree(tree, nodes) if nodes is not None else obj.save_typetree(tree)
                    fo = sf.objects[src]
                    ft = fo.read_typetree()
                    ft["m_FontData"] = list(font)
                    fo.save_typetree(ft)
            if rel == srel:
                obj = sf.objects[spid]
                _, tree, nodes = ctx.tree(obj)
                table = tree.setdefault("m_fallbackFontAssets", [])
                for pid, _ in targets:
                    if not any(p["m_FileID"] == default["m_FileID"] and p["m_PathID"] == pid for p in table):
                        table.append({"m_FileID": default["m_FileID"], "m_PathID": pid})
                obj.save_typetree(tree, nodes) if nodes is not None else obj.save_typetree(tree)
            out[rel] = _save(root, sf)
        return out

    def _legacy(self, game_dir: Path, get, need: set[str]) -> dict[str, bytes]:
        """Replace the data of embedded Unity fonts (legacy UI Text) with a Windows CJK font in the same style; fonts a
        TMP asset renders from are left to _dynamic(). Returns every file involved, unchanged when nothing is needed."""
        ctx = _context(game_dir)
        out = {}
        for path in _files(game_dir):
            rel = path.relative_to(game_dir).as_posix()
            root, sf = _load(rel, get(rel))
            fonts, sources = [], set()
            for pid, obj in sf.objects.items():
                if obj.type.name == "Font":
                    fonts.append(pid)
                elif obj.type.name == "MonoBehaviour":
                    try:
                        src = ctx.tree(obj)[1].get("m_SourceFontFile") or {}
                    except (ValueError, KeyError, EOFError, IndexError, struct.error):
                        continue
                    if src.get("m_FileID") == 0:
                        sources.add(src.get("m_PathID"))
            targets = []
            for pid in fonts:
                if pid not in sources:
                    tree = sf.objects[pid].read_typetree()
                    if tree.get("m_FontData"):
                        targets.append((pid, tree))
            if not targets:
                continue
            out[rel] = get(rel)
            if not need:
                continue
            for pid, tree in targets:
                font = _system_font(need, [tree.get("m_Name", "")])
                if font is not None:
                    tree["m_FontData"] = list(font)
                    sf.objects[pid].save_typetree(tree)
            out[rel] = _save(root, sf)
        return out

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]],
                game_dir: Path | None = None) -> dict[str, tuple[bytes, int]]:
        backup = Backup(game_dir)
        result = {}
        for rel in texts:                  # every file we may have patched: unchanged ones go back to the original
            raw = backup.original_bytes(rel)
            result[rel] = (self._apply(game_dir, rel, raw, repl[rel]) if rel in repl else raw, len(repl.get(rel, {})))
        need = {ch for r in repl.values() for v in r.values() for ch in v if _CJK.match(ch)}
        get = lambda rel: result[rel][0] if rel in result else backup.original_bytes(rel)
        fonts = self._dynamic(game_dir, get, need)
        for rel, data in fonts.items():
            result[rel] = (data, result.get(rel, (b"", 0))[1])
        if need and not fonts:
            for rel, data in self._fonts(game_dir, {rel: data for rel, (data, _) in result.items()}, need).items():
                result[rel] = (data, result[rel][1])
        for rel, data in self._legacy(game_dir, get, need).items():
            result[rel] = (data, result.get(rel, (b"", 0))[1])
        catalog = f"{_data_dir(game_dir).relative_to(game_dir).as_posix()}/{_CATALOG}"
        if any(rel.endswith(".bundle") for rel in result) and (game_dir / catalog).is_file():
            raw = backup.original_bytes(catalog)
            result[catalog] = (_catalog(raw) if need else raw, 0)
        return result
