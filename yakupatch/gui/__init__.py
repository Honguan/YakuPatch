"""PySide6 GUI (PROJECT_PLAN §11). It only calls yakupatch.core and listens to Store change events."""
import faulthandler
import sys
from pathlib import Path


def main() -> int:
    # launch_win.bat runs this in its console, so each step and any traceback shows there.
    if sys.stderr is None:  # pythonw has no console: keep slot tracebacks and native crash stacks in a log instead
        from ..extract import projects_home
        log = projects_home().parent / "logs" / "gui.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(sys.stderr)
    print("Loading the GUI toolkit (PySide6)...", flush=True)
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow

    print("Creating the main window...", flush=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("YakuPatch")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.ico"))))
    window = MainWindow()
    window.show()
    print("GUI 已開啟：操作請使用主視窗；工具列「工作紀錄」與此視窗同步顯示工作狀態。", flush=True)
    print("此命令視窗保留原生元件與錯誤的詳細輸出。請最小化即可；關閉它也會關閉 YakuPatch。", flush=True)
    window.check_update()
    window.autostart_sakura()
    result = app.exec()
    print(f"YakuPatch GUI 已結束（代碼 {result}）。", flush=True)
    return result
