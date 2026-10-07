@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 金融投研智能体 - 本地分析台

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto environment_ready

set "BASE_PYTHON="
if defined FIN_AGENT_PYTHON if exist "%FIN_AGENT_PYTHON%" set "BASE_PYTHON=%FIN_AGENT_PYTHON%"
if not defined BASE_PYTHON for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined BASE_PYTHON set "BASE_PYTHON=%%I"
if not defined BASE_PYTHON if exist "%USERPROFILE%\anaconda3\python.exe" set "BASE_PYTHON=%USERPROFILE%\anaconda3\python.exe"
if not defined BASE_PYTHON if exist "%USERPROFILE%\miniconda3\python.exe" set "BASE_PYTHON=%USERPROFILE%\miniconda3\python.exe"
if not defined BASE_PYTHON if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "BASE_PYTHON=%LOCALAPPDATA%\anaconda3\python.exe"
if not defined BASE_PYTHON if exist "%LOCALAPPDATA%\miniconda3\python.exe" set "BASE_PYTHON=%LOCALAPPDATA%\miniconda3\python.exe"
if not defined BASE_PYTHON goto python_missing

"%BASE_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto python_old

echo [1/3] 正在创建本地 Python 环境...
"%BASE_PYTHON%" -m venv ".venv"
if errorlevel 1 goto startup_failed
echo [2/3] 正在安装锁定依赖，首次运行需要网络...
"%PYTHON_EXE%" -m pip install --disable-pip-version-check -r "requirements-lock.txt"
if errorlevel 1 goto startup_failed

:environment_ready
set "PYTHONPATH=%CD%\src"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

"%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1)" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8000/"
  exit /b 0
)

"%PYTHON_EXE%" -m fin_agent.cli doctor
if errorlevel 1 (
  echo 检测到依赖不完整，正在修复...
  "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r "requirements-lock.txt"
  if errorlevel 1 goto startup_failed
)

echo [3/3] 正在启动本地分析台：http://127.0.0.1:8000
echo 关闭此窗口会停止本地服务。
"%PYTHON_EXE%" -m fin_agent.cli serve --host 127.0.0.1 --port 8000 --open-browser
if errorlevel 1 goto startup_failed
exit /b 0

:python_missing
echo 未找到 Python 3.10 或以上版本。
echo 如已安装 Anaconda，请确认安装位置为用户目录下的 anaconda3 或 miniconda3。
goto startup_failed

:python_old
echo 检测到的 Python 版本低于 3.10，请升级后重试。
goto startup_failed

:startup_failed
echo.
echo 启动失败。请保留本窗口中的错误信息，以便排查。
pause
exit /b 1
