"""Capture Python, CRT and Windows standard handles without touching widgets in reader threads."""
import codecs
import io
import os
import sys
import threading
import shutil
import uuid
from datetime import datetime
from collections import deque


def _session_lock(file, acquire):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


def previous_abnormal(folder):
    for path in sorted(folder.glob("gui-*.session"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with path.open("r+b") as file:
                _session_lock(file, True)  # A live instance holds this lock; never report it as crashed.
                state = file.read(1)
                _session_lock(file, False)
        except OSError:
            continue
        log = path.with_suffix(".log")
        return log if state in (b"R", b"E") and log.exists() else None
    return None


def read_error_context(path):
    before = deque(maxlen=20)
    context, target, error_line, after = [], 0, 0, 0
    with path.open(encoding="utf-8", errors="replace") as source:
        for number, line in enumerate(source, 1):
            line = line[:4000].rstrip("\r\n")
            if is_error(line):
                context = list(before) + [line]
                target, error_line, after = len(before), number, 20
            elif after:
                context.append(line)
                after -= 1
            before.append(line)
    return "\n".join(context or before), target, error_line


def is_error(text):
    return any(word in text.lower() for word in
               ("error", "exception", "traceback", "fatal", "failed", "錯誤", "失敗", "異常",
                "object reference", "sequence contains", "given key", "duplicate key"))


def read_history(path, query="", errors=False):
    lines = deque()
    size = 0
    count = 0
    with path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if query.lower() in line.lower() and (not errors or is_error(line)):
                count += 1
                lines.append(line[-200000:])
                size += len(lines[-1])
                while len(lines) > 2000 or size > 200000:
                    size -= len(lines.popleft())
    return "".join(lines)[-200000:], count


class ConsoleCapture:
    def __init__(self):
        from ..extract import projects_home
        folder = projects_home().parent / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        self.previous_log = previous_abnormal(folder)
        self.log_path = folder / f"gui-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.log"
        self.log = self.log_path.open("xb", buffering=0)
        self.session = self.log_path.with_suffix(".session").open("x+b", buffering=0)
        self.session.write(b"R")
        _session_lock(self.session, True)
        os.fsync(self.session.fileno())
        self.pending = deque(maxlen=256)
        self.lock = threading.Lock()
        self.dropped = 0
        self.streams = []
        self.closed = False
        self.kernel = None
        if os.name == "nt":
            import ctypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
            self.kernel.GetStdHandle.restype = ctypes.c_void_p
            self.kernel.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        try:
            for fd, name, handle in ((1, "stdout", -11), (2, "stderr", -12)):
                original = getattr(sys, name)
                if original:
                    original.flush()
                try:
                    saved = os.dup(fd)
                except OSError:  # pythonw may start without CRT standard descriptors.
                    fallback = os.open(os.devnull, os.O_WRONLY)
                    os.dup2(original.fileno() if original else fallback, fd)
                    if fallback != fd:
                        os.close(fallback)
                    saved = os.dup(fd)
                read, write = os.pipe()
                os.dup2(write, fd)
                os.close(write)
                if self.kernel:
                    import msvcrt
                    self.kernel.SetStdHandle(handle, msvcrt.get_osfhandle(fd))
                stream = io.TextIOWrapper(os.fdopen(os.dup(fd), "wb", buffering=0),
                                          encoding="utf-8", errors="replace", write_through=True)
                setattr(sys, name, stream)
                thread = threading.Thread(target=self._read, args=(read, saved), daemon=True)
                self.streams.append((fd, name, handle, original, saved, stream, thread))
                thread.start()
        except BaseException:
            self.close()
            raise

    def _read(self, fd, console):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                data = os.read(fd, 8192)
                if not data:
                    break
                self._queue(decoder.decode(data))
                try:
                    remaining = data
                    while remaining:
                        remaining = remaining[os.write(console, remaining):]
                except OSError:
                    pass
            self._queue(decoder.decode(b"", final=True))
        finally:
            os.close(fd)
            os.close(console)

    def _queue(self, text):
        if text:
            with self.lock:
                if not getattr(self, "log_error", None):
                    try:
                        self.log.write(text.encode("utf-8"))
                    except OSError as ex:
                        self.log_error = str(ex)
                        text = f"\n[紀錄寫入失敗：{ex}；請檢查磁碟空間]\n" + text
                if len(self.pending) == self.pending.maxlen:
                    self.dropped += 1
                self.pending.append(text)

    def drain(self):
        with self.lock:
            warning = f"\n[輸出過多：GUI 已略過 {self.dropped} 個區塊，完整輸出仍送至命令視窗]\n" if self.dropped else ""
            self.dropped = 0
            chunks = [self.pending.popleft() for _ in range(min(8, len(self.pending)))]
        return warning + "".join(chunks)

    def export(self, path):
        if path.resolve() == self.log_path.resolve():
            raise ValueError("請選擇不同的匯出路徑，避免覆蓋目前紀錄")
        with self.lock:
            if getattr(self, "log_error", None):
                raise OSError(f"紀錄不完整：{self.log_error}")
            shutil.copyfile(self.log_path, path)

    def close(self, normal=False):
        if self.closed:
            return
        self.closed = True
        for fd, name, handle, original, saved, stream, thread in self.streams:
            stream.flush()
            os.dup2(saved, fd)
            if self.kernel:
                import msvcrt
                self.kernel.SetStdHandle(handle, msvcrt.get_osfhandle(fd))
            setattr(sys, name, original)
            stream.close()
        for *_, thread in self.streams:
            thread.join(timeout=2)
        if not any(thread.is_alive() for *_, thread in self.streams):
            self.log.close()
        self.session.seek(0)
        self.session.write(b"N" if normal and not getattr(self, "log_error", None) else b"E")
        os.fsync(self.session.fileno())
        _session_lock(self.session, False)
        self.session.close()
