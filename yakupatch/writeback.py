"""Writeback pipeline W1–W7 (PROJECT_PLAN §7). All files succeed or everything is rolled back."""
import shutil
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
            # shortcut: no W3 LineWrapper yet; message lines are kept as translated (I3).
            repl[ref["file"]].update(zip(ref["locs"], parts))
    for rel, info in infos.items():
        text, _ = decode(originals[rel], plugin.default_encoding)
        new_text = plugin.render(rel, text, repl[rel])                   # W5
        _verify(plugin, rel, text, new_text, repl[rel])                  # W6
        new = encode(new_text, info)
        if new != read_bytes(game_dir, rel):
            result.files.append(FilePlan(rel, originals[rel], new, len(repl[rel])))
    return result


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
