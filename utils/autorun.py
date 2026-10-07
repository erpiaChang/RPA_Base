r"""**지금 돌려도 되는가**를 판정하고 회차를 기록한다.

`utils/schedule.py` 가 "언제" 를 계산하고, 이 파일이 "그래서 지금 돌리나" 를
정한다. 흐름을 직접 부르지 않고 **콜백으로 받는다** — 시계도, 화면 잠김도,
실행도 전부 밖에서 주입한다.

그렇게 만든 이유는 하나다. 자동 실행은 **사람이 없을 때** 도는 기능이라
"잠겨 있을 때 건너뛰는가", "앞 회차가 아직 도는데 또 시작하지 않는가" 를
실제로 새벽까지 기다려 확인할 수가 없다. 확인 도구가 가짜 시계로 몇
밀리초에 하루를 돌려 본다 (`tools/probe_autorun.py`).

## 건너뛰는 이유는 다섯뿐이다

| 왜 | 어떻게 아나 | 그래서 |
| --- | --- | --- |
| 화면이 잠겼다 | `utils.process.screen_locked()` | **건너뛴다.** 잠금은 풀 수 없다 |
| 앞 회차가 아직 돈다 | 앱의 `busy` | 건너뛴다. 같은 아이디 동시 로그인은 서버가 막는다 |
| 사람이 잠깐 멈췄다 | `paused` | 건너뛴다. **껐다 켜도 이어진다** (`logs/autorun_state.json`, 10-01) |
| 시각을 10분 넘게 놓쳤다 | 박자가 늦게 왔다 (PC 잠자기·꺼짐) | 건너뛴다. 늦게 돌면 예고 없이 돈다 (10-02) |
| 사람이 이번만 건너뛰었다 | 예약 전 경고 창 [이번만 건너뛰기] → `skip()` | 그 한 번만. 다음 차례는 그대로 (10-02) |

★ **건너뛴 회차는 나중에 몰아 돌리지 않는다.** 다음 예약 시각으로 넘긴다.
  이유는 `utils/schedule.py` 머리말의 "지나간 시각은 따라잡지 않는다" 와 같다.

## 실패해도 곧바로 다시 시도하지 않는다 (사용자 확정 2026-09-16)

기록만 남기고 **다음 예약**을 기다린다. 실계정이라, 같은 상황에서 연달아
돌리면 저장된 내용이 이상하게 쌓이거나 대상 프로그램 상태가 어긋나는 것을 사람이 한참
뒤에 발견한다. 무제한 Retry 금지 규칙과도 같은 방향이다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from config.settings import LOG_DIR
from utils import schedule
from utils.logger import get_logger

log = get_logger(__name__)

# 예약 [일시정지] 상태 (10-01). 설정이 아니라 런타임 상태라 굽지 않는다.
# 경로는 부를 때 읽는다 — 확인 도구가 임시 폴더로 돌린다
STATE_PATH = LOG_DIR / "autorun_state.json"


def load_paused(path=None) -> tuple[bool, float]:
    """(멈춤, 언제부터). 파일이 없거나 깨졌으면 (False, 0) — 이 기능 전과 같다."""
    path = STATE_PATH if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, 0.0
    except (OSError, ValueError) as exc:
        log.warning("예약 일시정지 상태를 읽지 못했다 — 멈춤 아님으로 본다: %s", exc)
        return False, 0.0
    if not isinstance(data, dict):
        log.warning("예약 일시정지 상태 파일 꼴이 틀렸다 — 멈춤 아님으로 본다")
        return False, 0.0
    since = data.get("since")
    ok = isinstance(since, (int, float)) and 0 < since <= time.time()    # 시계가 바뀌어 앞날이면 '언제부터' 를 안 보인다
    return bool(data.get("paused")), float(since) if ok else 0.0


def save_paused(paused: bool, since: float, path=None) -> None:
    """남긴다. 실패해도 멈춤은 이번 실행에 그대로다 — 경고만."""
    path = STATE_PATH if path is None else path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"paused": paused, "since": since}), encoding="utf-8")
    except OSError as exc:
        log.warning("예약 일시정지 상태를 저장하지 못했다 (다시 켜면 풀린다): %s", exc)

# 회차 결과
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
SKIPPED = "skipped"       # 조건이 안 돼서 아예 시작하지 않았다

RESULT_LABELS = {
    RUNNING: "진행 중",
    DONE: "완료",
    FAILED: "실패",
    CANCELLED: "중단",
    SKIPPED: "건너뜀",
}

# 기록을 몇 회차까지 들고 있을지. 무인으로 며칠 돌면 끝없이 쌓인다.
HISTORY_LIMIT = 60

# 시작 시각을 이만큼 넘겨서야 박자가 왔으면 놓친 것으로 본다 (10-02). 예약 전 경고·점검이 지나간 뒤다
LATE_LIMIT = 10 * 60
MISSED = "예약 시각에 PC 가 꺼져 있었거나 잠자기였다 (늦게 돌리지 않는다)"


@dataclass
class Run:
    """회차 하나. 화면은 이것을 그대로 표에 그린다."""

    planned_at: float           # 돌기로 했던 시각
    started_at: float = 0.0     # 실제로 시작한 시각 (건너뛰면 0)
    finished_at: float = 0.0
    state: str = RUNNING
    summary: str = ""           # 결과 한 줄, 또는 건너뛴 이유

    @property
    def state_label(self) -> str:
        return RESULT_LABELS.get(self.state, self.state)

    @property
    def elapsed(self) -> float:
        if not self.started_at or not self.finished_at:
            return 0.0
        return max(self.finished_at - self.started_at, 0.0)

    def clock(self) -> str:
        stamp = self.started_at or self.planned_at
        return time.strftime("%m-%d %H:%M:%S", time.localtime(stamp))

    def took(self) -> str:
        if not self.elapsed:
            return ""
        minutes, seconds = divmod(int(self.elapsed), 60)
        return f"{minutes}분 {seconds}초" if minutes else f"{seconds}초"


class AutoRunner:
    """예약대로 흐름을 부르는 시계. **tkinter 를 모른다.**

    부르는 쪽이 `tick()` 을 주기적으로 불러 준다(화면에서는 `root.after`).
    시각이 됐고 막는 것이 없으면 `start()` 콜백을 부르고, 흐름이 끝나면
    부르는 쪽이 `finished()` 를 알려 준다.
    """

    def __init__(self, plan: schedule.Plan, *, start, busy, locked,
                 now=time.time, on_skip=None) -> None:
        self.plan = plan
        self._start = start        # () -> None   흐름을 시작한다
        self._busy = busy          # () -> bool   앞 회차가 아직 도는가
        self._locked = locked      # () -> bool   화면이 잠겼는가
        self._now = now            # () -> float  지금 (시험할 때 가짜를 준다)
        # (예약 시각, 이유) -> None  건너뛴 회차를 서버에도 남긴다 (09-22). 없어도 된다
        self._on_skip = on_skip

        self.paused = False
        self.paused_since = 0.0     # 멈춘 시각 — 껐다 켜도 이어지니 언제부터인지 보인다 (10-01)
        self.next_run: float | None = None
        self.last_finished: float = 0.0
        self.history: list[Run] = []
        self._current: Run | None = None

    # --- 예약 --------------------------------------------------------
    def set_plan(self, plan: schedule.Plan) -> None:
        """규칙을 바꾼다. 다음 차례를 **즉시 다시 잡는다.**"""
        self.plan = plan
        self.reschedule()

    def reschedule(self) -> None:
        self.next_run = self.plan.next_at(self._now(), self.last_finished)
        if self.next_run is not None:
            log.info("다음 자동 실행: %s (%s)",
                     time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(self.next_run)),
                     self.plan.describe())

    def skip(self, reason: str) -> None:
        """이번 차례를 **돌기 전에** 건너뛴다 — 예약 전 경고의 [이번만 건너뛰기] (10-02). 다음 차례는 그대로."""
        if self.next_run is not None and self._current is None:
            self._skip(self.next_run, reason)

    def remaining(self) -> float | None:
        """다음 실행까지 남은 초. 예약이 없으면 None."""
        if self.next_run is None:
            return None
        return max(self.next_run - self._now(), 0.0)

    # --- 한 박자 ------------------------------------------------------
    def tick(self) -> str:
        """시각을 본다. 무슨 일을 했으면 한 줄로 돌려준다(없으면 빈 문자열).

        ★ **여기서 예외를 올리지 않는다.** 화면의 반복 호출에서 불리므로
          한 번 터지면 그 뒤 예약이 전부 죽는다. 무인 운전에서 가장 위험한
          고장 방식이다.
        """
        try:
            return self._tick()
        except Exception as exc:
            log.exception("자동 실행 판정에서 예외가 났다: %s", exc)
            return f"판정 실패: {type(exc).__name__}"

    def _tick(self) -> str:
        if not self.plan.usable:
            self.next_run = None
            return ""
        if self.next_run is None:
            self.reschedule()
            return ""

        now, planned = self._now(), self.next_run
        if now < planned:
            return ""

        reason = self._blocked() or (MISSED if now - planned > LATE_LIMIT else "")
        if reason:
            self._skip(planned, reason)
            return reason

        self._current = Run(planned_at=planned, started_at=now, state=RUNNING)
        self._remember(self._current)
        # ★ 다음 차례는 **끝난 뒤에** 잡는다. 여기서 미리 잡으면 흐름이 간격보다
        #   오래 걸릴 때 도는 중에 다음 시각이 지나가 버린다.
        self.next_run = None
        log.info("자동 실행 시작 — 예약 %s",
                 time.strftime("%m-%d %H:%M", time.localtime(planned)))
        self._start()
        return "실행 시작"

    def _blocked(self) -> str:
        """지금 못 도는 이유. 돌 수 있으면 빈 문자열."""
        if self.paused:
            return "사람이 자동 실행을 멈춰 두었다"
        if self._busy():
            return "앞 회차가 아직 돌고 있다"
        if self._locked():
            return "화면이 잠겨 있다 (잠긴 화면에서는 자동화가 동작하지 않는다)"
        return ""

    def _skip(self, planned: float, reason: str) -> None:
        record = Run(planned_at=planned, state=SKIPPED, summary=reason,
                     finished_at=self._now())
        self._remember(record)
        log.warning("자동 실행을 건너뛴다 (예약 %s) — %s",
                    time.strftime("%H:%M", time.localtime(planned)), reason)
        if self._on_skip is not None:
            try:
                self._on_skip(planned, reason)
            except Exception as exc:                        # noqa: BLE001 — 알림 때문에 예약이 죽으면 안 된다
                log.warning("건너뜀을 알리지 못했다: %s", exc)
        # 건너뛴 회차는 몰아 돌리지 않는다. 다음 예약으로 넘긴다 — 그 차례 **뒤**부터 (돌기 전에 건너뛰면 지금이 앞이다).
        # 놓친 차례면 10분 안에 든 줄은 살린다 — 09:00 을 놓치고 13:00:30 에 깨면 13:00 은 돈다
        since = max(self._now() - (LATE_LIMIT if reason == MISSED else 0), planned)
        self.next_run = self.plan.next_at(since, self.last_finished)

    # --- 흐름이 끝났다 ------------------------------------------------
    def finished(self, state: str, summary: str = "") -> None:
        """부르는 쪽이 흐름의 끝을 알려 준다. **실패도 여기로 온다.**"""
        now = self._now()
        self.last_finished = now
        if self._current is not None:
            self._current.state = state
            self._current.summary = summary
            self._current.finished_at = now
            log.info("자동 실행 회차 종료 — %s (%s) %s",
                     RESULT_LABELS.get(state, state),
                     self._current.took(), summary)
            self._current = None
        self.reschedule()

    # --- 상태 ---------------------------------------------------------
    @property
    def current(self) -> Run | None:
        """지금 도는 회차. `start()` 안에서 읽으면 그 회차의 예약 시각이 든다."""
        return self._current

    def status_line(self) -> str:
        """화면에 한 줄로. 사람이 이것만 보고 상태를 알 수 있어야 한다."""
        if not self.plan.enabled:
            return "자동 실행 꺼짐"
        problems = self.plan.problems()
        if problems:
            return "설정을 고쳐야 한다 — " + problems[0]
        if self.paused:
            since = (time.strftime("%m-%d %H:%M부터, ", time.localtime(self.paused_since))
                     if self.paused_since else "")
            return f"일시정지 — 예약 멈춤 ({since}{self.plan.describe()})"     # 단추 이름과 같게 (09-29)
        if self._current is not None:
            return "실행 중..."
        if self.plan.mode == schedule.DAILY and not self.plan.slots():
            return "예약 없음"
        left = self.remaining()
        if left is None:
            return f"남은 예약 없음 — 지정한 날짜가 모두 지났다 ({self.plan.describe()})"
        when = schedule.when_text(self.next_run, self._now())
        return f"다음 실행 {when} — {schedule.countdown(left)} 남음"

    def counts(self) -> dict[str, int]:
        """회차 집계. 화면이 `완료 3 / 실패 1 / 건너뜀 2` 로 쓴다."""
        out: dict[str, int] = {}
        for record in self.history:
            out[record.state] = out.get(record.state, 0) + 1
        return out

    def _remember(self, record: Run) -> None:
        self.history.append(record)
        if len(self.history) > HISTORY_LIMIT:
            del self.history[:-HISTORY_LIMIT]

    def rows(self, limit: int = 10) -> list[tuple[str, str, str, str]]:
        """최근 회차를 **새 것부터** 표 줄로. (시각, 상태, 소요, 내용)"""
        return [(record.clock(), record.state_label, record.took(),
                 record.summary)
                for record in reversed(self.history[-limit:])]
