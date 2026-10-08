r"""[확인 도구 - 읽기 전용] 기능 선택(모듈화)이 **고른 것만, 정해진 순서로** 도는지.

ERPia·메일·휴대폰을 건드리지 않는다. 통합 흐름(`full_flow`)이 부르는 바깥 함수를
가짜로 바꿔 끼우고, **무엇이 어떤 순서로 불렸는지**만 본다. 실행용 창의 입력 잠금·
필수값 판단도 창을 띄워(숨긴 채로) 확인한다.

    .venv\Scripts\python.exe -m tools.probe_modules
    .venv\Scripts\python.exe -m tools.probe_modules --shot    실행 창 탭 넷 + 진행 화면을 찍는다 (logs/)

설계: `docs/BILLING.md` 2절 / `orchestrator/modules.py`.
"""
from __future__ import annotations

import os
import sys
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from config.settings import SETTINGS  # noqa: E402
from orchestrator import (  # noqa: E402
    erpia_flow, full_flow, history, modules, steps, steps_collect, steps_erpia)
from orchestrator.common import Hooks, Result  # noqa: E402
from utils import cancel  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def _raises(fn, kind) -> bool:
    try:
        fn()
    except kind:
        return True
    except Exception as exc:        # 다른 예외면 실패로 본다
        log.info("    (다른 예외: %s: %s)", type(exc).__name__, exc)
        return False
    return False


class FakeTarget:
    pid = 4242

    def main_window(self):
        return SimpleNamespace(window_text=lambda: "메인")


def patched(calls: list[str]):
    """`full_flow` 바깥 함수를 가짜로 바꾼다. 되돌릴 함수를 돌려준다."""
    saved: list[tuple[object, str, object]] = []

    def put(owner, name, value):
        saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def fake_collect(hooks=None, dry_run=False):
        calls.append("mail")
        for item in steps_collect.plan():
            with hooks.stage(item):
                pass
        return Result(summary="", details={"ok": 3})

    def fake_collect_all(screen, options, pid, hooks, dry_run=False,
                         uploads=None, site_failures=None, site_info=None, on_uploaded=None):
        calls.append("orders")
        for source in options.normalized_sources():
            with hooks.stage(steps_erpia.SOURCE_STEPS[source]):
                pass
        return 0, []

    def fake_launch(exe, work_dir=None, status=None, alert=None):
        calls.append("launch")
        return FakeTarget()

    def fake_login(company, user_id, password, pid=None):
        calls.append("login")
        return "메인"

    def fake_screen(target):
        calls.append("screen")
        return object()

    def fake_sales(screen, main_window=None, dry_run=False):
        calls.append("sales")
        return {"skipped": False, "before": "3", "after": "0"}

    def fake_wait(target, dry_run=False, hold=True):
        calls.append("logistics_wait")
        return {"shortage": 1, "held": 1, "saved": 2, "items": []}

    def fake_logi(target, courier="", box="", mode="", dry_run=False):
        calls.append("logistics")
        return {"registered": 2, "courier": "c", "box": "b", "mode": "자동",
                "save_clicked": True, "finish": "운송장출력", "finish_clicked": True}

    book = SimpleNamespace(pending=lambda: [])
    put(full_flow.collect_flow, "run", fake_collect)
    put(full_flow.erpia_flow, "collect_all", fake_collect_all)
    # ★ 실행·로그인·매출처리·물류는 `full_flow` 가 `erpia_flow` 의 단계 묶음으로 부른다
    #   (09-21). **거기를** 바꿔야 한다 — 예전 자리(`full_flow`)만 바꾸면 실제 ERPia 가 뜬다.
    #   `full_flow` 에서 그 이름들을 뺐으므로 옛 자리를 바꾸려 하면 여기서 AttributeError 다.
    put(erpia_flow, "start_new_instance", fake_launch)
    put(erpia_flow, "login", fake_login)
    put(erpia_flow, "updater", SimpleNamespace(settle=lambda *a, **k: ""))
    put(full_flow, "enter_order_mapping", fake_screen)
    put(erpia_flow, "process_sales", fake_sales)
    put(erpia_flow, "logistics_wait", SimpleNamespace(run=fake_wait))
    put(erpia_flow, "logistics", SimpleNamespace(run=fake_logi))
    put(full_flow, "manifest_mod",
        SimpleNamespace(load=lambda: book, save=lambda b: None))
    put(full_flow, "_pending_uploads", lambda b: [])

    def restore() -> None:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)
    return restore


def _options(selected, sources=("site",)) -> erpia_flow.Options:
    return erpia_flow.Options(exe="C:/fake/erp.exe", company="c", user_id="u",
                              password="p", sources=list(sources),
                              courier="c", box="b", modules=selected)


def _run(selected, sources=("site",)):
    calls: list[str] = []
    hooks = Hooks()
    restore = patched(calls)
    try:
        result = full_flow.run(_options(selected, sources), hooks=hooks)
    finally:
        restore()
    return calls, hooks, result


def check_normalize() -> list[bool]:
    out = []
    out.append(check("None 이면 전부, 정해진 순서",
                     [m.id for m in modules.normalize(None)] == list(modules.IDS)))
    got = [m.id for m in modules.normalize(["logistics", "orders"])]
    out.append(check("고른 순서와 상관없이 실행 순서로 정렬", got == ["orders", "logistics"],
                     str(got)))
    out.append(check("★ 빈 선택은 막는다 (None 과 다르다)",
                     _raises(lambda: modules.normalize([]), ValueError)))
    out.append(check("모르는 기능은 막는다",
                     _raises(lambda: modules.normalize(["bogus"]), ValueError)))
    return out


def check_plan() -> list[bool]:
    out = []
    both = ["excel", "site"]
    same = modules.plan(modules.normalize(None), both)
    old = steps_collect.plan() + steps_erpia.plan(both)
    out.append(check("★ 전부 고르면 예전 통합 계획과 똑같다",
                     [s.id for s in same] == [s.id for s in old],
                     " → ".join(s.id for s in same)))
    got = [s.id for s in modules.plan(modules.normalize(["orders", "logistics"]), ["site"])]
    out.append(check("주문수집·매출처리 + 물류관리 = 물류대기 없이",
                     got == ["launch", "login", "screen", "site", "sales", "logistics"],
                     str(got)))
    got = [s.id for s in modules.plan(modules.normalize(["mail"]), [])]
    out.append(check("메일만 고르면 ERPia 단계가 없다",
                     got == [s.id for s in steps_collect.plan()], str(got)))
    got = [s.id for s in modules.plan(modules.normalize(["logistics"]), [])]
    out.append(check("물류관리만 고르면 주문매핑 화면을 열지 않는다",
                     got == ["launch", "login", "logistics"], str(got)))
    out.append(check("모르는 수집방식은 막는다",
                     _raises(lambda: modules.plan(modules.normalize(["orders"]), ["x"]),
                             ValueError)))
    return out


def check_flow() -> list[bool]:
    out = []
    calls, hooks, _ = _run(None, ("excel", "site"))
    out.append(check("★ 전부: 예전과 같은 순서로 전부 부른다",
                     calls == ["mail", "launch", "login", "screen", "orders", "sales",
                               "logistics_wait", "logistics"], str(calls)))

    calls, hooks, result = _run(["logistics", "orders"])
    out.append(check("★ 주문수집·매출처리 + 물류관리: 물류대기·메일을 부르지 않는다",
                     calls == ["launch", "login", "screen", "orders", "sales",
                               "logistics"], str(calls)))
    states = [run["state"] for run in hooks.report()]
    out.append(check("그 계획의 단계가 전부 끝남으로 남는다 (대기로 남은 단계 없음)",
                     all(state in steps.FINISHED for state in states), str(states)))
    out.append(check("요약에 고르지 않은 기능이 없다",
                     "물류대기" not in result.summary and "수집 " not in result.summary,
                     result.summary))

    calls, _, result = _run(["mail"])
    out.append(check("★ 메일만: ERPia 를 띄우지 않는다", calls == ["mail"], str(calls)))
    out.append(check("메일만: 결과에 대상 프로그램이 없다", result.target is None))

    calls, _, _ = _run(["logistics"], sources=())
    out.append(check("물류관리만: 수집방식을 둘 다 꺼도 돈다 (주문 화면도 안 연다)",
                     calls == ["launch", "login", "logistics"], str(calls)))

    calls, _, _ = _run(["mail", "logistics"])
    out.append(check("메일 + 물류관리: 주문수집 없이 이어진다",
                     calls == ["mail", "launch", "login", "logistics"], str(calls)))

    for label, selected, sources in (
            ("주문수집을 고르고 수집방식이 없으면", ["orders"], ()),
            ("기능을 하나도 안 고르면", [], ("site",)),
            ("모르는 기능이면", ["bogus"], ("site",))):
        calls: list[str] = []
        restore = patched(calls)
        try:
            blocked = _raises(lambda: full_flow.run(_options(selected, sources),
                                                    hooks=Hooks()), ValueError)
        finally:
            restore()
        out.append(check(f"★ {label} **아무것도 하기 전에** 막는다",
                         blocked and not calls, str(calls)))
    return out


def check_mail_failure() -> list[bool]:
    """메일이 멈춰도 뒤 기능은 돈다 (사용자 확정 09-30). 메일만·단계 밖 실패·중단은 원래대로 멈춘다."""
    out = []

    def run_failing(selected, mode="stage"):
        calls: list[str] = []
        hooks = Hooks()

        def failing_collect(hooks=None, dry_run=False):
            calls.append("mail")
            with hooks.stage(steps_collect.PREFLIGHT):
                pass
            if mode == "outside":
                raise RuntimeError("단계 밖 실패 시험")
            with hooks.stage(steps_collect.MAIL_LOGIN):
                raise cancel.Cancelled("시험") if mode == "cancel" else RuntimeError("로그인 실패 시험")

        restore = patched(calls)
        full_flow.collect_flow.run = failing_collect            # restore() 가 실물로 되돌린다
        real_step = full_flow.step
        full_flow.step = partial(real_step, screenshot_on_error=False)   # 일부러 낸 실패에 화면을 찍지 않는다
        try:
            result, stopped = full_flow.run(_options(selected, ("excel", "site")), hooks=hooks), ""
        except (RuntimeError, cancel.Cancelled) as exc:
            result, stopped = None, type(exc).__name__
        finally:
            full_flow.step = real_step
            restore()
        return calls, hooks, result, stopped

    calls, hooks, result, stopped = run_failing(None)
    out.append(check("★ 메일 로그인이 실패해도 뒤 기능을 전부 부른다",
                     not stopped and calls == ["mail", "launch", "login", "screen", "orders",
                                               "sales", "logistics_wait", "logistics"], str(calls)))
    states = {run["id"]: run["state"] for run in hooks.report()}
    out.append(check("메일 단계: 점검 완료 · 로그인 실패 · 받은메일함 건너뜀",
                     (states.get("preflight"), states.get("mail_login"), states.get("mailbox"))
                     == (steps.DONE, steps.FAILED, steps.SKIPPED), str(states)))
    out.append(check("확인할 것에 메일 사유가 남는다",
                     any(item.startswith("메일 엑셀 받기를 끝내지 못해") for item in hooks.attention),
                     str(hooks.attention)))
    summary = result.summary if result else ""
    out.append(check("요약 맨 앞에 끝나지 않은 단계",
                     summary.startswith(f"끝나지 않은 단계: {steps_collect.MAIL_LOGIN.name}"), summary))
    out.append(check("지난 실행 이력은 '끝나지 않은 단계 있음'",
                     history.state_of(summary, hooks.report()) == history.UNFINISHED))

    calls, _, _, stopped = run_failing(["mail"])
    out.append(check("★ 메일만 고르면 원래대로 멈춘다", stopped == "RuntimeError", stopped))
    calls, _, _, stopped = run_failing(None, "outside")
    out.append(check("★ 단계 밖에서 멈추면 원래대로 멈춘다 (ERPia 를 띄우지 않는다)",
                     stopped == "RuntimeError" and calls == ["mail"], str(calls)))
    calls, _, _, stopped = run_failing(None, "cancel")
    out.append(check("★ [중단] 은 그대로 전체를 멈춘다", stopped == "Cancelled" and calls == ["mail"],
                     str(calls)))
    return out


def check_window() -> list[bool]:
    """실행용 창 — 고른 기능에 필요한 입력만 요구하고, 나머지는 잠그는가."""
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return []
    root.withdraw()
    out = []
    try:
        from gui.run_app import RunWindow

        window = RunWindow(root)

        def pick(*ids):
            for module in modules.ALL:
                window.module_vars[module.id].set(module.id in ids)
            root.update_idletasks()

        pick("orders", "logistics")
        keys = set(window._values())
        out.append(check("주문+물류관리: 사이트 비밀번호를 요구하지 않는다",
                         "사이트 비밀번호" not in keys and "택배사" in keys, str(sorted(keys))))
        out.append(check("주문+물류관리: 메일 비밀번호 칸이 잠긴다",
                         str(window.mail_pw_entry.cget("state")) == "disabled"))
        planned = [s.id for s in window._plan()]
        out.append(check("주문+물류관리: 계획 표에 물류대기·메일이 없다",
                         "logistics_wait" not in planned and "mailbox" not in planned
                         and planned[-2:] == ["sales", "logistics"], str(planned)))

        pick("mail")
        keys = set(window._values())
        out.append(check("메일만: ERPia 값·택배사를 요구하지 않는다 — 메일 쪽(저장 폴더·인증 문자·사이트 연결)은 요구한다 (기본값 없음, 10-02)",
                         keys == {"실행할 기능", "사이트 비밀번호", "엑셀 저장 폴더", "인증 문자", "메일 사이트 연결"},
                         str(sorted(keys))))
        out.append(check("메일만: ERPia 비밀번호·택배사·ERPia 경로 칸이 잠기고 엑셀 저장 폴더는 열린다",
                         str(window.pw_entry.cget("state")) == "disabled"
                         and str(window.courier_entry.cget("state")) == "disabled"
                         and str(window.exe_entry.cget("state")) == "disabled"
                         and str(window.download_entry.cget("state")) == "normal"))
        # 경로 두 칸 (09-23): 저장값에 들어가고, 실행 중 잠그는 칸에 포함된다
        saved = window._saved_values("x.exe", "c", "u", "p")    # 가짜 값. 경로가 아니다
        out.append(check("경로 두 칸: 실행 파일·엑셀 저장 폴더가 저장값에 들어간다",
                         saved.get("target_exe") == "x.exe"
                         and saved.get("download_base_dir") == window.download_var.get()
                         and all(w in window.input_widgets for w in (
                             window.exe_entry, window.browse_btn,
                             window.download_entry, window.download_btn))))
        from automation.application import find_exe

        found = find_exe("no-such-file.exe")     # 설정값이 틀렸을 때의 경로 — 등록 정보·바로가기까지 훑는다
        out.append(check("find_exe: 설정값이 맞으면 그대로, 틀리면 이 PC 에서 찾거나 None (예외 없음)",
                         find_exe(SETTINGS.target_exe) == SETTINGS.target_exe
                         and (found is None or Path(found).is_file()), str(found)))
        out.append(check("엑셀 저장 폴더는 기본값이 없다 — 설정값 그대로 (비면 빈 칸, 10-02)",
                         window.download_var.get() == (getattr(SETTINGS, "download_base_dir", None) or "")))

        pick()
        out.append(check("★ 아무것도 안 고르면 [실행] 이 잠긴다",
                         "실행할 기능" in window._missing()
                         and str(window.run_btn.cget("state")) == "disabled"))

        pick(*modules.IDS)
        out.append(check("전부 고르면 모든 칸이 풀린다",
                         all(str(w.cget("state")) == "normal" for w in (
                             window.mail_pw_entry, window.pw_entry,
                             window.courier_entry, window.box_entry))))

        # 메일만이면 ERPia 실행 파일이 이 PC 에 없어도 막지 않는다 (09-21 검토)
        pick("mail")
        mail_only = window._needs_exe()
        pick("orders")
        out.append(check("메일만이면 실행 파일을 요구하지 않고, ERPia 기능이면 요구한다",
                         mail_only is False and window._needs_exe() is True))

        # ★ 이번 회차가 ERPia 를 띄우기 **전에** 실패해도 지난 회차 ERPia 를 닫지 않는다
        out.append(check_stale_target(window))
        out += check_scheduled(window, pick)
        out += check_repair(window, pick)
        out += check_busy_lock(window)
        out += check_pre_run(window)
        out += check_rerun(window)
        out += check_crash_notice(window)
    finally:
        root.destroy()
    out += check_small_screen()
    return out


def check_crash_notice(window) -> list[bool]:
    """처리 안 된 예외 알림 (10-07, `utils/crashlog.py` → `RunWindow.show_crash`)."""
    from gui import run_app

    out = []
    keep = (window.busy, window.unattended, window.status_var.get())
    try:
        window._close_notice()
        window.busy = True
        window.show_crash("가짜 알림")
        out.append(check("실행 중이면 창 없이 상태줄만", window._notice is None
                         and window.status_var.get() == "가짜 알림"))
        window.busy = False
        window.show_crash("가짜 알림")
        out.append(check("한가하면 알림 창이 뜬다", window._notice_kind == run_app.NOTICE_CRASH))
        window._show_notice(run_app.NOTICE_ERPIA, "곧 예약 실행", [("닫기", window._close_notice)])
        window.show_crash("가짜 알림")
        out.append(check("예약 전 경고 창이 떠 있으면 덮지 않는다", window._notice_kind == run_app.NOTICE_ERPIA))
    finally:
        window._close_notice()
        window.busy, window.unattended = keep[0], keep[1]
        window.status_var.set(keep[2])
    return out


def _texts(widget) -> list[str]:
    import tkinter as tk

    found = []
    for child in widget.winfo_children():
        try:
            found.append(str(child.cget("text")))
        except tk.TclError:
            pass    # 글자가 없는 틀
        found += _texts(child)
    return found


def check_pre_run(window) -> list[bool]:
    """예약 전 (10-01) — 5분 전 경고 창·[이번만 건너뛰기](10-02), 30분 전 휴대폰 점검. 가짜 시계·가짜 찾기·가짜 점검."""
    from types import SimpleNamespace

    from gui import run_app
    from utils import autorun as auto, schedule as sched

    clock = {"now": time.mktime((2026, 10, 1, 8, 56, 0, 0, 0, -1))}
    nine = time.mktime((2026, 10, 1, 9, 0, 0, 0, 0, -1))

    def runner_for(*times):
        made = auto.AutoRunner(sched.Plan(enabled=True, times=times), start=lambda: None,
                               busy=lambda: window.busy, locked=lambda: False, now=lambda: clock["now"])
        made.reschedule()
        window._warned_key, window._warn_looked = None, 0.0
        return made

    saved = (window.autorunner, run_app.logged_in_pids, run_app.process.screen_locked)
    out = []
    try:
        run_app.logged_in_pids = lambda company, user: [1234]
        run_app.process.screen_locked = lambda: False
        window.autorunner = runner_for("09:00 orders,logistics")
        window._warn_before_run()
        notice = window._notice
        shown = notice is not None and window._notice_kind == run_app.NOTICE_ERPIA
        out.append(check("★ 5분 전 + 같은 아이디 ERPia → 맨 위 알림 창 (모달 아님, [이번만 건너뛰기])",
                         shown and window.root.grab_current() is None and bool(notice.attributes("-topmost"))
                         and run_app.SKIP_TEXT in _texts(notice) and "09:00" in window._notice_var.get(),
                         window._notice_var.get() if shown else "안 뜸"))
        skipped: list = []
        window.autorunner._on_skip = lambda planned, reason: skipped.append((planned, reason))
        window._skip_this()
        out.append(check("★ [이번만 건너뛰기] → 09:00 은 건너뜀으로 남고 다음 차례로 · 창은 닫힌다",
                         window._notice is None and skipped == [(nine, run_app.SKIP_BY_PERSON)]
                         and window.autorunner.next_run not in (None, nine), str(skipped)))

        window.autorunner = runner_for("09:00 mail")
        window._warn_before_run()
        out.append(check("메일만 도는 회차는 경고하지 않는다 (ERPia 를 닫지 않는다)", window._notice is None))

        window.autorunner = runner_for("09:00 orders")
        window._warn_before_run()
        window.busy = True
        window._warn_before_run()
        window.busy = False
        out.append(check("실행이 시작되면 알림 창을 닫는다 (ERPia 클릭을 가로채지 않게)", window._notice is None))

        run_app.logged_in_pids = lambda company, user: []
        window.autorunner = runner_for("09:00 orders")
        window._warn_before_run()
        out.append(check("같은 아이디 ERPia 가 없으면 경고하지 않는다", window._notice is None))

        # 휴대폰 점검 — 메일 회차 30분 전부터. 보기만 하는 점검 함수는 가짜로
        alerts: list[str] = []
        window._reporter = lambda: SimpleNamespace(alert=lambda kind, text: alerts.append(kind))
        window._phone_check = lambda: (False, "휴대폰이 PC 와 연결돼 있지 않습니다")
        clock["now"] = time.mktime((2026, 10, 1, 8, 40, 0, 0, 0, -1))
        window.autorunner = runner_for("09:00 mail,orders")
        window._phone_seen, window._phone_next, window._phone_alerted, window._phone_thread = None, 0.0, None, None

        def tick_check():
            window._precheck_before_run()
            if window._phone_thread is not None:
                window._phone_thread.join(2)
            window._precheck_before_run()

        tick_check()
        note = window._phone_note()
        out.append(check("★ 메일 회차 30분 안 → 점검 결과가 홈 둘째 줄에, '안 됨' 이면 알림 창 1회 + 서버 알림",
                         note.startswith("휴대폰 인증: 안 됨") and window._notice_kind == run_app.NOTICE_PHONE
                         and alerts == ["phone"], f"{note} / {alerts}"))
        window._close_notice()
        window._phone_next = 0.0
        tick_check()
        out.append(check("같은 회차에서 다시 '안 됨' 이어도 알림 창·메일은 또 하지 않는다",
                         window._notice is None and alerts == ["phone"]))

        window.autorunner = runner_for("09:00 orders")
        window._phone_seen, window._phone_next, window._phone_thread = None, 0.0, None
        window._precheck_before_run()
        out.append(check("메일이 없는 회차는 점검하지 않는다", window._phone_thread is None and window._phone_note() == ""))
        window._phone_result = {"key": nine - 86400, "ready": False, "reason": "지난 차례", "at": clock["now"]}
        window._collect_phone_result()
        out.append(check("늦게 끝난 지난 차례의 점검 결과는 버린다 — 알림 창·메일 없음 (10-02)",
                         window._phone_seen is None and window._notice is None and alerts == ["phone"]))

        # [실행] 을 받아 시작하는 중(검사·알림 창)에는 예약·웹 [실행] 이 끼어들지 않는다 (10-02)
        import inspect

        from gui import erpia_app

        seen: list = []
        window._begin_run = lambda start_step: seen.append(
            (window._starting, window._remote_start([])))
        try:
            window.on_run()
        finally:
            vars(window).pop("_begin_run", None)
        out.append(check("★ [실행] 을 시작하는 중에는 예약이 '도는 중' 으로 보고, 웹 [실행] 도 받지 않는다",
                         seen == [(True, "이미 실행 중이라 시작하지 않았다")] and not window._starting
                         and "self.busy or self._starting" in inspect.getsource(erpia_app.ErpiaWindow.attach_autorun),
                         str(seen)))
    finally:
        window._close_notice()
        vars(window).pop("_reporter", None)
        vars(window).pop("_phone_check", None)
        window.autorunner, run_app.logged_in_pids, run_app.process.screen_locked = saved
    return out


def check_rerun(window) -> list[bool]:
    """[멈춘 곳부터 다시] (10-01) — 지난 실행이 멈춘 기능부터 끝까지 한 번. 저장된 실행할 기능은 그대로."""
    import json

    from gui import run_app

    def write(state, steps):
        entry = {"finished_at": time.time(), "started_at": time.time(), "elapsed": 1, "trigger": "예약 실행",
                 "modules": ["주문수집·매출처리", "물류대기", "물류관리"], "state": state, "summary": state,
                 "attention": 0, "report": "", "steps": [{"id": i, "state": s} for i, s in steps]}
        with history.HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    out = []
    write("failed", [("launch", "done"), ("login", "done"), ("screen", "done"), ("site", "done"), ("sales", "done"),
                     ("logistics_wait", "failed"), ("logistics", "pending")])
    window._refresh_last()
    out.append(check("★ 멈춘 실행 뒤 — 홈에 단추, 마지막 실행 줄에 '멈춘 곳'",
                     bool(window.resume_btn.grid_info()) and window.resume_ids == ["logistics_wait", "logistics"]
                     and "멈춘 곳: 물류대기" in window.last_var.get(), window.last_var.get()))
    checked = window._checked_modules()
    started: list = []
    window.on_run = lambda start_step="": started.append(
        (window._modules(), window._trigger(), window._saved_values("", "", "", "").get("run_modules")))
    try:
        window.on_resume()
    finally:
        vars(window).pop("on_run", None)
    out.append(check("★ 누르면 그 기능만 이번에 — '어떻게' 는 '이어서 실행', 저장된 실행할 기능은 그대로",
                     started == [(["logistics_wait", "logistics"], run_app.RERUN_TRIGGER, checked)], str(started)))
    out.append(check("시작을 못 하고 돌아서면 표시를 거둔다 (다음 [실행] 이 이 기능만 돌지 않게)",
                     window.resume_run is False and window.scheduled_modules is None))
    write("done", [("launch", "done"), ("logistics_wait", "done"), ("logistics", "done")])
    window._refresh_last()
    out.append(check("다 끝난 실행 뒤에는 단추가 없다", window.resume_ids == [] and not window.resume_btn.grid_info()))
    return out


def check_busy_lock(window) -> list[bool]:
    """실행 중 — 홈에 고정, 다른 탭·[바꾸기] 잠금, 홈에 [일시정지]·[중단] (09-29 사용자 요청). 흐름은 부르지 않는다."""
    from gui.overlay import PAUSE_TEXT, RESUME_TEXT
    from utils import cancel

    out = []
    others = (window.schedule_tab, window.history_tab, window.settings_tab)
    window._show_tab(window.settings_tab)
    window.token = cancel.CancelToken()
    window._set_busy(True)          # 단추·탭 모양만 — 토큰은 가짜, 스레드·흐름 없음
    window.root.update_idletasks()
    out.append(check("★ 실행 중 — 홈으로 옮기고 예약·실행 기록·설정 탭을 잠근다",
                     window.tabs.select() == str(window.home_tab)
                     and all(str(window.tabs.tab(tab, "state")) == "disabled" for tab in others)))
    window._show_tab(window.settings_tab)
    window._go_modules()
    out.append(check("실행 중 — [바꾸기]·[예약 바꾸기] 도 잠기고, 불러도 홈에 남는다",
                     window.tabs.select() == str(window.home_tab)
                     and str(window.modules_btn.cget("state")) == "disabled"
                     and str(window.schedule_btn.cget("state")) == "disabled"))
    out.append(check("★ 홈에 [일시정지]·[중단] 이 보인다",
                     bool(window.pause_btn.grid_info()) and bool(window.stop_btn.grid_info())
                     and window.pause_btn.cget("text") == PAUSE_TEXT
                     and str(window.stop_btn.cget("state")) == "normal"))
    window.toggle_pause()
    paused = window.token.paused and window.pause_btn.cget("text") == RESUME_TEXT
    window.toggle_pause()
    out.append(check("[일시정지] → 토큰이 멈추고 [계속하기] 로, 다시 누르면 이어 간다",
                     paused and not window.token.paused and window.pause_btn.cget("text") == PAUSE_TEXT))
    window._set_busy(False)
    window.token = None
    out.append(check("끝나면 탭이 다시 열리고 [일시정지]·[중단] 은 숨는다",
                     all(str(window.tabs.tab(tab, "state")) == "normal" for tab in others)
                     and not window.pause_btn.grid_info() and not window.stop_btn.grid_info()))
    return out


def check_stale_target(window) -> bool:
    import gui.erpia_app as erpia_app

    closed: list[str] = []
    window.target = SimpleNamespace(started=True, close=lambda: closed.append("close"))

    def failing_flow(options, hooks):
        raise RuntimeError("메일 인증 실패 (가짜)")

    saved = (window._call_flow, window._on_failed, window._on_report_ready,
             erpia_app.report.write)
    window._call_flow = failing_flow
    window._on_failed = lambda *args: None
    window._on_report_ready = lambda *args: None
    erpia_app.report.write = lambda result: None      # 리포트 파일을 남기지 않는다
    try:
        window._run_worker("C:/fake/erp.exe", "c", "u", "p")
    finally:
        (window._call_flow, window._on_failed, window._on_report_ready,
         erpia_app.report.write) = saved
    return check("★ 띄우기 전에 실패해도 **지난 회차 ERPia 를 닫지 않는다**",
                 not closed, f"닫힘 {closed}")


def check_scheduled(window, pick) -> list[bool]:
    """예약마다 고른 기능으로 도는가 (09-21). **저장하지 않는다** — `set_plan` 은 적용
    콜백을 부르지 않고, `on_run` 은 가짜로 바꿔 실행이 시작되지 않는다."""
    from utils import autorun, schedule

    pane = window.autorun_pane
    out = []
    pick("mail")
    for key, var in pane.module_vars.items():
        var.set(key in ("orders", "logistics"))
    text, error = pane._slot_text()
    out.append(check("예약 칸: 체크한 기능이 줄 끝에 붙는다",
                     not error and text.endswith(" orders,logistics"), text))
    for var in pane.module_vars.values():
        var.set(False)
    out.append(check("예약 칸: 기능을 안 고르면 추가하지 않는다", pane._slot_text()[0] == ""))

    plan = schedule.from_settings({"auto_run_enabled": True, "auto_run_mode": "daily", "auto_run_times": [
        "매일 09:00 logistics", "월수금 09:00", "평일 13:00 orders"]}, known_modules=modules.IDS)
    pane.set_plan(plan)
    window._update_source_fields()
    out.append(check("창은 메일만인데 예약에 물류관리가 있으면 택배사 칸을 연다",
                     str(window.courier_entry.cget("state")) == "normal"))
    rows = {row[0]: row[3] for row in pane._slot_rows()}
    out.append(check("예약 목록에 기능 칸 — 기능 없는 줄은 [실행할 기능] 선택대로",
                     rows.get("매일") == "물류관리" and "선택대로" in rows.get("월·수·금", ""),
                     str(rows)))

    runner = autorun.AutoRunner(plan, start=lambda: None, busy=lambda: False,
                                locked=lambda: False)
    saved_runner, saved_on_run = window.autorunner, window.on_run
    started: list[list[str]] = []
    window.autorunner = runner
    window.on_run = lambda: started.append(window._modules())
    try:
        # 2026-09-16 은 수요일. 09:00 에 `매일 09:00 logistics` + `월수금 09:00`(창 선택 = 메일)
        runner._current = autorun.Run(planned_at=time.mktime((2026, 9, 16, 9, 0, 0, 0, 0, -1)))
        window._start_scheduled_run()
        out.append(check("★ 같은 시각 두 줄 → 기능을 합친다 (메일 + 물류관리, 실행 순서)",
                         started == [["mail", "logistics"]], str(started)))
        out.append(check("예약 회차의 계획 표도 그 기능", [s.id for s in window._plan()][-1]
                         == "logistics" and "sales" not in [s.id for s in window._plan()]))
        saved = window._saved_values("C:/fake/erp.exe", "c", "u", "p")["run_modules"]
        out.append(check("★ 예약 회차가 창의 선택(run_modules)을 덮지 않는다", saved == ["mail"],
                         str(saved)))
        window._end_autorun_cycle(autorun.DONE, "")
        out.append(check("회차가 끝나면 창의 선택으로 돌아온다", window._modules() == ["mail"]))
        started.clear()
        runner._current = autorun.Run(planned_at=time.mktime((2026, 9, 17, 13, 0, 0, 0, 0, -1)))
        window._start_scheduled_run()
        out.append(check("목 13:00 `평일 13:00 orders` → 주문수집만", started == [["orders"]],
                         str(started)))
        window._end_autorun_cycle(autorun.DONE, "")
    finally:
        window.autorunner, window.on_run = saved_runner, saved_on_run
        window.unattended = False
        window.scheduled_modules = None
    return out


def check_repair(window, pick) -> list[bool]:
    """경로 두 칸 — [실행] 때 이 PC 에 맞추고, ERPia 가 없으면 실패로 남긴다 (09-29 사용자 요청).

    `find_exe` 는 가짜, 부모 `_begin_run` 은 받은 경로만 적는다 — 실행이 시작되지 않는다. 이력·알림 창도 가짜.
    """
    import logging
    import tempfile

    import gui.erpia_app as erpia_app
    import gui.run_app as run_app
    from automation import application

    out = []
    name = "AppMain.exe"            # 가짜 이름 — 실제 실행 파일 이름·설치 폴더는 git 에 두지 않는다 (10-02)
    keep_name = getattr(SETTINGS, "target_exe_name", None)
    SETTINGS.target_exe_name = name
    with tempfile.TemporaryDirectory(prefix="probe_paths_") as tmp:
        tmp = Path(tmp)
        erpia = tmp / "설치폴더A" / name
        other = erpia.parent / "unins000.exe"
        custom = tmp / "주문엑셀"
        erpia.parent.mkdir()
        custom.mkdir()
        erpia.write_bytes(b"")
        other.write_bytes(b"")
        out.append(check("ERPia 판정 — 설정의 실행 파일 이름만. 다른 exe·없는 파일·빈 값은 아니다",
                         application.is_erpia_exe(str(erpia)) and not application.is_erpia_exe(str(other))
                         and not application.is_erpia_exe(str(tmp / "없음" / name))
                         and not application.is_erpia_exe("")))
        SETTINGS.target_exe_name = None
        out.append(check("★ 실행 파일 이름이 비면 어떤 exe 도 ERPia 가 아니고 찾지도 않는다 (10-02)",
                         not application.is_erpia_exe(str(erpia)) and application.find_exe() is None
                         and application._exe_near(str(erpia.parent)) is None))
        SETTINGS.target_exe_name = name
        fake_pf = tmp / "PF"
        (fake_pf / "제조사A" / "제품A").mkdir(parents=True)
        (fake_pf / "제조사A" / "제품A" / name).write_bytes(b"")
        keep_env = (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
        os.environ["ProgramFiles(x86)"], os.environ["ProgramFiles"] = str(fake_pf), str(tmp / "없는PF")
        try:
            in_pf = application._exe_from_program_files()
        finally:
            for env, value in zip(("ProgramFiles(x86)", "ProgramFiles"), keep_env):
                if value is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = value
        out.append(check("찾기 — Program Files 아래 두 단계에서 이름으로 (폴더 이름을 코드에 두지 않는다)",
                         in_pf == str(fake_pf / "제조사A" / "제품A" / name), str(in_pf)))
        out.append(check("찾기 — 등록 정보·바로가기가 다른 exe(제거 프로그램)를 가리켜도 옆의 ERPia 를 고른다",
                         application._exe_near(f'"{other}",0') == str(erpia)
                         and application._exe_near(str(erpia.parent)) == str(erpia)
                         and application._exe_near(str(custom / "x.exe")) is None))

        found: list[str | None] = [str(erpia)]
        started: list[tuple[str, str]] = []
        recorded, told, gave_up, records = [], [], [], []
        handler = logging.Handler()
        handler.emit = records.append
        saved = (run_app.find_exe, erpia_app.ErpiaWindow._begin_run, run_app.history.record,
                 window.exe_var.get(), window.download_var.get())
        searched: list[int] = []
        run_app.find_exe = lambda hint=None: (searched.append(1), found[0])[1]
        erpia_app.ErpiaWindow._begin_run = lambda self, start_step: started.append(
            (self.exe_var.get(), self.download_var.get()))
        run_app.history.record = lambda **kw: recorded.append(kw)
        window._tell_problem = lambda title, message, kind="error": told.append(message)
        window._give_up_run = gave_up.append
        run_app.log.addHandler(handler)
        try:
            pick("orders", "mail")
            window.exe_var.set("")
            out.append(check("ERPia 경로가 비어도 [실행] 을 막지 않는다 ([실행] 이 찾아 넣는다)",
                             "ERPia 프로그램" not in window._missing(), str(window._missing())))
            for label, exe in (("비어 있으면", ""), ("다른 exe 면", str(other)),
                               ("없는 파일이면", str(tmp / "옛 자리" / name))):
                window.exe_var.set(exe)
                started.clear()
                window.on_run()
                out.append(check(f"★ ERPia 경로가 {label} 찾아 넣고 실행한다",
                                 len(started) == 1 and started[0][0] == str(erpia), str(started)))
            found[0] = str(tmp / "다른 설치" / name)
            started.clear()
            window.on_run()
            out.append(check("ERPia 경로가 맞으면 건드리지 않는다",
                             started and started[0][0] == str(erpia), str(started)))
            out.append(check("경로를 맞춘 것은 로그에 남는다",
                             any("경로를 이 PC 에 맞췄다" in r.getMessage() for r in records)))

            for label, folder in (("비어 있으면", ""), ("없는 폴더면", str(tmp / "없는폴더"))):
                window.download_var.set(folder)
                out.append(check(f"★ 엑셀 저장 폴더가 {label} 되돌리지 않고 '입력 필요' 로 잠근다 (기본값 없음, 10-02)",
                                 "엑셀 저장 폴더" in window._missing() and window.download_var.get() == folder,
                                 str(window._missing())))
            window.download_var.set(str(custom))
            started.clear()
            window.on_run()
            out.append(check("엑셀 저장 폴더가 있는 폴더면 그대로 두고 실행한다",
                             len(started) == 1 and Path(started[0][1]) == custom, str(started)))

            found[0] = None
            window.exe_var.set("")
            started.clear()
            window.on_run()
            out.append(check("★ ERPia 가 없으면 실행하지 않고 실패로 남긴다 — 로그(오류)·[실행 기록]·상태 줄",
                             not started and any(r.levelno >= logging.ERROR and run_app.NO_ERPIA in r.getMessage()
                                                 for r in records)
                             and len(recorded) == 1 and recorded[0]["summary"] == f"실패 — {run_app.NO_ERPIA}"
                             and run_app.NO_ERPIA in window.status_var.get(),
                             f"이력 {recorded} / 상태 {window.status_var.get()}"))
            out.append(check("★ ERPia 가 없으면 알림 창을 띄우고, 예약 회차면 실패로 닫는다",
                             len(told) == 1 and run_app.NO_ERPIA in told[0] and gave_up == [run_app.NO_ERPIA],
                             f"{told} / {gave_up}"))
            recorded.clear()
            keep_pw = window.pw_var.get()
            window.pw_var.set("")
            started.clear()
            window.on_run()
            window.pw_var.set(keep_pw)
            out.append(check("다른 값이 비었으면(서버 확인 전 포함) ERPia 없음으로 남기지 않고 '입력 필요' 로 넘긴다 (09-29 검토)",
                             not recorded and len(started) == 1, f"이력 {recorded} / 부모 {started}"))
            pick("mail")
            started.clear()
            searched.clear()
            window.on_run()
            out.append(check("메일만이면 ERPia 가 없어도 실행하고, ERPia 를 찾지도 않는다",
                             len(started) == 1 and not searched, f"{started} / 찾기 {len(searched)}번"))
        finally:
            run_app.find_exe, erpia_app.ErpiaWindow._begin_run, run_app.history.record = saved[:3]
            del window._tell_problem, window._give_up_run
            run_app.log.removeHandler(handler)

        # 무인 예약 회차가 '입력 필요' 로 시작을 못 하면 [실행 기록]·서버에 남는다 (10-02 검토 — 전에는 아무 데도 없었다)
        recorded, sent = [], []
        keep = run_app.history.record, window._reporter, window.unattended, window.remote_run
        run_app.history.record = lambda **kw: recorded.append(kw)
        window._reporter = lambda: SimpleNamespace(skipped=lambda at, reason, modules=(): sent.append(reason))
        try:
            window.unattended, window.remote_run = True, False
            window._give_up_run("입력값이 비어 있다: 택배사")
            out.append(check("★ 무인 예약이 시작을 못 하면 [실행 기록]·서버(건너뜀)에 '입력 필요' 로 남는다",
                             len(recorded) == 1 and "입력 필요: 택배사" in recorded[0]["summary"]
                             and sent == ["시작하지 못함 — 입력 필요: 택배사"] and not window.unattended,
                             f"{recorded} / {sent}"))
            recorded.clear()
            window._give_up_run("입력값이 비어 있다: 택배사")     # 사람이 누른 실행 — 화면이 알린다
            out.append(check("사람이 누른 실행은 기록하지 않는다 (알림 창이 알린다)", not recorded))
        finally:
            run_app.history.record, window.unattended, window.remote_run = keep[0], keep[2], keep[3]
            del window._reporter

        # 진짜 부모 [실행] — 맞춘 두 경로를 설정 파일에 쓰고 시작하는가. 설정 파일은 임시,
        # 실행 스레드·화면 위 표시는 가짜 (ERPia 를 띄우지 않는다)
        import json

        import config.settings as config_settings

        keep_path, keep_values = config_settings.LOCAL_SETTINGS_PATH, SETTINGS.as_dict()
        temp = tmp / "settings.local.json"
        config_settings.LOCAL_SETTINGS_PATH = temp
        run_app.find_exe = lambda hint=None: str(erpia)
        window._run_worker = lambda *args: None
        window._open_overlay = lambda: None
        try:
            pick("orders", "mail")
            window.exe_var.set("")
            window.download_var.set(str(custom))
            window.on_run()
            written = json.loads(temp.read_text(encoding="utf-8")) if temp.exists() else {}
            out.append(check("★ 진짜 [실행] — 맞춘 ERPia 경로와 저장 폴더를 설정 파일에 저장하고 시작한다",
                             window.busy and written.get("target_exe") == str(erpia)
                             and Path(written.get("download_base_dir") or "") == custom,
                             f"{written.get('target_exe')} / {written.get('download_base_dir')}"))
        finally:
            window._set_busy(False)
            run_app.find_exe = saved[0]
            del window._run_worker, window._open_overlay
            config_settings.LOCAL_SETTINGS_PATH = keep_path
            for key, value in keep_values.items():
                setattr(SETTINGS, key, value)
            window.exe_var.set(saved[3])
            window.download_var.set(saved[4])
    SETTINGS.target_exe_name = keep_name
    return out


def check_small_screen() -> list[bool]:
    """세로 768px 노트북(작업 영역 728px) — 창과 홈이 화면 안에 들어오는가 (09-29 탭 개편).

    예전에는 표·도움말을 줄여 맞췄다. 이제 창은 고정 크기이고 설정 탭만 스크롤한다 —
    창이 작업 영역 안이고, 홈(대기 화면 + 단추 줄)과 예약 탭이 줄이지 않고 창 안에 들어오면 된다.
    """
    import tkinter as tk

    import gui.common as common
    import gui.run_app as run_app

    small = (0, 0, 1366, 728)
    saved = (common.work_area, run_app.work_area)
    common.work_area = run_app.work_area = lambda: small
    root = tk.Tk()
    root.withdraw()
    try:
        window = run_app.RunWindow(root)
        root.update_idletasks()
        room = small[3] - int(common.WINDOW_FRAME_MARGIN * common.ui_scale())
        tab_bar = 40                                    # 탭 머리 + 바깥 여백 (대략)
        home = window.home_tab.winfo_reqheight()
        plan = window.schedule_tab.winfo_reqheight()
        fits = window.window_height <= room and max(home, plan) + tab_bar <= window.window_height
        out = [check("★ 작은 화면에서도 창이 화면 안이고, 홈·예약이 줄이지 않고 들어온다",
                     fits, f"홈 {home}px · 예약 {plan}px / 창 {window.window_height}px / 작업 영역 {room}px")]
        # 홈 [바꾸기] — 설정 탭을 아래로 굴려 둔 채여도 맨 위(실행할 기능)가 보여야 한다 (09-29 실기)
        window.tabs.select(window.settings_tab)
        root.update()
        window.settings_canvas.yview_moveto(1)
        scrolled = window.settings_canvas.yview()[0]
        window._go_modules()
        out.append(check("홈 [바꾸기] 는 설정 탭 맨 위(실행할 기능)를 보여 준다",
                         scrolled > 0 and window.settings_canvas.yview()[0] == 0
                         and window.tabs.select() == str(window.settings_tab),
                         f"굴린 위치 {scrolled:.2f} → {window.settings_canvas.yview()[0]:.2f}"))
        return out
    finally:
        common.work_area, run_app.work_area = saved
        root.destroy()


def shoot_window() -> None:
    """`--shot`: 실행용 창을 선택 상태별로 **그림으로** 남긴다 (`logs/shot_run_*.png`).

    확인 도구는 값만 센다. 줄이 밀리거나 글자가 잘리는 것은 찍어서 봐야 안다.
    찍은 그림은 중간 파일이다 — 보고 나면 지운다.
    """
    import tkinter as tk

    from PIL import ImageGrab

    from gui.common import apply_scaling
    from gui.run_app import RunWindow
    from utils.dpi import ensure_dpi_awareness

    from utils import autorun, schedule

    ensure_dpi_awareness()
    out_dir = Path(__file__).resolve().parent.parent / "logs"
    for name, ids in (("all", modules.IDS), ("orders_logistics", ("orders", "logistics")),
                      ("mail_only", ("mail",)), ("autorun", modules.IDS)):
        root = tk.Tk()
        try:
            apply_scaling(root)
            window = RunWindow(root)
            for module in modules.ALL:
                window.module_vars[module.id].set(module.id in ids)
            if name == "autorun":
                # 예약 칸을 예시로 채운다. **저장하지 않는다** — `set_plan` 은 적용 콜백을
                # 부르지 않고, 시계는 한 번도 `tick()` 하지 않아 실행이 시작될 수 없다.
                plan = schedule.from_settings({
                    "auto_run_enabled": True, "auto_run_mode": "daily",
                    "auto_run_times": ["평일 09:00 mail,orders", "월수금 15:30",
                                       "매월 25일 18:00 logistics", "2026-12-31 10:00 "
                                       + ",".join(modules.IDS)]}, known_modules=modules.IDS)
                pane = window.autorun_pane
                pane.set_plan(plan)
                pane.repeat_var.set("요일 지정")
                pane._show_repeat_fields()
                for index in (1, 3):
                    pane.weekday_vars[index].set(True)
                runner = autorun.AutoRunner(plan, start=lambda: None,
                                            busy=lambda: False, locked=lambda: False)
                runner.reschedule()
                pane.refresh(runner)
                window.next_var.set(runner.status_line())     # 홈의 '다음 예약' — 창의 1초 시계가 하는 일
            root.deiconify()
            root.geometry("+40+20")          # 화면 위쪽에 — 아래가 화면 밖으로 나가면 찍히지 않는다
            root.lift()
            root.attributes("-topmost", True)
            # 09-29 개편: 탭마다 찍는다. 전부 고른 상태에서만 탭 넷 + 진행 화면
            shots = [("home", window.home_tab)]
            if name == "all":
                shots += [("schedule", window.schedule_tab), ("history", window.history_tab),
                          ("settings", window.settings_tab), ("running", window.home_tab)]
            for tab_name, tab in shots:
                window.tabs.select(tab)
                if tab_name == "running":
                    # 진행 화면 모양만 — 실행하지 않는다 (흐름·저장·오버레이를 부르지 않는다).
                    # 09-29: 실행 중 모양(탭 잠금·[일시정지]·[중단])과 큰 단계 진행을 같이 찍는다
                    from dataclasses import replace

                    from orchestrator import steps
                    from utils import cancel

                    window.token = cancel.CancelToken()
                    window._set_busy(True)
                    window._set_form_visible(False)
                    window._set_progress_visible(True)
                    done = {"preflight", "mail_login", "mailbox", "launch", "login", "screen", "excel"}
                    states = {**{step_id: steps.DONE for step_id in done},
                              "site": steps.SKIPPED, "sales": steps.RUNNING}
                    window.step_pane.show_plan([replace(event, state=states.get(event.step_id, steps.PENDING))
                                                for event in steps.preview(window._plan())])
                # 탭을 바꾸면 여러 위젯이 다시 그려진다. "다 그렸다" 는 신호가 없어서
                # 이벤트 고리를 잠깐 돌려 그리기를 끝낸다 (상한 0.6초).
                root.after(600, root.quit)
                root.mainloop()
                box = (root.winfo_rootx(), root.winfo_rooty(),
                       root.winfo_rootx() + root.winfo_width(),
                       root.winfo_rooty() + root.winfo_height())
                path = out_dir / f"shot_run_{name}_{tab_name}.png"
                ImageGrab.grab(bbox=box, all_screens=True).save(path)
                log.info("  찍음  %s (%dx%d)", path.name, box[2] - box[0], box[3] - box[1])
            if name == "autorun":
                # 맨 위 알림 창 둘 (10-01) — 단추는 닫기만 한다 (건너뛰기·점검을 부르지 않는다)
                from collect import phonelink
                from gui import run_app

                soon = time.time() + 240
                for shot, kind, title, buttons, text in (
                        ("notice_erpia", run_app.NOTICE_ERPIA, "곧 예약 실행", (run_app.SKIP_TEXT, "닫기"),
                         RunWindow._erpia_notice_text(soon, 240)),
                        ("notice_phone", run_app.NOTICE_PHONE, "휴대폰 인증 확인", ("다시 확인", "닫기"),
                         RunWindow._phone_notice_text(soon, phonelink.PEEK_OFFLINE))):
                    window._show_notice(kind, title, [(label, window._close_notice) for label in buttons]).set(text)
                    notice = window._notice
                    notice.geometry("+820+20")
                    root.after(400, root.quit)
                    root.mainloop()
                    box = (notice.winfo_rootx(), notice.winfo_rooty(),
                           notice.winfo_rootx() + notice.winfo_width(), notice.winfo_rooty() + notice.winfo_height())
                    path = out_dir / f"shot_run_{shot}.png"
                    ImageGrab.grab(bbox=box, all_screens=True).save(path)
                    log.info("  찍음  %s (%dx%d)", path.name, box[2] - box[0], box[3] - box[1])
                    window._close_notice()
        finally:
            root.destroy()


def main() -> int:
    setup_logging()
    from tools import probe_guard

    if "--shot" in sys.argv:
        with probe_guard.offline():      # 창을 만들면 그것만으로 서버 확인이 나간다
            shoot_window()
        return 0
    # 가짜 실행이 **진짜 이력**(`logs/history.jsonl`)에 줄을 남기지 않게 임시 파일로 돌린다 (09-22)
    history.HISTORY_PATH = history.LOG_DIR / "_probe_history_modules.jsonl"
    try:
        with probe_guard.offline():      # 실서버로 가짜 실행이 보고되지 않게 (09-23 사고)
            return _main()
    finally:
        history.HISTORY_PATH.unlink(missing_ok=True)


def _main() -> int:
    results: list[bool] = []
    log.info("▶ 기능 이름 정리")
    results.extend(check_normalize())
    log.info("▶ 계획 (대시보드에 그릴 단계)")
    results.extend(check_plan())
    log.info("▶ 통합 흐름 — 고른 것만 부르는가 (가짜 함수)")
    results.extend(check_flow())
    log.info("▶ 메일이 멈춰도 뒤 기능은 도는가 (가짜 함수)")
    results.extend(check_mail_failure())
    log.info("▶ 실행용 창 — 필수값·입력 잠금")
    results.extend(check_window())
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
