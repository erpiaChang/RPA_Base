@echo off
chcp 65001 >nul
rem 빌드 프로그램. 설정값을 보고 고쳐서 그 값대로 실행용 exe 를 만든다 (gui\build_app.py).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main_build.py
if errorlevel 1 pause
