@echo off
chcp 65001 >nul
rem 실행용 빌드 (콘솔). 산출물: dist\run\RPA_1.exe
rem 화면에서 설정을 보고 고쳐서 빌드하려면 build_tool.bat. 둘 다 tools\build_run.py 를 쓴다.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m tools.build_run
pause
