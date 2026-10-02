r"""[조사 전용 / 읽기 전용] 지금 열려 있는 업무 화면의 구조를 뽑는다.

창 전체 덤프(`dump_controls.py`)는 그리드 한 개가 1,000줄을 넘겨서
정작 필요한 버튼과 그리드 식별자가 묻히고, 도중에 끊기기도 한다.

이 도구는 **그리드 안의 행/셀을 건너뛰고** 구조만 뽑는다.
Pane / Tab / Button / Table / Edit 처럼 조작 대상이 되는 것만 남는다.

    .venv\Scripts\python.exe -m tools.probe_screen
    .venv\Scripts\python.exe -m tools.probe_screen --auto-id Frm_Management_HoldLogisticsStandard
    .venv\Scripts\python.exe -m tools.probe_screen --all      (행/셀까지 전부)

클릭하지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from config.settings import DOCS_DIR, SETTINGS  # noqa: E402
from utils import ui  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

# 그리드 내부. 수가 많고 값은 실행할 때마다 달라진다. 식별자 조사에 쓸모가 없다.
SKIP_DESCEND = {"DataItem", "ListItem", "Header", "HeaderItem", "ScrollBar", "Thumb"}
# 같은 종류가 수십 개씩 나오면 앞의 몇 개만 본다.
SAME_TYPE_LIMIT = 4


def _info(ctrl):
    info = ctrl.element_info
    return (info.control_type or "?", info.automation_id or "", info.name or "")


def _line(depth: int, ctrl) -> str:
    control_type, auto_id, name = _info(ctrl)
    try:
        rect = ctrl.rectangle()
        size = f"{rect.width()}x{rect.height()}"
    except Exception:
        size = "?"
    parts = [f"{'  ' * depth}{control_type}"]
    if auto_id:
        parts.append(f'auto_id="{auto_id}"')
    if name:
        parts.append(f"name={name!r}")
    if control_type == "DataItem":         # --all 에서만 온다. 셀 값도 본다 (09-22)
        parts.append(f"값={ui.cell_value(ctrl)!r}")
    parts.append(f"({size})")
    return "  ".join(parts)


def walk(ctrl, depth: int, out: list, keep_all: bool) -> None:
    out.append(_line(depth, ctrl))

    control_type = _info(ctrl)[0]
    if not keep_all and control_type in SKIP_DESCEND:
        return

    try:
        children = ctrl.children()
    except Exception as exc:
        out.append(f"{'  ' * (depth + 1)}(자식을 읽지 못함: {type(exc).__name__})")
        return

    seen: dict[str, int] = {}
    for child in children:
        child_type = _info(child)[0]
        seen[child_type] = seen.get(child_type, 0) + 1
        if not keep_all and seen[child_type] == SAME_TYPE_LIMIT + 1:
            total = sum(1 for c in children if _info(c)[0] == child_type)
            out.append(f"{'  ' * (depth + 1)}... {child_type} {total}개 (이하 생략)")
        if not keep_all and seen[child_type] > SAME_TYPE_LIMIT:
            continue
        walk(child, depth + 1, out, keep_all)


def _screens(main_window) -> list:
    """열려 있는 업무 화면 Pane 목록. auto_id가 Frm_ 으로 시작한다."""
    found = []
    for pane in ui.search(main_window, control_type="Pane"):
        auto_id = _info(pane)[1]
        if auto_id.startswith("Frm_"):
            found.append(pane)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="업무 화면 구조 조사 (읽기 전용)")
    parser.add_argument("--auto-id", default=None, help="조사할 화면 Pane의 auto_id")
    parser.add_argument("--pid", type=int, default=None, help="대상 ERPia 인스턴스 PID")
    parser.add_argument("--all", action="store_true", help="그리드 행/셀까지 전부")
    args = parser.parse_args()

    setup_logging()
    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()

    if not SETTINGS.target_exe:
        log.error("config/settings.local.json 에 target_exe 가 없다.")
        return 1
    target = connect(SETTINGS.target_exe, pid=args.pid)
    if target is None:
        log.error("ERPia가 실행 중이 아니다.")
        return 1

    main_window = target.main_window()
    screens = _screens(main_window)
    if not screens:
        log.error("열려 있는 업무 화면(Frm_* Pane)을 찾지 못했다. 화면을 먼저 열 것.")
        return 1

    print()
    print("열려 있는 화면:")
    for pane in screens:
        print(f"  - {_info(pane)[1]}")

    if args.auto_id:
        targets = [p for p in screens if _info(p)[1] == args.auto_id]
        if not targets:
            log.error("auto_id=%r 인 화면이 없다.", args.auto_id)
            return 1
    else:
        targets = screens

    out: list[str] = []
    for pane in targets:
        name = _info(pane)[1]
        out.append("")
        out.append("=" * 78)
        out.append(f" {name}")
        out.append("=" * 78)
        walk(pane, 0, out, args.all)

    text = "\n".join(out)
    print(text)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = args.auto_id or "screens"
    path = DOCS_DIR / f"_probe_{label}_{stamp}.txt"
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"\n저장: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
