r"""프로세스 조회.

EXE 경로로 실행 중인 프로세스를 찾는다. 중복 실행 가능 여부 판정에 쓴다.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from pywinauto.application import process_get_modules

from utils.logger import get_logger

log = get_logger(__name__)


def pids_for_exe(exe: str) -> list[int]:
    """해당 EXE로 실행 중인 프로세스의 PID 목록.

    권한이 없어 모듈 경로를 읽지 못하는 프로세스는 건너뛴다.
    """
    target = os.path.normcase(os.path.abspath(exe))
    pids = []
    for entry in process_get_modules():
        pid, module = entry[0], entry[1]
        if not module:
            continue
        try:
            if os.path.normcase(module) == target:
                pids.append(pid)
        except (OSError, ValueError) as exc:
            log.debug("모듈 경로 비교 실패 (pid=%s): %s", pid, exc)
    return sorted(pids)


# --- 이름만으로 찾기 ----------------------------------------------------
# `pids_for_exe` 는 **프로세스를 열어** 모듈 경로를 읽는다. 그래서 우리보다
# 권한이 높은 프로세스는 통째로 빠진다. 실측(2026-09-17): UAC 동의 창
# (`consent.exe`, SYSTEM 권한)이 화면에 떠 있는데도 목록에 나오지 않았다.
#
# 이름만 필요할 때는 프로세스 목록 스냅샷을 쓴다. **대상을 열지 않으므로
# 권한 경계를 넘지 않는다.**
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * MAX_PATH),
    ]


def pids_by_name(name: str) -> list[int]:
    """실행 파일 **이름**이 같은 프로세스의 PID 목록.

    경로는 보지 않는다. 그러니 같은 이름의 다른 프로그램을 가려낼 수 없다 —
    Windows 자신의 프로세스처럼 **이름이 고정인 것에만** 쓴다.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, ctypes.c_void_p(-1).value):
        log.debug("프로세스 목록을 얻지 못했다: %s", ctypes.get_last_error())
        return []
    out: list[int] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            exe = entry.szExeFile.decode("mbcs", errors="replace")
            if exe.lower() == name.lower():
                out.append(int(entry.th32ProcessID))
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return sorted(out)


# 프로세스 생성 시각 조회용. 이름만 알면 되는 값이라 상수로 둔다.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def create_time(pid: int) -> int | None:
    """프로세스 생성 시각(FILETIME, 100ns 단위). 읽지 못하면 None.

    psutil을 새로 설치하지 않기 위해 Win32 GetProcessTimes 를 직접 부른다.
    권한이 부족하면(대상이 관리자 권한이고 우리가 아니면) None 이 나온다.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        log.debug("OpenProcess 실패 (pid=%s): %s", pid, ctypes.get_last_error())
        return None
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        )
        if not ok:
            log.debug("GetProcessTimes 실패 (pid=%s): %s", pid, ctypes.get_last_error())
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def created_epoch(pid: int) -> float | None:
    """생성 시각을 `time.time()` 과 같은 초로. 읽지 못하면 None."""
    filetime = create_time(pid)
    return None if filetime is None else (filetime - 116444736000000000) / 1e7


def newest_pid(pids: list[int]) -> int | None:
    """가장 최근에 실행된 PID. 생성 시각을 하나도 읽지 못하면 None.

    PID 크기로 대신 판단하지 않는다. PID는 재사용되므로 순서를 보장하지 않는다.
    """
    timed = [(t, pid) for pid in pids if (t := create_time(pid)) is not None]
    if not timed:
        return None
    return max(timed)[1]


def screen_locked() -> bool:
    """화면이 잠겨 있는지. 판정할 수 없으면 False.

    잠금 화면에서는 입력이 대상 프로그램에 닿지 않는다 (`click_input()` 은 예외 없이 넘어가고 안 눌린다).
    둘 중 하나면 잠김: ① 입력 데스크톱이 안 열린다 ② 맨 앞 창이 `LockApp.exe` 의 것이다.
    ②가 필요한 이유 — 잠금 화면이 떠 있는데 입력 데스크톱이 열리는 상태가 있다 (09-17 실측).
    제목은 언어마다 달라 **프로세스 이름**으로 가린다.
    """
    user32 = ctypes.windll.user32
    DESKTOP_SWITCHDESKTOP = 0x0100
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    user32.CloseDesktop(handle)
    return lock_app_in_front()


# 잠금 화면을 그리는 Windows 프로세스. 이름이 고정이라 상수로 둔다.
LOCK_APP_EXE = "LockApp.exe"


def lock_app_in_front() -> bool:
    """맨 앞 창이 잠금 화면 앱의 것인지. 판정할 수 없으면 False.

    `LockApp.exe` 는 평소에도 **멈춘 채로 떠 있다.** 떠 있다는 것만으로는
    잠긴 것이 아니다 — 그래서 **맨 앞 창의 주인**인지를 본다.
    """
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    return bool(pid.value) and pid.value in pids_by_name(LOCK_APP_EXE)
