import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:          # GUI is optional for CLI-only installs
    QApplication = None

from yakupatch import core, engines
from yakupatch.exchange import Store
from yakupatch.extract import project_dir
from helpers import copy_game, fake_translate, snapshot
from test_codex import IdsFake

if QApplication:
    from yakupatch.gui.models import TagEdit, TextFilter, TextModel, delete_span, snap, tag_spans
    from yakupatch.gui.window import MainWindow
    app = QApplication.instance() or QApplication([])


def wait(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Launch(unittest.TestCase):
    def test_main_shows_window(self):
        from yakupatch import gui
        with patch("yakupatch.gui.window.MainWindow") as win, patch.object(QApplication, "exec", return_value=0):
            self.assertEqual(gui.main(), 0)
            win.return_value.show.assert_called_once()
            win.side_effect = RuntimeError("boom")
            with self.assertRaises(RuntimeError):          # reaches the launcher's console as a traceback
                gui.main()


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class TagLogic(unittest.TestCase):
    def test_spans_snap_and_delete(self):
        text = "a<0>bc⟦1⟧"
        self.assertEqual(tag_spans(text), [(1, 4), (6, 9)])
        self.assertEqual((snap(text, 2), snap(text, 2, forward=False), snap(text, 5)), (4, 1, 5))
        self.assertEqual(delete_span(text, 4, backward=True), (1, 4))
        self.assertEqual(delete_span(text, 1, backward=False), (1, 4))
        self.assertIsNone(delete_span(text, 5, backward=True))

    def test_editor_removes_whole_tag(self):
        edit = TagEdit()
        edit.setPlainText("你<0>好")
        cur = edit.textCursor()
        cur.setPosition(4)
        edit.setTextCursor(cur)
        QTest.keyClick(edit, Qt.Key_Backspace)
        self.assertEqual(edit.toPlainText(), "你好")
        edit.setPlainText("你<0>好")
        cur.setPosition(2)                                      # inside the tag: the cursor snaps out of it
        edit.setTextCursor(cur)
        self.assertIn(edit.textCursor().position(), (1, 4))


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Gui(unittest.TestCase):
    def setUp(self):
        self.game, self.home = copy_game("mz_min")
        env = patch.dict(os.environ, {"YAKUPATCH_HOME": str(self.home.parent / "yh")})
        env.start()
        self.addCleanup(env.stop)
        self.original = snapshot(self.game)
        self.win = MainWindow(core.Library(self.home.parent / "library.json"), home=self.home)
        yes = patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
        info = patch.object(QMessageBox, "information")
        warn = patch.object(QMessageBox, "warning", side_effect=lambda *a: self.fail(a[2]))
        from yakupatch import sakura_kit                                  # settings tab: no network or GPU probe
        catalogue = patch.object(sakura_kit, "catalogue", return_value=[sakura_kit.Model("r", "m.gguf", 2**30, "")])
        hardware = patch.object(sakura_kit, "hardware", return_value={"gpus": [("GPU", 8 * 2**30)], "ram": 16 * 2**30,
                                                                       "variant": "cuda"})
        for p in (yes, info, warn, catalogue, hardware):
            p.start()
            self.addCleanup(p.stop)
        self.key = self.win.lib.add(self.game)[0]
        self.win.refresh_library()
        self.win.select_game(self.key)

    def idle(self):
        self.assertTrue(wait(lambda: not self.win.callbacks))

    def test_model_filter_and_store_events(self):
        store = Store(project_dir(self.game, engines.PLUGINS[0], self.home))
        core.extract(self.game, engines.PLUGINS[0], store)
        model, proxy = TextModel(store), TextFilter()
        proxy.setSourceModel(model)
        self.assertEqual(model.rowCount(), len(store.records))
        proxy.set(status="pending")
        pending = proxy.rowCount()
        self.assertTrue(0 < pending <= len(store.records))
        rid = model.record(proxy.mapToSource(proxy.index(0, 0)).row())["id"]
        seen = []
        model.dataChanged.connect(lambda a, b: seen.append(a.row()))
        store.listeners.append(model.changed)
        store.update_many("t", [(rid, {"dst": "譯", "status": "translated"})])
        self.assertEqual(seen, [model.pos[rid]])
        proxy.set()                                             # re-filter: one fewer pending row
        self.assertEqual(proxy.rowCount(), pending - 1)
        proxy.set(status="", query="譯")
        self.assertEqual(proxy.rowCount(), 1)

    def test_one_click_batch_fills_every_stage(self):
        win = self.win
        win.lib_list.item(0).setSelected(True)
        win.batch_apply.setChecked(True)
        with patch.object(MainWindow, "make_backend", lambda self, name=None: (IdsFake(), "sakura")):
            win.batch_translate()
            self.idle()
        self.assertEqual([r["status"] for r in win.batch_job.result], ["ok"])
        self.assertNotEqual(snapshot(self.game), self.original)                   # extracted, translated, written back
        bars = win.stages.bars
        self.assertTrue(all(bars[n].maximum() and bars[n].value() == bars[n].maximum()
                            for n in ("批次", "提取", "翻譯", "寫回")))
        self.assertGreater(bars["整體"].value(), 0)

    def test_batch_defaults_to_translation_only_and_can_cancel(self):
        from PySide6.QtWidgets import QPushButton
        win = self.win
        win.lib_list.item(0).setSelected(True)
        button = next(b for b in win.findChildren(QPushButton) if b.text() == "批次翻譯選取遊戲")
        self.assertFalse(win.batch_apply.isChecked())
        with patch.object(MainWindow, "make_backend", lambda self, name=None: (IdsFake(), "sakura")):
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                button.click()
            self.assertIsNone(win.batch_job)
            button.click()
            self.idle()
        self.assertGreater(win.batch_job.result[0]["translated"], 0)
        self.assertEqual(win.batch_job.result[0]["files"], 0)
        self.assertEqual(snapshot(self.game), self.original)

    def test_remove_unsupported_keeps_other_games(self):
        win = self.win
        win.lib.games["Z:/broken"] = {"engine": "rpgmaker_mz", "lang": "", "probe": {}, "unsupported": "boom"}
        win.refresh_library()
        win.remove_where(lambda r: r["status"] == "不支援", "不支援")
        self.assertEqual(list(win.lib.games), [self.key])
        self.assertEqual(win.lib_list.count(), 1)

    def test_builtin_chinese_is_not_offered_for_translation(self):
        win = self.win
        win.lib.games[self.key]["builtin_chinese"] = True
        win.refresh_library()
        win._refresh_counts()
        self.assertTrue(win.rows[self.key]["no_need"])
        self.assertEqual(win.primary.text(), "啟動遊戲")
        self.assertIn("中文包", win.title.text())
        self.assertFalse(win.tabs.isTabEnabled(3))
        with patch.object(MainWindow, "make_backend", side_effect=AssertionError("Chinese game must be skipped")):
            win.batch_translate()
            win.start_translation()
        self.assertIsNone(win.batch_job)

    def test_chinese_mark_invalidates_preview_and_can_be_removed(self):
        win = self.win
        win.extract()
        self.idle()
        pending = []
        with patch.object(win, "run_bg", side_effect=lambda fn, done, label: pending.append(done)):
            win.preview()
        win.set_builtin_chinese(self.key, True)
        from types import SimpleNamespace
        pending[0](SimpleNamespace(error=None, result=SimpleNamespace(files=[object()])))
        self.assertIsNone(win.plan)
        self.assertFalse(win.apply_button.isEnabled())
        self.assertTrue(win.tabs.isTabEnabled(4))             # restore remains accessible
        with patch.object(core, "preview", side_effect=AssertionError("must not preview")), \
                patch.object(core, "apply", side_effect=AssertionError("must not write")):
            win.preview()
            win.apply()
        win.set_builtin_chinese(self.key, False)
        self.assertTrue(win.tabs.isTabEnabled(3))
        self.assertTrue(win.preview_button.isEnabled())

    def test_full_flow_through_gui(self):
        win = self.win
        win.extract()
        self.idle()
        self.assertGreater(win.model.rowCount(), 0)
        self.assertEqual(win.primary.text(), "開始翻譯")
        store = win.current[2]

        gate = threading.Event()

        class Slow(IdsFake):
            def complete(inner, messages, temperature=None):
                gate.wait(10)
                return super().complete(messages, temperature)

        with patch.object(MainWindow, "make_backend", lambda self: (Slow(), "sakura")):
            win.start_button.click()                            # the real button: clicked(bool) must not reach `only`
            self.assertTrue(wait(lambda: win.translation and win.translation[0].state == "running"))
            first = win.translation[0]
            win.tabs.setCurrentIndex(1)                         # browsing and editing stay responsive meanwhile
            rec = next(r for r in store.records.values() if r["status"] == "pending")
            win.table.selectRow(0)
            win.start_translation(only=[rec["id"]], fresh=True)
            self.assertTrue(wait(lambda: win.translation[0] is not first))
            self.assertEqual(win.translation[0].state, "queued")  # same backend: second job waits
            gate.set()
            self.idle()
        self.assertEqual(first.state, "done")
        self.assertEqual(core.counts(store)["pending"], 0)

        win.tabs.setCurrentIndex(1)
        win.table.selectRow(0)
        rec = win._rec()
        src = core.masked(rec, win.current[1])[0]
        win.editor.setPlainText(fake_translate(src) + "改")
        win.save_edit()
        self.assertEqual(store.records[rec["id"]]["status"], "reviewed")
        self.assertEqual(core.history(store)[-1]["by"], "manual")

        win.preview()
        self.idle()
        self.assertTrue(win.apply_button.isEnabled())
        win.apply()
        self.idle()
        self.assertNotEqual(snapshot(self.game), self.original)
        self.assertIn("✓", win.step_buttons[4].text())

        run = next(r["run"] for r in core.history(store) if r["by"].startswith("ids"))
        win.load_history()
        row = next(n for n in range(win.history.rowCount()) if win.history.item(n, 1).text() == run)
        win.history.selectRow(row)
        win.undo_run()
        self.assertTrue(next(r for r in core.history(store) if r["run"] == run)["undone"])

        win.restore()
        self.idle()
        self.assertEqual(snapshot(self.game), self.original)

    def test_qa_button_filters_inconsistent_and_style_saves(self):
        win = self.win
        win.extract()
        self.idle()
        store = win.current[2]
        a, b = [r for r in store.records.values() if r["status"] == "pending"][:2]
        store.update_many("t", [(a["id"], {"dst": "甲", "status": "translated"}),
                                (b["id"], {"dst": "乙", "status": "failed"})])
        store.update_many("t", [(b["id"], {"src": a["src"], "status": "translated", "dst": "完全不同的譯文"})])
        win.run_qa()
        self.assertEqual(win.f_flag.currentData(), "inconsistent")
        self.assertEqual(win.proxy.rowCount(), 2)
        win.table.selectRow(0)
        self.assertIn("不一致", win.edit_error.text())
        win.load_glossary()
        win.style_edit.setPlainText("語氣輕鬆")
        win.save_style()
        self.assertEqual(core.style(store), "語氣輕鬆")

    def test_settings_dictionary_tab_lists_sources(self):
        from yakupatch import dicts
        from yakupatch.gui.settings import SettingsDialog
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 5)
        self.assertEqual(dlg.dict_table.rowCount(), len(dicts.SOURCES))
        self.assertEqual(dlg.dict_table.item(0, 2).text(), "查詢後建立")

    def test_settings_open_local_sakura_until_sakura_is_set_up(self):
        from PySide6.QtWidgets import QTabWidget
        from yakupatch.gui.settings import SettingsDialog
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win)
            tabs = dlg.findChild(QTabWidget)
            self.assertEqual(tabs.currentIndex(), 1)
            self.assertEqual(tabs.tabText(0), "Sakura 翻譯連線")
            self.assertEqual(tabs.tabText(1), "本地模型管理（推薦）")
            core.save_config("sakura", {})
            self.assertEqual(SettingsDialog(self.win).findChild(QTabWidget).currentIndex(), 0)

    def test_local_sakura_defaults_and_saved_slots(self):
        from yakupatch.gui.settings import SettingsDialog
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 1)
            self.assertEqual((dlg.local_slots.value(), dlg.local_ctx.value()), (8, 2048))
            dlg.local_slots.setValue(2)
            dlg.local_ctx.setValue(4096)
            dlg.save()
            saved = SettingsDialog(self.win, 1)
            self.assertEqual((saved.local_slots.value(), saved.local_ctx.value()), (2, 4096))
            self.assertEqual((core.local_config().slots, core.local_config().ctx_per_slot), (2, 4096))

    def test_delete_downloaded_model_updates_settings_and_lists(self):
        from PySide6.QtWidgets import QPushButton
        from yakupatch import sakura_kit as kit
        from yakupatch.gui.settings import SettingsDialog
        models = kit.home() / "models"
        models.mkdir(parents=True)
        selected, other = models / "m.gguf", models / "other.gguf"
        selected.write_bytes(b"selected")
        other.write_bytes(b"other")
        core.save_config("sakura_local", {"model": selected.name, "autostart": True, "slots": 2})
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 1)
        self.idle()
        self.assertEqual(dlg.model_table.item(0, 3).text(), "已下載")
        next(b for b in dlg.findChildren(QPushButton) if b.text() == "刪除模型…").click()
        self.assertFalse(selected.exists())
        self.assertEqual(other.read_bytes(), b"other")
        self.assertEqual(dlg.model_table.item(0, 3).text(), "")
        self.assertEqual(dlg.local_model.count(), 1)
        self.assertEqual(dlg.local_model.currentText(), "")
        self.assertFalse(dlg.local_auto.isChecked())
        self.assertEqual(core.load_config()["sakura_local"], {"model": "", "autostart": False, "slots": 2})

    def test_delete_model_cancel_and_failure_keep_settings(self):
        from types import SimpleNamespace
        from yakupatch import sakura_kit as kit
        from yakupatch.gui.settings import SettingsDialog
        models = kit.home() / "models"
        models.mkdir(parents=True)
        selected = models / "m.gguf"
        selected.write_bytes(b"model")
        core.save_config("sakura_local", {"model": selected.name, "autostart": True})
        before = core.load_config()
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 1)
        self.idle()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
            dlg.delete_model()
            self.assertEqual(question.call_args.args[-1], QMessageBox.No)  # confirmation defaults to keeping the file
        with patch.object(kit.SERVER, "process", SimpleNamespace(poll=lambda: None)), \
                patch.object(QMessageBox, "warning") as warning:
            dlg.delete_model()
            self.assertIn("停止", warning.call_args.args[2])
        with patch.object(kit, "delete_model", side_effect=PermissionError("in use")), \
                patch.object(QMessageBox, "warning") as warning:
            dlg.delete_model()
            self.assertIn("無法刪除", warning.call_args.args[2])
        self.assertEqual(selected.read_bytes(), b"model")
        self.assertEqual(dlg.local_model.currentText(), selected.name)
        self.assertEqual(core.load_config(), before)
        with patch.object(core, "save_config", side_effect=OSError("read only")), \
                patch.object(QMessageBox, "warning") as warning:
            dlg.delete_model()
            self.assertIn("模型已刪除，但設定未儲存", warning.call_args.args[2])
        self.assertFalse(selected.exists())
        self.assertEqual(core.load_config(), before)
        self.assertEqual(dlg.local_model.currentText(), "")
        self.assertFalse(dlg.local_auto.isChecked())

    def test_delete_model_keeps_another_saved_model(self):
        from yakupatch import sakura_kit as kit
        from yakupatch.gui.settings import SettingsDialog
        models = kit.home() / "models"
        models.mkdir(parents=True)
        for name in ("m.gguf", "other.gguf"):
            (models / name).write_bytes(b"model")
        core.save_config("sakura_local", {"model": "other.gguf", "autostart": True})
        before = core.load_config()
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 1)
        self.idle()
        dlg.local_model.setCurrentText("m.gguf")
        dlg.delete_model()
        self.assertFalse((models / "m.gguf").exists())
        self.assertEqual(core.load_config(), before)
        self.assertEqual(dlg.local_model.currentText(), "other.gguf")

    def test_export_sakura_kit_is_a_small_zip_without_downloads(self):
        import zipfile
        from PySide6.QtWidgets import QFileDialog
        from yakupatch import sakura_kit as kit
        from yakupatch.gui.settings import SettingsDialog
        dest = self.home.parent / "simple kit.zip"
        with patch.object(SettingsDialog, "_account", return_value=""):
            dlg = SettingsDialog(self.win, 1)
        self.idle()
        dlg.model_table.selectRow(0)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(dest), "ZIP (*.zip)")), \
                patch.object(kit, "install_llama") as install, patch.object(kit, "download_model") as download:
            dlg.export_kit()
            self.idle()
            install.assert_not_called()
            download.assert_not_called()
        with zipfile.ZipFile(dest) as z:
            self.assertIn("start_sakura.bat", z.namelist())
            self.assertFalse(any(n.endswith((".exe", ".gguf")) for n in z.namelist()))

    def test_settings_openai_tab_saves_without_key_in_config(self):
        from yakupatch.backends import openai_compat
        from yakupatch.gui.settings import SettingsDialog
        with patch.object(SettingsDialog, "_account", return_value=""):
            SettingsDialog(self.win, 3).save()                        # untouched tab: other sections still save
            self.assertNotIn("openai", core.load_config())
            dlg = SettingsDialog(self.win, 3)
        dlg.oa_base.setText("https://api.example.com/v1")
        dlg.oa_model.setCurrentText("deepseek-chat")
        dlg.oa_key.setText("sk-secret")
        with patch.object(openai_compat, "set_key") as set_key:
            dlg.save()
        set_key.assert_called_once_with("https://api.example.com/v1", "sk-secret")
        self.assertEqual(core.openai_config().model, "deepseek-chat")
        self.assertNotIn("sk-secret", core.config_path().read_text("utf-8"))
        self.win.backend_combo.setCurrentIndex(self.win.backend_combo.findData("openai"))
        self.assertEqual(self.win.make_backend()[1], "openai")

    def test_usage_limit_pauses_with_action_and_resumes(self):
        win = self.win
        win.extract()
        self.idle()
        from yakupatch.backends import UsageLimit
        calls = []

        def action(texts, n):
            calls.append(n)
            if len(calls) == 2:
                raise UsageLimit("quota")

        backend = IdsFake(action)
        backend.caps = IdsFake.caps.__class__(io_format="json_ids", max_input_tokens=100_000, max_items=1,
                                              max_parallel=1)
        with patch.object(MainWindow, "make_backend", lambda self: (backend, "codex")):
            core.save_config("codex", {"privacy_ack": True})
            win.start_translation()
            self.idle()
            self.assertIsInstance(win.translation[0].error, UsageLimit)
            self.assertFalse(win.action_box.isHidden())
            self.assertTrue(win.resume_button.isEnabled())
            self.assertGreater(core.counts(win.current[2])["pending"], 0)   # rest stays pending
            win.resume_job()
            self.idle()
        self.assertEqual(win.translation[0].state, "done")
        self.assertEqual(core.counts(win.current[2])["pending"], 0)


if __name__ == "__main__":
    unittest.main()
