@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CET-6 听力分节播放器

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY if exist "D:\anaconda3\python.exe" set "PY=D:\anaconda3\python.exe"
if not defined PY if exist "C:\anaconda3\python.exe" set "PY=C:\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if not defined PY (
  echo.
  echo   没有找到 Python，无法启动。
  echo   其实也可以直接双击「CET-6 听力分节播放器.html」使用，
  echo   只是「导出音频」按钮会用不了。
  echo.
  pause
  exit /b 1
)

"%PY%" server.py
if errorlevel 1 (
  echo.
  echo   程序意外退出了，上面是错误信息。
  pause
)
