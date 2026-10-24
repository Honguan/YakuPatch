""".NET assembly user strings (the #US heap), e.g. dialogue a Unity Mono game keeps in Assembly-CSharp.dll.
Rewriting builds a new #US heap, points every ldstr at its new offset in place, and moves the metadata (all streams,
only #US changed) to the end of the last PE section; method bodies and everything else keep their places."""
import struct


class ClrError(ValueError):
    pass


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _blob_len(b: bytes, i: int) -> tuple[int, int]:
    x = b[i]
    if x & 0x80 == 0:
        return x, i + 1
    if x & 0xC0 == 0x80:
        return (x & 0x3F) << 8 | b[i + 1], i + 2
    return (x & 0x1F) << 24 | b[i + 1] << 16 | b[i + 2] << 8 | b[i + 3], i + 4


def _blob_head(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return struct.pack(">H", n | 0x8000)
    return struct.pack(">I", n | 0xC0000000)


def _us_entry(s: str) -> bytes:
    data = s.encode("utf-16-le")
    # ECMA-335 II.24.2.4: the final byte is 1 when a character needs more than plain ASCII handling.
    special = any(ord(c) >= 0x7F or 0x01 <= ord(c) <= 0x08 or 0x0E <= ord(c) <= 0x1F or c in "'-" for c in s)
    return _blob_head(len(data) + 1) + data + bytes([special])


# IL operand sizes (ECMA-335 III); -1 marks switch. Opcodes not listed take no operand.
_ONE = {**dict.fromkeys([0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13, 0x1F, 0xDE, *range(0x2B, 0x38)], 1),
        **dict.fromkeys([0x20, 0x22, 0x27, 0x28, 0x29, *range(0x38, 0x45), 0x6F, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75,
                         0x79, *range(0x7B, 0x82), 0x8C, 0x8D, 0x8F, 0xA3, 0xA4, 0xA5, 0xC2, 0xC6, 0xD0, 0xDD], 4),
        0x21: 8, 0x23: 8, 0x45: -1}
_NONE = {*range(0x00, 0x0E), 0x14, *range(0x15, 0x1F), 0x25, 0x26, 0x2A, *range(0x46, 0x6F), 0x76, 0x7A,
         *range(0x82, 0x8C), 0x8E, *range(0x90, 0xA3), *range(0xB3, 0xBB), 0xC3, *range(0xD1, 0xDD), 0xDF, 0xE0}
_TWO = {0x06: 4, 0x07: 4, 0x09: 2, 0x0A: 2, 0x0B: 2, 0x0C: 2, 0x0D: 2, 0x12: 1, 0x15: 4, 0x16: 4, 0x19: 1, 0x1C: 4,
        **dict.fromkeys([0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x0F, 0x11, 0x13, 0x14, 0x17, 0x18, 0x1A, 0x1D, 0x1E], 0)}


class Assembly:
    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b"MZ":
            raise ClrError("不是 PE 檔")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ClrError("不是 PE 檔")
        coff = pe + 4
        self.nsec, = struct.unpack_from("<H", data, coff + 2)
        optsize, = struct.unpack_from("<H", data, coff + 16)
        self.opt = coff + 20
        magic, = struct.unpack_from("<H", data, self.opt)
        dd = self.opt + (96 if magic == 0x10B else 112)
        self.sec_table = self.opt + optsize
        self.sections = [struct.unpack_from("<IIII", data, self.sec_table + 40 * i + 8) for i in range(self.nsec)]
        if struct.unpack_from("<II", data, dd + 8 * 4)[1]:
            raise ClrError("DLL 帶有數位簽章，改了會失效")
        cli_rva, cli_size = struct.unpack_from("<II", data, dd + 8 * 14)
        if not cli_size:
            raise ClrError("不是 .NET 組件")
        self.cli = self.off(cli_rva)
        md_rva, _ = struct.unpack_from("<II", data, self.cli + 8)
        md = self.off(md_rva)
        if struct.unpack_from("<I", data, md)[0] != 0x424A5342:
            raise ClrError("中繼資料格式不符")
        vlen, = struct.unpack_from("<I", data, md + 12)
        p = md + 16 + vlen
        self.root = data[md:p + 2]                              # signature .. version, flags
        count, = struct.unpack_from("<H", data, p + 2)
        p += 4
        self.streams: list[list] = []                           # [name, bytes]
        for _ in range(count):
            off, size = struct.unpack_from("<II", data, p)
            end = data.index(b"\0", p + 8)
            self.streams.append([data[p + 8:end].decode("ascii"), data[md + off:md + off + size]])
            p = _align(end + 1, 4)
        names = [s[0] for s in self.streams]
        if "#~" not in names:
            raise ClrError("不支援未壓縮的中繼資料表（#-）")
        self.us = self.streams[names.index("#US")][1] if "#US" in names else b""
        self.strings = {}                                       # #US offset -> string
        i = 1
        while i < len(self.us):
            n, j = _blob_len(self.us, i)
            if n:
                self.strings[i] = self.us[j:j + n - 1].decode("utf-16-le", "replace")
            i = j + n
        self.ldstr = self._ldstr(self.streams[names.index("#~")][1])

    def off(self, rva: int) -> int:
        for vsize, va, rawsize, rawptr in self.sections:
            if va <= rva < va + max(vsize, rawsize):
                return rva - va + rawptr
        raise ClrError(f"RVA {rva:#x} 不在任何區段")

    def _ldstr(self, t: bytes) -> list[tuple[int, int]]:
        """(file offset of the operand, #US offset) of every ldstr in every method body."""
        heap, valid = t[6], struct.unpack_from("<Q", t, 8)[0]
        rows, p = {}, 24
        for k in range(64):
            if valid >> k & 1:
                rows[k] = struct.unpack_from("<I", t, p)[0]
                p += 4
        s, g, b = (4 if heap & 1 else 2), (4 if heap & 2 else 2), (4 if heap & 4 else 2)

        def idx(k):
            return 2 if rows.get(k, 0) < 0x10000 else 4

        def coded(ks, bits):
            return 2 if max(rows.get(k, 0) for k in ks) < 1 << (16 - bits) else 4
        sizes = {0: 2 + s + 3 * g, 1: coded([0x00, 0x1A, 0x23, 0x01], 2) + 2 * s,
                 2: 4 + 2 * s + coded([0x02, 0x01, 0x1B], 2) + idx(4) + idx(6), 3: idx(4), 4: 2 + s + b, 5: idx(6)}
        for k in range(6):
            p += rows.get(k, 0) * sizes[k]
        out = []
        row = 4 + 2 + 2 + s + b + idx(8)
        for r in range(rows.get(6, 0)):
            rva, = struct.unpack_from("<I", t, p + r * row)
            if rva:
                out += self._method(self.off(rva))
        return out

    def _method(self, at: int) -> list[tuple[int, int]]:
        d = self.data
        if d[at] & 3 == 2:                                      # tiny header
            start, size = at + 1, d[at] >> 2
        elif d[at] & 3 == 3:                                    # fat header
            flags, = struct.unpack_from("<H", d, at)
            start, size = at + (flags >> 12) * 4, struct.unpack_from("<I", d, at + 4)[0]
        else:
            raise ClrError(f"方法標頭無法辨識（位置 {at:#x}）")
        out, i, end = [], start, start + size
        while i < end:
            op = d[i]
            i += 1
            if op == 0xFE:
                n = _TWO.get(d[i])
                if n is None:
                    raise ClrError(f"未知的 IL 指令 FE {d[i]:02X}")
                i += 1 + n
            elif op == 0x72:
                token, = struct.unpack_from("<I", d, i)
                if token >> 24 != 0x70:
                    raise ClrError("ldstr 的參數不是字串")
                out.append((i, token & 0xFFFFFF))
                i += 4
            elif op in _ONE:
                n = _ONE[op]
                i += 4 + 4 * struct.unpack_from("<I", d, i)[0] if n == -1 else n
            elif op not in _NONE:
                raise ClrError(f"未知的 IL 指令 {op:02X}")
        return out

    def save(self, repl: dict[int, str]) -> bytes:
        """The assembly with the #US strings at the given offsets replaced."""
        heap, moved = bytearray(b"\0"), {}
        for off, text in self.strings.items():
            moved[off] = len(heap)
            heap += _us_entry(repl.get(off, text))
        heap += b"\0" * (_align(len(heap), 4) - len(heap))
        if len(heap) >= 1 << 24:
            raise ClrError("字串區超過 16 MB，無法以 ldstr 參照")
        out = bytearray(self.data)
        for at, off in self.ldstr:
            if off not in moved:
                raise ClrError(f"ldstr 指向不存在的字串（{off:#x}）")
            struct.pack_into("<I", out, at, 0x70000000 | moved[off])
        streams = [(n, bytes(heap) if n == "#US" else data) for n, data in self.streams]
        headers = b"".join(struct.pack("<II", 0, 0) + n.encode("ascii") + b"\0" * (4 - len(n) % 4) for n, _ in streams)
        md = bytearray(self.root + struct.pack("<H", len(streams)) + headers)
        p, pos = len(self.root) + 2, []
        for n, data in streams:
            pos.append((p, len(md)))
            md += data + b"\0" * (_align(len(data), 4) - len(data))
            p += 8 + _align(len(n) + 1, 4)
        for (hp, off), (_, data) in zip(pos, streams):
            struct.pack_into("<II", md, hp, off, len(data))
        return self._extend_last(out, bytes(md))

    def _extend_last(self, out: bytearray, md: bytes) -> bytes:
        """Append the metadata to the last section (headers rarely have room for another one)."""
        sec_align, file_align = struct.unpack_from("<II", out, self.opt + 32)
        k = max(range(self.nsec), key=lambda i: self.sections[i][1])
        vsize, va, rawsize, rawptr = self.sections[k]
        if rawptr != max(s[3] for s in self.sections) or rawptr + rawsize != len(out):
            raise ClrError("最後一個區段之後還有資料，無法延長")
        start = _align(max(vsize, 1), 16)
        size = start + len(md)
        body = bytes(out[rawptr:rawptr + min(start, rawsize)]).ljust(start, b"\0") + md
        out[rawptr:] = body + b"\0" * (_align(size, file_align) - size)
        head = self.sec_table + 40 * k
        flags, = struct.unpack_from("<I", out, head + 36)
        struct.pack_into("<III", out, head + 8, size, va, _align(size, file_align))
        struct.pack_into("<I", out, head + 36, (flags | 0x40000040) & ~0x02000000)   # readable data, not discardable
        struct.pack_into("<I", out, self.opt + 56, _align(va + size, sec_align))      # SizeOfImage
        struct.pack_into("<II", out, self.cli + 8, va + start, len(md))
        return bytes(out)
