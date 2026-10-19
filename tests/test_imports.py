"""所有模块都能导入，防止打包时因语法错误漏掉模块。"""
import importlib
import pkgutil

import dgt


def test_all_modules_import():
    for m in pkgutil.walk_packages(dgt.__path__, "dgt."):
        importlib.import_module(m.name)
