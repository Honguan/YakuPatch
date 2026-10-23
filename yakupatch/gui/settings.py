"""Settings dialog (§11.9, I2 scope): Sakura, Codex, advanced. Values go through core.save_config."""
import webbrowser
from dataclasses import fields
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import core
from ..backends import codex
from ..backends.codex import Codex, Credentials
from ..backends.sakura import Sakura, SakuraConfig
from .models import tr

SAKURA_LABELS = {"api_base": "API 位址", "version": "提示詞版本", "model": "模型（空白＝自動）", "timeout": "逾時秒數",
                 "parallel": "平行數（0＝讀伺服器槽數）", "retries": "重試次數", "max_tokens": "每次最大輸出 token",
                 "temperature": "溫度", "top_p": "top_p", "frequency_penalty": "頻率懲罰",
                 "force_glossary": "每批附上整份術語表", "enlarge_kana": "放大小假名", "repeat_threshold": "重複退化門檻"}


class SettingsDialog(QDialog):
    def __init__(self, window, tab=0):
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle(tr("設定"))
        self.resize(560, 520)
        try:
            self.config = core.load_config()
        except ValueError as ex:
            QMessageBox.warning(self, "YakuPatch", str(ex))
            self.config = {}
        tabs = QTabWidget()
        tabs.addTab(self._sakura(), tr("Sakura"))
        tabs.addTab(self._codex(), tr("Codex（實驗性）"))
        tabs.addTab(self._advanced(), tr("進階"))
        tabs.setCurrentIndex(tab)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(tabs)
        v.addWidget(box)

    # ---------- Sakura ----------
    def _sakura(self):
        w = QWidget()
        form = QFormLayout(w)
        saved = self.config.get("sakura", {})
        self.sakura = {}
        for f in fields(SakuraConfig):
            value = saved.get(f.name, f.default)
            if f.name == "version":
                edit = QComboBox()
                edit.addItems(["1.0", "0.9", "galtransl-v1"])
                edit.setCurrentText(str(value))
            elif f.type in (bool, "bool"):
                edit = QCheckBox(checked=bool(value))
            elif f.name in ("parallel", "retries", "max_tokens", "repeat_threshold"):
                edit = QSpinBox(minimum=0, maximum=1_000_000, value=int(value or 0))
            elif f.name in ("timeout", "temperature", "top_p", "frequency_penalty"):
                edit = QDoubleSpinBox(minimum=0, maximum=100_000, decimals=2, singleStep=0.05, value=float(value))
            else:
                edit = QLineEdit(str(value))
            self.sakura[f.name] = edit
            form.addRow(tr(SAKURA_LABELS.get(f.name, f.name)), edit)
        self.sakura_status = QLabel()
        form.addRow(QPushButton(tr("測試連線"), clicked=self.test_sakura), self.sakura_status)
        form.addRow(QLabel(tr("與命令列共用 ~/.yakupatch/config.json 的 sakura 設定。")))
        return w

    def sakura_values(self) -> dict:
        out = {}
        for name, edit in self.sakura.items():
            value = (edit.currentText() if isinstance(edit, QComboBox) else edit.isChecked() if isinstance(edit, QCheckBox)
                     else edit.value() if isinstance(edit, (QSpinBox, QDoubleSpinBox)) else edit.text().strip())
            out[name] = None if name == "parallel" and not value else value
        return out

    def test_sakura(self):
        try:
            backend = Sakura(SakuraConfig(**self.sakura_values()))
        except (TypeError, ValueError) as ex:
            self.sakura_status.setText(str(ex))
            return
        self.sakura_status.setText(tr("測試中…"))

        def check(job):
            backend.prepare()
            return tr("已連線：{}，{} 槽").format(backend.model, backend.caps.max_parallel)

        self.window_.run_bg(check, lambda job: self.sakura_status.setText(str(job.error or job.result)), tr("測試 Sakura"))

    # ---------- Codex ----------
    def _codex(self):
        w = QWidget()
        form = QFormLayout(w)
        saved = self.config.get("codex", {})
        self.account = QLabel(self._account())
        row = QHBoxLayout()
        row.addWidget(QPushButton(tr("登入"), clicked=self.login))
        row.addWidget(QPushButton(tr("登出"), clicked=self.logout))
        form.addRow(tr("帳號"), self.account)
        form.addRow("", row)
        self.model = QComboBox(editable=True)
        self.model.addItem(saved.get("model", ""))
        self.effort = QComboBox(editable=True)
        self.effort.addItems(["low", "medium", "high", "minimal"])
        self.effort.setCurrentText(saved.get("effort", "low"))
        models = QHBoxLayout()
        models.addWidget(self.model, 1)
        models.addWidget(QPushButton(tr("取得模型清單"), clicked=self.load_models))
        form.addRow(tr("模型（空白＝清單第一個）"), models)
        form.addRow(tr("推理等級"), self.effort)
        self.parallel = QSpinBox(minimum=1, maximum=16, value=int(saved.get("parallel", 4)))
        form.addRow(tr("平行數"), self.parallel)
        self.script = QComboBox()
        self.script.addItem(tr("繁體中文"), "zh-Hant")
        self.script.addItem(tr("簡體中文"), "zh-Hans")
        self.script.setCurrentIndex(0 if saved.get("output_script", "zh-Hant") == "zh-Hant" else 1)
        form.addRow(tr("輸出字體"), self.script)
        self.privacy = QCheckBox(tr("我了解翻譯時遊戲文本會送到 OpenAI"), checked=bool(saved.get("privacy_ack")))
        form.addRow(tr("隱私"), self.privacy)
        form.addRow(QLabel(tr("Codex 使用 ChatGPT 訂閱的非公開端點，可能隨時失效。憑證只存在系統認證管理員。")))
        return w

    def _account(self) -> str:
        try:
            info = Codex().account()
            return info["email"] or info["account_id"]
        except Exception as ex:          # keyring backends raise their own types; show them, never crash settings
            return str(ex)

    def login(self):
        def done(job):
            self.account.setText(str(job.error) if job.error else (job.result.get("email") or job.result["account_id"]))

        self.account.setText(tr("請在瀏覽器完成登入…"))
        self.window_.run_bg(lambda job: codex.login(Credentials(), open_url=webbrowser.open), done, tr("Codex 登入"))

    def logout(self):
        Credentials().clear()
        self.account.setText(tr("已登出（只刪除 YakuPatch 自己的憑證）"))

    def load_models(self):
        def done(job):
            if job.error:
                QMessageBox.warning(self, "YakuPatch", str(job.error))
                return
            current = self.model.currentText()
            self.model.clear()
            self.model.addItems(list(job.result))
            if current:
                self.model.setCurrentText(current)

        self.window_.run_bg(lambda job: Codex().list_models(), done, tr("取得模型"))

    def codex_values(self) -> dict:
        return {**self.config.get("codex", {}), "model": self.model.currentText().strip(),
                "effort": self.effort.currentText().strip(), "parallel": self.parallel.value(),
                "output_script": self.script.currentData(), "privacy_ack": self.privacy.isChecked()}

    # ---------- advanced ----------
    def _advanced(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QPushButton(tr("匯出診斷包（不含遊戲文本、token 與使用者名稱）"), clicked=self.diagnostics))
        v.addStretch()
        return w

    def diagnostics(self):
        win = self.window_
        if not win.key:
            QMessageBox.information(self, "YakuPatch", tr("請先選擇一款遊戲"))
            return
        game, plugin, store = win.current
        path, _ = QFileDialog.getSaveFileName(self, tr("儲存診斷包"), str(Path.home() / "yakupatch-diagnostics.zip"),
                                              "Zip (*.zip)")
        if path:
            core.diagnostics(game, plugin, store, Path(path))
            QMessageBox.information(self, "YakuPatch", tr("已匯出：{}").format(path))

    def save(self):
        try:
            core.save_config("sakura", self.sakura_values())
            core.save_config("codex", self.codex_values())
        except (TypeError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("設定未儲存：{}").format(ex))
            return
        self.accept()
