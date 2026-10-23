import os
import threading
import time
import unittest
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
        for p in (yes, info, warn):
            p.start()
            self.addCleanup(p.stop)
        self.win.lib.add(self.game)
        self.win.refresh_library()
        self.key = str(self.game)
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
            win.start_translation()
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
