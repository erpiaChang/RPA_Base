r"""[조사 도구 - 읽기 전용] 진행 단계 통지와 중단 토큰이 실제로 도는지 확인한다.

**대상 프로그램을 건드리지 않는다.** GUI 도 띄우지 않는다.

    .venv\Scripts\python.exe -m tools.probe_progress

## 왜 이 도구가 있나 (2026-09-15)

대시보드와 중단/재개는 **화면을 그리는 일이 아니라 통지와 취소가 도는 일**이다.
그 둘은 눈으로 보이지 않아서, 안 돌아도 "조용히 아무 일도 없는" 형태로 실패한다.
`wait_rect_settled` 가 `tuple(RECT)` 때문에 무동작이었던 것과 같은 종류다
(`tools/probe_rect_settle.py` 참고).

특히 중단은 **상한이 곧 반응 시간**이 되는 구조다. 자동수집 상한이 4시간이라,
취소 확인이 대기 안에 들어가 있지 않으면 [중단] 이 영원히 안 듣는 것처럼 보인다.
여기서 재는 것은 "상한을 다 쓰지 않고 빠져나오는가" 다.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from orchestrator import erpia_flow, steps, steps_collect, steps_erpia  # noqa: E402
from orchestrator.common import Hooks  # noqa: E402
from utils import cancel  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402
from utils.wait import WaitTimeout, wait_for  # noqa: E402

log = get_logger(__name__)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def check_plan() -> list[bool]:
    log.info("▶ 단계 표")
    out = []
    full = steps_erpia.plan(["excel", "site"])
    out.append(check("고른 수집방식만 계획에 들어간다",
                     [s.id for s in steps_erpia.plan(["site"])]
                     == ["launch", "login", "screen", "site", "sales",
                         "logistics_wait", "logistics"],
                     "자동수집만 고르면 7단계"))
    out.append(check("엑셀수집을 고르면 8단계", len(full) == 8))

    # ★ 6-2 표의 핵심. 매출처리는 되돌릴 수 없다.
    sales = steps.by_id(full, "sales")
    out.append(check("매출처리는 되돌릴 수 없다로 표시된다",
                     sales is not None and sales.reentry == steps.IRREVERSIBLE,
                     sales.reentry_label if sales else "없다"))
    safe = [s.id for s in full if not s.needs_confirm]
    out.append(check("확인 없이 시작점으로 고를 수 있는 것은 실행/로그인/화면뿐",
                     safe == ["launch", "login", "screen"], str(safe)))
    out.append(check("수집 흐름도 같은 틀을 쓴다",
                     all(isinstance(s, steps.Step) for s in steps_collect.plan())))
    out.append(check("모든 단계에 재실행 설명이 있다",
                     all(s.replay for s in (*steps_erpia.ALL, *steps_collect.ALL))))
    return out


def check_notify() -> list[bool]:
    log.info("▶ 단계 통지")
    out = []
    events, lines = [], []
    hooks = Hooks(status=lines.append, on_step=events.append)
    plan = steps_erpia.plan(["excel", "site"])

    hooks.begin(plan)
    out.append(check("시작 전에 전체 목록을 먼저 알린다",
                     len(events) == len(plan)
                     and all(e.state == steps.PENDING for e in events),
                     f"{len(events)}건"))
    out.append(check("대기 상태는 상태줄에 찍지 않는다", lines == [],
                     "시작 전에 마지막 단계 이름이 남으면 안 된다"))

    with hooks.stage(steps_erpia.EXCEL, "3건") as run:
        hooks.note(steps_erpia.EXCEL, "2/3 진행")
        run.result = {"ok": 3}
        run.detail = "업로드 3/3건"
    out.append(check("진행 중 -> 세부 갱신 -> 완료 순으로 알린다",
                     [e.state for e in events[-3:]]
                     == [steps.RUNNING, steps.RUNNING, steps.DONE]))
    out.append(check("진행 중에는 세부 진행을 보여 준다",
                     events[-2].detail == "2/3 진행", events[-2].one_line()))
    out.append(check("완료에는 중간 진행이 남지 않는다",
                     events[-1].detail == "업로드 3/3건", events[-1].one_line()))
    out.append(check("상태줄 한 줄에 몇 번째/전체가 들어간다",
                     lines[0].startswith("4/8 엑셀수집"), lines[0]))
    out.append(check("결과를 기록에 담는다",
                     next(r for r in hooks.report() if r["id"] == "excel")["result"]
                     == {"ok": 3}))

    hooks.skip(steps_erpia.SALES, "수집된 주문 0건")
    out.append(check("건너뜀은 실패가 아니다",
                     events[-1].state == steps.SKIPPED
                     and steps_erpia.SALES in
                     [s for s in hooks.plan if s.id == "sales"]))

    try:
        with hooks.stage(steps_erpia.LOGI_WAIT):
            raise RuntimeError("조회 버튼이 없다")
    except RuntimeError as exc:
        # 일부러 낸 실패다. 삼키는 것이 아니라 **상태가 남는지**를 아래에서 본다.
        log.debug("일부러 낸 실패: %s", exc)
    failed = next(r for r in hooks.report() if r["id"] == "logistics_wait")
    # 09-21: `error` 는 사람 말(단계 이름 + 할 일), 원문은 `error_detail` (관리자용).
    out.append(check("실패는 단계와 원인을 함께 남긴다 (사람 말 + 원문 따로)",
                     failed["state"] == steps.FAILED
                     and failed["error"].startswith("[물류대기] 단계에서")
                     and "RuntimeError: 조회 버튼이 없다" == failed["error_detail"]
                     and "RuntimeError" not in failed["error"], failed["error"]))
    out.append(check("안 끝난 단계를 알 수 있다 (재개 지점 후보)",
                     [s.id for s in hooks.unfinished()]
                     == ["launch", "login", "screen", "site",
                         "logistics_wait", "logistics"]))
    return out


def check_cancel() -> list[bool]:
    log.info("▶ 중단 토큰")
    out = []

    # 1) 토큰이 없으면 예전과 똑같이 동작해야 한다 (콘솔 도구 경로).
    start = time.monotonic()
    try:
        wait_for(lambda: False, "없는 것", timeout=0.6)
        out.append(check("토큰이 없으면 상한까지 기다린다", False))
    except WaitTimeout:
        spent = time.monotonic() - start
        out.append(check("토큰이 없으면 예전처럼 상한까지 기다린다",
                         0.5 <= spent < 1.5, f"{spent:.2f}초"))

    # 2) ★ 상한을 다 쓰지 않고 빠져나오는가. 이것이 [중단] 의 체감이다.
    token = cancel.CancelToken()
    with cancel.use(token):
        threading.Timer(0.3, token.cancel, ["[중단] 눌림"]).start()
        start = time.monotonic()
        try:
            wait_for(lambda: False, "자동수집 완료", timeout=120.0)
            out.append(check("상한 120초를 다 쓰지 않고 멈춘다", False))
        except cancel.Cancelled:
            spent = time.monotonic() - start
            out.append(check("상한 120초를 다 쓰지 않고 멈춘다", spent < 2.0,
                             f"{spent:.2f}초에 멈췄다"))

        # 3) 중간의 `except Exception` 이 취소를 삼키지 않는가.
        def swallowing():
            try:
                wait_for(lambda: False, "안쪽 대기", timeout=5)
            except Exception:
                return "삼켰다"
            return None

        try:
            wait_for(swallowing, "바깥 대기", timeout=5)
            out.append(check("중간의 except Exception 을 통과한다", False,
                             "취소가 삼켜졌다"))
        except cancel.Cancelled:
            out.append(check("중간의 except Exception 을 통과한다", True,
                             "Cancelled 가 BaseException 이라서"))

    # 4) 중단된 뒤에는 되돌릴 수 없는 단계를 **시작하지 않는다**.
    hooks = Hooks(token=token)
    hooks.begin(steps_erpia.plan(["site"]))
    with hooks.running():
        try:
            with hooks.stage(steps_erpia.SALES):
                out.append(check("중단 뒤에 매출처리를 시작하지 않는다", False,
                                 "블록에 들어갔다"))
        except cancel.Cancelled:
            out.append(check("중단 뒤에 매출처리를 시작하지 않는다", True))

    # 5) 토큰 밖에서는 아무 일도 없다 (같은 토큰이어도 스레드에 안 걸려 있으면).
    try:
        cancel.check("밖")
        out.append(check("토큰을 걸지 않은 경로는 영향이 없다", True))
    except cancel.Cancelled:
        out.append(check("토큰을 걸지 않은 경로는 영향이 없다", False))
    return out


def check_segment() -> list[bool]:
    """구간 실행 — 시작점 앞은 하지 않고, 위험한 구간은 확인을 받는다."""
    log.info("▶ 구간 실행")
    out = []
    plan = steps_erpia.plan(["excel", "site"])

    hooks = Hooks(start_step="logistics_wait")
    hooks.begin(plan)
    out.append(check("시작점보다 앞은 하지 않을 것으로 표시된다",
                     [r["state"] for r in hooks.report()][:6]
                     == [steps.SKIPPED] * 6,
                     "앞의 6단계"))
    out.append(check("시작점부터는 할 차례다",
                     [hooks.want(s) for s in plan]
                     == [False] * 6 + [True, True]))
    first = hooks.first_wanted()
    out.append(check("이번에 처음 할 단계를 찾는다",
                     first is not None and first.id == "logistics_wait",
                     first.name if first else "없다"))

    # ★ 되돌릴 수 없는 구간을 **아무도 막지 않았다고 해서** 해도 되는 것이 아니다.
    hooks_no_ask = Hooks(start_step="sales")
    hooks_no_ask.begin(plan)
    try:
        hooks_no_ask.approve_start(steps_erpia.SALES, "미매출 주문수 : 4건")
        out.append(check("물어볼 상대가 없으면 시작하지 않는다", False, "그냥 통과했다"))
    except cancel.Cancelled as exc:
        out.append(check("물어볼 상대가 없으면 시작하지 않는다", True, str(exc)[:40]))

    asked = []

    def say_no(question: str) -> bool:
        asked.append(question)
        return False

    hooks_no = Hooks(start_step="sales", confirm=say_no)
    hooks_no.begin(plan)
    try:
        hooks_no.approve_start(steps_erpia.SALES, "미매출 주문수 : 4건")
        out.append(check("승인하지 않으면 시작하지 않는다", False))
    except cancel.Cancelled:
        out.append(check("승인하지 않으면 시작하지 않는다", True))
    out.append(check("확인 문구에 되돌릴 수 없다는 사실과 화면 상태가 함께 들어간다",
                     bool(asked) and "되돌릴 수 없" in asked[0]
                     and "미매출 주문수 : 4건" in asked[0]))

    hooks_yes = Hooks(start_step="sales", confirm=lambda q: True)
    hooks_yes.begin(plan)
    hooks_yes.approve_start(steps_erpia.SALES, "")
    out.append(check("승인하면 그대로 진행한다", True))

    # 안전한 단계는 묻지 않는다.
    hooks_safe = Hooks(start_step="login")
    hooks_safe.begin(plan)
    hooks_safe.approve_start(steps_erpia.LOGIN, "")
    out.append(check("안전한 단계는 확인 창구가 없어도 묻지 않는다", True))

    out.append(check("계획에 없는 시작 단계는 막는다",
                     _raises(lambda: _begin_with(plan, "없는단계"), ValueError)))

    # ★ 통합 흐름은 구간 실행을 지원하지 않는다. 화면이 단추를 안 만드는 것만으로는
    #   부족하다 — 흐름 쪽에서도 막아야 표와 실제가 어긋나지 않는다.
    #   이 검사는 **프로그램을 띄우기 전에** 예외가 나는지를 본다.
    from orchestrator import full_flow

    options = erpia_flow.Options(exe="", company="c", user_id="u", password="p",
                                 sources=["site"], start_step="sales")
    out.append(check("통합 흐름은 구간 실행을 막는다 (프로그램을 띄우기 전에)",
                     _raises(lambda: full_flow.run(options, hooks=Hooks()),
                             ValueError)))
    return out


def _begin_with(plan, start_step: str) -> None:
    hooks = Hooks(start_step=start_step)
    hooks.begin(plan)


def _raises(call, kind) -> bool:
    try:
        call()
    except kind:
        return True
    return False


def check_tables() -> list[bool]:
    """화면에 보여 줄 **상세 표**가 결과에서 제대로 만들어지는지 본다."""
    log.info("▶ 상세 표")
    out = []

    # 경로는 **파일명만** 쓴다. 표는 `Path(path).name` 만 보여 주므로 충분하고,
    # 가짜 절대경로를 적으면 "경로 하드코딩" 규칙에 걸린다.
    uploads = [
        {"site": "사이트C", "path": "사이트C_1.xlsx", "ok": True,
         "error": None, "attempts": 1},
        {"site": "사이트B", "path": "사이트B_2.xlsx", "ok": False,
         "error": "파일 선택 창을 찾지 못했다\n둘째 줄", "attempts": 2},
    ]
    rows = erpia_flow.upload_table(uploads)
    # 09-21: 파일명은 **누를 수 있는 칸**(report.Link)이다. 글자는 파일명 그대로.
    out.append(check("엑셀수집 — 파일마다 한 줄, 파일명만 보여 준다",
                     len(rows) == 2 and str(rows[0][1]) == "사이트C_1.xlsx",
                     str(rows[0])))
    out.append(check("실패한 줄에 결과와 **사람 말 사유 한 줄**이 들어간다",
                     rows[1][2] == "못 올림" and "\n" not in rows[1][4]
                     and "파일 고르는 창" in rows[1][4], rows[1][4]))
    # ★ dry-run 은 누르지 않고 ok 가 된다. **성공이라 쓰지 않는다** (09-21, HANDOFF 2-2).
    dry = [{**uploads[0], "dry_run": True}]
    out.append(check("★ dry-run 업로드는 표에 '올림' 이 아니라 dry-run 으로 적힌다",
                     erpia_flow.upload_table(dry)[0][2] == "안 올림 (시험 실행)",
                     erpia_flow.upload_table(dry)[0][2]))
    out.append(check("★ dry-run 업로드는 요약에 '올리지 않음' 이 붙는다",
                     "올리지 않음" in erpia_flow.upload_summary(dry)
                     and "올리지 않음" not in erpia_flow.upload_summary(uploads),
                     erpia_flow.upload_summary(dry)))

    hold = {"items": [
        {"code": "8800000000047", "name": "시험 상품 A", "shortage": "790,422",
         "state": "보류함", "checked": 12, "already": 0},
        {"code": "8800000000001", "shortage": "5,386", "state": "이미 보류",
         "checked": 0, "already": 208},
        {"code": "8800000000006", "shortage": "3", "state": "제외",
         "checked": 0, "already": 0},
    ]}
    rows = erpia_flow.hold_table(hold)
    # 09-22 사용자 확정: **이번에 보류를 건 상품만** 적는다 (이미 보류·제외는 빼고).
    out.append(check("물류대기 — 보류 건 상품만 한 줄, 이름으로",
                     len(rows) == 1 and rows[0][0] == "시험 상품 A"
                     and rows[0][2] == "배송보류 설정함", str(rows)))
    out.append(check("이름을 못 읽었으면 코드 (보류 확인 실패도 적는다)",
                     erpia_flow.hold_table({"items": [
                         {"code": "8800000000001", "state": "보류 확인 실패"}]})[0][0]
                     == "8800000000001"))
    # 09-22 실제 실행 꼴: 부족 1종이 전부 이미 보류 → 부족 상품을 적지 않는다.
    already = {"items": [hold["items"][1]], "saved": 0, "saved_exact": True}
    out.append(check("★ 보류를 안 걸었으면 부족 상품을 적지 않는다",
                     erpia_flow.hold_table(already) == []
                     and erpia_flow.hold_detail(already) == "물류관리로 넘길 주문 없음",
                     erpia_flow.hold_detail(already)))
    unsure = {"items": [{"name": "시험 상품 B", "state": "보류 확인 실패", "checked": 3}],
              "saved": 0, "saved_exact": True, "save_message": "저장실패: x"}
    out.append(check("보류 확인 실패도 한 줄에 · 저장 뒤 알림이면 '넘길 주문 없음' 이라 쓰지 않는다",
                     erpia_flow.hold_detail(unsure) == "재고 부족으로 배송보류 상품 1종(시험 상품 B) "
                     "· 주문 3건 · 저장 뒤 ERPia 알림이 떠 저장되지 않았을 수 있음",
                     erpia_flow.hold_detail(unsure)))

    sales = {"before": "4건", "after": "2건", "clicked": "예", "dialogs": [],
             "skipped": False}
    rows = erpia_flow.sales_table(sales)
    out.append(check("매출처리 — ERPia 라벨을 옮기고 처리 건수를 뺀다",
                     ("처리 전 미매출 주문", "4건") in rows
                     and erpia_flow.sales_detail(sales).startswith("주문 2건 매출처리"),
                     str(rows)))

    # ★ 없는 값을 지어내지 않는다. 못 읽었으면 `확인 못 함` 이다.
    rows = erpia_flow.sales_table({"before": "", "after": "", "skipped": False})
    out.append(check("못 읽은 값은 '확인 못 함' (0 으로 만들지 않는다)",
                     ("처리 전 미매출 주문", "확인 못 함") in rows, str(rows)))

    # 09-22 사용자 확정: 물류관리는 **만든 전표 수** — 택배사·박스·버튼 줄은 없다.
    # 09-29: 전표는 박스(송장 = 배송전표)다. 주문 하나가 박스 여럿이 될 수 있다 (실측 주문 2건 → 송장 31).
    done = {"registered": 2, "courier": "택배사A", "box": "박스A", "mode": "자동",
            "finish": "운송장출력", "saved": True, "save_clicked": True,
            "finish_clicked": True, "slips": 2, "slips_exact": True, "slips_saved": 2,
            "boxes": 31, "boxes_exact": True}
    out.append(check("★ 물류관리 — 전표는 송장(박스) 수, 주문 수는 따로",
                     erpia_flow.logistics_table(done) == [("생성한 개별배송 전표(송장)", "31건"),
                                                          ("올라간 주문", "2건")]
                     and erpia_flow.logistics_detail(done) == "개별배송 전표(송장) 31건 생성 · 주문 2건",
                     erpia_flow.logistics_detail(done)))
    partial = {**done, "boxes": 300, "boxes_exact": False}
    out.append(check("박스를 끝까지 못 셌으면 'N건 이상'",
                     erpia_flow.logistics_detail(partial).startswith("개별배송 전표(송장) 300건 이상 생성"),
                     erpia_flow.logistics_detail(partial)))
    unknown = {**done, "boxes": None, "boxes_exact": False}
    out.append(check("박스를 못 셌으면 주문 수로 대신하지 않는다 ('건수 확인 못 함')",
                     erpia_flow.logistics_detail(unknown) == "개별배송 전표(송장) 생성 (건수 확인 못 함)",
                     erpia_flow.logistics_detail(unknown)))
    none = {"skipped": "등록 대상 없음(배송보류 등)", "slips": 0, "slips_exact": True}
    out.append(check("★ 올린 것이 없으면 '개별배송으로 올릴 주문 0건' 으로 끝",
                     erpia_flow.logistics_table(none) == [("개별배송으로 올릴 주문", "0건")]
                     and erpia_flow.logistics_detail(none) == "개별배송으로 올릴 주문 0건",
                     erpia_flow.logistics_detail(none)))
    failed = {**done, "saved": False, "finish_clicked": False, "slips_saved": None}
    out.append(check("저장 실패는 0건 생성 — 저장되지 않음 (완료로 읽히지 않게)",
                     erpia_flow.logistics_detail(failed) == "개별배송 전표(송장) 0건 생성 — 저장되지 않음",
                     erpia_flow.logistics_detail(failed)))

    out.append(check("빈 결과에도 터지지 않는다",
                     erpia_flow.hold_table({}) == []
                     and erpia_flow.upload_table([]) == []))
    return out


def main() -> int:
    setup_logging()
    results = [*check_plan(), *check_notify(), *check_cancel(), *check_segment(),
               *check_tables()]
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
