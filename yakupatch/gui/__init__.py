"""PySide6 GUI (PROJECT_PLAN §11). It only calls yakupatch.core and listens to Store change events."""
import faulthandler
import os
import sys
import traceback
from pathlib import Path


def main() -> int:
    # launch_win.bat shows progress until this file appears: empty once the window is up, else the startup error.
    ready = os.environ.pop("YAKUPATCH_READY", None)
    if sys.stderr is None:  # pythonw has no console: keep slot tracebacks and native crash stacks in a log instead
        from ..extract import projects_home
        log = projects_home().parent / "logs" / "gui.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(sys.stderr)
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from .window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("YakuPatch")
        app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.ico"))))
        window = MainWindow()
        window.show()
    except BaseException:
        _signal(ready, traceback.format_exc())
        raise
    _signal(ready, "")
    window.check_update()
    window.autostart_sakura()
    return app.exec()


def _signal(ready: str | None, text: str) -> None:
    if ready:  # written then renamed, so the launcher never reads a half-written error as an empty success file
        Path(ready + ".tmp").write_text(text, encoding="utf-8")
        os.replace(ready + ".tmp", ready)
