"""工作台：总体进度、分时期进度、九阶段文件分布、最近批次、每日工作量。"""
from __future__ import annotations

from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from .common import Card, fill_table, make_table, page_header

PERIODS = ["现行", "近期", "清欠", "早期"]


class WorkbenchPage(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        head.addWidget(page_header("工作台", "治理总体进度与待办"))
        btn = QPushButton("刷新")
        btn.clicked.connect(self.refresh)
        head.addWidget(btn)
        lay.addLayout(head)

        cards = QHBoxLayout()
        self.c_total = Card("移交档案数", hint="交接清单两批合计")
        self.c_done = Card("已完成治理档数")
        self.c_review = Card("待人工复核件数")
        self.c_issue = Card("未关闭问题数")
        for c in (self.c_total, self.c_done, self.c_review, self.c_issue):
            cards.addWidget(c)
        lay.addLayout(cards)

        mid = QHBoxLayout()
        g1 = QGroupBox("分时期治理进度")
        self.period_grid = QGridLayout(g1)
        self.period_bars = {}
        for i, p in enumerate(PERIODS):
            self.period_grid.addWidget(QLabel(p), i, 0)
            bar = QProgressBar()
            bar.setFormat("%v / %m 档")
            self.period_grid.addWidget(bar, i, 1)
            note = QLabel("")
            note.setObjectName("sub")
            self.period_grid.addWidget(note, i, 2)
            self.period_bars[p] = (bar, note)
        mid.addWidget(g1, 1)
        g2 = QGroupBox("九个阶段落库文件分布")
        self.stage_grid = QGridLayout(g2)
        self.stage_bars = {}
        mid.addWidget(g2, 1)
        lay.addLayout(mid)

        bottom = QHBoxLayout()
        g3 = QGroupBox("最近批次")
        v3 = QVBoxLayout(g3)
        self.t_batch = make_table(["批次", "档号区段", "程序版本", "规则版本", "状态", "档数", "完成", "失败", "开始时间"])
        v3.addWidget(self.t_batch)
        bottom.addWidget(g3, 3)
        g4 = QGroupBox("每日工作量")
        v4 = QVBoxLayout(g4)
        self.t_daily = make_table(["日期", "处理档数", "落库文件", "人工复核"])
        v4.addWidget(self.t_daily)
        bottom.addWidget(g4, 2)
        lay.addLayout(bottom, 1)
        self.refresh()

    def refresh(self):
        db = self.engine.db
        handover = db.scalar("SELECT COUNT(*) FROM handover", default=0)
        scanned = db.scalar("SELECT COUNT(*) FROM archive WHERE root IS NOT NULL", default=0)
        self.c_total.set(handover or scanned, f"已盘点 {scanned} 档" + ("" if handover else "，未导入交接清单"))
        done = db.scalar("SELECT COUNT(*) FROM archive WHERE proc_status='已完成'", default=0)
        partial = db.scalar("SELECT COUNT(*) FROM archive WHERE proc_status IN ('待复核','待项目关联')", default=0)
        self.c_done.set(done, f"另有 {partial} 档待复核或待关联")
        submitted = db.scalar("SELECT COUNT(*) FROM review WHERE status='已提交'", default=0)
        self.c_review.set(db.scalar("SELECT COUNT(*) FROM review WHERE status!='已确认'", default=0),
                          f"已提交技术负责人 {submitted} 件")
        recon = db.scalar("SELECT COUNT(*) FROM issue WHERE status='未关闭' AND kind='对账差异'", default=0)
        self.c_issue.set(db.scalar("SELECT COUNT(*) FROM issue WHERE status='未关闭'", default=0),
                         f"其中对账差异 {recon} 条")

        for p, (bar, note) in self.period_bars.items():
            total = db.scalar("SELECT COUNT(*) FROM archive WHERE period=?", (p,), 0)
            ok = db.scalar("SELECT COUNT(*) FROM archive WHERE period=? AND proc_status='已完成'", (p,), 0)
            part = db.scalar("SELECT COUNT(*) FROM archive WHERE period=? AND proc_status IN ('待复核','待项目关联')", (p,), 0)
            unsup = db.scalar("SELECT COUNT(*) FROM archive WHERE period=? AND proc_status='暂不支持'", (p,), 0)
            bar.setMaximum(max(total, 1))
            bar.setValue(ok)
            bar.setFormat(f"{ok} / {total} 档")
            note.setText(f"进行中 {part}" + (f"，待后续版本 {unsup}" if unsup else ""))

        while self.stage_grid.count():
            w = self.stage_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        stages = ["立项论证", "续作评估", "任务书", "实施方案", "年度设计", "质量监督", "年度考核", "野外验收",
                  "成果评审", "资料汇交"]
        counts = {r["stage"]: r["n"] for r in db.query(
            "SELECT stage, COUNT(*) n FROM mapping WHERE status='已落库' GROUP BY stage")}
        mx = max(counts.values(), default=1)
        for i, s in enumerate(stages):
            label = s if s not in ("实施方案", "年度设计") else f"{s}（{'二' if s == '实施方案' else '三'}级）"
            self.stage_grid.addWidget(QLabel(label), i, 0)
            bar = QProgressBar()
            bar.setMaximum(mx)
            bar.setValue(counts.get(s, 0))
            bar.setFormat(f"{counts.get(s, 0)} 件")
            self.stage_grid.addWidget(bar, i, 1)

        rows = []
        for b in db.query("SELECT * FROM batch ORDER BY id DESC LIMIT 12"):
            rng = f"{b['dh_from'] or '起'} ~ {b['dh_to'] or '止'}"
            rows.append([b["name"], rng, b["program_version"], b["rule_version"], b["status"], b["total"],
                         b["done"], b["failed"], b["started_at"]])
        fill_table(self.t_batch, rows)

        daily = {}
        for r in db.query("SELECT substr(processed_at,1,10) d, COUNT(*) n FROM archive WHERE processed_at IS NOT NULL GROUP BY d"):
            daily.setdefault(r["d"], [0, 0, 0])[0] = r["n"]
        for r in db.query("SELECT substr(processed_at,1,10) d, COUNT(*) n FROM mapping WHERE status='已落库' GROUP BY d"):
            daily.setdefault(r["d"], [0, 0, 0])[1] = r["n"]
        for r in db.query("SELECT substr(ts,1,10) d, COUNT(*) n FROM oplog WHERE step='复核' GROUP BY d"):
            daily.setdefault(r["d"], [0, 0, 0])[2] = r["n"]
        fill_table(self.t_daily, [[d, *v] for d, v in sorted(daily.items(), reverse=True)[:14]])
