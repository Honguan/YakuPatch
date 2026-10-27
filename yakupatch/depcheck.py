"""Startup check run by launch_win.bat (§12.1): every package in requirements.lock installed at its pinned version,
and Qt loadable. Exit 0: ready; 1: packages missing or wrong (the launcher offers to reinstall); 2: Qt cannot load
its DLLs, usually a missing Microsoft Visual C++ Redistributable."""
import re
import sys
from importlib import metadata
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements.lock"
VC_REDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def problems(lock: Path = LOCK) -> list[str]:
    out = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)==(\S+)", line)
        if not m:
            continue
        name, want = m.groups()
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            out.append(f"{name}: not installed (needs {want})")
            continue
        if have != want:
            out.append(f"{name}: version {have} (needs {want})")
    return out


def main() -> int:
    if not LOCK.is_file():
        print("requirements.lock not found. Download YakuPatch again.")
        return 1
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", LOCK.read_text(encoding="utf-8"), re.M)
    print(f"Checking {len(pins)} packages in {LOCK.name}: " + ", ".join(f"{n} {v}" for n, v in pins))
    found = problems()
    if found:
        print("Missing or wrong-version packages:")
        print("\n".join("  " + p for p in found))
        return 1
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError as ex:
        print(f"Cannot load the GUI toolkit: {ex}")
        print("This usually means the Microsoft Visual C++ Redistributable (x64) is missing. Install it and start again:")
        print(VC_REDIST)
        return 2
    print("All packages match and the GUI toolkit loads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
