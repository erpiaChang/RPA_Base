r"""[조사 도구 - 읽기 전용] 자동 실행 판정을 확인한다.

**대상 프로그램을 건드리지 않는다.** 가짜 시계로 하루를 몇 밀리초에 돌린다.

    .venv\Scripts\python.exe -m tools.probe_autorun

## 왜 가짜 시계인가 (2026-09-16)

자동 실행은 **사람이 없을 때** 도는 기능이다. "09:00 에 화면이 잠겨 있으면
건너뛰는가", "앞 회차가 아직 도는데 또 시작하지 않는가" 를 실제로 확인하려면
새벽까지 기다리면서 화면을 잠갔다 풀어야 한다. 그럴 수 없다.

그래서 `utils/autorun.py` 는 시계·잠김·실행을 **전부 콜백으로 받는다.**
여기서 그 콜백을 가짜로 끼워 넣고 시각을 마음대로 돌린다.

★ 여기서 확인하는 것은 **판정**뿐이다. 실제로 그 시각에 ERPia 가 열리고
  흐름이 완주하는 것은 실제 실행에서만 확인된다.
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

from utils import autorun, schedule  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def at(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


class Fake:
    """가짜 세계. 시계를 손으로 돌린다."""

    def __init__(self, now: float) -> None:
        self.now = now
        self.busy = False
        self.locked = False
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        self.busy = True          # 실제 앱도 시작하면 busy 가 된다

    def jump_to(self, stamp: float) -> None:
        self.now = stamp


def make(plan: schedule.Plan, world: Fake) -> autorun.AutoRunner:
    return autorun.AutoRunner(
        plan,
        start=world.start,
        busy=lambda: world.busy,
        locked=lambda: world.locked,
        now=lambda: world.now,
    )


DAILY = schedule.Plan(enabled=True, mode=schedule.DAILY,
                      times=("09:00", "13:00", "17:00"))


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 시각이 되면 흐름을 부른다")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()
    results.append(check("아직 08:59 — 부르지 않는다", world.starts == 0))
    world.jump_to(at(2026, 9, 16, 9, 0))
    said = runner.tick()
    results.append(check("09:00 — 부른다", world.starts == 1, said))
    results.append(check("도는 중에는 다음 예약을 비워 둔다 "
                         "(흐름이 길면 다음 시각이 지나가 버린다)",
                         runner.next_run is None))

    log.info("▶ ★ 첫 박자는 예약만 잡는다 — 띄운 순간이 예약 시각이면 그 회차는 건너뛴다")
    world2 = Fake(at(2026, 9, 16, 9, 0))           # 정확히 09:00 에 띄웠다
    runner2 = make(DAILY, world2)
    runner2.tick()
    results.append(check("★ 띄운 순간에는 돌지 않는다 (의도한 동작이다. "
                         "지나간 시각을 따라잡지 않는 것과 같은 규칙)",
                         world2.starts == 0))
    results.append(check("★ 대신 다음 예약(13:00)을 잡는다",
                         time.strftime("%H:%M", time.localtime(runner2.next_run))
                         == "13:00",
                         time.strftime("%H:%M", time.localtime(runner2.next_run))))

    log.info("▶ 도는 중에 또 부르지 않는다")
    world.jump_to(at(2026, 9, 16, 13, 0))
    runner.tick()
    results.append(check("★ 13:00 이 지나도 한 번만 돌고 있다",
                         world.starts == 1, f"부른 횟수 {world.starts}"))

    log.info("▶ 끝나면 다음 차례를 잡는다")
    world.jump_to(at(2026, 9, 16, 13, 30))
    world.busy = False
    runner.finished(autorun.DONE, "주문 2건 처리")
    results.append(check("끝난 시각 13:30 이면 다음은 17:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))
                         == "17:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))))
    results.append(check("회차 기록이 완료로 남는다",
                         runner.history[-1].state == autorun.DONE))
    results.append(check("소요 시간이 남는다 (09:00 시작 → 13:30)",
                         runner.history[-1].took() == "270분 0초",
                         runner.history[-1].took()))

    log.info("▶ ★ 화면이 잠겨 있으면 건너뛴다")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()
    world.locked = True
    world.jump_to(at(2026, 9, 16, 9, 0))
    said = runner.tick()
    results.append(check("★ 잠겨 있으면 **부르지 않는다**", world.starts == 0, said))
    results.append(check("건너뛴 이유를 기록한다",
                         runner.history[-1].state == autorun.SKIPPED
                         and "잠겨" in runner.history[-1].summary,
                         runner.history[-1].summary))
    results.append(check("★ 건너뛴 회차를 몰아 돌리지 않는다 — 다음은 13:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))
                         == "13:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))))

    log.info("▶ 잠금이 풀리면 다음 예약부터 정상으로 돈다")
    world.locked = False
    world.jump_to(at(2026, 9, 16, 13, 0))
    runner.tick()
    results.append(check("13:00 에 돈다", world.starts == 1))

    log.info("▶ ★ 앞 회차가 아직 돌면 건너뛴다 (같은 아이디 동시 로그인 금지)")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()
    world.busy = True                    # 사람이 손으로 돌리는 중이라고 하자
    world.jump_to(at(2026, 9, 16, 9, 0))
    said = runner.tick()
    results.append(check("★ 바쁘면 부르지 않는다", world.starts == 0, said))
    results.append(check("이유를 기록한다", "앞 회차" in runner.history[-1].summary,
                         runner.history[-1].summary))

    log.info("▶ 사람이 멈춰 두면 돌지 않는다")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()
    runner.paused = True
    world.jump_to(at(2026, 9, 16, 9, 0))
    runner.tick()
    results.append(check("멈춤 상태면 부르지 않는다", world.starts == 0))
    results.append(check("상태줄이 멈춤이라고 말한다",
                         "멈춤" in runner.status_line(), runner.status_line()))
    runner.paused = False
    world.jump_to(at(2026, 9, 16, 13, 0))
    runner.tick()
    results.append(check("풀면 다음 예약에 돈다", world.starts == 1))

    log.info("▶ ★ 실패해도 곧바로 다시 시도하지 않는다 (사용자 확정)")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()                                  # 첫 박자는 예약만 잡는다
    world.jump_to(at(2026, 9, 16, 9, 0))
    runner.tick()
    results.append(check("09:00 에 돌았다", world.starts == 1))
    world.jump_to(at(2026, 9, 16, 9, 5))
    world.busy = False
    runner.finished(autorun.FAILED, "로그인 창을 찾지 못했다")
    results.append(check("★ 실패 직후에 다시 부르지 않는다", world.starts == 1))
    results.append(check("★ 다음은 13:00 — 예약을 기다린다",
                         time.strftime("%H:%M", time.localtime(runner.next_run))
                         == "13:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))))
    world.jump_to(at(2026, 9, 16, 12, 59))
    runner.tick()
    results.append(check("12:59 까지 가만히 있는다", world.starts == 1))
    world.jump_to(at(2026, 9, 16, 13, 0))
    runner.tick()
    results.append(check("13:00 에 다시 돈다", world.starts == 2))
    results.append(check("실패도 회차 기록에 남는다",
                         any(r.state == autorun.FAILED for r in runner.history)))

    log.info("▶ 중단도 회차로 남는다 (실패와 구분한다)")
    world.jump_to(at(2026, 9, 16, 13, 10))
    world.busy = False
    runner.finished(autorun.CANCELLED, "사람이 중단했다")
    results.append(check("중단 상태로 남는다",
                         runner.history[-1].state == autorun.CANCELLED))
    results.append(check("중단 뒤에도 다음 예약이 잡힌다",
                         runner.next_run is not None))

    log.info("▶ 간격 모드 — 끝난 뒤부터 센다")
    gap = schedule.Plan(enabled=True, mode=schedule.INTERVAL,
                        interval_minutes=120)
    world = Fake(at(2026, 9, 16, 9, 0))
    runner = make(gap, world)
    runner.tick()
    results.append(check("★ 창을 띄운 직후에는 돌지 않는다 "
                         "(사람이 값을 확인할 틈도 없이 실계정이 도는 것은 사고다)",
                         world.starts == 0))
    world.jump_to(at(2026, 9, 16, 11, 0))
    runner.tick()
    results.append(check("120분 뒤에 첫 회차", world.starts == 1))
    world.jump_to(at(2026, 9, 16, 11, 40))
    world.busy = False
    runner.finished(autorun.DONE, "완료")
    results.append(check("★ 다음은 끝난 시각(11:40) + 120분 = 13:40",
                         time.strftime("%H:%M", time.localtime(runner.next_run))
                         == "13:40",
                         time.strftime("%H:%M", time.localtime(runner.next_run))))

    log.info("▶ 못 쓰는 설정이면 아무 일도 하지 않는다")
    for name, bad in (("꺼져 있다", schedule.Plan(enabled=False, times=("09:00",))),
                      ("시각이 없다", schedule.Plan(enabled=True, times=())),
                      ("형식이 틀렸다",
                       schedule.Plan(enabled=True, times=("9시",)))):
        world = Fake(at(2026, 9, 16, 9, 0))
        runner = make(bad, world)
        for _ in range(5):
            runner.tick()
            world.jump_to(world.now + 3600)
        results.append(check(f"{name} — 다섯 번 봐도 부르지 않는다",
                            world.starts == 0))

    log.info("▶ 설정을 바꾸면 곧바로 다시 잡는다")
    world = Fake(at(2026, 9, 16, 10, 0))
    runner = make(DAILY, world)
    runner.tick()
    before = runner.next_run
    runner.set_plan(schedule.Plan(enabled=True, mode=schedule.DAILY,
                                  times=("11:00",)))
    results.append(check("바꾼 시각으로 옮겨진다",
                         before != runner.next_run
                         and time.strftime("%H:%M",
                                           time.localtime(runner.next_run))
                         == "11:00",
                         time.strftime("%H:%M", time.localtime(runner.next_run))))

    log.info("▶ ★ 판정에서 예외가 나도 죽지 않는다 (무인 운전의 최악 고장)")
    world = Fake(at(2026, 9, 16, 8, 59))
    runner = make(DAILY, world)
    runner.tick()                                  # 예약을 잡아 둔다

    def boom() -> bool:
        raise RuntimeError("잠김 판정이 터졌다")

    runner._locked = boom
    world.jump_to(at(2026, 9, 16, 9, 0))           # 이제 판정이 불린다
    said = runner.tick()
    results.append(check("★ 예외를 삼키고 한 줄로 알린다",
                         "판정 실패" in said, said))
    runner._locked = lambda: False
    world.jump_to(at(2026, 9, 16, 13, 0))
    runner.tick()                                  # 놓친 09:00 은 건너뛴다 (10-02)
    runner.tick()                                  # 13:00 은 돈다
    results.append(check("★ 그 뒤 예약이 계속 돈다 (한 번 터져도 죽지 않는다)",
                         world.starts == 1))

    log.info("▶ 기록은 끝없이 쌓이지 않는다")
    world = Fake(at(2026, 9, 16, 0, 0))
    runner = make(DAILY, world)
    for _ in range(autorun.HISTORY_LIMIT + 30):
        runner._remember(autorun.Run(planned_at=world.now, state=autorun.DONE))
    results.append(check(f"{autorun.HISTORY_LIMIT}회차까지만 든다",
                         len(runner.history) == autorun.HISTORY_LIMIT,
                         f"{len(runner.history)}건"))

    log.info("▶ 화면이 쓰는 값")
    world = Fake(at(2026, 9, 16, 8, 30))
    runner = make(DAILY, world)
    runner.tick()
    results.append(check("남은 시간이 30분", runner.remaining() == 30 * 60,
                         str(runner.remaining())))
    results.append(check("상태줄에 시각과 남은 시간이 있다",
                         "09:00" in runner.status_line()
                         and "30분" in runner.status_line(),
                         runner.status_line()))
    runner._remember(autorun.Run(planned_at=world.now, state=autorun.DONE))
    runner._remember(autorun.Run(planned_at=world.now, state=autorun.FAILED))
    runner._remember(autorun.Run(planned_at=world.now, state=autorun.SKIPPED))
    counts = runner.counts()
    results.append(check("집계가 상태별로 센다",
                         counts.get(autorun.DONE) == 1
                         and counts.get(autorun.FAILED) == 1
                         and counts.get(autorun.SKIPPED) == 1, str(counts)))
    rows = runner.rows(limit=3)
    results.append(check("표는 **새 것부터** 준다",
                         rows[0][1] == "건너뜀", str([row[1] for row in rows])))
    results.append(check("표 한 줄은 (시각, 상태, 소요, 내용) 네 칸",
                         all(len(row) == 4 for row in rows)))

    log.info("▶ 꺼져 있을 때 상태줄")
    off = make(schedule.Plan(enabled=False), Fake(at(2026, 9, 16, 9, 0)))
    results.append(check("꺼짐이라고 말한다", off.status_line() == "자동 실행 꺼짐",
                         off.status_line()))
    bad = make(schedule.Plan(enabled=True, times=("9시",)),
               Fake(at(2026, 9, 16, 9, 0)))
    results.append(check("설정이 틀렸으면 고치라고 말한다",
                         "고쳐야" in bad.status_line(), bad.status_line()))
    # 09-22 사용자 확정: 예약이 없는 것은 문제가 아니다 — 경고 없이 '예약 없음'
    empty = make(schedule.Plan(enabled=True, times=()), Fake(at(2026, 9, 16, 9, 0)))
    results.append(check("예약이 없으면 고치라 하지 않고 '예약 없음'",
                         empty.status_line() == "예약 없음"
                         and schedule.Plan(enabled=True, times=()).problems() == [],
                         empty.status_line()))

    results += _check_lock_screen()
    results += _check_skip_this()
    results += _check_pause_file()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 미검증: 실제 그 시각에 ERPia 가 열리고 흐름이 완주하는 것은 "
             "여기서 확인할 수 없다. 실제 실행에서만 확인된다.")
    return 0 if all(results) else 1


def _check_skip_this() -> list[bool]:
    """[이번만 건너뛰기] (10-02 — 미루기 대신) / 늦게 온 박자 / 간격의 지난 차례."""
    log.info("▶ 이번만 건너뛰기")
    nine = at(2026, 10, 1, 9, 0)
    world = Fake(at(2026, 10, 1, 8, 56))
    skipped: list[tuple[float, str]] = []
    runner = make(DAILY, world)
    runner._on_skip = lambda planned, reason: skipped.append((planned, reason))
    runner.reschedule()
    runner.skip("사람이 이번 회차를 건너뛰었다")
    out = [check("★ 돌기 전에 건너뛰면 그 차례만 — 기록·알림은 09:00, 다음은 13:00 (09:00 이 다시 잡히지 않는다)",
                 skipped == [(nine, "사람이 이번 회차를 건너뛰었다")]
                 and runner.history[-1].state == autorun.SKIPPED
                 and runner.next_run == at(2026, 10, 1, 13, 0), str(skipped))]
    world.jump_to(nine)
    runner.tick()
    out.append(check("★ 09:00 에는 돌지 않는다", world.starts == 0))
    world.jump_to(at(2026, 10, 1, 13, 0))
    runner.tick()
    runner.skip("도는 중")
    out.append(check("다음 차례(13:00)는 그대로 돈다 · 도는 중에는 건너뛰기가 아무 일도 안 한다",
                     world.starts == 1 and len(skipped) == 1))

    gap = schedule.Plan(enabled=True, mode=schedule.INTERVAL, interval_minutes=60)
    every = make(gap, Fake(at(2026, 10, 1, 8, 0)))
    every.last_finished = at(2026, 10, 1, 8, 0)
    every.reschedule()
    every.skip("사람")
    out.append(check("간격 — 건너뛰면 그 차례 + 간격 (09:00 → 10:00)", every.next_run == at(2026, 10, 1, 10, 0),
                     time.strftime("%H:%M", time.localtime(every.next_run or 0))))

    late = Fake(at(2026, 10, 1, 8, 56))
    slept = make(DAILY, late)
    slept.reschedule()
    late.jump_to(nine + 1800)           # 09:00 예약을 PC 잠자기로 30분 놓쳤다
    slept.tick()
    out.append(check("★ 시작 시각을 10분 넘게 놓쳤으면 깨어나서 돌지 않고 건너뛴다, 다음은 13:00 (10-02)",
                     late.starts == 0 and slept.history[-1].summary == autorun.MISSED
                     and slept.next_run == at(2026, 10, 1, 13, 0)))
    late.jump_to(at(2026, 10, 1, 13, 5))
    slept.tick()
    out.append(check("10분 안이면 늦어도 돈다", late.starts == 1))
    late.busy = False
    slept.finished(autorun.DONE)
    late.jump_to(at(2026, 10, 1, 17, 30))           # 17:00 을 놓치고 깼다 — 다음 날 09:00 으로
    slept.tick()
    gap = Fake(at(2026, 10, 1, 8, 50))
    woke = make(DAILY, gap)
    woke.reschedule()
    gap.jump_to(at(2026, 10, 1, 13, 0) + 30)        # 09:00 을 놓치고 13:00:30 에 깼다
    woke.tick()
    woke.tick()
    out.append(check("★ 놓친 줄만 건너뛰고, 10분 안에 든 다음 줄은 돈다",
                     slept.next_run == at(2026, 10, 2, 9, 0) and gap.starts == 1
                     and woke.current is not None and woke.current.planned_at == at(2026, 10, 1, 13, 0)))

    step = schedule.Plan(enabled=True, mode=schedule.INTERVAL, interval_minutes=60)
    out.append(check("★ 간격 — 지난 차례면 지금부터 센다 (매초 건너뜀이 쌓이고 풀리면 예고 없이 돌던 것, 10-02)",
                     step.next_at(nine, last_finished=nine - 7200) == nine + 3600
                     and step.next_at(nine, last_finished=nine - 600) == nine + 3000))
    return out


def _check_pause_file() -> list[bool]:
    """예약 [일시정지] 는 껐다 켜도 이어진다 (10-01, `logs/autorun_state.json`)."""
    import tempfile

    log.info("▶ 일시정지 저장")
    path = Path(tempfile.mkdtemp()) / "autorun_state.json"
    autorun.save_paused(True, 123.0, path=path)
    saved = autorun.load_paused(path)
    missing = autorun.load_paused(path.with_name("없음.json"))
    path.write_text("{깨짐", encoding="utf-8")
    broken = autorun.load_paused(path)
    autorun.save_paused(True, time.time() + 86400, path=path)
    future = autorun.load_paused(path)
    autorun.save_paused(False, 0.0, path=path.parent)      # 폴더에 쓰기 — 예외 없이 경고만 남겨야 한다
    runner = make(DAILY, Fake(at(2026, 10, 1, 8, 0)))
    runner.paused, runner.paused_since = True, at(2026, 10, 1, 7, 30)
    line = runner.status_line()
    return [check("저장한 멈춤을 다시 읽는다 — 껐다 켜도 이어진다", saved == (True, 123.0), str(saved)),
            check("파일이 없거나 깨졌으면 멈춤 아님 (이 기능 전과 같다)",
                  missing == (False, 0.0) and broken == (False, 0.0)),
            check("쓰기에 실패해도 예외를 올리지 않는다", True),
            check("'언제부터' 가 앞날이면(시계가 바뀜) 멈춤만 살리고 시각은 버린다", future == (True, 0.0), str(future)),
            check("상태줄에 언제부터 멈췄는지", "10-01 07:30부터" in line, line)]


def _check_lock_screen() -> list[bool]:
    """★ 잠금 화면이 **맨 앞에 떠 있기만 해도** 잠긴 것으로 보는가.

    왜 있나 (2026-09-17): 이 PC 에서 잠금 화면이 떠 있는데 입력 데스크톱이
    열려 `screen_locked()` 가 False 였다. 자동 실행이었다면 건너뛰지 않고
    그대로 돌았을 것이다. 여기서는 맨 앞 창의 주인을 가짜로 바꿔 가린다.
    """
    import ctypes

    from utils import process

    log.info("▶ ★ 잠금 화면 앱이 맨 앞이면 잠긴 것으로 본다")
    foreground = ctypes.windll.user32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    real = process.pids_by_name
    out: list[bool] = []
    try:
        process.pids_by_name = lambda name: [pid.value] if name == process.LOCK_APP_EXE else []
        out.append(check("맨 앞 창이 잠금 화면 앱이면 잠긴 것이다",
                         process.lock_app_in_front() and process.screen_locked()))
        process.pids_by_name = lambda name: []
        out.append(check("잠금 화면 앱이 맨 앞이 아니면 이것만으로 잠겼다고 하지 않는다",
                         not process.lock_app_in_front()))
    finally:
        process.pids_by_name = real
    return out


if __name__ == "__main__":
    raise SystemExit(main())
