# 打包离线安装目录。在联网的开发机上执行，产物 dist\地质资料数据治理工具 整体拷入涉密机。
# 依赖的 wheel 先用 pip download -r requirements.txt -d wheels 下载，供甲方检查。
python -m PyInstaller --noconfirm --windowed --name 地质资料数据治理工具 `
  --add-data "rules;rules" `
  --collect-submodules dgt `
  --hidden-import win32com.client --hidden-import pythoncom `
  run_gui.py
Copy-Item -Recurse -Force rules dist\地质资料数据治理工具\rules
