"""Backup and restore inside the game folder (PROJECT_PLAN §9)."""
import json
import shutil
import time
from pathlib import Path

from . import __version__
from .fsutil import atomic_write, sha256
from .safety import read_bytes, safe_path

BACKUP_DIR = "_yakupatch_backup"


class Backup:
    def __init__(self, game_dir: Path):
        self.game = Path(game_dir)
        self.dir = self.game / BACKUP_DIR
        path = self.dir / "manifest.json"
        self.files: dict[str, dict] = json.loads(path.read_text("utf-8"))["files"] if path.exists() else {}

    def original_bytes(self, rel: str) -> bytes:
        """The unpatched file: the backup copy while our patch is in place, else the current file."""
        current = read_bytes(self.game, rel)
        e = self.files.get(rel)
        if e and sha256(current) == e["patched_sha"] and e["patched_sha"] != e["orig_sha"]:
            return read_bytes(self.dir, f"files/{rel}")
        return current

    def ensure(self, rel: str, original: bytes) -> None:
        """Copy the original once; a game update (different original) replaces the stale copy."""
        e = self.files.get(rel)
        if e and e["orig_sha"] == sha256(original):
            return
        atomic_write(safe_path(self.dir, f"files/{rel}"), original)
        self.files[rel] = {"orig_sha": sha256(original), "size": len(original), "mtime": time.time(),
                           "patched_sha": sha256(original), "tool_version": __version__}

    def set_patched(self, rel: str, data: bytes) -> None:
        self.files[rel]["patched_sha"] = sha256(data)

    def save(self) -> None:
        # shortcut: no "created files" list yet; needed once an engine adds new files (patch.xp3, I4).
        atomic_write(self.dir / "manifest.json",
                     json.dumps({"files": self.files}, ensure_ascii=False, indent=1).encode("utf-8"))

    def restore(self) -> list[tuple[str, str]]:
        results, keep = [], {}
        for rel, e in self.files.items():
            path = safe_path(self.game, rel)
            current = sha256(path.read_bytes()) if path.exists() else None
            if current == e["orig_sha"]:
                results.append((rel, "original"))
            elif current == e["patched_sha"]:
                data = read_bytes(self.dir, f"files/{rel}")
                if sha256(data) != e["orig_sha"]:
                    raise RuntimeError(f"備份檔已損毀：{rel}")
                atomic_write(path, data)
                if sha256(path.read_bytes()) != e["orig_sha"]:
                    raise RuntimeError(f"還原後雜湊不符：{rel}")
                results.append((rel, "restored"))
            else:
                # Updated by the game or another tool: restoring would downgrade it (§9).
                keep[rel] = {**e, "stale": True}
                results.append((rel, "stale"))
        if keep:
            self.files = keep
            self.save()
        elif self.dir.exists():
            shutil.rmtree(self.dir)
            self.files = {}
        return results
