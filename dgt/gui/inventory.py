"""档案盘点：选择数据目录、导入交接清单、扫描对账、时期识别、盘点台账导出、问题登记。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.db import loads
from .common import Worker, fill_table, make_table, page_header, row_data

RECON = ["全部", "一致", "数量差异", "清单外", "目录缺失", "文件不可读"]
PERIODS = ["全部", "现行", "近期", "清欠", "早期", "待人工判别", "未知"]


class InventoryPage(QWidget):
    def __init__(self, engine, status_cb=None):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        self.status_cb = status_cb or (lambda s: None)
        self.worker = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(page_header("档案盘点", "扫描档号文件夹，与交接清单对账并识别时期"))

        bar = QHBoxLayout()
        bar.addWidget(QLabel("数据目录"))
        self.ed_root = QLineEdit(engine.db.get_meta("last_data_root", ""))
        self.ed_root.setPlaceholderText("移交资料所在目录，只读访问")
        bar.addWidget(self.ed_root, 2)
        b = QPushButton("选择…")
        b.clicked.connect(self.pick_root)
        bar.addWidget(b)
        b = QPushButton("导入交接清单")
        b.clicked.connect(self.import_handover)
        bar.addWidget(b)
        b = QPushButton("导入项目对照表")
        b.clicked.connect(self.import_projects)
        bar.addWidget(b)
        self.btn_scan = QPushButton("开始扫描")
        self.btn_scan.setObjectName("primary")
        self.btn_scan.clicked.connect(self.start_scan)
        bar.addWidget(self.btn_scan)
        self.btn_run = QPushButton("治理选中档")
        self.btn_run.clicked.connect(self.run_selected)
        bar.addWidget(self.btn_run)
        self.btn_stop = QPushButton("中止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        bar.addWidget(self.btn_stop)
        b = QPushButton("导出盘点台账")
        b.clicked.connect(self.export_inventory)
        bar.addWidget(b)
        lay.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        g = QGroupBox("按时期筛选")
        gv = QVBoxLayout(g)
        self.lst_period = QListWidget()
        gv.addWidget(self.lst_period)
        lv.addWidget(g)
        g = QGroupBox("按对账状态筛选")
        gv = QVBoxLayout(g)
        self.lst_recon = QListWidget()
        gv.addWidget(self.lst_recon)
        lv.addWidget(g)
        left.setMaximumWidth(220)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        self.table = make_table(["档号", "识别时期", "形态", "目录入口", "实扫文件数", "清单文件数", "实扫文件夹",
                                 "容量MB", "对账结果", "处理状态", "说明"], stretch=10)
        self.table.itemSelectionChanged.connect(self.show_detail)
        right.addWidget(self.table)
        detail = QGroupBox("档案详情")
        dv = QVBoxLayout(detail)
        self.detail = QTextBrowser()
        dv.addWidget(self.detail)
        hb = QHBoxLayout()
        hb.addStretch(1)
        b = QPushButton("登记问题")
        b.clicked.connect(self.add_issue)
        hb.addWidget(b)
        dv.addLayout(hb)
        right.addWidget(detail)
        right.setSizes([420, 260])
        split.addWidget(right)
        split.setSizes([200, 1000])
        lay.addWidget(split, 1)
        self.lst_period.currentRowChanged.connect(lambda _: self.refresh_table())
        self.lst_recon.currentRowChanged.connect(lambda _: self.refresh_table())
        self.refresh()

    # ---------------------------------------------------------------- 数据
    def refresh(self):
        db = self.engine.db
        for lst, names, col in ((self.lst_period, PERIODS, "period"), (self.lst_recon, RECON, "recon_status")):
            cur = lst.currentRow()
            lst.blockSignals(True)
            lst.clear()
            for n in names:
                cnt = db.scalar("SELECT COUNT(*) FROM archive", default=0) if n == "全部" else \
                    db.scalar(f"SELECT COUNT(*) FROM archive WHERE {col}=?", (n,), 0)
                it = QListWidgetItem(f"{n}  ({cnt})")
                it.setData(Qt.UserRole, n)
                lst.addItem(it)
            lst.setCurrentRow(max(cur, 0))
            lst.blockSignals(False)
        self.refresh_table()

    def refresh_table(self):
        db = self.engine.db
        cond, params = ["1=1"], []
        p = self.lst_period.currentItem()
        r = self.lst_recon.currentItem()
        if p and p.data(Qt.UserRole) != "全部":
            cond.append("a.period=?")
            params.append(p.data(Qt.UserRole))
        if r and r.data(Qt.UserRole) != "全部":
            cond.append("a.recon_status=?")
            params.append(r.data(Qt.UserRole))
        sql = ("SELECT a.*, h.file_count AS h_files FROM archive a LEFT JOIN handover h ON h.dh=a.dh "
               f"WHERE {' AND '.join(cond)} ORDER BY a.root IS NULL, a.dh LIMIT 5000")
        rows, data = [], []
        for a in db.query(sql, params):
            info = loads(a["period_basis"], {})
            entries = loads(a["catalog_entries"], [])
            rows.append([a["dh"], a["period"], info.get("label") or "", "；".join(entries[:2]) + ("…" if len(entries) > 2 else ""),
                         a["file_count"], a["h_files"], a["dir_count"],
                         round((a["total_bytes"] or 0) / 1024 ** 2, 1), a["recon_status"], a["proc_status"],
                         a["proc_message"] or ""])
            data.append(a["dh"])
        fill_table(self.table, rows, data)

    def show_detail(self):
        dh = row_data(self.table)
        if not dh:
            return
        db = self.engine.db
        a = db.one("SELECT * FROM archive WHERE dh=?", (dh,))
        info = loads(a["period_basis"], {})
        cc = loads(a["category_counts"], {})
        h = db.one("SELECT * FROM handover WHERE dh=?", (dh,))
        hc = loads(h["counts"], {}) if h else {}
        html = [f"<h3>档号 {dh}</h3><p><b>数据路径</b> {a['root'] or '未找到'}</p>"]
        html.append(f"<p><b>识别时期</b> {a['period']}　{info.get('label') or ''}　得分 {a['period_score']}</p>")
        html.append("<p><b>时期判定依据</b><br>" + "<br>".join(info.get("basis", [])) + "</p>")
        sc = info.get("scores") or {}
        if sc:
            html.append("<p><b>各形态得分</b> " + "，".join(f"{k} {v}" for k, v in sc.items()) + "</p>")
        html.append("<p><b>目录文件</b><br>" + "<br>".join(loads(a["catalog_entries"], [])) + "</p>")
        res = cc.get("成果") or {}
        line = []
        for code, name in [("Z", "正文"), ("S", "审批"), ("T", "附图"), ("B", "附表"), ("J", "附件"),
                           ("K", "数据库"), ("R", "软件"), ("M", "多媒体"), ("Q", "其他")]:
            want = (hc.get("成果") or {}).get(code)
            line.append(f"{name}{code} 实扫{res.get(code, 0)}" + (f"/清单{want}" if want is not None else ""))
        html.append("<p><b>类别分布</b><br>" + "　".join(line) + "</p>")
        html.append(f"<p><b>对账结果</b> {a['recon_status']}<br>" + "<br>".join(loads(a["recon_detail"], [])) + "</p>")
        issues = db.query("SELECT kind, description FROM issue WHERE dh=? AND status='未关闭'", (dh,))
        if issues:
            html.append("<p><b>问题台账</b><br>" + "<br>".join(f"［{i['kind']}］{i['description']}" for i in issues) + "</p>")
        self.detail.setHtml("".join(html))

    # ---------------------------------------------------------------- 操作
    def pick_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据目录", self.ed_root.text())
        if d:
            self.ed_root.setText(d)

    def import_handover(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择交接清单", "", "Excel (*.xlsx)")
        if f:
            n = self.engine.import_handover(f)
            QMessageBox.information(self, "导入交接清单", f"已导入 {n} 档")
            self.refresh()

    def import_projects(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择 GRP 项目对照表", "", "表格 (*.xlsx *.csv)")
        if f:
            n, problems = self.engine.import_projects(f)
            msg = f"已导入 {n} 个项目"
            if problems:
                msg += f"，{len(problems)} 条问题：\n" + "\n".join(problems[:10])
            QMessageBox.information(self, "导入项目对照表", msg)

    def _busy(self, on: bool):
        self.btn_scan.setEnabled(not on)
        self.btn_run.setEnabled(not on)
        self.btn_stop.setEnabled(on)

    def start_scan(self):
        root = self.ed_root.text().strip()
        if not root or not Path(root).exists():
            QMessageBox.warning(self, "扫描", "请先选择存在的数据目录")
            return
        self.engine.db.set_meta("last_data_root", root)

        def job(progress, stop):
            return self.engine.scan(root, progress=lambda dh, i: progress(f"正在扫描 {dh}（已完成 {i} 档）"),
                                    should_stop=stop)
        self._start(job, lambda n: f"扫描完成，共 {n} 档")

    def run_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        dhs = [self.table.item(r, 0).data(Qt.UserRole) for r in rows] or None
        if not dhs:
            if QMessageBox.question(self, "治理", "未选中档案，是否对当前筛选结果全部运行治理？") != QMessageBox.Yes:
                return
            dhs = [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]

        def job(progress, stop):
            return self.engine.run_batch(dhs=dhs, force=True, should_stop=stop,
                                         progress=lambda dh, i, n: progress(f"正在治理 {dh}（{i + 1}/{n}）"))
        self._start(job, lambda bid: f"批次 {bid} 运行结束")

    def _start(self, job, done_msg):
        self.worker = Worker(job)
        self.worker.progress.connect(self.status_cb)
        self.worker.done.connect(lambda r: (self._busy(False), self.status_cb(done_msg(r)), self.refresh()))
        self.worker.failed.connect(lambda e: (self._busy(False), QMessageBox.critical(self, "出错", e[:2000])))
        self._busy(True)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.status_cb("已请求中止，当前档处理完后停止")

    def export_inventory(self):
        from ..output.excel import write_inventory
        f, _ = QFileDialog.getSaveFileName(self, "导出盘点台账", str(self.engine.out_root / "盘点台账.xlsx"), "Excel (*.xlsx)")
        if f:
            write_inventory(self.engine.db.query("SELECT * FROM archive ORDER BY dh"), Path(f))
            self.status_cb(f"盘点台账已导出到 {f}")

    def add_issue(self):
        dh = row_data(self.table)
        if not dh:
            return
        text, ok = QInputDialog.getMultiLineText(self, "登记问题", f"档号 {dh} 的问题描述")
        if ok and text.strip():
            self.engine.add_issue(dh, "人工登记", text.strip(), self.engine.operator)
            self.show_detail()
