"""Writeback tab: preview, apply, restore and launch.

Mixed into MainWindow (window.py); methods use its widgets and helpers through `self`."""
from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

from .. import core
from .models import tr


class WritebackMixin:
    def preview(self):
        if self._row(self.key)["no_need"]:
            return
        game, plugin, store = self.current
        key = self.key

        def done(job):
            if self.key != key or self._row(key)["no_need"]:
                return
            if self._error(job, tr("無法預覽")):
                return
            self.plan = job.result
            files = store.manifest.get("files", {})
            self.wb_table.setColumnCount(4)
            self.wb_table.setHorizontalHeaderLabels([tr(s) for s in ("檔案", "大小", "替換句數", "編碼")])
            self.wb_table.setRowCount(len(self.plan.files))
            for n, f in enumerate(self.plan.files):
                for col, value in enumerate((f.rel, f"{len(f.new):,} bytes", str(f.replaced),
                                             files.get(f.rel, {}).get("encoding", ""))):
                    self.wb_table.setItem(n, col, QTableWidgetItem(value))
            notes = [tr("將修改 {} 個檔案（替換 {} 處）").format(len(self.plan.files), sum(f.replaced for f in self.plan.files))]
            notes += [tr("警告：{}").format(w) for w in self.plan.warnings[:20]]
            if len(self.plan.warnings) > 20:
                notes.append(tr("……另有 {} 則警告").format(len(self.plan.warnings) - 20))
            if any(f.rel.endswith(("Actors.json", "System.json")) for f in self.plan.files):
                notes.append(tr("存檔相容：舊存檔中的角色名等資料會維持原文，建議開新遊戲。"))
            if not self.plan.files:
                notes = [tr("沒有需要寫回的內容。")]
            self.wb_notes.setText("\n".join(notes))
            self.apply_button.setEnabled(bool(self.plan.files))

        self.run_bg(lambda job: core.preview(game, store, plugin), done, tr("寫回預覽"), key=("game", str(game.resolve())))

    def apply(self):
        if not self.plan or self._row(self.key)["no_need"]:
            return
        game = self.current[0]
        key = self.key
        msg = tr("將修改 {} 個檔案，原檔會先備份到 _yakupatch_backup/。確定寫回？").format(len(self.plan.files))
        if QMessageBox.question(self, tr("寫回遊戲"), msg) != QMessageBox.Yes:
            return
        plan, self.plan = self.plan, None
        self.apply_button.setEnabled(False)

        def done(job):
            if self.key != key:
                return
            self.plan = None
            if not self._error(job, tr("寫回失敗，遊戲未被修改")):
                self.statusBar().showMessage(tr("寫回完成"), 5000)
            self.stages.set("寫回", job.error is None, 1, tr("完成") if job.error is None else tr("未完成"))
            self.refresh_library()
            self._refresh_counts()

        self.stages.busy("寫回", tr("寫回中"))
        self.run_bg(lambda job: core.apply(game, plan), done, tr("寫回"), key=("game", str(game.resolve())))

    def restore(self):
        game = self.current[0]
        key = self.key
        if QMessageBox.question(self, tr("還原遊戲"), tr("把遊戲還原成寫回前的原檔？")) != QMessageBox.Yes:
            return

        def done(job):
            if self.key != key:
                return
            self.plan = None
            if self._error(job, tr("還原失敗")):
                return
            names = {"restored": tr("已還原"), "original": tr("已是原檔"), "stale": tr("遊戲已更新而略過")}
            self.wb_table.setColumnCount(2)
            self.wb_table.setHorizontalHeaderLabels([tr("檔案"), tr("結果")])
            self.wb_table.setRowCount(len(job.result))
            for n, (rel, result) in enumerate(job.result):
                self.wb_table.setItem(n, 0, QTableWidgetItem(rel))
                self.wb_table.setItem(n, 1, QTableWidgetItem(names.get(result, result)))
            self.wb_notes.setText(tr("沒有備份，遊戲未被修改。") if not job.result else tr("還原完成"))
            self.apply_button.setEnabled(False)
            self.refresh_library()
            self._refresh_counts()

        self.run_bg(lambda job: core.restore(game), done, tr("還原"), key=("game", str(game.resolve())))

    def launch(self):
        try:
            core.launch(self.current[0])
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", str(ex))
