r"""[조사 전용 / 읽기 전용] 그리드 행의 **원시(Raw) UIA 트리**와 LegacyIAccessible 값을 뽑는다 (09-29).

`probe_screen` 은 컨트롤 보기(Control view)만 본다. 거기에는 그리드 **좌측 숫자**(행 인디케이터)가
없다 — 주문매핑 하단 그리드의 행은 `row Check Box` 셀부터 시작한다. 사용자는 건수를 행 수가 아니라
그 숫자로 세라고 했다 (그룹 행·한 전표의 여러 상품 행). 원시 보기·Legacy 속성에 숫자가 있는지 본다.

    .venv\Scripts\python.exe -m tools.probe_grid_raw --pid <PID> --grid gridCtrl_Order
    .venv\Scripts\python.exe -m tools.probe_grid_raw --pid <PID> --grid <물류관리 그리드 auto_id> --rows 8

클릭하지 않는다. 결과는 화면에만 찍는다 (파일로 남기지 않는다).
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


def _legacy(element) -> str:
    """LegacyIAccessible 의 이름·값·설명·역할. 없으면 빈 문자열."""
    import comtypes.gen.UIAutomationClient as UIA

    try:
        pattern = element.GetCurrentPattern(UIA.UIA_LegacyIAccessiblePatternId)
        legacy = pattern.QueryInterface(UIA.IUIAutomationLegacyIAccessiblePattern)
    except Exception:           # noqa: BLE001 — 패턴이 없는 요소
        return ""
    parts = []
    for label, getter in (("L이름", "CurrentName"), ("L값", "CurrentValue"),
                          ("L설명", "CurrentDescription"), ("L역할", "CurrentRole"),
                          ("L상태", "CurrentState")):
        try:
            value = getattr(legacy, getter)
        except Exception:       # noqa: BLE001
            continue
        if value not in ("", None, 0):
            parts.append(f"{label}={value!r}")
    return " ".join(parts)


def _patterns(element) -> list[str]:
    """Grid/Table 패턴 — 전체 행·열 수, 열 머리글, **행 머리글(좌측 숫자 후보)**. 없으면 그렇다고."""
    import comtypes.gen.UIAutomationClient as UIA

    out = []
    try:
        grid = element.GetCurrentPattern(UIA.UIA_GridPatternId).QueryInterface(UIA.IUIAutomationGridPattern)
        out.append(f"GridPattern RowCount={grid.CurrentRowCount} ColumnCount={grid.CurrentColumnCount}")
    except Exception as exc:    # noqa: BLE001
        out.append(f"GridPattern 없음 ({type(exc).__name__})")
    try:
        table = element.GetCurrentPattern(UIA.UIA_TablePatternId).QueryInterface(UIA.IUIAutomationTablePattern)
        for label, getter in (("열 머리글", table.GetCurrentColumnHeaders), ("행 머리글", table.GetCurrentRowHeaders)):
            array = getter()
            names = [array.GetElement(i).CurrentName for i in range(array.Length)] if array else []
            out.append(f"TablePattern {label} {len(names)}개: {names[:60]}")
    except Exception as exc:    # noqa: BLE001
        out.append(f"TablePattern 없음 ({type(exc).__name__})")
    try:
        scroll = element.GetCurrentPattern(UIA.UIA_ScrollPatternId).QueryInterface(UIA.IUIAutomationScrollPattern)
        out.append(f"ScrollPattern 가로 {scroll.CurrentHorizontallyScrollable} "
                   f"{scroll.CurrentHorizontalScrollPercent:.0f}% (보이는 폭 {scroll.CurrentHorizontalViewSize:.0f}%) / "
                   f"세로 {scroll.CurrentVerticallyScrollable} {scroll.CurrentVerticalScrollPercent:.0f}% "
                   f"(보이는 높이 {scroll.CurrentVerticalViewSize:.0f}%)")
    except Exception as exc:    # noqa: BLE001
        out.append(f"ScrollPattern 없음 ({type(exc).__name__})")
    return out


def _line(depth: int, element) -> str:
    try:
        rect = element.CurrentBoundingRectangle
        size = f"({rect.left},{rect.top} {rect.right - rect.left}x{rect.bottom - rect.top})"
    except Exception:           # noqa: BLE001
        size = ""
    try:
        kind = element.CurrentLocalizedControlType or element.CurrentControlType
    except Exception:           # noqa: BLE001
        kind = "?"
    name = element.CurrentName or ""
    auto_id = element.CurrentAutomationId or ""
    extra = _legacy(element)
    return (f"{'  ' * depth}{kind} name={name!r}" + (f" auto_id={auto_id!r}" if auto_id else "")
            + f" {size} {extra}").rstrip()


def _walk(walker, element, depth: int, limit: int, out: list[str]) -> None:
    out.append(_line(depth, element))
    if depth >= limit:
        return
    child = walker.GetFirstChildElement(element)
    while child:                # 끝이면 NULL 포인터(거짓)가 온다 — None 이 아니다
        _walk(walker, child, depth + 1, limit, out)
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:       # noqa: BLE001
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="그리드 원시 UIA 트리 (읽기 전용)")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--grid", required=True, help="그리드 auto_id (예: gridCtrl_Order)")
    parser.add_argument("--rows", type=int, default=5, help="앞에서 몇 행까지")
    parser.add_argument("--depth", type=int, default=3, help="그리드 아래 몇 층까지")
    args = parser.parse_args()

    setup_logging()
    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    target = connect(SETTINGS.target_exe, pid=args.pid)
    found = ui.search(target.main_window(), auto_id=args.grid)     # 깊이 제한 없이 — 물류대기 하단은 깊다
    if not found:
        print(f"그리드 {args.grid} 를 찾지 못했다.")
        return 1
    element = found[0].element_info.element
    from pywinauto.uia_defines import IUIA

    walker = IUIA().iuia.RawViewWalker
    out: list[str] = _patterns(element)
    # 그리드 바로 아래(스크롤바·헤더 패널·데이터 패널 등)를 원시 보기로 — 행은 앞 몇 개만
    out.append(_line(0, element))
    child = walker.GetFirstChildElement(element)
    while child:
        name = child.CurrentName or ""
        if name == "데이터 패널" or name.startswith("Data Panel"):
            out.append(_line(1, child))
            row = walker.GetFirstChildElement(child)
            shown = 0
            while row and shown < args.rows:
                _walk(walker, row, 2, args.depth + 1, out)
                shown += 1
                row = walker.GetNextSiblingElement(row)
        else:
            _walk(walker, child, 1, args.depth, out)
        child = walker.GetNextSiblingElement(child)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
