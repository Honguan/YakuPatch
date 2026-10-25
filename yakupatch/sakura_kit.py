"""Local Sakura server kit: hardware check, SakuraLLM model catalogue, downloads, llama-server start/stop, and a
portable folder for running the server on another PC. Files live under ~/.yakupatch/sakura (never the game)."""
import atexit
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .extract import projects_home
from .fsutil import atomic_write

HF = "https://huggingface.co"
RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
GIB = 1024 ** 3
# Current Japanese→Chinese models, best first for games; Qwen3 builds are marked unofficial on their model cards.
# (repo, only files containing this text, note)
CATALOGUE = [("Sakura-GalTransl-14B-v3.8", "v3.8", ""), ("Sakura-14B-Qwen2.5-v1.0-GGUF", "", ""),
             ("Sakura-14B-Qwen3-v1.5-GGUF", "", "非正式版"), ("Sakura-GalTransl-7B-v3.7", "v3.7", ""),
             ("Sakura-7B-Qwen2.5-v1.0-GGUF", "", ""), ("Sakura-8B-Qwen3-v1.1-SFT-GGUF", "", "驗證版"),
             ("GalTransl-v4-4B-2601", "", ""), ("Sakura-1.5B-Qwen2.5-v1.0-GGUF", "", "")]
VARIANTS = {"cuda": "CUDA（NVIDIA）", "vulkan": "Vulkan（AMD／Intel）", "cpu": "CPU"}


@dataclass(frozen=True)
class LocalConfig:
    model: str = ""                  # file name under models/
    port: int = 8080
    gpu_layers: int = 999
    slots: int = 4
    ctx_per_slot: int = 2048
    lan: bool = False                # listen on 0.0.0.0 instead of 127.0.0.1
    autostart: bool = False

    def __post_init__(self):
        if not 1 <= self.port <= 65535 or self.slots < 1 or self.ctx_per_slot < 512 or self.gpu_layers < 0:
            raise ValueError("連接埠須為 1–65535，平行槽數至少 1，每槽 context 至少 512")


@dataclass(frozen=True)
class Model:
    repo: str
    file: str
    size: int
    sha256: str
    note: str = ""

    @property
    def version(self) -> str:
        """Sakura prompt template for this model (backends.sakura.VERSIONS)."""
        name = self.file.lower()
        return "galtransl-v1" if "galtransl" in name else "1.5" if "qwen3-v1.5" in name else "1.0"

    @property
    def url(self) -> str:
        return f"{HF}/SakuraLLM/{self.repo}/resolve/main/{self.file}"


def home() -> Path:
    return projects_home().parent / "sakura"


def _get_json(url: str, timeout: float = 30):
    with urlopen(Request(url, headers={"Accept": "application/json", "User-Agent": "YakuPatch"}), timeout=timeout) as r:
        return json.load(r)


# ---------- hardware and recommendations ----------
@cache                                                   # probed once per session
def hardware() -> dict:
    """{"gpus": [(name, vram_bytes)], "ram": bytes, "variant": cuda|vulkan|cpu}. Best effort; never raises."""
    gpus = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW).stdout
        gpus = [(n.strip(), int(m) * 1024 ** 2) for n, m in (line.rsplit(",", 1) for line in out.splitlines() if "," in line)]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    variant = "cuda" if gpus else "cpu"
    if not gpus:
        gpus = _registry_gpus()
        variant = "vulkan" if any(v >= 2 * GIB for _, v in gpus) else "cpu"
    return {"gpus": gpus, "ram": _ram(), "variant": variant}


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _registry_gpus() -> list[tuple[str, int]]:
    """AMD/Intel cards: WMI caps VRAM at 4 GB, the driver key has the real size."""
    try:
        import winreg
    except ImportError:
        return []
    out, base = [], r"SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                        size = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                        out.append((winreg.QueryValueEx(key, "DriverDesc")[0], int(size)))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _ram() -> int:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                    ("virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    try:
        status = Status(length=ctypes.sizeof(Status))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.total)
    except (AttributeError, OSError):
        return 0


def need(model: Model) -> int:
    """Weights plus KV cache and buffers for 4 slots × 2048 context (measured headroom, rounded up)."""
    return model.size + (int(1.5 * GIB) if model.size < 6 * GIB else int(2.5 * GIB))


def recommend(models: list[Model], vram: int, ram: int = 0) -> dict[str, str]:
    """file -> 推薦 / 可用 / 較慢 / 不建議. 推薦 is the best fitting file of the best fitting family."""
    labels, best = {}, None
    for m in models:          # CATALOGUE order is quality order; larger quant first within a family
        if need(m) <= vram:
            labels[m.file] = "可用"
            best = best or m
        elif need(m) <= vram + ram // 2:
            labels[m.file] = "較慢（部分用 CPU）"
        else:
            labels[m.file] = "不建議（記憶體不足）"
    if best:
        labels[best.file] = "推薦"
    return labels


# ---------- catalogue ----------
@cache                                                   # failures are not cached; cache_clear() refreshes
def catalogue() -> list[Model]:
    out = []
    for repo, only, note in CATALOGUE:
        files = [f for f in _get_json(f"{HF}/api/models/SakuraLLM/{repo}/tree/main")
                 if f["path"].endswith(".gguf") and only in f["path"]]
        files.sort(key=lambda f: -f["size"])
        out += [Model(repo, f["path"], f["size"], (f.get("lfs") or {}).get("oid", ""), note) for f in files]
    return out


def installed() -> list[Path]:
    return sorted((home() / "models").glob("*.gguf"))


def version_of(file: str) -> str:
    return Model("", file, 0, "").version


# ---------- downloads ----------
def download(url: str, dest: Path, sha256: str = "", progress=None, stop=None) -> Path:
    """Resumable (`.part` + Range), verified with SHA-256 when given. `stop` (Event) leaves the part for later."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    request = Request(url, headers={"User-Agent": "YakuPatch", **({"Range": f"bytes={done}-"} if done else {})})
    try:
        with urlopen(request, timeout=60) as r, open(part, "ab" if done and r.status == 206 else "wb") as f:
            done = done if r.status == 206 else 0
            total = done + int(r.headers.get("Content-Length") or 0)
            while chunk := r.read(1 << 20):
                if stop is not None and stop.is_set():
                    raise InterruptedError("下載已暫停，下次會從中斷處繼續")
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except HTTPError as ex:
        if ex.code != 416:                       # 416: the part is already complete
            raise OSError(f"下載失敗（HTTP {ex.code}）：{url}") from None
    except URLError as ex:
        raise OSError(f"下載失敗：{ex.reason}") from None
    if sha256:
        digest = hashlib.sha256()
        with open(part, "rb") as f:
            while block := f.read(1 << 24):
                digest.update(block)
        if digest.hexdigest() != sha256:
            part.unlink()
            raise OSError(f"{dest.name} 校驗失敗，已刪除，請重新下載")
    os.replace(part, dest)
    return dest


def download_model(model: Model, folder: Path | None = None, progress=None, stop=None) -> Path:
    return download(model.url, (folder or home() / "models") / model.file, model.sha256, progress, stop)


def llama_release(variant: str) -> tuple[str, list[dict]]:
    """Newest b#### build that ships Windows x64 binaries for `variant` (plus the CUDA runtime for cuda)."""
    for release in _get_json(RELEASES):
        assets = {a["name"]: a for a in release["assets"]}
        want = f"llama-{release['tag_name']}-bin-win-{'cuda-12.4' if variant == 'cuda' else variant}-x64.zip"
        if want in assets:
            extra = [assets[n] for n in ("cudart-llama-bin-win-cuda-12.4-x64.zip",) if variant == "cuda" and n in assets]
            return release["tag_name"], [assets[want], *extra]
    raise OSError("找不到 llama.cpp 的 Windows 版本")


def install_llama(variant: str, folder: Path | None = None, progress=None, stop=None) -> Path:
    """Downloads and unpacks llama.cpp into `folder` (default ~/.yakupatch/sakura/llama/<tag>-<variant>)."""
    tag, assets = llama_release(variant)
    target = folder or home() / "llama" / f"{tag}-{variant}"
    if server_exe(target):
        return target
    total = sum(a["size"] for a in assets)
    base = 0
    for asset in assets:
        digest = asset.get("digest", "").removeprefix("sha256:")
        zip_path = download(asset["browser_download_url"], home() / "downloads" / asset["name"], digest,
                            progress and (lambda d, _t, b=base: progress(b + d, total)), stop)
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():                   # zip-slip guard: names stay inside the target
                if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                    raise OSError(f"llama.cpp 壓縮檔內有不安全的路徑：{info.filename}")
            z.extractall(target)
        zip_path.unlink()
        base += asset["size"]
    if not server_exe(target):
        raise OSError("llama.cpp 解壓後找不到 llama-server.exe")
    return target


def server_exe(folder: Path) -> Path | None:
    return next(iter(sorted(Path(folder).rglob("llama-server.exe"))), None) if Path(folder).exists() else None


def installed_llama() -> Path | None:
    """Newest installed build (highest b#### number)."""
    dirs = [d for d in (home() / "llama").glob("b*-*") if server_exe(d)]
    return max(dirs, key=lambda d: int(re.sub(r"\D", "", d.name.split("-")[0]) or 0), default=None)


# ---------- server ----------
def server_args(exe: Path, model: Path, cfg: LocalConfig) -> list[str]:
    return [str(exe), "-m", str(model), "--host", "0.0.0.0" if cfg.lan else "127.0.0.1", "--port", str(cfg.port),
            "-ngl", str(cfg.gpu_layers), "-np", str(cfg.slots), "-c", str(cfg.slots * cfg.ctx_per_slot),
            "-a", model.stem]


class Server:
    """One llama-server process owned by YakuPatch; stopped when YakuPatch exits."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.log = projects_home().parent / "logs" / "llama-server.log"
        atexit.register(self.stop)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, cfg: LocalConfig, model: Path | None = None, exe: Path | None = None, wait: float = 300,
              stop=None) -> str:
        """Returns the API base once /health answers; raises OSError with the server's last log lines otherwise."""
        self.stop()
        model = model or home() / "models" / cfg.model
        exe = exe or server_exe(installed_llama() or home() / "missing")
        if not exe:
            raise OSError("尚未下載 llama.cpp")
        if not model.exists():
            raise OSError(f"找不到模型 {model.name}，請先下載")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "w", encoding="utf-8") as log:
            self.process = subprocess.Popen(server_args(exe, model, cfg), stdout=log, stderr=subprocess.STDOUT,
                                            cwd=exe.parent, creationflags=_NO_WINDOW)
        base = f"http://127.0.0.1:{cfg.port}"
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not self.running:
                raise OSError("llama-server 已結束：\n" + self.tail())
            if stop is not None and stop.is_set():
                self.stop()
                raise InterruptedError("已取消啟動")
            try:
                with urlopen(base + "/health", timeout=2) as r:
                    if r.status == 200:
                        return base + "/v1"
            except (OSError, ValueError):
                pass
            time.sleep(1)
        self.stop()
        raise OSError("llama-server 啟動逾時：\n" + self.tail())

    def stop(self) -> None:
        if self.running:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def tail(self, lines: int = 8) -> str:
        try:
            return "\n".join(self.log.read_text("utf-8", "replace").splitlines()[-lines:])
        except OSError:
            return ""


SERVER = Server()

# ---------- portable kit for another PC ----------
START_BAT = """@echo off
rem YakuPatch Sakura server kit. Listens on the local network so YakuPatch on another PC can use this GPU.
cd /d "%~dp0"
echo Sakura server: http://%COMPUTERNAME%:{port}/v1  (YakuPatch: Settings - Sakura - API address)
"{exe}" -m "models\\{model}" --host 0.0.0.0 --port {port} -ngl 999 -np 4 -c 8192 -a {alias}
pause
"""
README = """YakuPatch Sakura 啟動包
1. 雙擊 start_sakura.bat 啟動伺服器（第一次 Windows 防火牆會詢問，請只允許「私人網路」）。
2. 在要翻譯的電腦上，YakuPatch「設定 → Sakura」的 API 位址填 http://這台電腦的IP:{port}/v1，提示詞版本選 {version}。
3. 伺服器會對區網開放，請只在信任的網路使用；不用時關閉視窗即可停止。
模型：{model}（來源：huggingface.co/SakuraLLM，授權依模型頁面）
"""


def export_kit(dest: Path, model: Model, variant: str, port: int = 8080, progress=None, stop=None) -> Path:
    """llama.cpp + the model + start_sakura.bat in one folder. A model already downloaded here is copied."""
    dest.mkdir(parents=True, exist_ok=True)
    llama = install_llama(variant, dest / "llama", progress=progress and (lambda d, t: progress(d, t, "llama.cpp")),
                          stop=stop)
    local = home() / "models" / model.file
    target = dest / "models" / model.file
    if local.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
    elif not target.exists():
        download_model(model, dest / "models", progress and (lambda d, t: progress(d, t, model.file)), stop)
    exe = server_exe(llama).relative_to(dest)
    atomic_write(dest / "start_sakura.bat", START_BAT.format(exe=exe, model=model.file, port=port,
                                                             alias=Path(model.file).stem).replace("\n", "\r\n").encode("utf-8"))
    atomic_write(dest / "README.txt", README.format(port=port, version=model.version, model=model.file).encode("utf-8"))
    return dest
