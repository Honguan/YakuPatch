"""E2 Unpacker / W7 for packed engines (PROJECT_PLAN §3.5, §3.6). A member path is "<container>!<inner>",
e.g. "resources/app.asar!data/scenario/first.ks". Containers are read-only except Asar.patch, which never moves
data: changed members become `unpacked` entries in a same-length header, their bytes go to <name>.unpacked/."""
import codecs
import hashlib
import io
import json
import pickle
import struct
import zlib
from pathlib import Path

from .backup import Backup
from .safety import MAX_FILE_BYTES, UnsafePath, read_bytes, safe_path

SEP = "!"
BLOCK = 4 * 1024 * 1024                  # asar integrity block size


def split(rel: str) -> tuple[str, str | None]:
    outer, sep, inner = rel.partition(SEP)
    return outer, (inner if sep else None)


def _inner_ok(inner: str) -> str:
    parts = inner.split("/")
    if not inner or inner.startswith("/") or any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
        raise UnsafePath(f"封包內路徑不安全：{inner!r}")
    return inner


class Asar:
    """Electron asar: [u32 4][u32 header_size] then a pickle [u32 payload][u32 json_len][json][pad]; data follows."""

    def __init__(self, path: Path, head: bytes | None = None):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            prefix = f.read(8)
            if len(prefix) < 8:
                raise ValueError("不是有效的 asar 封包")
            size, = struct.unpack("<4xI", prefix)
            if size > 256 * 1024 * 1024:
                raise ValueError("asar 標頭過大")
            stored = f.read(size)
        self.head = head if head is not None else prefix + stored     # the original header when we have patched it
        if len(self.head) != 8 + size:
            raise ValueError("asar 標頭與備份長度不符")
        self.json_len, = struct.unpack("<I", self.head[12:16])
        self.tree = json.loads(self.head[16:16 + self.json_len].decode("utf-8"))
        self.files: dict[str, dict] = {}
        self._walk(self.tree, "")

    def _walk(self, node: dict, prefix: str) -> None:
        for name, entry in (node.get("files") or {}).items():
            inner = _inner_ok(prefix + name)
            if "files" in entry:
                self._walk(entry, inner + "/")
            elif "link" not in entry:
                self.files[inner] = entry

    def read(self, inner: str) -> bytes:
        entry = self.files[inner]
        if int(entry["size"]) > MAX_FILE_BYTES:
            raise UnsafePath(f"檔案超過大小上限：{inner}")
        if entry.get("unpacked"):
            return read_bytes(self.path.with_name(self.path.name + ".unpacked"), inner)
        with open(self.path, "rb") as f:
            f.seek(len(self.head) + int(entry["offset"]))
            data = f.read(int(entry["size"]))
        if len(data) != int(entry["size"]):
            raise ValueError(f"asar 內容不完整：{inner}")
        return data

    def patch(self, changes: dict[str, bytes]) -> tuple[bytes, dict[str, bytes]]:
        """New header (same length as the original) + files for <name>.unpacked/, relative to the asar's folder."""
        if not changes:
            return self.head, {}
        tree =json.loads(self.head[16:16 + self.json_len].decode("utf-8"))
        out = {}
        for inner, data in changes.items():
            node = tree
            for part in inner.split("/")[:-1]:
                node = node["files"][part]
            entry = node["files"][inner.rsplit("/", 1)[-1]]
            entry.pop("offset", None)
            entry.update(size=len(data), unpacked=True)
            if "integrity" in entry:
                entry["integrity"] = _integrity(data)
            out[f"{self.path.name}.unpacked/{inner}"] = data
        text = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(text) > self.json_len:                               # make room: drop recomputed hashes we added
            for inner in changes:
                node = tree
                for part in inner.split("/"):
                    node = node["files"][part]
                node.pop("integrity", None)
            text = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(text) > self.json_len:
            raise ValueError("asar 標頭空間不足，無法改成外部檔案（不支援重建封包）")
        # JSON allows trailing whitespace, so the header keeps its length and no data offset moves.
        head = self.head[:16] + text + b" " * (self.json_len - len(text)) + self.head[16 + self.json_len:]
        return head, out


class _SafeUnpickler(pickle.Unpickler):
    """Plain data only: an RPA index is a dict of lists of tuples; bytes may come as _codecs.encode / bytes()."""

    def find_class(self, module, name):
        if (module, name) == ("_codecs", "encode"):
            return codecs.encode
        if module in ("builtins", "__builtin__") and name == "bytes":     # protocol 2 writes b"" as bytes()
            return bytes
        raise pickle.UnpicklingError(f"封包索引含不允許的物件：{module}.{name}")


class Rpa:
    """Ren'Py archive (read-only): "RPA-3.0 <offset hex> <key hex>\\n" or "RPA-2.0 <offset hex>\\n", then a
    zlib pickle index {name: [(offset, length, prefix)]}; RPA-3.0 XORs offset and length with the key."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            line = f.readline(200).split()
            if not line or line[0] not in (b"RPA-3.0", b"RPA-2.0"):
                raise ValueError("不支援的 RPA 封包版本")
            offset = int(line[1], 16)
            key = 0
            if line[0] == b"RPA-3.0":
                for part in line[2:]:
                    key ^= int(part, 16)
            f.seek(offset)
            raw = zlib.decompressobj().decompress(f.read(), MAX_FILE_BYTES)
        index = _SafeUnpickler(io.BytesIO(raw), encoding="bytes").load()
        self.files: dict[str, tuple[int, int, bytes]] = {}
        for name, parts in index.items():
            name = name.decode("utf-8") if isinstance(name, bytes) else name
            start, length, *prefix = parts[0]
            prefix = prefix[0] if prefix else b""
            prefix = prefix.encode("latin-1") if isinstance(prefix, str) else prefix
            self.files[_inner_ok(name)] = (start ^ key, length ^ key, prefix)

    def read(self, inner: str) -> bytes:
        start, length, prefix = self.files[inner]
        if length > MAX_FILE_BYTES:
            raise UnsafePath(f"檔案超過大小上限：{inner}")
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read(length - len(prefix))
        if len(data) != length - len(prefix):
            raise ValueError(f"RPA 內容不完整：{inner}")
        return prefix + data


def _integrity(data: bytes) -> dict:
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(), "blockSize": BLOCK,
            "blocks": [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, max(len(data), 1), BLOCK)]}


def open_container(game_dir: Path, outer: str, backup: Backup | None = None):
    path = safe_path(game_dir, outer)
    if outer.lower().endswith(".rpa"):
        return Rpa(path)
    if not outer.lower().endswith(".asar"):
        raise ValueError(f"不支援的封包格式：{outer}")
    head = (backup or Backup(game_dir)).original_head(outer)
    return Asar(path, head)


def read_original(game_dir: Path, rel: str, backup: Backup | None = None) -> bytes:
    """The unpatched bytes of a loose file or a container member."""
    backup = backup or Backup(game_dir)
    outer, inner = split(rel)
    if inner is None:
        return backup.original_bytes(rel)
    return open_container(game_dir, outer, backup).read(inner)


def read_current(game_dir: Path, rel: str) -> bytes:
    outer, inner = split(rel)
    if inner is None:
        return read_bytes(game_dir, rel)
    if outer.lower().endswith(".rpa"):
        return Rpa(safe_path(game_dir, outer)).read(inner)
    return Asar(safe_path(game_dir, outer)).read(inner)


def physical(game_dir: Path, news: dict[str, bytes], originals: dict[str, bytes],
             backup: Backup | None = None) -> dict[str, tuple[bytes, bool]]:
    """Map logical results to file writes: {path: (bytes, is_header)}. A container is rebuilt from its original
    header with every member that differs from the original, so earlier patches are kept or reverted together."""
    backup = backup or Backup(game_dir)
    writes, members = {}, {}
    for rel, data in news.items():
        outer, inner = split(rel)
        if inner is None:
            writes[rel] = (data, False)
        else:
            members.setdefault(outer, {})
            if data != originals[rel]:
                members[outer][inner] = data
    for outer, changes in members.items():
        container = open_container(game_dir, outer, backup)
        head, files = container.patch(changes)
        writes[outer] = (head, True)
        base = outer.rsplit("/", 1)[0] + "/" if "/" in outer else ""
        for rel, data in files.items():
            writes[base + rel] = (data, False)
    return writes
