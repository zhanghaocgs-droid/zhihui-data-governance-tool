"""原件只读保护。所有写操作都要经过 WriteGuard 校验目标路径。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


class ReadOnlyViolation(RuntimeError):
    """试图在原始区写入时抛出。"""


class WriteGuard:
    def __init__(self, output_root: Path | str):
        self.output_root = Path(output_root).resolve()
        self._sources: list[Path] = []

    def add_source(self, root: Path | str) -> None:
        p = Path(root).resolve()
        if p not in self._sources:
            self._sources.append(p)

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def check(self, target: Path | str) -> Path:
        t = Path(os.path.abspath(target))
        for s in self._sources:
            if self._under(t, s):
                raise ReadOnlyViolation(f"禁止写入原始区：{t}")
        if not self._under(t, self.output_root):
            raise ReadOnlyViolation(f"写入目标不在成果区内：{t}")
        return t

    # ---- 受控写操作 ----
    def makedirs(self, path: Path | str) -> Path:
        p = self.check(path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def copy(self, src: Path | str, dst: Path | str) -> Path:
        d = self.check(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
        return d

    def link_or_copy(self, src: Path | str, dst: Path | str) -> Path:
        """在成果区内派生副本，优先硬链接。src 也必须位于成果区。"""
        self.check(src)
        d = self.check(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            d.unlink()
        try:
            os.link(src, d)
        except OSError:
            shutil.copy2(src, d)
        return d

    def remove_tree(self, path: Path | str) -> None:
        p = self.check(path)
        if p == self.output_root:
            raise ReadOnlyViolation("不允许删除整个成果区")
        if p.exists():
            shutil.rmtree(p)

    def remove_file(self, path: Path | str) -> None:
        p = self.check(path)
        if p.exists():
            p.unlink()
