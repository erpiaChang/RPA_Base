@echo off
rem 업무 화면 구조 조사 - 읽기 전용. 클릭하지 않는다.
rem 사용법: probe_screen.bat                     열려 있는 화면 전부
rem         probe_screen.bat Frm_Management_HoldLogisticsStandard
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 관리자 권한으로 다시 실행합니다. UAC 창에서 [예]를 눌러주세요.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%1' -Verb RunAs"
    exit /b
)

echo ====================================================
echo  업무 화면 구조 조사 (읽기 전용)
echo ====================================================
echo.
if "%~1"=="" (
    ".venv\Scripts\python.exe" -m tools.probe_screen
) else (
    ".venv\Scripts\python.exe" -m tools.probe_screen --auto-id %1
)
echo.
pause
