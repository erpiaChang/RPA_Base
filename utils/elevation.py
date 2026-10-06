r"""프로세스 권한(Integrity Level) 확인.

Windows UIPI 규칙상 **낮은 권한 프로세스는 높은 권한 프로세스의 UI를
열거하거나 조작할 수 없다.** 창이 없는 것처럼 보일 뿐 오류도 나지 않는다.

ERPia를 관리자 권한으로 띄워 쓰는 환경이 있으므로, 자동화를 시작하기 전에
권한이 맞는지 확인하고 안 맞으면 명확히 알린다.

근거: docs/UI_SURVEY.md "권한(Integrity Level) 문제" (2026-09-03)
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from utils.logger import get_logger
from utils.process import PROCESS_QUERY_LIMITED_INFORMATION

log = get_logger(__name__)

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


class ElevationMismatch(RuntimeError):
    """대상 프로그램이 더 높은 권한으로 실행 중이라 조작할 수 없다."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__(
            f"대상 프로그램(pid={pid})이 관리자 권한으로 실행 중인데\n"
            "RPA는 일반 권한으로 실행 중입니다. 이 상태로는 창을 찾을 수 없습니다.\n"
            "\n"
            "해결: RPA를 관리자 권한으로 다시 실행하세요 (exe 우클릭 → 관리자 권한으로 실행).\n"
            "(또는 ERPia를 일반 권한으로 실행하거나, RPA가 직접 띄운 인스턴스만 사용)"
        )


def is_elevated(pid: int | None = None) -> bool | None:
    """프로세스가 관리자 권한인지. 확인 불가면 None.

    pid를 생략하면 현재 프로세스를 본다.
    """
    pid = os.getpid() if pid is None else pid

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # 핸들조차 못 여는 것은 보통 상대가 더 높은 권한이라는 뜻이다.
        log.debug("OpenProcess 실패 (pid=%s, err=%s)", pid, ctypes.get_last_error())
        return None

    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            log.debug("OpenProcessToken 실패 (pid=%s, err=%s)", pid, ctypes.get_last_error())
            return None
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(
            token, TOKEN_ELEVATION, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)
        )
        if not ok:
            log.debug("GetTokenInformation 실패 (pid=%s, err=%s)", pid, ctypes.get_last_error())
            return None
        return bool(elevation.value)
    finally:
        if token:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(handle)


def check_target(pid: int) -> None:
    """대상 프로세스를 조작할 수 있는 권한인지 확인한다.

    대상이 관리자 권한이고 우리가 아니면 `ElevationMismatch`.
    확인 불가(핸들을 못 여는 경우)도 같은 이유일 가능성이 높으므로 동일하게 본다.
    """
    mine = is_elevated()
    theirs = is_elevated(pid)

    log.info("권한 확인: RPA=%s / 대상(pid=%s)=%s", _label(mine), pid, _label(theirs))

    if mine:
        return  # 관리자면 양쪽 다 조작 가능하다
    if theirs is True or theirs is None:
        raise ElevationMismatch(pid)


def _label(value: bool | None) -> str:
    if value is None:
        return "확인 불가"
    return "관리자" if value else "일반"

