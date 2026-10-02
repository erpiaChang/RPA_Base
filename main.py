r"""[개발 전용] 진입점 — 어떤 기능을 띄울지 고른다.

배포본은 이 파일을 쓰지 않는다. 기능별 진입점이 따로 있다.

    main_collect.py   주문 엑셀 수집
    main_erpia.py     ERPia 자동화
    main_full.py      통합
    main_run.py       실행용 (설정을 빌드에 굽는다)
"""
from __future__ import annotations

import sys

from gui.launcher import run

if __name__ == "__main__":
    sys.exit(run())
