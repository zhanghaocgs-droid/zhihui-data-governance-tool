"""Word 转 PDF。按配置优先级尝试 Word、WPS、LibreOffice 便携版，均不可用时返回失败原因。

源文件先复制到成果区临时目录再转换，避免办公软件在原始区生成锁文件。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_lock = threading.Lock()


class ConvertError(RuntimeError):
    pass


def pdf_pages(path: Path | str) -> int | None:
    try:
        import pymupdf
        with pymupdf.open(path) as d:
            return d.page_count
    except Exception:
        return None


def _office_com(progid: str, src: Path, dst: Path) -> int | None:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = None
    doc = None
    try:
        app = win32com.client.DispatchEx(progid)
        app.Visible = False
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass
        doc = app.Documents.Open(str(src), False, True)  # ConfirmConversions, ReadOnly
        pages = None
        try:
            pages = int(doc.ComputeStatistics(2))  # wdStatisticPages
        except Exception:
            pass
        doc.ExportAsFixedFormat(str(dst), 17)  # wdExportFormatPDF
        return pages
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        finally:
            if app is not None:
                app.Quit()
            pythoncom.CoUninitialize()


def _libreoffice(exe: str, src: Path, dst: Path) -> int | None:
    outdir = dst.parent
    r = subprocess.run([exe, "--headless", "--norestore", "--convert-to", "pdf", "--outdir",
                        str(outdir), str(src)], capture_output=True, timeout=600)
    produced = outdir / (src.stem + ".pdf")
    if r.returncode != 0 or not produced.exists():
        raise ConvertError(r.stderr.decode(errors="ignore")[:200] or "LibreOffice 转换失败")
    if produced != dst:
        produced.replace(dst)
    return None


def find_libreoffice(configured: str = "") -> str | None:
    cands = [configured] if configured else []
    cands += [
        str(Path(__file__).resolve().parents[2] / "tools" / "LibreOfficePortable" / "App" / "libreoffice" / "program" / "soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return shutil.which("soffice")


class Converter:
    def __init__(self, order: list[str], libreoffice_path: str = "", work_dir: Path | None = None):
        self.order = list(order or [])
        self.lo = libreoffice_path
        self.work_dir = work_dir

    def available(self) -> list[str]:
        out = []
        for name in self.order:
            if name in ("word", "wps"):
                try:
                    import winreg
                    key = "Word.Application" if name == "word" else "KWPS.Application"
                    winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key))
                    out.append(name)
                except Exception:
                    pass
            elif name == "libreoffice" and find_libreoffice(self.lo):
                out.append(name)
        return out

    def to_pdf(self, src: Path, dst: Path) -> dict:
        """转换成功返回 {engine, src_pages, pdf_pages}，失败抛 ConvertError。"""
        engines = self.available()
        if not engines:
            raise ConvertError("本机没有可用的 Word、WPS 或 LibreOffice 转换器")
        dst.parent.mkdir(parents=True, exist_ok=True)
        errors = []
        tmp_root = self.work_dir or dst.parent
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as td:
            tmp_src = Path(td) / ("src" + src.suffix.lower())
            shutil.copy2(src, tmp_src)
            os.chmod(tmp_src, 0o666)
            tmp_dst = Path(td) / "out.pdf"
            for eng in engines:
                try:
                    with _lock:
                        if eng == "word":
                            sp = _office_com("Word.Application", tmp_src, tmp_dst)
                        elif eng == "wps":
                            sp = _office_com("KWPS.Application", tmp_src, tmp_dst)
                        else:
                            sp = _libreoffice(find_libreoffice(self.lo), tmp_src, tmp_dst)
                    if not tmp_dst.exists():
                        raise ConvertError("未生成 PDF")
                    pp = pdf_pages(tmp_dst)
                    if not pp:
                        raise ConvertError("生成的 PDF 无法打开")
                    shutil.move(str(tmp_dst), dst)
                    return {"engine": eng, "src_pages": sp, "pdf_pages": pp}
                except Exception as e:  # 换下一个转换器
                    errors.append(f"{eng}:{e}")
        raise ConvertError("；".join(errors))
