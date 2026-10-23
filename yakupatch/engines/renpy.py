"""Ren'Py: dialogue from compiled scripts (.rpyc, loose or inside .rpa), written back as a new language
in game/tl/yakupatch/ that the game is switched to; the original scripts are never modified (§10)."""
import ast
import hashlib
import io
import json
import pickle
import re
import struct
import zlib
from pathlib import Path

from .. import vfs
from ..safety import MAX_FILE_BYTES
from .base import EnginePlugin, LangPack, Segment

LANG = "yakupatch"
OUT = f"game/tl/{LANG}/"
LANG_FILE = f"game/{LANG}_language.rpy"
_SAFE = {("builtins", n) for n in ("set", "frozenset", "object", "list", "dict", "tuple", "str", "bytes", "int",
                                   "float", "bool", "complex", "slice")}
_SAFE |= {("__builtin__", n) for _, n in _SAFE} | {("collections", "OrderedDict"), ("collections", "defaultdict")}
_ZH_TL = re.compile(r"^(zh|cn|tw|chs|cht|sc|tc|chinese|schinese|tchinese|简|繁|中)", re.I)
_CHARACTER = {"Character", "ADVCharacter", "NVLCharacter"}
_WHAT = "\0"                                     # where the translated text goes in a code template
_LINKS = {"next", "after", "chain", "translation_relevant"}    # graph links, not children


class _Node:
    """Stand-in for any renpy.* class: keeps the pickled state, never runs game code."""

    def __init__(self, *args, **kwargs):
        self._args = args

    def __setstate__(self, state):
        if isinstance(state, tuple) and len(state) == 2:          # (dict, slots)
            state = {**(state[0] or {}), **(state[1] or {})}
        if isinstance(state, dict):
            self.__dict__.update({k.decode() if isinstance(k, bytes) else k: v for k, v in state.items()})


class _Expr(str):
    """renpy.ast.PyExpr: the source text of a Python expression."""

    def __new__(cls, s="", *args):
        return str.__new__(cls, s)


class _Code(_Node):
    """renpy.ast.PyCode pickles as (version, source, location, mode[, py])."""

    def __setstate__(self, state):
        if isinstance(state, tuple) and len(state) >= 2:
            self.source = state[1]
        else:
            super().__setstate__(state)


class _List(list, _Node):
    pass


class _Dict(dict, _Node):
    pass


class _Set(set, _Node):
    pass


class _Unpickler(pickle.Unpickler):
    _classes: dict = {}

    def find_class(self, module, name):
        if (module, name) in _SAFE:
            return super().find_class("builtins" if module == "__builtin__" else module, name)
        if module in ("copy_reg", "copyreg") and name == "_reconstructor":
            return _reconstruct
        if module == "_codecs" and name == "encode":
            return lambda s, enc="latin-1": s.encode(enc)
        if not module.startswith(("renpy.", "store")):
            raise pickle.UnpicklingError(f"腳本含不允許的物件：{module}.{name}")
        if name == "PyExpr":
            return _Expr
        base = _List if name.endswith("List") else _Dict if name.endswith("Dict") else \
            _Set if name.endswith("Set") else _Code if name == "PyCode" else _Node
        key = (module, name)
        if key not in self._classes:
            self._classes[key] = type(name, (base,), {"__module__": "yakupatch.renpy." + module})
        return self._classes[key]


def _reconstruct(cls, base, state):
    return base.__new__(cls) if base is object else base.__new__(cls, state)


def load_rpyc(raw: bytes):
    """The statement list of a compiled script, as stand-in objects. Slot 2 (written since Ren'Py 6.99) holds
    the script after Ren'Py's own translation pass, so dialogue already sits in Translate nodes with ids."""
    if raw.startswith(b"RENPY RPC2"):
        pos, chunks = 10, {}
        while pos + 12 <= len(raw):
            slot, start, length = struct.unpack("<III", raw[pos:pos + 12])
            if slot == 0:
                break
            chunks[slot] = raw[start:start + length]
            pos += 12
        chunk = chunks.get(2) or chunks.get(1)
        if chunk is None:
            raise ValueError("rpyc 缺少腳本區段")
    else:
        chunk = raw
    data = zlib.decompressobj().decompress(chunk, MAX_FILE_BYTES)
    _, stmts = _Unpickler(io.BytesIO(data), encoding="utf-8", errors="surrogateescape").load()
    return stmts


def encode_say_string(s: str) -> str:
    """Ren'Py's own quoting for say statements and translation strings."""
    s = s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return '"' + re.sub(r"(?<= ) ", "\\ ", s) + '"'


def _say_code(node) -> str | None:
    """Say.get_code() with the text left as a placeholder; None for forms we do not reproduce."""
    if getattr(node, "arguments", None) or not isinstance(getattr(node, "what", None), str):
        return None
    rv = [node.who] if getattr(node, "who", None) else []
    rv += list(getattr(node, "attributes", None) or ())
    if getattr(node, "temporary_attributes", None):
        rv += ["@", *node.temporary_attributes]
    rv.append(_WHAT)
    if not getattr(node, "interact", True):
        rv.append("nointeract")
    if getattr(node, "with_", None):
        rv += ["with", node.with_]
    return " ".join(rv)


def _literal(who):
    if isinstance(who, str) and who[:1] in "\"'":
        try:
            value = ast.literal_eval(who)
        except (ValueError, SyntaxError):
            return None
        return value if isinstance(value, str) else None
    return None


def _character_names(source: str) -> list[str]:
    """String names in Character("名前", ...) calls of a define statement; the code is parsed, never run."""
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) in _CHARACTER:
            arg = n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "name"), None)
            if isinstance(arg, ast.Call) and getattr(arg.func, "id", None) in ("_", "__") and arg.args:
                arg = arg.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                out.append(arg.value)
    return out


def entries(stmts) -> list[dict]:
    """Translatable units: dialogue in default-language Translate blocks (by Ren'Py's identifier), and
    strings Ren'Py translates through `translate strings` (menu choices, character names)."""
    out, locs, seen = [], set(), set()

    def string(kind, text):
        loc = "s" + hashlib.sha1(f"{kind}\0{text}".encode("utf-8")).hexdigest()[:12]
        if text and loc not in locs:
            locs.add(loc)
            out.append({"loc": loc, "kind": kind, "text": text})

    def visit(node, depth=0):
        if depth > 200 or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for v in node:
                visit(v, depth + 1)
            return
        if not isinstance(node, _Node):
            return
        name = type(node).__name__
        if name in ("Translate", "TranslateSay") and getattr(node, "language", None) is None:
            says = [node] if name == "TranslateSay" else [n for n in node.block if type(n).__name__ == "Say"]
            code = [_say_code(node)] if name == "TranslateSay" else \
                [_say_code(n) if type(n).__name__ == "Say" else getattr(n, "line", None) for n in node.block]
            if len(says) == 1 and all(code) and getattr(node, "identifier", None):
                say = says[0]
                speaker = _literal(getattr(say, "who", None))
                if speaker:
                    string("name", speaker)
                out.append({"loc": node.identifier, "kind": "dialogue", "text": say.what, "speaker": speaker,
                            "code": code})
            return
        if name == "Menu":
            for item in getattr(node, "items", None) or ():
                if isinstance(item[0], str):
                    string("choice", item[0])
                visit(item[2] if len(item) > 2 else None, depth + 1)
            return
        if name == "Define" and getattr(node, "code", None) is not None:
            for text in _character_names(getattr(node.code, "source", "") or ""):
                string("name", text)
            return
        for k, v in node.__dict__.items():
            if k not in _LINKS:
                visit(v, depth + 1)

    visit(stmts)
    return out


def _inner(rel: str) -> str:
    """Script path below game/, the same for a loose file and an archive member."""
    outer, inner = vfs.split(rel)
    return inner if inner is not None else rel[len("game/"):]


class RenPy(EnginePlugin):
    name = "renpy"
    version = "1.0.0"
    default_encoding = "utf-8"
    control_codes = [r"\{\{", r"\[\[", r"\{[^{}]*\}", r"\[[^\[\]]*\]"]

    def detect(self, game_dir: Path) -> float:
        return 1.0 if (game_dir / "renpy").is_dir() and (game_dir / "game").is_dir() else 0.0

    def _archives(self, game_dir: Path):
        for p in sorted((game_dir / "game").glob("*.rpa")):
            yield f"game/{p.name}", vfs.Rpa(p)

    def _skip(self, inner: str) -> bool:
        return inner.startswith("tl/") or inner == Path(LANG_FILE).name + "c" or not inner.endswith(".rpyc")

    def locate(self, game_dir: Path) -> list[str]:
        found = {}
        for outer, archive in self._archives(game_dir):
            for inner in archive.files:
                if not self._skip(inner):
                    found.setdefault(inner, f"{outer}{vfs.SEP}{inner}")
        game = game_dir / "game"
        for p in game.rglob("*.rpyc"):                             # loose files win over archived ones
            inner = p.relative_to(game).as_posix()
            if not self._skip(inner) and not inner.startswith("cache/"):
                found[inner] = f"game/{inner}"
        if not found:
            raise ValueError("找不到 Ren'Py 的編譯腳本（.rpyc）")
        return sorted(found.values())

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        names = {p.name for p in (game_dir / "game" / "tl").glob("*") if p.is_dir()}
        for _, archive in self._archives(game_dir):
            names |= {k.split("/")[1] for k in archive.files if k.startswith("tl/") and k.count("/") >= 2}
        return [LangPack("zh", f"game/tl/{n}", f"遊戲內有中文翻譯 tl/{n}，請在遊戲設定切換語言")
                for n in sorted(names) if n != LANG and _ZH_TL.match(n)]

    def title(self, game_dir: Path, read) -> str:
        try:
            rel = next(r for r in self.locate(game_dir) if _inner(r) == "options.rpyc")
            stmts = load_rpyc(read(rel))
        except (StopIteration, OSError, KeyError, ValueError, pickle.UnpicklingError, zlib.error):
            return ""
        for node in _walk(stmts):
            if type(node).__name__ == "Define" and getattr(node, "varname", None) == "name" and \
                    getattr(node, "store", "") == "store.config":
                source = getattr(node.code, "source", "") or ""
                m = re.match(r"""^\s*_?\(?\s*(['"])(.*)\1\s*\)?\s*$""", source)
                return m.group(2) if m else source
        return ""

    def decode(self, rel: str, raw: bytes) -> tuple[str, dict]:
        try:
            items = entries(load_rpyc(raw))
        except (pickle.UnpicklingError, zlib.error, EOFError, struct.error, AttributeError, TypeError) as ex:
            raise ValueError(f"無法讀取編譯腳本：{type(ex).__name__}") from None
        return json.dumps(items, ensure_ascii=False), {"encoding": "utf-8", "bom": False, "newline": "\n"}

    def parse(self, rel: str, text: str) -> list[Segment]:
        return [Segment(e["loc"], e["kind"], e["text"], e.get("speaker")) for e in json.loads(text)]

    def outputs(self, texts: dict[str, str], repl: dict[str, dict[str, str]]) -> dict[str, tuple[bytes, int]]:
        files, strings = {}, {}
        for rel, r in repl.items():
            blocks = []
            for e in json.loads(texts[rel]):
                value = r.get(e["loc"])
                if value is None:
                    continue
                if e["kind"] != "dialogue":
                    strings.setdefault(e["text"], value)     # one string, one translation for the whole game
                    continue
                body = "".join(f"    {line.replace(_WHAT, encode_say_string(value))}\n" for line in e["code"])
                blocks.append(f"translate {LANG} {e['loc'].replace('.', '_')}:\n{body}")
            if blocks:
                files[OUT + _inner(rel)[:-1]] = ("\n".join(blocks), len(blocks))
        if strings:
            body = "".join(f"    old {encode_say_string(s)}\n    new {encode_say_string(v)}\n\n"
                           for s, v in strings.items())
            files[OUT + "strings.rpy"] = (f"translate {LANG} strings:\n\n{body}", len(strings))
        if files:
            files[LANG_FILE] = (f'init 999 python:\n    config.language = "{LANG}"\n', 0)
        header = "# Generated by YakuPatch. Restore from YakuPatch to remove.\n\n"
        return {rel: ((header + text).encode("utf-8"), n) for rel, (text, n) in files.items()}

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        raise NotImplementedError("Ren'Py 不修改原腳本，請用 outputs()")


def _walk(node, depth=0):
    if isinstance(node, (list, tuple)) and depth < 50:
        for v in node:
            yield from _walk(v, depth + 1)
    elif isinstance(node, _Node):
        yield node
        for k, v in node.__dict__.items():
            if k not in _LINKS and isinstance(v, (list, tuple)):
                yield from _walk(v, depth + 1)
