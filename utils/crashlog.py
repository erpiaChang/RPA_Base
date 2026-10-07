r"""처리 안 된 예외를 logs 에 남기고 알린다 (10-07).

exe 는 콘솔이 없어 stderr 가 None 이다 (`utils/logger.py`). 이것이 없으면 Tk 콜백·스레드의 예외가 흔적 없이 사라지고,
켜는 중 import 가 실패하면 "눌렀는데 아무것도 안 뜬다" 가 된다.

★ 표준 라이브러리만 쓴다 — 설정이 깨져 켜지지 못한 때도 남겨야 한다 (`config.settings` 를 부르지 않는다).
  `utils.cancel` 도 부르지 않는다 (그것이 config 를 끌고 온다) — 이미 올라와 있을 때만 `Cancelled` 를 본다.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

CRASH_TEXT = ("예상하지 못한 문제가 생겼습니다. 같은 일이 되풀이되면 프로그램을 다시 켜 주세요. "
              "계속되면 logs 폴더의 crash 로 시작하는 파일을 관리자에게 보내 주세요.")
START_FAIL_TEXT = ("프로그램을 켜지 못했습니다. 다시 켜 보고, 계속되면 logs 폴더의 crash 로 시작하는 파일을 "
                   "관리자에게 보내 주세요.")
MAX_NOTICES = 3                 # 알림은 프로세스당 이만큼만. 같은 오류는 한 번
BACKGROUND = "--background"     # 자동 켜기로 뜬 것 — 사람이 없다. 알림 창을 띄우면 안 닫힌 채 남는다

_log_dir: Path | None = None
_installed = False
_notifier = None                # 글 하나를 받아 GUI 스레드에서 보여 주는 함수 (`set_notifier`)
_seen: dict[tuple, int] = {}
_notified = 0
_lock = threading.Lock()


def _default_dir() -> Path:
    # `config.settings._project_root` 와 같은 규칙 (exe 옆 / 개발 폴더) — 그 모듈을 부르지 않으려고 되풀이한다
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return Path(__file__).resolve().parent.parent / "logs"


def install(log_dir: Path | None = None) -> None:
    """훅을 건다. 여러 번 불러도 한 번만."""
    global _installed, _log_dir
    _log_dir = log_dir or _default_dir()
    if _installed:
        return
    sys.excepthook = lambda t, e, tb: report(t, e, tb, "main")
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback,
                                            f"thread:{getattr(a.thread, 'name', '?')}")
    _installed = True


def set_notifier(notifier) -> None:
    """창이 만들어지면 알림 함수를 건다 (None 이면 뗀다). 어느 스레드에서 불러도 안전해야 한다."""
    global _notifier
    _notifier = notifier


def tk_handler(exc_type, exc, tb) -> None:
    """`root.report_callback_exception = crashlog.tk_handler` — Tk 콜백의 예외 (기본값은 보이지 않는 stderr)."""
    report(exc_type, exc, tb, "tk")


def _ignored(exc_type) -> bool:
    if issubclass(exc_type, (SystemExit, KeyboardInterrupt, GeneratorExit)):
        return True
    cancelled = getattr(sys.modules.get("utils.cancel"), "Cancelled", None)   # BaseException — 사람이 [중단]
    return cancelled is not None and issubclass(exc_type, cancelled)


def report(exc_type, exc, tb, where: str) -> None:
    """파일에 남기고(같은 오류는 두 번째부터 한 줄), 처음 보는 오류면 알린다 (`MAX_NOTICES` 까지)."""
    if _ignored(exc_type):
        return
    global _notified
    frames = traceback.extract_tb(tb)
    key = (exc_type.__name__, str(exc)[:200], f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "")
    with _lock:
        _seen[key] = count = _seen.get(key, 0) + 1
        notify = count == 1 and _notified < MAX_NOTICES
        if notify:
            _notified += 1
    text = ("".join(traceback.format_exception(exc_type, exc, tb)) if count == 1
            else f"(같은 오류 {count}번째) {exc_type.__name__}: {exc}\n")
    try:
        folder = _log_dir or _default_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"crash_{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%H:%M:%S}] 처리 안 된 예외 ({where})\n{text}\n")
    except OSError as write_exc:
        logging.getLogger(__name__).warning("crash 기록을 쓰지 못했다: %s", write_exc)
    if count == 1:
        logging.getLogger("crash").error("처리 안 된 예외 (%s)\n%s", where, text)
    if notify:
        _tell(where)


def _tell(where: str) -> None:
    notifier = _notifier
    if notifier is not None:
        notifier(CRASH_TEXT)
    elif where == "main" and BACKGROUND not in sys.argv[1:]:     # 창이 생기기 전에 죽었다 — 켜는 중 실패
        _message_box(START_FAIL_TEXT)


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "주문 자동화", 0x10)
