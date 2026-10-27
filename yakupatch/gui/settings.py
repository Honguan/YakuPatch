"""Settings dialog (§11.9, I2 scope): Sakura, local Sakura server, Codex, OpenAI-compatible, advanced. Values go through core.save_config."""
import webbrowser
from dataclasses import fields
from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import core, dicts, sakura_kit
from ..backends import codex, openai_compat
from ..backends.codex import Codex, Credentials
from ..backends.openai_compat import OpenAICompat, OpenAIConfig
from ..backends.sakura import Sakura, SakuraConfig
from .models import tr

SAKURA_LABELS = {"api_base": "API 位址", "version": "提示詞版本", "model": "模型（空白＝自動）", "timeout": "逾時秒數",
                 "parallel": "平行數（0＝讀伺服器槽數）", "retries": "重試次數", "max_tokens": "每次最大輸出 token",
                 "temperature": "溫度", "top_p": "top_p", "frequency_penalty": "頻率懲罰",
                 "force_glossary": "每批附上整份術語表", "enlarge_kana": "放大小假名", "repeat_threshold": "重複退化門檻"}


class SettingsDialog(QDialog):
    def __init__(self, window, tab=None):
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
        tabs.addTab(self._local(), tr("本地 Sakura（推薦）"))
        tabs.addTab(self._codex(), tr("Codex（實驗性）"))
        tabs.addTab(self._openai(), tr("OpenAI 相容"))
        tabs.addTab(self._advanced(), tr("進階"))
        tabs.addTab(self._dicts(), tr("字典"))
        if tab is None:                                   # until Sakura is set up, open the recommended local tab
            tab = 0 if "sakura" in self.config else 1
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
        form.addRow(QLabel(tr("推薦在「本地 Sakura」分頁下載模型並啟動伺服器，位址會自動填好；"
                              "只有連到自行架設或其他電腦的伺服器時才需要改這裡。"), wordWrap=True))
        for f in fields(SakuraConfig):
            value = saved.get(f.name, f.default)
            if f.name == "version":
                edit = QComboBox()
                edit.addItems(["1.0", "1.5", "0.9", "galtransl-v1"])
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

    # ---------- local Sakura server ----------
    def _local(self):
        w = QWidget()
        v = QVBoxLayout(w)
        saved = self.config.get("sakura_local", {})
        self.hw = sakura_kit.hardware()
        gib = sakura_kit.GIB
        gpu = "、".join(f"{n}（{m / gib:.0f} GB）" for n, m in self.hw["gpus"]) or tr("沒有偵測到獨立顯示卡")
        v.addWidget(QLabel(tr("本機：{}，記憶體 {:.0f} GB。llama.cpp 建議版本：{}").format(
            gpu, self.hw["ram"] / gib, sakura_kit.VARIANTS[self.hw["variant"]]), wordWrap=True))
        self.models = []
        self.model_table = QTableWidget(0, 4)
        self.model_table.setHorizontalHeaderLabels([tr(s) for s in ("模型", "大小", "本機推薦", "狀態")])
        self.model_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.model_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.model_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        v.addWidget(self.model_table)
        row = QHBoxLayout()
        row.addWidget(QPushButton(tr("重新整理模型清單"), clicked=lambda: self.load_catalogue(refresh=True)))
        row.addWidget(QPushButton(tr("下載選取的模型"), clicked=self.download_model))
        self.llama_label = QLabel()
        row.addWidget(self.llama_label, 1)
        row.addWidget(QPushButton(tr("下載最新 llama.cpp"), clicked=self.download_llama))
        v.addLayout(row)
        form = QFormLayout()
        self.local_model = QComboBox()
        self.local_port = QSpinBox(minimum=1, maximum=65535, value=int(saved.get("port", 8080)))
        self.local_slots = QSpinBox(minimum=1, maximum=16, value=int(saved.get("slots", sakura_kit.LocalConfig().slots)))
        self.local_ctx = QSpinBox(minimum=512, maximum=32768, singleStep=512, value=int(saved.get("ctx_per_slot", 2048)))
        self.local_ngl = QSpinBox(minimum=0, maximum=999, value=int(saved.get("gpu_layers", 999)))
        self.local_lan = QCheckBox(tr("允許區網連線（其他電腦可使用這台的模型；請只在信任的網路開啟）"),
                                   checked=bool(saved.get("lan")))
        self.local_auto = QCheckBox(tr("啟動 YakuPatch 時自動開啟伺服器"), checked=bool(saved.get("autostart")))
        model_row = QHBoxLayout()
        model_row.addWidget(self.local_model, 1)
        model_row.addWidget(QPushButton(tr("刪除模型…"), clicked=self.delete_model))
        form.addRow(tr("使用的模型（已下載）"), model_row)
        for label, widget in ((tr("連接埠"), self.local_port),
                              (tr("平行槽數"), self.local_slots), (tr("每槽 context"), self.local_ctx),
                              (tr("GPU 層數（999＝全部）"), self.local_ngl), ("", self.local_lan), ("", self.local_auto)):
            form.addRow(label, widget)
        v.addLayout(form)
        row = QHBoxLayout()
        self.server_status = QLabel(tr("● 執行中") if sakura_kit.SERVER.running else tr("○ 未啟動"))
        row.addWidget(QPushButton(tr("啟動伺服器"), clicked=self.start_server))
        row.addWidget(QPushButton(tr("停止伺服器"), clicked=self.stop_server))
        row.addWidget(self.server_status, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.kit_vram = QSpinBox(minimum=0, maximum=192, suffix=" GB",
                                 value=round(max((m for _, m in self.hw["gpus"]), default=0) / gib))
        self.kit_vram.valueChanged.connect(self._fill_models)
        self.kit_variant = QComboBox()
        for key, name in sakura_kit.VARIANTS.items():
            self.kit_variant.addItem(tr(name), key)
        row.addWidget(QLabel(tr("其他電腦：顯示卡記憶體")))
        row.addWidget(self.kit_vram)
        row.addWidget(self.kit_variant)
        row.addWidget(QPushButton(tr("匯出簡易啟動包 ZIP…"), clicked=self.export_kit))
        v.addLayout(row)
        self._fill_installed(saved.get("model", ""))
        self._fill_models()
        self.load_catalogue()
        return w

    def _fill_installed(self, current=""):
        self.local_model.clear()
        for path in sakura_kit.installed():
            self.local_model.addItem(path.name)
        if current:
            self.local_model.setCurrentText(current)
        exe = sakura_kit.installed_llama()
        self.llama_label.setText(tr("llama.cpp：{}").format(exe.name if exe else tr("尚未下載")))

    def _fill_models(self):
        """The recommendation column follows this PC, or the VRAM typed for another PC's kit."""
        gib = sakura_kit.GIB
        local = max((m for _, m in self.hw["gpus"]), default=0)
        target = self.kit_vram.value() * gib
        here = abs(target - local) < gib
        labels = sakura_kit.recommend(self.models, target, self.hw["ram"] if here else 0)
        self.model_table.setHorizontalHeaderItem(2, QTableWidgetItem(
            tr("本機推薦") if here else tr("{} GB 推薦").format(self.kit_vram.value())))
        have = {p.name for p in sakura_kit.installed()}
        self.model_table.setRowCount(len(self.models))
        for i, m in enumerate(self.models):
            for col, text in enumerate((m.file + (f"（{tr(m.note)}）" if m.note else ""), f"{m.size / gib:.1f} GB",
                                        tr(labels[m.file]), tr("已下載") if m.file in have else "")):
                self.model_table.setItem(i, col, QTableWidgetItem(text))

    def load_catalogue(self, refresh=False):
        def done(job):
            if job.error:
                self.server_status.setText(tr("無法取得模型清單：{}").format(job.error))
                return
            self.models = job.result
            self._fill_models()

        if refresh:
            sakura_kit.catalogue.cache_clear()
        self.window_.run_bg(lambda job: sakura_kit.catalogue(), done, tr("取得 Sakura 模型清單"))

    def _selected_model(self):
        rows = {i.row() for i in self.model_table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, "YakuPatch", tr("請先在清單選一個模型"))
            return None
        return self.models[rows.pop()]

    def _download(self, label, fn):
        def work(job):
            return fn(lambda d, t, *_: job.report({"translated": d >> 20, "failed": 0, "total": t >> 20}), job.stop)

        def done(job):
            if job.error:
                QMessageBox.warning(self, "YakuPatch", str(job.error))
            self._fill_installed(self.local_model.currentText())
            self._fill_models()

        self.window_.run_bg(work, done, label)

    def download_model(self):
        model = self._selected_model()
        if model:
            self._download(tr("下載 {}（MB）").format(model.file),
                           lambda progress, stop: sakura_kit.download_model(model, progress=progress, stop=stop))

    def download_llama(self):
        variant = self.hw["variant"]
        self._download(tr("下載 llama.cpp（MB）"),
                       lambda progress, stop: sakura_kit.install_llama(variant, progress=progress, stop=stop))

    def delete_model(self):
        name = self.local_model.currentText()
        if not name:
            QMessageBox.information(self, "YakuPatch", tr("請先選取已下載的模型"))
            return
        if sakura_kit.SERVER.running:
            QMessageBox.warning(self, "YakuPatch", tr("請先停止本地 Sakura 伺服器，再刪除模型"))
            return
        if QMessageBox.question(self, tr("刪除模型"), tr("永久刪除已下載的「{}」？需要時可重新下載。").format(name),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            saved = core.load_config().get("sakura_local", {})
            sakura_kit.delete_model(name)
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("無法刪除模型：{}").format(ex))
            return
        if saved.get("model") == name:
            saved = {**saved, "model": "", "autostart": False}
            self.local_auto.setChecked(False)
            try:
                core.save_config("sakura_local", saved)
            except (OSError, ValueError, TypeError) as ex:
                QMessageBox.warning(self, "YakuPatch", tr("模型已刪除，但設定未儲存：{}").format(ex))
        self._fill_installed(saved.get("model", ""))
        if not saved.get("model"):
            self.local_model.setCurrentIndex(-1)
        self._fill_models()
        self.server_status.setText(tr("已刪除模型：{}").format(name))

    def local_values(self) -> dict:
        return {"model": self.local_model.currentText(), "port": self.local_port.value(),
                "gpu_layers": self.local_ngl.value(), "slots": self.local_slots.value(),
                "ctx_per_slot": self.local_ctx.value(), "lan": self.local_lan.isChecked(),
                "autostart": self.local_auto.isChecked()}

    def start_server(self):
        try:
            cfg = sakura_kit.LocalConfig(**self.local_values())
        except (TypeError, ValueError) as ex:
            self.server_status.setText(str(ex))
            return
        if not cfg.model:
            self.server_status.setText(tr("請先下載並選擇模型"))
            return
        self.server_status.setText(tr("啟動中…（大模型載入可能要一分鐘）"))

        def done(job):
            if job.error:
                self.server_status.setText(tr("○ 啟動失敗：{}").format(job.error))
                return
            self.sakura["api_base"].setText(job.result)               # the Sakura tab now points at this server
            self.sakura["version"].setCurrentText(sakura_kit.version_of(cfg.model))
            self.server_status.setText(tr("● 執行中：{}（{}）").format(job.result, cfg.model))
            try:
                core.save_config("sakura_local", self.local_values())
                core.save_config("sakura", self.sakura_values())
            except (TypeError, ValueError) as ex:
                QMessageBox.warning(self, "YakuPatch", tr("設定未儲存：{}").format(ex))

        self.window_.run_bg(lambda job: sakura_kit.SERVER.start(cfg, stop=job.stop), done, tr("啟動 Sakura 伺服器"))

    def stop_server(self):
        sakura_kit.SERVER.stop()
        self.server_status.setText(tr("○ 已停止"))

    def export_kit(self):
        model = self._selected_model()
        if not model:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("匯出簡易啟動包（首次啟動時下載模型）"),
                                             f"Sakura_{Path(model.file).stem}.zip", "ZIP (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        variant = self.kit_variant.currentData()

        def done(job):
            if job.error:
                QMessageBox.warning(self, "YakuPatch", tr("匯出失敗：{}").format(job.error))
            else:
                QMessageBox.information(self, "YakuPatch", tr("已匯出：{}\n解壓縮後啟動，才會下載 llama.cpp 與模型。").format(job.result))

        self.window_.run_bg(lambda job: sakura_kit.export_kit(Path(path), model, variant, stop=job.stop), done,
                            tr("匯出 Sakura 簡易啟動包 ZIP"))

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

    # ---------- OpenAI-compatible (F5) ----------
    def _openai(self):
        w = QWidget()
        form = QFormLayout(w)
        saved = self.config.get("openai", {})
        self.oa_base = QLineEdit(saved.get("api_base", ""), placeholderText="https://api.deepseek.com/v1")
        form.addRow(tr("API 位址"), self.oa_base)
        self.oa_key = QLineEdit(echoMode=QLineEdit.Password)
        self.oa_key.setPlaceholderText(tr("已存金鑰，留空＝不變") if saved.get("api_base") and openai_compat.get_key(
            saved["api_base"]) else tr("本機伺服器可留空"))
        form.addRow(tr("API 金鑰"), self.oa_key)
        self.oa_model = QComboBox(editable=True)
        self.oa_model.addItem(saved.get("model", ""))
        models = QHBoxLayout()
        models.addWidget(self.oa_model, 1)
        models.addWidget(QPushButton(tr("取得模型清單"), clicked=self.load_openai_models))
        form.addRow(tr("模型（空白＝清單第一個）"), models)
        self.oa_parallel = QSpinBox(minimum=1, maximum=64, value=int(saved.get("parallel", 4)))
        form.addRow(tr("平行數"), self.oa_parallel)
        self.oa_temperature = QDoubleSpinBox(minimum=0, maximum=2, decimals=2, singleStep=0.05,
                                             value=float(saved.get("temperature", 0.3)))
        form.addRow(tr("溫度"), self.oa_temperature)
        self.oa_script = QComboBox()
        self.oa_script.addItem(tr("繁體中文"), "zh-Hant")
        self.oa_script.addItem(tr("簡體中文"), "zh-Hans")
        self.oa_script.setCurrentIndex(0 if saved.get("output_script", "zh-Hant") == "zh-Hant" else 1)
        form.addRow(tr("輸出字體"), self.oa_script)
        self.oa_json = QCheckBox(tr("要求 JSON 輸出（伺服器不支援時自動關閉）"), checked=bool(saved.get("json_mode", True)))
        form.addRow("", self.oa_json)
        self.oa_status = QLabel()
        form.addRow(QPushButton(tr("測試連線"), clicked=self.test_openai), self.oa_status)
        form.addRow(QLabel(tr("適用 DeepSeek、OpenAI、OpenRouter、Ollama、LM Studio 等。金鑰只存在系統認證管理員；"
                              "雲端 API 會收到遊戲文本並計費。")))
        return w

    def openai_values(self) -> dict:
        return {**self.config.get("openai", {}), "api_base": self.oa_base.text().strip(),
                "model": self.oa_model.currentText().strip(), "parallel": self.oa_parallel.value(),
                "temperature": self.oa_temperature.value(), "output_script": self.oa_script.currentData(),
                "json_mode": self.oa_json.isChecked()}

    def _openai_backend(self) -> OpenAICompat:
        config = OpenAIConfig(**self.openai_values())
        return OpenAICompat(config, key=self.oa_key.text().strip() or None)

    def load_openai_models(self):
        try:
            backend = self._openai_backend()
        except (TypeError, ValueError) as ex:
            self.oa_status.setText(str(ex))
            return

        def done(job):
            if job.error:
                self.oa_status.setText(str(job.error))
                return
            current = self.oa_model.currentText()
            self.oa_model.clear()
            self.oa_model.addItems(list(job.result))
            if current:
                self.oa_model.setCurrentText(current)

        self.window_.run_bg(lambda job: backend.list_models(), done, tr("取得模型"))

    def test_openai(self):
        try:
            backend = self._openai_backend()
        except (TypeError, ValueError) as ex:
            self.oa_status.setText(str(ex))
            return
        self.oa_status.setText(tr("測試中…"))

        def check(job):
            backend.prepare()
            backend.complete([{"role": "user", "content": '只回覆這個 JSON：{"ok":true}'}])   # model name and key are only checked here
            return tr("已連線：{}").format(backend.model)

        self.window_.run_bg(check, lambda job: self.oa_status.setText(str(job.error or job.result)), tr("測試 API"))

    # ---------- dictionaries (§6.10) ----------
    def _dicts(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.dict_table = QTableWidget(0, 5)
        self.dict_table.setHorizontalHeaderLabels([tr(s) for s in ("字典", "授權", "狀態", "筆數", "更新日期")])
        self.dict_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dict_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.dict_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.dict_table)
        row = QHBoxLayout()
        for text, slot in ((tr("下載／更新"), self.dict_download), (tr("刪除"), self.dict_remove),
                           (tr("匯入自備詞庫（CSV／TSV／JSON）"), self.dict_import)):
            row.addWidget(QPushButton(text, clicked=slot))
        v.addLayout(row)
        credits = QLabel("\n".join(f"・{s['title']}（{s['license']}）：{s['credit']}" for s in dicts.SOURCES.values()),
                         wordWrap=True)
        credits.setStyleSheet("color: #666")
        v.addWidget(QLabel(tr("字典資料不隨程式散佈，只在你按下下載或查詢時才連線取得；譯名一律需人工確認。")))
        v.addWidget(credits)
        self.load_dicts()
        return w

    def load_dicts(self):
        self.dict_rows = dicts.info()
        self.dict_table.setRowCount(0)
        for d in self.dict_rows:
            n = self.dict_table.rowCount()
            self.dict_table.insertRow(n)
            state = tr("已安裝") if d["installed"] else (tr("未下載") if d["name"] in dicts.DOWNLOADABLE else tr("查詢後建立"))
            for col, value in enumerate((d["title"], d["license"], state, str(d["entries"] or ""), d["date"])):
                self.dict_table.setItem(n, col, QTableWidgetItem(value))

    def _dict_selected(self):
        rows = self.dict_table.selectionModel().selectedRows()
        return self.dict_rows[rows[0].row()]["name"] if rows else None

    def dict_download(self):
        name = self._dict_selected()
        if name not in dicts.DOWNLOADABLE:
            QMessageBox.information(self, "YakuPatch", tr("請選擇 JMnedict 或 Japanese Personal Name Dataset。"
                                                          "VNDB 與 Wikidata 在術語分頁依遊戲查詢。"))
            return
        if QMessageBox.question(self, "YakuPatch", tr("從官方來源下載「{}」並轉成本地詞庫？").format(
                dicts.SOURCES[name]["title"])) != QMessageBox.Yes:
            return

        def done(job):
            if job.error:
                QMessageBox.warning(self, "YakuPatch", tr("下載失敗：{}").format(job.error))
            self.load_dicts()

        self.window_.run_bg(lambda job: dicts.download(name), done, tr("下載字典"))

    def dict_remove(self):
        name = self._dict_selected()
        if name and QMessageBox.question(self, "YakuPatch", tr("刪除本地的「{}」？").format(
                dicts.SOURCES[name]["title"])) == QMessageBox.Yes:
            dicts.remove(name)
            self.load_dicts()

    def dict_import(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("匯入自備詞庫"), "", "CSV / TSV / JSON (*.csv *.tsv *.txt *.json)")
        if not path:
            return
        try:
            n = dicts.import_user(Path(path))
        except (OSError, ValueError, UnicodeError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("匯入失敗：{}").format(ex))
            return
        QMessageBox.information(self, "YakuPatch", tr("已匯入 {} 筆").format(n))
        self.load_dicts()

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
            core.save_config("sakura_local", self.local_values())
            values = self.openai_values()
            if values["api_base"] or "openai" in self.config:      # an untouched tab needs no address
                core.save_config("openai", values)
        except (TypeError, ValueError) as ex:
            QMessageBox.warning(self, "YakuPatch", tr("設定未儲存：{}").format(ex))
            return
        if self.oa_key.text().strip():
            try:
                openai_compat.set_key(values["api_base"], self.oa_key.text().strip())
            except Exception as ex:          # keyring backends raise their own types
                QMessageBox.warning(self, "YakuPatch", tr("金鑰未儲存：{}").format(ex))
                return
        self.accept()
