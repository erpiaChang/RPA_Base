r"""[조사 도구 - 읽기 전용] **클릭이 통과하는 오버레이**가 가능한지 확인한다.

우리 tkinter 창만 만든다. 대상 프로그램을 건드리지 않는다.

    .venv\Scripts\python.exe -m tools.probe_overlay

## 왜 이걸 먼저 재나 (2026-09-15)

ERPia 는 최대화돼 화면을 다 덮는다. 진행 상황을 보여 주려면 RPA 창을 그 위에
띄워야 하는데, **그러면 자동화가 깨질 수 있다.**

| 깨지는 이유 | 근거 |
| --- | --- |
| `ui.click` 의 주력 수단이 `click_input()` = **실제 마우스로 그 좌표를 누른다** | `utils/ui.CELL_ICON_METHODS = ("클릭", "Invoke")` |
| 우리 창이 그 좌표를 덮으면 클릭이 **우리 창으로** 간다 | Windows 는 맨 위 창에 보낸다 |
| 우리 창이 포커스를 가져가면 `type_keys(set_foreground=True)` 가 엉뚱한 곳으로 | `ui._scroll_down_once` 의 키보드 폴백 |

해법은 **클릭 통과 + 포커스 안 뺏기**다.

    WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE

여기서 재는 것은 그 조합이 이 PC 에서 **정말 통하는가** 다. 판정은
`WindowFromPoint` 로 한다 — 이 함수는 `WS_EX_TRANSPARENT` 창을 **건너뛴다.**
즉 오버레이 한가운데를 찍었는데 오버레이가 아닌 창이 나오면, 마우스 클릭도
그 창으로 간다는 뜻이다.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from gui.overlay import Overlay  # noqa: E402
from utils import ui  # noqa: E402
from utils.dpi import ensure_dpi_awareness  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

_user32 = ctypes.windll.user32


def _get_ex_style(hwnd: int) -> int:
    return _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def make_click_through(hwnd: int) -> int:
    """그 창을 **클릭이 통과하고 포커스를 뺏지 않는** 창으로 바꾼다."""
    style = _get_ex_style(hwnd)
    style |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
              | WS_EX_TOOLWINDOW)
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    return _get_ex_style(hwnd)


def window_at(x: int, y: int) -> int:
    """그 좌표에서 **마우스 입력을 받게 될** 창. 투명 창은 건너뛴다."""
    point = wintypes.POINT(x, y)
    return _user32.WindowFromPoint(point)


def _window_name(hwnd: int) -> tuple[str, str]:
    """(class_name, title). 못 읽으면 빈 문자열."""
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    class_name = buf.value
    _user32.GetWindowTextW(hwnd, buf, 256)
    return class_name, buf.value


def screen_locked() -> bool:
    """화면이 **잠금 화면에 덮여 있는가.**

    ★ 덮여 있으면 `Windows 기본 잠금 화면`(`Windows.UI.Core.CoreWindow`)이
      화면 전체를 차지해서, `WindowFromPoint` 가 **어느 좌표를 찍어도 그 창**을
      돌려준다. 그 상태에서 클릭 판정을 하면 멀쩡한 코드가 실패로 나온다
      (2026-09-15에 실제로 그렇게 나와 한참 원인을 찾았다).

    ## `OpenInputDesktop` 만으로는 부족하다 (2026-09-16 정정)

    처음에는 그것만 봤는데, **"잠기지 않았다"고 답하면서도 잠금 화면 창이
    맨 위에 남아 있는 상태**가 실제로 있었다(LockApp.exe 가 화면을 푼 뒤에도
    창을 들고 있다). 계측기가 틀린 답을 준 것이다.

    그래서 **직접 본다** — 화면 한가운데의 창이 잠금 화면이면 덮인 것이다.
    이것이 우리가 실제로 신경 쓰는 사실(클릭 판정을 할 수 있는가)과 같다.
    """
    DESKTOP_SWITCHDESKTOP = 0x0100
    handle = _user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    _user32.CloseDesktop(handle)

    center = (_user32.GetSystemMetrics(0) // 2, _user32.GetSystemMetrics(1) // 2)
    class_name, title = _window_name(window_at(*center))
    if class_name == "Windows.UI.Core.CoreWindow" and "잠금" in title:
        log.warning("잠금 화면 창이 맨 위에 있다: %r (%s)", title, class_name)
        return True
    return False


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "그렇다" if ok else "아니다", name,
             f" - {detail}" if detail else "")
    return ok


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="오버레이 확인 (읽기 전용)")
    parser.add_argument("--show", action="store_true",
                        help="판정을 마친 뒤 실제 오버레이를 띄워 둔다 "
                             "([닫기] 를 누르면 끝난다)")
    args = parser.parse_args()

    setup_logging()
    ensure_dpi_awareness()

    import tkinter as tk

    results: list[bool] = []
    # ★ 잠겨 있으면 클릭 판정을 할 수 없다. **못 한 것을 실패로 적지 않는다.**
    locked = screen_locked()
    if locked:
        log.warning("화면이 잠겨 있다. 잠금 화면이 전체를 덮어서 **클릭 판정은 "
                    "건너뛴다.** 잠금을 풀고 다시 돌릴 것.")

    # 기준이 될 '아래 창' 을 하나 만든다. 실제로는 ERPia 자리다.
    under = tk.Tk()
    under.title("[조사] 아래 창 (대상 프로그램 자리)")
    under.geometry("600x400+200+200")
    # ★ 이 창을 맨 위로 올려 둔다. 그러지 않으면 마침 그 자리를 덮고 있는
    #   다른 프로그램 창이 잡혀 **멀쩡한 코드가 실패로 나온다**
    #   (2026-09-16에 실제로 그랬다. 잠금 화면 때와 같은 종류의 착각이다).
    under.attributes("-topmost", True)
    under.update()
    from gui.overlay import _hwnd, _window_chain

    under_chain = set(_window_chain(under))
    under_hwnd = _hwnd(under)

    # 그 위에 오버레이를 겹친다.
    over = tk.Toplevel(under)
    over.title("[조사] 오버레이")
    over.geometry("300x150+350+300")     # 아래 창 한가운데를 덮는다
    over.attributes("-topmost", True)
    over.attributes("-alpha", 0.85)
    over.overrideredirect(True)
    tk.Label(over, text="진행 상황 오버레이", bg="#101820", fg="#e8e8e8").pack(
        fill="both", expand=True)
    over.update()
    over_hwnd = _hwnd(over)

    center = (350 + 150, 300 + 75)      # 오버레이 한가운데

    log.info("▶ 손대기 전 — 보통 창이면 클릭을 가로챈다")
    before = window_at(*center)
    if not locked:
        results.append(check("오버레이가 클릭을 가로챈다 (아직 투명 아님)",
                             before not in (0, under_hwnd),
                             f"그 좌표의 창 handle={before}"))

    log.info("▶ 클릭 통과로 바꾼 뒤")
    # ★ 창을 이루는 HWND 전부에 건다. 뿌리에만 걸면 내용 창이 클릭을 받는다.
    for handle in _window_chain(over):
        style = make_click_through(handle)
    over.update()
    after = window_at(*center)
    results.append(check("WS_EX_TRANSPARENT 가 걸렸다",
                         bool(style & WS_EX_TRANSPARENT)))
    results.append(check("WS_EX_NOACTIVATE 가 걸렸다 (포커스를 뺏지 않는다)",
                         bool(style & WS_EX_NOACTIVATE)))
    if not locked:
        results.append(check("★ 그 좌표의 클릭이 **아래 창으로** 간다",
                             after in under_chain,
                             f"handle={after} (아래 창은 {sorted(under_chain)})"))
    results.append(check("오버레이는 여전히 보인다 (화면에 떠 있다)",
                         bool(_user32.IsWindowVisible(over_hwnd))))

    # 작업표시줄에 뜨지 않는지도 본다 (TOOLWINDOW). 상태 표시용 창이라 뜨면 거슬린다.
    results.append(check("작업표시줄에 뜨지 않는다 (TOOLWINDOW)",
                         bool(style & WS_EX_TOOLWINDOW)))

    over.destroy()

    results += check_real_overlay(under, locked)

    log.info("결과: 확인 %d / 어긋남 %d", sum(results), len(results) - sum(results))
    if all(results):
        log.info("→ **클릭 통과 오버레이를 쓸 수 있고, [중단] 은 누를 수 있다.**")
    if args.show:
        # 눈으로 보라고 띄워 둔다. 판정은 이미 끝났다.
        log.info("오버레이를 띄운다. [닫기] 를 누르면 끝난다.")
        under.withdraw()
        shown = Overlay(under, on_stop=lambda: None, title="보기 전용")
        shown.show_plan(fake_plan())
        for event in fake_plan():
            shown.update(event)
        shown.set_busy(False)      # 단추가 [닫기] 가 된다
        under.wait_window(shown.status)
    under.destroy()
    return 0 if all(results) else 1


TABLE_COLUMNS = ("사이트", "결과", "시도", "파일", "오류")
TABLE_ROWS = (("사이트C", "성공", "1", "사이트C_1.xlsx", ""),
              ("사이트B", "실패", "2", "사이트B_2.xlsx",
               "파일 선택 창을 찾지 못했다"),
              ("사이트A", "성공", "1", "사이트A_3.xlsx", ""))


def fake_plan() -> list:
    """가짜 단계들. **대상 프로그램을 건드리지 않는다.**

    기능별 단계 표(`orchestrator/steps_erpia.py` 등)를 쓰지 않는 이유: 이
    도구는 어느 기능에도 매이지 않아야 하고, 상태가 섞인 화면을 일부러
    만들어 봐야 하기 때문이다 (완료/진행 중/실패/대기가 한 화면에 있는 그림).
    """
    import time

    from orchestrator.steps import (CANCELLED, DONE, FAILED, PENDING, RUNNING,
                                    SKIPPED, StepEvent)

    now = time.time()
    shape = [("로그인", DONE, "user01", 8.0), ("화면 진입", DONE, "", 7.9),
             ("엑셀수집", DONE, "업로드 2/3건", 48.4),
             ("자동수집", SKIPPED, "고르지 않았다", 0.0),
             ("매출처리", RUNNING, "미매출 4건", 12.0),
             ("물류대기", PENDING, "", 0.0), ("물류관리", PENDING, "", 0.0),
             ("운송장출력", FAILED, "", 3.0), ("정리", CANCELLED, "", 1.0)]
    out = []
    for position, (name, state, detail, elapsed) in enumerate(shape, start=1):
        started = now - 200 + position * 20
        out.append(StepEvent(
            step_id=f"s{position}", name=name, index=position, total=len(shape),
            state=state, detail=detail, elapsed=elapsed,
            started_at=0.0 if state == PENDING else started,
            finished_at=0.0 if state in (RUNNING, PENDING) else started + elapsed,
            # 세 번째 칸에만 상세 표를 준다 (표가 있는 칸/없는 칸 둘 다 본다).
            columns=TABLE_COLUMNS if position == 3 else (),
            rows=TABLE_ROWS if position == 3 else (),
            # 5번 칸에만 건수를 준다. **셀 수 있는 단계와 없는 단계**를
            # 둘 다 확인해야 한다.
            total_items=120 if position == 5 else 0,
            done_items=42 if position == 5 else 0,
            ok_items=41 if position == 5 else 0,
            failed_items=1 if position == 5 else 0,
            activity=("거래처 10321 자료를 조회하고 있습니다"
                      if position == 5 else "")))
    return out


def check_real_overlay(parent, locked: bool = False) -> list:
    """실제 `gui.overlay.Overlay` 로 확인한다.

    중요한 것은 둘이다.

    1. 상태 표시는 **클릭이 통과**한다 (넓어서 통과하지 않으면 ERPia 를 못 누른다)
    2. [중단] 은 **통과하지 않는다** (사람이 눌러야 한다) — 대신 자동화가 그
       자리를 누르려 하면 **스스로 비켜야** 한다
    """
    from gui.overlay import Overlay, _window_chain

    log.info("▶ 실제 오버레이")
    out = []
    from utils.cancel import CancelToken

    token = CancelToken()
    overlay = Overlay(parent, on_stop=lambda: None, token=token,
                      on_report=lambda: None)
    try:
        # ★ 빈 오버레이로 재면 안 된다. 칸이 없으면 창이 거의 0 높이라
        #   "그려지는가" 판정이 아무것도 재지 못한다.
        overlay.show_plan(fake_plan())
        parent.update()
        # 오버레이가 쓰는 판정을 그대로 쓴다. 도구가 다르게 세면 의미가 없다.
        status_chain = set(_window_chain(overlay.status))
        stop_chain = set(_window_chain(overlay.bar))

        # 상태 표시 한가운데를 찍는다.
        overlay.status.update_idletasks()
        sx = overlay.status.winfo_x() + overlay.width() // 2
        sy = overlay.status.winfo_y() + 20
        at_status = window_at(sx, sy)
        if not locked:
            out.append(check("상태 표시는 클릭이 통과한다",
                             at_status not in status_chain,
                             f"그 좌표의 창={at_status} / 상태창={sorted(status_chain)}"))

        # [중단] 한가운데를 찍는다. 여기는 **통과하면 안 된다.**
        left, top, right, bottom = overlay._stop_rect()
        bx, by = (left + right) // 2, (top + bottom) // 2
        at_button = window_at(bx, by)
        if not locked:
            # 그 좌표의 창을 **뿌리까지 올려서** 비교한다. Tk 가 위젯을 그리려고
            # 더 깊은 자식 창을 만들 수 있어서, 사슬에 그대로 들어 있지 않다.
            root_at_button = _user32.GetAncestor(at_button, 2) or at_button
            out.append(check("조작 막대는 눌린다 (통과하지 않는다)",
                             at_button in stop_chain or root_at_button in stop_chain,
                             f"그 좌표의 창={at_button} 뿌리={root_at_button} "
                             f"/ 버튼창={sorted(stop_chain)}"))

        # ★ 자동화가 그 자리를 누르려 한다고 알린다. 비켜야 한다.
        before = overlay._stop_rect()
        overlay.avoid(before)
        after = overlay._stop_rect()
        out.append(check("★ 클릭 지점과 겹치면 조작 막대가 비킨다",
                         after != before, f"{before} → {after}"))

        # 겹치지 않는 자리를 알리면 **가만히 있어야** 한다.
        still = overlay._stop_rect()
        overlay.avoid((0, 0, 10, 10))
        out.append(check("겹치지 않으면 움직이지 않는다",
                         overlay._stop_rect() == still))

        out.append(check("상태 표시에 클릭 통과 스타일이 걸렸다",
                         all(_get_ex_style(h) & WS_EX_TRANSPARENT
                             for h in status_chain),
                         "잠겨 있어도 이것은 확인할 수 있다"))
        out.append(check("조작 막대에는 통과 스타일이 없다",
                         not any(_get_ex_style(h) & WS_EX_TRANSPARENT
                                 for h in stop_chain)))
        out.append(check("두 창 모두 포커스를 뺏지 않는다 (NOACTIVATE)",
                         all(_get_ex_style(h) & WS_EX_NOACTIVATE
                             for h in status_chain | stop_chain)))
        out.append(check("좌표 클릭 직전에 부를 가드가 걸려 있다",
                         ui.CLICK_GUARD is not None))
        out += check_paints(overlay, locked)
        out += check_density(overlay)
        out += check_counts(overlay)
        out += check_result_panel(overlay)
        # ★ 배경 비침은 **펼친 상태**에서 잰다. HUD 는 판이 거의 전부라
        #   투명한 자리가 없다. (`check_density` 가 이미 펼쳐 두었다.)
        out += check_see_through(overlay, locked)
        out += check_bar_visible(overlay, locked)
        out += check_controls(overlay)
    finally:
        overlay.close()
    out.append(check("닫으면 가드도 풀린다", ui.CLICK_GUARD is None))
    return out


def check_bar_visible(overlay, locked: bool = False) -> list:
    """★ 조작 막대가 **눈에 보이는가** — 밀도를 바꿔 가며 화면을 찍어 센다.

    ## 왜 히트 테스트로는 안 되나 (2026-09-16)

    펼치면 상태 띠가 화면 전체를 덮으면서 **막대 위에 그려졌다.** 그래서
    [중단]·[숨기기] 가 어두운 제목 줄에 가려 보이지 않았다.

    그런데 `WindowFromPoint` 는 상태 띠가 `WS_EX_TRANSPARENT` 라 **어차피
    건너뛴다.** 가려져 있어도 "막대가 잡힌다" 고 답한다 — 계측기가 볼 수
    없는 문제였다. 그래서 여기서는 **빨간 [중단] 픽셀을 직접 센다.**

    (원인은 `SetWindowPos` 에 넘긴 `HWND_TOPMOST(-1)` 가 ctypes 에서 32비트로
    잘린 것이었다. 오류 없이 조용히 아무 일도 하지 않았다.)
    """
    if locked:
        log.info("  건너뜀  조작 막대가 보이는지 — 화면이 잠겨 있다")
        return []
    try:
        from PIL import ImageGrab
    except ImportError:
        log.warning("  건너뜀  조작 막대가 보이는지 — Pillow 가 없다")
        return []

    def red_share() -> tuple:
        """(빨간 픽셀, [중단] 단추 면적). **비율로 본다** —
        절대 개수로 재면 단추 크기를 바꿀 때마다 판정이 틀린다.
        """
        overlay.parent.update()
        x1, y1, x2, y2 = overlay._bar_rect()
        image = ImageGrab.grab(bbox=(x1, y1, x2, y2),
                               all_screens=True).convert("RGB")
        data = (image.get_flattened_data() if hasattr(image, "get_flattened_data")
                else image.getdata())
        red = sum(1 for r, g, b in data if r > 150 and g < 80 and b < 80)
        area = max(overlay.stop_btn.winfo_width()
                   * overlay.stop_btn.winfo_height(), 1)
        return red, area

    was = overlay.expanded
    out = []
    for expanded, label in ((False, "HUD 일 때"), (True, "펼쳤을 때")):
        overlay.set_expanded(expanded)
        overlay.parent.update()
        count, area = red_share()
        out.append(check(f"★ {label} [중단] 이 가려지지 않는다",
                         count > area * 0.4,
                         f"빨간 픽셀 {count}개 / 단추 면적 {area} "
                         f"= {count / area:.0%} (가려지면 0에 가깝다)"))
    overlay.set_expanded(was)
    overlay.parent.update()
    return out


def check_density(overlay) -> list:
    """★ 기본은 **작은 코너 HUD**, [펼치기] 면 화면 전체 (사용자 확정 2026-09-16).

    처음에는 화면 전체가 기본이었는데, 9칸이 y=0~564 = **화면의 51.8%** 를
    걸쳐 대상 프로그램의 조회 조건줄과 상단 그리드를 통째로 덮었다.
    실행 중에 정작 필요한 칸은 하나뿐이라 그만큼 덮을 이유가 없다.
    """
    from gui.overlay import _work_area

    left, top, right, bottom = _work_area()
    room = (right - left) * (bottom - top)
    out = []

    # ★ 기본 자리로 되돌린 뒤에 잰다. 앞선 "비키기" 시험이 막대를
    #   다른 모서리로 옮겨 놓았다 — 그대로 재면 기본 배치를 재는 것이
    #   아니다 (2026-09-16에 그렇게 헛짚었다).
    overlay._place_bar(0)
    overlay.parent.update()
    overlay.status.update_idletasks()
    width, height = overlay.status.winfo_width(), overlay.status.winfo_height()
    share = width * height / room
    # ★ 3/4 로 줄였다 (사용자 요청 2026-09-16). 기준도 함께 조인다 —
    #   느슨하게 두면 다시 커져도 통과한다.
    out.append(check("★ 기본은 코너 HUD 다 (화면의 3% 이하)",
                     not overlay.expanded and share <= 0.03,
                     f"{width}x{height} = 화면의 {share:.1%}"))
    out.append(check("HUD 가 오른쪽 위에 붙는다",
                     overlay.status.winfo_x() + width >= right - overlay.INSET - 4,
                     f"({overlay.status.winfo_x()}, {overlay.status.winfo_y()})"))
    # ★ 조작 막대가 **진행 박스보다 위**에 온다 (사용자 요청 2026-09-16).
    out.append(check("★ 조작 막대가 진행 박스보다 위에 있다",
                     overlay._bar_rect()[1] < overlay.status.winfo_y(),
                     f"막대 y={overlay._bar_rect()[1]} / 박스 y="
                     f"{overlay.status.winfo_y()}"))

    # HUD 는 **진행 중인 칸**을 고른다. 마지막으로 통지된 칸이 아니다.
    here = overlay.pipeline.current()
    out.append(check("★ HUD 는 진행 중인 칸을 보여 준다",
                     here.state in ("running", "failed", "cancelled"),
                     f"{here.index}/{here.total} {here.name} ({here.state})"))
    drawn = " | ".join(overlay.pipeline.drawn())
    # ★ 돌고 있을 때의 상태 문구는 `진행 중` 이 아니라 **`자동 조작 중`** 이다
    #   (2026-09-16). 사람이 지금 끼어들면 안 된다는 것을 같은 줄에서 읽게 했다.
    out.append(check("HUD 에 단계 이름과 상태가 있다",
                     here.name in drawn and "자동 조작 중" in drawn, drawn[:70]))
    kinds = [overlay.pipeline.canvas.type(s)
             for s in overlay.pipeline.canvas.find_all()]
    # ★ HUD 는 **진행 중인 칸 하나만** 그린다. 단계 목록은 펼쳐서 본다 —
    #   점열은 11단계에서 소음이라 뺐다 (2026-09-16).
    out.append(check("★ HUD 는 단계 목록을 늘어놓지 않는다",
                     kinds.count("oval") == 0,
                     f"점 {kinds.count('oval')}개"))
    out.append(check("HUD 에 경과 시간이 나온다",
                     any(t.count(":") == 2 for t in overlay.pipeline.drawn()),
                     " | ".join(overlay.pipeline.drawn()[:6])))
    out.append(check("막대 글귀가 HUD 와 같은 칸을 가리킨다",
                     overlay.bar_var.get().startswith(f"{here.index}/{here.total}"),
                     overlay.bar_var.get()))

    # 펼치면 화면 전체 + 카드.
    overlay.set_expanded(True)
    overlay.parent.update()
    overlay.status.update_idletasks()
    width, height = overlay.status.winfo_width(), overlay.status.winfo_height()
    share = width * height / room
    # ★ 펼쳐도 **화면을 다 덮지 않는다** (사용자 요청 2026-09-16).
    #   레퍼런스처럼 가운데 카드로 띄우고 뒤가 보여야 한다.
    out.append(check("★ 펼쳐도 화면을 다 덮지 않는다 (가운데 카드)",
                     0.15 <= share <= 0.55,
                     f"{width}x{height} = 화면의 {share:.1%}"))
    cx = overlay.status.winfo_x() + width // 2
    out.append(check("카드가 가운데에 있다",
                     abs(cx - (left + right) // 2) <= 8,
                     f"카드 가운데 x={cx} / 화면 가운데 {(left + right) // 2}"))
    out.append(check("펼치면 단계마다 표식을 하나씩 그린다",
                     overlay.pipeline.markers() == len(overlay.pipeline.order),
                     f"표식 {overlay.pipeline.markers()}개 / 단계 "
                     f"{len(overlay.pipeline.order)}개"))
    drawn = overlay.pipeline.drawn()
    # ★ Stepper 는 **위치 지도**다. 시각은 빼고 이름과 상태만 남겼다
    #   (2026-09-16, 외부 검토 의견) — 8~11칸에 세 줄이면 촘촘해진다.
    out.append(check("펼치면 단계 이름과 상태가 보인다",
                     "완료" in drawn, " | ".join(drawn[:8])))
    out.append(check("★ Stepper 에 **시각을 적지 않는다** (위치 지도다)",
                     not any(t.count(":") == 2 for t in drawn),
                     next((t for t in drawn if t.count(":") == 2), "없다")))
    # 시각이 사라진 것이 아니라 **자리를 옮겼다.** 머리줄이 들고 있다.
    out.append(check("그 시각은 머리줄이 들고 있다",
                     ":" in overlay.clock_var.get(),
                     overlay.clock_var.get().replace(chr(10), " / ")))
    out.append(check("[접기] 로 돌아온다",
                     overlay.expand_btn.cget("text") == "접기"))

    # ★ 펼치면 **자세한 것**이 나와야 한다 (사용자 요청 2026-09-16).
    out.append(check("★ 펼치면 상태별 개수와 경과를 머리줄에 보여 준다",
                     "완료" in overlay.title_var.get()
                     and "경과" in overlay.title_var.get(),
                     overlay.title_var.get()))
    overlay.pipeline.select("s3")          # 상세 표가 있는 칸
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    table = " | ".join(overlay.table.drawn())
    out.append(check("★ 펼치면 고른 칸의 **상세 표**를 보여 준다",
                     all(name in table for name in TABLE_COLUMNS)
                     and "사이트C_1.xlsx" in table, table[:80]))
    out.append(check("표가 있으면 상세 칸이 자리를 차지한다",
                     bool(overlay.table.canvas.winfo_manager()),
                     f"manager={overlay.table.canvas.winfo_manager()!r}"))

    # ★ **표가 없으면 상세 칸을 접는다** (2026-09-16, 외부 검토 의견).
    #   한 줄짜리 안내를 띄우려고 큰 빈 상자를 유지하면, 화면의 40% 를
    #   덮으면서 그 아래쪽이 통째로 비어 있게 된다.
    overlay.pipeline.select("s6")          # 상세 표가 없는 칸
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    out.append(check("★ 표가 없으면 **상세 칸을 접는다** (빈 상자를 두지 않는다)",
                     not overlay.table.canvas.winfo_manager(),
                     f"manager={overlay.table.canvas.winfo_manager()!r}"))

    # 다시 표가 있는 칸으로 가면 되살아나야 한다.
    overlay.pipeline.select("s3")
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    out.append(check("표가 있는 칸으로 돌아오면 다시 나온다",
                     bool(overlay.table.canvas.winfo_manager()),
                     f"manager={overlay.table.canvas.winfo_manager()!r}"))
    out += _check_no_empty_box(overlay)
    return out


def _check_no_empty_box(overlay) -> list:
    """★ **카드 높이가 내용을 따라가는가.**

    2026-09-16에 화면을 찍어 보고 잡았다 — 펼치면 늘 1080x740 이라, 상세 표가
    없는 단계에서는 **아래 60% 가 통째로 빈 상자**였다. 화면의 40% 를 덮으면서
    그 자리에 아무것도 없는 상태다.

    ★ 확인 도구로는 잡히지 않았다. 글자도 다 있고 대비도 맞았다.
      **찍어서 눈으로 보고서야** 알았다. 그래서 이 판정을 남긴다.
    """
    overlay.set_expanded(True)

    overlay.pipeline.select("s3")           # 상세 표가 있는 칸
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    with_table = overlay.size()[1]

    overlay.pipeline.select("s6")           # 상세 표가 없는 칸
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    without = overlay.size()[1]

    ceiling = int(overlay.CARD_H * 1.0)
    return [
        check("★ 표가 없으면 카드가 **더 짧아진다** (빈 상자를 두지 않는다)",
              without < with_table,
              f"표 있을 때 {with_table}px / 없을 때 {without}px"),
        check("★ 카드 높이가 내용을 따라간다 (고정이 아니다)",
              with_table < ceiling,
              f"{with_table}px < 상한 {ceiling}px"),
        # 표가 있을 때 표를 **다 보여 주는지.** 눌리면 `... N줄 더 있습니다` 가
        # 뜨는데, 세 줄짜리 표에서 그러면 자리 배분이 잘못된 것이다.
        check("★ 세 줄짜리 표는 **줄이지 않고** 다 보여 준다",
              _shows_all_rows(overlay),
              "표를 눌러 `더 있습니다` 가 뜨면 자리 배분이 잘못된 것이다"),
    ]


def _shows_all_rows(overlay) -> bool:
    overlay.pipeline.select("s3")
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    return not any("더 있습니다" in text for text in overlay.table.drawn())


def _table_for(overlay, step_id: str) -> bool:
    overlay.pipeline.select(step_id)
    overlay._on_pick(overlay.pipeline.selected())
    overlay.parent.update()
    return any("없습니다" in text for text in overlay.table.drawn())



def check_counts(overlay) -> list:
    """★ 건수·진행률이 **실제 값에서** 오는지 (사용자 요청 2026-09-16).

    가짜 계획의 5번 칸에만 건수를 줬다. 그 칸에서는 숫자가 보이고, 안 준
    칸에서는 **아무 숫자도 지어내지 않아야** 한다.
    """
    overlay.set_expanded(True)
    overlay.parent.update()
    out = []

    panel = " | ".join(overlay.current_view.drawn())
    out.append(check("★ 현재 단계 판에 처리 건수가 실제 값으로 나온다",
                     "42 / 120건 (35%)" in panel, panel[:80]))
    out.append(check("성공 / 실패 / 대기 건수를 나눠 보여 준다",
                     "41건" in panel and "1건" in panel and "78건" in panel))
    out.append(check("현재 작업 문장이 나온다",
                     "거래처 10321" in panel))
    out.append(check("HUD 에도 건수와 단계 진행률이 나온다",
                     "42 / 120건" in _hud_text(overlay) and "35%" in _hud_text(overlay),
                     _hud_text(overlay)[:70]))

    # ★ 건수를 모르는 칸에서는 **숫자를 지어내지 않는다.**
    plan = [e for e in fake_plan() if e.total_items == 0]
    overlay.show_plan(plan)
    overlay.parent.update()
    panel = " | ".join(overlay.current_view.drawn())
    out.append(check("★ 셀 수 없는 단계에서는 건수를 만들어내지 않는다",
                     "셀 수 없는" in panel and "0 / 0건" not in panel, panel[:70]))
    overlay.show_plan(fake_plan())
    for event in fake_plan():
        overlay.update(event)
    overlay.parent.update()
    return out


def _hud_text(overlay) -> str:
    was = overlay.expanded
    overlay.set_expanded(False)
    overlay.parent.update()
    text = " | ".join(overlay.pipeline.drawn())
    overlay.set_expanded(was)
    overlay.parent.update()
    return text


def check_result_panel(overlay) -> list:
    """★ 실패·완료를 **패널로** 알리는지. traceback 을 그대로 쏟지 않는지."""
    out = []
    traceback_like = ("ScreenError: 조회 버튼을 찾지 못했다" + chr(10)
                      + '  File "automation/logistics.py", line 42, in run'
                      + chr(10) + "    raise ScreenError(...)")
    overlay.finish("failed", "", traceback_like)
    overlay.parent.update()
    banner = overlay.result_var.get()
    out.append(check("★ 실패하면 실패 패널을 보여 준다",
                     "RPA 실행 실패" in banner and "실패 단계" in banner
                     and "완료 단계" in banner and "실행 시간" in banner,
                     banner.replace(chr(10), " / ")[:90]))
    out.append(check("★ traceback 을 그대로 쏟지 않는다 (첫 줄만)",
                     "File " not in banner and "조회 버튼을 찾지 못했다" in banner))
    out.append(check("[상세 보기] 로 넘길 곳이 있다",
                     overlay.report_btn is not None or overlay.on_report is None))
    # ★ **실패는 펼친다.** 사람이 봐야 하는 일이다.
    out.append(check("★ 실패하면 **펼쳐서** 보여 준다", overlay.expanded,
                     f"expanded={overlay.expanded}"))

    # ★ **실패 건수가 있으면 완료여도 펼친다.** 지금 가짜 계획에는 실패
    #   1건이 들어 있으므로 여기서는 펼쳐지는 것이 맞다.
    overlay.set_expanded(False)
    overlay.finish("done", "실패가 섞인 완료")
    overlay.parent.update()
    out.append(check("★ 실패 건수가 있으면 완료여도 펼친다", overlay.expanded,
                     f"expanded={overlay.expanded}"))

    # ★ 정상 완료는 **펼치지 않는다** (2026-09-16, 외부 검토 의견).
    #   예약 실행은 아무도 안 보고 있는데 화면의 40% 를 덮고 그대로 남는다.
    #   하루 세 번 도는 예약이면 그 창이 세 번 뜬다.
    #   실패가 하나도 없는 계획으로 바꿔서 조용한 쪽을 확인한다.
    import dataclasses

    clean = [dataclasses.replace(e, state="done", failed_items=0,
                                 ok_items=e.total_items)
             for e in fake_plan()]
    overlay.show_plan(clean)
    for event in clean:
        overlay.update(event)
    overlay.set_expanded(False)
    overlay.finish("done", "조용히 끝난 경우")
    overlay.parent.update()
    out.append(check("★ **정상 완료는 펼치지 않는다** (무인 실행에서 화면을 덮는다)",
                     not overlay.expanded, f"expanded={overlay.expanded}"))
    out.append(check("그래도 결과는 만들어 둔다 (펼치면 보인다)",
                     "RPA 실행 완료" in overlay.result_var.get(),
                     overlay.result_var.get().replace(chr(10), " / ")[:60]))
    overlay.set_expanded(True)
    overlay.parent.update()
    out.append(check("★ 나중에 펼치면 **결과 띠가 붙어 있다**",
                     bool(overlay.result_label.winfo_manager()),
                     f"manager={overlay.result_label.winfo_manager()!r}"))
    overlay.set_expanded(False)

    overlay.finish("done", "업로드 2/3건 / 매출처리 완료")
    overlay.parent.update()
    banner = overlay.result_var.get()
    out.append(check("★ 끝나면 완료 패널을 보여 준다",
                     "RPA 실행 완료" in banner and "총 실행 시간" in banner,
                     banner.replace(chr(10), " / ")[:90]))
    # 09-29 실기: 단위가 다른 건수(메일 파일·사이트·주문)를 더해 "26건 중 21건 성공" 이 실패 5건처럼 읽혔다
    out.append(check("★ 단계별 건수를 더한 '처리 결과' 줄을 쓰지 않는다 (단위가 다르다)",
                     "처리 결과" not in banner, banner.replace(chr(10), " / ")))
    # 09-29 실기: 긴 요약이 줄바꿈 없이 잘렸고, 접은 작은 판에도 결과 띠가 남아 300px 에서 잘렸다
    long_summary = " · ".join(["메일 엑셀 3개 받음", "엑셀 3개 중 3개 올림", "새 주문 7건"] * 8)
    overlay.finish("done", long_summary)
    overlay.set_expanded(True)
    overlay.parent.update()
    wrap = int(str(overlay.result_label.cget("wraplength")) or 0)
    out.append(check("★ 펼치면 긴 결과를 카드 폭 안에서 줄바꿈한다",
                     0 < wrap <= overlay.size()[0]
                     and overlay.result_label.winfo_reqwidth() <= overlay.size()[0],
                     f"wraplength={wrap} / 띠 {overlay.result_label.winfo_reqwidth()} / 카드 {overlay.size()[0]}"))
    overlay.set_expanded(False)
    overlay.parent.update()
    out.append(check("★ 접으면(작은 판) 결과 띠를 떼어 둔다 (잘려서 읽히지 않는다)",
                     not overlay.result_label.winfo_manager(),
                     f"manager={overlay.result_label.winfo_manager()!r}"))

    overlay.finish("cancelled", "사용자가 중단했습니다.")
    overlay.parent.update()
    out.append(check("중단도 따로 말한다",
                     "RPA 실행 중단" in overlay.result_var.get()))
    return out


def check_see_through(overlay, locked: bool = False) -> list:
    """★ **카드 밖은 대상 프로그램이 그대로 보이는가.**

    ## 왜 판정이 바뀌었나 (2026-09-16 저녁)

    처음에는 오버레이가 화면 전체를 덮고 배경색만 투명하게 비웠다. 그래서
    "배경 자리에 뒤가 비치는가" 를 쟀다. 그런데 그 구조에서는 **Stepper 글자가
    대상 프로그램 위에 그대로 얹혀 읽히지 않았다.**

    지금은 창을 **카드 크기로만** 잡고 그 안을 칠한다. 카드 밖은 아예 창이
    없으므로 뒤가 100% 보인다 — 이것이 실제로 확인할 값이다.
    """
    import tkinter as tk

    if locked:
        log.info("  건너뜀  카드 밖이 보이는지 — 화면이 잠겨 있다")
        return []
    try:
        from PIL import ImageGrab
    except ImportError:
        log.warning("  건너뜀  카드 밖이 보이는지 — Pillow 가 없다")
        return []

    from gui.overlay import _work_area
    from utils import wait

    overlay.set_expanded(True)
    overlay._place_bar(0)
    overlay.parent.update()

    left, top, right, bottom = _work_area()
    card = (overlay.status.winfo_x(), overlay.status.winfo_y(),
            overlay.status.winfo_x() + overlay.status.winfo_width(),
            overlay.status.winfo_y() + overlay.status.winfo_height())
    # 카드 **아래쪽** 바깥. 여기에 눈에 띄는 색의 창을 깔고 그대로 보이는지
    # 본다. 왼쪽 바깥을 쓰지 않는 이유: 이 도구가 앞 절에서 만든 기준 창이
    # 거기 떠 있어 그것을 찍는다 (2026-09-16에 그렇게 헛짚었다).
    spot = ((card[0] + card[2]) // 2, (card[3] + bottom) // 2)
    if spot[1] <= card[3] + 20:
        return [check("카드 밖이 보이는지 확인", False, "카드가 너무 높다")]

    mark = tk.Toplevel(overlay.parent)
    mark.overrideredirect(True)
    mark.attributes("-topmost", True)
    mark.configure(bg="#ff00ff")
    try:
        mark.geometry(f"120x80+{spot[0] - 40}+{spot[1] - 40}")
        overlay.parent.update()
        mark.lower(overlay.status)
        overlay.parent.update()
        seen = []

        def shows() -> bool:
            overlay.parent.update()
            pixel = ImageGrab.grab(bbox=(spot[0], spot[1], spot[0] + 2, spot[1] + 2),
                                   all_screens=True).convert("RGB").getpixel((0, 0))
            seen.append(pixel)
            red, green, blue = pixel
            return red > 200 and blue > 200 and green < 90

        try:
            ok = wait.wait_for(shows, "카드 밖의 색", timeout=3.0, interval=0.2)
        except Exception as exc:
            log.debug("카드 밖 색이 안 나타났다: %s", type(exc).__name__)
            ok = False
        return [check("★ 카드 밖은 대상 프로그램이 그대로 보인다", bool(ok),
                      f"카드 밖 픽셀 {seen[-1] if seen else '?'} / 아래 창은 "
                      "(255, 0, 255)")]
    finally:
        mark.destroy()



def _check_bar_fit(overlay) -> list:
    """★ 막대의 **글귀가 잘리지 않는가** / 단추가 **같은 크기인가**.

    2026-09-16에 사용자가 화면을 찍어 보냈다 — `5/9 매출처...` 로 잘려 있었고
    [숨기기] 가 자리를 너무 먹고 있었다. 원인은 `tk.Button(width=N)` 의 N 이
    **글자 수**라 단추마다 실제 폭이 달랐던 것이다.

    여기서는 눈이 아니라 **픽셀**로 본다.
    """
    from gui import overlay as module

    buttons = [overlay.expand_btn, overlay.hide_btn, overlay.pause_btn,
               overlay.stop_btn]
    if overlay.report_btn is not None:
        buttons.append(overlay.report_btn)

    overlay.bar.update_idletasks()
    widths = {btn.cget("text"): btn.master.winfo_width() for btn in buttons}
    same = len(set(widths.values())) == 1

    # 글귀에 남는 자리 vs 가장 긴 문장이 필요한 자리
    label_room = overlay.bar_label.winfo_width()
    need = overlay._bar_font().measure(module.BAR_SAMPLE)

    return [
        check("★ 막대 단추가 **모두 같은 너비**다 (사용자 요청 2026-09-16)",
              same, " / ".join(f"{k} {v}px" for k, v in widths.items())),
        check("★ 막대 글귀가 잘리지 않는다 (현재 단계가 다 보인다)",
              label_room >= need,
              f"글귀 자리 {label_room}px / 가장 긴 문장 "
              f"{module.BAR_SAMPLE!r} 은 {need}px 필요"),
        # 글자가 바뀌어도 자리가 흔들리면 안 된다 — 이미 한 번 지적받았다.
        check("★ 단추 너비가 **나올 수 있는 모든 글자**를 담는다",
              overlay._button_unit()
              >= max(overlay._bar_font().measure(t)
                     for t in module.BTN_LABELS) + 1,
              f"단추 {overlay._button_unit()}px / 가장 긴 글자 "
              f"{max(module.BTN_LABELS, key=lambda t: overlay._bar_font().measure(t))!r}"),
        *_check_sample_is_real(overlay),
        *_check_no_fake_percent(overlay),
    ]


def _check_no_fake_percent(overlay) -> list:
    """★ **단계 개수로 만든 %를 보여 주지 않는가.**

    전에는 `끝난 단계 수 / 전체 단계 수` 로 전체 진행률 %를 그렸다. 이 흐름의
    단계는 8초짜리도 있고 4시간짜리도 있어서 **`50%` 가 절반을 뜻하지 않았다.**
    `없는 숫자를 만들지 않는다` 규칙을 스스로 어긴 자리였다(외부 검토가 잡았다).

    %는 **실제로 셀 수 있는 단계**에서만 쓴다. 여기서는 작업 흐름 줄에 %가
    다시 나타나지 않는지 본다.
    """
    head = overlay.title_var.get()
    return [
        check("★ 머리줄에 **%가 없다** (단계 개수로 만든 가짜 숫자였다)",
              "%" not in head, head.replace(chr(10), " / ")),
        check("머리줄이 **위치**를 적는다 (n / N 단계)",
              "단계" in head and "/" in head, head.replace(chr(10), " / ")),
        # ★ 같은 말을 두 번 하지 않는다 (2026-09-16 화면에서 잡았다).
        check("★ 머리줄이 단계 이름을 **되풀이하지 않는다** "
              "(Stepper 와 현재 단계 판이 이미 말한다)",
              (overlay.pipeline.current() is None
               or overlay.pipeline.current().name not in head),
              head.replace(chr(10), " / ")),
    ]


def _check_sample_is_real(overlay) -> list:
    """★ 견본 문장이 **실제 단계 표보다 짧지 않은가.**

    처음에 `"11/11 운송장출력 — 진행 중"` 으로 쟀는데 코드가 만들지 않는
    문장이었다. 실제 형식(`"{번호}/{전체} {이름}"`, 멈추면 `"⏸ 멈춤 — "` 접두)
    으로 다시 재니 166px 가 아니라 227px 였다 — **자리를 과소평가**하고 있었다.

    단계 이름이 길어지면 견본도 같이 늘려야 하는데, 사람이 잊는다. 그래서
    여기서 **진짜 단계 표와 대조**한다.
    """
    from gui import overlay as module
    from orchestrator import steps_collect, steps_erpia

    font = overlay._bar_font()
    worst, worst_px = "", 0
    for plan in (steps_erpia.ALL, steps_collect.ALL):
        total = len(plan)
        for index, step in enumerate(plan, 1):
            for text in (f"{index}/{total} {step.name}",
                         f"⏸ {index}/{total} {step.name}"):
                width = font.measure(text)
                if width > worst_px:
                    worst, worst_px = text, width
    sample_px = font.measure(module.BAR_SAMPLE)
    return [
        check("★ 견본 문장이 실제 단계 표를 담는다 "
              "(단계 이름이 길어지면 BAR_SAMPLE 도 늘린다)",
              sample_px >= worst_px,
              f"견본 {sample_px}px / 실제 최장 {worst!r} {worst_px}px"),
    ]


def _bar_stays(overlay) -> bool:
    """접기/펼치기를 오가도 막대가 같은 자리에 있는가."""
    was = overlay.expanded
    overlay.set_expanded(False)
    overlay.parent.update()
    first = overlay._bar_rect()
    overlay.set_expanded(True)
    overlay.parent.update()
    second = overlay._bar_rect()
    overlay.set_expanded(False)
    overlay.parent.update()
    third = overlay._bar_rect()
    overlay.set_expanded(was)
    overlay.parent.update()
    log.info("      (막대 자리 %s -> %s -> %s)", first[:2], second[:2],
             third[:2])
    return first == second == third


def check_controls(overlay) -> list:
    """★ [중단] 과 [숨기기] 가 **실제로 있는지** 본다.

    2026-09-16에 사용자가 "중단 단추가 없다" 고 했다. 실제로는 86x30 짜리가
    화면 오른쪽 끝에 있었는데 너무 작아 눈에 띄지 않았다. 그래서 크기도 함께 잰다.
    """
    out = [
        check("[중단] 이 있다", "중단" in overlay.stop_btn.cget("text"),
              overlay.stop_btn.cget("text")),
        check("[숨기기] 가 있다", overlay.hide_btn.cget("text") == "숨기기"),
        check("[펼치기]/[접기] 가 있다",
              overlay.expand_btn.cget("text") in ("펼치기", "접기"),
              overlay.expand_btn.cget("text")),
        check("★ 숨겨도 어디까지 왔는지 글귀가 남는다",
              "/" in overlay.bar_var.get(), overlay.bar_var.get()),
        # ★ 단추 자리가 **밀도와 무관하게 같아야** 한다. 옮겨 다니면
        #   같은 단추를 두 번 누를 수가 없다 (사용자 지적 2026-09-16).
        check("★ 접었다 펼쳐도 조작 막대 자리가 그대로다",
              _bar_stays(overlay)),
        check("★ 일시정지 단추가 있다 (토큰이 있을 때만)",
              overlay.pause_btn.cget("state") == ("normal" if overlay.token
                                                  else "disabled"),
              f"토큰={overlay.token is not None} / "
              f"{overlay.pause_btn.cget('state')}"),
        # ★ 전역 단축키는 뺐다 (사용자 확정 2026-09-16) — RPA 가 실수로
        #   누르면 실행이 중단될 수 있다. 확인 도구도 **없다는 것**을 본다.
        check("★ 전역 단축키를 걸지 않는다 (RPA 오작동 위험)",
              not hasattr(overlay, "hotkeys")),
        check("조작 막대가 눈에 띌 만큼 크다",
              overlay.BAR_WIDTH >= 180 and overlay.BAR_HEIGHT >= 36,
              f"{overlay.BAR_WIDTH}x{overlay.BAR_HEIGHT}"),
        *_check_bar_fit(overlay),
    ]

    # 숨겼다 켰다 해 본다. **막대는 남아야** 다시 켤 수 있다.
    overlay.toggle()
    overlay.parent.update()
    out.append(check("★ 숨기면 상태 띠만 사라지고 조작 막대는 남는다",
                     not overlay.status.winfo_ismapped()
                     and overlay.bar.winfo_ismapped()
                     and overlay.hide_btn.cget("text") == "보이기"))
    overlay.toggle()
    overlay.parent.update()
    out.append(check("다시 누르면 돌아온다",
                     overlay.status.winfo_ismapped()
                     and overlay.hide_btn.cget("text") == "숨기기"))

    # ★ 끝나면 자동으로 펼친다 (사용자 확정 2026-09-16).
    overlay.set_expanded(False)
    overlay.parent.update()
    overlay.set_busy(False)
    overlay.parent.update()
    # 칸을 골라 상세를 바꿔 볼 수 있어야 한다 (막대가 아니라 칸을 눌러서).
    overlay.set_expanded(True)
    overlay.pipeline.select("s1")
    overlay._on_pick(overlay.pipeline.selected())
    overlay.step(1)
    overlay.parent.update()
    out.append(check("보는 칸을 앞뒤로 옮길 수 있다",
                     overlay._shown_event().step_id == "s2",
                     f"옮긴 칸={overlay._shown_event().step_id}"))

    # ★ 일시정지가 **토큰에 실제로** 걸리는지. 오버레이가 들고 있는
    #   토큰을 그대로 본다 — 도구가 따로 만든 것을 보면 의미가 없다.
    overlay.toggle_pause()
    out.append(check("★ [일시정지] 가 중단 토큰을 멈춘다",
                     overlay.token is not None and overlay.token.paused,
                     overlay.pause_btn.cget("text")))
    overlay.toggle_pause()
    out.append(check("[재개] 가 다시 풀어 준다",
                     overlay.token is not None and not overlay.token.paused))

    out.append(check("★ 실행이 끝나면 저절로 펼쳐진다",
                     overlay.expanded and overlay.stop_btn.cget("text") == "닫기",
                     f"펼침={overlay.expanded} / 단추={overlay.stop_btn.cget('text')}"))
    return out


def check_paints(overlay, locked: bool = False) -> list:
    """★ 오버레이가 **실제로 그려지는가.**

    스타일이 다 맞아도 화면에는 **검은 상자만** 보일 수 있다
    (2026-09-16에 사용자가 그 화면을 보고 알려 줬다).

    원인은 `WS_EX_LAYERED` 를 Tk 의 **내용 창에까지** 건 것이다. Tk 는 창
    하나를 HWND 두 개로 만드는데, `-alpha` 가 이미 뿌리 창을 레이어드로
    만들어 `SetLayeredWindowAttributes` 로 갱신한다. 그 아래 내용 창까지
    레이어드로 만들면 **그 창의 표면은 아무도 갱신하지 않아** 글자가 사라지고
    배경만 남는다. 스타일 검사로는 잡히지 않는다 — 스타일은 멀쩡하다.

    그래서 화면을 찍어 **색이 몇 가지인지** 센다. 글자가 그려졌으면 배경색
    말고도 여러 색이 나온다. 단색이면 빈 상자다.
    """
    if locked:
        log.info("  건너뜀  그려지는지 확인 — 화면이 잠겨 있다")
        return []
    try:
        from PIL import ImageGrab
    except ImportError:
        log.warning("  건너뜀  그려지는지 확인 — Pillow 가 없다")
        return []

    overlay.status.update_idletasks()
    overlay.parent.update()
    x, y = overlay.status.winfo_x(), overlay.status.winfo_y()
    width = overlay.status.winfo_width()
    height = overlay.status.winfo_height()
    if width < 10 or height < 10:
        return [check("오버레이가 그려진다", False,
                      f"창이 너무 작다 {width}x{height}")]

    image = ImageGrab.grab(bbox=(x, y, x + width, y + height),
                           all_screens=True).convert("RGB")
    colors = image.getcolors(maxcolors=1 << 20) or []
    colors.sort(reverse=True)
    total = width * height
    background = colors[0][0] if colors else total
    # 글자·테두리는 전체의 일부다. 1%만 넘어도 무언가 그려진 것이다.
    painted = 1.0 - background / total
    top = ", ".join(f"{rgb}x{count}" for count, rgb in colors[:3])
    return [check("★ 오버레이가 **실제로 그려진다** (단색 상자가 아니다)",
                  len(colors) > 4 and painted > 0.01,
                  f"색 {len(colors)}가지 / 배경 아닌 픽셀 {painted:.1%} / 많은 색: {top}")]


if __name__ == "__main__":
    raise SystemExit(main())
