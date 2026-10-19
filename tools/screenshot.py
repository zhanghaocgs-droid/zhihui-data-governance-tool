"""开发辅助：打开主窗口，逐个模块截图保存，供评审演示。

python tools/screenshot.py 工作区目录 输出目录
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dgt.gui.app import MainWindow  # noqa: E402
from dgt.gui.common import STYLE  # noqa: E402
from dgt.pipeline import Engine  # noqa: E402


def main():
    ws, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    eng = Engine(ws, operator="演示用户")
    w = MainWindow(eng)
    w.resize(1500, 900)
    w.show()
    plan = [("工作台", None), ("档案盘点", "inventory"), ("人工复核", "review"),
            ("质量检查", "qc"), ("规则管理", None)]

    def shoot(i=0):
        if i >= len(plan):
            app.quit()
            return
        name, action = plan[i]
        w.nav.setCurrentRow(["工作台", "档案盘点", "规则管理", "批量治理", "人工复核", "质量检查", "成果输出",
                             "系统设置"].index(name))
        page = w.pages[name]
        if action == "inventory":
            for r in range(page.table.rowCount()):
                if page.table.item(r, 0).text() == "Edmaker2017":
                    page.table.selectRow(r)
        elif action == "review":
            for r in range(page.queue.rowCount()):
                if page.queue.item(r, 0).text() == "6933":
                    page.queue.selectRow(r)
                    break
        elif action == "qc":
            page.run()

        def grab():
            w.grab().save(str(out / f"{i + 1:02d}_{name}.png"))
            shoot(i + 1)
        QTimer.singleShot(900, grab)

    QTimer.singleShot(800, shoot)
    app.exec()


if __name__ == "__main__":
    main()
