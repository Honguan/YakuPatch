"""Job log panel: live console output, saved session logs and crash context.

Mixed into MainWindow (window.py); methods use its widgets and helpers through `self`."""
import shutil
from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from .console import is_error, read_error_context, read_history
from .models import tr


class ActivityMixin:
    def console_output(self, text):
        if not text:
            return
        bar = self.activity.verticalScrollBar()
        follow = bar.value() == bar.maximum()
        cursor = QTextCursor(self.activity.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        excess = self.activity.document().characterCount() - 200000
        if excess > 0:
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, excess)
            cursor.removeSelectedText()
        if follow:
            bar.setValue(bar.maximum())
        if self.log_choice.currentData() is None and (self.activity_errors.isChecked() or self.log_search.text()):
            self._filter_activity()

    def refresh_logs(self):
        capture = getattr(self, "capture", None)
        if capture is None:
            return
        selected = self.log_choice.currentData()
        self.log_choice.blockSignals(True)
        self.log_choice.clear()
        self.log_choice.addItem(tr("即時紀錄"), None)
        for path in sorted(capture.log_path.parent.glob("gui*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path != capture.log_path:
                label = tr("上次紀錄 · {}").format(path.name) if self.log_choice.count() == 1 else path.name
                self.log_choice.addItem(label, str(path))
        index = self.log_choice.findData(selected)
        self.log_choice.setCurrentIndex(max(0, index))
        self.log_choice.blockSignals(False)
        self._select_log()

    def show_previous_crash(self, path):
        self.previous_crash = path
        self.crash_label.setText(tr("上次未正常結束（也可能是強制關閉或斷電）。已保留紀錄：{}").format(path.name))
        self.crash_notice.show()

    def view_previous_crash(self):
        path = self.previous_crash
        self._log_timer.stop()
        for control in (self.log_choice, self.log_search, self.activity_errors):
            control.blockSignals(True)
        index = self.log_choice.findData(str(path))
        if index < 0:
            self.log_choice.addItem(path.name, str(path))
            index = self.log_choice.count() - 1
        self.log_choice.setCurrentIndex(index)
        self.log_search.clear()
        self.activity_errors.setChecked(False)
        for control in (self.log_choice, self.log_search, self.activity_errors):
            control.blockSignals(False)
        self._log_request += 1
        request = self._log_request
        self.log_views.setCurrentIndex(1)
        self.activity_dock.show()
        self.history_log.clear()
        self.activity_path.setText(tr("讀取異常前後文：{}").format(path))

        def done(job):
            if request != self._log_request:
                return
            if job.error:
                self.activity_path.setText(tr("讀取失敗：{}").format(job.error))
                return
            text, target, line = job.result
            self.history_log.setPlainText(text)
            cursor = QTextCursor(self.history_log.document().findBlockByNumber(target if line else 0))
            cursor.select(QTextCursor.LineUnderCursor)
            self.history_log.setTextCursor(cursor)
            self.history_log.ensureCursorVisible()
            detail = tr("最後錯誤第 {} 行（前後各最多 20 行）").format(line) if line else tr("未找到錯誤關鍵字，顯示結尾紀錄")
            self.activity_path.setText(f"{path} — {detail}")

        self.run_bg(lambda job: read_error_context(path), done, tr("查看上次異常"), key="log-history")

    def _select_log(self):
        self._log_request += 1
        request = self._log_request
        path = self.log_choice.currentData()
        if path is None:
            self.log_views.setCurrentIndex(0)
            capture = getattr(self, "capture", None)
            self.activity_path.setText(str(capture.log_path) if capture else "")
            self._filter_activity()
            return
        path = Path(path)
        self.log_views.setCurrentIndex(1)
        self.history_log.clear()
        self.activity_path.setText(tr("讀取中：{}").format(path))
        query, errors = self.log_search.text(), self.activity_errors.isChecked()

        def done(job):
            if request != self._log_request:
                return
            if job.error:
                self.activity_path.setText(tr("讀取失敗：{}").format(job.error))
                return
            text, count = job.result
            self.history_log.setPlainText(text)
            self.history_log.moveCursor(QTextCursor.End)
            self.activity_path.setText(tr("{} — 符合 {} 行；顯示最後 2,000 行／200,000 字元").format(path, count))

        self.run_bg(lambda job: read_history(path, query, errors) if request == self._log_request else None,
                    done, tr("讀取歷史紀錄"), key="log-history")

    def _filter_activity(self):
        if self.log_choice.currentData() is not None:
            self._select_log()
            return
        only_errors = self.activity_errors.isChecked()
        query = self.log_search.text().lower()
        document = self.activity.document()
        block = document.begin()
        while block.isValid():
            text = block.text().lower()
            block.setVisible(query in text and (not only_errors or is_error(text)))
            block = block.next()
        document.markContentsDirty(0, document.characterCount())
        self.activity.viewport().update()

    def export_activity(self):
        capture = getattr(self, "capture", None)
        if capture is None:
            QMessageBox.information(self, "YakuPatch", tr("尚無自動儲存紀錄"))
            return
        source = Path(self.log_choice.currentData()) if self.log_choice.currentData() else capture.log_path
        filename, _ = QFileDialog.getSaveFileName(self, tr("匯出完整工作紀錄"),
                                                 str(Path.home() / source.name), "Log (*.log)")
        if not filename:
            return

        def done(job):
            if not self._error(job, tr("匯出紀錄失敗")):
                self.statusBar().showMessage(tr("紀錄已匯出：{}").format(filename), 10000)

        def export(job):
            target = Path(filename)
            if source.resolve() == target.resolve():
                raise ValueError("請選擇不同的匯出路徑")
            if source == capture.log_path:
                capture.export(target)
            else:
                shutil.copyfile(source, target)

        self.run_bg(export, done, tr("匯出工作紀錄"))
