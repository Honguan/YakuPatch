"""Writeback pipeline W1–W7 (PROJECT_PLAN §7). All files succeed or everything is rolled back."""
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .backup import Backup
from .encoding import decode, encode
from .engines import EnginePlugin
from .exchange import Store
from .fsutil import atomic_write, sha256
from .safety import read_bytes, safe_path


class GameChanged(Exception):
    pass


@dataclass
class FilePlan:
    rel: str
    original: bytes
    new: bytes
    replaced: int


@dataclass
class ApplyPlan:
    files: list[FilePlan] = field(default_factory=list)      # only files whose bytes change
    warnings: list[str] = field(default_factory=list)


def plan(game_dir: Path, store: Store, plugin: EnginePlugin) -> ApplyPlan:
    backup, result = Backup(game_dir), ApplyPlan()
    infos = store.manifest["files"]
    originals = {}
    for rel, info in infos.items():                                      # W1
        current = read_bytes(game_dir, rel)
        e = backup.files.get(rel)
        if sha256(current) == info["sha256"]:
            originals[rel] = current
        elif e and sha256(current) == e["patched_sha"] and e["orig_sha"] == info["sha256"]:
            originals[rel] = read_bytes(backup.dir, f"files/{rel}")
        else:
            raise GameChanged(f"遊戲檔案已變動，請先重新提取：{rel}")
    repl: dict[str, dict[str, str]] = {rel: {} for rel in infos}
    limits = wrap_limits(store, plugin)
    codes = re.compile("|".join(plugin.control_codes)) if plugin.control_codes else None
    for rec in store.records.values():                                   # W2
        if rec["status"] not in ("translated", "reviewed") or not rec.get("dst"):
            continue
        for ref in rec["refs"]:
            text = rec.get("overrides", {}).get(f"{ref['file']}#{ref['locs'][0]}", rec["dst"])
            parts = text.split("\n") if len(ref["locs"]) > 1 else [text]
            if len(parts) != len(ref["locs"]):
                result.warnings.append(f"{rec['id']} 行數與原文不同，保留原文")
                continue
            try:                                                         # W4 EncodeGuard
                text.encode(infos[ref["file"]]["encoding"])
            except UnicodeEncodeError:
                result.warnings.append(f"{rec['id']} 無法以 {infos[ref['file']]['encoding']} 編碼，保留原文")
                continue
            if rec["kind"] in limits:                                    # W3
                parts = [wrap(p, limits[rec["kind"]], codes) for p in parts]
            repl[ref["file"]].update(zip(ref["locs"], parts))
    for rel, info in infos.items():
        text, _ = decode(originals[rel], plugin.default_encoding)
        new_text = plugin.render(rel, text, repl[rel])                   # W5
        _verify(plugin, rel, text, new_text, repl[rel])                  # W6
        new = encode(new_text, info)
        if new != read_bytes(game_dir, rel):
            result.files.append(FilePlan(rel, originals[rel], new, len(repl[rel])))
    return result


def width(text: str, codes=None) -> float:
    """Display width: full-width and ambiguous characters 1, half-width 0.5; control codes take no room."""
    text = codes.sub("", text) if codes else text
    return sum(1 if unicodedata.east_asian_width(c) in "FWA" else 0.5 for c in text)


def wrap_limits(store: Store, plugin: EnginePlugin) -> dict[str, float]:
    """The game's own line width per kind (99th percentile of source lines), for kinds the engine does not wrap."""
    codes = re.compile("|".join(plugin.control_codes)) if plugin.control_codes else None
    lines: dict[str, list[float]] = {}
    for rec in store.records.values():
        if plugin.wrap_rule(rec["kind"]):
            lines.setdefault(rec["kind"], []).extend(width(s, codes) for s in rec["src"].split("\n"))
    out = {}
    for kind, ws in lines.items():
        if len(ws) >= 20:                     # too few lines to learn the window width from
            ws.sort()
            out[kind] = max(8.0, ws[int(len(ws) * 0.99) - 1])
    return out


_CLOSE = set("、。，．・：；？！ー…‥」』）】〉》〕］｝〟’”!?,.)]}")


def wrap(line: str, limit: float, codes=None) -> str:
    """W3: insert "\\n" so no line is wider than `limit`; never inside a control code; closing punctuation hangs."""
    if width(line, codes) <= limit:
        return line
    tokens, pos = [], 0
    for m in (codes.finditer(line) if codes else []):
        tokens += list(line[pos:m.start()]) + [m.group(0)]
        pos = m.end()
    tokens += list(line[pos:])
    out, cur = [], 0.0
    for tok in tokens:
        w = 0 if codes and codes.fullmatch(tok) else width(tok)
        if w and cur + w > limit and cur and tok not in _CLOSE:
            out.append("\n")
            cur = 0.0
        out.append(tok)
        cur += w
    return "".join(out)


def _verify(plugin: EnginePlugin, rel: str, text: str, new_text: str, repl: dict[str, str]) -> None:
    before, after = plugin.parse(rel, text), {s.loc: s.text for s in plugin.parse(rel, new_text)}
    if len(before) != len(after) or any(after.get(loc) != s for loc, s in repl.items()):
        raise RuntimeError(f"寫回驗證失敗，未修改任何檔案：{rel}")


def commit(game_dir: Path, apply: ApplyPlan) -> None:                    # W7
    need = sum(len(f.new) + len(f.original) for f in apply.files)
    if shutil.disk_usage(game_dir).free < need * 2:
        raise OSError("磁碟空間不足")
    backup, written = Backup(game_dir), []
    try:
        for f in apply.files:
            path = safe_path(game_dir, f.rel)
            before = path.read_bytes()
            backup.ensure(f.rel, f.original)
            atomic_write(path, f.new)
            written.append((path, before))
            backup.set_patched(f.rel, f.new)
        backup.save()
    except BaseException:
        for path, before in reversed(written):
            atomic_write(path, before)
        raise
