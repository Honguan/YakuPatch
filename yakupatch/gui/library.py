"""Game library: scanning, folders, filtering, batch translation and game selection.

Mixed into MainWindow (window.py); methods use its widgets and helpers through `self`."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout)

from .. import core
from .models import tr


def _name(game: Path) -> str:
    return game.parent.name if game.name.lower() == "www" else game.name


class LibraryMixin:
    def add_game(self):
        path = QFileDialog.getExistingDirectory(self, tr("選擇遊戲資料夾"))
        if path:
            self.run_bg(lambda job: self.lib.add(Path(path)), self._library_done, tr("加入遊戲"))

    def scan_folder(self, path=None):
        """The folder is remembered, so「重新掃描」scans it again with the others."""
        path = path or QFileDialog.getExistingDirectory(self, tr("選擇上層資料夾"))
        if path:
            self.run_bg(lambda job: self.lib.scan(Path(path), progress=job.report, stop=job.stop), self._library_done,
                        tr("掃描資料夾"))

    def rescan(self):
        if not self.lib.folders:
            QMessageBox.information(self, "YakuPatch", tr("還沒有掃描過的資料夾，請先按「掃描資料夾」。"))
            return
        self.run_bg(lambda job: self.lib.rescan(progress=job.report, stop=job.stop), self._library_done,
                    tr("重新掃描 {} 個資料夾").format(len(self.lib.folders)))

    def manage_folders(self):
        dlg = QDialog(self, windowTitle=tr("掃描資料夾清單"))
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(tr("「重新掃描」會依序掃描這些資料夾。移除只是不再掃描，已加入遊戲庫的遊戲會保留。"), wordWrap=True))
        folders = QListWidget()
        folders.setSelectionMode(QAbstractItemView.ExtendedSelection)
        folders.addItems(self.lib.folders)
        v.addWidget(folders)

        def add():
            path = QFileDialog.getExistingDirectory(dlg, tr("選擇上層資料夾"))
            if path:
                dlg.accept()
                self.scan_folder(path)

        def remove():
            for item in folders.selectedItems():
                self.lib.forget_folder(item.text())
                folders.takeItem(folders.row(item))

        row = QHBoxLayout()
        for text, slot in ((tr("新增並掃描"), add), (tr("移除選取"), remove), (tr("全部重新掃描"), lambda: (dlg.accept(), self.rescan())),
                           (tr("關閉"), dlg.reject)):
            row.addWidget(QPushButton(text, clicked=slot))
        v.addLayout(row)
        dlg.resize(560, 320)
        dlg.exec()

    def _library_done(self, job):
        if not self._error(job, tr("加入失敗")):
            bad = sum(1 for k in job.result if self.lib.games.get(k, {}).get("unsupported"))
            self.statusBar().showMessage(tr("已加入或更新 {} 款遊戲，其中不支援 {} 款（游標移到遊戲上可看原因）").format(
                len(job.result), bad), 8000)
        self.refresh_library()
        self._refresh_counts()

    def remove_where(self, match, what):
        keys = [r["key"] for r in self.rows.values() if match(r)]
        if not keys:
            QMessageBox.information(self, "YakuPatch", tr("沒有「{}」的遊戲").format(what))
            return
        if QMessageBox.question(self, "YakuPatch", tr("從遊戲庫移除 {} 款「{}」的遊戲？遊戲檔案與翻譯資料都會保留，"
                                                      "重新掃描時會再加入。").format(len(keys), what)) != QMessageBox.Yes:
            return
        self.lib.remove_many(keys)
        if self.key in keys:
            self.key = None
            self.tabs.setEnabled(False)
        self.refresh_library()

    def _row(self, key):
        opened = self.games.get(key)
        return self.lib.row(key, self.home, store=opened[2] if opened else None)

    def refresh_library(self, reload=True):
        if not reload:
            self._filter_library()
            return
        current = self.key
        rows = []
        for key in list(self.lib.games):   # snapshot: a scan may add games meanwhile
            try:
                row = self._row(key)
            except (OSError, ValueError, StopIteration) as ex:
                row = {"key": key, "name": Path(key).name, "engine": "?", "lang": "", "status": tr("不支援"),
                       "progress": 0, "no_need": False, "reason": str(ex)}
            if self.translation and self.translation[1] == key and self.translation[0].state in ("queued", "running"):
                row["status"] = tr("翻譯中")
            rows.append(row)
        rows.sort(key=lambda r: (r["no_need"] or r["status"] == "不支援", r["name"].lower()))
        self.rows = {r["key"]: r for r in rows}
        self.lib_list.blockSignals(True)
        self.lib_list.clear()
        for r in rows:
            label = r["status"] if r["no_need"] or r["status"] == "不支援" else f"{r['status']} {r['progress']}%"
            item = QListWidgetItem(f"{r['name']}\n  {r['engine']}  [{r['lang'] or '—'}]  {label}")
            item.setData(Qt.UserRole, r["key"])
            if r["reason"]:
                item.setToolTip(r["reason"])
            if r["no_need"] or r["status"] == "不支援":
                item.setForeground(QColor("#999999"))
            self.lib_list.addItem(item)
            if r["key"] == current:
                self.lib_list.setCurrentItem(item)
        self.lib_list.blockSignals(False)
        self._filter_library()

    def _filter_library(self):
        q, wanted = self.search.text().strip().lower(), self.lib_filter.currentText()
        self.lib_list.setUpdatesEnabled(False)
        self.lib_list.blockSignals(True)
        try:
            for i in range(self.lib_list.count()):
                item = self.lib_list.item(i)
                row = self.rows[item.data(Qt.UserRole)]
                hidden = bool(q and q not in row["name"].lower() or
                              wanted != tr("全部") and row["status"] != wanted)
                if item.isHidden() != hidden:
                    item.setHidden(hidden)
                if hidden:
                    item.setSelected(False)
                    if item is self.lib_list.currentItem():
                        self.lib_list.setCurrentItem(None)
        finally:
            self.lib_list.blockSignals(False)
            self.lib_list.setUpdatesEnabled(True)

    def _library_menu(self, pos):
        item = self.lib_list.itemAt(pos)
        if not item:
            return
        key = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction(tr("開啟遊戲資料夾"), lambda: core.open_folder(Path(key)))
        menu.addAction(tr("重新偵測"), lambda: self.run_bg(lambda job: self.lib.redetect(key), self._library_done_quiet,
                                                       tr("重新偵測")))
        menu.addAction(tr("移除（只移除專案，不刪遊戲）"), lambda: self.remove_game(key))
        mark = menu.addAction(tr("遊戲內可切換中文（不需翻譯）"))
        mark.setCheckable(True)
        mark.setChecked(bool(self.lib.games[key].get("builtin_chinese")))
        mark.toggled.connect(lambda checked: self.set_builtin_chinese(key, checked))
        menu.addSeparator()
        menu.addAction(tr("批次翻譯選取的遊戲（{} 款）").format(len(self.lib_list.selectedItems()) or 1),
                       lambda: self.batch_translate())
        menu.exec(self.lib_list.mapToGlobal(pos))

    def set_builtin_chinese(self, key, enabled):
        self.lib.set_builtin_chinese(key, enabled)
        self.refresh_library()
        if self.key == key:
            self._refresh_counts()

    def batch_translate(self, apply=False):
        """F16: extract → translate → optionally apply each selected game, in one queued job."""
        keys = [i.data(Qt.UserRole) for i in self.lib_list.selectedItems()]
        if not keys and self.lib_list.currentItem():
            keys = [self.lib_list.currentItem().data(Qt.UserRole)]
        if not keys:
            QMessageBox.information(self, "YakuPatch", tr("請先在左側選取要翻譯的遊戲（可按 Ctrl／Shift 多選）"))
            return
        keys = [k for k in keys if not self._row(k)["no_need"]]
        if not keys:
            QMessageBox.information(self, "YakuPatch", tr("選取的遊戲不需翻譯，可在遊戲內切換中文"))
            return
        try:
            backend, name = self.make_backend()
            opened = {}
            for key in keys:
                try:
                    opened[key] = self._open_game(key)
                except (OSError, ValueError):     # unsupported games are reported in the batch result instead
                    pass
        except (TypeError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("設定有誤：{}").format(ex))
            return
        if name == "codex" and not core.load_config().get("codex", {}).get("privacy_ack"):
            if QMessageBox.question(self, "YakuPatch", tr("Codex 是實驗性後端（非公開端點）。翻譯時遊戲文本會送到 OpenAI。"
                                                          "\n確定要使用嗎？")) != QMessageBox.Yes:
                return
            core.save_config("codex", {**core.load_config().get("codex", {}), "privacy_ack": True})
        msg = tr("用 {} 依序翻譯 {} 款遊戲（尚未提取的會先提取，已是中文的會略過）。").format(
            self.backend_combo.currentText(), len(keys))
        if backend.caps.metered:
            msg += "\n" + tr("這是計費後端，批次不會逐款顯示用量預估。")
        msg += "\n" + (tr("完成後直接寫回遊戲（原檔會先備份）。") if apply else tr("只翻譯，不寫回遊戲。"))
        if QMessageBox.question(self, tr("批次翻譯"), msg + "\n\n" + tr("開始？")) != QMessageBox.Yes:
            return

        def work(job):
            return core.batch(keys, lambda: self.make_backend(name)[0], self.home, apply=apply, progress=job.report,
                              stop=job.stop, opener=lambda key: opened.get(key) or core.open_game(Path(key), self.home))

        self.batch_index = None
        self.stages.clear("批次", "提取", "翻譯", "寫回")
        self.stages.show_batch(True)
        self.batch_job = self.run_bg(work, self._batch_done, tr("批次翻譯 {} 款").format(len(keys)), key=name)
        if self.key:
            self.tabs.setCurrentIndex(3)

    def _show_batch(self, job):
        """Batch bar: games done of all; stage bars: the game being worked on."""
        s, p = self.stages, job.progress
        if job.state not in ("queued", "running"):
            for name in ("提取", "翻譯", "寫回"):
                if s.is_busy(name):
                    s.set(name, job.state == "done", 1, tr("完成") if job.state == "done" else tr("已中斷"))
            if job.state == "done":
                s.set("批次", len(job.result), len(job.result), tr("完成 {} 款").format(len(job.result)))
            return
        if "of" not in p:
            return
        if p["index"] != self.batch_index:
            self.batch_index = p["index"]
            s.clear("提取", "翻譯", "寫回")
        s.set("批次", p["index"], p["of"], tr("第 {}／{} 款：{}").format(p["index"] + 1, p["of"], _name(Path(p["game"]))))
        if p["stage"] == "提取":
            s.busy("提取", tr("提取中"))
            return
        if s.is_busy("提取") or not s.bars["提取"].value():
            s.set("提取", 1, 1, tr("完成"))
        if p["stage"] == "翻譯":
            if "total" in p:
                done = p.get("translated", 0) + p.get("failed", 0)
                s.set("翻譯", done, p["total"], tr("{}/{} 句，失敗 {}").format(done, p["total"], p.get("failed", 0)))
            else:
                s.busy("翻譯", tr("準備中"))
        elif p["stage"] == "寫回":
            s.set("翻譯", 1, 1, tr("完成"))
            s.busy("寫回", tr("寫回中"))

    def _batch_done(self, job):
        self.refresh_library()
        if self.key:
            self._refresh_counts()
        if self._error(job, tr("批次翻譯失敗")):
            return
        labels = {"ok": "完成", "skip": "不需翻譯", "stopped": "已中斷", "paused": "暫停", "error": "失敗"}
        lines = [tr("{}：{}（已翻 {}、失敗 {}）{}").format(tr(labels[r["status"]]), Path(r["game"]).name, r["translated"],
                                                     r["failed"], r["error"]) for r in job.result]
        QMessageBox.information(self, tr("批次翻譯"), "\n".join(lines) or tr("沒有處理任何遊戲"))

    def _library_done_quiet(self, job):
        self._error(job, tr("偵測失敗"))
        self.refresh_library()
        self._refresh_counts()

    def remove_game(self, key):
        if QMessageBox.question(self, "YakuPatch", tr("從遊戲庫移除？遊戲檔案與翻譯資料都會保留。")) != QMessageBox.Yes:
            return
        self.lib.remove(key)
        if key == self.key:
            self.key = None
            self.tabs.setEnabled(False)
        self.refresh_library()

    def _open_game(self, key):
        if key not in self.games:
            game, plugin, store = core.open_game(Path(key), self.home)
            store.listeners.append(lambda ids, k=key: self.bridge.store_changed.emit(k, ids))
            self.games[key] = (game, plugin, store)
        return self.games[key]

    def select_game(self, key):
        if key == self.key:
            return
        try:
            self._open_game(key)
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", str(ex))
            return
        self.key, self.plan = key, None
        self.tabs.setEnabled(True)
        game, plugin, store = self.games[key]
        self.model.set_store(store)
        self.f_file.blockSignals(True)
        self.f_file.clear()
        self.f_file.addItem(tr("檔案：全部"), "")
        for rel in sorted(store.manifest.get("files", {})):
            self.f_file.addItem(rel, rel)
        self.f_file.blockSignals(False)
        self.proxy.set(file="")
        self.wb_table.setRowCount(0)
        self.wb_notes.clear()
        self.apply_button.setEnabled(False)
        if not (self.batch_job and self.batch_job.state in ("queued", "running")):
            self.stages.clear("提取", "翻譯", "寫回")
            self.stages.show_batch(False)
        self.load_glossary()
        self._refresh_counts()

    @property
    def current(self):
        return self.games[self.key]

    def _store_changed(self, key, ids):
        if key == self.key:
            self.model.changed(ids)
            if not self._dirty.isActive():
                self._dirty.start()
