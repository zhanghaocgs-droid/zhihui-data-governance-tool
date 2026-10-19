"""主窗口。左侧八个功能模块，右侧页面，底部状态栏。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

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
            "工作台": lambda: WorkbenchPage(self.engine),
            "档案盘点": lambda: InventoryPage(self.engine, self.status),
            "规则管理": lambda: RulesPage(self.engine),
            "批量治理": lambda: BatchPage(self.engine, self.status),
            "人工复核": lambda: ReviewPage(self.engine, self.status),
            "质量检查": lambda: QcPage(self.engine, self.status),
            "成果输出": lambda: ExportPage(self.engine, self.status),
            "系统设置": lambda: SettingsPage(self.engine, self.set_operator, lambda: open_setup(self)),
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

    def set_workspace(self, ws: Path, operator: str):
        if Path(ws).resolve() != Path(self.engine.ws).resolve():
            self.engine.db.close()
            self.engine = Engine(ws, operator=operator)
            for name in list(self.pages):
                page = self.pages.pop(name)
                self.stack.removeWidget(page)
                page.deleteLater()
        self.engine.operator = operator
        self.update_right()
        self.switch(self.nav.currentItem().text() if self.nav.currentItem() else "工作台")
        self.status(f"工作区 {self.engine.ws}，操作人 {operator}")

    def update_right(self):
        self.lbl_right.setText(f"操作人 {self.engine.operator or '未设置'}　｜　规则 {self.engine.rule_tag}　｜　"
                               f"工作区 {self.engine.ws}")


class SetupDialog(QDialog):
    """工作区与操作人设置。首次启动时在主界面上弹出，系统设置页也可打开。"""

    def __init__(self, parent, workspace: str, operator: str, first: bool = False):
        super().__init__(parent)
        self.setWindowTitle("首次使用设置" if first else "工作区与操作人")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        if first:
            tip = QLabel("欢迎使用。请确认工作区目录并填写操作人姓名，之后可在 系统设置 中修改。\n"
                         "工作区存放数据库和成果区，需要足够的磁盘空间，不要选在移交资料所在目录内。")
            tip.setWordWrap(True)
            lay.addWidget(tip)
        form = QFormLayout()
        row = QHBoxLayout()
        self.ed_ws = QLineEdit(workspace)
        row.addWidget(self.ed_ws, 1)
        b = QPushButton("选择…")
        b.clicked.connect(self.pick)
        row.addWidget(b)
        form.addRow("工作区目录", row)
        self.ed_op = QLineEdit(operator)
        self.ed_op.setPlaceholderText("例如 张三，写入作业日志和人工确认记录")
        form.addRow("操作人姓名", self.ed_op)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.check)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作区目录", self.ed_ws.text())
        if d:
            self.ed_ws.setText(d)

    def check(self):
        if not self.ed_ws.text().strip():
            QMessageBox.warning(self, "设置", "请选择工作区目录")
            return
        if not self.ed_op.text().strip():
            QMessageBox.warning(self, "设置", "请填写操作人姓名")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.ed_ws.text().strip(), self.ed_op.text().strip()


def open_setup(window: "MainWindow", first: bool = False) -> None:
    st = QSettings("zbooksoft", "dgt")
    dlg = SetupDialog(window, str(window.engine.ws), window.engine.operator, first)
    if dlg.exec() != QDialog.Accepted:
        return
    ws, op = dlg.values()
    st.setValue("workspace", ws)
    st.setValue("operator", op)
    window.set_workspace(Path(ws), op)


def main(workspace: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    st = QSettings("zbooksoft", "dgt")
    saved = workspace or st.value("workspace") or ""
    op = st.value("operator") or ""
    first = not saved or not op
    engine = Engine(Path(saved or default_workspace()), operator=op)
    w = MainWindow(engine)
    w.show()
    if first:
        # 先显示主界面，再在其上弹出设置框
        QTimer.singleShot(300, lambda: open_setup(w, first=True))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
