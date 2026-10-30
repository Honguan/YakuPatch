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
    def setUp(self):
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {"YAKUPATCH_HOME": folder.name})
        env.start()
        self.addCleanup(env.stop)

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

    def test_library_filter_reuses_rows_and_open_store(self):
        with patch.object(self.win.lib, "row", side_effect=AssertionError("disk reload")):
            self.win.search.setText("missing")
            self.assertTrue(self.win.lib_list.item(0).isHidden())
            self.win.search.clear()
            self.assertEqual(self.win.lib_list.count(), 1)
            self.assertFalse(self.win.lib_list.item(0).isHidden())
        with patch("yakupatch.core.Store", side_effect=AssertionError("store reloaded")):
            self.win._refresh_counts()

    def test_filter_keeps_items_without_reloading_game(self):
        win = self.win
        item = win.lib_list.item(0)
        item.setSelected(True)
        with patch.object(win.lib_list, "clear", side_effect=AssertionError("list rebuilt")), \
             patch.object(win, "_open_game", side_effect=AssertionError("game reloaded")):
            win.lib_filter.setCurrentText("已寫回")
            self.assertTrue(item.isHidden())
            self.assertFalse(item.isSelected())
            win.lib_filter.setCurrentText("全部")
            self.assertIs(win.lib_list.item(0), item)
            self.assertFalse(item.isHidden())

    def test_job_controls_have_one_entry_and_overview_only_navigates(self):
        from PySide6.QtWidgets import QPushButton, QLabel
        win = self.win
        for text in ("暫停", "繼續", "取消"):
            self.assertEqual(sum(b.text() == text for b in win.findChildren(QPushButton)), 1)
        self.assertTrue(all(isinstance(label, QLabel) for label in win.step_labels))
        win.extract()
        self.idle()
        self.assertEqual(win.primary.text(), "前往翻譯")
        with patch.object(win, "start_translation", side_effect=AssertionError("translation started")):
            win.primary.click()
            self.assertEqual(win.tabs.currentIndex(), 3)

    def test_secondary_actions_are_grouped_and_still_callable(self):
        from PySide6.QtWidgets import QPushButton
        from unittest.mock import Mock
        buttons = self.win.findChildren(QPushButton)
        menus = {b.text(): b.menu() for b in buttons if b.menu()}
        for title, actions in {
            "遊戲庫管理": ["掃描資料夾", "重新掃描", "掃描資料夾清單", "移除不需翻譯", "移除不支援"],
            "更多文本操作": ["套用到相同原文", "全文 QA"],
            "查詢譯名": ["查字典", "VNDB 角色名單", "Wikidata 查譯名"],
            "匯入／匯出": ["匯出分批檔", "匯入譯文", "匯出分享檔"],
            "遊戲操作": ["還原遊戲", "啟動遊戲"],
        }.items():
            self.assertEqual([a.text() for a in menus[title].actions()], actions)
            self.assertFalse(any(b.text() in actions for b in buttons))
        self.assertFalse(any(b.text() == "重翻此句" for b in buttons))
        self.assertEqual(self.win.f_flag.findData("failed"), -1)
        callback = Mock()
        button = self.win._menu_button("test", (("action", callback),))
        button.menu().actions()[0].trigger()
        callback.assert_called_once()

    def test_translation_scope_and_estimate_guard(self):
        win = self.win
        self.assertFalse(win.trial.isEnabled())
        win.scope.button(3).setChecked(True)
        self.assertTrue(win.trial.isEnabled())
        win.scope.button(1).setChecked(True)
        self.assertFalse(win.start_button.isEnabled())
        self.assertIn("0 句", win.scope.button(1).text())
        win.estimating = True
        win._translation_buttons()
        self.assertEqual(win.start_button.text(), "估算中…")
        with patch.object(win, "make_backend", side_effect=AssertionError("duplicate estimate")):
            win.start_translation()

    def test_continuous_changes_refresh_without_reloading_history(self):
        win = self.win
        with patch.object(win, "_refresh_counts") as refresh, patch.object(win.model, "changed"):
            for _ in range(8):
                win._store_changed(self.key, [])
                QTest.qWait(60)
            self.assertGreaterEqual(refresh.call_count, 1)
            refresh.assert_called_with(history=False)
        win._dirty.stop()
        with patch.object(win, "load_history", side_effect=AssertionError("history reload")):
            win._refresh_counts(history=False)

    def test_progress_does_not_read_library_from_disk(self):
        from types import SimpleNamespace
        job = SimpleNamespace(state="running", key="test", label="test", progress={"total": 100})
        win = self.win
        win.jobs.append(job)
        try:
            with patch.object(win, "_row", side_effect=AssertionError("disk read")):
                for i in range(100):
                    job.progress["translated"] = i
                    win._job_changed(job)
            self.assertEqual(win.job_bar.value(), 99)
        finally:
            win.jobs.remove(job)

    def test_activity_matches_console_without_progress_spam(self):
        from unittest.mock import Mock
        job = Mock(state="running", label="提取", gui_number=7, gui_state=None, error=None,
                   key="test", progress={})
        win = self.win
        with patch("builtins.print") as output:
            win._job_changed(job)
            win._job_changed(job)
            self.assertEqual(output.call_count, 1)
            self.assertEqual(win.activity.toPlainText(), output.call_args.args[0])
            job.state = "failed"
            job.error = ValueError("test failure")
            win._job_changed(job)
            self.assertEqual(output.call_count, 2)
            self.assertIn("#7 提取：失敗", win.activity.toPlainText())
            self.assertIn("test failure", win.activity.toPlainText())

    def test_console_chunks_keep_partial_lines_and_limit_large_lines(self):
        win = self.win
        win.console_output("原生錯")
        win.console_output("誤\n下一行")
        self.assertEqual(win.activity.toPlainText(), "原生錯誤\n下一行")
        win.console_output("x" * 210000)
        self.assertLessEqual(win.activity.document().characterCount(), 200000)

    def test_activity_error_filter_preserves_full_text(self):
        win = self.win
        text = "normal output\nERROR native failure\n工作失敗\ncompleted"
        win.console_output(text)
        win.activity_errors.setChecked(True)
        block = win.activity.document().begin()
        visible = []
        while block.isValid():
            if block.isVisible():
                visible.append(block.text())
            block = block.next()
        self.assertEqual(visible, ["ERROR native failure", "工作失敗"])
        self.assertEqual(win.activity.toPlainText(), text)
        win.activity_errors.setChecked(False)
        self.assertTrue(win.activity.document().begin().isVisible())

    def test_export_activity_uses_full_saved_log(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        target = self.home / "export.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        export = Mock(side_effect=lambda path: path.write_text("normal\nERROR\n", encoding="utf8"))
        self.win.capture = SimpleNamespace(log_path=self.home / "source.log", export=export)
        self.win.activity_errors.setChecked(True)
        with patch("yakupatch.gui.window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.win.export_activity()
            self.idle()
        export.assert_called_once_with(target)
        self.assertEqual(target.read_text(encoding="utf8"), "normal\nERROR\n")

    def test_history_search_and_live_output_are_separate(self):
        from types import SimpleNamespace
        win = self.win
        self.home.mkdir(parents=True, exist_ok=True)
        old = self.home / "gui-20200101.log"
        old.write_text("normal\nERROR previous crash\n", encoding="utf8")
        win.capture = SimpleNamespace(log_path=self.home / "gui-current.log")
        win.refresh_logs()
        win.log_choice.setCurrentIndex(win.log_choice.findData(str(old)))
        self.idle()
        self.assertIn("previous crash", win.history_log.toPlainText())
        win.console_output("live output\n")
        self.assertNotIn("live output", win.history_log.toPlainText())
        win.log_search.setText("PREVIOUS")
        QTest.qWait(250)
        self.idle()
        self.assertEqual(win.history_log.toPlainText().strip(), "ERROR previous crash")
        win.refresh_logs()
        self.idle()
        self.assertEqual(win.log_choice.currentData(), str(old))
        target = self.home / "history-export.log"
        with patch("yakupatch.gui.window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            win.export_activity()
            self.idle()
        self.assertEqual(target.read_bytes(), old.read_bytes())
        win.log_choice.setCurrentIndex(0)
        self.assertIn("live output", win.activity.toPlainText())

    def test_previous_crash_jump_clears_filters_and_selects_error(self):
        win = self.win
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / "gui-crash.log"
        path.write_text("before\nERROR crash\nafter\n", encoding="utf8")
        win.show_previous_crash(path)
        self.assertFalse(win.crash_notice.isHidden())
        win.log_search.setText("missing")
        win.activity_errors.setChecked(True)
        win.view_previous_crash()
        self.idle()
        self.assertEqual(win.history_log.textCursor().selectedText(), "ERROR crash")
        self.assertIn("before", win.history_log.toPlainText())
        self.assertIn("after", win.history_log.toPlainText())
        self.assertFalse(win.activity_errors.isChecked())
        self.assertFalse(win.activity_dock.isHidden())

    def test_preview_and_apply_are_serialized_for_same_game(self):
        from yakupatch.writeback import ApplyPlan
        win=self.win;entered=threading.Event();release=threading.Event();written=threading.Event()
        def preview(*args):
            entered.set();release.wait(5);return ApplyPlan()
        try:
            with patch("yakupatch.core.preview", side_effect=preview), patch("yakupatch.core.apply", side_effect=lambda *a: written.set()):
                win.preview()
                self.assertTrue(entered.wait(2))
                win.plan=ApplyPlan();win.apply()
                QTest.qWait(50)
                self.assertFalse(written.is_set())
                release.set();self.idle()
                self.assertTrue(written.is_set())
        finally:
            release.set()

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
        from PySide6.QtWidgets import QPushButton
        button = next(b for b in win.findChildren(QPushButton) if b.text() == "一鍵提取→翻譯→寫回")
        with patch.object(MainWindow, "make_backend", lambda self, name=None: (IdsFake(), "sakura")):
            button.click()
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
        with patch.object(win, "run_bg", side_effect=lambda fn, done, label, **kw: pending.append(done)):
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
        self.assertEqual(win.primary.text(), "前往翻譯")
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
        self.assertIn("完成", win.step_labels[4].text())

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
