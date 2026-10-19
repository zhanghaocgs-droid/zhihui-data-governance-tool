# 打包免安装目录。在仓库根目录执行：powershell -ExecutionPolicy Bypass -File tools\build.ps1
# 产物为 dist\地质资料数据治理工具\，整个文件夹拷给同事，双击其中的 exe 运行。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$name = "地质资料数据治理工具"
$work = Join-Path $env:TEMP "dgt_build"
python -m PyInstaller --noconfirm --clean --windowed --name $name --workpath $work --specpath $work `
  --paths . --collect-submodules dgt `
  --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.Qt3DCore `
  --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization `
  --exclude-module tkinter --exclude-module matplotlib --exclude-module pandas --exclude-module numpy `
  --exclude-module pytest `
  run_gui.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# 规则配置放在 exe 旁边，便于查看和调整
Copy-Item -Recurse -Force rules "dist\$name\rules"
Copy-Item -Force docs\操作说明.md "dist\$name\操作说明.md"
Remove-Item -Force -ErrorAction SilentlyContinue "dist\$name\使用说明.md"
