@echo off
rem 창 목록 조사 (읽기 전용). 결과를 docs 에 저장한다.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.survey_windows --save %*
pause
