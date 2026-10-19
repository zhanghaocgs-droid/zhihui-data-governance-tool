"""命令行入口。

python -m dgt.cli --ws 工作区 handover 交接清单.xlsx
python -m dgt.cli --ws 工作区 projects 项目对照表.xlsx
python -m dgt.cli --ws 工作区 scan 数据目录
python -m dgt.cli --ws 工作区 run [--from 档号] [--to 档号] [--force]
python -m dgt.cli --ws 工作区 qc
python -m dgt.cli --ws 工作区 export
python -m dgt.cli gui
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dgt", description="地质资料数据治理工具")
    ap.add_argument("--ws", default="workspace", help="工作区目录，存放数据库和成果区")
    ap.add_argument("--rules", default=None, help="规则目录，默认使用程序自带规则")
    ap.add_argument("--operator", default="", help="操作人")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("handover").add_argument("path")
    sub.add_parser("projects").add_argument("path")
    sub.add_parser("scan").add_argument("data_root")
    r = sub.add_parser("run")
    r.add_argument("--from", dest="dh_from")
    r.add_argument("--to", dest="dh_to")
    r.add_argument("--force", action="store_true")
    r.add_argument("--no-convert", action="store_true", help="不做 Word 转 PDF")
    sub.add_parser("qc")
    sub.add_parser("export")
    sub.add_parser("gui")
    a = ap.parse_args(argv)

    if a.cmd == "gui":
        from .gui.app import main as gui_main
        return gui_main()

    from .pipeline import Engine
    eng = Engine(Path(a.ws), a.rules, a.operator)
    if a.cmd == "handover":
        print(f"导入 {eng.import_handover(a.path)} 档")
    elif a.cmd == "projects":
        n, problems = eng.import_projects(a.path)
        print(f"导入 {n} 个项目")
        for p in problems:
            print("  问题：", p)
    elif a.cmd == "scan":
        n = eng.scan(a.data_root, progress=lambda dh, i: print(f"  扫描 {dh}"))
        print(f"扫描 {n} 档")
    elif a.cmd == "run":
        if a.no_convert:
            eng.set_converters([])
        bid = eng.run_batch(a.dh_from, a.dh_to, a.force, progress=lambda dh, i, n: print(f"  [{i + 1}/{n}] {dh}"))
        b = eng.db.one("SELECT * FROM batch WHERE id=?", (bid,))
        print(f"批次 {bid} {b['status']}：共 {b['total']} 档，完成 {b['done']}，失败 {b['failed']}")
    elif a.cmd == "qc":
        from .output.qc import run_qc
        summary, failures = run_qc(eng)
        for s in summary:
            print(f"  {s['check']}: 应检 {s['total']} 未通过 {s['failed']}")
        for f in failures[:30]:
            print("   ", f["check"], f["target"], f["message"])
    elif a.cmd == "export":
        from .output.export import export_sets
        res = export_sets(eng)
        print("输出目录", res["dir"])
        for k, v in res["sets"].items():
            print(" ", k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
