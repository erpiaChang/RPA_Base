r"""공용 팝업(모달) 처리.

**이 프로그램의 팝업은 최상위 창이 아니라 부모 창의 자식으로 뜬다.**
`Desktop.windows()`는 `top_level_only=True`라서 잡히지 않는다.
(2026-09-03 로그인 팝업에서 확인)

그래서 부모 창 하위를 뒤진다. 본문은 하위 Text를 이어 붙여 읽는다.

`automation/login.py`에도 같은 로직이 있다. 로그인은 실제 동작이 검증된
상태라 손대지 않았다. 이후 정리할 때 이 모듈로 합칠 것.
"""
from __future__ import annotations

import re

from utils.logger import get_logger

log = get_logger(__name__)

CONFIRM_YES_RE = "예.*|확인.*|OK"
# 실패로 봐야 하는 본문
FAILURE_RE = re.compile(r"실패|오류|에러|error", re.I)

# ERPia 공통 메시지 팝업 (CONTROLS.md "메시지 팝업"). 로그인 안내·입력누락·저장 실패가 모두 이것이다.
MESSAGE_BOX_AUTO_ID = "Popup_ERPiaMessageBox"
OK_ONLY_RE = re.compile(r"확인|OK")
MAX_CHAINED = 3        # 닫으면 다음 팝업이 뜨기도 한다 (매출처리 2단). 그 이상은 이상 상황이다


class DialogNotFound(RuntimeError):
    """기대한 팝업이 뜨지 않았다."""


def post_close(handle: int) -> None:
    """창에 닫기(WM_CLOSE)를 **보내기만** 한다 — 기다리지 않는다 (취소 중인 finally 에서도 부른다, 10-07 찾기 창)."""
    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.user32.PostMessageW(wintypes.HWND(handle), 0x0010, 0, 0)   # WM_CLOSE
    except Exception as exc:
        log.debug("닫기를 보내지 못했다: %s", type(exc).__name__)


def find(parent, title_re: str, exclude_handle: int | None = None):
    """부모 창 하위에서 팝업을 찾는다. 없으면 None.

    최상위 창과 부모 하위를 모두 본다.
    """
    from utils import ui

    pattern = re.compile(title_re)
    for candidate in ui.search(parent, control_type="Window"):
        try:
            if exclude_handle and candidate.handle == exclude_handle:
                continue
            name = candidate.element_info.name or ""
        except Exception:
            continue
        if name and pattern.search(name):
            return candidate
    return None


def find_in(scopes, title_re: str, exclude_handle: int | None = None):
    """여러 범위를 순서대로 뒤져 팝업을 찾는다. 없으면 None.

    팝업이 어느 창의 자식으로 붙는지는 화면마다 다르다. 화면 Pane 하위에
    없을 수 있으므로 메인 창까지 넓혀서 본다.
    """
    for scope in scopes:
        if scope is None:
            continue
        try:
            found = find(scope, title_re, exclude_handle)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def find_in_process(pid: int | None, title_re: str):
    """해당 프로세스의 팝업을 찾는다. 없으면 None.

    최상위로 뜨는지 자식으로 뜨는지 환경마다 다르다 (로그인 팝업·파일 선택 창).

    ★ **싼 방법을 먼저 본다** (2026-09-11 실측으로 바꿨다).
      예전에는 `find_elements(top_level_only=False)` 하나만 썼다. 그런데 그것은
      **부모를 주지 않으면 데스크톱 전체 트리**를 훑고 나서 프로세스로 거른다.
      대상이 메모장처럼 작아도 **3.0초**가 들었고, ERPia 가 떠 있으면 훨씬 커진다.
      그 비용 때문에 "팝업 없음" 을 판정하는 데 165초가 걸린 실측이 있다.

      | 방법 | 실측 |
      | --- | --- |
      | Win32 최상위 창 스캔 | **0.7ms** |
      | `top_level_only=True` | 0.13초 |
      | `top_level_only=False` (예전) | 3.0초 |

      소유된 팝업도 최상위 창이라 Win32 스캔에 잡힌다.
    """
    if pid is None:
        return None

    import re as _re

    from pywinauto import backend as _backend
    from pywinauto.findwindows import find_elements

    from config.settings import SETTINGS
    from utils import winprobe

    pattern = _re.compile(title_re)
    for window in winprobe.top_windows(pid):
        if window.title and pattern.search(window.title):
            from pywinauto import Desktop

            return Desktop(backend=SETTINGS.backend).window(handle=window.handle)

    # 못 찾았다. 자식으로 떠서 EnumWindows 에 안 잡히는 경우를 위한 폴백.
    # **`top_level_only=True` 로 좁힌다.** False 는 데스크톱 전체를 훑는다.
    try:
        elements = find_elements(
            process=pid, top_level_only=True, control_type="Window",
            title_re=title_re, backend=SETTINGS.backend,
        )
    except Exception as exc:
        log.debug("프로세스 팝업 검색 실패: %s", exc)
        return None
    if not elements:
        return None

    wrap = _backend.registry.backends[SETTINGS.backend].generic_wrapper_class
    return wrap(elements[0])


def text_of(dialog) -> str:
    """팝업 본문. 하위 Text control을 이어 붙인다."""
    from utils import ui

    parts = []
    for item in ui.search(dialog, control_type="Text"):
        try:
            value = (item.window_text() or "").strip()
        except Exception:
            continue
        if value:
            parts.append(value)
    return " / ".join(parts)


def buttons_of(dialog) -> list[str]:
    from utils import ui

    names = []
    for item in ui.search(dialog, control_type="Button"):
        try:
            name = (item.window_text() or "").strip()
        except Exception:
            continue
        if name:
            names.append(name)
    return names


def click_button(dialog, title_re: str, what: str, dry_run: bool = False) -> str:
    """팝업의 버튼을 누른다."""
    from utils import ui

    matches = ui.search(dialog, title_re=title_re, control_type="Button")
    if not matches:
        raise DialogNotFound(
            f"{what} 버튼(title_re={title_re!r})을 찾지 못했다.\n"
            f"  팝업 안 버튼: {buttons_of(dialog) or '(없음)'}"
        )
    button = matches[0]
    return ui.click(button, f"팝업 [{button.window_text()}]", dry_run=dry_run)


def is_failure(text: str) -> bool:
    """본문이 실패/오류를 뜻하는지."""
    return bool(FAILURE_RE.search(text))


def _message_box(window):
    """떠 있는 ERPia 메시지 팝업 하나. 없으면 None. **기다리지 않는다.**"""
    from utils import ui

    found = ui.search(window, auto_id=MESSAGE_BOX_AUTO_ID, control_type="Window")
    return found[0] if found else None


def dismiss_message_box(window, timeout: float = 3.0) -> str | None:
    """ERPia 메시지 팝업이 떠 있고 **버튼이 [확인] 하나뿐이면** 누른다 (사용자 확정 2026-09-22).

    돌려주는 것: 닫은 팝업들의 `"제목: 본문"`. 팝업이 없거나, [예]/[아니오] 처럼 **고르는**
    팝업이거나, 눌러도 안 닫히면 None — 그때는 누르지 않은 것으로 보고 부르는 쪽이 원래대로 한다.
    제목줄 [닫기] 는 세지 않는다 (auto_id 가 빈 값. 내용 버튼은 `btn_확인(&O)` 꼴).
    """
    from utils import ui
    from utils.wait import WaitTimeout, wait_for

    if window is None:
        return None
    closed: list[str] = []
    for _ in range(MAX_CHAINED):
        popup = _message_box(window)
        if popup is None:
            break
        title = (popup.window_text() or "").strip()
        body = text_of(popup)
        text = ": ".join(part for part in (title, body) if part) or "(본문 없음)"
        buttons = [b for b in ui.search(popup, control_type="Button")
                   if b.element_info.automation_id]
        names = [b.window_text() for b in buttons]
        if len(buttons) != 1 or not OK_ONLY_RE.match(names[0] or ""):
            log.warning("ERPia 메시지 팝업 [%s] — 버튼이 %s 라 누르지 않는다.", text, names)
            return None
        handle = popup.handle
        ui.click(buttons[0], f"팝업 [{names[0]}]")
        try:
            wait_for(lambda: getattr(_message_box(window), "handle", None) != handle,
                     "메시지 팝업 닫힘", timeout=timeout)
        except WaitTimeout:
            log.warning("ERPia 메시지 팝업 [%s] 을 눌렀는데 닫히지 않았다.", text)
            return None
        log.warning("ERPia 메시지 팝업을 [%s] 로 닫았다 — %s", names[0], text)
        closed.append(text)
    return " / ".join(closed) or None
