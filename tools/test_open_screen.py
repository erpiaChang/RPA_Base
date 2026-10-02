r"""[조작 — 저장 없음] 물류대기·물류관리 화면을 **열기만** 한다 (09-29, 건수 조사용).

흐름과 같은 함수로 화면을 연다 — 저장·보류·개별배송·자동/수동은 누르지 않는다.
열린 뒤의 구조는 읽기 전용 도구로 본다 (`probe_screen --all`, `probe_grid_raw`).

    .venv\Scripts\python.exe -m tools.test_open_screen --pid <PID> --screen wait        # 물류대기 [일반] 탭 → [조회]
    .venv\Scripts\python.exe -m tools.test_open_screen --pid <PID> --screen stock       # 물류대기 [상품별 재고검토] → [조회]
    .venv\Scripts\python.exe -m tools.test_open_screen --pid <PID> --screen logistics   # 물류관리 → [등록(I)] (화면 모드)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from config.settings import SETTINGS  # noqa: E402
from utils import ui  # noqa: E402
from utils.logger import setup_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="물류 화면 열기 (저장 없음)")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--screen", required=True, choices=("wait", "stock", "logistics"))
    parser.add_argument("--count", action="store_true",
                        help="전표 수를 센다 (ui.distinct_count — 끝까지 내리고 [일반] 은 전표번호까지 가로로 넘긴다)")
    args = parser.parse_args()

    setup_logging()
    from automation import logistics, logistics_wait
    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    target = connect(SETTINGS.target_exe, pid=args.pid)
    if args.screen == "wait":
        screen = logistics_wait.open_screen(target)
        logistics_wait.activate_tab(screen, logistics_wait.TAB_GENERAL)
        logistics_wait.click_search(screen)
        grid = logistics_wait._general_grid(screen)
        rows = logistics_wait._wait_rows(grid, "일반 탭 그리드", timeout=SETTINGS.timeouts.dialog)
        print(f"물류대기 [일반] 보이는 행 {rows} (화면 밖 행 있음: {ui.has_hidden_rows(grid)})")
        if args.count:
            print(f"  전표 수 (count_general_slips): {logistics_wait.count_general_slips(grid)}")
            print(f"  되돌린 뒤 맨 앞 컬럼 보임: {ui.search(grid, title='보류', control_type='Header') != []}")
    elif args.screen == "stock":
        screen = logistics_wait.open_screen(target)
        logistics_wait.activate_tab(screen, logistics_wait.TAB_STOCK_REVIEW)
        logistics_wait.click_search(screen)
        for auto_id in (logistics_wait.TOP_GRID_AUTO_ID, logistics_wait.BOTTOM_GRID_AUTO_ID):
            grid = logistics_wait._grid(screen, auto_id, auto_id)
            rows = logistics_wait._wait_rows(grid, auto_id, timeout=SETTINGS.timeouts.dialog)
            print(f"물류대기 [재고검토] {auto_id} 보이는 행 {rows}")
            if args.count and auto_id == logistics_wait.BOTTOM_GRID_AUTO_ID:
                print(f"  전표 수: {ui.distinct_count(grid, '전표번호')}")
    else:
        screen = logistics.open_screen(target)
        rows = logistics.prepare(screen)
        print(f"물류관리 하단 보이는 행 {rows} / 상단 {ui.visible_row_count(logistics.top_grid(screen))}")
        if args.count:
            print(f"  전표 수 (count_slips): {logistics.count_slips(logistics.bottom_grid(screen))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
