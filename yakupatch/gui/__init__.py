"""PySide6 GUI (PROJECT_PLAN §11). It only calls yakupatch.core and listens to Store change events."""
import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("YakuPatch")
    window = MainWindow()
    window.show()
    window.check_update()
    return app.exec()
