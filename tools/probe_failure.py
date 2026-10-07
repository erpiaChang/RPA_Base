r"""[조사 도구 - 읽기 전용] **실패했을 때 제대로 실패하는지** 확인한다.

대상 프로그램을 건드리지 않는다. 가짜 흐름에 일부러 실패를 심어 본다.

    .venv\Scripts\python.exe -m tools.probe_failure

## 왜 이 도구가 있나 (2026-09-17)

`docs/ERROR_HANDLING.md` 에 "실패하면 이렇게 한다" 가 적혀 있다 — 스크린샷을
남기고, 재시도는 상한이 있고, 중단은 실패가 아니고, 리포트가 만들어진다.

그런데 **그 경로들이 실제로 도는 것을 확인한 적이 없다.** 성공하는 흐름만
돌려 봤기 때문이다. 실패는 드물게 나고, 날 때는 실계정이 걸려 있어 관찰할
여유가 없다.

여기서는 **실패를 일부러 만들어** 다음을 본다.

1. 단계가 실패하면 그 뒤 단계를 **건너뛰는가**
2. 실패가 **`Result` 와 리포트에 남는가**
3. **중단은 실패로 세지 않는가** (`Cancelled` 는 BaseException 이다)
4. 재시도 상한이 **지켜지는가** (무제한 Retry 금지)
5. 실패해도 **훅이 계속 불리는가** (화면이 멎지 않는다)

★ 여기서 확인하는 것은 **흐름 틀의 실패 처리**다. 각 업무 단계가 실제
  ERPia 에서 어떻게 실패하는지는 실행해 봐야 안다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from orchestrator import steps  # noqa: E402
from orchestrator.common import Hooks, Result  # noqa: E402
from utils import cancel  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402
from utils.wait import WaitTimeout  # noqa: E402

log = get_logger(__name__)

PLAN = [
    steps.Step(id="a", name="첫째", reentry=steps.SAFE),
    steps.Step(id="b", name="둘째", reentry=steps.SAFE),
    steps.Step(id="c", name="셋째", reentry=steps.WRITE),
]


def recorder() -> tuple[Hooks, list]:
    """화면 대신 받아 적는 훅. **실패 뒤에도 통지가 오는지** 보려는 것이다.

    ★ `Hooks` 는 dataclass 이고 `on_step` 은 **메서드가 아니라 필드**다.
      상속해서 메서드로 덮으면 필드가 이기므로 통지가 오지 않는다
      (2026-09-17에 그렇게 만들었다가 `seen` 이 빈 채로 나왔다).
    """
    seen: list[tuple[str, str]] = []
    hooks = Hooks(plan=list(PLAN),
                  on_step=lambda event: seen.append((event.name, event.state)))
    return hooks, seen


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


class Boom(RuntimeError):
    """일부러 낸 실패."""


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 단계가 실패하면 그 뒤를 건너뛴다")
    hooks, seen = recorder()
    hooks.begin(PLAN)
    failure = ""
    try:
        with hooks.stage(PLAN[0]):
            pass
        with hooks.stage(PLAN[1]):
            raise Boom("둘째에서 일부러 실패")
        with hooks.stage(PLAN[2]):         # 여기까지 오면 안 된다
            results.append(check("★ 실패 뒤 단계를 건너뛴다", False,
                                 "셋째가 실행됐다"))
    except Boom as exc:
        failure = str(exc)
        results.append(check("★ 실패가 위로 올라온다 (조용히 넘어가지 않는다)",
                             True, failure))
    states = dict(seen)
    results.append(check("첫째는 완료로 남는다", states.get("첫째") == steps.DONE,
                         str(seen)))
    results.append(check("★ 둘째는 **실패**로 남는다",
                         states.get("둘째") == steps.FAILED, str(seen)))
    # ★ 내 판정이 처음에 틀렸다 (2026-09-17). 셋째도 통지는 온다 —
    #   `begin()` 이 **계획 전체를 대기 상태로** 미리 알리기 때문이다.
    #   그래야 화면이 "몇 단계 중 몇 번째" 를 그릴 수 있다.
    #   확인할 것은 "통지가 오는가" 가 아니라 **"실행된 것처럼 보이는가"** 다.
    results.append(check("★ 셋째는 **대기인 채로 남는다** (실행되지 않았다)",
                         states.get("셋째") == steps.PENDING,
                         f"셋째={states.get('셋째')}"))
    started = [name for name, state in seen if state == steps.RUNNING]
    results.append(check("★ 실패 뒤 단계를 **시작하지 않는다**",
                         "셋째" not in started, f"시작된 것: {started}"))

    log.info("▶ 실패한 단계가 **기록에 남는다**")
    rows = hooks.report()
    broken = [r for r in rows if r.get("state") == steps.FAILED]
    results.append(check("★ 실패한 단계가 기록에 남는다", len(broken) == 1,
                         str([r.get("name") for r in broken])))
    # 09-21: `error` 는 사람 말(어느 단계에서 멈췄는지), 원인 원문은 `error_detail`.
    results.append(check("★ 무엇이 실패했는지 적힌다 (단계 + 원인 원문)",
                         any("둘째" in str(r.get("name")) for r in broken)
                         and any(str(r.get("error")).startswith("[둘째] 단계에서")
                                 and "일부러 실패" in str(r.get("error_detail"))
                                 for r in broken),
                         str(broken[:1])))

    log.info("▶ ★ 중단은 **실패가 아니다** (`Cancelled` 는 BaseException)")
    results.append(check("`Cancelled` 는 `Exception` 에 잡히지 않는다",
                         not issubclass(cancel.Cancelled, Exception),
                         f"바탕: {cancel.Cancelled.__mro__[1].__name__}"))
    hooks2, seen2 = recorder()
    hooks2.begin(PLAN)
    token = cancel.CancelToken()
    caught = None
    try:
        with hooks2.stage(PLAN[0]):
            token.cancel("시험")
            token.raise_if_cancelled()
    except cancel.Cancelled as exc:
        caught = exc
    except Exception:                      # noqa: BLE001 - 잡히면 안 된다
        results.append(check("★ 중단이 일반 예외로 잡혔다", False))
    results.append(check("★ 중단은 `Cancelled` 로만 잡힌다", caught is not None,
                         str(caught)))
    results.append(check("★ 중단한 단계는 **실패가 아니라 중단**으로 남는다",
                         dict(seen2).get("첫째") == steps.CANCELLED,
                         str(seen2)))

    log.info("▶ 재시도 상한이 **값으로** 정해져 있다 (무제한 금지)")
    # 범용 retry 헬퍼는 없다 — 재시도 상한은 자리마다 상수다 (docs/ERROR_HANDLING.md 3절 표).
    # 그 상수들이 작은 정수인지 본다. (옛 `config.Retry` 는 어디서도 안 써서 09-30 에 지웠다)
    from automation import logistics_wait, order_mapping
    from collect import webmail

    caps = {"엑셀 업로드": order_mapping.UPLOAD_ATTEMPTS, "메일 열기": webmail.OPEN_ATTEMPTS,
            "메일 사이트 인증": webmail.AUTH_ATTEMPTS, "인증번호 다시 요청": webmail.CODE_RETRIES,
            "메일 목록 페이지": webmail.MAX_PAGES, "하단 스캔 페이지": logistics_wait.MAX_SCROLLS}
    results.append(check("★ 재시도 상한이 자리마다 작은 정수로 있다 (무제한이 아니다)",
                         all(isinstance(v, int) and 1 <= v <= 60 for v in caps.values()), str(caps)))

    log.info("▶ 대기에는 상한이 있다 (영원히 기다리지 않는다)")
    try:
        from utils.wait import wait_for

        wait_for(lambda: False, "절대 안 되는 조건", timeout=0.2,
                 interval=0.05)
        results.append(check("★ 시간이 다 되면 예외를 올린다", False))
    except WaitTimeout as exc:
        results.append(check("★ 시간이 다 되면 `WaitTimeout` 을 올린다", True,
                             str(exc)[:50]))

    log.info("▶ 실패한 실행도 리포트가 만들어진다")
    results += _check_report(hooks)

    results += _check_popup_recover()
    results += _check_dismiss()
    results += _check_close_others()
    results += _check_auth_retry()
    results += _check_sales_popup_race()
    results += _check_selected_sales_menu()
    results += _check_sales_mode()
    results += _check_resume_from()
    results += _check_collect_wait()
    results += _check_real_popup()
    results += _check_password_change()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 여기서 확인한 것은 **흐름 틀의 실패 처리**다. 각 업무 단계가 "
             "실제 대상 프로그램에서 어떻게 실패하는지는 실행해 봐야 안다.")
    return 0 if all(results) else 1


def _check_sales_popup_race() -> list[bool]:
    """매출처리 팝업 — [예] 뒤 닫히는 중인 같은 창·처리 중 창을 누르지도, 거기서 멈추지도 않는다.

    09-29 실계정: 확인 창 [예] 직후 같은 창을 2번으로 잡아 `본문 없음` → 단추 읽기에서
    COMError(-2147220991) → 흐름이 ERPia 를 닫았다 (09-17 HANDOFF 3-1). 찾기·읽기·누르기는 가짜다.
    """
    import _ctypes

    from automation import order_mapping as om
    from config.settings import SETTINGS

    log.info("▶ 매출처리 팝업 경합 (가짜 팝업)")
    # 10-01 부터 다시 [매출처리] 단추의 '일괄' 확인 창이다 (09-04 실측 문구). 선택주문 확인 창도 [예] 로 받는다
    confirm = "매핑되어 있고 정상인 매출건에 대해 일괄 매출처리를 진행하시겠습니까?"
    selected = "선택하신 주문에 대해 매출처리를 진행하시겠습니까? 계속진행을 원하시면 예를 클릭하세요."
    done = "정상매출처리 성공"
    yes_no, ok = ["예(Y)", "아니오(N)", "닫기"], ["확인(O)", "닫기"]
    vanished = _ctypes.COMError(-2147220991, "이벤트에서 가입자를 불러낼 수 없습니다.", (None,) * 5)

    class Fake:
        def __init__(self, handle, body, buttons):
            self.handle, self.body, self.buttons = handle, body, buttons

    answers: list[str] = []         # 누른 단추의 정규식 — 확인 창에는 [예] 여야 한다

    def run(sequence):
        items = list(sequence)
        clicked, waits = [], []
        answers.clear()

        def wait_dialog(scopes, what, timeout):
            waits.append(timeout)
            return Fake(*items.pop(0)) if items else None

        def buttons_of(dialog):
            if isinstance(dialog.buttons, Exception):
                raise dialog.buttons
            return dialog.buttons

        names = ("_wait_sales_dialog", "_scope_pid", "_wait_window_closed", "_wait_sales_dialogs_closed")
        saved = [getattr(om, name) for name in names]
        saved_dialogs = (om.dialogs.text_of, om.dialogs.buttons_of, om.dialogs.click_button)
        om._wait_sales_dialog = wait_dialog
        om._scope_pid = lambda scopes: 1
        om._wait_window_closed = lambda pid, handle, what, timeout: None
        om._wait_sales_dialogs_closed = lambda scopes: None
        om.dialogs.text_of = lambda dialog: dialog.body
        om.dialogs.buttons_of = buttons_of
        om.dialogs.click_button = lambda dialog, title_re, what, dry_run=False: (
            clicked.append(dialog.handle), answers.append(title_re), "Invoke")[2]
        try:
            try:
                return om._handle_sales_dialogs([]), clicked, waits, None
            except Exception as exc:        # noqa: BLE001 — 멈추면 실패로 적는다
                return [], clicked, waits, exc
        finally:
            for name, value in zip(names, saved):
                setattr(om, name, value)
            om.dialogs.text_of, om.dialogs.buttons_of, om.dialogs.click_button = saved_dialogs

    out = []
    handled, clicked, waits, error = run([(10, confirm, yes_no), (10, "", vanished), (20, "", []),
                                          (30, done, ok)])
    out.append(check("★ 09-29 재현 — 닫히는 중인 확인 창(읽다 COMError)·처리 중 창을 건너뛰고 결과 창만 누른다",
                     error is None and clicked == [10, 30] and handled == [confirm, done],
                     f"누름 {clicked} / 오류 {error!r}"))
    out.append(check("건너뛴 창이 있어도 결과 팝업은 긴 대기로 기다린다 (3초로 줄지 않는다)",
                     waits and all(t == SETTINGS.timeouts.long_task for t in waits), str(waits)))
    handled, clicked, _, error = run([(10, confirm, yes_no), (10, confirm, yes_no), (30, done, ok)])
    out.append(check("★ 누른 확인 창이 아직 떠 있어도 [예] 를 두 번 누르지 않는다",
                     error is None and clicked == [10, 30], f"누름 {clicked} / 오류 {error!r}"))
    handled, clicked, _, error = run([(10, confirm, yes_no), (30, done, ok)])
    out.append(check("평소 2단 — 확인 [예] → 결과 [확인]", error is None and clicked == [10, 30], str(clicked)))
    handled, clicked, _, error = run([(10, confirm, yes_no), (10, done, ok)])
    out.append(check("같은 창에 본문이 바뀌어 다시 뜨면 새 팝업으로 누른다",
                     error is None and clicked == [10, 10], str(clicked)))
    handled, clicked, _, error = run([(10, confirm, yes_no), (30, done, ok)])
    out.append(check("★ '일괄' 확인 창([매출처리] 단추)에 [예] → 결과 [확인] — 거절하지 않는다 (10-01 되돌림)",
                     error is None and clicked == [10, 30]
                     and answers == [om.dialogs.CONFIRM_YES_RE] * 2,
                     f"누름 {clicked} {answers} / 오류 {error!r}"))
    handled, clicked, _, error = run([(10, selected, yes_no), (30, done, ok)])
    out.append(check("선택주문 확인 창도 [예] → 결과 [확인] (그 경로로 다시 바꿀 때)",
                     error is None and clicked == [10, 30], str(clicked)))
    return out


def _check_selected_sales_menu() -> list[bool]:
    """[선택주문 매출처리] 누르기 (09-29). 우클릭·메뉴 찾기는 가짜다.

    실계정에서 메뉴를 Invoke 로 누르자 확인 창(모달)이 닫힐 때까지 5분 넘게 돌아오지 않았다 → 마우스 클릭만.
    시험 실행은 우클릭을 안 하므로 메뉴를 찾으면 안 된다 (20초 버리고 '못 찾음').
    """
    from automation import order_mapping as om

    log.info("▶ 선택주문 매출처리 메뉴 (가짜 우클릭·메뉴)")
    calls: list = []
    saved = (om._context_row, om._scope_pid, om.ui.cell, om.ui.right_click, om.ui.click_process_menu_item)
    om._context_row = lambda grid: "행"
    om._scope_pid = lambda scopes: 1
    om.ui.cell = lambda row, column: f"{column} 셀"
    om.ui.right_click = lambda ctrl, what, dry_run=False: calls.append(("우클릭", ctrl, dry_run))
    om.ui.click_process_menu_item = lambda pid, title, dry_run=False, methods=("Invoke", "클릭"): (
        calls.append(("메뉴", title, methods)), "클릭")[1]
    try:
        dry = om.open_selected_sales(None, None, dry_run=True)
        dry_calls = list(calls)
        calls.clear()
        real = om.open_selected_sales(None, None)
    finally:
        (om._context_row, om._scope_pid, om.ui.cell, om.ui.right_click,
         om.ui.click_process_menu_item) = saved
    return [
        check("시험 실행은 메뉴를 찾지 않는다 (우클릭도 안 했다)",
              dry == "dry-run" and dry_calls == [("우클릭", "사이트코드 셀", True)], str(dry_calls)),
        check("★ 실제로는 사이트코드 셀 우클릭 → [선택주문 매출처리] 를 **마우스 클릭만**으로 (Invoke 는 모달에 막힌다)",
              real == "클릭" and calls == [("우클릭", "사이트코드 셀", False),
                                          ("메뉴", "선택주문 매출처리", ("클릭",))], str(calls)),
    ]


def _check_sales_mode() -> list[bool]:
    """매출처리 방식 `sales_mode` (10-01) — 전체는 [매출처리] 단추, 선택주문은 전체 체크 + 우클릭 메뉴. 화면은 가짜다."""
    from automation import order_mapping as om

    log.info("▶ 매출처리 방식 고르기 (가짜 화면)")
    calls: list = []
    names = ("orders_in_grid", "collect_checks", "unsold_count", "_sales_button",
             "select_all_orders", "open_selected_sales", "_handle_sales_dialogs")
    saved = [getattr(om, name) for name in names]
    saved_ui = (om.ui.find, om.ui.click, om.ui.describe)
    unsold = iter(["2건", "1건"] * 2)
    om.orders_in_grid = lambda screen, grid: 1
    om.collect_checks = lambda screen: (0, 1, 0)
    om.unsold_count = lambda screen: next(unsold)
    om._sales_button = lambda screen: "매출처리"
    om.select_all_orders = lambda grid, dry_run=False: calls.append("전체 체크")
    om.open_selected_sales = lambda screen, grid, dry_run=False: (calls.append("메뉴"), "클릭")[1]
    om._handle_sales_dialogs = lambda scopes: ["성공"]
    om.ui.find = lambda *args, **kwargs: "주문 그리드"
    om.ui.click = lambda ctrl, what, dry_run=False, **kwargs: (calls.append(f"단추 {ctrl}"), "Invoke")[1]
    om.ui.describe = str
    try:
        whole = om.process_sales("화면", wait_collect=False, mode=om.SALES_ALL)
        whole_calls = list(calls)
        calls.clear()
        picked = om.process_sales("화면", wait_collect=False, mode=om.SALES_SELECTED)
        picked_calls = list(calls)
        try:
            om.normalized_sales_mode("일괄")
            bad = None
        except om.ScreenError as exc:
            bad = exc
        try:                              # 기본값 없음 (10-02) — 빈 값도 화면을 건드리기 전에 멈춘다
            om.normalized_sales_mode("")
            empty = None
        except om.ScreenError as exc:
            empty = exc
    finally:
        for name, value in zip(names, saved):
            setattr(om, name, value)
        om.ui.find, om.ui.click, om.ui.describe = saved_ui
    return [
        check("★ 전체 = [매출처리] 단추만 (행 체크·메뉴 없음), 결과에 조회된 주문수를 넣지 않는다",
              whole_calls == ["단추 매출처리"] and "selected" not in whole, f"{whole_calls} {whole}"),
        check("★ 선택주문 = 전체 체크 → 우클릭 메뉴, 단추는 안 누른다. 결과에 조회된 주문수",
              picked_calls == ["전체 체크", "메뉴"] and picked.get("selected") == 1, f"{picked_calls} {picked}"),
        check("빈 값·모르는 값은 화면을 건드리기 전에 멈춘다 (비어도 '전체' 로 가지 않는다, 10-02)",
              bad is not None and empty is not None, f"{bad!r} {empty!r}"),
    ]


def _check_auth_retry() -> list[bool]:
    """메일 사이트 2차 인증 실패 → 1분 뒤 한 번 더 (`AUTH_ATTEMPTS`). 공용 틀(`webmail.login`)을 가짜 사이트로 본다."""
    from types import SimpleNamespace

    from collect import auth_code, webmail

    log.info("▶ 메일 사이트 인증 실패 뒤 재시도 — 한 번만, 분이 바뀌도록 쉬고")
    saved = (webmail.pause, webmail.SETTINGS)
    out: list[bool] = []

    def run(failures: list[BaseException]):
        calls, pauses = [], []

        def once(page):
            calls.append(1)
            if failures:
                raise failures.pop(0)

        site = SimpleNamespace(login_once=once)
        webmail.pause = lambda seconds, why: pauses.append(seconds)
        webmail.SETTINGS = SimpleNamespace(require=lambda *keys: None)
        try:
            webmail.login(None, site)
            error = None
        except Exception as exc:              # noqa: BLE001 — 무엇이 올라오는지 본다
            error = exc
        return len(calls), pauses, error

    try:
        n, pauses, error = run([webmail.LoginError("인증번호 거부")])
        out.append(check("★ 한 번 실패하면 쉬고 다시 해서 성공한다",
                         n == 2 and pauses == [webmail.AUTH_RETRY_DELAY] and error is None,
                         f"시도 {n}회, 쉼 {pauses}"))
        n, pauses, error = run([webmail.LoginError("1"), webmail.LoginError("2"),
                                webmail.LoginError("3")])
        out.append(check("★ 계속 실패해도 두 번에서 멈추고 실패를 올린다 (무제한 금지)",
                         n == webmail.AUTH_ATTEMPTS == 2 and len(pauses) == 1
                         and isinstance(error, webmail.LoginError), f"시도 {n}회"))
        # 09-28 사용자 확정: 안 오면 1분 뒤 최대 2번 더 요청하고 중단
        n, pauses, error = run([auth_code.CodeNotArrived(str(k)) for k in range(5)])
        out.append(check("★ 인증번호가 **안 오면** 1분 뒤 2번 더 요청하고 중단한다 (모두 3번)",
                         n == 3 and pauses == [webmail.AUTH_RETRY_DELAY] * 2
                         and isinstance(error, auth_code.CodeNotArrived), f"시도 {n}회, 쉼 {pauses}"))
        n, pauses, error = run([auth_code.CodeNotArrived("안 옴")])
        out.append(check("한 번 안 왔다가 다음 요청에 오면 성공한다",
                         n == 2 and error is None, f"시도 {n}회"))
        n, pauses, error = run([auth_code.AuthCodeError("휴대폰 연결이 준비되지 않았다")])
        out.append(check("안 온 것이 아닌 인증 오류(준비 안 됨 등)는 다시 하지 않는다",
                         n == 1 and not pauses and type(error) is auth_code.AuthCodeError,
                         f"시도 {n}회"))
    finally:
        webmail.pause, webmail.SETTINGS = saved

    from collect import phonelink
    from utils.wait import WaitTimeout

    def kind_of(exc):
        try:
            auth_code._reraise(exc)
        except auth_code.AuthCodeError as wrapped:
            return type(wrapped)

    timed_out = phonelink.PhoneLinkError("120초 안에 인증 문자가 오지 않았다")
    timed_out.__cause__ = WaitTimeout("인증 문자 도착", 120)
    out.append(check("대기 시간 초과만 '안 왔다'(`CodeNotArrived`)로 분류한다",
                     kind_of(timed_out) is auth_code.CodeNotArrived
                     and kind_of(phonelink.PhoneLinkError("숫자 후보가 여러 개")) is auth_code.AuthCodeError))
    return out


def _check_close_others() -> list[bool]:
    """중복 로그인 때 **같은 계정 인스턴스만** 닫는다 (09-28 — 다른 계정 ERPia 까지 닫던 결함)."""
    import re

    from automation import application
    from utils.winprobe import Window

    log.info("▶ 중복 로그인 — 같은 계정으로 로그인된 인스턴스만 닫는다")
    titles = {11: "user01 - user01", 22: "other01 - other01", 33: "ERPia 로그인", 44: "우리 것"}

    class FakeApp:
        def __init__(self, **_):
            pass

        def connect(self, process):
            return self

    saved = (application.pids_for_exe, application.winprobe.top_windows,
             application.Application, application.Target.close, application.wait_gone)
    closed_pids: list[int] = []
    try:
        application.pids_for_exe = lambda exe: list(titles)
        application.winprobe.top_windows = lambda pid, visible_only=True: [
            Window(handle=pid, class_name="", title=titles[pid], pid=pid, visible=True)]
        application.Application = FakeApp
        application.Target.close = lambda self: closed_pids.append(self.pid) or True
        application.wait_gone = lambda exe, pids: []
        closed = application.close_others(
            "ERPia.exe", keep_pid=44,
            title_re=f"{re.escape('user01')}.*{re.escape('user01')}")
    finally:
        (application.pids_for_exe, application.winprobe.top_windows,
         application.Application, application.Target.close, application.wait_gone) = saved
    out = [check("★ 같은 계정 인스턴스만 닫는다 (다른 계정·로그인 창·우리 것은 둔다)",
                 closed == [11] and closed_pids == [11], f"닫음={closed}")]

    # 예약 전 경고가 쓰는 '같은 아이디로 로그인된 ERPia' (10-01) — 읽기만 한다
    saved_find = (application.pids_by_name, application.winprobe.top_windows,
                  getattr(application.SETTINGS, "target_exe_name", None))
    asked: list[str] = []
    try:
        application.SETTINGS.target_exe_name = "AppMain.exe"      # 가짜 이름 — 실제 이름은 git 에 두지 않는다 (10-02)
        application.pids_by_name = lambda name: asked.append(name) or list(titles)
        application.winprobe.top_windows = lambda pid, visible_only=True: [
            Window(handle=pid, class_name="", title=titles[pid], pid=pid, visible=True)]
        found = application.logged_in_pids("user01", "user01")
        empty = application.logged_in_pids("", "")
        application.SETTINGS.target_exe_name = None
        unnamed = application.logged_in_pids("user01", "user01")
    finally:
        (application.pids_by_name, application.winprobe.top_windows,
         application.SETTINGS.target_exe_name) = saved_find
    out.append(check("예약 전 경고 — 같은 계정으로 로그인된 ERPia 만 찾는다 (로그인 창·다른 계정 빼고)",
                     found == [11] and empty == [], f"{found} / {empty}"))
    out.append(check("프로세스는 설정의 실행 파일 이름으로 찾는다 · 이름이 비면 찾지 않는다 (10-02)",
                     asked == ["AppMain.exe"] and unnamed == [], f"{asked} / {unnamed}"))
    return out


def _check_resume_from() -> list[bool]:
    """[멈춘 곳부터 다시] (10-01) — 지난 실행이 멈춘 기능부터 끝까지. 이력 한 줄로 계산한다."""
    from orchestrator import modules

    log.info("▶ 멈춘 곳부터 다시")
    every = ["메일 엑셀 받기", "주문수집·매출처리", "물류대기", "물류관리"]

    def entry(state, *rows, names=every):
        return {"state": state, "modules": names, "steps": [{"id": i, "state": s} for i, s in rows]}

    done = [("preflight", "done"), ("mail_login", "done"), ("mailbox", "done"), ("launch", "done"),
            ("login", "done"), ("screen", "done"), ("excel", "done"), ("site", "done"), ("sales", "done"),
            ("logistics_wait", "done"), ("logistics", "done")]

    def with_state(step_id, state, after="done"):
        rows, seen = [], False
        for sid, _ in done:
            rows.append((sid, state if sid == step_id else after if seen else "done"))
            seen = seen or sid == step_id
        return rows

    mail_failed = entry("unfinished", *[(i, "failed" if i == "mail_login" else "skipped" if i == "mailbox"
                                         else "done") for i, _ in done])
    return [
        check("★ 메일만 실패하고 뒤가 돌았으면 메일부터 끝까지 (받은 엑셀을 올려야 해서)",
              modules.resume_from(mail_failed) == ["mail", "orders", "logistics_wait", "logistics"],
              str(modules.resume_from(mail_failed))),
        check("★ 물류대기에서 멈췄으면 물류대기·물류관리",
              modules.resume_from(entry("failed", *with_state("logistics_wait", "failed", "pending")))
              == ["logistics_wait", "logistics"]),
        check("ERPia 실행이 실패하면 첫 ERPia 기능부터 (실행·로그인 단계는 그 기능 몫)",
              modules.resume_from(entry("failed", *with_state("launch", "failed", "pending")))
              == ["orders", "logistics_wait", "logistics"]),
        check("다 끝났거나(완료) 물류대기 메뉴가 없어 건너뛴 것은 다시 할 것이 없다",
              modules.resume_from(entry("done", *done)) == []
              and modules.resume_from(entry("unfinished", *with_state("logistics_wait", "skipped"))) == []),
        check("그 실행이 고른 기능만 — 고르지 않은 기능은 넣지 않는다",
              modules.resume_from(entry("failed", ("launch", "done"), ("login", "done"), ("logistics_wait", "failed"),
                                        ("logistics", "pending"), names=["물류대기", "물류관리"]))
              == ["logistics_wait", "logistics"]),
        check("기능 고정 빌드는 그 빌드가 아는 기능만",
              modules.resume_from(entry("failed", *with_state("screen", "failed", "pending")),
                                  known=("orders",)) == ["orders"]),
        check("시작도 못 한 실패(단계 기록 없음)는 다시 할 것이 없다",
              modules.resume_from({"state": "failed", "modules": every, "steps": []}) == []),
    ]


def _check_popup_recover() -> list[bool]:
    """단계가 실패했는데 ERPia 알림([확인] 하나)을 닫았으면 **다음 단계로** (09-22 사용자 확정)."""
    log.info("▶ ERPia 알림을 닫았으면 그 단계만 실패로 적고 다음 단계로")
    out = []
    hooks, seen = recorder()
    hooks.recover = lambda: "저장실패: 연락처를 입력하세요"
    hooks.begin(PLAN)
    reached = []
    try:
        with hooks.stage(PLAN[0]):
            raise Boom("팝업에 막혀 실패")
        with hooks.stage(PLAN[1]):
            reached.append("둘째")
        escaped = None
    except Boom as exc:
        escaped = exc
    states = dict(seen)
    out.append(check("★ 알림을 닫았으면 예외가 올라오지 않고 다음 단계가 돈다",
                     escaped is None and reached == ["둘째"], str(seen)))
    out.append(check("그 단계는 **실패**로 남는다 (완료로 속이지 않는다)",
                     states.get("첫째") == steps.FAILED and states.get("둘째") == steps.DONE))
    out.append(check("★ '확인할 것' 에 알림 글이 남는다",
                     any("연락처를 입력하세요" in item and "[첫째]" in item
                         for item in hooks.attention), str(hooks.attention)))

    # 알림을 닫고 넘어간 뒤 **다른 단계가 알림 없이 멈추면** 그 단계의 이유를 보여야 한다 (09-22 검토)
    popups = iter(["알림", None])
    hooks, _seen = recorder()
    hooks.recover = lambda: next(popups)
    hooks.begin(PLAN)
    try:
        with hooks.stage(PLAN[0]):
            raise Boom("알림에 막힘")
        with hooks.stage(PLAN[1]):
            raise Boom("알림 없이 멈춤")
    except Boom as exc:
        text = hooks.failure_text(exc)
    out.append(check("★ 끝 실패 문구는 **실제로 멈춘** 단계의 것", text.startswith("[둘째]"),
                     text.splitlines()[0]))

    for label, recover in (("팝업이 없으면", lambda: None),
                           ("팝업 확인이 터지면", lambda: (_ for _ in ()).throw(OSError("x")))):
        hooks, _seen = recorder()
        hooks.recover = recover
        hooks.begin(PLAN)
        try:
            with hooks.stage(PLAN[0]):
                raise Boom("원래 실패")
            raised = False
        except Boom:
            raised = True
        out.append(check(f"★ {label} 원래대로 멈춘다 (원래 예외가 올라온다)", raised))

    hooks, _seen = recorder()
    called = []
    hooks.recover = lambda: called.append(1) or "알림"
    hooks.begin(PLAN)
    stopped = False
    try:
        with hooks.stage(PLAN[0]):
            raise cancel.Cancelled("시험")
    except cancel.Cancelled:
        stopped = True
    out.append(check("중단은 팝업을 보지 않고 그대로 올라온다", stopped and not called))
    return out


def _check_dismiss() -> list[bool]:
    """`dialogs.dismiss_message_box` — [확인] **하나뿐일 때만** 누른다. 가짜 팝업으로 본다."""
    from types import SimpleNamespace

    from utils import dialogs, ui

    log.info("▶ ERPia 메시지 팝업 닫기 — [확인] 하나뿐일 때만")

    def button(name, auto_id, box):
        def click():
            box["clicks"].append(name)
            if box.get("closes", True):
                box["open"] = False
        return SimpleNamespace(window_text=lambda: name, click=click,
                               element_info=SimpleNamespace(automation_id=auto_id, name=name))

    def world(names, closes=True):
        box = {"open": True, "clicks": [], "closes": closes}
        popup = SimpleNamespace(handle=77, window_text=lambda: "저장실패",
                                element_info=SimpleNamespace(automation_id="Popup_ERPiaMessageBox"))
        box["buttons"] = [button(n, a, box) for n, a in names]
        box["popup"] = popup
        return box

    saved = (ui.search, ui.click, dialogs.text_of)

    def fake_search(parent, **criteria):
        box = fake_search.box
        if criteria.get("auto_id") == dialogs.MESSAGE_BOX_AUTO_ID:
            return [box["popup"]] if box["open"] else []
        if criteria.get("control_type") == "Button":
            return box["buttons"]
        return []

    ui.search = fake_search
    ui.click = lambda ctrl, what, **k: ctrl.click() or "Invoke"
    dialogs.text_of = lambda popup: "연락처를 입력하세요"
    out = []
    try:
        fake_search.box = world([("확인(O)", "btn_확인(&O)"), ("닫기", "")])
        got = dialogs.dismiss_message_box(object(), timeout=0.3)
        out.append(check("★ [확인] 하나(+제목줄 닫기)면 누르고 글을 돌려준다",
                         got == "저장실패: 연락처를 입력하세요"
                         and fake_search.box["clicks"] == ["확인(O)"], str(got)))
        fake_search.box = world([("예(Y)", "btn_예(&Y)"), ("아니오(N)", "btn_아니오(&N)")])
        got = dialogs.dismiss_message_box(object(), timeout=0.3)
        out.append(check("★ [예]/[아니오] 처럼 고르는 팝업은 **누르지 않는다**",
                         got is None and not fake_search.box["clicks"]))
        fake_search.box = world([("확인(O)", "btn_확인(&O)")], closes=False)
        got = dialogs.dismiss_message_box(object(), timeout=0.3)
        out.append(check("눌러도 안 닫히면 None (못 닫은 것으로 본다)", got is None))
        fake_search.box = world([])
        fake_search.box["open"] = False
        out.append(check("팝업이 없으면 None", dialogs.dismiss_message_box(object()) is None))
    finally:
        ui.search, ui.click, dialogs.text_of = saved
    return out


def _check_report(hooks) -> list[bool]:
    """실패한 실행도 리포트에 남는가. **파일을 만들되 지운다.**"""
    from orchestrator import report

    out: list[bool] = []
    result = Result(summary="일부러 실패시킨 시험", steps=hooks.report())
    path = None
    try:
        path = report.write(result, directory=Path("logs"))
        out.append(check("리포트 파일이 만들어진다",
                         path is not None and Path(path).exists(), str(path)))
        text = Path(path).read_text(encoding="utf-8") if path else ""
        out.append(check("★ 실패한 단계가 리포트에 남는다",
                         "둘째" in text and "실패" in text))
        out.append(check("성공한 단계도 함께 남는다", "첫째" in text))
    finally:
        # 조사하고 나온 것은 남기지 않는다.
        if path and Path(path).exists():
            Path(path).unlink()
    return out


class _FakeButton:
    """매출처리 버튼 자리. `states` 를 차례로 돌려준다 — 예외면 그것을 올린다(요소가 사라짐)."""

    def __init__(self, states):
        self.states = list(states)
        self.last = self.states[-1]

    def is_enabled(self):
        value = self.states.pop(0) if self.states else self.last
        if isinstance(value, Exception):
            raise value
        return value


class _FakeScreen:
    def __init__(self, pid=4242):
        self.pid = pid

    def top_level_parent(self):
        return SimpleNamespace(handle=1)

    def process_id(self):
        return self.pid


def _check_collect_wait() -> list[bool]:
    """자동수집 대기 (10-07) — 대기 중 알림 닫기·버튼을 못 읽으면 끝난 것으로 보지 않기. 화면은 가짜다."""
    from automation import order_mapping as om
    from config.settings import SETTINGS
    from utils import winprobe

    log.info("▶ 자동수집 대기 — 알림 감시·버튼 읽기 실패 (가짜 화면)")
    keep = (om._sales_button, om.dialogs.dismiss_message_box, winprobe.top_windows, om.POPUP_CHECK_INTERVAL,
            SETTINGS.timeouts.collect_settle, SETTINGS.timeouts.poll_interval, om.collect_checks, om._collect_progress)
    out = []
    try:
        SETTINGS.timeouts.collect_settle, SETTINGS.timeouts.poll_interval = 0.02, 0.005
        om.collect_checks = lambda screen: (0, 0, 0)            # 진행 로그용 — 가짜 화면에는 수집로그가 없다
        om._collect_progress = lambda screen: "가짜"
        om.POPUP_CHECK_INTERVAL = 0
        dismissed: list = []
        om.dialogs.dismiss_message_box = lambda window: dismissed.append(window.handle) or "ERPia: 가짜 알림"
        popup = SimpleNamespace(handle=2)
        windows = iter([[popup]] * 3 + [[]] * 1000)
        winprobe.top_windows = lambda pid=None, visible_only=True: next(windows)
        buttons = [_FakeButton([False] * 30 + [True])]
        om._sales_button = lambda screen: buttons[-1]
        om.wait_collect_done(_FakeScreen(), timeout=10)
        out.append(check("★ 대기 중 메인 창 말고 뜬 창이 있으면 알림 닫기를 부른다 — 같은 창 묶음은 한 번만",
                         dismissed == [1], str(dismissed)))

        refound: list = []
        lost = RuntimeError("가짜 — 요소가 사라짐")
        first = _FakeButton([False, lost, lost, lost])     # 회색이다가 읽을 수 없게 된다

        def find(screen):
            refound.append(1)
            return first if len(refound) == 1 else _FakeButton([False] * 5 + [True])
        om._sales_button = find
        winprobe.top_windows = lambda pid=None, visible_only=True: []
        om.wait_collect_done(_FakeScreen(), timeout=10)
        out.append(check("★ 버튼을 못 읽으면 끝난 것으로 보지 않고 다시 찾는다", len(refound) >= 2, str(len(refound))))

        refound.clear()
        om._sales_button = lambda screen: refound.append(1) or (
            _FakeButton([lost]) if len(refound) == 1 else _FakeButton([False] * 5 + [True]))
        om.wait_collect_done(_FakeScreen(), timeout=10)
        out.append(check("처음부터 못 읽으면 '진행 중인 수집 없음' 으로 넘기지 않는다", len(refound) >= 2, str(len(refound))))

        om._sales_button = lambda screen: _FakeButton([False])
        try:
            om.wait_collect_done(_FakeScreen(), timeout=0.3)
            timed_out = False
        except om.ScreenError:
            timed_out = True
        out.append(check("끝나지 않으면 상한에서 멈춘다 (무한 대기 없음)", timed_out))

        om._sales_button = lambda screen: _FakeButton([lost])        # ERPia 가 꺼졌다 — 다시 찾아도 못 읽는다
        started = time.monotonic()
        try:
            om.wait_collect_done(_FakeScreen(), timeout=30)
            error = ""
        except om.ScreenError as exc:
            error = str(exc)
        spent = time.monotonic() - started
        out.append(check("★ 버튼을 계속 못 읽으면(ERPia 꺼짐) 상한을 기다리지 않고 곧 멈춘다 (10-07 검토)",
                         "연달아 읽지 못했다" in error and spent < 5, f"{spent:.1f}초 / {error[:40]}"))
    finally:
        (om._sales_button, om.dialogs.dismiss_message_box, winprobe.top_windows, om.POPUP_CHECK_INTERVAL,
         SETTINGS.timeouts.collect_settle, SETTINGS.timeouts.poll_interval, om.collect_checks,
         om._collect_progress) = keep
    return out


# 가짜 ERPia — WinForms 는 컨트롤 Name 을 UIA AutomationId 로 낸다. 그래서 ERPia 메시지 팝업과 같은 auto_id 를 낼 수 있다.
# 이 프로세스의 창만 건드린다 (실제 ERPia 는 보지 않는다). 글은 ASCII — 명령줄 인코딩을 타지 않게.
_FAKE_APP = r"""
Add-Type -AssemblyName System.Windows.Forms
$main = New-Object System.Windows.Forms.Form
$main.Name = 'FakeErpiaMain'; $main.Text = 'comp01 - user01 FAKE'; $main.Width = 420; $main.Height = 260
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
  $timer.Stop()
  foreach ($kind in @('ok', 'yesno')) {
    $p = New-Object System.Windows.Forms.Form
    $p.Name = 'Popup_ERPiaMessageBox'; $p.Text = 'ERPia'; $p.Width = 300; $p.Height = 160
    $label = New-Object System.Windows.Forms.Label
    $label.Name = 'lbl_Message'; $label.Text = "FAKE notice $kind"; $label.AutoSize = $true
    $p.Controls.Add($label)
    $names = if ($kind -eq 'ok') { @('OK') } else { @('Yes', 'No') }
    $left = 10
    foreach ($text in $names) {
      $b = New-Object System.Windows.Forms.Button
      $b.Name = "btn_$text"; $b.Text = $text; $b.Top = 50; $b.Left = $left; $left += 90
      $b.Add_Click({ $this.FindForm().Close() })
      $p.Controls.Add($b)
    }
    [void]$p.ShowDialog($main)
  }
})
$main.Add_Shown({ $timer.Start() })
[System.Windows.Forms.Application]::Run($main)
"""


def _check_real_popup() -> list[bool]:
    """진짜 창으로 알림 닫기 (10-07) — 가짜 ERPia(WinForms)의 [OK] 하나 팝업은 닫고, [Yes]/[No] 는 누르지 않는다."""
    import base64
    import subprocess

    from pywinauto import Application

    from automation import order_mapping as om
    from utils import dialogs, process, winprobe
    from utils.wait import wait_for

    log.info("▶ 진짜 창 — 가짜 ERPia 팝업 닫기 (WinForms, 이 시험의 프로세스만)")
    if process.screen_locked():
        log.info("  건너뜀  화면이 잠겨 있어 진짜 창 시험을 못 한다")
        return []
    encoded = base64.b64encode(_FAKE_APP.encode("utf-16-le")).decode("ascii")
    fake = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    out = []
    try:
        handle = wait_for(lambda: next((w.handle for w in winprobe.top_windows(fake.pid)
                                        if w.title.endswith("FAKE")), None), "가짜 ERPia 창", timeout=30)
        main = Application(backend="uia").connect(process=fake.pid).window(handle=handle).wrapper_object()
        wait_for(lambda: dialogs._message_box(main), "가짜 [OK] 팝업", timeout=10)
        watch = {"next": 0.0, "seen": frozenset()}
        om._close_popups(main, watch)
        reopened = wait_for(lambda: dialogs._message_box(main), "가짜 [Yes]/[No] 팝업", timeout=10)
        out.append(check("★ [OK] 하나뿐인 팝업은 수집 대기 중에 닫는다 (진짜 UIA)",
                         "Yes" in dialogs.buttons_of(reopened), str(dialogs.buttons_of(reopened))))
        watch["next"] = 0.0
        om._close_popups(main, watch)
        out.append(check("★ [Yes]/[No] 처럼 고르는 팝업은 누르지 않는다", dialogs._message_box(main) is not None))
        watch["next"] = 0.0
        calls: list = []
        keep = dialogs.dismiss_message_box
        dialogs.dismiss_message_box = lambda window: calls.append(1)
        try:
            om._close_popups(main, watch)
        finally:
            dialogs.dismiss_message_box = keep
        out.append(check("같은 창 묶음이면 다시 찾지 않는다 (UIA 비용)", not calls))
    finally:
        fake.kill()
        fake.wait(timeout=10)
    return out


# 가짜 ERPia 로그인 — 로그인 단추를 누르면 비밀번호 변경 요구 창(같은 auto_id)이 로그인 창의 자식으로 뜬다.
# [Later] 만 로그인을 끝낸다. [Change] 는 아무것도 하지 않는다 (누르면 로그인이 끝나지 않아 시험이 실패한다).
# 로그인 단추는 타이머로 창을 띄운다 — 단추 안에서 ShowDialog 하면 Invoke 가 창이 닫힐 때까지 돌아오지 않는다.
_FAKE_LOGIN = r"""
Add-Type -AssemblyName System.Windows.Forms
$later = __LATER__
$login = New-Object System.Windows.Forms.Form
$login.Name = 'LoginForm'; $login.Text = 'login FAKE'; $login.Width = 360; $login.Height = 260
$top = 10
foreach ($name in @('txt_Admin_Code', 'txt_ID', 'txt_Password')) {
  $t = New-Object System.Windows.Forms.TextBox
  $t.Name = $name; $t.Top = $top; $t.Left = 10; $t.Width = 200; $top += 30
  $login.Controls.Add($t)
}
$main = New-Object System.Windows.Forms.Form
$main.Name = 'Main'; $main.Text = 'comp01 - user01 FAKE'
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
  $timer.Stop()
  $c = New-Object System.Windows.Forms.Form
  $c.Name = 'Frm_ChangePassword'; $c.Text = 'change FAKE'; $c.Width = 300; $c.Height = 160
  $b = New-Object System.Windows.Forms.Button
  $b.Name = 'btn_Change'; $b.Text = 'Change'; $b.Top = 50; $b.Left = 10
  $c.Controls.Add($b)
  if ($later) {
    $n = New-Object System.Windows.Forms.Button
    $n.Name = 'btn_NextTime'; $n.Text = 'Later'; $n.Top = 50; $n.Left = 100
    $n.Add_Click({ $this.FindForm().Close(); $login.Close(); $main.Show() })
    $c.Controls.Add($n)
  }
  [void]$c.ShowDialog($login)
})
$go = New-Object System.Windows.Forms.Button
$go.Name = 'lbl_LoginBtn'; $go.Text = 'Login'; $go.Top = $top; $go.Left = 10
$go.Add_Click({ $timer.Start() })
$login.Controls.Add($go)
$login.Show()
[System.Windows.Forms.Application]::Run()
"""


def _check_password_change() -> list[bool]:
    """비밀번호 변경 요구 창 (10-07 사용자 확정 — 늘 [다음에 변경하기]). 가짜 ERPia 로그인(WinForms)으로."""
    import base64
    import subprocess

    from automation import login as lg
    from orchestrator import friendly
    from utils import process, winprobe
    from utils.wait import wait_for

    log.info("▶ 진짜 창 — 로그인 중 비밀번호 변경 요구 창 (WinForms, 이 시험의 프로세스만)")
    if process.screen_locked():
        log.info("  건너뜀  화면이 잠겨 있어 진짜 창 시험을 못 한다")
        return []

    def run(later: bool):
        script = _FAKE_LOGIN.replace("__LATER__", "$true" if later else "$false")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        fake = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            # 가짜 로그인 창이 뜬 뒤에 부른다 — 못 찾으면 login 이 전역(진짜 ERPia)으로 넘어간다
            wait_for(lambda: any(w.title == "login FAKE" for w in winprobe.top_windows(fake.pid)),
                     "가짜 로그인 창", timeout=30)
            try:
                return lg.login("comp01", "user01", "pw-fake", pid=fake.pid, timeout=20)
            except Exception as exc:                # noqa: BLE001 - 무엇이 났는지 본다
                return exc
        finally:
            fake.kill()
            fake.wait(timeout=10)

    got = run(later=True)
    out = [check("★ [다음에 변경하기] 를 누르고 로그인을 마친다", isinstance(got, str) and "comp01 - user01" in got,
                 repr(got)[:120])]
    got = run(later=False)
    out.append(check("★ [다음에 변경하기] 가 없으면(강제 변경) 누르지 않고 멈춘다",
                     isinstance(got, lg.LoginRejected) and "비밀번호 변경을 요구" in str(got), repr(got)[:120]))
    text = friendly.explain(got) if isinstance(got, BaseException) else ""
    out.append(check("강제 변경은 사람 말로 — 직접 바꾸고 [설정] 도 고치라고", "[ERPia 비밀번호]" in text, text[:80]))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
