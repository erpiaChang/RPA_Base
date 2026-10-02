"""조건 대기.

고정 대기(정해진 초를 그냥 쉬는 것)로 다음 단계로 넘어가지 않는다.
"무엇이 나타나면/사라지면 진행"을 조건으로 쓰고, 모든 대기에 timeout을 둔다.

## 취소는 **여기서** 듣는다 (2026-09-15)

흐름의 시간 대부분을 이 모듈 안에서 쓴다. 상한이 10초~4시간이라,
취소 확인을 여기 넣지 않으면 **[중단] 이 최대 그 시간만큼 뒤에 듣는다.**
그래서 두 군데에 넣었다.

1. 조건을 확인할 때마다 `cancel.check()`
2. 폴링 간격의 `sleep` 을 `cancel.sleep()` 으로 — 간격을 다 쓰지 않고 깨어난다

취소는 `Cancelled`(BaseException) 로 빠져나간다. 자세한 것은 `utils/cancel.py`.
토큰이 걸려 있지 않으면(콘솔 도구 등) 아무 일도 하지 않는다.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

from config.settings import SETTINGS
from utils import cancel
from utils.logger import get_logger

T = TypeVar("T")

log = get_logger(__name__)


class WaitTimeout(TimeoutError):
    """제한 시간 안에 조건이 만족되지 않았다."""

    def __init__(self, what: str, timeout: float, last_error: Exception | None = None):
        self.what = what
        self.timeout = timeout
        self.last_error = last_error
        msg = f"대기 실패: {what} ({timeout:.1f}초 초과)"
        if last_error is not None:
            msg += f" / 마지막 오류: {type(last_error).__name__}: {last_error}"
        super().__init__(msg)


def wait_for(
    condition: Callable[[], T],
    what: str,
    timeout: float | None = None,
    interval: float | None = None,
) -> T:
    """`condition()`이 참 값을 돌려줄 때까지 기다리고 그 값을 반환한다.

    condition 안에서 나는 예외는 "아직 준비되지 않음"으로 보고 재시도하되,
    마지막 예외는 보관해서 timeout 시 함께 보고한다.
    """
    timeout = SETTINGS.timeouts.control if timeout is None else timeout
    interval = SETTINGS.timeouts.poll_interval if interval is None else interval

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    attempts = 0

    while time.monotonic() < deadline:
        # 조건을 확인하기 **전에** 본다. 중단된 뒤에 한 번 더 조작하지 않는다.
        cancel.check(what)
        attempts += 1
        try:
            result = condition()
            if result:
                log.debug("대기 성공: %s (%d회 확인)", what, attempts)
                return result
        except Exception as exc:
            last_error = exc
        # UIA 상태를 이벤트로 구독할 수 없어 폴링한다. 고정 대기가 아니라
        # 조건 확인 사이의 간격이며, 위 deadline으로 상한이 걸려 있다.
        # 중단되면 간격을 다 쓰지 않고 즉시 깨어난다.
        cancel.sleep(interval)

    raise WaitTimeout(what, timeout, last_error)


def stays_true(condition: Callable[[], object], what: str, seconds: float,
               interval: float | None = None) -> bool:
    """`seconds` 동안 **계속** 참인지 확인한다. 한 번이라도 거짓이면 즉시 False.

    "지금 참인가" 가 아니라 "잠깐 뒤집히지 않는가" 를 봐야 하는 자리가 있다.
    예: [가져오기] 직후에는 버튼이 회색이 되기까지 몇 초 걸린다. 한 번만 보고
    판단하면 그 틈에 "완료" 로 오판한다.

    폴링 간격의 대기는 이 모듈 안에만 둔다. 업무 코드에는 고정 대기를 두지 않는다.
    """
    interval = SETTINGS.timeouts.poll_interval if interval is None else interval
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        cancel.check(what)
        if not condition():
            log.debug("%s: %.1f초를 채우지 못했다", what, seconds)
            return False
        cancel.sleep(interval)
    return bool(condition())


def pause(seconds: float, reason: str) -> None:
    """**의도적으로** 정해진 시간을 기다린다. 조건 대기가 아니다.

    고정 대기는 원칙적으로 금지지만, "무엇이 나타나기를 기다리는" 것이 아니라
    **시간 자체가 조건인** 경우가 있다. 지금 쓰는 곳은 둘이다.

    1. `collect/webmail.py` — 인증번호를 다시 받기 전에 분(minute)이 바뀌기를
       기다린다. 화면에서 확인할 수 있는 상태가 아니라 **시간이 조건**이다
    2. `utils/ui.py` — 스크롤이 먹지 않을 때 "다시 조회 중일 수 있다" 고 보고
       한 번 더 시도하기 전의 간격. 성격이 다르다(시간이 조건이 아니다).
       기다린 뒤 `visible_row_numbers()` 로 **실제로 바뀌었는지 검증**하므로
       고정 대기의 위험은 없앴지만, 조건 대기로 바꿀 수 있으면 그게 낫다

    그래도 `wait_for` 위에 얹는다. 상한(timeout)이 그대로 걸리고,
    이유가 로그에 남는다.
    """
    deadline = time.monotonic() + seconds
    # %.0f 로 찍으면 1초 미만이 "0초 기다린다" 가 된다 (2026-09-10 실측).
    log.info("%.1f초 기다린다: %s", seconds, reason)
    wait_for(lambda: time.monotonic() >= deadline, f"대기({reason})",
             timeout=seconds + 5.0)


def wait_until_gone(
    condition: Callable[[], object],
    what: str,
    timeout: float | None = None,
    interval: float | None = None,
) -> None:
    """`condition()`이 거짓이 되거나 예외를 낼 때까지 기다린다 (사라짐 확인)."""

    def gone() -> bool:
        try:
            return not condition()
        except Exception:
            return True  # 접근 자체가 실패 = 사라진 것으로 본다

    wait_for(gone, f"{what} 사라짐", timeout=timeout, interval=interval)

