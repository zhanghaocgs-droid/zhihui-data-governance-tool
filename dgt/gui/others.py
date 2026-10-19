"""其余五个模块的 V1.0 简版：规则管理只读查看、批量治理、质量检查、成果输出、系统设置。V1.x 补齐。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__
from ..core.db import loads
from .common import Worker, fill_table, make_table, page_header


class RulesPage(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(page_header("规则管理", f"当前规则 {engine.rule_tag}，V1.0 只读查看，编辑在 rules 目录进行"))
        split = QSplitter(Qt.Horizontal)
        self.files = QListWidget()
        self.files.addItems(["ruleset.yaml"] + list(engine.rules.ruleset.get("files", [])))
        self.files.currentTextChanged.connect(self.show)
        self.files.setMaximumWidth(220)
        split.addWidget(self.files)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        split.addWidget(self.text)
        lay.addWidget(split, 1)
        g = QGroupBox("规则版本登记")
        gv = QVBoxLayout(g)
        self.t = make_table(["版本", "摘要", "各文件版本", "加载时间"], stretch=2)
        gv.addWidget(self.t)
        g.setMaximumHeight(200)
        lay.addWidget(g)
        self.files.setCurrentRow(0)
        fill_table(self.t, [[r["version"], r["digest"], r["files"], r["loaded_at"]]
                            for r in engine.db.query("SELECT * FROM rule_version ORDER BY loaded_at DESC")])

    def show(self, name):
        if name:
            self.text.setPlainText((self.engine.rules.root / name).read_text(encoding="utf-8"))


class BatchPage(QWidget):
    def __init__(self, engine, status_cb):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        self.status_cb = status_cb
        self.worker = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(page_header("批量治理", "按档号区段建批次运行，已完成且规则版本未变的档自动跳过，可断点续跑"))
        hb = QHBoxLayout()
        hb.addWidget(QLabel("起始档号"))
        self.ed_from = QLineEdit()
        hb.addWidget(self.ed_from)
        hb.addWidget(QLabel("终止档号"))
        self.ed_to = QLineEdit()
        hb.addWidget(self.ed_to)
        self.btn = QPushButton("运行批次")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self.run)
        hb.addWidget(self.btn)
        self.btn_force = QPushButton("强制重跑区段")
        self.btn_force.clicked.connect(lambda: self.run(force=True))
        hb.addWidget(self.btn_force)
        self.btn_stop = QPushButton("中止")
        self.btn_stop.clicked.connect(lambda: self.worker and self.worker.stop())
        hb.addWidget(self.btn_stop)
        hb.addStretch(1)
        lay.addLayout(hb)
        self.t = make_table(["批次号", "名称", "档号区段", "规则版本", "状态", "档数", "完成", "失败", "开始", "结束", "操作人"])
        lay.addWidget(self.t, 1)
        self.refresh()

    def refresh(self):
        fill_table(self.t, [[b["id"], b["name"], f"{b['dh_from'] or '起'} ~ {b['dh_to'] or '止'}", b["rule_version"],
                             b["status"], b["total"], b["done"], b["failed"], b["started_at"], b["finished_at"],
                             b["operator"]] for b in self.engine.db.query("SELECT * FROM batch ORDER BY id DESC")])

    def run(self, force=False):
        f, t = self.ed_from.text().strip() or None, self.ed_to.text().strip() or None

        def job(progress, stop):
            return self.engine.run_batch(f, t, force=force, should_stop=stop,
                                         progress=lambda dh, i, n: progress(f"正在治理 {dh}（{i + 1}/{n}）"))
        self.worker = Worker(job)
        self.worker.progress.connect(self.status_cb)
        self.worker.done.connect(lambda b: (self.status_cb(f"批次 {b} 结束"), self.refresh()))
        self.worker.failed.connect(lambda e: QMessageBox.critical(self, "出错", e[:2000]))
        self.worker.start()


class QcPage(QWidget):
    def __init__(self, engine, status_cb):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        self.status_cb = status_cb
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        hb = QHBoxLayout()
        hb.addWidget(page_header("质量检查", "落库说明第六章校验与映射完整性、涉密分套检查"))
        b = QPushButton("执行校验")
        b.setObjectName("primary")
        b.clicked.connect(self.run)
        hb.addWidget(b)
        lay.addLayout(hb)
        self.sum = make_table(["校验项", "应检数", "未通过数", "结论"], stretch=0)
        self.sum.setMinimumHeight(400)
        self.sum.setMaximumHeight(420)
        lay.addWidget(self.sum)
        lay.addWidget(QLabel("未通过明细"))
        self.fail = make_table(["校验项", "档号", "对象", "说明"], stretch=3)
        lay.addWidget(self.fail, 1)

    def run(self):
        from ..output.qc import run_qc
        dhs = [r["dh"] for r in self.engine.db.query("SELECT dh FROM archive WHERE root IS NOT NULL")]
        summary, failures = run_qc(self.engine, dhs)
        fill_table(self.sum, [[s["check"], s["total"], s["failed"], "通过" if not s["failed"] else "未通过"] for s in summary],
                   sortable=False)
        fill_table(self.fail, [[f["check"], f["dh"], f["target"], f["message"]] for f in failures[:2000]])
        self.status_cb(f"校验完成，未通过 {len(failures)} 项")


class ExportPage(QWidget):
    def __init__(self, engine, status_cb):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        self.status_cb = status_cb
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        hb = QHBoxLayout()
        hb.addWidget(page_header("成果输出", "生成全补、涉密、非涉密三套成果，非涉密套输出前二次校验"))
        b = QPushButton("生成三套成果")
        b.setObjectName("primary")
        b.clicked.connect(self.run)
        hb.addWidget(b)
        lay.addLayout(hb)
        self.t = make_table(["成果套", "档数", "项目数", "落库文件", "映射记录", "说明"], stretch=5)
        lay.addWidget(self.t)
        self.info = QTextBrowser()
        lay.addWidget(self.info, 1)

    def run(self):
        from ..output.export import export_sets
        dhs = [r["dh"] for r in self.engine.db.query("SELECT dh FROM archive WHERE root IS NOT NULL")]
        res = export_sets(self.engine, dhs)
        rows = []
        for k, v in res["sets"].items():
            if v.get("aborted"):
                rows.append([k, "", "", "", "", "二次校验发现涉密内容，已中止输出"])
            else:
                rows.append([k, v["archives"], v["projects"], v["landed"], v["records"], ""])
        fill_table(self.t, rows)
        self.info.setHtml(f"<p>输出目录 {res['dir']}</p><p>每套含 落库成果 目录、结构化目录 Excel、映射关系表；"
                          f"根目录含 质检结果.xlsx 和 盘点台账.xlsx。</p>")
        self.status_cb("三套成果已生成")


class SettingsPage(QWidget):
    def __init__(self, engine, operator_cb):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(page_header("系统设置", "工作区、操作人、转换器与作业日志"))
        g = QGroupBox("运行环境")
        fl = QFormLayout(g)
        fl.addRow("程序版本", QLabel(__version__))
        fl.addRow("规则版本", QLabel(engine.rule_tag))
        fl.addRow("工作区", QLabel(str(engine.ws)))
        fl.addRow("成果区", QLabel(str(engine.out_root)))
        fl.addRow("数据目录（只读）", QLabel("；".join(loads(engine.db.get_meta("data_roots"), [])) or "未设置"))
        avail = engine.converter.available()
        fl.addRow("Word 转 PDF", QLabel("、".join(avail) if avail else "无可用转换器，Word 文件将进入待处理"))
        self.ed_op = QLineEdit(engine.operator)
        self.ed_op.editingFinished.connect(lambda: operator_cb(self.ed_op.text().strip()))
        fl.addRow("当前操作人", self.ed_op)
        lay.addWidget(g)
        g2 = QGroupBox("作业日志（最近 500 条）")
        v = QVBoxLayout(g2)
        t = make_table(["时间", "批次", "档号", "步骤", "级别", "内容", "操作人"], stretch=5)
        fill_table(t, [[r["ts"], r["batch_id"], r["dh"], r["step"], r["level"], (r["message"] or "")[:200], r["operator"]]
                       for r in engine.db.query("SELECT * FROM oplog ORDER BY id DESC LIMIT 500")])
        v.addWidget(t)
        lay.addWidget(g2, 1)
