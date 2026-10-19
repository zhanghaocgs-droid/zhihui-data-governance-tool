"""图形界面启动入口，也用于 PyInstaller 打包。"""
import sys

from dgt.gui.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
