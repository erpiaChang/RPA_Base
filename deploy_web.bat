@echo off
chcp 65001 >nul
rem 웹 대시보드 배포 (09-28). build\web 의 정적 파일을 Cloudflare Workers 로 올린다 (web\wrangler.jsonc).
rem git 의 web\ 에는 Supabase 주소·키 대신 자리표시가 있다 (10-02) — .env 값으로 채워 build\web 에 만든 뒤 올린다.
rem 처음 한 번:  npx wrangler@4 login   (브라우저에서 Cloudflare 로그인. 토큰은 사용자 프로필에 남고 프로젝트에는 없다)
rem wrangler@4 인 이유: 이 PC 의 node 22. 큰 버전을 고정해 어느 PC 에서나 같은 판이 돈다.
cd /d "%~dp0"

"%~dp0.venv\Scripts\python.exe" -m tools.build_web
if errorlevel 1 (
    echo [실패] .env 의 SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY 를 확인하세요. 꼴은 .env.example.
    pause
    exit /b 1
)

where npx >nul 2>nul
if errorlevel 1 (
    echo [오류] node/npx 가 없습니다. https://nodejs.org 에서 설치하세요.
    pause
    exit /b 1
)

cd /d "%~dp0web"
call npx wrangler@4 deploy
if errorlevel 1 (
    echo.
    echo [실패] 위 메시지를 확인하세요. 로그인이 안 됐으면:  npx wrangler@4 login
    pause
    exit /b 1
)
echo.
echo [완료] https://rpa-dashboard.rpa-dashboard.workers.dev 에 올라갔습니다.
pause
