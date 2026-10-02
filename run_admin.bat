@echo off
rem RPA 런처를 관리자 권한으로 실행한다.
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 관리자 권한으로 다시 실행합니다. UAC 창에서 [예]를 눌러주세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 pause

