"""Qt models for the text table and the placeholder-tag editor (PROJECT_PLAN §11.5). No file I/O here."""
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QCoreApplication
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

TAG = re.compile(r"<\d+>|⟦\d+⟧")
STATUS = {"pending": "待翻", "translated": "已翻譯", "reviewed": "已審閱", "failed": "失敗", "skip": "不翻",
          "orphan": "已失效"}
KIND = {"dialogue": "對話", "choice": "選項", "name": "名稱", "system": "系統", "ui": "介面", "other": "其他"}
LONG = {"too_long", "length_ratio"}


def tr(text: str) -> str:
    return QCoreApplication.translate("YakuPatch", text)


def tag_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every placeholder; the editor treats each span as one character."""
    return [m.span() for m in TAG.finditer(text)]


def snap(text: str, pos: int, forward: bool = True) -> int:
    """Move a cursor position out of a placeholder."""
    for start, end in tag_spans(text):
        if start < pos < end:
            return end if forward else start
    return pos


def delete_span(text: str, pos: int, backward: bool) -> tuple[int, int] | None:
    """The placeholder a Backspace (or Delete) at `pos` removes as a whole, if any."""
    for start, end in tag_spans(text):
        if (backward and start < pos <= end) or (not backward and start <= pos < end):
            return start, end
    return None


class TextModel(QAbstractTableModel):
    """Virtual table over Store.records; rows are looked up live, so 100k rows cost one id list."""
    HEAD = ["#", "種類", "說話者", "原文", "譯文", "狀態"]

    def __init__(self, store=None):
        super().__init__()
        self.store, self.ids, self.pos = None, [], {}
        self.set_store(store)

    def set_store(self, store):
        self.beginResetModel()
        self.store = store
        self.ids = list(store.records) if store else []
        self.pos = {rid: i for i, rid in enumerate(self.ids)}
        self.endResetModel()

    def changed(self, ids):
        """Slot for Store change events (already moved to the GUI thread)."""
        if ids is None or any(i not in self.pos for i in ids):
            self.set_store(self.store)
            return
        rows = [self.pos[i] for i in ids]
        self.dataChanged.emit(self.index(min(rows), 0), self.index(max(rows), len(self.HEAD) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return tr(self.HEAD[section])
        return None

    def record(self, row: int) -> dict:
        return self.store.records[self.ids[row]]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rec = self.record(index.row())
        if role == Qt.DisplayRole:
            col = index.column()
            return [index.row() + 1, tr(KIND.get(rec["kind"], rec["kind"])), rec.get("speaker") or "—",
                    rec["src"].replace("\n", " ⏎ "), (rec.get("dst") or "").replace("\n", " ⏎ "),
                    tr(STATUS.get(rec["status"], rec["status"]))][col]
        if role == Qt.ForegroundRole and rec["status"] == "failed":
            return QColor("#c62828")
        if role == Qt.ForegroundRole and rec["status"] in ("skip", "orphan"):
            return QColor("#888888")
        if role == Qt.UserRole:
            return rec["id"]
        return None


class TextFilter(QSortFilterProxyModel):
    """Status / kind / file / flag filters and a substring search over source and translation."""

    def __init__(self):
        super().__init__()
        self.status = self.kind = self.file = self.flag = ""
        self.query = ""

    def set(self, **kw):
        modern = hasattr(self, "beginFilterChange")         # Qt 6.9+; invalidateFilter is deprecated there
        if modern:
            self.beginFilterChange()
        for k, v in kw.items():
            setattr(self, k, v)
        self.endFilterChange() if modern else self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        rec = self.sourceModel().record(row)
        if self.status and rec["status"] != self.status:
            return False
        if self.kind and rec["kind"] != self.kind:
            return False
        if self.file and not any(ref["file"] == self.file for ref in rec["refs"]):
            return False
        if self.flag == "long" and not LONG & set(rec.get("errors", [])):
            return False
        if self.flag == "uncertain" and "uncertain" not in rec.get("flags", []):
            return False
        if self.flag == "failed" and rec["status"] != "failed":
            return False
        if self.flag == "inconsistent" and "inconsistent" not in rec.get("flags", []):
            return False
        q = self.query
        return not q or q in rec["src"] or q in (rec.get("dst") or "")


class TagHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#cfe3ff"))
        fmt.setForeground(QColor("#0b3d91"))
        fmt.setFontWeight(700)
        for start, end in tag_spans(text):
            self.setFormat(start, end - start, fmt)


class TagEdit(QPlainTextEdit):
    """Placeholders look like tags and cannot be split: the cursor jumps over them, Backspace removes a whole tag."""

    def __init__(self):
        super().__init__()
        self.highlighter = TagHighlighter(self.document())
        self.cursorPositionChanged.connect(self._snap)
        self._last = 0

    def _snap(self):
        cur = self.textCursor()
        if cur.hasSelection():
            return
        pos = cur.position()
        fixed = snap(self.toPlainText(), pos, forward=pos >= self._last)
        if fixed != pos:
            cur.setPosition(fixed)
            self.setTextCursor(cur)
        self._last = fixed

    def keyPressEvent(self, event):
        cur = self.textCursor()
        if not cur.hasSelection() and event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            span = delete_span(self.toPlainText(), cur.position(), event.key() == Qt.Key_Backspace)
            if span:
                cur.setPosition(span[0])
                cur.setPosition(span[1], QTextCursor.KeepAnchor)
                cur.removeSelectedText()
                return
        super().keyPressEvent(event)
