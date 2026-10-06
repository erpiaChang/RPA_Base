r"""[조작 도구] **Windows 작업 스케줄러에 등록/해제한다.** 시스템을 바꾼다.

    .venv\Scripts\python.exe -m tools.test_autorun_task --show       지금 상태만 본다
    .venv\Scripts\python.exe -m tools.test_autorun_task --register <exe 경로>
    .venv\Scripts\python.exe -m tools.test_autorun_task --remove

★ **`--show` 말고는 시스템을 바꾼다.** 등록하면 다음 로그인부터 프로그램이
  저절로 뜬다. 사용자 승인 없이 돌리지 않는다.

## 왜 필요한가 (자동 실행 2단)

`utils/autorun.py` 의 예약 시계는 **프로그램 창이 떠 있는 동안만** 돈다.
창을 닫거나 PC 를 재부팅하면 예약도 함께 사라진다. 이 도구가 그 구멍을 메운다 —
작업 스케줄러에 "로그인하면 띄워라" 를 등록해 두면 시계가 되살아난다.

## ★ 부팅이 아니라 **로그인**이다

| 방식 | 언제 도나 | 쓸 수 있나 |
| --- | --- | --- |
| `/sc ONSTART` (부팅) | 아무도 로그인하기 전 | **못 쓴다.** 화면이 없어 UI 자동화가 전부 실패한다 |
| `/sc ONLOGON` (로그인) | 사람이 로그인한 뒤 | **이것을 쓴다.** 바탕화면이 있어야 클릭이 통한다 |

그래서 자동 로그인을 걸어 둔 PC 라면 "전원을 켜면 알아서" 가 되고, 비밀번호를
넣어야 하는 PC 라면 **누군가 로그인해 주어야** 그때부터 예약이 돈다.
이건 우회할 수 없다 (`docs/SETTINGS.md` 6번의 잠긴 화면 설명과 같은 이유다).

## 권한

`/sc ONLOGON` 은 **자기 계정에 대해서는 관리자 권한 없이** 등록된다.
`/ru` 로 남의 계정을 지정하면 권한이 필요하다 — 그래서 지정하지 않는다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

# 작업 이름. 바꾸면 예전 등록이 남으므로 함부로 바꾸지 않는다.
TASK_NAME = "RPA_AutoRun_OnLogon"

# `schtasks` 가 "그런 작업 없다" 로 답할 때의 종료 코드.
NOT_FOUND = 1


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """`schtasks` 를 부른다. **출력은 그대로 보여 준다** — 숨기면 원인을 못 찾는다."""
    log.info("실행: schtasks %s", " ".join(args))
    return subprocess.run(["schtasks", *args], capture_output=True,
                          text=True, encoding="cp949", errors="replace")


def show() -> int:
    """지금 등록돼 있는지 본다. **아무것도 바꾸지 않는다.**"""
    done = _run(["/query", "/tn", TASK_NAME, "/fo", "LIST"])
    if done.returncode != 0:
        log.info("등록돼 있지 않다 (%s)", TASK_NAME)
        log.info("등록하려면: --register <exe 경로>")
        return 0
    for line in (done.stdout or "").splitlines():
        if line.strip():
            log.info("  %s", line.rstrip())
    return 0


def register(exe: Path) -> int:
    """로그인할 때 이 exe 를 띄우도록 등록한다. **시스템을 바꾼다.**"""
    if not exe.is_file():
        log.error("실행 파일이 없다: %s", exe)
        log.error("빌드부터 한다: build_tool.bat")
        return 1
    if exe.suffix.lower() != ".exe":
        log.error("exe 가 아니다: %s. 배포본을 지정한다.", exe)
        return 1

    done = _run(["/create", "/tn", TASK_NAME, "/tr", f'"{exe}"',
                 "/sc", "ONLOGON", "/f"])
    if done.returncode != 0:
        log.error("등록하지 못했다 (코드 %d)", done.returncode)
        log.error("%s", (done.stderr or done.stdout or "").strip())
        return 1
    log.info("등록했다: %s -> %s", TASK_NAME, exe)
    log.info("★ **다음 로그인부터** 저절로 뜬다. 지금 바로 뜨지는 않는다.")
    log.info("★ 프로그램이 뜨는 것과 **예약이 도는 것은 다르다.** 화면의 "
             "[자동 실행] 을 켜고 시각을 넣어야 실제로 돈다 (docs/SETTINGS.md 6번).")
    return 0


def remove() -> int:
    """등록을 지운다. **시스템을 바꾼다.**"""
    done = _run(["/delete", "/tn", TASK_NAME, "/f"])
    if done.returncode != 0:
        log.warning("지우지 못했다 (코드 %d). 원래 없었을 수 있다.",
                    done.returncode)
        log.warning("%s", (done.stderr or done.stdout or "").strip())
        return 0
    log.info("지웠다: %s", TASK_NAME)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="로그인 시 자동 시작 등록 (조작 도구)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true",
                       help="지금 상태만 본다 (아무것도 바꾸지 않는다)")
    group.add_argument("--register", metavar="EXE",
                       help="로그인 시 띄우도록 등록한다 (시스템을 바꾼다)")
    group.add_argument("--remove", action="store_true",
                       help="등록을 지운다 (시스템을 바꾼다)")
    args = parser.parse_args()

    setup_logging()
    if args.show:
        return show()
    if args.remove:
        return remove()
    return register(Path(args.register).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
