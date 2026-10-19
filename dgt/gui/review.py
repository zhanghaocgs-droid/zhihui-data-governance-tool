"""人工复核：待复核队列、原文预览、程序建议与依据、人工确认或修改、提交技术负责人、登记问题。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.db import loads
from .common import fill_table, make_table, page_header, row_data

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
except Exception:  # 缺少 QtPdf 时退化为只显示路径
    QPdfDocument = QPdfView = None

ASSOCIATE_LABEL = "关联保存（不落库）"


class ReviewPage(QWidget):
    def __init__(self, engine, status_cb=None):
        super().__init__()
        self.setObjectName("page")
        self.engine = engine
        self.status_cb = status_cb or (lambda s: None)
        self.current = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        head.addWidget(page_header("人工复核", "程序拿不准的文件在此确认，结果回写映射表并重新落库"))
        head.addWidget(QLabel("状态"))
        self.cb_status = QComboBox()
        self.cb_status.addItems(["待复核", "已提交", "已确认", "全部"])
        self.cb_status.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.cb_status)
        b = QPushButton("刷新")
        b.clicked.connect(self.refresh)
        head.addWidget(b)
        lay.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        # 左：队列
        g = QGroupBox("待复核队列")
        gv = QVBoxLayout(g)
        self.queue = make_table(["档号", "类型", "文件", "进入原因", "状态"], stretch=3)
        self.queue.itemSelectionChanged.connect(self.load_current)
        gv.addWidget(self.queue)
        split.addWidget(g)

        # 中：原文
        g = QGroupBox("原文")
        mv = QVBoxLayout(g)
        self.doc_info = QTextBrowser()
        self.doc_info.setMaximumHeight(170)
        mv.addWidget(self.doc_info)
        self.preview = QStackedWidget()
        self.preview_msg = QLabel("选择左侧条目后显示原文")
        self.preview_msg.setAlignment(Qt.AlignCenter)
        self.preview.addWidget(self.preview_msg)
        if QPdfView:
            self.pdf_doc = QPdfDocument(self)
            self.pdf_view = QPdfView()
            self.pdf_view.setDocument(self.pdf_doc)
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.preview.addWidget(self.pdf_view)
        mv.addWidget(self.preview, 1)
        hb = QHBoxLayout()
        hb.addStretch(1)
        b = QPushButton("用系统程序打开副本")
        b.setToolTip("先复制到成果区临时目录再打开，避免办公软件在原始区生成锁文件")
        b.clicked.connect(self.open_copy)
        hb.addWidget(b)
        mv.addLayout(hb)
        split.addWidget(g)

        # 右：建议与确认
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        g = QGroupBox("程序建议")
        sv = QVBoxLayout(g)
        self.suggest = QTextBrowser()
        sv.addWidget(self.suggest)
        self.cands = make_table(["候选", "阶段", "子目录", "文件类型", "得分", "命中词"], stretch=3)
        self.cands.setMaximumHeight(150)
        self.cands.itemDoubleClicked.connect(self.apply_candidate)
        sv.addWidget(QLabel("候选结论（双击填入下方）"))
        sv.addWidget(self.cands)
        rv.addWidget(g, 1)

        self.form_box = QGroupBox("人工确认")
        fl = QFormLayout(self.form_box)
        self.cb_stage = QComboBox()
        self.cb_stage.currentIndexChanged.connect(self.on_stage)
        self.cb_subdir = QComboBox()
        self.cb_subdir.currentIndexChanged.connect(self.on_subdir)
        self.cb_type = QComboBox()
        self.ed_period = QLineEdit()
        self.ed_period.setPlaceholderText("年度性填 2026，质量监督填 2026年3月，一次性阶段留空")
        self.cb_version = QComboBox()
        self.ed_basis = QPlainTextEdit()
        self.ed_basis.setPlaceholderText("确认依据，例如 核对原文首页为年度工作方案批复")
        self.ed_basis.setMaximumHeight(70)
        self.cb_project = QComboBox()
        fl.addRow("阶段", self.cb_stage)
        fl.addRow("子目录", self.cb_subdir)
        fl.addRow("文件类型", self.cb_type)
        fl.addRow("期间标识", self.ed_period)
        fl.addRow("版本", self.cb_version)
        fl.addRow("关联项目", self.cb_project)
        fl.addRow("确认依据", self.ed_basis)
        hb = QHBoxLayout()
        self.btn_ok = QPushButton("确认落库")
        self.btn_ok.setObjectName("primary")
        self.btn_ok.clicked.connect(self.confirm)
        self.btn_submit = QPushButton("提交技术负责人")
        self.btn_submit.clicked.connect(self.submit)
        self.btn_issue = QPushButton("登记问题")
        self.btn_issue.clicked.connect(self.add_issue)
        hb.addWidget(self.btn_ok)
        hb.addWidget(self.btn_submit)
        hb.addWidget(self.btn_issue)
        fl.addRow(hb)
        rv.addWidget(self.form_box)
        split.addWidget(right)
        split.setSizes([520, 480, 440])
        lay.addWidget(split, 1)
        self.refresh()

    # ---------------------------------------------------------------- 队列
    def refresh(self):
        st = self.cb_status.currentText()
        sql = "SELECT * FROM review"
        params = ()
        if st != "全部":
            sql += " WHERE status=?"
            params = (st,)
        sql += " ORDER BY dh, id"
        rows, data = [], []
        for r in self.engine.db.query(sql, params):
            kind = "项目关联" if r["kind"] == "project" else "阶段分类"
            name = r["src_rel"].rsplit("/", 1)[-1] if r["src_rel"] else "（整档）"
            rows.append([r["dh"], kind, name, r["reason"], r["status"]])
            data.append(r["id"])
        fill_table(self.queue, rows, data)
        if rows:
            self.queue.selectRow(0)
        else:
            self.current = None
            self.doc_info.setHtml("<p>当前没有需要复核的条目。</p>")
            self.suggest.clear()

    def load_current(self):
        rid = row_data(self.queue)
        if rid is None:
            return
        db = self.engine.db
        rv = dict(db.one("SELECT * FROM review WHERE id=?", (rid,)))
        self.current = rv
        sug = loads(rv["suggestion"], {})
        cands = loads(rv["candidates"], [])
        proj = db.one("SELECT p.* FROM archive_project ap JOIN project p ON p.uuid=ap.project_uuid WHERE ap.dh=?",
                      (rv["dh"],))
        level = proj["level"] if proj else None
        case = loads((db.one("SELECT fields FROM case_info WHERE dh=?", (rv["dh"],)) or {"fields": "{}"})["fields"], {})
        m = db.one("SELECT * FROM mapping WHERE id=?", (rv["mapping_id"],)) if rv["mapping_id"] else None
        d = db.one("SELECT * FROM document WHERE id=?", (m["doc_id"],)) if m and m["doc_id"] else None

        info = [f"<p><b>档号</b> {rv['dh']}　<b>时期</b> {m['period'] if m else ''}　"
                f"<b>项目层级</b> {level or '未关联'}</p>"]
        info.append(f"<p><b>资料题名</b> {case.get('TM') or case.get('BGTM') or ''}</p>")
        if d:
            info.append(f"<p><b>文件题名</b> {d['title'] or '【缺失】'}<br><b>题名来源</b> {d['title_source']}<br>"
                        f"<b>类别代码</b> {d['category']}　<b>册号件号</b> {d['volume'] or ''}/{d['item'] or ''}　"
                        f"<b>密级</b> {d['secrecy'] or case.get('MJ') or ''}</p>")
            info.append(f"<p><b>存档文件</b> {d['archive_rel'] or '无'}<br><b>源文件</b> {d['source_rel'] or '无'}</p>")
        self.doc_info.setHtml("".join(info))
        self._preview(rv["dh"], d)

        if rv["kind"] == "project":
            self.suggest.setHtml(f"<p><b>进入原因</b> {rv['reason']}</p><p>建议关联项目 {sug.get('name')}<br>"
                                 f"UUID {sug.get('uuid')}</p>")
            fill_table(self.cands, [[c["uuid"][:8], c["name"], "", "", c["score"], ""] for c in cands], cands)
            self._project_mode(True, cands)
            return
        self._project_mode(False, [])
        txt = [f"<p><b>进入原因</b> <span style='color:#b4462c'>{rv['reason']}</span></p>"]
        txt.append(f"<p><b>建议阶段</b> {sug.get('stage') or '无'}　<b>子目录</b> {sug.get('subdir') or '无'}<br>"
                   f"<b>建议文件类型</b> {sug.get('file_type') or '无'}<br>"
                   f"<b>置信度</b> {sug.get('confidence') or 0:.2f}　<b>命中规则</b> {sug.get('rule_id') or '无'}<br>"
                   f"<b>命中依据</b> {sug.get('evidence') or '无'}</p>")
        self.suggest.setHtml("".join(txt))
        fill_table(self.cands, [[c["rule"], c["stage"], c["subdir"] or "", c["file_type"], c["score"],
                                 "、".join(c["matched"])] for c in cands], cands)
        self._fill_form(level, sug)

    def _project_mode(self, on: bool, cands):
        for w in (self.cb_stage, self.cb_subdir, self.cb_type, self.ed_period, self.cb_version):
            w.setEnabled(not on)
        self.cb_project.setEnabled(on)
        self.cb_project.clear()
        if on:
            for c in cands:
                self.cb_project.addItem(f"{c['name']}（{c['score']}）", c["uuid"])
            for p in self.engine.db.query("SELECT uuid, name FROM project ORDER BY name"):
                if p["uuid"] not in [c["uuid"] for c in cands]:
                    self.cb_project.addItem(p["name"], p["uuid"])
            self.btn_ok.setText("确认关联")
        else:
            self.btn_ok.setText("确认落库")

    def _fill_form(self, level, sug):
        self.level = level
        self.cb_stage.blockSignals(True)
        self.cb_stage.clear()
        for s in self.engine.rules.stage_names(level):
            self.cb_stage.addItem(s)
        self.cb_stage.addItem(ASSOCIATE_LABEL)
        stage = sug.get("stage")
        if stage and "或" in stage and level:
            stage = self.engine.rules.stage4_name(level)
        idx = self.cb_stage.findText(stage or "")
        self.cb_stage.setCurrentIndex(idx if idx >= 0 else 0)
        self.cb_stage.blockSignals(False)
        self.on_stage()
        if sug.get("subdir"):
            self.cb_subdir.setCurrentText(sug["subdir"])
        ft = sug.get("file_type") or ""
        if "或" in ft and level:
            ft = ft.split("或")[0 if level == "二级项目" else 1]
        i = self.cb_type.findText(ft)
        if i >= 0:
            self.cb_type.setCurrentIndex(i)
        self.ed_period.setText(sug.get("period_id") or "")
        self.ed_basis.clear()

    def on_stage(self):
        stage = self.cb_stage.currentText()
        self.cb_subdir.blockSignals(True)
        self.cb_subdir.clear()
        subs = self.engine.rules.subdirs(stage) if stage != ASSOCIATE_LABEL else []
        self.cb_subdir.addItems(subs or ["（无）"])
        self.cb_subdir.setEnabled(bool(subs))
        self.cb_subdir.blockSignals(False)
        self.on_subdir()
        ptype = self.engine.rules.period_type(stage) if self.engine.rules.stage_def(stage) else "once"
        self.ed_period.setEnabled(ptype != "once")
        if ptype == "once":
            self.ed_period.clear()

    def on_subdir(self):
        stage = self.cb_stage.currentText()
        sub = self.cb_subdir.currentText() if self.cb_subdir.isEnabled() else None
        self.cb_type.clear()
        self.cb_version.clear()
        if stage == ASSOCIATE_LABEL:
            self.cb_type.setEnabled(False)
            self.cb_version.setEnabled(False)
            return
        self.cb_type.setEnabled(True)
        self.cb_version.setEnabled(True)
        self.cb_type.addItems(list(self.engine.rules.file_types(stage, sub)))
        self.cb_version.addItem("按规则自动")
        vw = self.engine.rules.stages["version_words"]
        self.cb_version.addItems(vw["text"] + ["v1", "v2", "v3"])

    def apply_candidate(self, item):
        c = self.cands.item(item.row(), 0).data(Qt.UserRole)
        if self.current and self.current["kind"] == "project":
            i = self.cb_project.findData(c["uuid"])
            self.cb_project.setCurrentIndex(i)
            return
        self._fill_form(self.level, {"stage": c["stage"], "subdir": c["subdir"], "file_type": c["file_type"],
                                     "period_id": self.ed_period.text()})

    # ---------------------------------------------------------------- 原文
    def _src_path(self, dh, d):
        if not d:
            return None
        root = self.engine.db.scalar("SELECT root FROM archive WHERE dh=?", (dh,))
        rel = d["archive_rel"] or d["source_rel"]
        return Path(root) / rel if root and rel else None

    def _preview(self, dh, d):
        p = self._src_path(dh, d)
        if p and p.exists() and p.suffix.lower() == ".pdf" and QPdfView:
            self.pdf_doc.load(str(p))
            self.preview.setCurrentWidget(self.pdf_view)
        else:
            self.preview_msg.setText("该文件无法内嵌预览，可用系统程序打开副本" if p and p.exists()
                                     else "档内找不到该文件")
            self.preview.setCurrentWidget(self.preview_msg)

    def open_copy(self):
        if not self.current or not self.current.get("mapping_id"):
            return
        m = self.engine.db.one("SELECT doc_id FROM mapping WHERE id=?", (self.current["mapping_id"],))
        d = self.engine.db.one("SELECT * FROM document WHERE id=?", (m["doc_id"],)) if m else None
        p = self._src_path(self.current["dh"], d)
        if not p or not p.exists():
            return
        dst = self.engine.tmp_root / "预览" / f"{self.current['dh']}_{p.name}"
        self.engine.guard.copy(p, dst)
        os.startfile(str(dst))

    # ---------------------------------------------------------------- 提交
    def confirm(self):
        if not self.current:
            return
        basis = self.ed_basis.toPlainText().strip()
        if not basis:
            QMessageBox.warning(self, "人工确认", "请填写确认依据")
            return
        if self.current["kind"] == "project":
            uuid = self.cb_project.currentData()
            if not uuid:
                return
            self.engine.confirm_review(self.current["id"], {"uuid": uuid}, basis, self.engine.operator or "作业员")
            self.status_cb("项目关联已确认，该档已重新治理")
            self.refresh()
            return
        stage = self.cb_stage.currentText()
        if stage == ASSOCIATE_LABEL:
            result = {"action": "associate"}
        else:
            ptype = self.engine.rules.period_type(stage)
            period = self.ed_period.text().strip()
            import re
            pats = {"annual": r"^\d{4}$", "monthly": r"^\d{4}年\d{1,2}月$"}
            if ptype in pats and not re.match(pats[ptype], period):
                QMessageBox.warning(self, "人工确认", f"{stage}阶段的期间标识格式应为 "
                                    f"{'2026' if ptype == 'annual' else '2026年3月'}")
                return
            ver = self.cb_version.currentText()
            result = {"action": "accept", "stage": stage,
                      "subdir": self.cb_subdir.currentText() if self.cb_subdir.isEnabled() else None,
                      "file_type": self.cb_type.currentText(), "period_id": period if ptype != "once" else "",
                      "version": None if ver == "按规则自动" else ver}
        res = self.engine.confirm_review(self.current["id"], result, basis, self.engine.operator or "作业员")
        self.status_cb(f"已确认并重新治理档 {res['dh']}：落库 {res.get('landed', 0)} 件，剩余待复核 {res.get('reviews', 0)} 件")
        self.refresh()

    def submit(self):
        if not self.current:
            return
        note, ok = QInputDialog.getMultiLineText(self, "提交技术负责人", "说明需要技术负责人判断的内容")
        if ok:
            self.engine.submit_review(self.current["id"], note, self.engine.operator or "作业员")
            self.refresh()

    def add_issue(self):
        if not self.current:
            return
        text, ok = QInputDialog.getMultiLineText(self, "登记问题", "问题描述，将汇总后经项目经理向全国馆请教")
        if ok and text.strip():
            self.engine.add_issue(self.current["dh"], "复核问题",
                                  f"{self.current['src_rel']}：{text.strip()}", self.engine.operator)
            self.status_cb("问题已登记")
