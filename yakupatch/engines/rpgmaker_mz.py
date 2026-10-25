"""RPG Maker MZ: data/*.json (PROJECT_PLAN §17.1)."""
import json
import re
from pathlib import Path

from ..jsonspan import escape, replace_strings, string_spans
from .base import EnginePlugin, LangPack, Segment

_DB_FIELDS = {
    "Actors.json": {"name": "name", "nickname": "name", "profile": "system"},
    "Classes.json": {"name": "name"},
    "Enemies.json": {"name": "name"},
    "Skills.json": {"name": "name", "description": "system", "message1": "system", "message2": "system"},
    "Items.json": {"name": "name", "description": "system"},
    "Weapons.json": {"name": "name", "description": "system"},
    "Armors.json": {"name": "name", "description": "system"},
    "States.json": {"name": "name", "message1": "system", "message2": "system",
                    "message3": "system", "message4": "system"},
}
_SYSTEM_LISTS = {"elements": "name", "skillTypes": "name", "weaponTypes": "name",
                 "armorTypes": "name", "equipTypes": "name"}
_TERM_LISTS = ("basic", "commands", "params")
_MAP = re.compile(r"^Map\d+\.json$")
_LANG_DIR = re.compile(r"^data[_\-]?(zh|cn|tw|chs|cht|sc|tc|chinese)", re.I)
# 357 plugin args: struct/array args are JSON text inside a string; "~j" marks "decode this string as JSON".
# escape() only emits ~0/~1, so ~j never occurs in a plain pointer.
_NESTED = "~j"
_TEXT_ARG = re.compile(r"text|message|label|title|caption|desc|help", re.I)
_FILE_ARG = re.compile(r"file|image|picture|pict|icon|sound|bgm|bgs|font|symbol|switch|variable", re.I)


class RpgMakerMZ(EnginePlugin):
    name = "rpgmaker-mz"
    version = "1.0.0"
    default_encoding = "utf-8"
    control_codes = [r"\\[A-Za-z]+(?:\[[^\]]*\])?", r"\\[{}\\.|!><^$]", r"%\d"]    # %1: battle message arguments

    def detect(self, game_dir: Path) -> float:
        if not (game_dir / "data" / "System.json").is_file():
            return 0.0
        return 1.0 if (game_dir / "js" / "rmmz_core.js").is_file() else 0.0

    def language_packs(self, game_dir: Path) -> list[LangPack]:
        # Not locales/*.pak: every NW.js build ships those, they say nothing about the game (§5.1).
        return [LangPack("zh", p.name, f"遊戲內有中文資料夾 {p.name}，請在遊戲內切換語言")
                for p in game_dir.iterdir() if p.is_dir() and _LANG_DIR.match(p.name)]

    def locate(self, game_dir: Path) -> list[str]:
        names = set(_DB_FIELDS) | {"System.json", "CommonEvents.json", "Troops.json"}
        return sorted(f"data/{p.name}" for p in (game_dir / "data").iterdir()
                      if p.is_file() and (p.name in names or _MAP.match(p.name)))

    def title(self, game_dir: Path, read) -> str:
        return json.loads(read("data/System.json").decode("utf-8-sig")).get("gameTitle") or ""

    def parse(self, rel: str, text: str) -> list[Segment]:
        data = json.loads(text)
        name = rel.rsplit("/", 1)[-1]
        out: list[Segment] = []
        if name in _DB_FIELDS:
            for i, entry in enumerate(data):
                if isinstance(entry, dict):
                    for field, kind in _DB_FIELDS[name].items():
                        _add(out, f"/{i}/{field}", kind, entry.get(field))
        elif name == "System.json":
            _add(out, "/gameTitle", "name", data.get("gameTitle"))
            _add(out, "/currencyUnit", "ui", data.get("currencyUnit"))
            for key, kind in _SYSTEM_LISTS.items():
                for i, s in enumerate(data.get(key) or []):
                    _add(out, f"/{key}/{i}", kind, s)
            terms = data.get("terms") or {}
            for key in _TERM_LISTS:
                for i, s in enumerate(terms.get(key) or []):
                    _add(out, f"/terms/{key}/{i}", "ui", s)
            for key, s in (terms.get("messages") or {}).items():
                _add(out, f"/terms/messages/{escape(key)}", "system", s)
        elif name == "CommonEvents.json":
            for i, ev in enumerate(data):
                if isinstance(ev, dict):
                    out += _commands(ev.get("list") or [], f"/{i}/list")
        elif name == "Troops.json":
            for i, troop in enumerate(data):
                if isinstance(troop, dict):
                    for j, page in enumerate(troop.get("pages") or []):
                        out += _commands(page.get("list") or [], f"/{i}/pages/{j}/list")
        elif _MAP.match(name):
            _add(out, "/displayName", "name", data.get("displayName"))
            for i, ev in enumerate(data.get("events") or []):
                if isinstance(ev, dict):
                    for j, page in enumerate(ev.get("pages") or []):
                        out += _commands(page.get("list") or [], f"/events/{i}/pages/{j}/list")
        return out

    def render(self, rel: str, text: str, repl: dict[str, str]) -> str:
        return _render(text, repl)

    def wrap_rule(self, kind: str) -> bool:
        # Message windows never wrap; a newline inside a 401 line breaks it and overflow pages automatically.
        return kind == "dialogue"


def _render(text: str, repl: dict[str, str]) -> str:
    flat, nested = {}, {}
    for loc, s in repl.items():
        outer, sep, rest = loc.partition(_NESTED)
        if sep:
            nested.setdefault(outer, {})[rest] = s
        else:
            flat[loc] = s
    if nested:
        spans = string_spans(text)
        for outer, sub in nested.items():
            start, end = spans[outer]
            flat[outer] = _render(json.loads(text[start:end]), sub)
    return replace_strings(text, flat)


def _plugin_args(out: list, loc: str, key: str, value) -> None:
    """357 arg string; recurse into JSON-encoded struct/array args. Text-like keys are translated, the rest uncertain."""
    if not isinstance(value, str):
        return
    if value.lstrip()[:1] in ("[", "{"):
        try:
            spans = string_spans(value)
        except ValueError:
            spans = None
        if spans is not None:
            for ptr, (start, end) in spans.items():
                last = ptr.rsplit("/", 1)[-1]                   # array items keep the parent arg's key
                _plugin_args(out, f"{loc}{_NESTED}{ptr}", key if last.isdigit() or not last else last,
                             json.loads(value[start:end]))
            return
    text_like = _TEXT_ARG.search(key) and not _FILE_ARG.search(key)
    _add(out, loc, "system" if text_like else "other", value)


def _add(out: list, loc: str, kind: str, value, speaker=None, group=None) -> None:
    if isinstance(value, str):
        out.append(Segment(loc, kind, value, speaker, group))


def _commands(cmds: list, base: str) -> list[Segment]:
    out: list[Segment] = []
    group = speaker = None
    for i, cmd in enumerate(cmds):
        if not isinstance(cmd, dict):
            continue
        code, params, p = cmd.get("code"), cmd.get("parameters") or [], f"{base}/{i}/parameters"
        if code not in (401, 405):
            group = speaker = None
        if code == 101:                                  # Show Text; params[4] = speaker name (MZ)
            speaker = params[4] if len(params) > 4 and isinstance(params[4], str) and params[4] else None
            group = f"{base}/{i}"
            _add(out, f"{p}/4", "name", speaker)
        elif code == 105:                                 # Show Scrolling Text
            speaker, group = None, f"{base}/{i}"
        elif code in (401, 405) and params:               # message / scrolling lines, merged per group
            _add(out, f"{p}/0", "dialogue", params[0], speaker, group or f"{base}/{i}")
        elif code == 102 and params and isinstance(params[0], list):
            for k, choice in enumerate(params[0]):
                _add(out, f"{p}/0/{k}", "choice", choice)
        elif code == 402 and len(params) > 1:
            _add(out, f"{p}/1", "choice", params[1])
        elif code in (320, 324) and len(params) > 1:     # change name / nickname
            _add(out, f"{p}/1", "name", params[1])
        elif code == 325 and len(params) > 1:            # change profile
            _add(out, f"{p}/1", "system", params[1])
        elif code == 357 and len(params) > 3 and isinstance(params[3], dict):
            # Plugin command args may be text, ids or file names; only text-like keys are translated by default.
            # 355/655 (script) and 657 (editor-only display of 357 args) are never extracted.
            for key, value in params[3].items():
                _plugin_args(out, f"{p}/3/{escape(key)}", key, value)
    return out
