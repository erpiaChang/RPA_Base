r"""ERPia 자동화 — 진입점.

    .venv\Scripts\python.exe main_erpia.py

이 파일이 배포본의 시작점이다. **다른 기능은 import 하지 않는다.**
PyInstaller 는 여기서 도달하는 것만 번들에 넣는다.
"""
from __future__ import annotations

import sys

from gui.erpia_app import run

if __name__ == "__main__":
    sys.exit(run())
