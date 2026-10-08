r"""최상위 창을 **싸게** 찾는다. UIA 트리를 훑지 않는다.

## 왜 필요한가 (2026-09-11 실측)

"파일 선택 창이 떴나?" / "오류 팝업이 떴나?" 를 확인하는 데 UIA 로 **대상
프로세스의 트리 전체**를 훑고 있었다. ERPia 메인 창은 컨트롤이 2천 개가 넘어,
배터리 모드에서 **확인 한 번에 100초 넘게** 들었다.

그러면 상한이 무의미해진다. `wait_for` 는 확인을 한 번 시작하면 끝날 때까지
자르지 못한다. 상한 10초짜리 대기가 **125초** 걸린 실측이 있다.

| 구간 | 실측 (배터리) |
| --- | --- |
| [엑셀업로드] 클릭 → 파일창 등장 판정 | 125초 |
| 파일창 닫힘 → 업로드 오류 팝업 없음 판정 | 165초 |

찾는 대상이 전부 **최상위 창**이므로 트리를 훑을 이유가 없다.
Win32 `EnumWindows` 는 창 목록만 훑으므로 비용이 트리 크기와 무관하다.

## 무엇을 하지 않나

- UIA 를 쓰지 않는다. 여기서 얻는 것은 **핸들과 클래스·제목뿐**이다
- 조작하지 않는다. 읽기만 한다
- 자식 컨트롤은 보지 않는다. 그건 대상을 찾은 **뒤에** UIA 로 한다 — 예외는 `child_windows`(핸들·위치만,
  그리드를 덮는 로딩 표시를 보려고, 10-07)

소유된 팝업(owned popup)도 최상위 창이라 `EnumWindows` 에 나온다.
그래서 "부모 창의 자식으로 뜨는 팝업"도 여기서 잡힌다.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

from utils.logger import get_logger

log = get_logger(__name__)

_user32 = ctypes.windll.user32
_MAX_TITLE = 512

# SendMessageTimeout 플래그. 답이 올 때까지 막고, 앱이 먹통이면 포기한다.
_SMTO_BLOCK = 0x0001
_SMTO_ABORTIFHUNG = 0x0002
_WM_NULL = 0x0000

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass(frozen=True)
class Window:
    """최상위 창 하나. UIA 래퍼가 아니라 **값**이다."""

    handle: int
    class_name: str
    title: str
    pid: int
    visible: bool


def _title_of(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(_MAX_TITLE)
    _user32.GetWindowTextW(handle, buffer, _MAX_TITLE)
    return buffer.value


def _class_of(handle: int) -> str:
    buffer = ctypes.create_unicode_buffer(_MAX_TITLE)
    _user32.GetClassNameW(handle, buffer, _MAX_TITLE)
    return buffer.value


def rect_of(handle: int) -> tuple | None:
    """창의 화면 위치 `(left, top, right, bottom)`. 못 읽으면 None.

    **위치를 읽는 것은 권한 경계를 넘어도 된다.** 우리보다 높은 권한의 창도
    여기까지는 읽힌다 (UAC 동의 창으로 실측, 2026-09-17). 조작은 별개다.

    예외를 올리지 않는다. 사람에게 "어디에 떴다" 고 알려 주는 용도라,
    이것 때문에 흐름이 멈추면 안 된다.
    """
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _pid_of(handle: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return int(pid.value)


def top_windows(pid: int | None = None, visible_only: bool = True) -> list[Window]:
    """최상위 창 목록. `pid` 를 주면 그 프로세스 것만.

    실패해도 예외를 올리지 않는다. **읽지 못한 창은 빼고** 돌려준다 —
    이 함수가 막히면 그 위의 대기가 통째로 멈추기 때문이다.
    """
    found: list[Window] = []

    def collect(handle, _lparam):
        try:
            owner = _pid_of(handle)
            if pid is not None and owner != pid:
                return True
            visible = bool(_user32.IsWindowVisible(handle))
            if visible_only and not visible:
                return True
            found.append(Window(
                handle=int(handle),
                class_name=_class_of(handle),
                title=_title_of(handle),
                pid=owner,
                visible=visible,
            ))
        except Exception as exc:
            log.debug("창 정보를 읽지 못했다(건너뛴다): %s", type(exc).__name__)
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(collect), 0)
    except Exception as exc:
        log.debug("EnumWindows 실패: %s", type(exc).__name__)
    return found


def child_windows(handle: int) -> dict[int, tuple]:
    """그 창 아래의 **보이는** 자식 창 전부(손자까지) `{핸들: (left, top, right, bottom)}`.

    ERPia 는 [조회] 중 그리드와 같은 크기의 자식 창(로딩 표시)을 그리드 위에 띄운다 — Win32 로 본다 (10-07 실측,
    `docs/CONTROLS.md` "찾기(Ctrl+F) 창·조회 로딩 창". UIA 에 보이는지는 확인하지 않았다). 메인 창 아래 ~160개라 싸다.
    예외를 올리지 않는다 — 못 읽으면 빈 dict (위의 대기가 멈추지 않게).
    """
    found: dict[int, tuple] = {}
    if not handle:
        return found

    def collect(child, _lparam):
        try:
            if _user32.IsWindowVisible(child):
                rect = rect_of(child)
                if rect is not None:
                    found[int(child)] = rect
        except Exception as exc:
            log.debug("자식 창 정보를 읽지 못했다(건너뛴다): %s", type(exc).__name__)
        return True

    try:
        _user32.EnumChildWindows(wintypes.HWND(handle), _ENUM_PROC(collect), 0)
    except Exception as exc:
        log.debug("EnumChildWindows 실패: %s", type(exc).__name__)
    return found


def new_window(pid: int, before: set, title: str) -> int:
    """기준선(before 핸들 집합)에 없던, 제목이 `title` 인 이 프로세스의 최상위 창. 없으면 0."""
    return next((w.handle for w in top_windows(pid) if w.title == title and w.handle not in before), 0)


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def focus_handle(handle: int) -> int:
    """그 창의 UI 스레드에서 지금 키보드 포커스를 가진 창. 못 읽으면 0.

    키를 보내기 전 '포커스가 정말 그 컨트롤에 있나' 를 본다 (10-07 — 찾기는 포커스가 있는 그리드에서 열린다).
    """
    try:
        thread = _user32.GetWindowThreadProcessId(wintypes.HWND(handle), None)
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        if not thread or not _user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            return 0
        return int(info.hwndFocus or 0)
    except Exception as exc:
        log.debug("포커스 창을 읽지 못했다: %s", type(exc).__name__)
        return 0


def is_within(child: int, ancestor: int) -> bool:
    """child 가 ancestor 자신이거나 그 아래 창인가."""
    if not child or not ancestor:
        return False
    return child == ancestor or bool(_user32.IsChild(wintypes.HWND(ancestor), wintypes.HWND(child)))


def ui_roundtrip(handle: int, timeout: float = 1.0) -> float:
    """UI 스레드에 `WM_NULL` 을 보내고 답을 받는 데 걸린 시간(초) = 대상 UI 스레드가 얼마나 밀려 있나.

    ERPia 조회 완료를 알리는 UIA 신호가 없어서 쓴다 (유휴 ~0.1ms / 조회 직후 ~6ms, 호출 비용 ~0.1ms.
    `IsHungAppWindow` 는 5초 넘게 먹통이어야 참이라 못 쓴다). 조회가 백그라운드 스레드면 한가해 보이므로
    **단독 판정에 쓰지 않는다** — "한가함" 조건 하나로만(바쁘면 계속 대기, 한가해도 유예).
    답이 없으면(먹통·창 사라짐) `timeout` 을 돌려주고 예외를 올리지 않는다 (위의 대기가 멈추지 않게).
    """
    started = time.perf_counter()
    result = wintypes.DWORD()
    try:
        ok = _user32.SendMessageTimeoutW(
            wintypes.HWND(handle), _WM_NULL, 0, 0,
            _SMTO_BLOCK | _SMTO_ABORTIFHUNG, int(timeout * 1000),
            ctypes.byref(result))
    except Exception as exc:
        log.debug("SendMessageTimeout 실패: %s", type(exc).__name__)
        return timeout
    if not ok:
        return timeout
    return time.perf_counter() - started


def main_window_handle(pid: int) -> int:
    """그 프로세스의 **제목이 있는** 최상위 창 핸들. 없으면 0.

    제목 없는 최상위 창은 WinForms 가 만드는 보조 창일 때가 많아 뒤로 민다.
    """
    windows = top_windows(pid)
    if not windows:
        return 0
    titled = [window for window in windows if window.title]
    return (titled or windows)[0].handle
