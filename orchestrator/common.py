r"""흐름 모듈이 함께 쓰는 것. **어떤 배포본에도 들어간다.**

여기에는 다른 기능의 이름을 적지 않는다. 이 파일이 모든 배포본에 들어가므로,
여기에 남긴 문구는 전부 흔적이 된다. 실제 단계 목록은 `steps_<기능>.py` 에 있다.

## 진행 상황을 어떻게 알리나 (2026-09-15 개편)

예전에는 `hooks.status("매출처리 중...")` 처럼 **자유 문자열** 여섯 개뿐이었다.
화면은 그 문자열을 상태줄에 찍는 것밖에 할 수 없었다 — 전체 몇 단계 중
몇 번째인지, 어디서 멈췄는지, 다시 실행해도 되는 단계인지 알 수 없었다.

지금은 **단계가 값이다**(`orchestrator/steps.py`). 흐름은 단계를 `stage()` 로
감싸고, 화면은 `on_step` 으로 받는다.

    hooks.begin(plan)                       # 전체 목록을 먼저 알린다
    with hooks.stage(SALES) as run:         # 진행 중 -> 완료/실패/중단
        run.result = process_sales(...)

`status` 는 없애지 않았다. `on_step` 을 붙이지 않은 화면과 콘솔은 예전처럼
한 줄만 받는다 (예: 3/8 매출처리 - 진행 중). 그래서 기존 화면을 깨지 않는다.

## 사용자가 읽는 글 (2026-09-21)

단계의 시작·끝·건너뜀·실패를 **사용자용 로그**(`utils/logger.user_log`)에 한 줄씩
남긴다. 화면의 진행 로그는 그것만 보인다. 그래서 `run.detail` 은 **사람 말**이어야 한다
(pid·창 제목·행 수 대신 건수와 이름). 실패 문구는 `explain` 이 바꾼다 — 원문은
`error_detail` 과 파일 로그에만 남는다.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator

from orchestrator import steps
from orchestrator.steps import Step, StepEvent
from utils import cancel
from utils.logger import get_logger, user_log

log = get_logger(__name__)
user = user_log()


def default_explain(exc: BaseException, step: Step | None) -> str:
    """기능을 모르는 기본 설명. 창마다 더 자세한 것을 `Hooks.explain` 으로 준다."""
    told = getattr(exc, "user_message", "")
    if told:
        return told
    # `물류대기 중에` 가 "물류가 대기하는 중에" 로 읽혔다 (09-21 2차 검토) — 단계 이름을 [ ] 로 묶는다.
    where = f"[{step.name}] 단계에서" if step is not None else "실행 중에"
    return (f"{where} 문제가 생겨 멈췄습니다.\n"
            "다시 실행해도 같으면 [결과 보기]로 연 리포트 파일을 담당자에게 보내 주세요.")


@dataclass
class StepRun:
    """한 단계의 **실행 기록.** 대시보드와 요약이 함께 읽는다."""

    step: Step
    index: int          # 1부터
    total: int
    state: str = steps.PENDING
    # 이 단계를 설명하는 말. 시작 시 준 것이거나, 흐름이 끝에 담은 결과 요약이다.
    detail: str = ""
    # 진행 중에만 뜻이 있는 세부 (예: 엑셀 2/3). 끝나면 지운다 - 그러지 않으면
    # `완료 - 2/3 진행` 처럼 **끝났는데 중간 진행이 남은** 문장이 된다.
    progress: str = ""
    result: object = None
    # 실패 문구. `error` 는 사람 말(`Hooks.explain`), `error_detail` 은 예외 원문(관리자용)
    error: str = ""
    error_detail: str = ""
    elapsed: float = 0.0
    # 벽시계 시각. 경과(`elapsed`)는 단조시계로 재고, 이 둘은 **언제**를 말한다.
    # 이유는 `orchestrator/steps.py` 의 `StepEvent.started_at` 주석 참고.
    started_at: float = 0.0
    finished_at: float = 0.0
    # 이 단계 안의 건수. **흐름이 알 때만** 채운다 (`Hooks.count` 로 넣는다).
    # 이유는 `orchestrator/steps.py` 의 `StepEvent.total_items` 주석 참고.
    total_items: int = 0
    done_items: int = 0
    ok_items: int = 0
    failed_items: int = 0
    activity: str = ""
    # 이 단계의 **상세 표**. 흐름이 채운다 (`StepEvent.columns` 주석 참고).
    columns: tuple = ()
    rows: list = field(default_factory=list)
    # 큰 단계 번호 (`steps.stage_numbers`). 없으면 0 — 사람에게는 세부 단계 번호를 보인다
    stage_index: int = 0
    stage_total: int = 0

    @property
    def where(self) -> str:
        """사용자 로그의 위치 — `2/4` (큰 단계) 또는 `9/11`. 파일 로그는 늘 세부 단계 번호다."""
        if self.stage_total:
            return f"{self.stage_index}/{self.stage_total}"
        return f"{self.index}/{self.total}"

    def event(self) -> StepEvent:
        """지금 상태를 얼려서 내보낸다. 이유는 `StepEvent` 주석 참고."""
        detail = self.progress if self.state == steps.RUNNING and self.progress \
            else self.detail
        return StepEvent(step_id=self.step.id, name=self.step.name,
                         index=self.index, total=self.total, state=self.state,
                         detail=detail, elapsed=self.elapsed,
                         started_at=self.started_at,
                         finished_at=self.finished_at,
                         total_items=self.total_items,
                         done_items=self.done_items,
                         ok_items=self.ok_items,
                         failed_items=self.failed_items,
                         activity=self.activity, error=self.error,
                         reentry=self.step.reentry, replay=self.step.replay,
                         # 얼려서 보낸다. 화면이 나중에 그리는 사이에 흐름이
                         # 줄을 더 담아도 지나간 사진이 바뀌지 않아야 한다.
                         columns=tuple(self.columns),
                         rows=tuple(tuple(row) for row in self.rows),
                         stage=self.step.stage, stage_index=self.stage_index,
                         stage_total=self.stage_total)

    def as_dict(self) -> dict:
        return {"id": self.step.id, "name": self.step.name,
                "state": self.state, "detail": self.detail,
                # 멈춘 단계에서는 **어디까지 갔는지**가 남아 있다. 버리지 않는다.
                "progress": self.progress,
                "error": self.error, "error_detail": self.error_detail,
                "elapsed": round(self.elapsed, 1),
                # 리포트가 "언제 끝났나" 를 적을 수 있게 함께 넘긴다.
                "started_at": self.started_at, "finished_at": self.finished_at,
                "total_items": self.total_items, "done_items": self.done_items,
                "ok_items": self.ok_items, "failed_items": self.failed_items,
                "result": self.result,
                # 상세 표. 실행 후 리포트(`orchestrator/report.py`)가 이것을 그린다.
                "columns": tuple(self.columns),
                "rows": [tuple(row) for row in self.rows]}


# 상태줄에 찍을 상태. `대기` 를 찍으면 시작 전에 마지막 단계 이름이 남고,
# `완료` 는 바로 다음 `진행 중` 에 덮여 한 줄을 낭비한다.
STATUS_STATES = (steps.RUNNING, steps.SKIPPED, steps.FAILED, steps.CANCELLED)


@dataclass
class Hooks:
    """진행 상황을 알리는 통로. GUI 는 상태줄과 대시보드를, 콘솔은 로그를 쓴다."""

    status: Callable[[str], None] = lambda text: None
    on_target: Callable[[object], None] = lambda target: None
    on_login: Callable[[str], None] = lambda title: None
    # 단계 통지. 대시보드가 여기에 붙는다. 없으면 `status` 한 줄로만 알린다.
    on_step: Callable[[StepEvent], None] | None = None
    # 중단 토큰. [중단] 을 누르는 쪽과 도는 쪽이 이것만 공유한다.
    # 없으면(콘솔 도구 등) 취소 확인이 아무 일도 하지 않는다.
    token: cancel.CancelToken | None = None
    # 구간 실행 — **이 단계부터** 시작한다. 빈 값이면 처음부터.
    start_step: str = ""
    # 되돌릴 수 없는 구간을 시작할 때 사람에게 묻는 창구. 돌려주는 값이 승인 여부다.
    # **없으면 물을 수가 없어서 시작하지 않는다** (`approve_start` 참고).
    confirm: Callable[[str], bool] | None = None
    # 실패를 사람 말로 바꾸는 함수 `(예외, 단계) -> 문구`. 기능을 아는 창이 준다
    # (`orchestrator/friendly.py`). 없으면 `default_explain`.
    explain: Callable[[BaseException, Step | None], str] | None = None
    # 단계가 실패했을 때 ERPia 팝업([확인] 하나)을 닫아 보는 창구 `() -> 닫은 팝업 글 | None`.
    # 닫았으면 그 단계만 실패로 적고 **다음 단계로 넘어간다** (사용자 확정 2026-09-22).
    # 흐름이 ERPia 로그인 뒤에 넣는다 — 그 전 단계(실행·로그인)는 원래대로 멈춘다.
    # 메일 실패는 이것과 별개로 `full_flow._full` 이 잡아 뒤 기능으로 넘어간다 (09-30).
    recover: Callable[[], str | None] | None = None
    # 멈췄을 때 "다시 실행하면 이 단계는 어떻게 되나" — 단계 id → 사람 말. 흐름이 준다
    # (흐름마다 다르다: 통합은 올린 엑셀을 다시 올리지 않고, ERPia 단독은 다시 올린다).
    rerun_notes: dict[str, str] = field(default_factory=dict)
    # 사람 손이 **지금** 필요한 일 `(종류, 글)` — 종류는 `telemetry.ALERT_KINDS`. 서버 보고기가 감싸 업체 담당자에게
    # 메일로 알린다 (10-01). 화면 안내는 따로 `notice` 가 한다. 보고기가 없으면 아무 일도 하지 않는다
    alert: Callable[[str, str], None] = lambda kind, text: None

    plan: list[Step] = field(default_factory=list)
    runs: list[StepRun] = field(default_factory=list)
    # **사람이 챙길 일** (09-21 검토: 문제가 상세 표에 흩어져 "완료 11" 만 보였다).
    # 흐름이 `attend()` 로 넣고, 리포트 맨 위·끝 결과·사용자 로그가 모아서 보여 준다.
    attention: list[str] = field(default_factory=list)

    # --- 실행 준비 ---------------------------------------------------------
    @contextmanager
    def running(self) -> Iterator[None]:
        """흐름 전체를 감싼다. 이 안에서 도는 **모든 대기가 토큰을 본다.**

        `utils/wait.py` 가 스레드에 걸린 토큰을 보므로, 흐름 함수마다 토큰을
        인자로 넘기지 않아도 된다. 이유는 `utils/cancel.py` 주석 참고.
        """
        with cancel.use(self.token):
            yield

    def begin(self, plan: list[Step]) -> None:
        """이번 실행이 지나갈 **전체 목록**을 먼저 알린다.

        대시보드는 시작 전에 목록을 다 그려야 한다. 단계가 시작될 때마다
        한 줄씩 나타나면 "전체 몇 개 중 몇 번째" 를 알 수 없다.
        """
        self.plan = list(plan)
        numbers = steps.stage_numbers(self.plan)
        self.runs = [StepRun(step=item, index=position, total=len(plan),
                             stage_index=numbers.get(item.id, (0, 0))[0],
                             stage_total=numbers.get(item.id, (0, 0))[1])
                     for position, item in enumerate(plan, start=1)]
        log.info("이번 실행 단계 %d개: %s", len(plan),
                 " -> ".join(item.name for item in plan))
        # 사람에게는 큰 단계로 말한다 (09-29 사용자 요청) — '11단계' 가 아니라 '4단계'
        stages = list(dict.fromkeys(item.stage for item in plan if item.stage))
        if stages:
            user.info("실행을 시작합니다 (%d단계: %s)", len(stages), " → ".join(stages))
        else:
            user.info("실행을 시작합니다 (%d단계)", len(plan))

        # 구간 실행이면 시작점보다 앞은 **하지 않을 것**으로 미리 표시한다.
        # 그래야 화면이 "안 한 것" 과 "하다가 멈춘 것" 을 구별할 수 있다.
        if self.start_step:
            start = steps.index_of(self.plan, self.start_step)
            if start < 0:
                raise ValueError(f"계획에 없는 시작 단계다: {self.start_step}")
            for run in self.runs[:start]:
                run.state = steps.SKIPPED
                run.detail = "구간 실행 — 시작점보다 앞이다"
            log.info("구간 실행: %s 부터 한다 (앞의 %d단계는 하지 않는다)",
                     self.plan[start].name, start)
        for run in self.runs:
            self._notify(run)

    def want(self, step: Step) -> bool:
        """구간 실행에서 이 단계를 **할 차례인가.** 시작점보다 앞이면 False.

        계획에 없는 단계는 막지 않는다(True). 진행 표시가 틀리는 것이 흐름을
        세우는 것보다 낫다.
        """
        if not self.start_step:
            return True
        start = steps.index_of(self.plan, self.start_step)
        here = steps.index_of(self.plan, step.id)
        if start < 0 or here < 0:
            return True
        return here >= start

    def first_wanted(self) -> Step | None:
        """이번 실행이 **실제로 처음 하는** 단계. 계획이 비어 있으면 None."""
        for item in self.plan:
            if self.want(item):
                return item
        return None

    def approve_start(self, step: Step, screen_state: str = "") -> None:
        """위험한 구간을 시작점으로 골랐다. **사람에게 확인받는다.**

        승인하지 않으면 `Cancelled` 로 빠져나온다 — 실패가 아니다.

        ★ **물어볼 상대가 없으면 시작하지 않는다.** 되돌릴 수 없는 단계를
          "아무도 안 막았으니 해도 된다" 로 해석하면 안 된다. 콘솔 도구에서
          쓰려면 `confirm` 을 넘겨야 한다.
        """
        if not step.needs_confirm:
            return
        lines = [f"[{step.name}] 부터 실행합니다.",
                 f"재실행 위험도: {step.reentry_label}"]
        if step.replay:
            lines.append(step.replay)
        if screen_state:
            # ★ 정책 문구만으로는 판단할 수 없다. **지금 화면에 무엇이 남아
            #   있는지**가 실제 근거다 (사용자 확정 2026-09-15).
            lines += ["", "지금 화면 상태:", screen_state]
        lines += ["", "그대로 진행할까요?"]
        question = "\n".join(lines)
        log.warning("구간 실행 확인 요청 — %s (%s)", step.name, step.reentry_label)
        if self.confirm is None:
            raise cancel.Cancelled(
                f"{step.name} 은(는) 확인이 필요한 구간인데 물어볼 상대가 없다. "
                "확인 창구(confirm)를 넘기고 다시 실행할 것.")
        if not self.confirm(question):
            raise cancel.Cancelled(f"{step.name} 부터 실행하지 않기로 했다")
        log.info("구간 실행 승인 — %s 부터 시작한다", step.name)

    # --- 단계 실행 ---------------------------------------------------------
    @contextmanager
    def stage(self, step: Step, detail: str = "") -> Iterator[StepRun]:
        """단계 하나를 감싼다. 진행 중 -> 완료 / 건너뜀 / 실패 / 중단.

        블록 안에서 `run.result` / `run.detail` 에 결과를 담으면 대시보드와
        요약이 그것을 읽는다. 담지 않아도 된다.

        ★ **시작 전에 취소를 확인한다.** 중단을 누른 뒤에 매출처리처럼
          되돌릴 수 없는 단계를 새로 시작하면 안 된다.
        """
        cancel.check(step.name)
        run = self._run_for(step)
        run.state, run.detail, run.progress = steps.RUNNING, detail, ""
        run.started_at, run.finished_at = time.time(), 0.0
        started = time.monotonic()
        log.info("[%d/%d] %s 시작%s", run.index, run.total, step.name,
                 f" - {detail}" if detail else "")
        # 사용자 로그에는 **시작 줄을 남기지 않는다** — 끝·실패만 (09-21 검토: 로그가 두 배).
        # 지금 무엇을 하는지는 상태줄과 오버레이가 보여 준다.
        self._notify(run)
        try:
            yield run
        except cancel.Cancelled:
            run.state = steps.CANCELLED
            run.elapsed = time.monotonic() - started
            run.finished_at = time.time()
            log.warning("[%d/%d] %s 중단 (%.1f초)", run.index, run.total,
                        step.name, run.elapsed)
            user.warning("[%s] %s 중단", run.where, step.name)
            self._notify(run)
            raise
        except Exception as exc:
            popup = self._recover(step)
            if popup:
                # ★ 예외를 **삼킨다** — 부르는 쪽은 `with` 뒤를 이어 간다. 그래서 단계 함수는
                #   블록 안에서 채우는 값을 블록 **앞에서** 초기화해 둔다.
                self.attend(f"[{step.name}] 단계에서 ERPia 가 알림을 띄워 [확인]을 누르고 "
                            f"다음 단계로 넘어갔습니다 — '{popup}'. 이 단계는 끝나지 않았으니 "
                            "ERPia 화면에서 확인하세요.")
            run.state = steps.FAILED
            run.elapsed = time.monotonic() - started
            run.finished_at = time.time()
            run.error_detail = f"{type(exc).__name__}: {exc}"
            if popup:
                run.error = f"ERPia 알림 '{popup}' — [확인]을 누르고 다음 단계로 넘어갔습니다."
                run.detail = "끝나지 않음 — ERPia 알림을 닫고 넘어감"
                log.error("[%d/%d] %s 실패 (%.1f초), 팝업을 닫고 다음 단계로: %s",
                          run.index, run.total, step.name, run.elapsed, run.error_detail)
                user.error("[%s] %s 끝나지 않음 — %s", run.where, step.name, run.error)
                self._notify(run)
                return
            run.error = self.describe(exc, step)
            # 스크린샷과 상세 로그는 안쪽(`utils.logger.step`)에서 이미 남긴다.
            # 여기서는 **어느 단계였는지**만 분명히 남긴다.
            log.error("[%d/%d] %s 실패 (%.1f초): %s", run.index, run.total,
                      step.name, run.elapsed, run.error_detail)
            # 한 줄로 이을 때는 ' / ' 로 — 그냥 붙이면 두 문장이 한 문장처럼 읽혔다.
            user.error("[%s] %s 실패 — %s", run.where, step.name, run.error.replace("\n", " / "))
            self._notify(run)
            raise
        else:
            if run.state == steps.RUNNING:   # 블록 안에서 건너뜀으로 바꿨을 수 있다
                run.state = steps.DONE
            run.progress = run.activity = ""  # 끝났으니 중간 진행은 지운다
            run.elapsed = time.monotonic() - started
            run.finished_at = time.time()
            log.info("[%d/%d] %s %s (%.1f초)%s", run.index, run.total, step.name,
                     steps.STATE_LABELS[run.state], run.elapsed,
                     f" - {run.detail}" if run.detail else "")
            user.info("[%s] %s %s%s", run.where, step.name, steps.STATE_LABELS[run.state],
                      f" — {run.detail}" if run.detail else "")
            self._notify(run)

    def _recover(self, step: Step) -> str | None:
        """`recover` 를 부른다. **여기서 예외를 올리지 않는다** — 원래 실패가 가려지면 안 된다."""
        if self.recover is None or (self.token is not None and self.token.cancelled):
            return None
        try:
            return self.recover()
        except cancel.Cancelled:
            # 팝업을 보는 도중 중단됐다. 원래 실패로 둔다 — 새어 나가면 단계가 '진행 중' 으로 남는다
            log.warning("[%s] 실패 뒤 팝업 확인 중에 중단됐다.", step.name)
            return None
        except Exception as bad:            # noqa: BLE001
            log.warning("[%s] 실패 뒤 팝업 확인을 하지 못했다: %s", step.name, bad)
            return None

    def unfinished_note(self) -> str:
        """끝났는데 실패로 남은 단계 — ERPia 알림을 닫고 넘어갔거나(`recover`) 메일이 멈추고 넘어갔다
        (`full_flow._full`). 요약 맨 앞에 붙일 한 줄 (없으면 ""). 사유는 '확인할 것' 에 있다.

        흐름이 끝까지 왔을 때만 부른다 — 그 밖의 실패는 예외로 멈추므로 여기 오지 않는다.
        """
        names = [run.step.name for run in self.runs if run.state == steps.FAILED]
        if not names:
            return ""
        return f"끝나지 않은 단계: {', '.join(names)} (확인할 것 참고)"

    def skip(self, step: Step, why: str) -> None:
        """이 단계는 할 일이 없어 지나간다. **실패가 아니다.**"""
        run = self._run_for(step)
        run.state, run.detail = steps.SKIPPED, why
        run.finished_at = time.time()
        log.info("[%d/%d] %s 건너뜀 - %s", run.index, run.total, step.name, why)
        user.info("[%s] %s 건너뜀 — %s", run.where, step.name, why)
        self._notify(run)

    # --- 사람에게 보이는 글 ------------------------------------------------
    def describe(self, exc: BaseException, step: Step | None = None) -> str:
        """실패를 사람 말로. **여기서 예외를 올리지 않는다** — 설명 때문에 원래 실패가 가려지면 안 된다."""
        explain = self.explain or default_explain
        try:
            return explain(exc, step) or default_explain(exc, step)
        except Exception as bad:            # noqa: BLE001
            log.warning("실패 설명을 만들지 못했다(기본 문구): %s", type(bad).__name__)
            return default_explain(exc, step)

    def failure_text(self, exc: BaseException) -> str:
        """이번 실행의 실패를 사람 말로 — **무슨 일 / 할 일 / 이미 끝난 것 / 다시 실행하면.**

        실패한 단계가 있으면 그 단계의 설명을 쓴다. 뒤 두 줄은 09-21 검토 반영이다 —
        "이미 매출처리까지 됐는데 다시 누르면 두 번 되나" 를 몰라 다시 실행을 못 했다.
        """
        # **맨 뒤** 실패 단계 — 앞의 실패는 ERPia 알림을 닫고 넘어간 것일 수 있다 (09-22 검토)
        text = next((run.error for run in reversed(self.runs)
                     if run.state == steps.FAILED and run.error), "") or self.describe(exc)
        done = [run for run in self.runs if run.state == steps.DONE]
        # 멈춘 단계와 그 뒤 — **하지 못한 것** (09-21 2차 검토: 오늘 출고가 막혔는지부터 알아야 한다)
        left = [run for run in self.runs if run.state in (steps.FAILED, steps.PENDING)]
        if not done:
            return text + "\n아직 아무것도 하지 않았습니다 — 고친 뒤 그대로 다시 실행하면 됩니다."
        text += f"\n이미 끝난 것: {', '.join(run.step.name for run in done)}"
        if left:
            text += f"\n하지 못한 것: {', '.join(run.step.name for run in left)}"
        # 멈춘 단계도 다시 돈다 — 그 단계의 안내도 붙인다 (예: 물류대기 도중 → 건 보류는 그대로).
        notes = [self.rerun_notes[run.step.id] for run in (*done, *left)
                 if run.step.id in self.rerun_notes]
        if notes:
            text += "\n다시 실행하면: " + " ".join(dict.fromkeys(notes))
        return text

    def notice(self, text: str) -> None:
        """사람이 **지금 알아야 할 것** (UAC 동의 창 등). 상태줄과 사용자 로그에 같이 남긴다."""
        user.warning("%s", text)
        self.status(text)

    def attend(self, text: str) -> None:
        """**사람이 챙길 일** 하나. 같은 글은 한 번만. 사용자 로그에도 남긴다.

        로그에는 **어느 단계의 일인지** 앞에 붙인다 — 단계 시작 줄이 없어서 끝 줄보다 먼저
        찍히면 앞 단계의 일로 읽혔다 (09-21 2차 검토).
        """
        if text and text not in self.attention:
            self.attention.append(text)
            user.warning("%s확인할 것 — %s", self.running_label(), text)

    def running_label(self) -> str:
        """지금 도는 단계를 `[2/4 엑셀수집] ` 꼴로 (큰 단계가 없으면 `[7/11 엑셀수집] `). 없으면 빈 문자열."""
        run = next((run for run in self.runs if run.state == steps.RUNNING), None)
        return f"[{run.where} {run.step.name}] " if run else ""

    def count(self, step: Step, *, total: int | None = None,
              done: int | None = None, ok: int | None = None,
              failed: int | None = None, activity: str | None = None) -> None:
        """이 단계 안의 **건수**를 알린다. 준 것만 바꾼다.

        화면이 "42 / 120건", 단계별 진행률, 성공·실패 개수를 그리는 근거다.

        ★ **아는 값만 준다.** 안 주면 0 으로 남고, 화면은 0 이면 아예
          그리지 않는다. 그럴듯한 숫자를 만들어 넣으면 사람이 그것을 믿는다.

        진행 중이 아닐 때 불러도 된다. 단계가 끝난 뒤 결과에서 세어
          `count(step, ok=..., failed=...)` 로 넣는 쓰임이 많다.
        """
        run = self._run_for(step)
        if total is not None:
            run.total_items = max(int(total), 0)
        if done is not None:
            run.done_items = max(int(done), 0)
        if ok is not None:
            run.ok_items = max(int(ok), 0)
        if failed is not None:
            run.failed_items = max(int(failed), 0)
        if activity is not None:
            run.activity = activity
        # 상태줄은 건드리지 않는다 — 건수는 대시보드 몫이다. 상태줄까지 고치면 끝난 뒤의
        # 결과가 "진행 중 (…올림)" 으로 찍혔다 (09-21 검토).
        self._notify(run, status=False)

    def note(self, step: Step, detail: str) -> None:
        """진행 중인 단계의 **세부 진행**을 갱신한다 (예: 엑셀 2/3).

        상태는 그대로 진행 중이다. 오래 걸리는 단계에서 화면이 멈춘 것처럼
        보이지 않게 하려는 것이다.
        """
        run = self._run_for(step)
        run.progress = detail
        if run.state == steps.RUNNING:
            self._notify(run)

    # --- 기록 --------------------------------------------------------------
    def report(self) -> list[dict]:
        """단계별 결과. `Result.steps` 에 그대로 담는다."""
        return [run.as_dict() for run in self.runs]

    def unfinished(self) -> list[Step]:
        """아직 끝나지 않은 단계. 재개 지점을 고를 때 쓴다."""
        return [run.step for run in self.runs if run.state not in steps.FINISHED]

    # --- 내부 -------------------------------------------------------------
    def _run_for(self, step: Step) -> StepRun:
        for run in self.runs:
            if run.step.id == step.id:
                return run
        # 계획에 없던 단계다. 기록은 남기되 막지 않는다 - 진행 표시가 틀리는 것이
        # 흐름을 세우는 것보다 낫다.
        log.debug("계획에 없는 단계다: %s", step.id)
        run = StepRun(step=step, index=len(self.runs) + 1,
                      total=max(len(self.runs) + 1, len(self.plan)))
        self.runs.append(run)
        return run

    def _notify(self, run: StepRun, status: bool = True) -> None:
        event = run.event()
        if self.on_step is not None:
            self.on_step(event)
        if status and run.state in STATUS_STATES:
            self.status(event.one_line())


@dataclass
class Result:
    summary: str
    target: object | None = None
    details: dict = field(default_factory=dict)
    # 단계별 결과. 대시보드가 마지막 상태를 다시 그릴 때 쓴다.
    steps: list[dict] = field(default_factory=list)
