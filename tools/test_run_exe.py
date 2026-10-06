r"""[조작 도구] **빌드본(exe)으로 전체 흐름을 완주시킨다.** 실계정이 돈다.

    .venv\Scripts\python.exe -m tools.test_run_exe            실행용 빌드
    .venv\Scripts\python.exe -m tools.test_run_exe --show     띄우기만 한다

## 왜 이 도구가 있나 (2026-09-17)

`exe 로 전체 흐름 완주` 가 오래 미검증으로 남아 있었다. 런처가 뜨는 것만
확인하고, 업무 흐름은 **개발 폴더에서 `python -m`** 으로만 돌렸기 때문이다.

그 둘은 같지 않다. exe 에서만 다른 것이 있다.

| exe 에서만 다른 것 | 왜 |
| --- | --- |
| 구운 설정 (`baked:` 비밀번호) | 개발 폴더는 `dpapi:` 를 쓴다. **푸는 코드가 다르다** |
| 로그·설정 파일 자리 | exe 옆이다. 개발 폴더가 아니다 |
| 번들에 안 들어간 모듈 | `import` 가 exe 안에서만 깨진다 |
| 콘솔 없음 (`windowed`) | `sys.stdout` 이 None 이다 |

## ★ [실행] 은 도구가 누를 수 없다 — **tkinter 위젯은 UIA 에 없다**

처음에 [실행] 을 UIA 로 눌러 보려고 만들었다가 알았다 (2026-09-17 실측).
빌드본 창(`TkTopLevel`)의 단추를 열거하면 **`최소화` / `최대화` / `닫기`
세 개뿐**이다. tkinter 는 위젯을 자기가 그리고 자식 창(HWND)을 만들지 않아서,
[실행] 은 UIA 트리에 아예 없다.

`tools/probe_gui.py` 가 우리 창을 UIA 로 훑지 않고 **코드 안에서 만들어**
보는 이유가 이것이다.

그래서 이 도구는 **사람이 [실행] 을 누르고, 도구가 따라간다.** 실계정
저장이 걸린 단추라 오히려 이 편이 맞다.

## 무엇을 하나

1. exe 를 띄운다 (이미 떠 있으면 그 창을 쓴다)
2. 사람에게 **[실행] 을 누르라고 알린다**
3. 끝날 때까지 exe 가 남기는 로그를 그대로 흘려 보여 준다
4. 리포트 파일이 생기면 완주로 본다

`--show` 는 띄우기만 하고 따라가지 않는다.

★ 2차 인증이 필요하다. [휴대폰 연결] 이 준비돼 있어야 한다
  (`tools.test_collect --check` 로 먼저 본다).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from utils.logger import get_logger, setup_logging  # noqa: E402
from utils.wait import WaitTimeout, wait_for  # noqa: E402

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 빌드 산출물. 경로를 바꾸려면 `build_run.spec` 과 함께 본다.
BUILDS = {
    "run": PROJECT_ROOT / "dist" / "run" / "RPA_1.exe",
}

WINDOW_TITLES = {"run": "주문 자동화"}
RUN_BUTTON = "실행"

WINDOW_TIMEOUT = 60.0        # 창이 뜨기를 기다리는 상한
FLOW_TIMEOUT = 1800.0        # 완주를 기다리는 상한. 2차 인증에 사람 손이 든다
POLL = 1.0


def find_window(title: str):
    """제목으로 우리 창을 찾는다. 없으면 None."""
    from utils import winprobe

    for window in winprobe.top_windows():
        if window.title == title:
            return window
    return None


def visible_buttons(handle: int) -> list[str]:
    """창 안에서 UIA 로 보이는 단추 이름들.

    ★ tkinter 창에서는 **창틀 단추만** 나온다(`최소화`/`최대화`/`닫기`).
      [실행] 을 찾는 데 쓰는 것이 아니라, 그렇다는 것을 남기는 데 쓴다.
    """
    from pywinauto import Desktop

    from config.settings import SETTINGS

    window = Desktop(backend=SETTINGS.backend).window(handle=handle)
    return [(b.window_text() or "").strip()
            for b in window.descendants(control_type="Button")]


def tail(path: Path, offset: int) -> int:
    """로그 파일의 새 부분만 그대로 흘려 보여 준다. 새 위치를 돌려준다."""
    if not path.exists():
        return offset
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        for line in handle:
            print("  |", line.rstrip(), flush=True)
        return handle.tell()


def newest(paths: list[Path]) -> Path | None:
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def main() -> int:
    parser = argparse.ArgumentParser(description="빌드본으로 전체 흐름 완주")
    parser.add_argument("--build", choices=sorted(BUILDS), default="run",
                        help="어느 빌드로 돌릴지 (기본: 실행용)")
    parser.add_argument("--show", action="store_true",
                        help="띄우기만 한다. [실행] 을 누르지 않는다")
    args = parser.parse_args()
    setup_logging()

    exe = BUILDS[args.build]
    if not exe.exists():
        log.error("빌드본이 없다: %s — 먼저 빌드할 것.", exe)
        return 1
    log_dir = exe.parent / "logs"
    before = set(log_dir.glob("report_*.html")) if log_dir.exists() else set()

    title = WINDOW_TITLES[args.build]
    if find_window(title) is not None:
        log.info("이미 떠 있는 창을 쓴다: %r", title)
    else:
        log.info("빌드본을 띄운다: %s", exe)
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        wait_for(lambda: find_window(title) is not None, f"창 {title!r} 등장",
                 timeout=WINDOW_TIMEOUT, interval=0.5)
    except WaitTimeout:
        log.error("창이 %.0f초 안에 뜨지 않았다. exe 옆 logs\\ 를 볼 것.",
                  WINDOW_TIMEOUT)
        return 1
    window = find_window(title)
    log.info("창 확인: %r handle=%s", window.title, window.handle)

    log.info("UIA 로 보이는 단추: %s  ← tkinter 라 창틀뿐이다",
             visible_buttons(window.handle))
    if args.show:
        log.info("띄우기만 했다. 따라가지 않는다.")
        return 0

    log.warning("★ 화면의 [%s] 을(를) 눌러 주세요. 도구는 누를 수 없다 "
                "(tkinter 위젯은 UIA 에 없다). 누르면 **실계정이 돈다** — "
                "물류대기·물류관리에서 실제로 저장한다.", RUN_BUTTON)
    log.info("누르기를 기다리며 exe 로그를 따라간다. 최대 %.0f분.",
             FLOW_TIMEOUT / 60)

    # exe 는 자기 옆에 로그를 남긴다. 그 파일을 그대로 흘려 보여 준다.
    deadline = time.monotonic() + FLOW_TIMEOUT
    offset, watched = 0, None
    while time.monotonic() < deadline:
        logs = sorted(log_dir.glob("rpa_*.log")) if log_dir.exists() else []
        current = newest(logs)
        if current is not None and current != watched:
            watched, offset = current, 0
            log.info("exe 로그를 따라간다: %s", current)
        if watched is not None:
            offset = tail(watched, offset)
        reports = set(log_dir.glob("report_*.html")) - before
        if reports:
            if watched is not None:
                tail(watched, offset)
            log.info("완주했다. 리포트: %s", sorted(reports)[-1])
            return 0
        if find_window(title) is None:
            log.warning("창이 사라졌다. 사람이 닫았거나 죽었다.")
            return 1
        # 다음 확인까지 쉰다. 여기서 볼 조건은 **위에서 이미 다 봤다** —
        # 로그가 자라기를 기다리는 것이라 조건이 따로 없다.
        try:
            wait_for(lambda: False, "다음 확인", timeout=POLL, interval=POLL)
        except WaitTimeout:
            pass    # 기다리는 것이 목적이다. 시간이 다 되는 것이 정상이다

    log.error("%.0f분 안에 끝나지 않았다. 화면을 확인할 것.", FLOW_TIMEOUT / 60)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
