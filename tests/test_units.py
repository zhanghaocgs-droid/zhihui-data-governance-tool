"""单元测试，不依赖样例数据。"""
from __future__ import annotations

import pytest
from openpyxl import Workbook

from dgt.adapt.parsers.base import ArchiveScan, Document
from dgt.adapt.parsers.ml import parse_doc_ml, parse_sub_dir
from dgt.adapt.period import identify
from dgt.core.config import load_rules
from dgt.core.fsguard import ReadOnlyViolation, WriteGuard
from dgt.ingest.handover import read_handover, reconcile
from dgt.ingest.scan import scan_archive
from dgt.output.validate import LandingValidator
from dgt.process.classify import ACCEPT, ASSOCIATE, RAW, REVIEW, Classifier
from dgt.process.linker import CONFIRMED, SUSPECT, UNMATCHED, ProjectLinker
from dgt.process.naming import build_name, clean_project_name, extract_period
from dgt.process.secrecy import SecrecyJudge

R = load_rules()
UUID = "2f5b8d83433c427883c261d3ec137c49"
PROJ = "内蒙古××地区金矿资源调查"


# ---------------------------------------------------------------- 命名
def test_clean_project_name():
    cfg = R.naming
    assert clean_project_name("1:5万区调-××幅", cfg) == "1∶5万区调××幅"
    assert clean_project_name("某地（东段）铜矿 普查", cfg) == "某地东段铜矿普查"
    assert clean_project_name("敖包图•乌兰【一期】", cfg) == "敖包图·乌兰一期"
    assert "-" not in clean_project_name("A－B—C-D", cfg)


def test_extract_period():
    cfg = R.naming
    assert extract_period("annual", ["赣国土核[2005]27号设计批复"], cfg)[0] == "2005"
    assert extract_period("annual", ["2012年度工作方案"], cfg)[0] == "2012"
    assert extract_period("annual", ["专题工作方案"], cfg)[0] is None
    assert extract_period("monthly", ["2026年3月质量监督月报"], cfg)[0] == "2026年3月"
    assert extract_period("once", ["任意"], cfg)[0] == ""


def test_build_name():
    assert build_name("2026", PROJ, "任务书", "终审", "pdf") == f"2026-{PROJ}-任务书-终审.pdf"
    assert build_name("", PROJ, "成果报告", "终审", "PDF") == f"{PROJ}-成果报告-终审.pdf"


# ---------------------------------------------------------------- 第六章校验
V = LandingValidator(R)


@pytest.mark.parametrize("rel_dir,name,stage,subdir,level", [
    (f"/二级项目/{UUID}/立项论证", f"{PROJ}-立项申报书-报批稿.pdf", "立项论证", None, "二级项目"),
    (f"/二级项目/{UUID}/续作评估", f"2026-{PROJ}-续作评估申请-v1.pdf", "续作评估", None, "二级项目"),
    (f"/二级项目/{UUID}/实施方案", f"2026-{PROJ}-实施方案-送审稿.pdf", "实施方案", None, "二级项目"),
    (f"/三级项目/{UUID}/年度设计", f"2026-{PROJ}-年度设计书-终审.pdf", "年度设计", None, "三级项目"),
    (f"/二级项目/{UUID}/质量监督", f"2026年10月-{PROJ}-质量监督月报-v1.pdf", "质量监督", None, "二级项目"),
    (f"/二级项目/{UUID}/野外验收/野外验收备案", f"{PROJ}-野外验收备案表-v1.xlsx", "野外验收", "野外验收备案", "二级项目"),
    (f"/三级项目/{UUID}/成果评审/信息化评审", f"{PROJ}-信息化评审结论-定稿.pdf", "成果评审", "信息化评审", "三级项目"),
    (f"/二级项目/{UUID}/资料汇交", f"{PROJ}-汇交汇总表-定稿.pdf", "资料汇交", None, "二级项目"),
])
def test_spec_examples_pass(rel_dir, name, stage, subdir, level):
    assert V.check_one(rel_dir, name, stage, subdir, level) == []


@pytest.mark.parametrize("name,stage", [
    ("2026 任务书 终审.pdf", "任务书"),
    ("2026－项目－任务书－终审.pdf", "任务书"),
    ("2026_项目_任务书_终审.pdf", "任务书"),
    ("任务书-2026-终审.pdf", "任务书"),
    ("2026-项目-任务书-20261018.pdf", "任务书"),
    ("2026-××地区-金矿调查-任务书-终审.pdf", "任务书"),
    ("2026-项目-任务书-终审(1).pdf", "任务书"),
    ("2026-10-项目-质量监督月报-v1.pdf", "质量监督"),
    ("项目-任务书-终审.pdf", "任务书"),
    ("2026-项目-立项批复-定稿.pdf", "立项论证"),
    ("2026-项目-任务书-最终版.pdf", "任务书"),
])
def test_spec_taboos_fail(name, stage):
    assert V.check_one(f"/二级项目/{UUID}/{stage}", name, stage, None, "二级项目")


def test_dir_rules():
    assert V.check_one(f"/二级项目/{UUID}/年度设计", f"2026-{PROJ}-年度设计书-终审.pdf", "年度设计", None, "二级项目")
    assert V.check_one(f"/二级项目/{UUID}/任务书/2026", f"2026-{PROJ}-任务书-终审.pdf", "任务书", None, "二级项目")
    assert V.check_one(f"/二级项目/ABC/任务书", f"2026-{PROJ}-任务书-终审.pdf", "任务书", None, "二级项目")
    long = "某" * 120
    assert any(f.check == "长度" for f in V.check_one(f"/二级项目/{UUID}/立项论证", f"{long}-立项批复-定稿.pdf",
                                                    "立项论证", None, "二级项目"))


def test_group_checks():
    items = [{"project_uuid": "p", "rel_dir": "/x", "name": "2026-项目-任务书-v1.pdf"},
             {"project_uuid": "p", "rel_dir": "/x", "name": "2026-项目-任务书-v3.pdf"},
             {"project_uuid": "p", "rel_dir": "/y", "name": "项目-成果报告-终审.pdf"},
             {"project_uuid": "p", "rel_dir": "/y", "name": "项目-成果报告-终审.pdf"}]
    checks = {f.check for f in V.check_group(items)}
    assert {"唯一性", "版本连续性"} <= checks


# ---------------------------------------------------------------- 分类
C = Classifier(R)


def doc(cat, title, kind="result"):
    return Document(kind=kind, category=cat, title=title, title_source="测试")


def test_classify_basic():
    r = C.classify(doc("Z", "某某地区金矿普查地质报告"), "二级项目")
    assert (r.action, r.stage, r.subdir, r.file_type) == (ACCEPT, "成果评审", "成果评审", "成果报告")
    r = C.classify(doc("S", "中地调(总)评字[2006]03号评审意见书"), "二级项目")
    assert (r.action, r.file_type) == (ACCEPT, "成果评审意见")
    r = C.classify(doc("Q", "赣国土核[2005]27号设计批复"), "三级项目")
    assert (r.action, r.stage, r.file_type) == (ACCEPT, "年度设计", "年度设计批复")
    r = C.classify(doc("Q", "赣国土核[2005]27号设计批复"), "二级项目")
    assert (r.stage, r.file_type) == ("实施方案", "实施方案批复")
    r = C.classify(doc("S", "某项目野外验收意见书"), "二级项目")
    assert (r.stage, r.subdir, r.file_type) == ("野外验收", "野外验收评审", "野外验收结论")
    r = C.classify(doc("GL", "地质资料汇交汇总表", "mgmt"), "二级项目")
    assert (r.stage, r.file_type) == ("资料汇交", "汇交汇总表")


def test_classify_exclusions_and_review():
    # 报告正文提到任务不算任务书
    r = C.classify(doc("Z", "关于完成任务书下达工作的总结报告"), "二级项目")
    assert r.stage == "成果评审"
    # 审查意见含义不清，进人工
    r = C.classify(doc("S", "审查意见书"), "二级项目")
    assert r.action == REVIEW and r.candidates
    # 未命中的审批件进人工，附件关联保存
    assert C.classify(doc("S", "内审意见书"), "二级项目").action == REVIEW
    assert C.classify(doc("J", "遥感解译报告"), "二级项目").action == ASSOCIATE
    assert C.classify(doc("T", "地质图"), "二级项目").action == ASSOCIATE
    assert C.classify(doc("D", "野外记录", "raw"), "二级项目").action == RAW


def test_classify_unknown_level():
    r = C.classify(doc("Q", "设计批复"), None)
    assert r.stage == "实施方案或年度设计" and "/" not in r.file_type


# ---------------------------------------------------------------- 涉密
def test_secrecy():
    j = SecrecyJudge(R.secrecy)
    assert j.judge("机密级", "不涉密", "", "")[0] == "涉密"
    assert j.judge("不涉密", "秘密", "", "")[0] == "涉密"
    assert j.judge("不涉密", "不涉密", "", "")[0] == "非涉密"
    assert j.judge("", "", "", "")[0] == "涉密"
    j2 = SecrecyJudge({**R.secrecy, "catalog_keywords": ["某敏感词"]})
    assert j2.judge("不涉密", "不涉密", "项目", "含某敏感词的图")[0] == "涉密"


# ---------------------------------------------------------------- 项目关联
def test_linker():
    projects = [{"uuid": "a" * 32, "name": "内蒙古某地1∶5万区域矿产地质调查", "code": "NMKD2012-44", "level": "二级项目"},
                {"uuid": "b" * 32, "name": "江西省某矿区普查", "code": "", "level": "三级项目"}]
    lk = ProjectLinker(projects)
    assert lk.link({"XMBH": "nmkd2012-44"}).status == CONFIRMED
    assert lk.link({"XMMC": "江西省某矿区普查"}).status == CONFIRMED
    assert lk.link({"BGTM": "江西省某矿区普查地质报告"}).status == SUSPECT
    assert lk.link({"XMMC": "完全无关的项目"}).status == UNMATCHED
    assert ProjectLinker([]).link({"XMMC": "x"}).status == UNMATCHED


# ---------------------------------------------------------------- ML
def test_ml_parse():
    assert parse_sub_dir("正文@Z01_0001.ml\n审批@S01_0002.ml\n") == [("正文", "Z01_0001.ml"), ("审批", "S01_0002.ml")]
    es = parse_doc_ml("新疆某地铜多金属资@1\n源评价报告@1\n")
    assert len(es) == 1 and es[0].title == "新疆某地铜多金属资源评价报告"
    es = parse_doc_ml("审查意见书@1\n评审意见书@2\n附件@5\n", total_pages=8)
    assert [(e.start_page, e.end_page) for e in es] == [(1, 1), (2, 4), (5, 8)]


# ---------------------------------------------------------------- 只读保护
def test_write_guard(tmp_path):
    src = tmp_path / "原始区"
    out = tmp_path / "成果区"
    src.mkdir()
    out.mkdir()
    (src / "a.pdf").write_bytes(b"x")
    g = WriteGuard(out)
    g.add_source(src)
    with pytest.raises(ReadOnlyViolation):
        g.copy(src / "a.pdf", src / "b.pdf")
    with pytest.raises(ReadOnlyViolation):
        g.copy(src / "a.pdf", tmp_path / "外部.pdf")
    g.copy(src / "a.pdf", out / "x" / "a.pdf")
    assert (out / "x" / "a.pdf").exists()
    with pytest.raises(ReadOnlyViolation):
        g.remove_tree(out)


# ---------------------------------------------------------------- 扫描与时期识别（合成档案）
def make_archive(root, kind):
    if kind == "xml":
        (root / "管理性文件").mkdir(parents=True)
        (root / "管理性文件" / "地质资料文件目录.xml").write_text(
            "<?xml version='1.0'?><AJXX><SysVersion>3.1</SysVersion><TM>t</TM></AJXX>", encoding="utf-8")
        for d in ("存档电子文件", "源电子文件", "原始资料电子文件"):
            (root / d).mkdir()
        (root / "存档电子文件" / "Z01_0001.pdf").write_bytes(b"%PDF")
        (root / "源电子文件" / "Z01_0001.doc").write_bytes(b"d")
        (root / "源电子文件" / "T01_0001").mkdir()
        (root / "源电子文件" / "T01_0001" / "a.WT").write_bytes(b"w")
    elif kind == "ml":
        root.mkdir(parents=True)
        (root / "sub_dir.ml").write_text("正文@Z01_0001.ml", encoding="gbk")
        (root / "z01_0001.tif").write_bytes(b"t")
    return root


def test_scan_and_period(tmp_path):
    a = make_archive(tmp_path / "000123", "xml")
    sc = scan_archive(a)
    assert sc.dh == "000123"
    assert sc.file_count == 4 and sc.category_counts == {"T": 1, "Z": 1}
    pr = identify(sc, R)
    assert pr.code == "xianxing_xml" and pr.supported
    b = make_archive(tmp_path / "DZ9", "ml")
    pr = identify(scan_archive(b), R)
    assert pr.code == "zaoqi" and not pr.supported
    c = tmp_path / "未知"
    c.mkdir()
    (c / "x.txt").write_text("x")
    assert identify(scan_archive(c), R).needs_review


def test_resolve_case_insensitive(tmp_path):
    a = make_archive(tmp_path / "1", "ml")
    sc = ArchiveScan(dh="1", root=a)
    assert sc.resolve("SUB_DIR.ML") == "sub_dir.ml"
    assert sc.resolve("不存在.ml") is None


# ---------------------------------------------------------------- 交接清单
def test_handover(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "第1批"
    ws.append(["交接地质资料数据清单"])
    ws.append(["序号", "档号", "正文", "审批", "附图", "附表", "附件", "软件", "数据库", "多媒体", "其他",
               "底/D", "测/C", "观/G", "探/T", "样/Y", "试/S", "录/L", "像/X", "综/Z", "文/W", "文件数", "文件夹数", "数据量"])
    ws.append([1, 123, 1, 0, 1, 0, 0, 0, 0, 0, 0] + [0] * 10 + [4, 5, "1 KB"])
    ws.append([None] * 24)
    ws.append(["制表人："])
    p = tmp_path / "清单.xlsx"
    wb.save(p)
    rows = read_handover(p)
    assert len(rows) == 1 and rows[0].dh == "123" and rows[0].counts["Z"] == 1
    sc = scan_archive(make_archive(tmp_path / "000123", "xml"))
    status, diffs = reconcile(sc, rows[0], size_tolerance=10)
    assert status == "一致", diffs
    rows[0].file_count = 9
    assert reconcile(sc, rows[0], size_tolerance=10)[0] == "数量差异"
    assert reconcile(sc, None)[0] == "清单外"
