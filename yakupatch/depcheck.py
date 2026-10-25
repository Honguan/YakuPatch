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
            out.append(f"{name}：未安裝（需要 {want}）")
            continue
        if have != want:
            out.append(f"{name}：版本 {have}（需要 {want}）")
    return out


def main() -> int:
    if not LOCK.is_file():
        print("找不到 requirements.lock，請重新下載 YakuPatch。")
        return 1
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", LOCK.read_text(encoding="utf-8"), re.M)
    print(f"依 {LOCK.name} 檢查 {len(pins)} 個套件：" + "、".join(f"{n} {v}" for n, v in pins))
    found = problems()
    if found:
        print("缺少或版本不符的套件：")
        print("\n".join("  " + p for p in found))
        return 1
    try:
        from PySide6 import QtWidgets  # noqa: F401
    except ImportError as ex:
        print(f"無法載入介面元件：{ex}")
        print("通常是缺少 Microsoft Visual C++ 可轉散發套件（x64），請下載安裝後再啟動：")
        print(VC_REDIST)
        return 2
    print("套件版本都正確，介面元件可以載入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
