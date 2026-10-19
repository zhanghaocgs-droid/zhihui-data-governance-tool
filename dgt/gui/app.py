"""主窗口。左侧八个功能模块，右侧页面，底部状态栏。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QMainWindow, QStackedWidget, QWidget)

from .. import PROGRAM_NAME, __version__
from ..pipeline import Engine
from .common import STYLE
from .inventory import InventoryPage
from .others import BatchPage, ExportPage, QcPage, RulesPage, SettingsPage
from .review import ReviewPage
from .workbench import WorkbenchPage

MODULES = ["工作台", "档案盘点", "规则管理", "批量治理", "人工复核", "质量检查", "成果输出", "系统设置"]


def default_workspace() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return base / "workspace"


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle(f"{PROGRAM_NAME} V{__version__}")
        self.resize(1440, 880)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(170)
        self.nav.addItems(MODULES)
        lay.addWidget(self.nav)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.pages = {}
        self.builders = {
            "工作台": lambda: WorkbenchPage(engine),
            "档案盘点": lambda: InventoryPage(engine, self.status),
            "规则管理": lambda: RulesPage(engine),
            "批量治理": lambda: BatchPage(engine, self.status),
            "人工复核": lambda: ReviewPage(engine, self.status),
            "质量检查": lambda: QcPage(engine, self.status),
            "成果输出": lambda: ExportPage(engine, self.status),
            "系统设置": lambda: SettingsPage(engine, self.set_operator),
        }
        self.nav.currentTextChanged.connect(self.switch)
        self.lbl_right = QLabel()
        self.statusBar().addPermanentWidget(self.lbl_right)
        self.update_right()
        self.nav.setCurrentRow(0)

    def switch(self, name: str):
        # 页面每次进入都重建，保证数据为最新
        old = self.pages.pop(name, None)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()
        page = self.builders[name]()
        self.pages[name] = page
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def status(self, text: str):
        self.statusBar().showMessage(text, 15000)

    def set_operator(self, name: str):
        self.engine.operator = name
        QSettings("zbooksoft", "dgt").setValue("operator", name)
        self.update_right()

    def update_right(self):
        self.lbl_right.setText(f"操作人 {self.engine.operator or '未设置'}　｜　规则 {self.engine.rule_tag}　｜　"
                               f"工作区 {self.engine.ws}")


def main(workspace: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    st = QSettings("zbooksoft", "dgt")
    ws = workspace or st.value("workspace") or ""
    if not ws:
        ws = QFileDialog.getExistingDirectory(None, "选择工作区目录（存放数据库与成果区）", str(default_workspace().parent))
        if not ws:
            ws = str(default_workspace())
        st.setValue("workspace", ws)
    op = st.value("operator") or ""
    if not op:
        op, ok = QInputDialog.getText(None, PROGRAM_NAME, "请输入操作人姓名")
        if ok and op:
            st.setValue("operator", op)
    engine = Engine(Path(ws), operator=op or "")
    w = MainWindow(engine)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
