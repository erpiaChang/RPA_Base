@echo off
rem RPA 런처 실행. 시스템 python이 아니라 .venv 인터프리터를 쓴다.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다. 먼저 아래를 실행하세요.
    echo     py -3 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
