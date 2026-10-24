"""Extraction pipeline E1–E11 + L1 (PROJECT_PLAN §5). Reads the game, writes only the exchange files."""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import dicts, engines, langdetect, vfs
from .backup import Backup
from .engines import EnginePlugin, Segment
from .exchange import Store
from .fsutil import atomic_write, sha256

FORMAT = "yakupatch-strings/1"
_PATHLIKE = re.compile(r"^[\w\-./\\]+\.(png|jpe?g|ogg|m4a|wav|json|js|txt|webm|efkefc)$", re.I)


class LanguageGateStop(Exception):
    def __init__(self, probe: dict, packs: list):
        self.probe, self.packs = probe, packs
        super().__init__(probe["primary"])


@dataclass
class Unit:
    file: str
    locs: list[str]
    kind: str
    lines: list[str]
    speaker: str | None
    status: str = "pending"
    flags: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def projects_home() -> Path:
    return Path(os.environ.get("YAKUPATCH_HOME") or Path.home() / ".yakupatch") / "projects"


def project_dir(game_dir: Path, plugin: EnginePlugin, home: Path | None = None) -> Path:
    """G1: name + fingerprint of engine and original title, so moving the folder keeps the project."""
    backup = Backup(game_dir)
    title = plugin.title(game_dir, lambda rel: vfs.read_original(game_dir, rel, backup))
    fp = hashlib.sha256(f"{plugin.name}\0{title}".encode("utf-8")).hexdigest()[:8]
    return (home or projects_home()) / f"{game_dir.name}-{fp}"


def read_game(game_dir: Path, plugin: EnginePlugin) -> tuple[dict, list[tuple[str, Segment]]]:
    """E2–E5 without writing anything; also used by the GUI library's language label."""
    backup = Backup(game_dir)
    files, segments = {}, []
    for rel in plugin.locate(game_dir):                                   # E3
        raw = vfs.read_original(game_dir, rel, backup)                    # E2
        text, info = plugin.decode(rel, raw, game_dir)                              # E4
        files[rel] = {"sha256": sha256(raw), **info}
        try:
            segments += [(rel, s) for s in plugin.parse(rel, text)]      # E5
        except (ValueError, json.JSONDecodeError) as ex:
            raise ValueError(f"無法解析 {rel}（可能已加密，不支援）：{ex}") from None
    return files, segments


def probe(game_dir: Path, plugin: EnginePlugin, segments: list[tuple[str, Segment]] | None = None) -> tuple[dict, list]:
    if segments is None:
        segments = read_game(game_dir, plugin)[1]
    result = language_gate([s for _, s in segments])                     # L1
    packs = plugin.language_packs(game_dir)
    result["packs"] = [p.path for p in packs]
    return result, packs


def extract(game_dir: Path, plugin: EnginePlugin, store: Store, force: bool = False) -> dict:
    files, segments = read_game(game_dir, plugin)
    lang, packs = probe(game_dir, plugin, segments)
    if not force and (lang["primary"].startswith("zh") or packs):
        raise LanguageGateStop(lang, packs)
    units = [u for rel in files for u in segment(rel, [s for r, s in segments if r == rel])]  # E6
    for u in units:
        u.status, u.flags = classify(u)                                  # E7
    records = dedupe(units)                                               # E8 speaker is set by the plugin; E9
    seed_glossary(store.dir, records)                                     # E10
    manifest = {"format": FORMAT, "game_dir": str(game_dir), "engine": plugin.name,
                "plugin_version": plugin.version, "source_lang": "ja", "target_lang": "",
                "language_probe": lang, "files": files}
    store.replace(merge(store.records, records), {**store.manifest, **manifest})   # E11
    return lang


def language_gate(segments: list[Segment]) -> dict:
    texts = [s.text for s in segments if s.kind == "dialogue"]
    if sum(map(len, texts)) < 2000:
        texts += [s.text for s in segments if s.kind != "dialogue"]
    return langdetect.probe(texts)


def segment(rel: str, segs: list[Segment]) -> list[Unit]:
    """E6: lines of one message become one unit; writeback splits them back by line (§7 W2)."""
    units, groups = [], {}
    for s in segs:
        u = groups.get(s.group) if s.group else None
        if u:
            u.locs.append(s.loc)
            u.lines.append(s.text)
            continue
        u = Unit(rel, [s.loc], s.kind, [s.text], s.speaker)
        units.append(u)
        if s.group:
            groups[s.group] = u
    return units


def classify(u: Unit) -> tuple[str, list[str]]:
    """E7 (§5): skip what must not be translated; kind=other is uncertain (off by default)."""
    core = u.text.strip()
    if not (langdetect.KANA.search(core) or langdetect.HAN.search(core)):
        return "skip", []
    if _PATHLIKE.match(core):
        return "skip", []
    if langdetect.sentence_is_chinese(core):
        return "skip", ["already_zh"]
    if u.kind == "other":
        return "pending", ["uncertain"]
    return "pending", []


def dedupe(units: list[Unit]) -> dict[str, dict]:
    """E9: one record per (kind, text); every occurrence goes into refs."""
    records: dict[str, dict] = {}
    for u in units:
        rid = _id(records, u.kind, u.text)
        rec = records.get(rid)
        if rec is None:
            rec = records[rid] = {"id": rid, "kind": u.kind, "speaker": u.speaker, "src": u.text, "dst": "",
                                  "status": u.status, "by": "", "refs": [], "flags": u.flags, "note": ""}
        rec["refs"].append({"file": u.file, "locs": u.locs})
        rec["speaker"] = rec["speaker"] or u.speaker
    return records


def _id(records: dict, kind: str, text: str) -> str:
    digest = hashlib.sha1(f"{kind}\0{text}".encode("utf-8")).hexdigest()
    for n in range(12, 41, 4):
        rid = digest[:n]
        rec = records.get(rid)
        if rec is None or (rec["kind"], rec["src"]) == (kind, text):
            return rid
    raise RuntimeError("id collision")


def seed_glossary(project: Path, records: dict[str, dict]) -> None:
    """E10: names and speakers become glossary candidates; existing rows are never overwritten.
    Local dictionaries (§6.10) add readings and existing Chinese names as unconfirmed `dict:` suggestions."""
    path = project / "glossary.tsv"
    rows = {}
    if path.exists():
        for line in path.read_text("utf-8").splitlines()[1:]:
            cols = line.split("\t")
            rows[cols[0]] = line
    for rec in records.values():
        for term in (rec["src"] if rec["kind"] == "name" and rec["status"] == "pending" else None, rec["speaker"]):
            if term and "\t" not in term and "\n" not in term and term not in rows:
                rows[term] = "\t".join((term, *(dicts.suggest(term) or ("", "", "seed"))))
    body = "src\tdst\tnote\tsource\n" + "".join(r + "\n" for r in rows.values())
    atomic_write(path, body.encode("utf-8"))


def merge(old: dict[str, dict], new: dict[str, dict]) -> dict[str, dict]:
    """§4.5: keep translations, refresh refs, never touch reviewed; vanished ids become orphan."""
    out = {}
    for rid, rec in new.items():
        prev = old.get(rid)
        if prev is None:
            out[rid] = rec
            continue
        keep = dict(prev, refs=rec["refs"], kind=rec["kind"], speaker=rec["speaker"])
        if prev["status"] == "orphan":
            keep["status"] = "translated" if prev.get("dst") else rec["status"]
        elif not prev.get("dst") and prev["status"] in ("pending", "skip"):
            keep["status"], keep["flags"] = rec["status"], rec["flags"]
        out[rid] = keep
    for rid, prev in old.items():
        if rid not in out:
            out[rid] = dict(prev, status="orphan")
    return out
