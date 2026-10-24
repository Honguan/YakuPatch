"""E2 Unpacker / W7 for packed engines (PROJECT_PLAN §3.5, §3.6). A member path is "<container>!<inner>",
e.g. "resources/app.asar!data/scenario/first.ks". Containers are read-only except Asar.patch, which never moves
data: changed members become `unpacked` entries in a same-length header, their bytes go to <name>.unpacked/."""
import codecs
import hashlib
import io
import json
import pickle
import re
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


XP3_MAGIC = b"XP3\r\n \n\x1a\x8b\x67\x01"


class Xp3:
    """KiriKiri archive (read; write() builds a new one): magic, u64 index offset, then a chain of index blocks
    [u8 flag][u64 size | u64 packed, u64 size] where flag&7 is 0 raw / 1 zlib and flag&0x80 means "an u64 offset
    of the next block follows" (the 2.32+ cushion header at 0x17). The index holds "File" chunks with "info"
    (u32 flags, u64 size, u64 packed, u16 name length, UTF-16LE name) and "segm" (u32 flags, u64 offset, u64
    size, u64 packed; flags&7 1 = zlib). Encrypted archives decode to garbage: callers check the text."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.files: dict[str, list[tuple[int, int, int, int]]] = {}
        with open(self.path, "rb") as f:
            if f.read(11) != XP3_MAGIC:
                raise ValueError("不是 XP3 封包")
            offset, = struct.unpack("<Q", f.read(8))
            index = b""
            for _ in range(16):
                f.seek(offset)
                flag = f.read(1)[0]
                if flag & 7 == 1:
                    packed, size = struct.unpack("<QQ", f.read(16))
                    if size > MAX_FILE_BYTES:
                        raise ValueError("XP3 索引過大")
                    index = zlib.decompress(f.read(packed))
                elif flag & 7 == 0:
                    size, = struct.unpack("<Q", f.read(8))
                    if size > MAX_FILE_BYTES:
                        raise ValueError("XP3 索引過大")
                    index = f.read(size)
                else:
                    raise ValueError("不支援的 XP3 索引格式")
                if not flag & 0x80:
                    break
                offset, = struct.unpack("<Q", f.read(8))
            else:
                raise ValueError("XP3 索引鏈過長")
        for tag, body in _chunks(index):
            if tag != b"File":
                continue                        # KiriKiri Z hashed names ("Hnfn", "eliF") are not supported
            name, segs = None, []
            for sub, data in _chunks(body):
                if sub == b"info":
                    n, = struct.unpack("<H", data[20:22])
                    name = data[22:22 + 2 * n].decode("utf-16-le")
                elif sub == b"segm":
                    segs = [struct.unpack("<IQQQ", data[i:i + 28]) for i in range(0, len(data) - 27, 28)]
            if name is not None:
                self.files[_inner_ok(name.replace("\\", "/"))] = segs

    def read(self, inner: str) -> bytes:
        out = []
        with open(self.path, "rb") as f:
            for flags, offset, size, packed in self.files[inner]:
                if size > MAX_FILE_BYTES or packed > MAX_FILE_BYTES:
                    raise UnsafePath(f"檔案超過大小上限：{inner}")
                f.seek(offset)
                data = f.read(packed)
                out.append(zlib.decompress(data) if flags & 7 == 1 else data)
        data = b"".join(out)
        if len(data) > MAX_FILE_BYTES:
            raise UnsafePath(f"檔案超過大小上限：{inner}")
        return data

    @staticmethod
    def write(files: dict[str, bytes]) -> bytes:
        """Uncompressed archive with a zlib index, the layout KiriKiri's own releaser writes."""
        body, index = bytearray(), bytearray()
        start = 11 + 8
        for name, data in files.items():
            n = name.encode("utf-16-le")
            info = struct.pack("<IQQH", 0, len(data), len(data), len(name)) + n
            segm = struct.pack("<IQQQ", 0, start + len(body), len(data), len(data))
            adlr = struct.pack("<I", zlib.adler32(data))
            chunk = b"".join(t + struct.pack("<Q", len(d)) + d for t, d in
                             ((b"info", info), (b"segm", segm), (b"adlr", adlr)))
            index += b"File" + struct.pack("<Q", len(chunk)) + chunk
            body += data
        packed = zlib.compress(bytes(index))
        return XP3_MAGIC + struct.pack("<Q", start + len(body)) + bytes(body) + \
            b"\x01" + struct.pack("<QQ", len(packed), len(index)) + packed


class Pfs:
    """Artemis archive (read; write() builds a new one): "pf6" / "pf8", u32 index size, then the index: u32 count,
    entries [u32 name length][name, "\\" separated][u32 0][u32 offset][u32 size], u32 count + 1, that many u64
    positions of each entry's offset field (counted from the end of the first u32) and a final 0, u32 position
    of that table (counted from the index start). pf8 XORs
    every file's bytes with the SHA-1 of the index."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.files: dict[str, tuple[int, int]] = {}
        with open(self.path, "rb") as f:
            self.magic = f.read(3)
            if self.magic not in (b"pf6", b"pf8"):
                raise ValueError("不支援的 pfs 封包版本")
            size, = struct.unpack("<I", f.read(4))
            if size > MAX_FILE_BYTES:
                raise ValueError("pfs 索引過大")
            index = f.read(size)
        self.key = hashlib.sha1(index).digest() if self.magic == b"pf8" else None
        count, = struct.unpack_from("<I", index, 0)
        p = 4
        for _ in range(count):
            n, = struct.unpack_from("<I", index, p)
            name = index[p + 4:p + 4 + n].decode("utf-8")
            offset, length = struct.unpack_from("<II", index, p + 8 + n)
            self.files[_inner_ok(name.replace("\\", "/"))] = (offset, length)
            p += 16 + n

    def read(self, inner: str) -> bytes:
        offset, length = self.files[inner]
        if length > MAX_FILE_BYTES:
            raise UnsafePath(f"檔案超過大小上限：{inner}")
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        if len(data) != length:
            raise ValueError(f"pfs 內容不完整：{inner}")
        return _xor(data, self.key) if self.key else data

    @staticmethod
    def write(files: dict[str, bytes], magic: bytes = b"pf8") -> bytes:
        names = [n.replace("/", "\\").encode("utf-8") for n in files]
        size = 4 + sum(16 + len(n) for n in names) + 4 + 8 * (len(names) + 1) + 4
        index, fields, offset = bytearray(struct.pack("<I", len(names))), [], 7 + size
        for n, data in zip(names, files.values()):
            index += struct.pack("<I", len(n)) + n + struct.pack("<I", 0)
            fields.append(len(index) - 4)
            index += struct.pack("<II", offset, len(data))
            offset += len(data)
        table = len(index)
        index += struct.pack(f"<I{len(fields) + 1}Q", len(fields) + 1, *fields, 0) + struct.pack("<I", table)
        key = hashlib.sha1(index).digest() if magic == b"pf8" else None
        body = b"".join(_xor(d, key) if key else d for d in files.values())
        return magic + struct.pack("<I", len(index)) + bytes(index) + body


def _xor(data: bytes, key: bytes) -> bytes:
    n = len(data)
    return (int.from_bytes(data, "little") ^ int.from_bytes((key * (n // len(key) + 1))[:n], "little")).to_bytes(n, "little")


def is_pfs(name: str) -> bool:
    return re.search(r"\.pfs(\.\d+)?$", name, re.I) is not None


def _chunks(data: bytes):
    i = 0
    while i + 12 <= len(data):
        size, = struct.unpack("<Q", data[i + 4:i + 12])
        yield data[i:i + 4], data[i + 12:i + 12 + size]
        i += 12 + size


def _integrity(data: bytes) -> dict:
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(), "blockSize": BLOCK,
            "blocks": [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, max(len(data), 1), BLOCK)]}


def open_container(game_dir: Path, outer: str, backup: Backup | None = None):
    path = safe_path(game_dir, outer)
    if outer.lower().endswith(".rpa"):
        return Rpa(path)
    if outer.lower().endswith(".xp3"):
        return Xp3(path)
    if is_pfs(outer):
        return Pfs(path)
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
    if outer.lower().endswith(".xp3"):
        return Xp3(safe_path(game_dir, outer)).read(inner)
    if is_pfs(outer):
        return Pfs(safe_path(game_dir, outer)).read(inner)
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
