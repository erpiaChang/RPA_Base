r"""공용 UI 조작 래퍼.

여기서 프로젝트 제약을 강제한다.

- 식별 우선순위: `auto_id` > `control_type` + `title` > `class_name`
- **절대좌표를 저장하지 않는다.** 클릭 좌표는 `rectangle()`에서 그 자리에서 얻고 버린다.
- 인덱스 접근(`found_index`)은 최후수단 — 쓰면 사유를 주석으로 남긴다.
- 못 찾으면 조용히 넘어가지 않고 예외를 던진다.
"""
from __future__ import annotations

import re
import sys
import time

from pywinauto import backend as _backend
from pywinauto.findwindows import find_elements

from config.settings import SETTINGS
from utils.logger import get_logger
from utils.wait import WaitTimeout, pause, wait_for

log = get_logger(__name__)


class ControlNotFound(RuntimeError):
    """컨트롤을 찾지 못했다. 추측으로 진행하지 않고 중단한다."""


class ControlDisabled(RuntimeError):
    """컨트롤이 비활성이다. 눌러도 아무 일이 일어나지 않으므로 누르지 않는다."""


# ---------------------------------------------------------------- 탐색 계측
# 이 프로그램은 UIA 트리가 커서 **탐색이 전체 시간의 대부분**이다.
# 어떤 조건을 몇 번, 몇 초 동안 찾는지 모르면 캐시를 넣어도 효과를 알 수 없다.
#
# 기본값은 None 이라 운영 실행에는 속성 검사 한 번 외에 부담이 없다.
# 켜는 것은 `tools/probe_search_trace.py` 뿐이다. **제품 코드는 스스로 켜지 않는다.**
trace_hook = None


def _caller() -> str:
    """이 모듈 바깥의 첫 호출 지점. '어느 코드가 같은 것을 반복해서 찾는지' 용."""
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if name != __name__:
            return f"{name}:{frame.f_lineno}"
        frame = frame.f_back
    return "?"


def _trace(kind: str, criteria: dict, started: float, matches: int) -> None:
    if trace_hook is None:
        return
    try:
        trace_hook(kind, dict(criteria), time.perf_counter() - started, matches, _caller())
    except Exception as exc:
        # 계측이 업무를 멈추게 하면 안 된다.
        log.debug("탐색 계측 실패: %s: %s", type(exc).__name__, exc)


def _as_wrapper(parent):
    """Wrapper든 WindowSpecification이든 Wrapper로 맞춘다."""
    resolve = getattr(parent, "wrapper_object", None)
    return resolve() if callable(resolve) else parent


def search(parent, strict: bool = False, **criteria) -> list:
    """`parent` 하위에서 조건에 맞는 컨트롤을 전부 찾는다.

    실패하면 `[]` 다 (대기 루프가 다시 보게). `strict=True` 면 예외를 올린다 —
    "없다" 와 "못 읽었다" 를 가려야 하는 판정용 (`has_hidden_rows`).

    **`wrapper.descendants()`를 쓰면 안 된다.** 그쪽은 ElementInfo 인자
    (process / class_name / control_type / title)만 받고 `auto_id`, `title_re`는
    **조용히 무시한다.** 2026-09-03에 이것 때문에 사이트 그리드를 못 찾았다.

    `findwindows.find_elements`는 pywinauto 조건명을 그대로 받는다.
    """
    started = time.perf_counter()
    wrapper = _as_wrapper(parent)
    try:
        elements = find_elements(
            parent=wrapper.element_info,
            backend=SETTINGS.backend,
            top_level_only=False,
            **criteria,
        )
    except Exception as exc:
        log.debug("find_elements 실패 (%s): %s", criteria, exc)
        _trace("search", criteria, started, -1)
        if strict:
            raise
        return []
    wrap = _backend.registry.backends[SETTINGS.backend].generic_wrapper_class
    _trace("search", criteria, started, len(elements))
    return [wrap(element) for element in elements]


# 층별 탐색의 안전장치. 이 화면의 가장 깊은 조작 대상이 5~6층이다(2026-09-07 실측).
MAX_SEARCH_DEPTH = 12

# 화면 고유 Pane 은 **5층**에 있는 것이 확정값이다(2026-09-08 계측).
# 화면이 아직 안 떴을 때 12층까지 훑으면 "없음" 판정 한 번에 1.5초가 들고,
# 대기하며 네 번 반복해 화면 하나당 14초를 버린다(docs/RESOURCES.md 4번).
# 6층까지만 보면 같은 판정을 훨씬 싸게 끝낸다. 있을 리 없는 깊이를 뒤지지 않는다.
SCREEN_PANE_DEPTH = 6


def _matches(info, criteria: dict) -> bool:
    """`children()` 으로 얻은 ElementInfo 가 조건에 맞는지."""
    try:
        if not info.visible:
            return False
        auto_id = criteria.get("auto_id")
        if auto_id is not None and (info.automation_id or "") != auto_id:
            return False
        control_type = criteria.get("control_type")
        if control_type is not None and info.control_type != control_type:
            return False
        title = criteria.get("title")
        if title is not None and (info.name or "") != title:
            return False
    except Exception as exc:
        log.debug("조건 확인 실패: %s", type(exc).__name__)
        return False
    return True


def search_shallow(parent, max_depth: int | None = None, **criteria) -> list:
    """`search` 와 같지만 **가장 얕은 층에서 찾으면 멈춘다.**

    `find_elements` 는 조건과 무관하게 하위 트리를 **끝까지** 훑는다.
    이 프로그램의 메인 창은 컨트롤이 2,971개, 물류관리 화면 하나만도 1,133개라
    버튼 하나 찾는 데 2~4초가 걸린다. 화면 진입부터 전체선택까지 같은 탐색을
    예닐곱 번 반복하므로 그대로 20초가 된다 (2026-09-07 계측).

    조작 대상은 3~5층에 있다. 층별로 내려가다 **맞는 것이 나온 층에서 멈추면**
    같은 결과를 6~7배 빨리 얻는다 (화면 Pane 4.28초 → 0.68초, 그리드 2.36초 → 0.33초).

    `title_re` 같은 정규식 조건은 지원하지 않는다. 그 경우 `search` 로 넘긴다.

    `max_depth` 를 주면 그 층까지만 본다. **찾는 것의 깊이가 확정된 경우에만** 쓴다.
    없을 때의 판정 비용이 크게 줄어든다.
    """
    if not criteria or set(criteria) - {"auto_id", "control_type", "title"}:
        return search(parent, **criteria)   # 계측은 search 쪽에서 한다

    started = time.perf_counter()
    wrapper = _as_wrapper(parent)
    wrap = _backend.registry.backends[SETTINGS.backend].generic_wrapper_class
    level = [wrapper.element_info]

    for _ in range(max_depth or MAX_SEARCH_DEPTH):
        found, deeper = [], []
        for info in level:
            try:
                children = info.children()
            except Exception as exc:
                log.debug("children() 실패: %s", type(exc).__name__)
                continue
            for child in children:
                deeper.append(child)
                if _matches(child, criteria):
                    found.append(child)
        if found:
            _trace("search_shallow", criteria, started, len(found))
            return [wrap(info) for info in found]
        if not deeper:
            break
        level = deeper
    _trace("search_shallow", criteria, started, 0)
    return []


def describe(ctrl) -> str:
    """로그용 한 줄 설명. dry_run에서 '무엇을 클릭할지' 보여줄 때 쓴다."""
    try:
        info = ctrl.element_info
        return (
            f"{info.control_type}(title={info.name!r}, auto_id={info.automation_id!r}) "
            f"rect={ctrl.rectangle()}"
        )
    except Exception as exc:
        return f"(설명 실패: {type(exc).__name__}: {exc})"


def find(
    parent,
    *,
    auto_id: str | None = None,
    control_type: str | None = None,
    title: str | None = None,
    title_re: str | None = None,
    timeout: float | None = None,
    what: str | None = None,
    max_depth: int | None = None,
):
    """컨트롤 하나를 찾는다. 없으면 `ControlNotFound`.

    auto_id가 있으면 그것만으로 찾는다(가장 안정적).
    """
    criteria: dict = {}
    if auto_id:
        criteria["auto_id"] = auto_id
    if control_type:
        criteria["control_type"] = control_type
    if title is not None:
        criteria["title"] = title
    if title_re is not None:
        criteria["title_re"] = title_re
    if not criteria:
        raise ValueError("식별 조건이 하나도 없다.")

    label = what or ", ".join(f"{k}={v!r}" for k, v in criteria.items())
    timeout = SETTINGS.timeouts.control if timeout is None else timeout

    def _first(matches):
        """여러 개가 맞으면 조용히 넘어가지 않고 경고로 남기고 첫 번째를 쓴다."""
        if not matches:
            return None
        if len(matches) > 1:
            log.warning("%s: %d개가 일치한다. 첫 번째를 쓴다.", label, len(matches))
            for item in matches[:5]:
                log.warning("    %s", describe(item))
        return matches[0]

    started = time.perf_counter()
    try:
        found = wait_for(
            lambda: _first(search_shallow(parent, max_depth=max_depth, **criteria)),
            f"컨트롤 {label}", timeout=timeout,
        )
    except WaitTimeout as exc:
        _trace("find", criteria, started, 0)
        raise ControlNotFound(
            f"{label} 를 {timeout:.0f}초 안에 찾지 못했다.\n"
            "화면이 조사 시점과 달라졌을 수 있다. docs/CONTROLS.md 를 다시 확인할 것."
        ) from exc
    _trace("find", criteria, started, 1)
    return found


def find_all(parent, *, control_type: str | None = None, title_re: str | None = None) -> list:
    """조건에 맞는 컨트롤 전부. 없으면 빈 리스트(예외 아님)."""
    criteria: dict = {}
    if control_type is not None:
        criteria["control_type"] = control_type
    if title_re is not None:
        criteria["title_re"] = title_re
    return search(parent, **criteria)


def exists(parent, *, auto_id: str | None = None, control_type: str | None = None,
           title: str | None = None, max_depth: int | None = None) -> bool:
    """존재 여부만 확인한다. 대기하지 않는다."""
    criteria: dict = {}
    if auto_id:
        criteria["auto_id"] = auto_id
    if control_type:
        criteria["control_type"] = control_type
    if title is not None:
        criteria["title"] = title
    return bool(search_shallow(parent, max_depth=max_depth, **criteria))


def is_enabled(ctrl) -> bool:
    """활성 상태. 읽지 못하면 True로 본다(판정 불가로 막지 않는다)."""
    try:
        return bool(ctrl.is_enabled())
    except Exception as exc:
        log.debug("활성 상태를 읽지 못했다(활성으로 간주): %s", exc)
        return True


def wait_enabled(ctrl, what: str, timeout: float | None = None, on_wait=None):
    """비활성 버튼이 활성될 때까지 기다린다.

    `on_wait()` 를 주면 대기 중 주기적으로 불러 진행 상황을 남길 수 있다.
    """
    if is_enabled(ctrl):
        return ctrl

    log.info("%s 이(가) 비활성이다. 활성될 때까지 기다린다.", what)

    def ready():
        if on_wait is not None:
            on_wait()
        return is_enabled(ctrl)

    from utils.wait import wait_for  # 순환 import 방지

    wait_for(ready, f"{what} 활성화", timeout=timeout)
    log.info("%s 이(가) 활성됐다.", what)
    return ctrl


CLICK_METHODS = ("Invoke", "Select", "클릭")
# 그리드 셀 안의 아이콘용. Select 는 셀을 '선택'만 하고 아이콘을 누르지 않는다.
#
# ★ **클릭이 먼저다.** 아이콘은 셀에 그려진 그림이라 Invoke 대상이 아니고,
#   실측에서 Invoke 는 **한 번도 성공한 적이 없다** (2026-09-08 통합 실행 3/3 실패).
#   Invoke 를 먼저 두면 매번 실패를 기다렸다가 클릭하느라 파일당 7초를 버린다.
#   Invoke 는 다른 환경 대비 폴백으로만 남긴다.
CELL_ICON_METHODS = ("클릭", "Invoke")
# 좌표 클릭 전에 창이 맨 앞으로 올라오기를 기다리는 상한(초)과 확인 간격.
FOREGROUND_TIMEOUT = 3.0
FOREGROUND_POLL = 0.05


def virtual_screen() -> tuple[int, int, int, int]:
    """모든 모니터를 합친 화면 영역 `(left, top, right, bottom)`.

    ★ **모니터가 여러 대면 좌표가 음수일 수 있다.** 주 모니터 왼쪽(또는 위)에
      보조 모니터가 있으면 거기 있는 창의 좌표는 음수다. 정상이다.
    """
    import ctypes

    metrics = ctypes.windll.user32.GetSystemMetrics
    left, top = metrics(76), metrics(77)          # SM_X/YVIRTUALSCREEN
    width, height = metrics(78), metrics(79)      # SM_CX/CYVIRTUALSCREEN
    if width <= 0 or height <= 0:                 # 못 읽으면 주 모니터만
        return 0, 0, metrics(0), metrics(1)
    return left, top, left + width, top + height


def _on_screen(rect) -> bool:
    """클릭할 지점(컨트롤 중앙)이 실제 화면 영역 안에 있는지."""
    try:
        vl, vt, vr, vb = virtual_screen()
    except Exception as exc:
        log.debug("화면 영역을 읽지 못했다: %s", type(exc).__name__)
        return True          # 판단할 수 없으면 막지 않는다
    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    return vl <= x < vr and vt <= y < vb


# ★ **좌표를 누르기 직전에 부르는 창구.** 화면에 떠 있는 우리 창이 그 좌표를
#   덮고 있으면 비켜 달라고 알린다 (2026-09-15).
#
#   진행 상황 오버레이를 ERPia 위에 띄우는데, 상태 표시는 클릭이 통과하도록
#   만들어 문제가 없다. 그런데 **[중단] 버튼은 눌려야 하므로 통과시킬 수 없다.**
#   그 작은 창이 마침 클릭 지점을 덮고 있으면 **자동화가 우리 버튼을 누른다.**
#
#   그래서 좌표 클릭 직전에 이 창구로 "여기를 누를 것이다" 를 알린다.
#   받는 쪽(`gui/overlay.py`)이 겹치면 스스로 자리를 옮긴다.
#
#   ★ 여기서 나는 예외가 클릭을 막으면 안 된다. 보조 장치가 본 동작을 세우는
#     것이 가장 나쁘다. 그래서 통째로 감싸고 로그만 남긴다.
CLICK_GUARD = None


def set_click_guard(guard) -> None:
    """좌표 클릭 직전에 부를 함수를 건다. `None` 이면 끈다.

    `guard(rect)` 형태로 불린다. `rect` 는 `(left, top, right, bottom)`.
    """
    global CLICK_GUARD
    CLICK_GUARD = guard


def _warn_click_guard(ctrl, what: str) -> None:
    """지금 누르려는 자리를 화면에 알린다. 실패해도 막지 않는다."""
    guard = CLICK_GUARD
    if guard is None:
        return
    rect = rect_of(ctrl)
    if rect is None:
        return
    try:
        guard(rect)
    except Exception as exc:
        log.debug("클릭 가드 실패(계속): %s: %s", type(exc).__name__, exc)


def bring_forward(ctrl, what: str) -> bool:
    """대상의 **최상위 창을 앞으로** 가져온다. 성공 여부를 돌려준다.

    ★ 좌표를 누르기 **직전에** 불리는 유일한 공통 지점이라, 여기서
      `_warn_click_guard()` 로 화면의 오버레이에게 비켜 달라고 알린다
      (`set_click_guard` 주석). 네 군데 좌표 클릭 경로가 모두 이 함수를 거친다.

    ★ 왜 필요한가 (2026-09-10 실측).

    | 방식 | 창이 뒤에 있으면 |
    | --- | --- |
    | 좌표 클릭(`click_input`) | **위에 있는 다른 창**을 누른다 |
    | 키 입력(`type_keys(set_foreground=False)`) | **지금 포커스가 있는 창**으로 간다 |

    같은 코드가 손으로 돌릴 때는 되고(내가 방금 클릭해서 ERPia 가 앞에 있다)
    자동 실행에서는 실패했다. 물류대기 하단 스크롤이 그렇게 조용히 실패해
    44행 중 20행만 훑었고, 물류관리 전체선택도 먹지 않았다.

    `ensure_onscreen()` 은 창이 **화면 밖일 때만** 복원한다. 앞으로 가져오지는
    않는다. 그래서 이 함수가 따로 필요하다.
    """
    _warn_click_guard(ctrl, what)
    try:
        window = ctrl.top_level_parent()
    except Exception as exc:
        log.debug("%s 의 최상위 창을 찾지 못했다: %s", what, type(exc).__name__)
        return False
    try:
        if window.is_minimized():
            window.restore()
        window.set_focus()
    except Exception as exc:
        # 다른 창이 포그라운드를 붙잡고 있으면 실패할 수 있다. 막지는 않는다.
        log.debug("%s 창을 앞으로 가져오지 못했다: %s", what, type(exc).__name__)
        return False
    return wait_foreground(window, what)


def _foreground_handle() -> int:
    """지금 맨 앞에 있는 창의 핸들. 읽지 못하면 0."""
    import ctypes

    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception as exc:
        log.debug("포그라운드 창을 읽지 못했다: %s", type(exc).__name__)
        return 0


def wait_foreground(window, what: str) -> bool:
    """`set_focus()` 뒤 창이 **실제로 앞에 올라올 때까지** 기다린다.

    ★ `set_focus()` 는 요청만 하고 바로 돌아온다. Windows 는 창을 올리는 데
      시간이 걸리므로, 곧바로 `click_input()` 을 하면 좌표에 **아직 위에 있는
      다른 창**이 눌린다. 우리 GUI 가 앞에 있는 **그 실행의 첫 좌표 클릭**에서만
      드러나고, 그 뒤로는 이미 앞에 있어 재현되지 않는다.
    """
    handle = getattr(window, "handle", None)         or getattr(getattr(window, "element_info", None), "handle", None)
    if not handle:
        log.debug("%s 창의 핸들을 읽지 못했다. 앞으로 올라왔는지 확인하지 않는다.", what)
        return False
    try:
        wait_for(lambda: _foreground_handle() == int(handle),
                 f"{what} 창이 맨 앞으로",
                 timeout=FOREGROUND_TIMEOUT, interval=FOREGROUND_POLL)
        return True
    except WaitTimeout:
        # 막지는 않는다. 다만 **왜 엉뚱한 곳이 눌렸는지** 알 수 있게 남긴다.
        log.warning("%s 창이 %.1f초 안에 맨 앞으로 오지 않았다. 지금 맨 앞: %s",
                    what, FOREGROUND_TIMEOUT, _foreground_title())
        return False


def _foreground_title() -> str:
    """맨 앞 창의 제목. 진단 로그용이다."""
    import ctypes

    try:
        handle = _foreground_handle()
        length = ctypes.windll.user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(handle, buffer, length + 1)
        return f"{buffer.value!r} (handle {handle})"
    except Exception as exc:
        return f"(읽지 못함: {type(exc).__name__})"


def ensure_onscreen(ctrl, what: str) -> bool:
    """좌표로 누르기 전에 컨트롤이 **화면 안에** 있는지 확인한다.

    창이 최소화돼 있으면 `rectangle()` 이 화면 밖 좌표를 준다(실측: `L-779, T-559`).
    그 상태에서 `click_input()` 을 하면 **엉뚱한 곳을 누르거나 아무 일도 일어나지
    않는다.** 사람이 실행 중에 창을 내려도 이렇게 된다.

    ★ **음수 좌표 = 화면 밖이 아니다** (2026-09-10 실측으로 고침).
      모니터가 두 대이고 보조 모니터가 왼쪽에 있으면 그쪽 창은 좌표가 음수다.
      예전 코드는 `left >= 0` 으로 판정해서, **왼쪽 모니터에 있는 창의 클릭을
      전부 건너뛰었다.** 지금은 모든 모니터를 합친 영역과 비교한다.

    화면 밖이면 최상위 창을 복원·포커스하고 다시 본다. 그래도 밖이면 False.
    """
    try:
        rect = ctrl.rectangle()
    except Exception as exc:
        log.debug("%s 위치를 읽지 못했다: %s", what, type(exc).__name__)
        return True          # 판단할 수 없으면 막지 않는다

    if _on_screen(rect):
        return True

    log.warning("%s 이(가) 화면 밖이다 (%s, 화면 영역 %s). 창을 복원한다.",
                what, rect, virtual_screen())
    try:
        window = ctrl.top_level_parent()
        if window.is_minimized():
            window.restore()
        window.set_focus()
    except Exception as exc:
        log.warning("창 복원 실패: %s", exc)
        return False

    try:
        rect = ctrl.rectangle()
    except Exception:
        return False
    ok = _on_screen(rect)
    log.info("창 복원 후 위치: %s (%s)", rect, "화면 안" if ok else "여전히 화면 밖")
    return ok


def screen_visible(main_window, pane_auto_id: str, what: str) -> bool:
    """화면 Pane 이 **실제로 화면 안에** 있는지.

    ★ Pane 이 있다 ≠ 앞에 보인다. 다른 화면 탭이 활성이면 Pane 은 트리에 있지만 화면 밖
      좌표를 갖고, 그 상태의 좌표 클릭은 아무 일도 안 한다 (09-10 물류관리 실측).
    """
    try:
        pane = find(main_window, auto_id=pane_auto_id, control_type="Pane",
                    max_depth=SCREEN_PANE_DEPTH, timeout=2, what=what)
    except ControlNotFound:
        return False
    try:
        return _on_screen(pane.rectangle())
    except Exception as exc:
        log.debug("화면 위치를 읽지 못했다: %s", type(exc).__name__)
        return True          # 판단할 수 없으면 막지 않는다


def click(ctrl, what: str, dry_run: bool = False,
          methods: tuple[str, ...] = CLICK_METHODS) -> str:
    """안전 클릭. Invoke → Select → 컨트롤 클릭 순으로 시도한다.

    좌표는 `click_input()` 내부에서 `rectangle()` 기준으로 계산되며
    어디에도 저장하지 않는다.

    `methods` 로 시도 순서를 좁힐 수 있다. 그리드 셀의 아이콘은 Select 가
    '성공'하면서 아이콘은 안 눌리므로 `CELL_ICON_METHODS` 를 쓴다.
    """
    if dry_run:
        enabled = is_enabled(ctrl)
        log.info("[dry-run] 클릭 대상: %s = %s (활성=%s)", what, describe(ctrl), enabled)
        if not enabled:
            log.warning("[dry-run] %s 은(는) 지금 비활성이다. 실제 실행이면 누르지 않는다.", what)
        return "dry-run"

    # 비활성 버튼은 눌러도 아무 일이 일어나지 않는다(회색 처리).
    # 조용히 지나가면 "눌렀는데 반응이 없다"로 헤매게 되므로 여기서 멈춘다.
    if not is_enabled(ctrl):
        raise ControlDisabled(f"{what} 이(가) 비활성이라 누르지 않았다. {describe(ctrl)}")

    actions = {
        "Invoke": lambda: ctrl.invoke(),
        "Select": lambda: ctrl.select(),
        "클릭": lambda: ctrl.click_input(),
    }
    for name in methods:
        # 좌표로 누르는 방법은 창이 화면 밖이면 소용이 없다. 먼저 복원한다.
        if name == "클릭":
            if not ensure_onscreen(ctrl, what):
                log.warning("%s: 창이 화면 밖이라 클릭을 건너뛴다.", what)
                continue
            # 창이 뒤에 있으면 그 좌표에 있는 **다른 창**을 누른다.
            bring_forward(ctrl, what)
        try:
            actions[name]()
            log.info("%s (%s)", what, name)
            return name
        except Exception as exc:
            log.debug("%s %s 실패: %s", what, name, type(exc).__name__)

    raise ControlNotFound(
        f"{what} 을(를) 누를 수 없다. 시도한 방법: {', '.join(methods)}"
    )


def set_text(ctrl, value: str, what: str, dry_run: bool = False) -> str:
    """입력창에 값을 넣는다. Value Pattern 우선, 실패 시 키 입력.

    ★ **넣은 값을 로그에 그대로 남긴다.** 엑셀 비밀번호도 마찬가지다 —
      RPA 가 맞는 값을 넣었는지 로그로 확인하려는 것이다 (사용자 확정
      2026-09-17). ERPia 로그인 비밀번호는 이 함수를 쓰지 않고
      `automation/login.py` 가 가려서 찍는다.
    """
    if dry_run:
        log.info("[dry-run] 입력 대상: %s = %s  값=%r", what, describe(ctrl), value)
        return "dry-run"

    try:
        ctrl.set_focus()
    except Exception as exc:
        log.debug("%s 포커스 실패(계속): %s", what, exc)

    try:
        ctrl.set_edit_text(value)
        log.info("%s 입력: %r (Value Pattern)", what, value)
        return "Value Pattern"
    except Exception:
        ctrl.type_keys("^a{BACKSPACE}", set_foreground=False)
        # `+^%~(){}` 는 pywinauto 키 문법이다. 감싸지 않으면 `주문 (1).xlsx`·`a+b` 가 다르게 들어간다
        ctrl.type_keys(re.sub(r"([+^%~(){}])", r"{\1}", value),
                       with_spaces=True, set_foreground=False)
        log.info("%s 입력: %r (키 입력)", what, value)
        return "키 입력"


# ---------------------------------------------------------------- 그리드
ROW_TITLE_RE = re.compile(r"^행 (\d+)$")


def grid_rows(grid) -> list:
    """그리드의 행(ListItem) 목록.

    주의: DevExpress 가상 스크롤이라 **화면에 보이는 행만** UIA에 노출된다.
    전체 건수를 세는 용도로 쓰면 안 된다. 화면 기준 행 조작에만 쓴다.
    """
    return find_all(grid, control_type="ListItem", title_re=r"^행 \d+$")


def visible_row_count(grid) -> int:
    return len(grid_rows(grid))


def row_number(row) -> int | None:
    """'행 12' → 12."""
    try:
        match = ROW_TITLE_RE.match(row.window_text())
    except Exception:
        return None
    return int(match.group(1)) if match else None


def cell(row, column: str, timeout: float | None = None):
    """행에서 컬럼 셀(DataItem)을 얻는다. 셀 title은 '<컬럼명> 행 N' 형식이다."""
    number = row_number(row)
    if number is None:
        raise ControlNotFound(f"행 번호를 읽을 수 없다: {describe(row)}")
    return find(
        row,
        title=f"{column} 행 {number}",
        control_type="DataItem",
        what=f"{column} 셀(행 {number})",
        timeout=timeout,
    )


def scroll_into_view(ctrl, what: str) -> bool:
    """컨트롤을 화면 안으로 스크롤한다. ScrollItem 패턴이 없으면 False.

    좌표를 계산하거나 저장하지 않는다. 스크롤은 프로그램이 알아서 한다.

    ★ **스크롤한 뒤에는 `wait_rect_settled()` 로 위치가 멎기를 기다린다.**
      스크롤은 곧바로 반영되지 않는다. 아래 함수 주석 참고.
    """
    try:
        ctrl.iface_scroll_item.ScrollIntoView()
        log.debug("%s 을(를) 화면 안으로 스크롤했다.", what)
        return True
    except Exception as exc:
        log.debug("%s ScrollIntoView 불가: %s", what, type(exc).__name__)
        return False


# 위치가 멎었다고 보기까지 **연속으로** 같아야 하는 횟수.
RECT_SETTLE_ROUNDS = 2
RECT_SETTLE_TIMEOUT = 3.0       # 그래도 안 멎으면 포기하고 진행한다
RECT_SETTLE_POLL = 0.1


def wait_rect_settled(ctrl, what: str,
                      timeout: float = RECT_SETTLE_TIMEOUT) -> bool:
    """컨트롤 위치가 **멎을 때까지** 기다린다. 멎었으면 True, 못 읽으면 막지 않고 True.

    스크롤(`ScrollIntoView` 등) 직후 다시 그리기 전에 읽으면 옛 좌표다 — 느린 PC 에서 그 좌표를 눌러
    업로드가 빗나갔다 (09-15). `click_input()` 은 누르는 순간 다시 읽지만 그 시점이 너무 이르다.
    """
    def read():
        return rect_of(ctrl)      # `tuple(RECT)` 금지 — RECT 는 iterable 이 아니다 (CONTROLS.md "tuple(RECT)")

    state = {"last": read(), "same": 0}
    if state["last"] is None:
        return True

    def settled() -> bool:
        current = read()
        if current is None:
            state["same"] = RECT_SETTLE_ROUNDS       # 읽지 못하면 막지 않는다
            return True
        if current == state["last"]:
            state["same"] += 1
        else:
            log.debug("%s 위치가 아직 움직인다: %s → %s",
                      what, state["last"], current)
            state["last"], state["same"] = current, 0
        return state["same"] >= RECT_SETTLE_ROUNDS

    try:
        wait_for(settled, f"{what} 위치 안정", timeout=timeout,
                 interval=RECT_SETTLE_POLL)
    except WaitTimeout:
        log.warning("%s 위치가 %.1f초 동안 멎지 않았다. 마지막 위치로 진행한다: %s",
                    what, timeout, state["last"])
        return False
    return True


def rect_of(ctrl) -> "tuple | None":
    """위치를 `(left, top, right, bottom)` 튜플로 읽는다. 못 읽으면 None.

    **예외를 올리지 않는다.** 계측·비교용이라 이것 때문에 흐름이 멈추면 안 된다.

    ★ **`tuple(rect)` 로 만들면 안 된다.** pywinauto 의 `RECT` 는 ctypes
      구조체라 iterable 이 아니어서 `TypeError` 가 난다. 2026-09-15 에 그렇게
      썼다가, 비교가 매번 예외로 빠져 **진단이 통째로 무동작**이었다.
      그걸 모르고 "위치가 안 변했다" 고 결론까지 냈다. 필드를 직접 읽는다.
    """
    try:
        rect = ctrl.rectangle()
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception as exc:
        log.debug("위치를 읽지 못했다: %s", type(exc).__name__)
        return None


def has_hidden_rows(grid) -> bool | None:
    """화면 밖에 행이 더 있는가 — **세로 스크롤바가 보이면** True. 못 읽으면 None.

    가상 스크롤이라 보이는 행만 셀 수 있다(`grid_rows`). 스크롤바가 없으면 보이는 것이
    전부라 그 수가 **정확한 건수**이고, 있으면 "N건 이상" 이다. 숨은 스크롤바는 크기가
    0 이다(`grid_data_area` 와 같은 판정).
    """
    try:
        bars = search(grid, strict=True, control_type="ScrollBar")
    except Exception as exc:                 # noqa: BLE001 — 판정만 못 할 뿐이다
        log.debug("스크롤바를 읽지 못했다: %s", type(exc).__name__)
        return None
    for bar in bars:
        box = rect_of(bar)
        if not box or box[2] <= box[0] or box[3] <= box[1]:
            continue
        name = (bar.element_info.name or "").lower()
        if "vertical" in name or "세로" in name:
            return True
    return False


def grid_data_area(grid) -> "tuple | None":
    """그리드에서 **행이 실제로 보이는 영역** `(left, top, right, bottom)`.

    그리드 사각형에서 **스크롤바와 머리줄을 뺀다.** 못 읽으면 None.
    크기가 0 인 스크롤바는 숨은 것이라 빼지 않는다.
    """
    rect = rect_of(grid)
    if rect is None:
        return None
    left, top, right, bottom = rect
    for bar in search(grid, control_type="ScrollBar"):
        box = rect_of(bar)
        if not box or box[2] <= box[0] or box[3] <= box[1]:
            continue
        name = (bar.element_info.name or "").lower()
        if "horizontal" in name or "가로" in name:
            bottom = min(bottom, box[1])
        elif "vertical" in name or "세로" in name:
            right = min(right, box[0])
    for head in search(grid, control_type="Header"):
        box = rect_of(head)
        if box and box[1] < bottom and box[3] > top:
            top = max(top, box[3])
    return left, top, right, bottom


def _center_inside(cell, area) -> "bool | None":
    box = rect_of(cell)
    if box is None or area is None:
        return None
    x, y = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return area[0] <= x < area[2] and area[1] <= y < area[3]


def reveal_cell(grid, cell, what: str) -> bool:
    """셀 **가운데**가 그리드의 보이는 영역 밖이면 보이게 만든다. 움직였으면 True.

    ## 왜 필요한가 (2026-09-17 실측, `docs/CONTROLS.md` "정정" 절)

    보이는 영역 아래쪽에 **걸쳐 잘린 행**의 셀을 누르면, 클릭은 셀 가운데에
    떨어지고 그 자리는 **가로 스크롤바 위**다. 실제로 눌린 것:

    | 업체 | 눌린 것 | 결과 |
    | --- | --- | --- |
    | comp01 (잘린 행 4곳) | 스크롤바 손잡이 `Thumb '위치'` | 아무 일도 없음 — 파일 창 안 뜸 |
    | user01 (행 16) | 스크롤바 빈 트랙 | **한 페이지 가로 스크롤** — 셀이 152px 튐 |

    **클릭이 아니라 UIA SetFocus** 로 보이게 한다. 이 그리드의 셀은
    ScrollItemPattern·SelectionItemPattern 이 없다(`scroll_into_view()` 가
    조용히 아무것도 안 하는 이유). SetFocus 는 실측으로 행을 올렸다
    (셀 y 520 → 496, 그 자리가 `엑셀업로드 행 16` 이 됐다).

    가운데가 이미 안쪽이면 **아무것도 하지 않는다.** 읽지 못하면 막지 않는다.
    """
    area = grid_data_area(grid)
    inside = _center_inside(cell, area)
    if inside is None or inside:
        return False
    log.info("%s 가운데가 그리드 보이는 영역 밖이다 (셀 %s / 영역 %s). "
             "포커스를 줘서 보이게 한다.", what, rect_of(cell), area)
    try:
        cell.set_focus()
    except Exception as exc:
        log.warning("%s 에 포커스를 주지 못했다: %s. 그대로 누른다.",
                    what, type(exc).__name__)
        return False
    wait_rect_settled(cell, what)
    if _center_inside(cell, grid_data_area(grid)):
        log.info("%s 가 보이게 됐다: %s", what, rect_of(cell))
    else:
        log.warning("%s 가 포커스 뒤에도 보이는 영역 밖이다: %s. 헛클릭할 수 있다.",
                    what, rect_of(cell))
    return True


RECHECK_TIMEOUT = 3.0       # 누르기 직전에 셀을 다시 잡을 때의 상한


def recheck_cell(grid, column: str, number: int, fallback=None):
    """**누르기 직전에** 셀을 행 번호로 다시 잡고 위치가 멎기를 기다린다. 못 잡으면 `fallback`.

    처음 잡은 셀이 낡는 이유: 우리가 스크롤한 뒤 반영 전 / ERPia 가 창이 뜬 뒤에도 배치를 더 움직인다
    (그리드가 다시 그려지면 요소 자체가 낡는다). 셀 UIA 이름 `'<컬럼> 행 N'` 의 N 은 가로 스크롤에
    흔들리지 않는 신원이다. 잡은 뒤 `wait_rect_settled` — 셀은 컬럼 정렬 → 행 정렬 두 단계로 자리를 잡는다.
    """
    before = rect_of(fallback) if fallback is not None else None
    what = f"{column} 셀(행 {number})"
    try:
        found = find(grid, title=f"{column} 행 {number}", control_type="DataItem",
                     what=what, timeout=RECHECK_TIMEOUT)
    except ControlNotFound:
        log.warning("%s 을(를) 다시 잡지 못했다. 처음 잡은 것을 그대로 쓴다.", what)
        return fallback

    wait_rect_settled(found, what)

    after = rect_of(found)
    if after is None:
        return found
    if before is not None and after != before:
        # 이 줄이 뜨면 **기다린 덕에 옛 좌표를 안 눌렀다**는 뜻이다. 남겨 둔다.
        log.info("%s 위치가 처음과 다르다: %s → %s (멎은 위치로 누른다)",
                 what, before, after)
    return found


CELL_TITLE_RE = re.compile(r"^(?P<column>.+) 행 (?P<row>\d+)$")


def column_values(grid, column: str) -> dict[int, str]:
    """그리드에서 **한 컬럼 전체**를 한 번에 읽어 `{행 번호: 값}` 으로 돌려준다.

    행마다 셀을 찾으면 행 수만큼 트리를 훑는다. 컬럼으로 한 번에 모으면
    **한 화면당 탐색 1회**로 끝난다 (2026-09-10: 410행 스캔 91초의 주원인).

    가상 스크롤이라 **화면에 보이는 행만** 들어 있다. 스크롤해 가며 여러 번 부른다.
    """
    out: dict[int, str] = {}
    pattern = f"^{re.escape(column)} 행 \\d+$"
    for item in find_all(grid, control_type="DataItem", title_re=pattern):
        try:
            name = item.element_info.name or ""
        except Exception as exc:
            log.debug("셀 이름을 읽지 못했다: %s", type(exc).__name__)
            continue
        matched = CELL_TITLE_RE.match(name)
        if not matched:
            continue
        out[int(matched.group("row"))] = cell_value(item)
    return out


def columns_values(grid, columns) -> dict[str, dict[int, str]]:
    """여러 컬럼을 **한 번의 탐색으로** 읽는다. `{컬럼: {행 번호: 값}}`.

    ## 왜 필요한가 (2026-09-15 실측)

    `column_values()` 를 컬럼마다 부르면 **그 횟수만큼 하위 트리를 끝까지 훑는다.**
    `find_elements` 는 조건에 맞는 것을 찾아도 멈추지 않고, 거르기 위해 모든
    요소의 이름을 읽는다. 물류대기 하단 스캔은 한 화면에서 세 컬럼
    (`ERP상품코드` / `보류` / 체크)을 읽으므로 **세 번** 훑고 있었다.

    실측: 하단 509행 스캔에 **118초** (페이지당 약 4초). 그 큰 몫이 이것이다.

    한 번만 훑고 이름으로 갈라 담는다. 컬럼 후보를 정규식 하나로 합쳐 넘기므로
    거르기도 탐색 안에서 한 번에 끝난다.
    """
    wanted = [name for name in dict.fromkeys(columns) if name]
    out: dict[str, dict[int, str]] = {name: {} for name in wanted}
    if not wanted:
        return out
    joined = "|".join(re.escape(name) for name in wanted)
    for item in find_all(grid, control_type="DataItem",
                         title_re=f"^(?:{joined}) 행 \\d+$"):
        try:
            name = item.element_info.name or ""
        except Exception as exc:
            log.debug("셀 이름을 읽지 못했다: %s", type(exc).__name__)
            continue
        matched = CELL_TITLE_RE.match(name)
        if not matched:
            continue
        column = matched.group("column")
        if column in out:
            out[column][int(matched.group("row"))] = cell_value(item)
    return out


def row_columns(row) -> list[str]:
    """행에 실제로 존재하는 셀의 컬럼명 목록. 실패 진단용이다."""
    number = row_number(row)
    suffix = f" 행 {number}"
    names = []
    for item in search(row, control_type="DataItem"):
        try:
            name = item.element_info.name or ""
        except Exception:
            continue
        if name.endswith(suffix):
            names.append(name[: -len(suffix)])
        elif name:
            names.append(name)
    return names


# 방향별 대체 키. ScrollPattern 이 없을 때 쓴다.
SCROLL_KEYS = {"right": "{END}", "down": "{PGDN}", "top": "^{HOME}"}


def scroll_grid(grid, direction: str, anchor=None, how: str = "ScrollPattern") -> bool:
    """그리드를 한 칸 스크롤한다. 성공 여부를 돌려준다.

    수단이 하나면 그것이 막혔을 때 방법이 없다. 우선순위 순으로 둔다.
      1) UIA ScrollPattern  — 컨트롤이 스스로 스크롤한다
      2) 키보드            — 우측은 End, 아래는 PageDown
    좌표는 쓰지 않는다. (CLAUDE.md "UI 조작 우선순위")
    """
    if direction == "top":
        # ScrollPattern 에는 "맨 위" 개념이 없다. 키보드로만 한다.
        how = "키보드"

    if how == "ScrollPattern":
        try:
            grid.scroll(direction, "page")
            return True
        except Exception as exc:
            # 왜 안 되는지 로그에 남긴다. debug로 숨기면 원인 파악이 안 된다.
            log.info("ScrollPattern %s 스크롤 불가(%s). 키보드로 전환한다.",
                     direction, exc)
            return False

    key = SCROLL_KEYS.get(direction)
    if key is None:
        log.info("%r 방향의 대체 키가 없다.", direction)
        return False
    if anchor is None:
        log.info("키보드 스크롤에 기준 컨트롤(anchor)이 없다.")
        return False
    # 값을 바꾸는 키가 아니라 이동 키다.
    targets = search(anchor, control_type="DataItem") or [anchor]
    bring_forward(grid, f"그리드 {direction} 스크롤")
    try:
        targets[0].set_focus()
        targets[0].type_keys(key, set_foreground=True)
        return True
    except Exception as exc:
        log.debug("키보드 %s 스크롤 불가: %s", key, type(exc).__name__)
        return False


SCROLLED_CELL_WAIT = 2.0   # 초. 한 번 스크롤한 뒤 셀이 트리에 나타나기를 기다리는 상한


def cell_scrolled(row, column: str, grid=None, max_scrolls: int = 10):
    """셀을 얻는다. 화면 밖이면 우측으로 스크롤해 가며 찾는다.

    **DevExpress 그리드는 가로로도 가상화된다.** 화면 밖 컬럼의 셀은 UIA 트리에
    아예 없어서, 스크롤하기 전에는 찾을 수 없다.
    사이트 그리드의 `엑셀업로드` 는 우측 끝에 있어 반드시 스크롤이 필요하다.
    (2026-09-03 사용자 화면으로 확인)
    """
    try:
        return _found_cell(row, column)
    except ControlNotFound:
        # 아직 화면 밖이다. 아래에서 우측으로 스크롤해 가며 다시 찾는다.
        log.debug("%r 셀이 지금 화면에 없다. 스크롤해서 찾는다.", column)

    if grid is None:
        raise ControlNotFound(
            f"{column!r} 셀이 화면에 없다. 스크롤하려면 grid 를 넘겨야 한다."
        )

    log.info(
        "%r 컬럼이 화면 밖이다. 우측으로 스크롤한다. 현재 보이는 컬럼: %s",
        column, row_columns(row) or "(없음)",
    )

    for how in ("ScrollPattern", "키보드"):
        for attempt in range(max_scrolls):
            if not scroll_grid(grid, "right", anchor=row, how=how):
                break
            try:
                # 짧게 본다. 기본 10초면 없는 컬럼 하나에 스크롤 20번 × 10초를 쓴다 (09-28 검토)
                found = _found_cell(row, column, timeout=SCROLLED_CELL_WAIT)
            except ControlNotFound:
                continue
            log.info("%r 컬럼을 찾았다 (%s, %d회).", column, how, attempt + 1)
            # 여기서 또 스크롤하지 않는다. `_found_cell` 이 이미 화면 안으로
            # 당기고 **위치가 멎기를 기다렸다.** 한 번 더 당기면 기다림 없이
            # 움직여 놓는 셈이라, 고쳐 놓은 경쟁 상태가 되살아난다.
            return found

    raise ControlNotFound(
        f"{column!r} 셀을 찾지 못했다. 우측으로 스크롤해도 나타나지 않았다.\n"
        f"  이 행에 있는 컬럼: {row_columns(row) or '(없음)'}\n"
        "  컬럼명이 다르거나 그리드가 스크롤되지 않았을 수 있다."
    )


def _found_cell(row, column: str, timeout: float | None = None):
    """셀을 찾는다. 컬럼을 찾아 스크롤하지는 않되, **화면 안으로는 당긴다.**

    ★ 당긴 뒤에는 **위치가 멎을 때까지 기다린다.** 스크롤은 곧바로 반영되지
      않아서, 바로 위치를 읽으면 옛 좌표가 나온다 (`wait_rect_settled` 주석).
    """
    found = cell(row, column, timeout=timeout)
    if scroll_into_view(found, f"{column} 셀"):
        wait_rect_settled(found, f"{column} 셀")
    return found


# 셀의 UIA Name 은 값이 아니라 "<컬럼명> 행 N" 형식의 식별자다 (`CELL_TITLE_RE`).
# window_text() 로 읽으면 '사이트코드 행 2' 같은 문자열이 나온다.


def cell_value(ctrl) -> str:
    """셀의 **실제 값**을 읽는다. 못 읽으면 빈 문자열.

    2026-09-04에 사이트코드를 찾지 못한 원인이 여기였다.
    DataItem의 Name은 값이 아니라 '<컬럼명> 행 N' 이라는 식별자다.
    값은 별도 패턴으로 읽어야 한다.

    순서 (CLAUDE.md "문자 읽기 우선순위" - UI Automation 우선):
      1) Value Pattern
      2) LegacyIAccessible Value
      3) 하위 Text control 들의 텍스트
      4) window_text() — 단, 식별자 형식이면 값이 아니므로 버린다
    """
    try:
        value = ctrl.iface_value.CurrentValue
        if value and str(value).strip():
            return str(value).strip()
    except Exception as exc:
        log.debug("Value Pattern 없음: %s", type(exc).__name__)

    try:
        value = ctrl.legacy_properties().get("Value")
        if value and str(value).strip():
            return str(value).strip()
    except Exception as exc:
        log.debug("LegacyIAccessible 없음: %s", type(exc).__name__)

    parts = []
    for item in search(ctrl, control_type="Text"):
        try:
            text = (item.window_text() or "").strip()
        except Exception:
            continue
        if text:
            parts.append(text)
    if parts:
        return " ".join(parts)

    try:
        text = (ctrl.window_text() or "").strip()
    except Exception:
        return ""
    # '사이트코드 행 2' 는 값이 아니라 이름이다. 값으로 쓰면 안 된다.
    return "" if CELL_TITLE_RE.match(text) else text


def is_checked(ctrl) -> bool | None:
    """체크 상태. 읽지 못하면 None (알 수 없음).

    True/False 만 돌려주면 '못 읽음'과 '체크 안 됨'을 구분할 수 없다.
    구분하지 않으면 판정 불가 상태를 '미완료'로 오해해 무한 대기하게 된다.
    """
    try:
        state = ctrl.iface_toggle.CurrentToggleState
        if state in (0, 1):                    # 2 = indeterminate
            return bool(state)
    except Exception as exc:
        log.debug("Toggle Pattern 없음: %s", type(exc).__name__)

    try:
        # LegacyIAccessible STATE_SYSTEM_CHECKED = 0x10
        state = ctrl.legacy_properties().get("State")
        if isinstance(state, int):
            return bool(state & 0x10)
    except Exception as exc:
        log.debug("LegacyIAccessible 없음: %s", type(exc).__name__)

    value = cell_value(ctrl).strip().lower()
    if value in ("true", "1", "y", "체크", "checked", "on"):
        return True
    if value in ("false", "0", "n", "unchecked", "off"):
        return False
    return None


# 로그에만 쓰는 셀을 읽을 때의 상한. 없으면 바로 포기한다.
# 가로 가상화 때문에 화면 밖 컬럼은 UIA 트리에 아예 없다. 기본 상한(10초)으로
# 기다리면 **로그 한 줄 때문에** 셀 하나당 10초를 버린다
# (2026-09-10 통합 실행에서 4회 중 3회 실패, 30초).
INFO_CELL_TIMEOUT = 1.0


def cell_text(row, column: str, timeout: float | None = None) -> str:
    """행의 특정 컬럼 값을 읽는다. 값이 없으면 빈 문자열.

    `timeout` 을 주면 그만큼만 기다린다. **로그용으로 읽을 때는 반드시 준다.**
    """
    try:
        number = row_number(row)
        if number is None:
            return ""
        found = find(row, title=f"{column} 행 {number}", control_type="DataItem",
                     timeout=timeout, what=f"{column} 셀(행 {number})")
        return cell_value(found)
    except ControlNotFound:
        return ""


def is_group_row(row) -> bool:
    """마켓별 그룹 헤더 행인지. 그룹 행에는 'Group Row' DataItem이 있다."""
    return bool(find_all(row, control_type="DataItem", title_re=r"^Group Row$"))


CHECK_COLUMN = "row Check Box"

# ★ 체크 셀에는 **Toggle 패턴이 없다.** 대신 **Value 패턴에 상태가 문자열로** 들어 있다
#   (2026-09-09 실측).
#
#       체크 안 됨 → '선택안됨'      체크됨 → '선택'
#
#   2026-09-04 에 "체크 상태는 읽을 수 없다" 고 적었던 것은 **Toggle 패턴만 봤기**
#   때문이다. 그 결론에 기대어 `check_row` 가 상태 확인 없이 무조건 클릭했고,
#   이미 체크된 행을 다시 눌러 **해제**하는 사고가 실제로 났다 (2026-09-09).
CHECK_ON = "선택"
CHECK_OFF = "선택안됨"


def check_state(row) -> bool | None:
    """행의 체크 상태. 읽지 못하면 None (알 수 없음).

    '체크 안 됨'과 '못 읽음'을 구분한다. 구분하지 않으면 못 읽었을 때
    엉뚱하게 클릭해 상태를 뒤집는다.
    """
    text = cell_text(row, CHECK_COLUMN).strip()
    if text == CHECK_ON:
        return True
    if text == CHECK_OFF:
        return False
    if text:
        log.debug("체크 셀 값을 해석하지 못했다: %r", text)
    return None


def check_row(row, checked: bool = True, dry_run: bool = False) -> bool:
    """행의 체크박스를 원하는 상태로 만든다.

    `Invoke` 는 예외 없이 성공하면서 체크는 안 된다 (2026-09-04 확인).
    **실제 마우스 클릭만** 쓴다. 좌표는 셀 rectangle() 중심이며 저장하지 않는다.

    **이미 원하는 상태면 누르지 않는다.** 이 컨트롤은 클릭이 토글이라,
    체크된 것을 다시 누르면 해제된다.
    """
    current = check_state(row)
    if current is checked:
        log.debug("행 체크박스가 이미 %s 다. 누르지 않는다.",
                  "체크" if checked else "해제")
        return True

    box = cell(row, CHECK_COLUMN)
    if current is None and is_checked(box) is True and checked:
        # 값을 못 읽었지만 Toggle 이 체크라고 하면 그것을 믿는다.
        return True

    click(box, f"행 체크박스({'체크' if checked else '해제'})",
          dry_run=dry_run, methods=("클릭",))
    if dry_run:
        return True

    # 누른 직후에는 옛 값이 읽힐 수 있다 (느린 PC). 잠깐 반영을 기다린다 (09-28 검토)
    def settled() -> bool:
        state = check_state(row)
        return state is None or state is checked

    try:
        wait_for(settled, "행 체크 상태 반영", timeout=CHECK_SETTLE)
    except WaitTimeout:
        log.warning("행 체크박스를 눌렀으나 상태가 %s 가 되지 않았다.",
                    CHECK_ON if checked else CHECK_OFF)
        return False
    return True


CHECK_SETTLE = 2.0   # 초. 체크 한 번의 반영 대기 — 실패하면 행마다 이만큼 쓴다


def shift_click(ctrl, what: str, dry_run: bool = False) -> None:
    """Shift 를 누른 채 클릭한다. 그리드에서 **범위 선택**에 쓴다.

    좌표는 `click_input()` 내부에서 `rectangle()` 기준으로 계산되고 저장하지 않는다.
    """
    if dry_run:
        log.info("[dry-run] Shift+클릭 대상: %s = %s", what, describe(ctrl))
        return
    if not ensure_onscreen(ctrl, what):   # `click()` 과 같다 — 화면 밖 좌표로 누르지 않는다
        raise ControlNotFound(f"{what}: 창이 화면 밖이라 Shift+클릭을 하지 않았다.")
    bring_forward(ctrl, what)   # 창이 뒤에 있으면 그 좌표의 다른 창이 눌린다
    ctrl.click_input(pressed="shift")
    log.info("%s (Shift+클릭)", what)


def right_click(ctrl, what: str, dry_run: bool = False) -> None:
    """우클릭한다. 좌표는 rectangle() 기준으로 내부 계산되며 저장하지 않는다."""
    if dry_run:
        log.info("[dry-run] 우클릭 대상: %s = %s", what, describe(ctrl))
        return
    if not ensure_onscreen(ctrl, what):
        raise ControlNotFound(f"{what}: 창이 화면 밖이라 우클릭을 하지 않았다.")
    bring_forward(ctrl, what)   # 좌표 우클릭도 맨 앞이 아니면 다른 창으로 간다
    ctrl.right_click_input()
    log.info("%s 우클릭", what)


# 팝업 메뉴 항목이 노출되는 control_type. 환경마다 다르다.
# 2026-09-04: DevExpress 메뉴에서 **하위 메뉴가 있는 항목만 MenuItem** 으로 잡혔고
# (`창고 변경 (주문단위)`), 나머지 평범한 항목은 다른 타입이었다.
# 그래서 타입으로 거르지 않고 이름으로 찾는다.
MENU_ITEM_TYPES = ("MenuItem", "Button", "ListItem", "Text", "Custom", "Pane")


def _find_elements(**criteria) -> list:
    from pywinauto import backend as _backend
    from pywinauto.findwindows import find_elements

    started = time.perf_counter()
    try:
        elements = find_elements(backend=SETTINGS.backend, **criteria)
    except Exception as exc:
        log.debug("find_elements(%s) 실패: %s", criteria, exc)
        _trace("process", criteria, started, -1)
        return []
    wrap = _backend.registry.backends[SETTINGS.backend].generic_wrapper_class
    _trace("process", criteria, started, len(elements))
    return [wrap(element) for element in elements]


def popup_menu_windows(pid: int | None) -> list:
    """프로세스에 떠 있는 **팝업 메뉴 창**의 핸들 목록.

    DevExpress 컨텍스트 메뉴는 부모 창의 자식이 아니라 **제목 없는 별도
    최상위 창**으로 뜬다 (2026-09-07 확인: class `WindowsForms10.Window.20808...`,
    크기 236x481).

    프로세스 전체를 UIA로 훑으면 화면 탭이 쌓일수록 느려진다. 실측으로
    `title` 만 주면 11.4초, `control_type` 을 더해도 6.6초였다.
    `timeouts.dialog`(10초) 안에 재시도가 사실상 불가능해서, 메뉴가 떠 있는데도
    못 찾고 실패했다 (2026-09-07 `개별 배송(B)`).

    이 창 안만 보면 트리가 작아 훨씬 빠르다.
    """
    if pid is None:
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _collect(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if title.value.strip():
            return True  # 제목이 있으면 업무 창이다. 메뉴가 아니다
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value.startswith("WindowsForms10.Window."):
            handles.append(hwnd)
        return True

    try:
        user32.EnumWindows(_collect, 0)
    except Exception as exc:
        log.info("팝업 메뉴 창 열거 실패: %s: %s", type(exc).__name__, exc)
        return []
    if not handles:
        log.debug("팝업 메뉴 창이 없다 (pid=%s). 메뉴가 아직 안 떴을 수 있다.", pid)
    return handles


def find_menu_item(pid: int | None, title: str, deep: bool = False):
    """떠 있는 메뉴에서 이름이 **완전히 같은** 항목을 찾는다. 없으면 None.

    팝업 창 안만 본다. 실측 0.06초라 반복 호출해도 부담이 없다.

    `deep=True` 면 프로세스 전체까지 넓힌다(11초). 메뉴가 별도 창으로 뜨지
    않는 환경 대비용이라 **마지막에 한 번만** 쓴다. 매번 하면 메뉴가 아직
    안 떴을 때 재시도 간격이 11초가 되어 버린다.
    """
    from pywinauto import Desktop

    limit = menu_height_limit()
    for handle in popup_menu_windows(pid):
        try:
            window = Desktop(backend=SETTINGS.backend).window(handle=handle)
            matches = search(window, title=title)
        except Exception as exc:
            # 여기서 조용히 넘어가면 "메뉴가 화면에 떠 있는데 못 찾는다"가 된다.
            # 2026-09-07에 실제로 그랬고 원인을 남기지 않아 진단할 수 없었다.
            log.info("팝업 창(%s) 검색 실패: %s: %s", hex(handle), type(exc).__name__, exc)
            continue
        # 모니터마다 배율이 다르면 시스템 배율로는 모자란다 — 메뉴 창이 뜬 모니터의 것도 본다
        window_limit = max(limit, int(MENU_ITEM_MAX_HEIGHT * _window_scale(handle)))
        for item in matches:
            if _is_menu_sized(item, window_limit):
                return item

    if not deep:
        return None

    # 폴백: 메뉴가 별도 창이 아닌 환경일 수 있다.
    for item in _find_elements(process=pid, top_level_only=False, title=title):
        if _is_menu_sized(item, limit):
            return item
    return None


# 메뉴 항목 최대 높이 (100% 배율 기준 px). 그리드·패널 같은 큰 컨테이너를 거른다.
# 실측: 컨텍스트 메뉴 항목 24px, 드롭다운 항목 16px (100% 배율).
MENU_ITEM_MAX_HEIGHT = 40


def menu_height_limit() -> int:
    """화면 배율을 반영한 메뉴 항목 최대 높이.

    per-monitor DPI 인식 상태라 `rectangle()` 은 **물리 픽셀**을 돌려준다.
    150% 배율이면 24px 항목이 36px, 200% 면 48px 이 된다.
    고정 40px 로 거르면 **고배율 PC에서 메뉴를 통째로 놓친다.**
    """
    from utils.dpi import scale_info

    try:
        scale = float(scale_info().get("scale") or 1.0)
    except Exception as exc:
        log.debug("화면 배율을 읽지 못했다(1.0으로 본다): %s", type(exc).__name__)
        scale = 1.0
    return int(MENU_ITEM_MAX_HEIGHT * max(scale, 1.0))


def _window_scale(handle: int) -> float:
    """그 창이 있는 **모니터**의 배율. 못 읽으면 1.0.

    `GetDpiForWindow` 는 쓰지 않는다 — DPI 를 모르는 앱의 창에는 모니터와 무관하게 96 을 준다.
    """
    import ctypes
    from ctypes import wintypes

    try:
        from_window = ctypes.windll.user32.MonitorFromWindow
        from_window.argtypes, from_window.restype = (wintypes.HWND, wintypes.DWORD), wintypes.HMONITOR
        monitor = from_window(handle, 2)     # MONITOR_DEFAULTTONEAREST
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        for_monitor = ctypes.windll.shcore.GetDpiForMonitor
        for_monitor.argtypes = (wintypes.HMONITOR, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
        for_monitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        return (dpi_y.value or 96) / 96      # 0 = MDT_EFFECTIVE_DPI
    except Exception as exc:                 # noqa: BLE001 — 옛 Windows. 시스템 배율로 간다
        log.debug("창 배율을 읽지 못했다: %s", type(exc).__name__)
        return 1.0


def _is_menu_sized(item, limit: int | None = None) -> bool:
    """메뉴 항목만한 크기인지. 그리드/패널을 걸러낸다."""
    if limit is None:
        limit = menu_height_limit()
    try:
        rect = item.rectangle()
    except Exception:
        return False
    return 0 < rect.height() <= limit and rect.width() > 0


def process_menu_items(pid: int | None) -> list:
    """해당 프로세스에 떠 있는 메뉴 항목 후보.

    **컨텍스트 메뉴는 행의 자식도, 최상위 Menu 도 아니다.**
    게다가 항목의 control_type 이 제각각이라 타입으로 거를 수 없다.
    진단용으로 눈에 보이는 후보를 모아 돌려준다.
    """
    if pid is None:
        return []

    found = []
    limit = menu_height_limit()
    for control_type in MENU_ITEM_TYPES:
        for item in _find_elements(process=pid, top_level_only=False,
                                   control_type=control_type):
            try:
                name = (item.element_info.name or "").strip()
                rect = item.rectangle()
            except Exception:
                continue
            # 화면에 실제로 그려진 작은 항목만. 그리드/패널을 걸러낸다.
            if name and 0 < rect.height() <= limit and rect.width() > 0:
                found.append(item)
    return found


def menu_item_names(items) -> list[str]:
    names = []
    for item in items:
        try:
            name = (item.element_info.name or "").strip()
            control_type = item.element_info.control_type
        except Exception:
            continue
        if name:
            names.append(f"{name} <{control_type}>")
    return names


def click_process_menu_item(pid: int | None, title: str, dry_run: bool = False,
                            methods: tuple = ("Invoke", "클릭")) -> str:
    """떠 있는 컨텍스트 메뉴에서 항목을 **완전 일치**로 찾아 누른다.

    부분 일치를 쓰면 안 된다. `배송보류` 와 `배송보류(주문단위)` 처럼
    한쪽이 다른 쪽의 접두사인 항목이 함께 있다.

    control_type 은 하나로 고정하지 않는다. 2026-09-04에 `MenuItem` 으로 걸렀더니
    하위 메뉴가 있는 항목 하나만 잡혔다. `process_menu_items()` 가 후보 타입을
    모두 훑는다.

    **찾는 방법을 진단과 똑같이 맞춘다.** 예전에는 여기서만
    `find_elements(process=pid, title=...)` 로 프로세스 전체를 이름 하나로
    훑었는데, 화면 탭이 늘어나자 한 번 도는 데 수십 초가 걸렸다.
    2026-09-07 실행에서 `개별 배송(B)` 가 화면에 떠 있는데도 10초 안에
    끝나지 않아 실패했고, 정작 실패 진단에는 그 항목이 찍혀 나왔다.

    누르면 **모달 확인 창**이 뜨는 항목은 `methods=("클릭",)` 로 부른다 — Invoke 가 창이 닫힐 때까지
    돌아오지 않는다 (09-29 `선택주문 매출처리` 에서 5분 넘게 멈췄다. 물류대기 [저장] 과 같다).
    """
    from utils.wait import WaitTimeout, wait_for

    def probe():
        return find_menu_item(pid, title)

    try:
        item = wait_for(probe, f"메뉴 항목 {title!r}", timeout=SETTINGS.timeouts.dialog)
    except WaitTimeout as exc:
        # 메뉴가 별도 창으로 뜨지 않는 환경일 수 있다. 넓혀서 한 번 더 본다.
        item = find_menu_item(pid, title, deep=True)
        if item is None:
            raise ControlNotFound(
                f"메뉴 항목 {title!r} 을(를) 찾지 못했다.\n"
                f"  프로세스에서 찾은 항목: "
                f"{menu_item_names(process_menu_items(pid)) or None}"
            ) from exc
        log.info("팝업 창에서는 못 찾고 프로세스 전체 검색으로 찾았다.")

    log.info("메뉴 항목 발견: %s", describe(item))
    return click(item, f"메뉴 [{title}]", dry_run=dry_run, methods=methods)


def header_select_all(grid, dry_run: bool = False) -> str:
    """그리드 헤더의 전체선택 체크박스를 누른다.

    헤더는 `Header` control이고 title은 'row Check Box'다.
    Toggle Pattern이 없어 클릭으로 처리한다.
    """
    header = find(
        grid,
        title="row Check Box",
        control_type="Header",
        what="헤더 전체선택 체크박스",
    )
    return click(header, "헤더 전체선택", dry_run=dry_run)


HEADER_PANEL_NAME = "헤더 패널"


def header_panel(grid):
    """그리드의 헤더 패널(Custom). 없으면 `ControlNotFound`."""
    for child in _as_wrapper(grid).children():
        try:
            if (child.element_info.name or "") == HEADER_PANEL_NAME:
                return child
        except Exception as exc:
            log.debug("헤더 패널 확인 실패: %s", type(exc).__name__)
    raise ControlNotFound(f"{HEADER_PANEL_NAME} 을(를) 찾지 못했다. {describe(grid)}")


def column_header(grid, column: str):
    """컬럼 헤더. 같은 이름이 여러 개면 **가장 높은 것**을 쓴다.

    물류관리 그리드는 같은 이름의 Header가 두 벌 나온다(2026-09-04).
    헤더 패널 안에 있는 실제 헤더가 더 높고, 그 아래 겹친 쪽은 얇다.
    """
    matches = search(grid, title=column, control_type="Header")
    if not matches:
        raise ControlNotFound(f"{column!r} 컬럼 헤더를 찾지 못했다. {describe(grid)}")
    return max(matches, key=lambda item: item.rectangle().height())


def click_row_indicator(grid, first_column: str, what: str, dry_run: bool = False) -> None:
    """헤더 맨 앞 컬럼의 **좌측 인디케이터 셀**을 클릭한다(전체선택).

    체크박스 컬럼이 없는 그리드의 전체선택 경로다 (물류관리, 사용자 확인 2026-09-04).
    이 셀은 UIA 컨트롤로 노출되지 않아 컨트롤을 잡을 수 없다.
    좌표는 `rectangle()` 에서 **그 자리에서** 계산해 쓰고 버린다. 저장하지 않는다.
    """
    from pywinauto import mouse

    # ★ 좌표 클릭이다. 창이 뒤에 있으면 **그 좌표에 있는 다른 창**을 누른다.
    #   2026-09-10 실행에서 물류관리 전체선택이 먹지 않아 개별배송이 실패했다.
    #   복원을 **좌표 계산 전에** 한다 — 최소화 상태의 좌표로 누르던 결함 (09-28 검토)
    if not dry_run:
        if not ensure_onscreen(grid, what):
            raise ControlNotFound(f"{what}: 그리드가 화면 밖이라 누르지 않았다.")
        bring_forward(grid, what)

    panel = header_panel(grid)
    header = column_header(grid, first_column)
    panel_rect, header_rect = panel.rectangle(), header.rectangle()

    if header_rect.left <= panel_rect.left:
        raise ControlNotFound(
            f"{first_column!r} 헤더 왼쪽에 인디케이터 셀 자리가 없다. "
            f"패널={panel_rect} 헤더={header_rect}"
        )

    x = (panel_rect.left + header_rect.left) // 2
    y = (header_rect.top + header_rect.bottom) // 2

    if dry_run:
        log.info("[dry-run] %s: 인디케이터 셀 (%d, %d) 클릭 예정 "
                 "(패널 left=%d, %s 헤더 left=%d)",
                 what, x, y, panel_rect.left, first_column, header_rect.left)
        return

    mouse.click(coords=(x, y))
    log.info("%s (인디케이터 셀 클릭, 좌표 %d,%d)", what, x, y)


def dropdown_lists(pid: int | None) -> list:
    """떠 있는 드롭다운 목록(List) 컨트롤들. 크기가 0인 것은 뺀다.

    ★ **팝업 창 안에서만 찾는다** (2026-09-11 실측으로 바꿨다).
      예전에는 `find_elements(process=pid, top_level_only=False)` 하나였다.
      그것은 부모를 주지 않으면 **데스크톱 전체 트리**를 훑고 나서 프로세스로
      거른다. 실측 **회당 14.5초**, 한 실행에서 3회에 43.6초를 먹었다
      (전체 343.7초의 13%). 택배사·박스 드롭다운이 느리던 이유다.

      드롭다운은 컨텍스트 메뉴와 같은 **제목 없는 WinForms 팝업 창**으로 뜬다.
      그 창 핸들을 Win32 로 싸게 얻은 뒤(`popup_menu_windows`), **그 안에서만**
      `List` 를 찾으면 트리가 작아 훨씬 싸다.
    """
    if pid is None:
        return []

    def usable(container) -> bool:
        try:
            rect = container.rectangle()
        except Exception as exc:
            log.debug("드롭다운 목록 위치를 읽지 못했다: %s", type(exc).__name__)
            return False
        return rect.width() > 0 and rect.height() > 0

    from pywinauto import Desktop

    found: list = []
    for handle in popup_menu_windows(pid):
        try:
            window = Desktop(backend=SETTINGS.backend).window(handle=handle)
            containers = search(window, control_type="List")
        except Exception as exc:
            # 조용히 넘어가면 "드롭다운이 떠 있는데 못 찾는다" 가 된다.
            log.info("팝업 창(%s) List 검색 실패: %s: %s",
                     hex(handle), type(exc).__name__, exc)
            continue
        for container in containers:
            if usable(container):
                found.append(container)
    if found:
        return found

    # 폴백: 팝업 창으로 안 뜨는 환경일 수 있다. 여기서만 예전 방식을 쓴다.
    log.debug("팝업 창에서 List 를 못 찾았다. 프로세스 전체로 넓힌다(느리다).")
    for container in _find_elements(process=pid, top_level_only=False,
                                    control_type="List"):
        if usable(container):
            found.append(container)
    return found


def dropdown_items(pid: int | None) -> list:
    """떠 있는 드롭다운(List) 안의 항목들. **화면에 보이는 순서 그대로.**

    컨텍스트 메뉴와 달리 드롭다운은 `List` > `ListItem` 으로 깔끔하게 잡힌다
    (물류관리 택배사 콤보, 2026-09-04 확인).
    프로세스 전체를 이름으로 뒤지면 목록 밖의 같은 이름 컨트롤까지 걸리므로
    **목록 안으로 범위를 좁힌다.**

    ★ **그리드와 마찬가지로 가상 스크롤이다.** 지금 그려진 항목만 들어 있다.
      목록 밖 항목은 `_search_dropdown` 이 스크롤해 가며 찾는다.
    """
    items: list = []
    for container in dropdown_lists(pid):
        items.extend(_list_items(container))
    return items


def _list_items(container) -> list:
    try:
        return container.children()
    except Exception as exc:
        log.debug("드롭다운 항목을 읽지 못했다: %s", type(exc).__name__)
        return []


def _item_name(ctrl) -> str:
    try:
        return (ctrl.element_info.name or "").strip()
    except Exception as exc:
        log.debug("드롭다운 항목 이름을 읽지 못했다: %s", type(exc).__name__)
        return ""


def _dropdown_names(container) -> list[str]:
    """지금 그려져 있는 항목 이름들. 스크롤이 먹었는지 판정하는 기준이다."""
    return [_item_name(item) for item in _list_items(container)]


# 드롭다운 목록을 움직이는 수단. **읽기만 하는 수단으로 제한한다.**
#   ScrollBar     — 목록 안 수직 스크롤바의 `페이지 아래로` Invoke. 부작용이 없다
#   ScrollPattern — 목록이 Scroll 패턴을 지원하면 그것으로
#
# ★ **마우스 휠과 방향키는 쓰지 않는다** (2026-09-10 실측으로 뺐다).
#   `--find` 로 **읽기만** 했는데 택배사가 `택배사B`(목록의 첫 항목)로 바뀌었다.
#   원인: 휠은 목록이 닫히는 순간부터 **콤보 자체**로 간다. 닫힌 WinForms 콤보
#   위에서 휠을 돌리면 **선택 값이 바뀐다.** `scroll_dropdown_to_top()` 이 위로
#   여러 번 돌리므로 선택이 맨 위 항목까지 걸어 올라간다.
#   방향키도 같은 이유로 위험하다(강조 항목을 옮기고, 닫히면 값이 된다).
#   **찾기만 하려다 값을 바꾸는 수단은 두지 않는다.**
DROPDOWN_SCROLL_METHODS = ("ScrollBar", "ScrollPattern")
# 한 페이지씩 움직이는 횟수 상한. 무제한으로 돌지 않는다.
DROPDOWN_SCROLL_ROUNDS = 40


def _scroll_dropdown_once(container, direction: str, method: str) -> bool:
    """수단 하나로 드롭다운을 한 번 움직인다. 시도 자체가 불가능하면 False."""
    if method == "ScrollBar":
        names = (SCROLL_PAGE_DOWN_NAMES if direction == "down"
                 else SCROLL_PAGE_UP_NAMES)
        button = scrollbar_button(container, names)
        if button is None:
            log.debug("드롭다운 수직 스크롤바의 %s 버튼을 찾지 못했다.", direction)
            return False
        return _press_scrollbar(button, f"드롭다운 스크롤바 {direction}")

    if method == "ScrollPattern":
        try:
            container.scroll(direction, "page")
            return True
        except Exception as exc:
            log.debug("드롭다운 ScrollPattern 불가: %s", type(exc).__name__)
            return False

    return False


def scroll_dropdown(container, direction: str) -> bool:
    """드롭다운 목록을 한 페이지 움직인다.

    **항목이 실제로 바뀌었는지로 성공을 판정한다.** 예외가 없다고 움직인 것이
    아니다 — 그리드에서 이미 겪은 실패다(`scroll_down_verified` 주석 참고).

    ★ 움직이는 도중 **목록이 사라지면 즉시 멈춘다.** 드롭다운이 닫힌 뒤에도
      계속 조작하면 그 조작이 **콤보 자체로 가서 값을 바꾼다** (2026-09-10 실측).
    """
    before = _dropdown_names(container)
    if not before:
        return False
    for method in DROPDOWN_SCROLL_METHODS:
        if not _scroll_dropdown_once(container, direction, method):
            continue
        after = _dropdown_names(container)
        if not after:
            log.warning("드롭다운이 닫혔다(%s 시도 중). 더 건드리지 않는다.", method)
            return False
        if after != before:
            # 어느 수단이 먹었는지는 **한 번은 남긴다.** 환경마다 다르고,
            # 이걸 모르면 다음에 또 추측으로 수단을 늘리게 된다.
            if method not in _SCROLL_METHOD_SEEN:
                _SCROLL_METHOD_SEEN.add(method)
                log.info("드롭다운 스크롤에 %s 가 먹는다.", method)
            log.debug("드롭다운 %s 스크롤 성공 (%s)", direction, method)
            return True
        log.debug("%s 로는 드롭다운 항목이 바뀌지 않았다.", method)
    return False


# 어느 스크롤 수단이 먹었는지 한 번만 로그로 남기려고 기억한다.
_SCROLL_METHOD_SEEN: set[str] = set()


def scroll_dropdown_to_top(container) -> None:
    """목록을 맨 위로 올린다. 더 올라가지 않으면 멈춘다.

    ★ 왜 필요한가: 콤보를 열면 목록이 **지금 선택된 항목 위치에서** 열릴 수
      있다. 그 상태에서 아래로만 훑으면 위쪽 항목을 통째로 놓친다.
    """
    for _ in range(DROPDOWN_SCROLL_ROUNDS):
        if not scroll_dropdown(container, "up"):
            return


def _search_dropdown(pid: int | None, title: str) -> tuple[object | None, list[str]]:
    """목록을 맨 위로 올린 뒤 한 페이지씩 내려 가며 찾는다.

    `(찾은 항목 또는 None, 훑으면서 본 이름 전부)` 를 돌려준다.
    """
    seen: list[str] = []
    for container in dropdown_lists(pid):
        scroll_dropdown_to_top(container)
        for _ in range(DROPDOWN_SCROLL_ROUNDS + 1):
            for item in _list_items(container):
                name = _item_name(item)
                if name and name not in seen:
                    seen.append(name)
                if name == title:
                    return item, seen
            if not scroll_dropdown(container, "down"):
                break
    return None, seen


def click_dropdown_item(pid: int | None, title: str, what: str,
                        dry_run: bool = False) -> str:
    """드롭다운에서 이름이 **완전 일치**하는 **첫 항목**을 누른다.

    이름이 중복될 수 있으나 UIA에는 코드가 노출되지 않는다
    (2026-09-04 조사: ListItem 의 Value / Description / Help 전부 비어 있다).
    그래서 **처음 일치하는 항목**을 고른다 (사용자 확정 2026-09-04).

    ★ **목록은 가상 스크롤이다** (2026-09-10 사용자 관찰). 열자마자 보이는
      항목만 보고 판단하면, 스크롤을 내리면 있는 값을 "없다" 고 한다.
      그래서 보이는 데서 못 찾으면 **맨 위로 올린 뒤 끝까지 내려 가며** 찾는다.
    """
    def visible_matches() -> list:
        return [item for item in dropdown_items(pid) if _item_name(item) == title]

    # 드롭다운이 열리는 데 시간이 걸린다. 한 번만 보고 판단하면 항상 빈 목록이다.
    try:
        matches = wait_for(visible_matches, f"드롭다운 항목 {title!r}",
                           timeout=SETTINGS.timeouts.dialog)
    except WaitTimeout:
        matches = []

    if matches:
        if len(matches) > 1:
            log.warning("%s: %r 이(가) 보이는 목록에만 %d개 있다. **처음 것**을 고른다.",
                        what, title, len(matches))
        item = matches[0]
    else:
        log.info("%s: 보이는 목록에 %r 이(가) 없다. 스크롤해 가며 찾는다.", what, title)
        item, seen = _search_dropdown(pid, title)
        if item is None:
            raise ControlNotFound(
                f"드롭다운에서 {title!r} 을(를) 찾지 못했다.\n"
                f"  끝까지 내려 확인한 값 {len(seen)}개: {seen or '(없음)'}"
            )
        log.info("%s: 스크롤해서 %r 을(를) 찾았다 (그때까지 본 항목 %d개).",
                 what, title, len(seen))

    log.info("%s 항목 선택: %s", what, describe(item))
    return click(item, f"{what} [{title}]", dry_run=dry_run,
                 methods=("Invoke", "Select", "클릭"))


def visible_row_numbers(grid) -> set[int]:
    """지금 UIA에 노출된 행 번호들. 가상 스크롤이라 화면 안의 행만 들어 있다."""
    numbers = set()
    for row in grid_rows(grid):
        number = row_number(row)
        if number is not None:
            numbers.add(number)
    return numbers


# 아래로 한 화면 내리는 수단. 위에서부터 시도한다.
# ★ **스크롤바 버튼이 가장 확실하다** (2026-09-10 실측으로 확정).
#   하단 그리드에는 수직 ScrollBar 가 있고 그 안에 `페이지 아래로` / `아래로 선`
#   Button 이 있으며 **Invoke 를 지원한다.** 포커스도, 좌표도, 키보드도 필요 없다.
#   그래서 맨 앞에 둔다. 키보드 수단은 이 그리드에서 신뢰할 수 없다
#   (`set_focus()` 가 행에 먹지 않아 키가 엉뚱한 곳으로 간다).
SCROLL_DOWN_METHODS = ("ScrollBar", "ScrollPattern", "PageDown", "Down")
# 수직 스크롤바 안의 버튼 이름. 스크롤 위치에 따라 나타나는 버튼이 달라지므로
# 후보를 여러 개 둔다. 실측 이름: '위로 선' / '페이지 아래로' / '아래로 선'.
SCROLL_PAGE_DOWN_NAMES = ("페이지 아래로", "아래로 선", "Page down", "Line down")
SCROLL_PAGE_UP_NAMES = ("페이지 위로", "위로 선", "Page up", "Line up")
# ★ **더 내릴 수 있는지**는 `페이지 아래로` 로만 판정한다 (2026-09-15 실측).
#   스크롤바의 '페이지' 영역은 **썸 아래에 남은 공간이 있을 때만** 버튼으로
#   나타난다. 그래서 맨 아래에 닿으면 사라진다.
#
#   | 위치 | 버튼 |
#   | --- | --- |
#   | 맨 위 | `위로 선`, **`페이지 아래로`**, `아래로 선` |
#   | 맨 아래 | `위로 선`, **`페이지 위로`**, `아래로 선` |
#
#   화살표(`아래로 선`)는 **끝에서도 남아 있다.** 그래서 그것으로 판정하면
#   끝에 닿은 줄 모르고 눌러 보고, 안 움직이니 키보드 폴백까지 내려가
#   **마지막 행을 선택한다**(선택이 바뀌면 다른 그리드가 다시 조회된다).
SCROLL_CAN_DOWN_NAMES = ("페이지 아래로", "Page down")
VERTICAL_SCROLLBAR_NAME = "Vertical"
# 스크롤이 먹지 않을 때 몇 바퀴까지 다시 볼 것인가. 무제한 재시도는 두지 않는다.
SCROLL_RETRY_ROUNDS = 2
# ★ **스크롤을 시도하는 것 자체가 부작용이다.** 이 그리드들은 Scroll 패턴이 없어
#   키보드로만 내릴 수 있고, 키를 보내려면 마지막 행에 `set_focus()` 해야 한다.
#   DevExpress 에서 행에 포커스를 주는 것은 **그 행을 선택하는 것**이다.
#   그래서 실패하는 스크롤 시도는 "마지막 행을 여러 번 누르는" 것처럼 보인다
#   (2026-09-10 사용자 관찰). 선택 변경이 다른 그리드를 다시 조회시키는 화면에서는
#   `rounds=1` 로 시도 횟수를 줄인다.
SCROLL_SETTLE = 1.5        # 바퀴 사이에 기다리는 시간(초)
SCROLL_TO_TOP_PRESSES = 60  # 맨 위로 올릴 때 스크롤바를 누르는 횟수 상한


def scroll_down_verified(grid, methods: tuple[str, ...] = SCROLL_DOWN_METHODS,
                         rounds: int | None = None) -> bool:
    """그리드를 아래로 내린다. **행 번호가 실제로 바뀌었는지로 성공을 판정한다**
    (예외 없음 ≠ 스크롤됨 — 한 화면에서 멈춰 행을 놓친 적이 있다, 09-04). 수단은 `methods` 순서대로.
    """
    before = visible_row_numbers(grid)
    if not before:
        return False

    # 수직 스크롤바가 없으면 더 내릴 것이 없다 — 스크롤바는 행이 한 화면을 넘을 때만 생긴다(사용자 확정 09-15).
    # 이 확인 없이 수단을 다 시도하면 키보드 폴백의 `set_focus()` 가 행을 **선택**해 물류대기 다른 그리드를
    # 다시 조회시킨다. 가정이 틀리면 행을 조용히 놓치므로 INFO 로 남긴다 — 훑은 행이 적으면 여기부터 의심.
    # 스크롤바는 한 번만 찾는다 (찾기 자체가 하위 트리 전체 탐색).
    bar = vertical_scrollbar(grid)
    if bar is None:
        log.info("수직 스크롤바가 없다. 더 내릴 것이 없다고 보고 시도하지 않는다 "
                 "(보이는 행 %d~%d).", min(before), max(before))
        return False

    # 스크롤바는 있는데 **맨 아래**인 경우. `페이지 아래로` 가 사라진다
    # (위 `SCROLL_CAN_DOWN_NAMES` 주석의 실측표).
    if scrollbar_button(grid, SCROLL_CAN_DOWN_NAMES, bar=bar) is None:
        log.info("스크롤바에 '페이지 아래로' 가 없다. 맨 아래로 보고 시도하지 않는다 "
                 "(보이는 행 %d~%d).", min(before), max(before))
        return False

    # 한 바퀴 실패하면 잠깐 뒤 한 번 더 본다 — 다시 조회 중인 그리드는 스크롤이 먹지 않아
    # "끝까지 봤다" 와 구별되지 않는다 (같은 화면이 20행/410행으로 보였다, 09-10).
    rounds = SCROLL_RETRY_ROUNDS if rounds is None else max(1, rounds)
    for attempt in range(rounds):
        for method in methods:
            if not _scroll_down_once(grid, method, before, bar=bar):
                continue
            after = visible_row_numbers(grid)
            if after and after != before:
                log.debug("아래로 스크롤 성공 (%s): %d행 → %d행",
                          method, max(before), max(after))
                return True
            log.debug("%s 로는 행이 바뀌지 않았다. 다음 수단을 시도한다.", method)

        if attempt + 1 < rounds:
            pause(SCROLL_SETTLE, "그리드가 다시 조회 중일 수 있어 잠깐 뒤 재시도한다")
            now = visible_row_numbers(grid)
            if now and now != before:
                # 기다리는 동안 그리드가 바뀌었다. 끝이 아니다.
                log.info("기다리는 동안 그리드가 갱신됐다 (행 %d~%d → %d~%d).",
                         min(before), max(before), min(now), max(now))
                return True

    log.info("그리드를 더 내릴 수 없다. 보이는 행 번호 %d~%d (수단 %s, %d바퀴).",
             min(before), max(before), "/".join(methods), rounds)
    return False


def vertical_scrollbar(grid):
    """그리드의 수직 스크롤바. 없으면 None."""
    for bar in find_all(grid, control_type="ScrollBar"):
        try:
            if (bar.element_info.name or "").strip() == VERTICAL_SCROLLBAR_NAME:
                return bar
        except Exception as exc:
            log.debug("스크롤바 이름을 읽지 못했다: %s", type(exc).__name__)
    return None


def scrollbar_button(grid, names: tuple[str, ...], bar=None):
    """수직 스크롤바 **안의** 버튼을 이름으로 찾는다. 없으면 None.

    ★ 그리드에서 바로 Button 을 찾으면 **수평 스크롤바의 버튼도 섞인다.**
      반드시 수직 스크롤바 안에서 찾는다.

    ★ `bar` 를 주면 **다시 찾지 않는다.** 스크롤바를 찾는 것도 하위 트리를
      끝까지 훑는 일이라, 한 번 스크롤할 때 세 번 찾으면 그만큼 느려진다
      (2026-09-15). 부르는 쪽이 이미 찾았으면 그대로 넘긴다.
    """
    if bar is None:
        bar = vertical_scrollbar(grid)
    if bar is None:
        return None
    buttons = find_all(bar, control_type="Button")
    for name in names:
        for button in buttons:
            try:
                if (button.element_info.name or "").strip() == name:
                    return button
            except Exception as exc:
                log.debug("버튼 이름을 읽지 못했다: %s", type(exc).__name__)
    return None


def _press_scrollbar(button, what: str) -> bool:
    """스크롤바 버튼을 누른다. Invoke 우선, 실패 시 좌표 클릭."""
    try:
        button.iface_invoke.Invoke()
        return True
    except Exception as exc:
        log.debug("%s Invoke 불가(%s). 클릭으로 전환한다.", what, type(exc).__name__)
    try:
        button.click_input()
        return True
    except Exception as exc:
        log.debug("%s 클릭 불가: %s", what, type(exc).__name__)
        return False


def _scroll_down_once(grid, method: str, before: set[int], bar=None) -> bool:
    """수단 하나로 한 번 내려 본다. 시도 자체가 불가능하면 False."""
    if method == "ScrollBar":
        button = scrollbar_button(grid, SCROLL_PAGE_DOWN_NAMES, bar=bar)
        if button is None:
            log.debug("수직 스크롤바의 아래로 버튼을 찾지 못했다.")
            return False
        return _press_scrollbar(button, "스크롤바 아래로")

    if method == "ScrollPattern":
        try:
            grid.scroll("down", "page")
            return True
        except Exception as exc:
            log.debug("ScrollPattern 불가: %s", exc)
            return False

    # 키보드는 **행(ListItem)** 에 포커스를 준다. 셀에 주면 편집 상태로 들어가
    # 이동 키가 먹지 않는 경우가 있다.
    rows = grid_rows(grid)
    if not rows:
        return False
    anchor = rows[-1]
    keys = {"PageDown": "{PGDN}", "Down": "{DOWN 10}"}.get(method)
    if keys is None:
        return False

    # ★ `set_focus()` 는 이 그리드의 **행에는 효과가 없다** (2026-09-10 실측:
    #   포커스가 '헤더 패널' 에 그대로 남았다). 그래서 창을 앞으로 가져오고
    #   키를 **포그라운드로** 보낸다. 그러지 않으면 키가 엉뚱한 창으로 간다.
    bring_forward(grid, "그리드 스크롤")
    try:
        anchor.set_focus()
    except Exception as exc:
        log.debug("행 포커스 불가(계속): %s", type(exc).__name__)
    try:
        anchor.type_keys(keys, set_foreground=True)
        return True
    except Exception as exc:
        log.debug("%s 키 입력 불가: %s", method, type(exc).__name__)
        return False


def scroll_to_top(grid) -> bool:
    """그리드를 맨 위로 되돌린다. 성공 여부를 행 번호로 판정한다."""
    before = visible_row_numbers(grid)
    if not before or min(before) <= 1:
        return True

    # ★ 스크롤바 버튼이 가장 확실하다. 맨 위에 닿을 때까지 **상한 안에서** 누른다.
    #   스크롤바는 한 번만 찾는다 — 누를 때마다 다시 찾으면 그때마다 하위 트리를
    #   끝까지 훑는다 (최대 60번). 버튼은 눌릴 때마다 다시 읽으므로, 맨 위에 닿아
    #   '위로' 버튼이 사라지는 것은 그대로 잡힌다.
    bar = vertical_scrollbar(grid)
    for _ in range(SCROLL_TO_TOP_PRESSES):
        button = scrollbar_button(grid, SCROLL_PAGE_UP_NAMES, bar=bar)
        if button is None:
            break
        if not _press_scrollbar(button, "스크롤바 위로"):
            break
        numbers = visible_row_numbers(grid)
        if numbers and min(numbers) <= 1:
            return True

    rows = grid_rows(grid)
    if not rows:
        return False
    bring_forward(grid, "그리드 맨 위로")
    try:
        rows[0].set_focus()
        rows[0].type_keys("^{HOME}", set_foreground=True)
    except Exception as exc:
        log.debug("Ctrl+Home 불가: %s", type(exc).__name__)
        return False

    after = visible_row_numbers(grid)
    if after and min(after) < min(before):
        return True
    log.info("그리드를 맨 위로 되돌리지 못했다. 보이는 행 번호 %d~%d.",
             min(after or before), max(after or before))
    return False


# --- 끝까지 세기 · 가로로 가려진 컬럼 (09-29) ---------------------------------------
# 건수는 **행 수가 아니라 데이터 수**다 (사용자 지적 09-29). ERPia 그리드는 그룹 행이 섞이고,
# 물류 화면은 한 전표(박스)의 상품이 여러 행이다. 좌측 숫자(행 인디케이터)는 UIA 에 없어서
# (원시 보기·Legacy·Grid/Table 패턴 전부 없음, 09-29 실측) **전표번호 컬럼을 끝까지 읽어 중복 없이** 센다.
SCAN_PAGES = 40                 # 끝까지 내리는 상한 — 한 페이지 15~20행
HORIZONTAL_SCROLLBAR_NAME = "Horizontal"
SCROLL_PAGE_RIGHT_NAMES = ("페이지 오른쪽", "Page right")
SCROLL_PAGE_LEFT_NAMES = ("페이지 왼쪽", "Page left")
COLUMN_PAGES = 10               # 가로로 넘기는 상한


def scan_column(grid, column: str, pages: int = SCAN_PAGES) -> tuple[dict[int, str], bool]:
    """그리드를 **맨 위부터 끝까지** 내리며 한 컬럼을 읽는다. `(행 번호 → 값, 끝까지 봤나)`.

    그룹 행에는 그 셀이 없어 저절로 빠진다. 내릴 때는 스크롤바 버튼만 쓴다 — 키보드는 행을 선택해
    다른 그리드를 다시 조회시킨다. 끝나면 맨 위로 되돌린다. `끝까지 봤나` 는 `collect_counts_all` 과
    같은 판정이다: 맨 위에서 시작했고, 끝에 닿았고, **보인** 행 번호(그룹 행 포함)가 1 부터
    빠짐없이 이어진다.
    """
    at_top = scroll_to_top(grid)
    values: dict[int, str] = {}
    seen: set[int] = set()           # 그룹 행 포함, 보인 행 번호 전부 — 빠짐없이 봤는지 판정용
    capped = True
    for page in range(pages):
        cells = columns_values(grid, (column,))[column]
        numbers = visible_row_numbers(grid)
        fresh = numbers - seen
        seen |= numbers
        values.update(cells)
        if (not fresh and page) or not scroll_down_verified(grid, methods=("ScrollBar",)):
            capped = False
            break
    if capped:
        log.warning("%s 컬럼을 %d페이지까지 내렸는데 끝이 아니다.", column, pages)
    bar = vertical_scrollbar(grid)
    at_end = (has_hidden_rows(grid) is False if bar is None
              else scrollbar_button(grid, SCROLL_CAN_DOWN_NAMES, bar=bar) is None)
    # 빈 그리드는 0행이 정확한 값이다 (행 제목은 늘 읽힌다 — 못 읽는 것은 셀 값뿐)
    contiguous = sorted(seen) == list(range(1, max(seen) + 1)) if seen else True
    exact = contiguous and at_top and at_end and not capped
    if bar is not None:
        scroll_to_top(grid)
    log.info("%s 컬럼 %d행 %s (보인 행 %d)", column, len(values),
             "끝까지 봄" if exact else "**끝까지 못 봄**", len(seen))
    return values, exact


def distinct_count(grid, column: str) -> tuple[int | None, bool]:
    """그 컬럼 값을 **중복 없이** 센다 — 전표(박스) 수. `(수, 정확한가)`. 행은 있는데 값을 하나도
    못 읽으면 `(None, False)` — 0 이라고 하지 않는다."""
    values, exact = scan_column(grid, column)
    numbers = {value.strip() for value in values.values() if value and value.strip()}
    if not numbers and visible_row_numbers(grid):
        log.warning("%s 을(를) 하나도 읽지 못했다. 건수를 모른다.", column)
        return None, False
    return len(numbers), exact


def horizontal_scrollbar(grid):
    """그리드의 가로 스크롤바. 없거나 숨었으면 None."""
    for bar in find_all(grid, control_type="ScrollBar"):
        try:
            if (bar.element_info.name or "").strip() == HORIZONTAL_SCROLLBAR_NAME:
                box = rect_of(bar)
                return bar if box and box[2] > box[0] and box[3] > box[1] else None
        except Exception as exc:
            log.debug("스크롤바 이름을 읽지 못했다: %s", type(exc).__name__)
    return None


def _header_shown(grid, column: str) -> bool:
    """그 컬럼이 지금 보이는가 — 가로 가상화라 보이는 컬럼의 머리글만 UIA 에 있다."""
    return bool(search(grid, title=column, control_type="Header"))


def reveal_column(grid, column: str) -> bool:
    """가로로 가려진 컬럼이 보이게 [페이지 오른쪽] 을 누른다. 보이면 True.

    물류대기 [일반] 탭의 `전표번호` 는 오른쪽 밖에 있어 셀이 없다 (09-29 실측 — 앞 15개 컬럼만
    UIA 에 있다). 그리드에 Scroll 패턴이 없어 스크롤바 버튼으로 넘긴다. 읽은 뒤 `columns_home`.
    """
    for _ in range(COLUMN_PAGES + 1):
        if _header_shown(grid, column):
            return True
        bar = horizontal_scrollbar(grid)
        button = scrollbar_button(grid, SCROLL_PAGE_RIGHT_NAMES, bar=bar) if bar is not None else None
        if button is None or not _press_scrollbar(button, "가로 스크롤 오른쪽"):
            break
    found = _header_shown(grid, column)
    if not found:
        log.info("%r 컬럼을 가로로 넘겨도 찾지 못했다.", column)
    return found


def columns_home(grid) -> None:
    """가로 스크롤을 맨 왼쪽으로 — [페이지 왼쪽] 이 없어질 때까지 (상한 안에서)."""
    for _ in range(COLUMN_PAGES + 1):
        bar = horizontal_scrollbar(grid)
        button = scrollbar_button(grid, SCROLL_PAGE_LEFT_NAMES, bar=bar) if bar is not None else None
        if button is None or not _press_scrollbar(button, "가로 스크롤 왼쪽"):
            return
