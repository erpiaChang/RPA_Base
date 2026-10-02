@echo off
rem 통합 자동화 실행 파일 빌드. 산출물: dist\full\Integrated_RPA.exe
rem
rem ★ 기능별로 폴더를 나눈다. 예전에는 세 배포본이 dist\ 를 공유하고
rem   dist\config\settings.local.json 하나를 함께 썼다. 그래서
rem     (1) 한 exe 를 실제 계정으로 한 번 돌리면 그 파일에 평문 계정이 남고
rem     (2) 'if not exist' 가드 때문에 재빌드해도 지워지지 않고
rem     (3) 빌드 끝에 그 파일을 '함께 배포' 하라고 안내했다.
rem   실제로 같은 경로로 사고가 한 번 있었다 (docs/PROGRESS.md).
rem   지금은 폴더를 나누고 **매 빌드마다 샘플로 덮어쓴다**.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다.
    pause
    exit /b 1
)

echo ====================================================
echo  통합 자동화 빌드
echo ====================================================

".venv\Scripts\python.exe" -m PyInstaller --version >nul 2>&1
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install pyinstaller

rem 이전 산출물을 지운다. 남아 있으면 옛 코드와 옛 설정이 섞인다.
if exist "build\build_full" rmdir /s /q "build\build_full"
if exist "dist\full" rmdir /s /q "dist\full"

".venv\Scripts\python.exe" -m PyInstaller build_full.spec --noconfirm --distpath "dist\full"
if errorlevel 1 (
    echo.
    echo [실패] 빌드가 중단됐습니다. 위 메시지를 확인하세요.
    pause
    exit /b 1
)

rem 설정은 **샘플로만** 넣는다. copy /y 라 이전 파일이 있어도 덮어쓴다.
if not exist "dist\full\config" mkdir "dist\full\config"
if exist "config\settings.sample.json" copy /y "config\settings.sample.json" "dist\full\config\settings.local.json" >nul

echo.
echo  완료: dist\full\Integrated_RPA.exe
echo  같이 주는 것: dist\full\config\settings.local.json  (빈 샘플)
echo.
echo  [주의] 이 exe 를 실제 계정으로 실행하면 위 파일에 계정이 **평문으로**
echo         기록됩니다. 동작을 확인할 때는 dist 밖에 복사해서 실행하고,
echo         계정이 채워진 파일은 절대 배포하지 마세요.
echo         (로그도 exe 옆 logs\ 에 쌓입니다. 배포 전에 비우세요)
pause
