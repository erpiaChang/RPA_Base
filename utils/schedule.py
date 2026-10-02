r"""**언제 돌릴지**만 계산한다. 시계도 화면도 흐름도 모른다.

이 파일을 따로 둔 이유는 하나다 — **가짜 시각을 넣어 전수 확인할 수 있게**
하려는 것이다. `time.time()` 을 안에서 부르면 "밤 12시를 넘길 때" 나
"여름/겨울 시각이 바뀔 때" 를 시험할 방법이 없다. 그래서 지금 시각은
**항상 인자로 받는다.**

확인 도구: `tools/probe_schedule.py`

## 두 가지 방식 (사용자 확정 2026-09-16)

| 모드 | 뜻 | 예 |
| --- | --- | --- |
| `daily` | **정해진 날·시각**에 돈다. 예약 여러 개 (09-21) | `["매일 09:00", "월수금 13:00"]` |
| `interval` | 앞 회차가 **끝난 뒤** 정해진 시간이 지나면 돈다 | `120` 분마다 |

`daily` 예약 한 줄의 꼴 (`parse_slot`). **이 넷만 받는다.**

| 꼴 | 뜻 |
| --- | --- |
| `09:00` / `매일 09:00` | 매일 |
| `월수금 13:00` / `평일 09:00` / `주말 10:00` | 그 요일마다 |
| `매월 25일 09:00` | 매월 그날. 그날이 없는 달(31일 등)은 **건너뛴다** |
| `2026-09-25 14:00` | 그날 한 번. 지나면 돌지 않는다 |

끝에 **실행할 기능**을 붙일 수 있다 (09-21): `평일 09:00 mail,orders`. 없으면 창에서
고른 기능. 기능 이름은 여기서 모른다 — 확인은 `from_settings(known_modules=)`.
같은 시각에 여러 줄이 걸리면 기능을 **합쳐** 한 번 돈다 (`Plan.slots_at`).

★ `interval` 은 **끝난 시각**부터 센다. 시작 시각부터 세면, 흐름이 간격보다
  오래 걸릴 때 다음 차례가 이미 지나 있어 **쉬지 않고 계속 도는** 상태가 된다.
  실측으로 통합 흐름이 270초였고 최악을 알 수 없으니(자동수집 상한이 4시간이다)
  이쪽이 안전하다.

## 지나간 시각은 따라잡지 않는다

PC 를 꺼 뒀거나 화면이 잠겨 있어 `09:00` 을 놓쳤다면, 켜는 순간 그것을
**소급해서 돌리지 않는다.** 다음 시각을 기다린다.

주문 수집은 "그 시각의 일" 이 아니라 "지금까지 쌓인 것을 처리하는 일" 이라
밀린 회차를 몰아서 도는 것은 의미가 없고, 오히려 사람이 없는 새벽에 갑자기
실계정 저장이 도는 쪽이 위험하다.
"""
from __future__ import annotations

import datetime
import re
import time
from dataclasses import dataclass, field, replace

# 모드
DAILY = "daily"
INTERVAL = "interval"
MODES = (DAILY, INTERVAL)

# 간격의 상·하한. 아래로 두면 흐름이 끝나기도 전에 다음 차례가 오고,
# 위로 두면 하루에 한 번도 못 도는 값이 된다.
MIN_INTERVAL_MINUTES = 10
MAX_INTERVAL_MINUTES = 24 * 60

# 예약 개수 상한. 실수로 수십 개를 넣는 것을 막는다.
MAX_SLOTS = 12

# 요일 글자. `struct_time.tm_wday` 순서(월=0)다.
WEEKDAYS = "월화수목금토일"
EVERY_DAY = frozenset(range(7))
WEEKDAY_ALIASES = {"매일": EVERY_DAY, "평일": frozenset(range(5)),
                   "주말": frozenset({5, 6})}
# 다음 차례를 며칠 앞까지 찾나. `매월 31일` 은 두 달 가까이 비기도 한다.
LOOKAHEAD_DAYS = 400
SLOT_EXAMPLE = "매일 09:00 / 월수금 13:00 / 매월 25일 09:00 / 2026-09-25 14:00"
# 끝에 붙는 기능 id 목록 (`mail,orders`). 한글·공백이 없어 날 조건과 헷갈리지 않는다.
MODULES_RE = re.compile(r"[a-z_]+(?:,[a-z_]+)*")


def parse_time(text: str) -> tuple[int, int] | None:
    """`"09:00"` → `(9, 0)`. 읽을 수 없으면 None.

    **추측해서 고쳐 주지 않는다.** `"9시"` 나 `"0900"` 은 None 이다 —
    설정에 잘못 쓴 값을 조용히 받아들이면 사람은 자기가 넣은 시각에 도는
    줄 알고 기다린다.
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def format_time(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class Slot:
    """예약 한 줄. 날 조건은 셋 중 하나다 — 한 번(`date`) / 매월(`month_day`) / 요일."""

    hour: int
    minute: int
    weekdays: frozenset = EVERY_DAY               # 월=0
    month_day: int = 0                            # 매월 N일. 0 이면 안 본다
    date: tuple[int, int, int] | None = None      # 그날 한 번 (년, 월, 일)
    modules: tuple[str, ...] = ()                 # 실행할 기능 id. 비면 창에서 고른 기능

    def matches(self, local: time.struct_time) -> bool:
        if self.date is not None:
            return (local.tm_year, local.tm_mon, local.tm_mday) == self.date
        if self.month_day:
            return local.tm_mday == self.month_day
        return local.tm_wday in self.weekdays

    def days_text(self) -> str:
        """날 조건만. 화면 `반복` 칸과 저장 꼴이 같이 쓴다."""
        if self.date is not None:
            return "%04d-%02d-%02d" % self.date
        if self.month_day:
            return f"매월 {self.month_day}일"
        for name, days in WEEKDAY_ALIASES.items():
            if self.weekdays == days:
                return name
        return "".join(WEEKDAYS[day] for day in sorted(self.weekdays))

    def text(self) -> str:
        """저장하는 꼴. `parse_slot(slot.text()) == slot` 이다."""
        text = f"{self.days_text()} {format_time(self.hour, self.minute)}"
        return f"{text} {','.join(self.modules)}" if self.modules else text

    def due_at(self, stamp: float) -> bool:
        """`stamp` 가 이 예약의 차례인가 (그날·그 분)."""
        local = time.localtime(stamp)
        return ((local.tm_hour, local.tm_min) == (self.hour, self.minute)
                and self.matches(local))

    def next_after(self, now: float) -> float | None:
        """`now` 뒤의 첫 차례(epoch 초). 없으면(지난 날짜) None."""
        if self.date is not None:
            year, month, day = self.date
            when = time.mktime((year, month, day, self.hour, self.minute, 0,
                                0, 0, -1))
            return when if when > now else None
        local = time.localtime(now)
        for offset in range(LOOKAHEAD_DAYS):
            when = Plan._stamp(local, self.hour, self.minute, day_offset=offset)
            if when > now and self.matches(time.localtime(when)):
                return when
        return None


def parse_slot(text: str) -> Slot | None:
    """예약 한 줄을 읽는다. 머리말의 네 꼴이 아니면 None — **고쳐 읽지 않는다.**"""
    if not isinstance(text, str):
        return None
    tokens = text.split()
    chosen: tuple[str, ...] = ()
    if tokens and MODULES_RE.fullmatch(tokens[-1]):
        chosen = tuple(tokens.pop().split(","))
        if len(set(chosen)) != len(chosen):
            return None
    slot = _parse_when(tokens)
    return replace(slot, modules=chosen) if slot and chosen else slot


def _parse_when(tokens: list[str]) -> Slot | None:
    if len(tokens) == 1:
        tokens = ["매일", *tokens]
    if len(tokens) == 3 and tokens[0] == "매월":
        tokens = [f"{tokens[0]} {tokens[1]}", tokens[2]]
    if len(tokens) != 2:
        return None
    days, clock = tokens
    parsed = parse_time(clock)
    if parsed is None:
        return None
    hour, minute = parsed

    if days in WEEKDAY_ALIASES:
        return Slot(hour, minute, weekdays=WEEKDAY_ALIASES[days])
    if days.startswith("매월 ") and days.endswith("일"):
        number = days[3:-1]
        if number.isdigit() and 1 <= int(number) <= 31:
            return Slot(hour, minute, month_day=int(number))
        return None
    if len(days) == 10 and days[4] == "-" and days[7] == "-":
        try:
            day = datetime.date(int(days[:4]), int(days[5:7]), int(days[8:]))
        except ValueError:
            return None
        return Slot(hour, minute, date=(day.year, day.month, day.day))
    if days and all(char in WEEKDAYS for char in days) \
            and len(set(days)) == len(days):
        return Slot(hour, minute,
                    weekdays=frozenset(WEEKDAYS.index(char) for char in days))
    return None


def when_text(stamp: float, now: float) -> str:
    """다음 실행 시각을 한 줄로. 오늘이면 `13:00`, 아니면 `09-22(화) 09:00`."""
    local = time.localtime(stamp)
    if time.localtime(now)[:3] == local[:3]:
        return time.strftime("%H:%M", local)
    return (time.strftime("%m-%d", local) + f"({WEEKDAYS[local.tm_wday]}) "
            + time.strftime("%H:%M", local))


@dataclass
class Plan:
    """예약 규칙. **값이다.** 여기서 아무것도 실행하지 않는다."""

    enabled: bool = False
    mode: str = DAILY
    # `daily` 에서 쓴다. 예약 줄 목록 (`parse_slot` 의 꼴)
    times: tuple[str, ...] = ()
    # `interval` 에서 쓴다. 분. 설정에서 읽은 Plan 은 비어 있을 수 있다 (기본값 없음, 10-02)
    interval_minutes: int | None = 60
    # 읽다가 버린 값. 화면에 "이건 못 읽었다" 고 보여 주려고 든다
    rejected: tuple[str, ...] = field(default_factory=tuple)

    # --- 유효성 -------------------------------------------------------
    def problems(self) -> list[str]:
        """사람에게 그대로 보여 줄 문제 목록. 비어 있으면 쓸 수 있다."""
        out: list[str] = []
        if self.mode not in MODES:
            out.append("예약 방식(정해진 날·시각 / 정해진 간격)을 고르지 않았다" if not self.mode
                       else f"모르는 모드다: {self.mode!r}")
            return out
        if self.mode == DAILY:
            # 예약이 없는 것은 문제가 아니다 — 그냥 돌지 않는다 (사용자 확정 09-22)
            if len(self.times) > MAX_SLOTS:
                out.append(f"예약이 너무 많다 ({len(self.times)}개). "
                           f"{MAX_SLOTS}개까지만 둔다.")
            for item in self.times:
                if parse_slot(item) is None:
                    out.append(f"예약 형식이 아니다: {item!r} (예: {SLOT_EXAMPLE})")
        elif self.interval_minutes is None:
            out.append(f"간격(분)을 넣지 않았다 ({MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES}분)")
        else:
            if not (MIN_INTERVAL_MINUTES <= self.interval_minutes
                    <= MAX_INTERVAL_MINUTES):
                out.append(f"간격이 범위를 벗어났다: {self.interval_minutes}분 "
                           f"({MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES}분)")
        for item in self.rejected:
            out.append(f"읽지 못해 버린 값: {item!r}")
        return out

    @property
    def usable(self) -> bool:
        return self.enabled and not self.problems()

    def slots(self) -> list[Slot]:
        """읽을 수 있는 예약만. 못 읽는 줄은 `problems()` 가 알린다."""
        return [slot for slot in (parse_slot(text) for text in self.times)
                if slot is not None]

    def slots_at(self, stamp: float) -> list[Slot]:
        """`stamp` 에 걸린 예약 줄. `interval` 이면 빈 목록 (기능은 창에서 고른 것)."""
        if self.mode != DAILY:
            return []
        return [slot for slot in self.slots() if slot.due_at(stamp)]

    def describe(self) -> str:
        """화면에 한 줄로. 예: `매일 09:00, 13:00 / 월수금 17:00` / `120분 간격`"""
        if self.mode == DAILY:
            groups: dict[str, list[str]] = {}
            for slot in self.slots():
                groups.setdefault(slot.days_text(), []).append(
                    format_time(slot.hour, slot.minute))
            if not groups:
                return "시각 없음"
            return " / ".join(f"{days} {', '.join(clocks)}"
                              for days, clocks in groups.items())
        if self.mode != INTERVAL:
            return "예약 방식 없음"
        return f"{self.interval_minutes}분 간격" if self.interval_minutes is not None else "간격 없음"

    # --- 다음 차례 ----------------------------------------------------
    def next_at(self, now: float, last_finished: float = 0.0) -> float | None:
        """다음 실행 시각(epoch 초). 돌릴 수 없으면 None.

        `last_finished` 는 **앞 회차가 끝난 시각**이다. `interval` 모드에서만
        쓴다. 0 이면 아직 한 번도 안 돌았다는 뜻이고, 그때는 `now` 부터 센다
        (창을 띄운 직후에 곧바로 도는 것을 막는다 — 사람이 값을 확인할 틈도
        없이 실계정이 도는 것은 사고다).
        """
        if not self.usable:
            return None
        if self.mode == INTERVAL:
            step = self.interval_minutes * 60
            base = last_finished or now
            # 이미 지난 차례(건너뛴 뒤·간격을 줄인 뒤)는 지금부터 다시 센다 — 지난 시각을 주면
            # 매초 건너뜀이 쌓이고 풀리는 순간 예고 없이 돈다 (10-02)
            return base + step if base + step > now else now + step
        return self._next_daily(now)

    def _next_daily(self, now: float) -> float | None:
        """예약 줄마다 다음 차례를 구해 가장 빠른 것. 남은 것이 없으면 None."""
        stamps = [when for when in (slot.next_after(now) for slot in self.slots())
                  if when is not None]
        return min(stamps) if stamps else None

    @staticmethod
    def _stamp(local: time.struct_time, hour: int, minute: int,
               day_offset: int) -> float:
        # ★ **날짜를 초로 더해 계산하지 않는다.** `now + N일` 로 만들면 서머타임
        #   전환일에 한 시간이 밀린다. `mktime` 에 날짜·시각을 그대로 주면
        #   그 지역의 실제 시각으로 맞춰 준다(넘친 날짜도 다음 달로 맞춘다).
        parts = list(local)
        parts[3] = hour
        parts[4] = minute
        parts[5] = 0
        parts[2] = local.tm_mday + day_offset
        parts[8] = -1        # tm_isdst = -1 : 서머타임 여부를 OS 가 판단한다
        return time.mktime(time.struct_time(parts))


def from_settings(values: dict, known_modules: tuple[str, ...] | None = None) -> Plan:
    """설정 딕셔너리에서 `Plan` 을 만든다. **틀린 값은 버리고 알린다.**

    설정 파일은 사람이 손으로 고치는 것이라 `"9시"` 같은 값이 들어올 수 있다.
    그것을 0시로 읽어 버리면 새벽에 실계정이 돈다. 그래서 **버리고 기록한다.**

    `known_modules` 를 주면 모르는 기능이 붙은 줄도 버리고, 기능을 그 순서로 맞춘다.
    """
    # ★ 기본값이 없다 (10-02) — 비거나 모르는 방식을 몰래 daily 로 돌리지 않는다. Plan.problems() 가 알린다
    bad: list[str] = []
    mode = str(values.get("auto_run_mode") or "").strip().lower()
    if mode and mode not in MODES:
        bad.append(mode)
        mode = ""

    raw = values.get("auto_run_times") or []
    if isinstance(raw, str):
        # `"09:00, 13:00"` 처럼 한 줄로 쓴 경우도 받아 준다
        raw = [piece for piece in raw.replace(";", ",").split(",") if piece.strip()]

    good: list[Slot] = []
    for item in raw:
        text = item if isinstance(item, str) else str(item)
        slot = parse_slot(text)
        if slot is not None and known_modules is not None and slot.modules:
            if not set(slot.modules) <= set(known_modules):
                slot = None
            else:
                slot = replace(slot, modules=tuple(
                    m for m in known_modules if m in slot.modules))
        if slot is None:
            bad.append(text)
        elif slot not in good:          # 같은 예약을 두 번 두면 한 번만 돈다
            good.append(slot)
    good.sort(key=lambda slot: (slot.hour, slot.minute, slot.text()))

    given = values.get("auto_run_interval_minutes")
    minutes: int | None = None
    if given not in (None, "") and not isinstance(given, bool):
        try:
            minutes = int(given)
        except (TypeError, ValueError):
            bad.append(str(given))

    return Plan(
        enabled=bool(values.get("auto_run_enabled")),
        mode=mode,
        times=tuple(slot.text() for slot in good),
        interval_minutes=minutes,
        rejected=tuple(bad),
    )


def to_settings(plan: Plan) -> dict:
    """`Plan` 을 설정에 저장할 모양으로. `from_settings` 의 반대다."""
    return {
        "auto_run_enabled": bool(plan.enabled),
        "auto_run_mode": plan.mode or None,
        "auto_run_times": list(plan.times),
        "auto_run_interval_minutes": None if plan.interval_minutes is None else int(plan.interval_minutes),
    }


def countdown(seconds: float) -> str:
    """남은 시간을 사람이 읽는 꼴로. 예: `2일 3시간` / `2시간 5분` / `40초`"""
    total = max(int(seconds), 0)
    if total < 60:
        return f"{total}초"
    minutes, _ = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}일 {hours}시간" if hours else f"{days}일"
    if hours:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    return f"{minutes}분"
