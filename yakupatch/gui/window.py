"""Main window (§11.2): library on the left, five-step bar, tabs, job queue at the bottom.

Long work runs on core.JobQueue threads; results and Store changes come back through Qt signals."""
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDockWidget, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy,
                               QSpinBox, QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import core, sakura_kit
from ..backends import AuthRequired, ConfigError, UsageLimit
from ..backends.codex import Codex
from ..backends.openai_compat import OpenAICompat
from ..backends.sakura import Sakura
from ..cli import _LANG, _REASON
from ..extract import LanguageGateStop
from .models import KIND, STATUS, TagEdit, TextFilter, TextModel, tr
from .settings import SettingsDialog

STEPS = ["①偵測", "②語言預檢", "③提取", "④翻譯", "⑤寫回"]
NEEDS_USER = (AuthRequired, UsageLimit, ConfigError)


def qa_text(reason: str, store) -> str:
    """T13 reasons (qa.check) in words."""
    kind, _, arg = reason.partition(":")
    if kind == "term":
        return tr("含術語「{}」卻未使用指定譯名").format(arg)
    if kind == "variant":
        other = store.records.get(arg)
        return tr("與相同原文的另一句譯法差異大：{}").format((other or {}).get("dst", arg).replace("\n", " "))
    if kind == "gap":
        return tr("前後句都已翻，此句未翻")
    return reason


def _name(game: Path) -> str:
    return game.parent.name if game.name.lower() == "www" else game.name


class StageBars(QGroupBox):
    """Progress on the translate tab: the game as a whole, the batch, and each stage of the running work."""
    NAMES = ("整體", "批次", "提取", "翻譯", "寫回")

    def __init__(self):
        super().__init__(tr("進度"))
        grid = QGridLayout(self)
        self.bars, self.labels, self.rows = {}, {}, {}
        for row, name in enumerate(self.NAMES):
            head, bar, label = QLabel(tr(name)), QProgressBar(textVisible=False), QLabel()
            for col, w in enumerate((head, bar, label)):
                grid.addWidget(w, row, col)
            self.bars[name], self.labels[name], self.rows[name] = bar, label, (head, bar, label)
        grid.setColumnStretch(1, 1)
        self.show_batch(False)

    def set(self, name, value, maximum, text=""):
        self.bars[name].setRange(0, max(maximum, 1))
        self.bars[name].setValue(min(value, max(maximum, 1)))
        self.labels[name].setText(text)

    def busy(self, name, text):
        self.bars[name].setRange(0, 0)               # an animated bar: the step reports no count
        self.labels[name].setText(text)

    def is_busy(self, name) -> bool:
        return self.bars[name].maximum() == 0

    def clear(self, *names):
        for name in names:
            self.set(name, 0, 1)

    def show_batch(self, on: bool):
        for w in self.rows["批次"]:
            w.setVisible(on)


class Bridge(QObject):
    """Store and job callbacks fire on worker threads; signals queue them onto the GUI thread."""
    store_changed = Signal(str, object)
    job_changed = Signal(object)
    update_found = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, library: core.Library | None = None, home: Path | None = None):
        super().__init__()
        self.setWindowTitle("YakuPatch")
        self.resize(1280, 800)
        self.home = home
        self.lib = library or core.Library()
        self.queue = core.JobQueue()
        self.bridge = Bridge()
        self.queue.listeners.append(self.bridge.job_changed.emit)
        self.bridge.job_changed.connect(self._job_changed)
        self.bridge.store_changed.connect(self._store_changed)
        self.bridge.update_found.connect(self._update_found)
        self.games: dict[str, tuple] = {}         # key -> (game, plugin, store), one Store per game (single writer)
        self.callbacks: dict = {}
        self.jobs: list = []
        self._job_serial = 0
        self.translation = None                   # (job, key) of the job shown on the translate tab
        self.key = None
        self.plan = None
        self.backend_info = {}
        self.rows: dict[str, dict] = {}            # key -> library row as last shown
        self.batch_job = None
        self.batch_index = None
        self._dirty = QTimer(self, singleShot=True, interval=200,
                             timeout=lambda: self._refresh_counts(history=False))
        self._build()
        self.refresh_library()

    def check_update(self):
        """Look for a newer release in the background; only a status-bar link, nothing is downloaded."""
        proxies = urllib.request.getproxies()       # here, not on the thread: see core.newer_release
        def run():
            found = core.newer_release(proxies=proxies)
            if found:
                self.bridge.update_found.emit(*found)

        threading.Thread(target=run, daemon=True).start()

    def autostart_sakura(self):
        """Settings → 本地模型管理「啟動 YakuPatch 時自動開啟伺服器」; the Sakura API address was saved on first start."""
        try:
            cfg = core.local_config()
        except (TypeError, ValueError):
            return
        if not (cfg.autostart and cfg.model):
            return

        def done(job):
            self.statusBar().showMessage(tr("本地 Sakura 啟動失敗：{}").format(job.error) if job.error
                                         else tr("本地 Sakura 已啟動：{}").format(job.result), 10000)

        self.run_bg(lambda job: sakura_kit.SERVER.start(cfg, stop=job.stop), done, tr("啟動 Sakura 伺服器"))

    def _update_found(self, tag: str, url: str):
        label = QLabel(tr('有新版本 {}：<a href="{}">前往下載</a>').format(tag, url), openExternalLinks=True)
        self.statusBar().addPermanentWidget(label)

    # ---------- layout ----------
    def _menu_button(self, title, actions):
        button = QPushButton(tr(title))
        menu = QMenu(button)
        for text, slot in actions:
            menu.addAction(tr(text), slot)
        button.setMenu(menu)
        return button

    def _build(self):
        bar = self.addToolBar(tr("主要"))
        bar.setMovable(False)
        bar.addAction(QAction(tr("＋加入遊戲"), self, triggered=self.add_game))
        bar.addWidget(self._menu_button("遊戲庫管理", (
            ("掃描資料夾", lambda: self.scan_folder()), ("重新掃描", self.rescan),
            ("掃描資料夾清單", self.manage_folders),
            ("移除不需翻譯", lambda: self.remove_where(lambda r: r["no_need"], tr("不需翻譯"))),
            ("移除不支援", lambda: self.remove_where(lambda r: r["status"] == tr("不支援"), tr("不支援"))))))
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.backend_label = QLabel(tr("後端：未連線"))
        bar.addWidget(self.backend_label)
        bar.addAction(QAction(tr("設定"), self, triggered=lambda: self.open_settings()))
        self.activity = QPlainTextEdit(readOnly=True)
        self.activity.document().setMaximumBlockCount(2000)
        dock = QDockWidget(tr("工作紀錄（與命令視窗同步）"), self)
        dock.setWidget(self.activity)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        action = dock.toggleViewAction()
        action.setText(tr("工作紀錄"))
        bar.addAction(action)

        left = QWidget()
        lv = QVBoxLayout(left)
        self.search = QLineEdit(placeholderText=tr("搜尋遊戲"))
        self.search.textChanged.connect(lambda: self.refresh_library(reload=False))
        self.lib_filter = QComboBox()
        self.lib_filter.addItems([tr(s) for s in ("全部", "未處理", "翻譯中", "已提取", "已寫回", "不需翻譯", "不支援")])
        self.lib_filter.currentIndexChanged.connect(lambda: self.refresh_library(reload=False))
        self.lib_list = QListWidget()
        self.lib_list.setUniformItemSizes(True)
        self.lib_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lib_list.currentItemChanged.connect(lambda item, _: item and self.select_game(item.data(Qt.UserRole)))
        self.lib_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lib_list.customContextMenuRequested.connect(self._library_menu)
        for w in (QLabel(tr("遊戲庫（可按 Ctrl／Shift 多選）")), self.search, self.lib_filter, self.lib_list):
            lv.addWidget(w)
        buttons = QGridLayout()
        self.batch_apply = QCheckBox(tr("批次翻譯完成後寫回"))
        buttons.addWidget(QPushButton(tr("批次翻譯選取遊戲"), clicked=self.batch_translate), 0, 0)
        buttons.addWidget(self.batch_apply, 1, 0)
        lv.addLayout(buttons)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.title = QLabel(tr("請從左側選擇或加入遊戲"))
        self.title.setStyleSheet("font-size: 16pt; font-weight: 600")
        rv.addWidget(self.title)
        steps = QHBoxLayout()
        self.step_labels = []
        for i, name in enumerate(STEPS):
            b = QLabel(tr(name), alignment=Qt.AlignCenter)
            steps.addWidget(b)
            self.step_labels.append(b)
        rv.addLayout(steps)
        self.tabs = QTabWidget()
        for widget, name in ((self._overview(), "總覽"), (self._text(), "文本"), (self._glossary(), "術語"),
                             (self._translate(), "翻譯"), (self._writeback(), "寫回與還原")):
            self.tabs.addTab(widget, tr(name))
        rv.addWidget(self.tabs)
        self.tabs.setEnabled(False)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 980])
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.addWidget(split)
        jobs = QHBoxLayout()
        self.job_label = QLabel(tr("工作佇列：沒有工作"))
        self.job_pause = QPushButton(tr("暫停"), clicked=self.pause_job)
        self.resume_button = QPushButton(tr("繼續"), clicked=self.resume_job)
        self.job_cancel = QPushButton(tr("取消"), clicked=self.cancel_job)
        self.job_bar = QProgressBar(maximumWidth=240, visible=False)
        jobs.addWidget(self.job_label, 1)
        jobs.addWidget(self.job_bar)
        jobs.addWidget(self.job_pause)
        jobs.addWidget(self.resume_button)
        jobs.addWidget(self.job_cancel)
        cv.addLayout(jobs)
        self.setCentralWidget(central)
        self.scope.idToggled.connect(lambda *_: self._translation_buttons())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._translation_buttons())
        self._translation_buttons()
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.tabs.setCurrentIndex(1), self.query.setFocus()))

    def _overview(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.info = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        self.lang_card = QLabel(wordWrap=True)
        self.lang_card.setStyleSheet("border: 1px solid #bbb; border-radius: 6px; padding: 10px")
        self.force_button = QPushButton(tr("仍要翻譯"), clicked=lambda: self.extract(force=True))
        self.primary = QPushButton(clicked=self.primary_action)
        self.primary.setStyleSheet("font-size: 13pt; padding: 8px 24px")
        for x in (self.info, self.lang_card, self.force_button, self.primary):
            v.addWidget(x)
        v.addStretch()
        return w

    def _text(self):
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.f_status, self.f_kind, self.f_file, self.f_flag = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        self.f_status.addItem(tr("狀態：全部"), "")
        for k, name in STATUS.items():
            self.f_status.addItem(tr(name), k)
        self.f_kind.addItem(tr("種類：全部"), "")
        for k, name in KIND.items():
            self.f_kind.addItem(tr(name), k)
        self.f_flag.addItem(tr("旗標：全部"), "")
        for k, name in (("long", "超長"), ("uncertain", "不確定"), ("inconsistent", "不一致")):
            self.f_flag.addItem(tr(name), k)
        self.query = QLineEdit(placeholderText=tr("搜尋原文或譯文"))
        self.count_label = QLabel()
        for x in (self.f_status, self.f_kind, self.f_file, self.f_flag, self.query, self.count_label):
            row.addWidget(x)
        v.addLayout(row)
        self.model, self.proxy = TextModel(), TextFilter()
        self.proxy.setSourceModel(self.model)
        for combo, attr in ((self.f_status, "status"), (self.f_kind, "kind"), (self.f_file, "file"),
                            (self.f_flag, "flag")):
            combo.currentIndexChanged.connect(lambda _=0, c=combo, a=attr: self._filter(**{a: c.currentData() or ""}))
        self.query.textChanged.connect(lambda t: self._filter(query=t))
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        for col, width in enumerate((60, 60, 90, 330, 330, 70)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self.load_editor())
        v.addWidget(self.table, 3)

        box = QGroupBox(tr("編輯"))
        g = QGridLayout(box)
        self.src_label = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        self.ctx_label = QLabel(wordWrap=True)
        self.ctx_label.setStyleSheet("color: #666")
        self.editor = TagEdit()
        self.editor.setMaximumHeight(90)
        self.edit_error = QLabel()
        self.edit_error.setStyleSheet("color: #c62828")
        self.refs_label = QLabel(wordWrap=True)
        buttons = QHBoxLayout()
        for text, slot, key in ((tr("儲存並下一句"), self.save_edit, "Ctrl+Return"),
                                (tr("重翻選取"), self.retranslate_selected, "F5"),
                                (tr("標為已審閱"), self.mark_reviewed, None)):
            b = QPushButton(text, clicked=slot)
            if key:
                b.setShortcut(QKeySequence(key))
                b.setToolTip(key)
            buttons.addWidget(b)
        buttons.addWidget(self._menu_button("更多文本操作", (
            ("套用到相同原文", self.apply_same), ("全文 QA", self.run_qa))))
        g.addWidget(QLabel(tr("原文")), 0, 0)
        g.addWidget(self.src_label, 0, 1)
        g.addWidget(QLabel(tr("上下文")), 1, 0)
        g.addWidget(self.ctx_label, 1, 1)
        g.addWidget(QLabel(tr("譯文")), 2, 0)
        g.addWidget(self.editor, 2, 1)
        g.addWidget(self.edit_error, 3, 1)
        g.addWidget(self.refs_label, 4, 1)
        g.addLayout(buttons, 5, 1)
        v.addWidget(box, 2)
        return w

    def _glossary(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.gloss = QTableWidget(0, 5)
        self.gloss.setHorizontalHeaderLabels([tr(s) for s in ("原文", "譯文", "種類／備註", "來源", "已確認")])
        self.gloss.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.gloss)
        row = QHBoxLayout()
        for text, slot in ((tr("新增"), self.gloss_add), (tr("刪除選取"), self.gloss_delete),
                           (tr("重新載入"), self.load_glossary), (tr("儲存"), self.save_glossary)):
            row.addWidget(QPushButton(text, clicked=slot))
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._menu_button("查詢譯名", (
            ("查字典", self.gloss_lookup), ("VNDB 角色名單", self.gloss_vndb),
            ("Wikidata 查譯名", self.gloss_wikidata))))
        row.addStretch()
        v.addLayout(row)
        v.addWidget(QLabel(tr("字典譯名只是候選（來源 dict:…），勾選「已確認」後才會要求翻譯使用。")))
        v.addWidget(QLabel(tr("翻譯進行中術語表為唯讀。")))
        style = QGroupBox(tr("翻譯風格 style.md（人稱、敬語、語氣、專有名詞規則；Codex 與 OpenAI 相容後端會使用）"))
        s = QVBoxLayout(style)
        self.style_edit = QPlainTextEdit(placeholderText=tr("例如：主角自稱「我」；女主角稱呼主角「哥哥」；語氣輕鬆。"))
        self.style_edit.setMaximumHeight(120)
        s.addWidget(self.style_edit)
        s.addWidget(QPushButton(tr("儲存風格"), clicked=self.save_style), 0, Qt.AlignRight)
        v.addWidget(style)
        return w

    def _translate(self):
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        line = QHBoxLayout()
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Sakura", "sakura")
        self.backend_combo.addItem(tr("Codex（實驗性）"), "codex")
        self.backend_combo.addItem(tr("OpenAI 相容"), "openai")
        self.backend_combo.currentIndexChanged.connect(self._backend_note)
        self.backend_status = QLabel()
        line.addWidget(self.backend_combo)
        line.addWidget(QPushButton(tr("測試連線"), clicked=self.test_backend))
        line.addWidget(self.backend_status, 1)
        form.addRow(tr("後端"), line)
        self.script_note = QLabel(wordWrap=True)
        form.addRow(tr("輸出字體"), self.script_note)
        scope = QHBoxLayout()
        self.scope = QButtonGroup(self)
        for i, name in enumerate(("全部待翻", "只翻選取的句子", "只翻名稱與術語", "試譯前 N 句")):
            r = QRadioButton(tr(name), checked=i == 0)
            self.scope.addButton(r, i)
            scope.addWidget(r)
        self.trial = QSpinBox(minimum=1, maximum=100000, value=20)
        scope.addWidget(self.trial)
        form.addRow(tr("範圍"), scope)
        v.addLayout(form)
        ctl = QHBoxLayout()
        self.start_button = QPushButton(tr("開始翻譯"), clicked=lambda: self.start_translation())
        ctl.addWidget(self.start_button)
        v.addLayout(ctl)
        self.stages = StageBars()
        self.progress_label = QLabel()
        self.action_box = QWidget()
        ab = QHBoxLayout(self.action_box)
        self.action_label = QLabel(wordWrap=True)
        self.action_label.setStyleSheet("color: #b26a00")
        self.fix = None
        self.action_button = QPushButton(clicked=lambda: self.fix and self.fix())
        ab.addWidget(self.action_label, 1)
        ab.addWidget(self.action_button)
        self.action_box.hide()
        for x in (self.stages, self.progress_label, self.action_box):
            v.addWidget(x)
        v.addWidget(QLabel(tr("翻譯歷史（撤銷不還原術語表與快取）")))
        self.history = QTableWidget(0, 6)
        self.history.setHorizontalHeaderLabels([tr(s) for s in ("時間", "紀錄", "後端／模型", "句數", "成功／失敗", "狀態")])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.history)
        row = QHBoxLayout()
        row.addWidget(self._menu_button("翻譯紀錄操作", (("撤銷此次翻譯", self.undo_run),)))
        row.addWidget(self._menu_button("匯入／匯出", (
            ("匯出分批檔", self.export_batches), ("匯入譯文", self.import_batches),
            ("匯出分享檔", self.export_share))))
        row.addStretch()
        v.addLayout(row)
        self._backend_note()
        return w

    def _writeback(self):
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.preview_button = QPushButton(tr("寫回前預覽"), clicked=self.preview)
        self.apply_button = QPushButton(tr("寫回遊戲"), clicked=self.apply, enabled=False)
        for b in (self.preview_button, self.apply_button):
            row.addWidget(b)
        row.addWidget(self._menu_button("遊戲操作", (
            ("還原遊戲", self.restore), ("啟動遊戲", self.launch))))
        v.addLayout(row)
        self.wb_table = QTableWidget(0, 4)
        self.wb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.wb_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.wb_notes = QLabel(wordWrap=True)
        v.addWidget(self.wb_table)
        v.addWidget(self.wb_notes)
        return w

    # ---------- background work ----------
    def run_bg(self, fn, done, label, key=None):
        """`fn(job)` runs on a worker thread; `done(job)` runs on the GUI thread when it ends."""
        job = self.queue.submit(key, fn, label)
        self._job_serial += 1
        job.gui_number = self._job_serial
        self.callbacks[job] = done
        self.jobs.append(job)
        self._job_changed(job)
        return job

    def _job_changed(self, job):
        if getattr(job, "gui_number", None) is not None and getattr(job, "gui_state", None) != job.state:
            job.gui_state = job.state
            state = {"queued": "排隊中", "running": "進行中", "done": "完成", "failed": "失敗",
                     "paused": "已暫停", "cancelled": "已取消"}.get(job.state, job.state)
            message = f"[{time.strftime('%H:%M:%S')}] #{getattr(job, 'gui_number', '—')} {job.label}：{tr(state)}"
            if getattr(job, "error", None):
                message += f" — {job.error}"
            self.activity.appendPlainText(message)
            print(message, flush=True)
        if job.state in ("done", "failed", "paused", "cancelled") and job in self.callbacks:
            self.callbacks.pop(job)(job)
        live = [j for j in self.jobs if j.state in ("queued", "running")]
        self.jobs = live + [j for j in self.jobs if j.state == "paused"][-5:]
        if live:
            self.job_label.setText(tr("工作佇列：") + "；".join(self._job_text(j) for j in live))
            p = live[0].progress
            done, total = (p["index"], p["of"]) if "of" in p else (p.get("translated", 0) + p.get("failed", 0),
                                                                     p.get("total", 0))
            self.job_bar.setRange(0, total if live[0].state == "running" and total else 0)
            self.job_bar.setValue(done)
        else:
            self.job_label.setText(tr("工作佇列：沒有進行中的工作"))
        self.job_bar.setVisible(bool(live))
        if self.translation and job is self.translation[0]:
            self._show_progress(job)
        if job is self.batch_job:
            self._show_batch(job)
        self._translation_buttons()
        if self.key:
            busy = any(j.key == ("game", str(self.current[0].resolve())) for j in live)
            if busy:
                self.preview_button.setEnabled(False)
                self.apply_button.setEnabled(False)
            else:
                self.preview_button.setEnabled(not self.rows.get(self.key, {}).get("no_need", False))
                self.apply_button.setEnabled(bool(self.plan and self.plan.files))

    @staticmethod
    def _job_text(j) -> str:
        p = j.progress
        if j.state == "queued":
            return f"{j.label} {tr('排隊中')}"
        bits = [j.label]
        if "of" in p:
            bits.append(f"{p['index'] + 1}/{p['of']}")
        if p.get("game"):
            bits.append(_name(Path(p["game"])))
        if "total" in p:
            bits.append(f"{p.get('translated', 0) + p.get('failed', 0)}/{p['total']}"
                        + (f" {tr('失敗')} {p['failed']}" if p.get("failed") else ""))
        elif p.get("stage"):
            bits.append(tr(p["stage"]))
        elif not p:
            bits.append(tr("進行中"))
        return " · ".join(bits)

    def _error(self, job, what) -> bool:
        if job.error is None:
            return False
        QMessageBox.warning(self, "YakuPatch", f"{what}：{job.error}")
        return True

    # ---------- library ----------
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
        for key in self.lib.games:
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

    def batch_translate(self):
        """F16: extract → translate → optionally apply each selected game, in one queued job."""
        apply = self.batch_apply.isChecked()
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

    # ---------- overview & steps ----------
    def _refresh_counts(self, history=True):
        if not self.key:
            return
        game, plugin, store = self.current
        c = core.counts(store)
        entry = self.lib.games.get(self.key, {})
        row = self._row(self.key)
        if self.translation and self.translation[1] == self.key and self.translation[0].state in ("queued", "running"):
            row["status"] = tr("翻譯中")
        self.rows[self.key] = row
        probe = store.manifest.get("language_probe") or entry.get("probe") or {}
        extracted, applied = bool(store.manifest), bool(core.Backup(game).files)
        todo = c["translated"] + c["reviewed"] + c["pending"] + c["failed"]
        pct = round(100 * (c["translated"] + c["reviewed"]) / todo) if todo else 0
        self.stages.set("整體", c["translated"] + c["reviewed"], todo, tr("已翻 {}／可翻 {}（{}%）").format(
            c["translated"] + c["reviewed"], todo, pct))
        self.title.setText(f"{game.name if game.name.lower() != 'www' else game.parent.name}    "
                           f"{plugin.name}  [{row['lang']}]")
        marks = [True, bool(probe), extracted, extracted and c["pending"] + c["failed"] == 0 and todo > 0, applied]
        for b, name, ok, i in zip(self.step_labels, STEPS, marks, range(5)):
            b.setText(tr(name) + (" ✓" if ok else f" {pct}%" if i == 3 and extracted else ""))
        self.info.setText(tr("路徑：{}\n引擎：{}（偵測分數 {:.2f}）\n文本檔：{} 個\n句數：共 {}，待翻 {}，已翻 {}，"
                             "已審閱 {}，失敗 {}，不翻 {}，不確定 {}").format(
            game, plugin.name, plugin.detect(game), len(store.manifest.get("files", {})), c["total"], c["pending"],
            c["translated"], c["reviewed"], c["failed"], c["skip"], c["uncertain"]))
        zh = row["no_need"]
        for i in (1, 2, 3):
            self.tabs.setTabEnabled(i, not zh)
        self.preview_button.setEnabled(not zh)
        if zh:
            self.plan = None
            self.apply_button.setEnabled(False)
            if self.tabs.currentIndex() in (1, 2, 3):
                self.tabs.setCurrentIndex(0)
        if probe:
            head = (tr("語言預檢：{} ✓ 可以翻譯") if not zh else tr("語言預檢：已是{}，不需翻譯")).format(
                _LANG.get(probe["primary"], probe["primary"]))
            self.lang_card.setText(f"{head}\n" + tr("假名比例 {:.0%}　簡體專有字 {}　繁體專有字 {}　內建語言包：{}").format(
                probe.get("kana_ratio", 0), probe.get("simp_only", 0), probe.get("trad_only", 0),
                "、".join(probe.get("packs", [])) or tr("無")))
        else:
            self.lang_card.setText(tr("語言預檢：提取時進行"))
        if row["no_need"]:
            self.lang_card.setText(tr("不需翻譯：{}\n請在遊戲設定切換中文。").format(row["reason"]))
        self.force_button.setVisible(False)
        self.primary.setText(tr("開始提取") if not extracted else tr("前往翻譯") if c["pending"] + c["failed"] else
                             tr("預覽寫回") if not applied else tr("啟動遊戲"))
        if zh:
            self.primary.setText(tr("啟動遊戲"))
        self.count_label.setText(tr("{} 句").format(self.proxy.rowCount()))
        if history:
            self.load_history()
        self._translation_buttons()

    def primary_action(self):
        text = self.primary.text()
        if text == tr("開始提取"):
            self.extract()
        elif text == tr("前往翻譯"):
            self.tabs.setCurrentIndex(3)
        elif text == tr("預覽寫回"):
            self.tabs.setCurrentIndex(4)
            self.preview()
        else:
            self.launch()

    def extract(self, force=False):
        if self._row(self.key)["no_need"]:
            QMessageBox.information(self, "YakuPatch", tr("遊戲內已有中文，不需翻譯"))
            return
        game, plugin, store = self.current
        key = self.key

        def done(job):
            if self.key != key:
                return
            if isinstance(job.error, LanguageGateStop):
                self.lib.games.setdefault(self.key, {})["probe"] = job.error.probe
                packs = "\n".join(p.hint for p in job.error.packs)
                QMessageBox.information(self, "YakuPatch", tr("遊戲已是中文，不需翻譯。仍要翻譯請按「仍要翻譯」。")
                                        + (f"\n{packs}" if packs else ""))
                self.force_button.setVisible(True)
            elif not self._error(job, tr("提取失敗")):
                self.statusBar().showMessage(tr("提取完成"), 5000)
                self.select_game_refresh()
            self.stages.set("提取", job.error is None, 1, tr("完成") if job.error is None else tr("未完成"))
            self._refresh_counts()

        self.stages.busy("提取", tr("提取中"))
        self.run_bg(lambda job: core.extract(game, plugin, store, force), done, tr("提取"), key=("game", str(game.resolve())))

    def select_game_refresh(self):
        key, self.key = self.key, None
        self.select_game(key)
        self.refresh_library()

    # ---------- text tab ----------
    def _filter(self, **kw):
        self.proxy.set(**kw)
        self.count_label.setText(tr("{} 句").format(self.proxy.rowCount()))

    def _rec(self, index=None):
        index = index or self.table.currentIndex()
        if not index.isValid():
            return None
        return self.model.record(self.proxy.mapToSource(index).row())

    def selected_ids(self):
        return [self.model.record(self.proxy.mapToSource(i).row())["id"] for i in self.table.selectionModel().selectedRows()]

    def load_editor(self):
        rec = self._rec()
        if rec is None:
            return
        game, plugin, store = self.current
        src, dst, masks = core.masked(rec, plugin)
        self.src_label.setText(src + ("\n" + "　".join(f"{k} = {v}" for k, v in masks.items()) if masks else ""))
        rows = self.model.ids
        i = self.model.pos[rec["id"]]
        prev = [store.records[r]["src"] for r in rows[max(0, i - 3):i]]
        self.ctx_label.setText(" ／ ".join(p.replace("\n", " ") for p in prev) or "—")
        self.editor.setPlainText(dst)
        errs = [_REASON.get(e, e) for e in rec.get("errors", [])]
        notes = [qa_text(q, store) for q in rec.get("qa", [])]
        self.edit_error.setText("\n".join(([tr("錯誤：") + "、".join(errs)] if errs else []) +
                                          ([tr("不一致：") + "；".join(notes)] if notes else [])))
        refs = rec["refs"]
        self.refs_label.setText(tr("出現位置：{} 等 {} 處").format(refs[0]["file"], len(refs)) if refs else "")

    def save_edit(self):
        rec = self._rec()
        if rec is None:
            return
        game, plugin, store = self.current
        errors = core.edit(store, plugin, rec["id"], self.editor.toPlainText())
        if errors:
            self.edit_error.setText(tr("未儲存：") + "、".join(_REASON.get(e, e) for e in errors))
            return
        nxt = self.proxy.index(min(self.table.currentIndex().row() + 1, self.proxy.rowCount() - 1), 0)
        self.table.setCurrentIndex(nxt)
        self.table.scrollTo(nxt)

    def mark_reviewed(self):
        game, plugin, store = self.current
        n = core.mark_reviewed(store, self.selected_ids())
        self.statusBar().showMessage(tr("已標為已審閱 {} 句").format(n), 5000)

    def apply_same(self):
        rec = self._rec()
        if rec:
            n = core.apply_same(self.current[2], rec["id"])
            self.statusBar().showMessage(tr("已套用到 {} 句相同原文").format(n), 5000)

    def retranslate_current(self):
        rec = self._rec()
        if rec:
            self.start_translation(only=[rec["id"]], fresh=True)

    def retranslate_selected(self):
        ids = self.selected_ids()
        if ids:
            self.start_translation(only=ids, fresh=True)

    def run_qa(self):
        if not self.key:
            return
        n = core.qa(self.current[2])
        self.f_flag.setCurrentIndex(self.f_flag.findData("inconsistent") if n else 0)
        self._filter(flag=self.f_flag.currentData() or "")
        self.statusBar().showMessage(tr("全文 QA：{} 句不一致").format(n), 8000)

    # ---------- glossary tab ----------
    def load_glossary(self):
        if not self.key:
            return
        rows = core.glossary(self.current[2])
        self.gloss.setRowCount(0)
        for r in rows:
            self._gloss_row(r)
        self.style_edit.setPlainText(core.style(self.current[2]))

    def gloss_lookup(self):
        rows = sorted({i.row() for i in self.gloss.selectedIndexes()})
        if not rows:
            return
        term = self.gloss.item(rows[0], 0).text().strip()
        hits = core.dicts.lookup(term)
        if not hits:
            QMessageBox.information(self, "YakuPatch", tr("本地字典查無「{}」。可先在「字典設定」下載字典，"
                                                          "或用 VNDB／Wikidata 查詢。").format(term))
            return
        labels = [f"{h['dst'] or '—'}　{h['reading']} {h['type']} {h['lang']}　[{h['source']}]" for h in hits]
        choice, ok = QInputDialog.getItem(self, tr("查字典：{}").format(term), tr("選擇要填入的候選"), labels, 0, False)
        if ok:
            h = hits[labels.index(choice)]
            if h["dst"]:
                self.gloss.item(rows[0], 1).setText(h["dst"])
            note = "；".join(x for x in (h["reading"], h["type"]) if x)
            if note and not self.gloss.item(rows[0], 2).text():
                self.gloss.item(rows[0], 2).setText(note)
            self.gloss.item(rows[0], 3).setText(f"dict:{h['source']}")

    def _gloss_network(self, fn, label, what):
        if not self.key or QMessageBox.question(self, "YakuPatch", what) != QMessageBox.Yes:
            return
        game, plugin, store = self.current

        def done(job):
            if job.error:
                QMessageBox.warning(self, "YakuPatch", tr("{} 失敗：{}").format(label, job.error))
                return
            self.load_glossary()
            self.statusBar().showMessage(tr("{}：更新 {} 筆候選").format(label, job.result), 8000)

        self.run_bg(lambda job: fn(game, plugin, store), done, label)

    def gloss_vndb(self):
        self._gloss_network(core.dict_vndb, tr("VNDB 角色名單"),
                            tr("會把這款遊戲的標題送到 VNDB（vndb.org）查詢角色名單，資料以 ODbL 授權。繼續嗎？"))

    def gloss_wikidata(self):
        self._gloss_network(lambda game, plugin, store: core.dict_wikidata(store), tr("Wikidata 查譯名"),
                            tr("會把尚無譯名的術語候選送到 Wikidata（wikidata.org）查詢中文名稱。繼續嗎？"))

    def save_style(self):
        if self.key:
            core.save_style(self.current[2], self.style_edit.toPlainText())
            self.statusBar().showMessage(tr("翻譯風格已儲存"), 5000)

    def _gloss_row(self, r):
        n = self.gloss.rowCount()
        self.gloss.insertRow(n)
        for col, value in enumerate(r):
            item = QTableWidgetItem(value)
            if col == 3:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.gloss.setItem(n, col, item)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        check.setCheckState(Qt.Checked if r[3] == "manual" else Qt.Unchecked)
        self.gloss.setItem(n, 4, check)

    def gloss_add(self):
        self._gloss_row(["", "", "", "manual"])
        self.gloss.editItem(self.gloss.item(self.gloss.rowCount() - 1, 0))

    def gloss_delete(self):
        for row in sorted({i.row() for i in self.gloss.selectedIndexes()}, reverse=True):
            self.gloss.removeRow(row)

    def save_glossary(self):
        rows = []
        for n in range(self.gloss.rowCount()):
            cells = [(self.gloss.item(n, c).text() if self.gloss.item(n, c) else "").strip() for c in range(4)]
            if not any(cells[:3]):
                continue
            confirmed = self.gloss.item(n, 4).checkState() == Qt.Checked
            cells[3] = "manual" if confirmed else ("seed" if cells[3] == "manual" else cells[3] or "seed")
            rows.append(cells)
        try:
            core.save_glossary(self.current[2], rows)
        except ValueError as ex:
            QMessageBox.warning(self, "YakuPatch", str(ex))
            return
        self.statusBar().showMessage(tr("術語表已儲存"), 5000)
        self.load_glossary()

    # ---------- translate tab ----------
    def make_backend(self, name=None):
        name = name or self.backend_combo.currentData()     # batch workers pass the name: no widget reads off-thread
        if name == "openai":
            return OpenAICompat(core.openai_config()), name
        return (Codex(core.codex_config()) if name == "codex" else Sakura(core.sakura_config())), name

    def _backend_note(self):
        name = self.backend_combo.currentData()
        try:
            script = (core.openai_config() if name == "openai" else core.codex_config()).output_script
        except (TypeError, ValueError):
            script = "zh-Hant"
        self.script_note.setText(tr("Sakura 固定輸出簡體（不做繁簡轉換）") if name == "sakura"
                                 else tr("{} 輸出{}（可在設定修改）").format(self.backend_combo.currentText(),
                                                                       "繁體" if script == "zh-Hant" else "簡體"))
        self.backend_status.setText(self.backend_info.get(self.backend_combo.currentData(), ""))

    def test_backend(self):
        try:
            backend, name = self.make_backend()
        except (TypeError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("設定有誤：{}").format(ex))
            return

        def done(job):
            if job.error:
                self.backend_info[name] = tr("● 無法使用：{}").format(job.error)
            else:
                self.backend_info[name] = job.result
                self.backend_label.setText(tr("後端：{} {}").format(self.backend_combo.currentText(), job.result))
            self._backend_note()
            self._handle_user_error(job, name)

        def check(job):
            backend.prepare()
            if name == "codex":
                return tr("● {}（{}／{}）").format(backend.account()["email"] or tr("已登入"), backend.model,
                                                 backend.config.effort)
            return tr("● 已連線 {} 槽（{}）").format(backend.caps.max_parallel, backend.model)

        self.backend_status.setText(tr("測試中…"))
        self.run_bg(check, done, tr("測試連線"))

    def _scope(self):
        store = self.current[2]
        i = self.scope.checkedId()
        if i == 1:
            return None, self.selected_ids(), False
        if i == 2:
            return None, [r["id"] for r in store.records.values() if r["kind"] in ("name", "system")
                          and r["status"] in ("pending", "failed")], False
        return (self.trial.value() if i == 3 else None), None, False

    def start_translation(self, only=None, fresh=False):
        if not self.key:
            return
        if getattr(self, "estimating", False):
            return
        if self._row(self.key)["no_need"]:
            QMessageBox.information(self, "YakuPatch", tr("遊戲內已有中文，不需翻譯"))
            return
        limit = None
        if only is None:
            limit, only, fresh = self._scope()
            if only == []:
                QMessageBox.information(self, "YakuPatch", tr("沒有可翻譯的句子（請先在文本分頁選取）"))
                return
        try:
            backend, name = self.make_backend()
        except (TypeError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("設定有誤：{}").format(ex))
            return
        if name == "codex" and not core.load_config().get("codex", {}).get("privacy_ack"):
            if QMessageBox.question(self, "YakuPatch", tr("Codex 是實驗性後端（非公開端點）。翻譯時遊戲文本會送到 OpenAI。"
                                                          "\n確定要使用嗎？")) != QMessageBox.Yes:
                return
            core.save_config("codex", {**core.load_config().get("codex", {}), "privacy_ack": True})
        key, (game, plugin, store) = self.key, self.current

        def estimate(job):
            return core.estimate(store, plugin, backend, limit, only, fresh, prepare=True)

        def estimated(job):
            self.estimating = False
            self._translation_buttons()
            if job.error:
                self._error(job, tr("無法開始翻譯"))
                self._handle_user_error(job, name)
                return
            est = job.result
            if not est["items"]:
                QMessageBox.information(self, "YakuPatch", tr("沒有待翻的句子"))
                return
            msg = tr("待翻 {} 句（{:,} 字），快取命中 {}，約 {} 批，預估 {} 秒。").format(
                est["items"], est["characters"], est["cached"], est["batches"], est["seconds"])
            if backend.caps.metered:
                msg += "\n" + tr("預估輸入約 {:,} token、輸出約 {:,} token（粗估），會使用你的{}額度。").format(
                    est["input_tokens"], est["output_tokens"], tr(" Codex ") if name == "codex" else tr(" API "))
            if QMessageBox.question(self, tr("用量預估"), msg + "\n\n" + tr("開始翻譯？")) != QMessageBox.Yes:
                return
            label = tr("翻譯 {}").format(game.parent.name if game.name.lower() == "www" else game.name)

            def work(job):
                job.started = time.monotonic()
                return core.translate(store, plugin, backend, limit, progress=job.report, stop=job.stop,
                                      only=only, fresh=fresh)

            job = self.run_bg(work, lambda j: self._translated(j, name), label, key=name)
            self.translation = (job, key)
            self.tabs.setCurrentIndex(3)
            self._translation_buttons()
            self.action_box.hide()
            self._show_progress(job)
            self.refresh_library()

        self.estimating = True
        self._translation_buttons()
        self.run_bg(estimate, estimated, tr("用量預估"))

    def _show_progress(self, job):
        p = job.progress
        total = p.get("total") or 0
        done = p.get("translated", 0) + p.get("failed", 0)
        self.stages.set("翻譯", done, total, tr("{}/{} 句，失敗 {}").format(done, total, p.get("failed", 0)))
        elapsed = time.monotonic() - getattr(job, "started", time.monotonic())
        rate = done / elapsed * 60 if elapsed > 1 and done else 0
        left = (total - done) / rate if rate else 0
        state = {"queued": tr("排隊中（同一後端一次只跑一個工作）"), "running": tr("翻譯中"), "paused": tr("已暫停"),
                 "done": tr("完成"), "cancelled": tr("已取消"), "failed": tr("已停止")}[job.state]
        self.progress_label.setText(tr("{}：{}/{} 句，失敗 {}，每分鐘 {:.0f} 句，剩餘約 {:.0f} 分").format(
            state, done, total, p.get("failed", 0), rate, left))

    def _translated(self, job, name):
        self._show_progress(job)
        if job.state == "done":
            r = job.result
            self.statusBar().showMessage(tr("翻譯完成：成功 {}，失敗 {}").format(r["translated"], r["failed"]), 8000)
        elif job.error and not isinstance(job.error, NEEDS_USER):
            self._error(job, tr("翻譯中斷"))
        self._handle_user_error(job, name)
        self.refresh_library()
        self._refresh_counts()

    def _handle_user_error(self, job, name):
        """§11.11: AuthRequired / UsageLimit / ConfigError pause the job and offer the fix."""
        if not isinstance(job.error, NEEDS_USER):
            return
        self.action_label.setText(tr("工作已暫停，已完成的批次已保存：{}").format(job.error))
        if isinstance(job.error, AuthRequired) and name == "codex":
            self.action_button.setText(tr("重新登入"))
            self.fix = lambda: self.open_settings(tab=2)
        elif isinstance(job.error, AuthRequired) and name == "openai":
            self.action_button.setText(tr("開啟設定（API 金鑰）"))
            self.fix = lambda: self.open_settings(tab=3)
        elif isinstance(job.error, UsageLimit):
            self.action_button.setText(tr("稍後繼續"))
            self.fix = self.resume_job
        else:
            self.action_button.setText(tr("開啟設定（測試連線）"))
            self.fix = lambda: self.open_settings(tab={"sakura": 1, "codex": 2, "openai": 3}[name])
        self.action_box.show()

    def _translation_buttons(self):
        job = self.translation[0] if self.translation else None
        live = job is not None and job.state in ("queued", "running")
        estimating = getattr(self, "estimating", False)
        self.trial.setEnabled(self.scope.checkedId() == 3 and not live and not estimating)
        selected = len(self.selected_ids())
        self.scope.button(1).setText(tr("選取句子（{} 句）").format(selected))
        self.start_button.setText(tr("估算中…") if estimating else tr("翻譯進行中") if live else tr("開始翻譯"))
        self.start_button.setEnabled(bool(self.key) and not live and not estimating and
                                     (self.scope.checkedId() != 1 or selected > 0))
        self.job_pause.setEnabled(live)
        self.job_cancel.setEnabled(live)
        self.resume_button.setEnabled(job is not None and (job.state == "paused" or isinstance(job.error, NEEDS_USER)))

    def pause_job(self):
        if self.translation:
            self.translation[0].pause()
            self.progress_label.setText(tr("暫停中：等待進行中的批次完成（最長到逾時）…"))

    def cancel_job(self):
        if self.translation:
            self.translation[0].cancel()
            self.progress_label.setText(tr("取消中：不再送出新批次…"))

    def resume_job(self):
        if not self.translation:
            return
        old, key = self.translation
        name = old.key
        job = self.queue.resume(old)
        self._job_serial += 1
        job.gui_number = self._job_serial
        self.callbacks[job] = lambda j: self._translated(j, name)
        self.jobs.append(job)
        self.translation = (job, key)
        self.action_box.hide()
        self._job_changed(job)

    def load_history(self):
        rows = core.history(self.current[2])
        self.history.setRowCount(len(rows))
        for n, r in enumerate(reversed(rows)):
            values = [r["time"], r["run"], r["by"] or "—", str(r["items"]), f"{r['translated']}／{r['failed']}",
                      tr("已撤銷") if r["undone"] else ""]
            for col, value in enumerate(values):
                self.history.setItem(n, col, QTableWidgetItem(value))

    def undo_run(self):
        row = self.history.currentRow()
        if row < 0:
            return
        run = self.history.item(row, 1).text()
        if run.startswith("undo-") or self.history.item(row, 5).text():
            QMessageBox.information(self, "YakuPatch", tr("這筆紀錄不能再撤銷"))
            return
        if QMessageBox.question(self, "YakuPatch", tr("撤銷翻譯紀錄 {}？術語表與快取不會還原。").format(run)) != QMessageBox.Yes:
            return
        n = core.undo(self.current[2], run)
        self.statusBar().showMessage(tr("已撤銷 {} 筆修改").format(n), 5000)
        self._refresh_counts()

    def export_batches(self):
        game, plugin, store = self.current
        paths = core.export_batches(store, plugin)
        QMessageBox.information(self, "YakuPatch", tr("已匯出 {} 個分批檔到\n{}").format(len(paths), store.dir / "batches" / "out"))

    def import_batches(self):
        game, plugin, store = self.current
        files, _ = QFileDialog.getOpenFileNames(self, tr("選擇譯文檔或分享檔"), str(store.dir / "batches" / "in"),
                                                "JSONL (*.jsonl)")
        if not files:
            return
        try:
            run, ok, bad = core.import_batches(store, plugin, [Path(f) for f in files])
        except ValueError as ex:                     # a share file from another engine
            QMessageBox.warning(self, "YakuPatch", str(ex))
            return
        detail = "\n".join(f"{_REASON.get(k, k)}：{v}" for k, v in sorted(bad.items(), key=lambda kv: -kv[1]))
        QMessageBox.information(self, "YakuPatch", tr("匯入紀錄 {}：通過 {} 句，拒絕 {} 句").format(run, ok, sum(bad.values()))
                                + (f"\n{detail}" if detail else ""))

    def export_share(self):
        game, plugin, store = self.current
        name = game.parent.name if game.name.lower() == "www" else game.name
        path, _ = QFileDialog.getSaveFileName(self, tr("儲存譯文分享檔"), str(Path.home() / f"{name}.yakupatch.jsonl"),
                                              "JSONL (*.jsonl)")
        if path:
            n = core.export_share(store, plugin, Path(path))
            QMessageBox.information(self, "YakuPatch", tr("已匯出 {} 句譯文到\n{}\n（只含譯文與原文雜湊，不含遊戲原文；"
                                                          "對方用「匯入譯文」選這個檔即可）").format(n, path))

    # ---------- writeback tab ----------
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

    # ---------- settings ----------
    def open_settings(self, tab=None):
        dialog = SettingsDialog(self, tab)
        dialog.exec()
        self._backend_note()
