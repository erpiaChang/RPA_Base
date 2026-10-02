r"""[조사 도구 - 읽기 전용] 예약 시각 계산을 확인한다.

**대상 프로그램을 건드리지 않는다.** 가짜 시각만 넣어 본다.

    .venv\Scripts\python.exe -m tools.probe_schedule

## 왜 이 도구가 있나 (2026-09-16)

자동 실행은 **사람이 없을 때** 돈다. 시각 계산이 하나 틀리면 새벽에 실계정
저장이 돌거나, 반대로 하루 종일 한 번도 안 돈다. 둘 다 사람이 뒤늦게 안다.

그래서 `utils/schedule.py` 는 지금 시각을 **인자로 받게** 만들었다.
여기서 밤 12시 넘김, 서머타임, 잘못 쓴 설정값을 가짜 시각으로 시험한다.
"""
from __future__ import annotations

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

from utils import schedule  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def at(year: int, month: int, day: int, hour: int, minute: int) -> float:
    """그 지역 시각으로 epoch 초. `tm_isdst=-1` 은 OS 가 판단하게 둔다."""
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def clock(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


def check_slots() -> list[bool]:
    """정해진 **날**·시각 예약 여러 개 (사용자 요청 2026-09-21)."""
    out: list[bool] = []
    log.info("▶ 예약 한 줄 읽기 — 네 꼴만 받는다")
    for text, days in (("09:00", "매일"), ("매일 09:00", "매일"), ("평일 09:00", "평일"),
                       ("주말 10:00", "주말"), ("월수금 13:00", "월수금"),
                       ("금월 13:00", "월금"), ("매월 25일 09:00", "매월 25일"),
                       ("2026-09-25 14:00", "2026-09-25")):
        slot = schedule.parse_slot(text)
        out.append(check(f"`{text}` → {days}", slot is not None and slot.days_text() == days,
                         slot.text() if slot else "None"))
    for bad in ("월,수 09:00", "월월 09:00", "매월 32일 09:00", "매월 25 09:00",
                "2026-02-30 09:00", "내일 09:00", "월수금", "09:00 월"):
        out.append(check(f"{bad!r} 은 **받지 않는다**", schedule.parse_slot(bad) is None))
    for text in ("월수금 13:00", "매월 25일 09:00", "2026-09-25 14:00", "평일 09:00"):
        slot = schedule.parse_slot(text)
        out.append(check(f"저장 꼴 되돌리기 `{text}`",
                         schedule.parse_slot(slot.text()) == slot, slot.text()))

    log.info("▶ 요일 / 매월 / 날짜 — 다음 차례")
    # 2026-09-16 은 수요일이다.
    wed = at(2026, 9, 16, 10, 0)
    got = schedule.parse_slot("월금 09:00").next_after(wed)
    out.append(check("수 10:00 에 `월금 09:00` → 금 09-18", clock(got) == "2026-09-18 09:00",
                     clock(got)))
    got = schedule.parse_slot("평일 09:00").next_after(at(2026, 9, 18, 9, 0))
    out.append(check("★ 금 09:00 정각 → 주말을 건너 월 09-21", clock(got) == "2026-09-21 09:00",
                     clock(got)))
    got = schedule.parse_slot("주말 10:00").next_after(wed)
    out.append(check("주말 → 토 09-19", clock(got) == "2026-09-19 10:00", clock(got)))
    got = schedule.parse_slot("매월 31일 09:00").next_after(at(2026, 9, 1, 0, 0))
    out.append(check("★ `매월 31일` 은 31일이 없는 9월을 건너뛴다 → 10-31",
                     clock(got) == "2026-10-31 09:00", clock(got)))
    got = schedule.parse_slot("매월 29일 09:00").next_after(at(2027, 2, 1, 0, 0))
    out.append(check("★ 2월 29일이 없는 해는 3-29", clock(got) == "2027-03-29 09:00",
                     clock(got)))
    got = schedule.parse_slot("매월 1일 09:00").next_after(at(2026, 12, 15, 0, 0))
    out.append(check("해를 넘긴다 → 2027-01-01", clock(got) == "2027-01-01 09:00", clock(got)))
    once = schedule.parse_slot("2026-09-25 14:00")
    out.append(check("날짜 지정 — 그날 그 시각", clock(once.next_after(wed)) == "2026-09-25 14:00"))
    out.append(check("★ 날짜 지정 — 지나면 None (다시 돌지 않는다)",
                     once.next_after(at(2026, 9, 25, 14, 0)) is None))

    log.info("▶ 여러 예약 — 가장 빠른 것")
    plan = schedule.from_settings({"auto_run_enabled": True, "auto_run_mode": "daily", "auto_run_times": [
        "월수금 13:00", "2026-09-17 08:00", "매월 16일 11:00"]})
    got = plan.next_at(wed)
    out.append(check("수 10:00 → 같은 날 `매월 16일 11:00`", clock(got) == "2026-09-16 11:00",
                     clock(got)))
    got = plan.next_at(at(2026, 9, 16, 13, 0))
    out.append(check("수 13:00 정각 → 목 08:00 (한 번)", clock(got) == "2026-09-17 08:00",
                     clock(got)))
    past = schedule.from_settings({"auto_run_enabled": True, "auto_run_mode": "daily",
                                   "auto_run_times": ["2026-09-01 09:00"]})
    out.append(check("★ 지난 날짜만 있으면 None — 몰아서 돌지 않는다",
                     past.next_at(wed) is None and past.usable))
    out.append(check("같은 예약은 한 번만", len(schedule.from_settings({
        "auto_run_times": ["월금 09:00", "금월 09:00"]}).times) == 1))
    out.append(check("예약은 12개까지", "12개까지" in " ".join(schedule.Plan(
        enabled=True, times=tuple(f"매일 {h:02d}:00" for h in range(13))).problems())))

    log.info("▶ 화면 글")
    out.append(check("같은 날 조건끼리 묶는다", schedule.Plan(times=(
        "매일 09:00", "매일 13:00", "월수금 17:00")).describe()
        == "매일 09:00, 13:00 / 월수금 17:00"))
    out.append(check("오늘이면 시각만", schedule.when_text(at(2026, 9, 16, 13, 0), wed) == "13:00"))
    out.append(check("다른 날이면 날짜·요일", schedule.when_text(at(2026, 9, 18, 9, 0), wed)
                     == "09-18(금) 09:00", schedule.when_text(at(2026, 9, 18, 9, 0), wed)))
    out.append(check("하루 넘게 남으면 `2일 3시간`",
                     schedule.countdown(2 * 86400 + 3 * 3600) == "2일 3시간"))
    return out


def check_slot_modules() -> list[bool]:
    """예약마다 실행할 기능 (사용자 요청 2026-09-21)."""
    out: list[bool] = []
    log.info("▶ 예약 줄 끝의 기능")
    slot = schedule.parse_slot("평일 09:00 mail,orders")
    out.append(check("`평일 09:00 mail,orders` → 기능 둘",
                     slot is not None and slot.modules == ("mail", "orders")
                     and slot.days_text() == "평일", repr(slot)))
    out.append(check("저장 꼴 되돌리기", slot is not None
                     and schedule.parse_slot(slot.text()) == slot, slot.text() if slot else ""))
    slot = schedule.parse_slot("매월 25일 18:00 logistics")
    out.append(check("`매월 25일 18:00 logistics`", slot is not None
                     and slot.month_day == 25 and slot.modules == ("logistics",)))
    out.append(check("기능 없는 예전 줄은 기능이 비어 있다 (창에서 고른 것)",
                     schedule.parse_slot("09:00").modules == ()))
    for bad in ("09:00 mail,mail", "mail", "09:00 mail,", "09:00 Mail", "월 09:00 mail orders"):
        out.append(check(f"{bad!r} 은 **받지 않는다**", schedule.parse_slot(bad) is None))

    known = ("mail", "orders", "logistics_wait", "logistics")
    got = schedule.from_settings({"auto_run_mode": "daily", "auto_run_times": [
        "평일 09:00 logistics,mail", "매일 10:00 billing", "매일 11:00"]}, known_modules=known)
    out.append(check("★ 기능을 실행 순서로 맞춘다", "평일 09:00 mail,logistics" in got.times,
                     str(got.times)))
    out.append(check("★ 모르는 기능이 붙은 줄은 **버리고 알린다**",
                     got.rejected == ("매일 10:00 billing",) and not got.usable,
                     str(got.rejected)))
    out.append(check("같은 날·시각이라도 기능이 다르면 둘 다 둔다", len(schedule.from_settings({
        "auto_run_times": ["매일 09:00 mail", "매일 09:00 logistics"]}).times) == 2))

    log.info("▶ 그 시각에 걸린 예약 (기능을 합칠 대상)")
    plan = schedule.from_settings({"auto_run_enabled": True, "auto_run_mode": "daily", "auto_run_times": [
        "매일 09:00 mail", "월수금 09:00 logistics", "평일 13:00 orders"]})
    wed_9 = at(2026, 9, 16, 9, 0)
    due = plan.slots_at(wed_9)
    out.append(check("수 09:00 → `매일 09:00` 과 `월수금 09:00` 둘",
                     sorted(m for s in due for m in s.modules) == ["logistics", "mail"],
                     str([s.text() for s in due])))
    due = plan.slots_at(at(2026, 9, 17, 9, 0))
    out.append(check("목 09:00 → `매일 09:00` 하나", [s.text() for s in due]
                     == ["매일 09:00 mail"], str([s.text() for s in due])))
    out.append(check("시각이 다르면 없다", plan.slots_at(at(2026, 9, 16, 9, 1)) == []))
    out.append(check("다음 차례의 예약이 걸린다 (next_at → slots_at)",
                     [s.text() for s in plan.slots_at(plan.next_at(at(2026, 9, 16, 10, 0)))]
                     == ["평일 13:00 orders"]))
    gap = schedule.Plan(enabled=True, mode=schedule.INTERVAL, times=("매일 09:00 mail",))
    out.append(check("간격 모드는 걸린 예약이 없다 (창에서 고른 기능)", gap.slots_at(wed_9) == []))
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 시각 문자열 읽기 — 추측해서 고쳐 주지 않는다")
    results.append(check("`09:00` 을 읽는다",
                         schedule.parse_time("09:00") == (9, 0)))
    results.append(check("`9:5` 도 읽는다", schedule.parse_time("9:5") == (9, 5)))
    results.append(check("앞뒤 공백을 걷어낸다",
                         schedule.parse_time(" 13:30 ") == (13, 30)))
    for bad in ("9시", "0900", "25:00", "09:60", "", "09:00:00", None, 900):
        results.append(check(f"{bad!r} 은 **받지 않는다**",
                             schedule.parse_time(bad) is None))

    log.info("▶ 매일 정해진 시각 — 오늘 남은 것 중 가장 빠른 것")
    plan = schedule.Plan(enabled=True, mode=schedule.DAILY,
                         times=("09:00", "13:00", "17:00"))
    now = at(2026, 9, 16, 10, 30)
    nxt = plan.next_at(now)
    results.append(check("10:30 이면 다음은 13:00",
                         clock(nxt) == "2026-09-16 13:00", clock(nxt)))
    nxt = plan.next_at(at(2026, 9, 16, 8, 59))
    results.append(check("08:59 이면 다음은 09:00",
                         clock(nxt) == "2026-09-16 09:00", clock(nxt)))

    log.info("▶ ★ 밤 12시를 넘긴다")
    nxt = plan.next_at(at(2026, 9, 16, 23, 30))
    results.append(check("★ 23:30 이면 **다음 날** 09:00",
                         clock(nxt) == "2026-09-17 09:00", clock(nxt)))
    nxt = plan.next_at(at(2026, 9, 30, 23, 59))
    results.append(check("★ 월말 23:59 이면 **다음 달 1일** 09:00",
                         clock(nxt) == "2026-10-01 09:00", clock(nxt)))
    nxt = plan.next_at(at(2026, 12, 31, 23, 59))
    results.append(check("★ 12월 31일이면 **해가 넘어간다**",
                         clock(nxt) == "2027-01-01 09:00", clock(nxt)))

    log.info("▶ 정확히 그 시각일 때 — 지금은 지나간 것으로 본다")
    nxt = plan.next_at(at(2026, 9, 16, 13, 0))
    results.append(check("13:00 정각이면 다음은 17:00 (같은 회차를 두 번 안 돈다)",
                         clock(nxt) == "2026-09-16 17:00", clock(nxt)))

    log.info("▶ 시각 하나만 둔 경우")
    one = schedule.Plan(enabled=True, mode=schedule.DAILY, times=("09:00",))
    nxt = one.next_at(at(2026, 9, 16, 9, 1))
    results.append(check("하나뿐이면 다음 날 같은 시각",
                         clock(nxt) == "2026-09-17 09:00", clock(nxt)))

    log.info("▶ 간격 모드 — **끝난 시각**부터 센다")
    gap = schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                        interval_minutes=120)
    started = at(2026, 9, 16, 9, 0)
    finished = at(2026, 9, 16, 9, 30)      # 30분 걸렸다
    nxt = gap.next_at(started + 60, last_finished=finished)
    results.append(check("★ 끝난 시각 09:30 + 120분 = 11:30 "
                         "(시작 시각부터 세면 11:00 이라 쉬는 시간이 줄어든다)",
                         clock(nxt) == "2026-09-16 11:30", clock(nxt)))
    nxt = gap.next_at(at(2026, 9, 16, 9, 0), last_finished=0.0)
    results.append(check("★ 한 번도 안 돌았으면 **지금부터** 센다 "
                         "(창 띄운 직후 곧바로 도는 것을 막는다)",
                         clock(nxt) == "2026-09-16 11:00", clock(nxt)))

    log.info("▶ 흐름이 간격보다 오래 걸린 경우")
    slow = schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                         interval_minutes=30)
    nxt = slow.next_at(at(2026, 9, 16, 10, 0),
                       last_finished=at(2026, 9, 16, 10, 0))
    results.append(check("★ 끝난 직후에도 간격만큼은 쉰다 "
                         "(쉬지 않고 계속 도는 상태가 되지 않는다)",
                         clock(nxt) == "2026-09-16 10:30", clock(nxt)))

    log.info("▶ 못 쓰는 설정은 None 을 준다 — 돌지 않는다")
    results.append(check("꺼 두면 None",
                         schedule.Plan(enabled=False, times=("09:00",))
                         .next_at(now) is None))
    results.append(check("시각이 없으면 None",
                         schedule.Plan(enabled=True, times=())
                         .next_at(now) is None))
    results.append(check("형식이 틀린 시각만 있으면 None",
                         schedule.Plan(enabled=True, times=("9시",))
                         .next_at(now) is None))
    results.append(check("간격이 너무 짧으면 None",
                         schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                                       interval_minutes=1).next_at(now) is None))
    results.append(check("간격이 하루를 넘으면 None",
                         schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                                       interval_minutes=2000)
                         .next_at(now) is None))

    log.info("▶ 문제를 사람이 읽을 말로 알려 준다")
    broken = schedule.Plan(enabled=True, mode=schedule.DAILY, times=("9시",))
    problems = broken.problems()
    results.append(check("무엇이 틀렸는지 말해 준다 (쓸 수 있는 꼴 예시와 함께)",
                         any("예약 형식이 아니다" in text and "매일 09:00" in text
                             for text in problems),
                         "; ".join(problems)))
    results.append(check("쓸 수 있으면 문제 목록이 비어 있다",
                         plan.problems() == []))

    log.info("▶ 설정에서 읽기 — **틀린 값은 버리고 알린다**")
    got = schedule.from_settings({
        "auto_run_enabled": True,
        "auto_run_mode": "daily",
        "auto_run_times": ["13:00", "09:00", "9시", "09:00"],
        "auto_run_interval_minutes": 120,
    })
    # 09-21: 저장 꼴은 `매일 09:00` 이다 (날 조건이 붙었다). 예전 `09:00` 도 읽는다.
    results.append(check("★ 시각을 정렬한다",
                         got.times[:2] == ("매일 09:00", "매일 13:00"), str(got.times)))
    results.append(check("★ 같은 시각은 한 번만 둔다", len(got.times) == 2,
                         str(got.times)))
    results.append(check("★ 못 읽은 값을 **버리고 남겨 둔다**",
                         got.rejected == ("9시",), str(got.rejected)))
    results.append(check("버린 값이 있으면 쓸 수 없다고 본다", not got.usable,
                         "; ".join(got.problems())))

    log.info("▶ 한 줄로 쓴 설정도 받는다")
    got = schedule.from_settings({"auto_run_enabled": True,
                                  "auto_run_times": "09:00, 13:00"})
    results.append(check("`\"09:00, 13:00\"` 을 두 개로 읽는다",
                         got.times == ("매일 09:00", "매일 13:00"), str(got.times)))

    log.info("▶ 방식이 비었거나 모르면 돌지 않는다 — daily 로 떨어지지 않는다 (기본값 없음, 10-02)")
    got = schedule.from_settings({"auto_run_enabled": True,
                                  "auto_run_mode": "매시간",
                                  "auto_run_times": ["09:00"]})
    results.append(check("모르는 방식은 버리고 알린다", got.mode == "" and "매시간" in got.rejected
                         and got.next_at(now) is None, str(got.problems())))
    got = schedule.from_settings({"auto_run_enabled": True, "auto_run_times": ["09:00"]})
    results.append(check("★ 방식이 비면 None — '예약 방식을 고르지 않았다'",
                         got.next_at(now) is None and any("예약 방식" in p for p in got.problems()),
                         str(got.problems())))

    log.info("▶ 저장 모양 — 읽은 것을 그대로 되돌린다")
    plan2 = schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                          times=("09:00",), interval_minutes=90)
    back = schedule.from_settings(schedule.to_settings(plan2))
    results.append(check("저장 → 읽기가 값을 지킨다",
                         back.mode == plan2.mode
                         and back.interval_minutes == plan2.interval_minutes
                         and back.slots() == plan2.slots()))

    log.info("▶ 남은 시간 표기")
    results.append(check("40초", schedule.countdown(40) == "40초"))
    results.append(check("5분", schedule.countdown(5 * 60) == "5분"))
    results.append(check("2시간 5분",
                         schedule.countdown(2 * 3600 + 5 * 60) == "2시간 5분"))
    results.append(check("2시간 (0분은 안 붙인다)",
                         schedule.countdown(2 * 3600) == "2시간"))
    results.append(check("음수는 0초로", schedule.countdown(-10) == "0초"))

    log.info("▶ 화면에 보여 줄 한 줄")
    results.append(check("매일 모드", plan.describe() == "매일 09:00, 13:00, 17:00",
                         plan.describe()))
    results.append(check("간격 모드", gap.describe() == "120분 간격",
                         gap.describe()))

    results += check_slots()
    results += check_slot_modules()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 여기서 확인한 것은 **시각 계산**뿐이다. 실제로 그 시각에 흐름이 "
             "도는 것은 tools/probe_autorun.py 와 실제 실행에서 본다.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
