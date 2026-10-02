r"""단계의 **틀**. 기능 이름을 적지 않는다.

`orchestrator/common.py` 와 같은 이유다 — 이 파일은 모든 배포본에 들어가므로
여기 남긴 문구는 전부 흔적이 된다. 실제 단계 목록은 기능별로 나눠 둔다
(`steps_collect.py` / `steps_erpia.py`. `config/fields_*.py` 와 같은 방식이다).

단계마다 신원(`Step.id`)·이름·**재진입 정책**(`Step.reentry`)을 값으로 들고 다닌다 — 화면이 몇 번째 단계인지,
어디서 멈췄는지, 다시 돌려도 되는지(매출처리는 전표를 만들어 되돌릴 수 없다) 알 수 있게.
화면의 큰 단계(`Step.stage` — 모듈 이름)는 `orchestrator/modules.plan` 이 붙인다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


# --- 재진입 정책 : 이 단계를 **다시** 실행해도 되는가 ----------------------
#
# 구간 실행(리모컨)의 시작점으로 고를 때 무엇을 확인받아야 하는지가 여기서 정해진다.
SAFE = "safe"                   # 몇 번이든 무해하다
CONDITIONAL = "conditional"     # 보호 장치가 있거나 대가가 크다. 확인을 받는다
WRITE = "write"                 # 저장 상태가 바뀐다. 확인을 받는다
IRREVERSIBLE = "irreversible"   # 되돌릴 수 없다. 강한 확인을 받는다

REENTRY_LABELS = {
    SAFE: "안전",
    CONDITIONAL: "조건부",
    WRITE: "쓰기",
    IRREVERSIBLE: "되돌릴 수 없다",
}

# 확인 없이 시작점으로 고를 수 있는 것은 **안전**뿐이다.
NEEDS_CONFIRM = (CONDITIONAL, WRITE, IRREVERSIBLE)


# --- 단계의 상태 -----------------------------------------------------------
PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"       # 할 일이 없어 건너뛴 것. 실패가 아니다
FAILED = "failed"
CANCELLED = "cancelled"   # 사용자가 중단했다. 실패가 아니다

STATE_LABELS = {
    PENDING: "실행 전",     # "대기" 는 "물류대기 대기" 로 읽혔다 (09-21 검토)
    RUNNING: "진행 중",
    DONE: "완료",
    SKIPPED: "건너뜀",
    FAILED: "실패",
    CANCELLED: "중단",
}

# 끝난 것으로 보는 상태. 재개 지점을 고를 때 쓴다.
FINISHED = (DONE, SKIPPED)


@dataclass(frozen=True)
class Step:
    """단계 하나. **값이다.** 여기에 실행 코드를 두지 않는다."""

    id: str
    name: str
    reentry: str
    # 다시 실행하면 무슨 일이 일어나는가. **사람에게 그대로 보여 줄 문장**이다.
    replay: str = ""
    # 사람이 느끼는 **큰 단계** 이름 (09-29 사용자 요청 — 11단계가 아니라 4단계로 보인다).
    # 계획을 짜는 쪽이 붙인다 (`orchestrator/modules.py`). 비면 큰 단계 없이 예전처럼 그린다
    stage: str = ""

    @property
    def needs_confirm(self) -> bool:
        return self.reentry in NEEDS_CONFIRM

    @property
    def reentry_label(self) -> str:
        return REENTRY_LABELS.get(self.reentry, self.reentry)


@dataclass(frozen=True)
class StepEvent:
    """단계가 바뀔 때 화면에 보내는 **한 장의 사진.**

    ★ 얼려서 보낸다. GUI 는 `after(0, ...)` 로 나중에 그리는데, 살아 있는
      객체를 넘기면 그 사이에 값이 바뀌어 **지나간 상태를 다른 값으로** 그린다.
    """

    step_id: str
    name: str
    index: int          # 1부터
    total: int
    state: str
    detail: str = ""
    elapsed: float = 0.0
    # ★ **벽시계** 시각 (`time.time()`). 경과 초와 따로 든다.
    #   경과만으로는 "언제 끝났나" 를 말할 수 없는데, 사람이 대시보드에서
    #   가장 먼저 찾는 것이 그것이다 (사용자 요청 2026-09-16).
    #   단조시계(`time.monotonic`)는 경과를 재는 데만 쓴다 — 벽시계는 시스템
    #   시각이 바뀌면 튀어서 경과 계산에 쓰면 안 된다.
    started_at: float = 0.0
    finished_at: float = 0.0
    # --- 이 단계 안의 **건수**. 흐름이 알 때만 채운다 ---------------
    #
    # ★ 모르면 0 으로 둔다. 화면은 0 이면 아예 그리지 않는다.
    #   **없는 숫자를 만들지 않는다** — 42/120 같은 값을 보여 주려고
    #   추정하면 사람이 그것을 믿는다.
    total_items: int = 0     # 이 단계가 다룰 전체 건수
    done_items: int = 0      # 지금까지 끝낸 건수
    ok_items: int = 0        # 그중 성공
    failed_items: int = 0    # 그중 실패
    # 지금 무엇을 하고 있는지 한 문장. 예: "사이트C_1.xlsx 를 올리는 중"
    activity: str = ""
    # 실패했을 때의 **사람 말** 문구 (`StepRun.error`). 서버 보고(`orchestrator/telemetry.py`)가
    # 실시간으로 싣는다 (09-22). 예외 원문(`error_detail`)은 싣지 않는다.
    error: str = ""
    # ★ 재진입 정책을 **사진에 함께 담는다.** 화면이 "이 단계부터 다시 시작해도
    #   되나" 를 판단할 때 필요한데, 공용 GUI 코드(`gui/common.py`)는 기능별
    #   단계 표를 import 할 수 없다(배포본마다 다른 기능이 섞이면 안 된다).
    reentry: str = SAFE
    replay: str = ""
    # ★ 이 단계의 **상세 표**. 화면은 이것을 그대로 그린다.
    #   공용 GUI 코드(`gui/common.py`)는 기능을 모른다 — 엑셀 목록인지 보류
    #   목록인지 알 수 없고, 알아서도 안 된다(배포본마다 기능이 갈린다).
    #   그래서 **기능을 아는 흐름 쪽이 표를 만들어 넣고**, 화면은 컬럼 이름과
    #   줄만 받아 그린다. 비어 있으면 상세가 없는 단계다.
    columns: tuple = ()
    rows: tuple = ()
    # 큰 단계 (`Step.stage`). 몇 번째 큰 단계인지와 전체 개수 — 없으면 0
    stage: str = ""
    stage_index: int = 0
    stage_total: int = 0

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def where(self) -> str:
        """사람에게 보일 위치. 큰 단계가 있으면 `2/4`, 없으면 `9/11` (세부 단계 번호)."""
        if self.stage_total:
            return f"{self.stage_index}/{self.stage_total}"
        return f"{self.index}/{self.total}"

    @property
    def reentry_label(self) -> str:
        return REENTRY_LABELS.get(self.reentry, self.reentry)

    @property
    def needs_confirm(self) -> bool:
        return self.reentry in NEEDS_CONFIRM

    @property
    def ratio(self) -> float | None:
        """이 단계의 진행률(0~1). **셀 수 없으면 None** 이다.

        None 과 0.0 은 다르다 — 전자는 "모른다", 후자는 "아직 하나도 못 했다".
        화면은 모르는 것을 막대로 그리지 않는다.
        """
        if self.total_items <= 0:
            return None
        return min(self.done_items / self.total_items, 1.0)

    @property
    def waiting_items(self) -> int:
        """아직 손대지 않은 건수. 셀 수 없으면 0."""
        if self.total_items <= 0:
            return 0
        return max(self.total_items - self.done_items, 0)

    def clock(self, with_date: bool = False) -> str:
        """이 단계를 대표하는 시각. 끝났으면 끝난 시각, 돌고 있으면 시작 시각.

        아직 시작하지 않았으면 빈 문자열이다 — **없는 시각을 지어내지 않는다.**
        """
        stamp = self.finished_at or self.started_at
        if not stamp:
            return ""
        shape = "%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S"
        return time.strftime(shape, time.localtime(stamp))

    def one_line(self) -> str:
        """상태줄 한 줄. 예: `2/4 매출처리 — 진행 중` (큰 단계가 없으면 `9/11 매출처리 — 진행 중`)"""
        text = f"{self.where} {self.name} — {self.state_label}"
        return f"{text} ({self.detail})" if self.detail else text


def stage_numbers(plan: list[Step]) -> dict[str, tuple[int, int]]:
    """단계 id → (몇 번째 큰 단계, 큰 단계 수). 나온 순서대로 센다. 큰 단계가 없는 계획이면 빈 dict."""
    names: list[str] = []
    for item in plan:
        if item.stage and item.stage not in names:
            names.append(item.stage)
    if not names:
        return {}
    return {item.id: (names.index(item.stage) + 1, len(names)) for item in plan if item.stage}


def preview(plan: list[Step]) -> list[StepEvent]:
    """아직 실행하지 않은 계획을 화면에 **미리** 그릴 사진들.

    구간 실행(리모컨)을 하려면 사용자가 **실행 전에** 단계를 골라야 한다.
    한 번 돌려 본 뒤에야 목록이 보이면 고를 수가 없다.
    """
    numbers = stage_numbers(plan)
    return [StepEvent(step_id=item.id, name=item.name, index=position,
                      total=len(plan), state=PENDING,
                      reentry=item.reentry, replay=item.replay, stage=item.stage,
                      stage_index=numbers.get(item.id, (0, 0))[0],
                      stage_total=numbers.get(item.id, (0, 0))[1])
            for position, item in enumerate(plan, start=1)]


def by_id(plan: list[Step], step_id: str) -> Step | None:
    for item in plan:
        if item.id == step_id:
            return item
    return None


def index_of(plan: list[Step], step_id: str) -> int:
    """`plan` 안에서 몇 번째인가(0부터). 없으면 -1."""
    for position, item in enumerate(plan):
        if item.id == step_id:
            return position
    return -1
