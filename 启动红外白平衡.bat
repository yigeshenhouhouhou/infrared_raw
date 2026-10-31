@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Infrared RAW Studio - 590 nm 0.3
set "TEMP=%~dp0runtime\tmp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%~dp0runtime\pip-cache"
set "PYTHONDONTWRITEBYTECODE=1"
set "IR_WB_OUT=%~dp0output"
if not exist "%TEMP%" mkdir "%TEMP%"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 请先运行 安装环境.bat
  pause
  exit /b 1
)
"%PY%" -B ir_wb_gui.py
if errorlevel 1 pause
