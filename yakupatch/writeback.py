"""Writeback pipeline W1–W7 (PROJECT_PLAN §7). All files succeed or everything is rolled back."""
import re
import shutil
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import vfs
from .backup import Backup, read_head, remove_created, write_head
from .encoding import RAW, encode
from .engines import EnginePlugin
from .exchange import Store
from .fsutil import atomic_write, sha256
from .safety import read_bytes, safe_path


class GameChanged(Exception):
    pass


class _Texts(Mapping):
    """Binary-output engines only need file names; decode text when an engine actually asks for it."""
    def __init__(self, originals, plugin, game_dir):
        self.originals, self.plugin, self.game_dir = originals, plugin, game_dir
        self.cache = {}

    def __iter__(self):
        return iter(self.originals)

    def __len__(self):
        return len(self.originals)

    def __getitem__(self, rel):
        if rel not in self.cache:
            self.cache[rel] = self.plugin.decode(rel, self.originals[rel], self.game_dir)[0]
        return self.cache[rel]


@dataclass
class FilePlan:
    rel: str
    original: bytes
    new: bytes
    replaced: int


@dataclass
class ApplyPlan:
    files: list[FilePlan] = field(default_factory=list)      # only files whose bytes change (logical paths)
    warnings: list[str] = field(default_factory=list)
    # Physical writes for W7: path -> (bytes, is_header); bytes None deletes a file we created earlier.
    writes: dict[str, tuple[bytes | None, bool]] = field(default_factory=dict)


def plan(game_dir: Path, store: Store, plugin: EnginePlugin) -> ApplyPlan:
    backup, result = Backup(game_dir), ApplyPlan()
    infos = store.manifest["files"]
    originals = {}
    for rel, info in infos.items():                                      # W1
        try:
            originals[rel] = vfs.read_original(game_dir, rel, backup)
        except (OSError, KeyError, ValueError):
            originals[rel] = None
        if originals[rel] is None or sha256(originals[rel]) != info["sha256"]:
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
                text.encode(infos[ref["file"]]["encoding"], RAW)
            except UnicodeEncodeError:
                result.warnings.append(f"{rec['id']} 無法以 {infos[ref['file']]['encoding']} 編碼，保留原文")
                continue
            if rec["kind"] in limits:                                    # W3
                parts = [wrap(p, limits[rec["kind"]], codes) for p in parts]
            parts = [plugin.fit(rec["kind"], p) for p in parts]
            repl[ref["file"]].update(zip(ref["locs"], parts))
    texts = _Texts(originals, plugin, game_dir)
    outputs = plugin.outputs(texts, {rel: r for rel, r in repl.items() if r}, game_dir)
    if outputs is not None:                       # new files beside the game; the originals stay untouched
        for rel in sorted(set(outputs) | {r for r in backup.files if backup.is_created(r)}):
            data, n = outputs.get(rel, (None, 0))
            current = read_bytes(game_dir, rel) if safe_path(game_dir, rel).exists() else None
            if data != current:
                result.files.append(FilePlan(rel, current or b"", data or b"", n))
                result.writes[rel] = (data, False)
        return result
    news = {}
    for rel, info in infos.items():
        text = texts[rel]
        new_text = plugin.render(rel, text, repl[rel])                   # W5
        _verify(plugin, rel, text, new_text, repl[rel])                  # W6
        news[rel] = encode(new_text, info)
        if news[rel] != vfs.read_current(game_dir, rel):
            result.files.append(FilePlan(rel, originals[rel], news[rel], len(repl[rel])))
    changed = {f.rel for f in result.files}
    outers = {vfs.split(rel)[0] for rel in changed if vfs.split(rel)[1] is not None}
    result.writes = vfs.physical(game_dir, {rel: data for rel, data in news.items()
                                            if rel in changed or vfs.split(rel)[0] in outers}, originals, backup)
    for outer in outers:                       # members reverted to the original: drop their unpacked copies
        folder = (outer.rsplit("/", 1)[0] + "/" if "/" in outer else "") + outer.rsplit("/", 1)[-1] + ".unpacked/"
        for rel in backup.files:
            if backup.is_created(rel) and rel.startswith(folder) and rel not in result.writes:
                result.writes[rel] = (None, False)
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
    need = sum(len(data) for data, _ in apply.writes.values() if data)
    if shutil.disk_usage(game_dir).free < need * 2:
        raise OSError("磁碟空間不足")
    backup, done = Backup(game_dir), []
    try:
        for rel, (data, head) in apply.writes.items():
            path = safe_path(game_dir, rel)
            if head:
                before = read_head(path, len(data))
                backup.ensure(rel, backup.original_head(rel) or before, head=True)
                done.append((path, before, True))
                write_head(path, data)
                backup.set_patched(rel, data)
                continue
            before = path.read_bytes() if path.exists() else None
            if data is None:                                             # a file we added, no longer needed
                done.append((path, before, False))
                remove_created(path)
                backup.drop(rel)
                continue
            if before is None or backup.is_created(rel):
                backup.created(rel, data)
            else:
                backup.ensure(rel, backup.original_bytes(rel))
            done.append((path, before, False))
            atomic_write(path, data)
            backup.set_patched(rel, data)
        backup.save()
    except BaseException:
        for path, before, head in reversed(done):
            if head:
                write_head(path, before)
            elif before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before)
        raise
