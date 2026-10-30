"""PySide6 GUI (PROJECT_PLAN §11). It only calls yakupatch.core and listens to Store change events."""
import faulthandler
import sys
import traceback
from pathlib import Path


def main() -> int:
    # launch_win.bat runs this in its console, so each step and any traceback shows there.
    if sys.stderr is None:  # pythonw has no console: keep slot tracebacks and native crash stacks in a log instead
        from ..extract import projects_home
        log = projects_home().parent / "logs" / "gui.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
    from .console import ConsoleCapture
    capture = ConsoleCapture()
    normal = False
    try:
        result = _run(capture)
        normal = result == 0
        return result
    except BaseException:
        with capture.lock:
            capture.log.write(traceback.format_exc().encode("utf-8", errors="replace"))
        raise
    finally:
        faulthandler.disable()
        capture.close(normal=normal)
        faulthandler.enable(sys.stderr)


def _run(capture) -> int:
    faulthandler.enable(capture.log)
    print("Loading the GUI toolkit (PySide6)...", flush=True)
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow

    print("Creating the main window...", flush=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("YakuPatch")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.ico"))))
    window = MainWindow()
    window.console_capture = True
    window.capture = capture
    window.refresh_logs()
    if capture.previous_log:
        window.show_previous_crash(capture.previous_log)
    timer = QTimer(app, interval=100, timeout=lambda: window.console_output(capture.drain()))
    timer.start()
    window.show()
    print("Main window is open. The toolbar's job log mirrors this console.", flush=True)
    print("You can minimize this console window; closing it also closes YakuPatch.", flush=True)
    window.check_update()
    window.autostart_sakura()
    try:
        result = app.exec()
    finally:
        timer.stop()
    print(f"YakuPatch GUI exited (code {result}).", flush=True)
    return result
