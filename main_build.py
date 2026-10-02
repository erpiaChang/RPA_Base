r"""[개발 전용] 빌드 프로그램 진입점 — `build_tool.bat`.

    .venv\Scripts\python.exe main_build.py

설정값을 화면에서 보고 고쳐서 그 값대로 실행용 exe(`dist/run/RPA_1.exe`)를 만든다.
배포본에는 들어가지 않는다.
"""
from __future__ import annotations

import sys

from gui.build_app import run

if __name__ == "__main__":
    sys.exit(run())
