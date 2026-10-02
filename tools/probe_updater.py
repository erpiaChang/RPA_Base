r"""[조사 도구 - 읽기 전용] 업데이트 상황 판정을 확인한다.

**대상 프로그램을 건드리지 않는다.** 가짜 프로세스 목록과 가짜 창으로 본다.

    .venv\Scripts\python.exe -m tools.probe_updater

## 왜 이 도구가 있나 (2026-09-16)

업데이트가 필요할 때만 뜨는 화면이라 **평소에는 재현할 수 없다.** 사용자가
이미 업데이트를 끝내서 다시 볼 수도 없다. 그래서 판정에 들어가는 입력
(프로세스 목록 / 떠 있는 창)을 가짜로 만들어 **분기만** 확인한다.

★ **여기서 확인하는 것은 판정 논리뿐이다.** 실제 UAC 창을 눌러 본 것이
  아니다 — 누를 수도 없다(`automation/updater.py` 머리말 참고).

가짜 경로는 **상대경로**로 만든다. 판정이 `os.path.abspath` 로 같은 기준에서
비교하므로 결과는 같고, 코드에 남의 절대경로를 적지 않아도 된다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from automation import updater  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

HOME = os.path.join("_fake", "App")
EXE = os.path.join(HOME, "AppMain.exe")
UPDATER = os.path.join(HOME, "App.Updater.exe")
CONSENT = os.path.join("_fake", "SystemDir", "consent.exe")

# 실제로 떠 있는 **SYSTEM 권한** 프로세스. 이름은 Windows 가 정한 것이라 고정이다.
# `consent.exe` 는 UAC 창이 떠 있을 때만 있으니, 항상 있는 것으로 대신 확인한다.
HIGH_PRIV_EXE = "winlogon.exe"
ELSEWHERE = os.path.join("_fake", "Other", "App.Update.exe")
SYSTEM_UPDATE = os.path.join("_fake", "SystemDir", "someupdate.exe")


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


class FakeDialog:
    """창 하나. `dialogs.find_in_process` 가 돌려주는 것의 모양만 흉내낸다."""

    def __init__(self, title: str) -> None:
        self.title = title

    def window_text(self) -> str:
        return self.title


def check_launch_uac() -> list[bool]:
    """★ 실행 직후 창 대신 UAC 가 뜰 때 `start_new_instance` 가 settle 로 넘기는가 (09-18).

    전에는 30초 뒤 "창이 안 뜬다" 로 끝나 로그인 단계의 settle 까지 가지 못했다.
    실측: 업데이터는 원래 인스턴스를 끝내고 **관리자 권한으로** 다시 띄운다.
    """
    from automation import application

    class FakeApp:
        def __init__(self, pid: int, shown: bool = False):
            self.process, self.shown, self.alive = pid, shown, True

        def start(self, cmd, work_dir=None, timeout=None):
            return self

        def windows(self):
            return ["로그인"] if self.shown else []

        def is_process_running(self):
            return self.alive

    said: list[str] = []

    def say(text: str) -> None:
        said.append(text)

    def run(settled: str, after_settle, apps: list, live_after=()):
        """`after_settle(첫 인스턴스)` 로 업데이트 뒤 상태를 만든다. `live_after` 는 그때의 pid 목록."""
        queue, live, seen = list(apps), [], {}
        said.clear()
        application.Application = lambda backend=None: queue.pop(0)
        application.pids_for_exe = lambda exe: list(live)

        def fake_settle(exe, pid, status=None, timeout=None, alert=None):
            seen.update(pid=pid, status=status, alert=alert)
            after_settle(apps[0])
            live[:] = live_after
            return settled

        updater.settle = fake_settle
        try:
            return application.start_new_instance(EXE, timeout=0.3, status=say), "", seen
        except application.ApplicationError as exc:
            return None, str(exc), seen

    real = (application.Application, application._validate, application.pids_for_exe,
            application.is_elevated, updater.settle)
    out: list[bool] = []
    try:
        application._validate = lambda exe: Path(exe)
        application.is_elevated = lambda pid=None: pid == 88   # 88 = 업데이터가 다시 띄운 것
        log.info("▶ ★ 실행 직후 창 대신 UAC 가 뜰 때 (start_new_instance)")

        target, err, seen = run("UAC 동의는 사람이 눌렀다 / 업데이트 완료",
                                lambda a: setattr(a, "shown", True), [FakeApp(77)])
        out.append(check("★ 창이 안 뜨면 settle 로 넘기고, 끝나면 이어 간다",
                         target is not None and target.pid == 77 and seen.get("pid") == 77,
                         err))
        out.append(check("안내 통로(status)를 settle 까지 넘긴다", seen.get("status") is say))

        target, err, _ = run("", lambda a: None, [FakeApp(77)])
        out.append(check("막힌 것이 없으면 예전처럼 '창이 안 뜬다' 로 실패",
                         target is None and "창이 나타나지 않았다" in err
                         and "업데이트 뒤" not in err, err))

        target, err, _ = run("업데이트 완료", lambda a: None, [FakeApp(77)])
        out.append(check("업데이트 뒤에도 창이 없으면 그렇게 말하고 실패",
                         target is None and "업데이트 뒤에도" in err, err))

        target, err, _ = run("업데이트 완료", lambda a: setattr(a, "alive", False),
                             [FakeApp(77), FakeApp(99, shown=True)], live_after=[88])
        out.append(check("★ 관리자 권한으로 다시 뜬 인스턴스에는 붙지 않고 새로 띄운다",
                         target is not None and target.pid == 99, err))
        out.append(check("그 창은 사람이 닫아야 한다고 알린다",
                         any("관리자 권한" in s for s in said), " / ".join(said)))

        target, err, _ = run("업데이트 완료", lambda a: setattr(a, "alive", False),
                             [FakeApp(77), FakeApp(66)], live_after=[88])
        out.append(check("다시 띄워도 창이 없으면 무한히 반복하지 않고 멈춘다",
                         target is None and bool(err), err))
    finally:
        (application.Application, application._validate, application.pids_for_exe,
         application.is_elevated, updater.settle) = real
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []

    real_running = updater._running
    real_find = updater.find_update_dialog
    real_locked = updater.screen_locked

    real_by_name = updater.pids_by_name

    def use(processes, dialog=None, locked=False):
        updater._running = lambda: list(processes)
        # ★ `consent_pids()` 는 **이름으로** 찾는다. 권한이 높은 프로세스는
        #   열 수 없어 경로를 못 읽기 때문이다 (2026-09-17 실측). 가짜 목록도
        #   같은 방식으로 흉내내야 한다 — 여기서 경로 방식으로 되돌리면
        #   확인 도구만 통과하고 실제 UAC 창은 놓치는 상태가 된다.
        updater.pids_by_name = lambda name: sorted(
            pid for pid, module in processes
            if os.path.basename(module).lower() == name.lower())
        updater.find_update_dialog = lambda pid: dialog
        updater.screen_locked = lambda: locked

    try:
        log.info("▶ 업데이터 프로세스 고르기")
        use([(1, EXE),
             (2, UPDATER),         # 같은 폴더 → 우리 것
             (3, SYSTEM_UPDATE),   # 다른 폴더의 남의 업데이트
             (4, ELSEWHERE)])      # 다른 폴더 → 우리 것 아니다
        found = updater.updater_pids(EXE)
        results.append(check("★ **같은 폴더**의 업데이터만 우리 것으로 본다",
                             found == [2], f"고른 pid={found}"))
        results.append(check("대상 프로그램 자신은 업데이터로 세지 않는다",
                             1 not in found))

        log.info("▶ UAC 동의 창 감지")
        use([(1, EXE), (9, CONSENT)])
        results.append(check("consent.exe 를 찾는다", updater.consent_pids() == [9]))
        kind, detail = updater.state(EXE, 1)
        results.append(check("★ UAC 동의 창이면 `consent` 로 본다",
                             kind == updater.CONSENT, f"{kind} — {detail}"))

        log.info("▶ 보는 차례 — **누를 수 있는 것을 먼저**")
        use([(1, EXE), (9, CONSENT), (2, UPDATER)],
            dialog=FakeDialog("업데이트 확인"))
        kind, detail = updater.state(EXE, 1)
        results.append(check("★ 안내 창이 있으면 그것을 먼저 본다 (누를 수 있다)",
                             kind == updater.DIALOG, f"{kind} — {detail}"))

        log.info("▶ 업데이터만 돌 때")
        use([(1, EXE), (2, UPDATER)])
        kind, _detail = updater.state(EXE, 1)
        results.append(check("업데이터가 돌면 `updating`", kind == updater.UPDATING))

        log.info("▶ 아무것도 없을 때")
        use([(1, EXE)])
        kind, _detail = updater.state(EXE, 1)
        results.append(check("막힌 것이 없으면 `none`", kind == updater.NONE))
        results.append(check("막힌 것이 없으면 아무 일도 하지 않는다",
                             updater.settle(EXE, 1) == ""))

        log.info("▶ 창은 없는데 입력 데스크톱이 안 열릴 때")
        use([(1, EXE)], locked=True)
        kind, detail = updater.state(EXE, 1)
        results.append(check("★ 단정하지 않고 '그럴 수 있다' 로 말한다",
                             kind == updater.CONSENT and "있을 수 있다" in detail,
                             detail))

        log.info("▶ 기다리는 상한")
        from config.settings import SETTINGS

        results.append(check("업데이트 대기 상한이 따로 있다",
                             SETTINGS.timeouts.update >= 300,
                             f"{SETTINGS.timeouts.update:.0f}초 — 사람이 UAC 를 "
                             "누르기를 기다리는 시간이 들어 있다"))
    finally:
        updater._running = real_running
        updater.find_update_dialog = real_find
        updater.screen_locked = real_locked
        updater.pids_by_name = real_by_name

    results.extend(check_launch_uac())

    # ★ 여기서부터는 **가짜가 아니라 이 PC** 를 본다 (읽기만 한다).
    #   왜 있나: `consent.exe` 는 SYSTEM 권한이라 프로세스를 **열 수 없다.**
    #   경로를 읽어 찾던 예전 방식은 UAC 창이 화면에 떠 있는데도 못 찾았고,
    #   그래서 사람에게 아무 안내도 못 했다 (2026-09-17 실측).
    #   같은 성질의 프로세스로 그 회귀를 막는다.
    log.info("▶ ★ 권한이 높은 프로세스도 **이름으로는** 찾힌다")
    named = real_by_name(HIGH_PRIV_EXE)
    results.append(check(f"★ 이름으로 찾는다 ({HIGH_PRIV_EXE}, SYSTEM 권한)",
                         bool(named), f"pid={named}"))
    by_path = [pid for pid, module in real_running()
               if os.path.basename(module).lower() == HIGH_PRIV_EXE]
    log.info("  참고 — 경로를 읽는 방식으로 찾은 것: %s%s", by_path or "없음",
             "  ← 이것이 예전 방식이 놓치던 자리다" if not by_path else "")

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 실제 UAC 창은 **눌러 봤고 눌리지 않았다** (2026-09-17). "
             "단추를 볼 수 없고 입력도 닿지 않는다 — "
             "automation/updater.py 머리말 참고.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
