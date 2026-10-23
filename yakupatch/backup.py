"""Backup and restore inside the game folder (PROJECT_PLAN §9).
Entries: whole files (default), `head` (only the first N bytes changed, e.g. an asar header) and `created`
(files we added, deleted on restore)."""
import json
import os
import shutil
import time
from pathlib import Path

from . import __version__
from .fsutil import atomic_write, sha256
from .safety import read_bytes, safe_path

BACKUP_DIR = "_yakupatch_backup"


def read_head(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def write_head(path: Path, data: bytes) -> None:
    """In-place overwrite of the first bytes; the copy in the backup folder is what makes this recoverable."""
    with open(path, "r+b") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def remove_created(path: Path) -> None:
    """Delete a file we added, and the .rpyc Ren'Py compiles next to an added .rpy (it would still load alone)."""
    path.unlink(missing_ok=True)
    if path.suffix == ".rpy":
        path.with_name(path.name + "c").unlink(missing_ok=True)


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
        if e and not e.get("created") and sha256(current) == e["patched_sha"] and e["patched_sha"] != e["orig_sha"]:
            return read_bytes(self.dir, f"files/{rel}")
        return current

    def original_head(self, rel: str) -> bytes | None:
        """For `head` entries while patched: the original first bytes; otherwise None (read the file itself)."""
        e = self.files.get(rel)
        if e and e.get("head"):
            current = read_head(safe_path(self.game, rel), e["head"])
            if sha256(current) == e["patched_sha"] and e["patched_sha"] != e["orig_sha"]:
                return read_bytes(self.dir, f"files/{rel}")
        return None

    def is_created(self, rel: str) -> bool:
        return bool(self.files.get(rel, {}).get("created"))

    def ensure(self, rel: str, original: bytes, head: bool = False) -> None:
        """Copy the original once; a game update (different original) replaces the stale copy."""
        e = self.files.get(rel)
        if e and e["orig_sha"] == sha256(original):
            return
        atomic_write(safe_path(self.dir, f"files/{rel}"), original)
        self.files[rel] = {"orig_sha": sha256(original), "size": len(original), "mtime": time.time(),
                           "patched_sha": sha256(original), "tool_version": __version__, **({"head": len(original)}
                                                                                             if head else {})}

    def created(self, rel: str, data: bytes) -> None:
        self.files[rel] = {"created": True, "orig_sha": None, "patched_sha": sha256(data), "size": len(data),
                           "mtime": time.time(), "tool_version": __version__}

    def drop(self, rel: str) -> None:
        self.files.pop(rel, None)

    def set_patched(self, rel: str, data: bytes) -> None:
        self.files[rel]["patched_sha"] = sha256(data)

    def save(self) -> None:
        atomic_write(self.dir / "manifest.json",
                     json.dumps({"files": self.files}, ensure_ascii=False, indent=1).encode("utf-8"))

    def restore(self) -> list[tuple[str, str]]:
        results, keep = [], {}
        for rel, e in self.files.items():
            path = safe_path(self.game, rel)
            if e.get("created"):
                current = sha256(path.read_bytes()) if path.exists() else None
                if current is None:
                    results.append((rel, "original"))
                elif current == e["patched_sha"]:
                    remove_created(path)
                    _prune(path.parent, self.game)
                    results.append((rel, "removed"))
                else:
                    keep[rel] = {**e, "stale": True}
                    results.append((rel, "stale"))
                continue
            n = e.get("head")
            if path.exists():
                current = sha256(read_head(path, n) if n else path.read_bytes())
            else:
                current = None
            if current == e["orig_sha"]:
                results.append((rel, "original"))
            elif current == e["patched_sha"]:
                data = read_bytes(self.dir, f"files/{rel}")
                if sha256(data) != e["orig_sha"]:
                    raise RuntimeError(f"備份檔已損毀：{rel}")
                write_head(path, data) if n else atomic_write(path, data)
                if sha256(read_head(path, n) if n else path.read_bytes()) != e["orig_sha"]:
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


def _prune(folder: Path, root: Path) -> None:
    """Remove folders our created files left empty, never the game folder itself."""
    root = root.resolve()
    folder = folder.resolve()
    while folder != root and root in folder.parents and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
