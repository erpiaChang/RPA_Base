@echo off
rem LLM worker (usage QnA). No window. Log: logs\llm\llm_YYYYMMDD.log. Exits at once if already running.
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe llm\worker.py
