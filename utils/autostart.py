r"""PC 에 로그온하면 켜고, 꺼져 있으면 5분 안에 다시 켠다 (09-28, 사용자 확정). **시스템을 바꾼다.**

| 무엇 | 어떻게 | 왜 |
| --- | --- | --- |
| 로그온 때 켜기 | 사용자 레지스트리 `HKCU\...\Run` | 관리자 권한이 필요 없다 |
| 5분마다 "안 떠 있으면 켜기" | 작업 스케줄러, **XML 로** 등록 | `schtasks /sc MINUTE` 기본값은 72시간 뒤 강제 종료·배터리면 시작 안 함이다 |

둘 다 `--background` 로 띄운다 — 최소화로 뜬다. 이미 떠 있으면 새로 뜬 쪽이 곧바로 끝난다
(`utils/instance.py`). 이름은 exe 경로로 정해 같은 PC 의 다른 빌드와 겹치지 않는다.

★ **로그온 때 켜진 RPA 는 작업에 넘긴다** (09-29, `hand_over`). 작업이 띄운 프로그램이 떠 있는 동안에는 5분
  트리거가 와도 스케줄러가 아무것도 띄우지 않는다(IgnoreNew). Run 값으로 켜진 것은 작업이 몰라서 5분마다 exe 가
  떠 **153MB 를 풀고** "이미 떠 있다" 로 끝났다(onefile). 작업은 `--task` 를 붙여 띄운다.
  사람이 두 번 누른 것은 넘기지 않는다 — 두 번 풀어 창이 늦어진다 (`main_run.py`).
★ 작업이 띄운 프로세스는 기본 우선순위가 '낮음'(7)이라 `<Priority>5</Priority>`(보통)로 둔다.

★ 사람이 로그온한 뒤에만 돈다. 꺼진 PC·로그온 전 화면에서는 켤 수 없다 (UI 자동화에 바탕화면이 필요하다).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import winreg
from pathlib import Path
from xml.sax.saxutils import escape

from utils.logger import get_logger

log = get_logger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKGROUND = "--background"
FROM_TASK = "--task"                         # 작업이 띄웠다 — 다시 넘기지 않는다
TASK_ARGS = f"{BACKGROUND} {FROM_TASK}"      # RPA 작업의 인자. Run 값은 BACKGROUND 만
CHECK_MINUTES = 5

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>주문 자동화 — 꺼져 있으면 다시 켠다 ({minutes}분마다)</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <StartWhenAvailable>false</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{exe}</Command><Arguments>{args}</Arguments><WorkingDirectory>{folder}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def name_for(exe: Path) -> str:
    return "RPA_" + hashlib.sha256(str(exe).lower().encode("utf-8")).hexdigest()[:10]


def task_xml(exe: Path, args: str = TASK_ARGS, folder: Path | None = None) -> str:
    return TASK_XML.format(minutes=CHECK_MINUTES, exe=escape(str(exe)), args=escape(args),
                           folder=escape(str(folder or exe.parent)))


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          encoding="cp949", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def enabled(exe: Path, name: str | None = None, task_args: str | None = None) -> bool:
    """둘 다 등록돼 있나. `task_args` 를 주면 작업이 그 인자로 띄우는지도 본다 — 옛 판 작업을 다시 건다."""
    name = name or name_for(exe)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
    except OSError:
        return False
    if task_args is None:
        return _schtasks("/query", "/tn", name).returncode == 0
    return _has_args(_schtasks("/query", "/tn", name, "/xml"), task_args)


def _has_args(query: subprocess.CompletedProcess, args: str) -> bool:
    return query.returncode == 0 and f"<Arguments>{escape(args)}</Arguments>" in (query.stdout or "")


def enable(exe: Path, args: str = BACKGROUND, folder: Path | None = None, name: str | None = None,
           task_args: str | None = None) -> None:
    """등록한다. 실패하면 `OSError` — 창이 사람에게 알린다.

    Run 값은 `args`, 작업은 `task_args`(없으면 `args`). RPA 는 `task_args=TASK_ARGS` 를 넘긴다.
    인자·폴더·이름은 LLM 워커(`pythonw llm\\worker.py`)가 쓴다 — 워커는 넘기기를 하지 않는다.
    """
    name = name or name_for(exe)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        # Run 값에는 작업 폴더가 없다 — 워커는 스크립트 경로를 인자로 넘겨 폴더와 무관하게 뜬다
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, f'"{exe}" {args}')
    # schtasks /xml 은 UTF-16 파일만 받는다
    handle, path = tempfile.mkstemp(suffix=".xml")
    os.close(handle)
    try:
        Path(path).write_text(task_xml(exe, task_args or args, folder), encoding="utf-16")
        done = _schtasks("/create", "/tn", name, "/xml", path, "/f")
    finally:
        Path(path).unlink(missing_ok=True)
    if done.returncode != 0:
        raise OSError(f"작업 스케줄러 등록 실패: {(done.stderr or done.stdout or '').strip()[:200]}")
    log.info("자동 켜기 등록 — 로그온 때 + %d분마다 (%s)", CHECK_MINUTES, name)


def disable(exe: Path, name: str | None = None) -> None:
    """지운다. 원래 없었어도 조용히 끝난다."""
    name = name or name_for(exe)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        log.debug("로그온 켜기는 원래 등록돼 있지 않았다 (%s)", name)
    _schtasks("/delete", "/tn", name, "/f")
    log.info("자동 켜기 해제 (%s)", name)


def hand_over(exe: Path) -> bool:
    """RPA 를 작업 스케줄러가 띄우게 넘긴다 (09-29). 넘겼으면 True — 부른 쪽은 곧바로 끝난다.

    지금 판의 작업(`TASK_ARGS`)이 아니거나 꺼져 있으면 넘기지 않는다 — 옛 작업은 `--task` 없이 띄워서
    넘겨받은 쪽이 또 넘기려 든다.
    """
    name = name_for(exe)
    query = _schtasks("/query", "/tn", name, "/xml")
    if not _has_args(query, TASK_ARGS) or "<Enabled>false</Enabled>" in query.stdout:
        return False
    return _schtasks("/run", "/tn", name).returncode == 0
