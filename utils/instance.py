r"""이 프로그램이 **한 벌만** 뜨게 한다 (09-28).

자동 켜기(`utils/autostart.py`)가 5분마다 exe 를 띄운다. 이미 떠 있으면 새로 뜬 쪽이 곧바로 끝나야 한다 —
두 벌이 같은 예약을 따로 돌리면 같은 ERPia 계정으로 동시에 로그인한다.

이름 있는 뮤텍스를 쓴다. 이름은 **exe 경로**로 정한다 — 같은 PC 에 다른 빌드(다른 폴더)는 따로 떠도 된다
(사용자 확정 09-28). 프로세스가 끝나면 Windows 가 뮤텍스를 거둔다 (죽어도 남지 않는다).
"""
from __future__ import annotations

import ctypes
import hashlib
import sys
from pathlib import Path

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5     # 다른 권한(관리자)으로 뜬 벌의 뮤텍스는 열 수 없다 — 떠 있다는 뜻 (10-07)
_handle = None      # 프로세스가 끝날 때까지 쥐고 있는다. 놓으면 다음 벌이 뜬다


def exe_path() -> Path:
    """지금 프로그램의 경로 — 빌드본이면 exe, 개발이면 진입 스크립트."""
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()


def mutex_name(path: Path) -> str:
    digest = hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\RPA_{digest}"


def acquire(path: Path | None = None) -> bool:
    """처음 뜬 것이면 True. 이미 같은 경로의 프로그램이 떠 있으면 False."""
    global _handle
    # use_last_error — 호출 직후의 오류값을 ctypes 가 붙잡아 둔다 (따로 GetLastError 를 부르면 그 사이 바뀔 수 있다)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)            # 앞선 호출의 오류값으로 오판하지 않게
    handle = kernel32.CreateMutexW(None, False, mutex_name(path or exe_path()))
    if is_second(handle, ctypes.get_last_error()):
        if handle:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    if not handle:
        return True                     # 그 밖의 이유로 만들지 못했다 — 막을 근거가 없으니 뜬다
    _handle = handle
    return True


def is_second(handle, error: int) -> bool:
    """이미 같은 경로의 프로그램이 떠 있나. 만들지 못했는데 접근 거부면 다른 권한의 벌이 쥐고 있다."""
    return error == (ERROR_ALREADY_EXISTS if handle else ERROR_ACCESS_DENIED)


def release() -> None:
    """쥔 것을 놓는다 — 작업 스케줄러에 넘길 때 넘겨받는 쪽이 잡게 (`main_run.py`, 09-29)."""
    global _handle
    if _handle:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_handle))
        _handle = None


def tell_already_running() -> None:
    """사람이 두 번 눌렀을 때 — 아무 일도 없는 것처럼 보이지 않게 알린다 (tk 없이)."""
    ctypes.windll.user32.MessageBoxW(
        None, "이미 켜져 있습니다. 작업 표시줄에서 [주문 자동화] 창을 여세요.", "주문 자동화", 0x40)
