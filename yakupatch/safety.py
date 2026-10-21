"""P6: game files are untrusted. Every path must stay inside its root (PROJECT_PLAN §3.6)."""
from pathlib import Path

MAX_FILE_BYTES = 512 * 1024 * 1024


class UnsafePath(ValueError):
    pass


def safe_path(root, rel) -> Path:
    root = Path(root).resolve()
    p = (root / str(rel).replace("\\", "/")).resolve()
    if p != root and root not in p.parents:
        raise UnsafePath(f"路徑超出允許範圍：{rel}")
    return p


def read_bytes(root, rel) -> bytes:
    p = safe_path(root, rel)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise UnsafePath(f"檔案超過大小上限：{rel}")
    return p.read_bytes()
