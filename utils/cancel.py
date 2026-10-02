r"""취소 토큰. **[중단] 을 누른 뒤 실제로 멈추게 하는 장치.**

## 왜 필요한가 (2026-09-15 확인)

흐름의 시간 대부분은 `utils/wait.py` 의 조건 대기 안에서 쓴다. 상한이 짧아도
10초, 물류대기 저장은 120초, 자동수집은 4시간이다. 그래서 **대기 안에 취소
확인을 넣지 않으면 [중단] 이 최대 그 시간만큼 뒤에 듣는다.** 사람은 그것을
"안 눌린다" 로 받아들인다.

## 왜 토큰을 인자로 넘기지 않나

`wait_for` 를 부르는 곳이 수십 군데고, 그 대부분은 `automation/` 의 UI 조작
함수다. 거기까지 토큰을 인자로 흘리면 **취소와 아무 상관없는 함수 시그니처가
전부 바뀐다.** 그래서 **실행 스레드에 매달아 둔다**(`threading.local`).
흐름을 시작하는 쪽에서 `use()` 로 걸고, `wait_for` 가 `check()` 만 부른다.

스레드별로 따로 보관하므로 GUI 스레드와 작업 스레드가 섞이지 않는다.

## ★ `Cancelled` 가 `BaseException` 인 이유

`Exception` 으로 두면 **경로 곳곳의 `except Exception` 이 취소를 삼킨다.**
UI 조작 코드는 "아직 준비되지 않음" 을 예외로 받아 재시도하는 구조라,
취소가 그 그물에 걸리면 **중단을 눌러도 루프가 계속 돈다.**
`KeyboardInterrupt` 와 같은 이유로 `BaseException` 아래에 둔다.

그래서 **받는 쪽은 반드시 명시적으로 잡아야 한다** (`except Cancelled`).
2026-09-15 확인: 이 프로젝트에 `except:` / `except BaseException` 은 없다.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from utils.logger import get_logger

log = get_logger(__name__)

# 일시정지 중에 중단을 살피는 간격. 짧아야 [중단] 이 곧바로 듣는다.
PAUSE_POLL = 0.1


# 사용자에게 보이는 말. `str(Cancelled)` 은 멈춘 자리(내부 이름)가 붙어 파일 로그에만 쓴다 (09-29 실기).
USER_TEXT = "사용자가 중단했습니다"


class Cancelled(BaseException):
    """사용자가 중단을 요청했다. 실패가 아니다.

    ★ `Exception` 이 아니다. 위 모듈 주석의 이유를 읽을 것.
    """

    def __init__(self, where: str = ""):
        self.where = where
        super().__init__(f"중단 요청으로 멈췄다{f' ({where})' if where else ''}")


class CancelToken:
    """중단 요청을 담는 상자. 누르는 쪽과 도는 쪽이 이것만 공유한다."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""
        # ★ 일시정지. **set 이면 "돌아도 된다"** 로 둔다 — 처음 상태가
        #   기본값이어야 하고, 기본은 "돈다" 이기 때문이다.
        self._go = threading.Event()
        self._go.set()

    def cancel(self, reason: str = "사용자 중단") -> None:
        """중단을 요청한다. 여러 번 불러도 처음 이유를 유지한다."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()
            log.info("중단 요청: %s", reason)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    # --- 일시정지 -------------------------------------------------------
    #
    # ★ **다음 대기 지점에서** 멈춘다. 지금 누르고 있는 클릭을 중간에
    #   끊지 않는다 — 끊으면 대상 프로그램이 어중간한 상태로 남는다.
    #   멈추는 자리는 취소를 보는 자리와 같다(`utils/wait.py` 의 폴링 루프).
    def pause(self) -> None:
        if self._go.is_set():
            self._go.clear()
            log.info("일시정지 요청 — 다음 대기 지점에서 멈춘다")

    def resume(self) -> None:
        if not self._go.is_set():
            self._go.set()
            log.info("재개")

    @property
    def paused(self) -> bool:
        return not self._go.is_set()

    def wait_while_paused(self, where: str = "") -> None:
        """일시정지 중이면 풀릴 때까지 기다린다. **중단은 그 사이에도 듣는다.**

        멈춰 있는 동안 [중단] 을 눌렀는데 아무 일도 안 일어나면 안 된다.
        """
        while not self._go.is_set():
            if self._event.is_set():
                raise Cancelled(where or self._reason)
            # 중단을 기다리며 쉰다. 풀릴 때까지 짧게 되돌아온다.
            self._event.wait(PAUSE_POLL)

    def raise_if_cancelled(self, where: str = "") -> None:
        if self._event.is_set():
            raise Cancelled(where or self._reason)

    def wait(self, timeout: float) -> bool:
        """중단될 때까지 최대 `timeout` 초 기다린다. 중단됐으면 True.

        폴링 간격의 `sleep` 을 이것으로 바꾸면 **간격을 다 쓰지 않고**
        즉시 깨어난다. 대기 간격이 곧 중단 반응 시간이 되는 것을 막는다.
        """
        return self._event.wait(timeout)


_local = threading.local()


def current() -> CancelToken | None:
    """이 스레드에 걸린 토큰. 없으면 None."""
    return getattr(_local, "token", None)


@contextmanager
def use(token: CancelToken | None):
    """이 블록 안에서 도는 대기가 `token` 을 보게 한다.

    중첩을 허용한다. 빠져나갈 때 이전 것으로 되돌린다.
    """
    previous = current()
    _local.token = token
    try:
        yield token
    finally:
        _local.token = previous


def check(where: str = "") -> None:
    """토큰이 걸려 있으면 **중단과 일시정지를 함께** 본다.

    - 중단됐으면 `Cancelled` 를 올린다
    - 일시정지 중이면 풀릴 때까지 여기서 기다린다

    **토큰이 없는 경로(콘솔 도구 등)에서 그냥 통과해야 한다.** 취소 기능이
    없다고 도구가 못 돌면 안 된다.
    """
    token = current()
    if token is not None:
        token.raise_if_cancelled(where)
        token.wait_while_paused(where)


def sleep(seconds: float) -> None:
    """폴링 간격을 쉰다. 중단되면 그 즉시 깨어나 `Cancelled` 를 올린다."""
    token = current()
    if token is None:
        import time

        time.sleep(seconds)
        return
    if token.wait(seconds):
        raise Cancelled(token.reason)
