"""界面公共部件：后台任务线程、指标卡、表格辅助、样式。"""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

STYLE = """
* { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "SimHei"; font-size: 10pt; }
QMainWindow, QWidget#page { background: #f4f6f9; }
QListWidget#nav { background: #1f3a5f; color: #dbe6f3; border: none; font-size: 11pt; outline: 0; }
QListWidget#nav::item { padding: 12px 18px; }
QListWidget#nav::item:selected { background: #2f5d94; color: white; border-left: 4px solid #6fb1ff; }
QLabel#title { font-size: 15pt; font-weight: bold; color: #1f3a5f; }
QLabel#sub { color: #6b7685; }
QFrame#card { background: white; border: 1px solid #e1e6ee; border-radius: 6px; }
QLabel#cardValue { font-size: 22pt; font-weight: bold; color: #1f3a5f; }
QLabel#cardLabel { color: #6b7685; }
QGroupBox { background: white; border: 1px solid #e1e6ee; border-radius: 6px; margin-top: 14px; padding: 8px; font-weight: bold; color: #1f3a5f; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTableWidget, QTableView { background: white; gridline-color: #edf0f4; border: 1px solid #e1e6ee; selection-background-color: #d6e6fa; selection-color: #1a1a1a; }
QHeaderView::section { background: #eef2f7; padding: 5px; border: none; border-right: 1px solid #e1e6ee; font-weight: bold; color: #33415c; }
QPushButton { background: white; border: 1px solid #c5ceda; border-radius: 4px; padding: 5px 14px; }
QPushButton:hover { background: #eef4fc; }
QPushButton#primary { background: #2f6fbf; color: white; border: 1px solid #2f6fbf; }
QPushButton#primary:hover { background: #255d9f; }
QPushButton:disabled { color: #a0a8b3; background: #f2f3f5; }
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit { background: white; border: 1px solid #c5ceda; border-radius: 4px; padding: 3px 6px; }
QProgressBar { border: 1px solid #d5dbe3; border-radius: 4px; background: #eef1f5; text-align: center; height: 16px; }
QProgressBar::chunk { background: #4a8ad8; border-radius: 3px; }
QTextBrowser { background: white; border: 1px solid #e1e6ee; }
QStatusBar { background: #e9edf3; color: #33415c; }
"""


class Worker(QThread):
    """在后台线程执行耗时任务，fn 的第一个参数为进度回调。"""

    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self._stop = False

    def stop(self):
        self._stop = True

    def should_stop(self) -> bool:
        return self._stop

    def run(self):
        try:
            res = self.fn(self.progress.emit, self.should_stop, *self.args, **self.kwargs)
            self.done.emit(res)
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


class Card(QFrame):
    def __init__(self, label: str, value: str = "0", hint: str = ""):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self.label = QLabel(label)
        self.label.setObjectName("cardLabel")
        self.value = QLabel(value)
        self.value.setObjectName("cardValue")
        self.hint = QLabel(hint)
        self.hint.setObjectName("sub")
        lay.addWidget(self.label)
        lay.addWidget(self.value)
        lay.addWidget(self.hint)

    def set(self, value, hint: str | None = None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(hint)


def page_header(title: str, sub: str = "") -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    t = QLabel(title)
    t.setObjectName("title")
    lay.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("sub")
        lay.addWidget(s)
    lay.addStretch(1)
    return w


def make_table(headers: list[str], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setSelectionMode(QTableWidget.SingleSelection)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setHighlightSections(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    if stretch is not None:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    return t


def fill_table(t: QTableWidget, rows: list[list], data: list | None = None, sortable: bool = True) -> None:
    t.setSortingEnabled(False)
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem("" if v is None else str(v))
            if j == 0 and data is not None:
                it.setData(Qt.UserRole, data[i])
            t.setItem(i, j, it)
    t.resizeColumnsToContents()
    for j in range(t.columnCount()):
        if t.columnWidth(j) > 360:
            t.setColumnWidth(j, 360)
    t.setSortingEnabled(sortable)


def row_data(t: QTableWidget):
    r = t.currentRow()
    if r < 0:
        return None
    it = t.item(r, 0)
    return it.data(Qt.UserRole) if it else None
