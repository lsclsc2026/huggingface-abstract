@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
  echo 首次运行：正在创建虚拟环境...
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3.10 -m venv .venv
    if errorlevel 1 py -3 -m venv .venv
  )
  if errorlevel 1 goto :error
)

echo 正在检查依赖...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :error

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo 启动失败。请检查上方错误信息、Python 版本和网络连接。
pause
exit /b 1
