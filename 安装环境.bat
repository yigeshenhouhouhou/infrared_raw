@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "TEMP=%~dp0runtime\tmp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%~dp0runtime\pip-cache"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%TEMP%" mkdir "%TEMP%"
python -B -m venv "%~dp0.venv"
if errorlevel 1 goto fail
"%~dp0.venv\Scripts\python.exe" -B -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto fail
echo 安装成功，请运行 启动红外白平衡.bat
pause
exit /b 0
:fail
echo 安装失败，请检查 Python 和网络连接。
pause
exit /b 1
