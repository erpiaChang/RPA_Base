r"""물류관리(물류처리) 화면 (Phase 9).

물류대기에서 저장된 전표를 **개별배송으로 등록하고 택배사를 지정해 저장한다.**

근거: 사용자 설명(2026-09-04) + `docs/CONTROLS.md` "물류관리(물류처리) 화면"

    .venv\Scripts\python.exe -m automation.logistics --dry-run
    .venv\Scripts\python.exe -m automation.logistics
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation import sidebar  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from utils import cancel, dialogs, ui, winprobe  # noqa: E402
from utils.logger import get_logger, step  # noqa: E402
from utils.wait import WaitTimeout, wait_for  # noqa: E402

log = get_logger(__name__)

# --- 확정된 식별자 (docs/CONTROLS.md) ---
SCREEN_PANE_AUTO_ID = "Frm_Management_Logistics"
SIDEBAR_LOGISTICS = sidebar.LOGISTICS      # `automation/sidebar.py`

TOP_GRID_AUTO_ID = "gridCtrl_BS_Top"
BOTTOM_GRID_AUTO_ID = "gridCtrl_BS_Bottom"

# 등록(I). **비활성 = 등록모드(입력 중)**, 활성 = 저장이 끝난 상태다
# (사용자 확인 2026-09-10: 정상 저장이면 팝업 없이 이 버튼이 살아난다).
INSERT_BUTTON_AUTO_ID = "btnInsert"
# [운송장출력] — 저장 뒤 마지막으로 누르는 버튼 (2026-09-10 실측으로 확정)
WAYBILL_BUTTON_AUTO_ID = "cmdPrint_Tag_Form"
# [엑셀파일생성] — `수동` 일 때 [운송장출력] 대신 누른다 (2026-09-17 실측)
EXCEL_FILE_BUTTON_AUTO_ID = "cmdPrint_E"

# --- 자동/수동 (2026-09-17 실측) ---
# 상단 툴바의 콤보. 박스 콤보처럼 **이 Pane 자체가 값**이고 클릭으로 열린다.
# 값에 따라 출력 버튼 줄의 **구성이 바뀐다.** 자동이면 [운송장출력], 수동이면
# [엑셀파일생성] 이 있고, 다른 값일 때는 보이지 않는 것까지 뒤져도 없다.
# ★ 버튼 **순서·구성은 업체마다 다르다** (comp01 의 자동은 [거래명세표] 가 먼저다).
#   그래서 자리가 아니라 **auto_id** 로 찾는다.
# 등록모드에서 하단 16행이 있을 때 바꿔도 등록모드·하단은 그대로였다 (comp01).
#
# ★ ERPia 가 이 값을 **업체마다 기억한다** (user01 는 `수동`, comp01 는 `자동`
#   으로 떠 있었다). 그래서 매번 명시적으로 맞춘다.
MODE_COMBO_AUTO_ID = "cboBS_Auto_YN"
MODE_AUTO = "자동"
MODE_MANUAL = "수동"
MODES = (MODE_AUTO, MODE_MANUAL)
# 모드별로 저장 뒤 마지막에 누르는 버튼. (auto_id, 이름)
FINISH_BUTTONS = {
    MODE_AUTO: (WAYBILL_BUTTON_AUTO_ID, "운송장출력"),
    MODE_MANUAL: (EXCEL_FILE_BUTTON_AUTO_ID, "엑셀파일생성"),
}

SAVE_BUTTON_AUTO_ID = "btnSave"          # 저장(S)
# 저장 실패는 ERPia 공통 메시지 팝업으로 알린다 (2026-09-10 실측: 제목 '입력누락', 본문
# '연락처를 입력하세요'). **[확인] 으로 닫고 넘어간다** (09-22) — `close_save_failure`.
# [개별 배송(B)] 뒤에 상단으로 올라오기를 기다리는 시간.
# ★ 올라오지 않는 것은 **오류가 아니다** (사용자 확정 2026-09-10).
#   그 전표들에 **배송보류가 걸려 있으면** 배송할 수 없어 등록되지 않는다.
#   예전에는 `long_task`(2분)를 다 기다린 뒤 예외를 냈다. 정상 상황에 2분을
#   버리고 흐름을 죽였다. 짧게 보고, 없으면 "등록할 것이 없다"로 넘어간다.
REGISTER_TIMEOUT = 20.0
ORDER_SEARCH_AUTO_ID = "Btn_Search_JuMun"  # 주문조회(J)

COURIER_COMBO_AUTO_ID = "cboTag"         # 배송정보설정 [업체]
COURIER_COMBO_INNER_AUTO_ID = "cbo"      # 값이 읽히는 실제 콤보
COURIER_APPLY_AUTO_ID = "cmdTag"         # [적용]

# 배송정보설정 [박스]. **택배사와 달리 안쪽 `cbo` 가 없다.**
# `cboTagAmt` 자체에서 값이 읽힌다 (2026-09-07 실측: '박스A').
BOX_COMBO_AUTO_ID = "cboTagAmt"
BOX_APPLY_AUTO_ID = "cmdTagAmt"          # 박스 [적용]

BOTTOM_FIRST_COLUMN = "매출구분"          # 전체선택 인디케이터 셀의 기준 컬럼
# 하단 매출번호 중복 제거 = 올라간 **주문** 수 (`count_slips`). 만든 전표(송장) 수는 상단 박스 줄로 센다
# (`count_boxes`, CONTROLS.md "상단 그리드는 1건이 2행으로"). 한 주문이 박스 여럿이 될 수 있다
SLIP_COLUMN = "매출번호"
SLIP_REFRESH_TIMEOUT = 15.0               # 저장 뒤 하단이 다시 조회되기를 기다리는 상한
TOP_COURIER_COLUMN = "배송업체"           # 적용 결과 확인용 (상단 그리드)
TOP_BOX_COLUMN = "박스 규격"              # 박스 적용 결과 확인용 (상단 그리드)

# 완전 일치로 찾는다. 공백이 들어 있다.
MENU_INDIVIDUAL_DELIVERY = "개별 배송(B)"


class ScreenError(RuntimeError):
    """물류관리 화면 처리 실패."""


# ------------------------------------------------------------------ 진입
def is_open(main_window) -> bool:
    # 화면 Pane 은 5층에 있다. 없을 때 12층까지 훑지 않는다(docs/RESOURCES.md 4번).
    return ui.exists(main_window, auto_id=SCREEN_PANE_AUTO_ID, control_type="Pane",
                     max_depth=ui.SCREEN_PANE_DEPTH)


def open_screen(target):
    """좌측 프로세스바 [물류처리] → 물류관리 화면. 화면 Pane을 돌려준다.

    ★ **dry-run 에서도 실제로 누른다.** 화면을 여는 것은 데이터를 바꾸지 않는다
      (2026-09-10 — 예전에는 이 클릭까지 건너뛰어 그 뒤가 전부 확인 불가였다).
    """
    with step(log, "물류관리 화면 진입"):
        main_window = target.main_window()

        # ★ Pane 이 있다 ≠ 화면이 **앞에 보인다.** 다른 화면 탭이 활성이면
        #   Pane 은 트리에 있지만 화면 밖 좌표를 갖는다. 그 상태에서 좌표를
        #   클릭하면 아무 일도 일어나지 않는다 (2026-09-10 실측:
        #   `하단 그리드 전체선택: 그리드가 화면 밖이라 누르지 않았다`).
        #
        #   메인 창의 `auto_id='Tab'` 은 **주문매핑 화면 내부 탭**이라 화면 탭이 아니다.
        #   여기서는 **프로세스바를 다시 누른다** (09-10 실기). 화면 탭을 제목으로 바로
        #   찾는 길도 있다 — 물류대기가 쓴다 (09-28 실기, `logistics_wait.open_screen`)
        if is_open(main_window) and ui.screen_visible(main_window, SCREEN_PANE_AUTO_ID, "물류관리 화면"):
            log.info("이미 열려 있고 화면에 보인다. 프로세스바를 다시 누르지 않는다.")
        else:
            entry = ui.find(main_window, auto_id=SIDEBAR_LOGISTICS,
                            what="프로세스바 '물류처리'")
            ui.click(entry, "좌측 프로세스바 '물류처리'")

        try:
            pane = wait_for(
                lambda: ui.find(main_window, max_depth=ui.SCREEN_PANE_DEPTH,
                                auto_id=SCREEN_PANE_AUTO_ID,
                                control_type="Pane", timeout=2, what="물류관리 화면"),
                f"화면 Pane({SCREEN_PANE_AUTO_ID})",
                timeout=SETTINGS.timeouts.window,
            )
        except WaitTimeout as exc:
            raise ScreenError(
                f"물류처리를 눌렀으나 {SCREEN_PANE_AUTO_ID} 를 찾지 못했다."
            ) from exc

        log.info("진입 확인: %s", ui.describe(pane))
        return pane


# ------------------------------------------------------------------ 그리드
def _grid(screen, auto_id: str, what: str):
    return ui.find(screen, auto_id=auto_id, control_type="Table", what=what)


def top_grid(screen):
    return _grid(screen, TOP_GRID_AUTO_ID, "상단 배송등록 그리드")


def bottom_grid(screen):
    return _grid(screen, BOTTOM_GRID_AUTO_ID, "하단 주문 그리드")


def _button(screen, auto_id: str, what: str):
    return ui.find(screen, auto_id=auto_id, control_type="Button", what=what)


def in_insert_mode(screen) -> bool:
    """등록모드인지. `등록(I)` 이 **비활성**이면 등록모드다 (2026-09-04 확인)."""
    return not ui.is_enabled(_button(screen, INSERT_BUTTON_AUTO_ID, "등록 버튼"))


# --- 등록모드 준비: "행이 없다" 를 얼마나 빨리 확정할 것인가 ---
# 왕복이 이보다 오래 걸리면 ERPia 가 아직 뭔가 하고 있다고 본다.
# 실측(2026-09-15): 유휴 0.1ms / 0행 조회 직후 6.3ms. 50ms 는 그 사이가 아니라
# **한참 위**라, 평범한 지연을 바쁘다고 잘못 읽지 않는다.
BUSY_ROUNDTRIP = 0.05
# 연속 이만큼 한가해야 인정한다. 한 번 튀는 것으로 판정하지 않는다.
IDLE_ROUNDS = 3
# [등록(I)] 뒤 유예. 아무것도 누르지 않았으면 여기서 바로 끝난다.
# 누른 경우에는 ERPia 가 바쁜 동안 위 표대로 계속 기다린다.
INSERT_SETTLE = 1.0


def prepare(screen) -> int:
    """등록모드로 만들고 하단 주문 목록을 채운다. 하단 행 수를 돌려준다.

    물류대기에서 넘어오면 화면이 알아서 조회 + 등록모드까지 해 준다.
    하지만 탭이 이미 열려 있으면 프로세스바를 눌러도 탭만 활성화되고 끝난다
    (2026-09-04 확인). 그래서 상태를 보고 부족한 것만 채운다.
    """
    with step(log, "등록모드 준비"):
        grid = bottom_grid(screen)
        handle = _process_handle(screen)

        if in_insert_mode(screen):
            log.info("이미 등록모드다. [등록(I)] 을 누르지 않는다.")
        else:
            # **dry-run 에서도 누른다.** 등록모드는 화면 모드일 뿐이고 저장이 아니다.
            # 누르지 않으면 하단 목록이 채워지지 않아 이후를 확인할 수 없다.
            ui.click(_button(screen, INSERT_BUTTON_AUTO_ID, "등록 버튼"), "등록(I)")

        rows = _wait_rows_settled(grid, "하단 주문 그리드", handle,
                                  settle=INSERT_SETTLE,
                                  timeout=SETTINGS.timeouts.control)
        if rows == 0:
            # 자동 조회가 일어나지 않은 경우다. 직접 조회한다.
            log.info("하단이 비어 있다. [주문조회(J)] 로 목록을 불러온다.")
            ui.click(_button(screen, ORDER_SEARCH_AUTO_ID, "주문조회 버튼"), "주문조회(J)")
            rows = _wait_rows_settled(grid, "하단 주문 그리드", handle,
                                      settle=SETTINGS.timeouts.control,
                                      timeout=SETTINGS.timeouts.long_task)

        log.info("등록모드 준비 완료 — 하단 %d행 (화면 기준)", rows)
        return rows


def _process_handle(screen) -> int:
    """화면이 속한 프로세스의 메인 창 핸들. 못 얻으면 0.

    0 이면 `_wait_rows_settled` 가 **유예만으로** 판정한다. 막지 않는다.
    """
    try:
        pid = screen.element_info.process_id
    except Exception as exc:
        log.debug("화면의 PID 를 읽지 못했다: %s", type(exc).__name__)
        return 0
    return winprobe.main_window_handle(pid)


def _wait_rows_settled(grid, what: str, handle: int, *,
                       settle: float, timeout: float) -> int:
    """행을 기다리되 **없으면 빨리 끝낸다.** 조회 완료를 알리는 UIA 신호가 없어 Win32 왕복
    (`winprobe.ui_roundtrip`)으로 ERPia 가 한가한지 본다. 예전에는 0행이면 상한(130초)을 통째로 썼다.

    | 상태 | 판정 |
    | --- | --- |
    | 행이 나왔다 | 즉시 반환 |
    | 행이 없고 ERPia 가 한가 | `settle` 뒤 0행으로 확정 |
    | 행이 없는데 ERPia 가 바쁨 | 계속 기다린다 (상한 `timeout`) — 조회가 정말 오래 걸리는 경우의 안전장치 |
    """
    state = {"count": 0, "idle": 0}
    started = time.monotonic()

    def done() -> bool:
        state["count"] = ui.visible_row_count(grid)
        if state["count"] > 0:
            return True
        if handle and winprobe.ui_roundtrip(handle) >= BUSY_ROUNDTRIP:
            state["idle"] = 0       # 아직 뭔가 하고 있다. 유예를 다시 센다
        else:
            state["idle"] += 1
        return (state["idle"] >= IDLE_ROUNDS
                and time.monotonic() - started >= settle)

    try:
        wait_for(done, f"{what} 행 등장", timeout=timeout)
    except WaitTimeout:
        log.info("%s 에 행이 나타나지 않았다. 0행으로 진행한다 (%.0f초).",
                 what, timeout)
        return 0
    if state["count"] == 0:
        log.info("%s 에 행이 없고 ERPia 가 한가하다. 0행으로 확정한다 (%.1f초).",
                 what, time.monotonic() - started)
    return state["count"]


def _wait_rows(grid, what: str, timeout: float | None = None) -> int:
    """행이 **하나라도 나타나면** 바로 돌려준다. 0행이어도 정상일 수 있다.

    하단 그리드에 행이 나왔다는 것은 등록할 준비가 됐다는 뜻이다
    (사용자 확정 2026-09-07). 행 수가 안정될 때까지 더 기다리지 않는다.
    """
    timeout = SETTINGS.timeouts.long_task if timeout is None else timeout
    state = {"count": 0}

    def appeared() -> bool:
        state["count"] = ui.visible_row_count(grid)
        return state["count"] > 0

    try:
        wait_for(appeared, f"{what} 행 등장", timeout=timeout)
    except WaitTimeout:
        log.info("%s 에 행이 나타나지 않았다. 0행으로 진행한다.", what)
    return state["count"]


# ------------------------------------------------------------------ 전표 수
def count_slips(grid) -> tuple[int | None, bool]:
    """하단 그리드의 **전표 수**(매출번호 중복 제거). `(수, 정확한가)`.

    행은 상품이고 한 전표(= 박스·송장)가 데이터 하나다 (09-29 사용자 확인). **끝까지 내려**
    읽는다 (`ui.distinct_count`) — 예전에는 보이는 행만 세서 화면 밖 행이 있으면 건수를 몰랐다.
    끝까지 못 봤으면 `정확한가` 가 False — 화면은 "N건 이상" 으로 쓴다. 행은 있는데 번호를
    하나도 못 읽으면 수가 None 이다 (0 이라고 하지 않는다).
    """
    return ui.distinct_count(grid, SLIP_COLUMN)


def count_boxes(grid) -> tuple[int | None, bool]:
    """상단 그리드의 **박스(송장 = 배송전표) 수**. `(수, 정확한가)`.

    상단은 박스 한 줄(배송번호·배송업체·박스 규격) 아래에 상품 줄이 붙는다 — 상품 줄의
    `배송업체` 는 비어 있다 (09-29 원시 값). 그래서 `배송업체` 가 찬 줄만 끝까지 센다.
    개별배송은 주문 하나를 박스 여럿으로 나눌 수 있다 (09-29 실측: 하단 주문 2건 → 박스 31개).
    """
    values, exact = ui.scan_column(grid, TOP_COURIER_COLUMN)
    boxes = sum(1 for value in values.values() if value and value.strip())
    if not boxes and ui.visible_row_numbers(grid):
        log.warning("상단 %s 을(를) 하나도 읽지 못했다. 박스 수를 모른다.", TOP_COURIER_COLUMN)
        return None, False
    return boxes, exact


def slips_left_after_save(grid, rows_before: int) -> tuple[int | None, bool]:
    """저장 뒤 하단에 **남은** 전표 수. 하단이 다시 조회되지 않으면 `(None, False)`.

    정상 저장이면 하단이 다시 조회되어 저장된 전표가 빠진다 (CONTROLS "저장 전/후":
    하단 3행 → 0행, 09-04 관측). 바뀌기 전에 세면 저장 전 수를 다시 세게 된다.
    """
    try:
        wait_for(lambda: ui.visible_row_count(grid) != rows_before,
                 "저장 뒤 하단 재조회", timeout=SLIP_REFRESH_TIMEOUT)
    except WaitTimeout:
        log.warning("저장 뒤 %.0f초 동안 하단이 %d행 그대로다. 올라간 주문 수를 세지 않는다.",
                    SLIP_REFRESH_TIMEOUT, rows_before)
        return None, False
    return count_slips(grid)


# ------------------------------------------------------------------ 개별배송
def selected_row_count(grid) -> int | None:
    """지금 **선택된** 행 수. 읽지 못하면 None.

    ★ 2026-09-10 정정. 예전 주석은 "선택 상태는 UIA로 읽을 수 없다" 였는데
      **읽힌다.** 실측에서 인디케이터 클릭 전 `[]`, 클릭 후 `[1..15]` 로 나왔다.
      그 잘못된 전제 때문에 "전체선택이 먹지 않았을 수 있다" 는 엉뚱한 오류
      메시지를 내고 있었다.
    """
    count = 0
    for row in ui.grid_rows(grid):
        if ui.is_group_row(row):
            continue
        try:
            if row.is_selected():
                count += 1
        except Exception as exc:
            log.debug("선택 상태를 읽지 못했다: %s", type(exc).__name__)
            return None
    return count


def select_all_bottom(screen, dry_run: bool = False) -> int | None:
    """하단 그리드 전체선택. 선택된 행 수를 돌려준다(못 읽으면 None).

    체크박스 컬럼이 없다. `매출구분` 헤더 좌측의 인디케이터 셀을 클릭한다
    (사용자 확인 2026-09-04). 하단의 `배송등록` 은 행별 체크박스이고
    **전체선택 수단이 아니다** (2026-09-10 확인).
    """
    grid = bottom_grid(screen)
    ui.click_row_indicator(grid, BOTTOM_FIRST_COLUMN, "하단 그리드 전체선택",
                           dry_run=dry_run)
    if dry_run:
        return None
    selected = selected_row_count(grid)
    if selected is None:
        log.info("선택 상태를 읽지 못했다. 개별배송 결과로 판정한다.")
    elif selected == 0:
        log.warning("전체선택을 눌렀으나 **선택된 행이 0개**다. "
                    "인디케이터 셀 클릭이 먹지 않았다.")
    else:
        log.info("전체선택 확인 — %d행 선택됨.", selected)
    return selected


def individual_delivery(screen, pid: int | None, dry_run: bool = False) -> int:
    """전체선택 → 우클릭 → [개별 배송(B)]. 상단으로 올라온 행 수를 돌려준다."""
    label = "개별배송 등록" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        upper = top_grid(screen)
        lower = bottom_grid(screen)

        before_top = ui.visible_row_count(upper)
        before_bottom = ui.visible_row_count(lower)
        log.info("개별배송 전 — 상단 %d행 / 하단 %d행 (화면 기준)",
                 before_top, before_bottom)
        if not before_bottom:
            # 빈 목록에 전체선택을 누르면 '선택 0개 — 클릭이 먹지 않았다' 거짓 경고만
            # 남는다. 누르지 않는다 (사용자 확정 09-22). 0건은 정상 경로다 (아래와 같다)
            log.info("하단 주문 그리드에 행이 없다. 개별배송할 대상이 없다.")
            return 0

        selected = select_all_bottom(screen, dry_run=dry_run)

        rows = ui.grid_rows(lower)
        if not rows:
            # ★ **오류가 아니다.** 수집 0건은 이 프로젝트가 정상으로 정의한 경로다
            #   (매출처리도 0행이면 건너뛴다). 아래 `registered == 0` 정상 종료가
            #   그대로 받는다 (2026-09-10 감사).
            log.info("하단 주문 그리드에 행이 없다. 개별배송할 대상이 없다.")
            return 0

        ui.right_click(rows[0], "하단 첫 행", dry_run=dry_run)
        if dry_run:
            log.info("[dry-run] 우클릭 후 [%s] 를 누를 예정", MENU_INDIVIDUAL_DELIVERY)
            return 0

        try:
            ui.click_process_menu_item(pid, MENU_INDIVIDUAL_DELIVERY)
        except ui.ControlNotFound as exc:
            raise ScreenError(str(exc)) from exc

        # 상단으로 올라오는 것이 유일하게 읽히는 완료 지표다.
        try:
            wait_for(lambda: ui.visible_row_count(upper) > before_top,
                     "상단 그리드로 이동", timeout=REGISTER_TIMEOUT)
        except WaitTimeout:
            # ★ **오류가 아니다.** 배송보류가 걸린 전표는 배송할 수 없어
            #   개별배송으로 올라오지 않는다 (사용자 확정 2026-09-10).
            #   전체선택은 정상 동작하는 것을 실측으로 확인했다(15행 전부 선택).
            log.warning(
                "개별배송으로 올라온 행이 없다 (%.0f초). 등록할 것이 없다.\n"
                "  상단 %d행 / 하단 %d행 / 선택 %s행\n"
                "  하단 전표에 **배송보류**가 걸려 있으면 배송할 수 없어 정상이다.",
                REGISTER_TIMEOUT, ui.visible_row_count(upper),
                ui.visible_row_count(lower),
                selected if selected is not None else "?")
            return 0

        after_top = _wait_rows(upper, "상단 배송등록 그리드")
        log.info("개별배송 완료 — 상단 %d행 → %d행 (화면 기준, 하단 %d행)",
                 before_top, after_top, ui.visible_row_count(lower))
        return after_top


# --------------------------------------------------- 배송정보설정 (택배사 / 박스)
# 택배사와 박스는 **같은 방식**으로 처리한다 (사용자 확정 2026-09-07).
#   드롭다운을 열고 → 이름이 완전히 일치하는 첫 항목을 고르고 → [적용].
# 다른 점은 콤보의 구조뿐이다.
#   택배사: `cboTag` > `tbPnl` > `cbo` 에서 값이 읽힌다
#   박스  : `cboTagAmt` 자체에서 값이 읽힌다 (안쪽 `cbo` 가 없다. 2026-09-07 실측)


def _courier_combo(screen):
    """배송정보설정 [업체] 콤보. 값이 읽히는 안쪽 `cbo` 를 돌려준다."""
    outer = ui.find(screen, auto_id=COURIER_COMBO_AUTO_ID, what="택배사 콤보")
    return ui.find(outer, auto_id=COURIER_COMBO_INNER_AUTO_ID, what="택배사 콤보(내부)")


def _box_combo(screen):
    """배송정보설정 [박스] 콤보. 안쪽 `cbo` 가 없다 — 이 컨트롤이 곧 값이다."""
    return ui.find(screen, auto_id=BOX_COMBO_AUTO_ID, what="박스 콤보")


def current_courier(screen) -> str:
    return ui.cell_value(_courier_combo(screen)).strip()


def current_box(screen) -> str:
    return ui.cell_value(_box_combo(screen)).strip()


def _open_dropdown(combo, what: str) -> None:
    """콤보의 드롭다운을 연다. ExpandCollapse 우선, 실패하면 클릭.

    두 콤보 모두 ExpandCollapse 패턴이 없어 실제로는 클릭으로 열린다
    (2026-09-04 택배사 / 2026-09-07 박스 실측). 다른 버전 대비로 순서만 남긴다.
    """
    try:
        combo.iface_expand_collapse.Expand()
        log.info("%s 드롭다운 열기 (ExpandCollapse)", what)
        return
    except Exception as exc:
        log.debug("ExpandCollapse 불가: %s", type(exc).__name__)

    ui.click(combo, f"{what} 드롭다운 열기", methods=("클릭",))


def _select_from_dropdown(combo, name: str, pid: int | None, what: str,
                          dry_run: bool = False) -> None:
    """`combo` 의 드롭다운에서 `name` 과 **완전히 일치하는 첫 항목**을 고른다.

    부분 일치를 쓰지 않는다. 이름은 서로 접두사가 될 수 있다
    (`택배사A` / `택배사A(직접)` 등).

    이름이 중복될 수 있으나 **코드 값은 UIA로 읽을 수 없다.**
    2026-09-04 조사 결과 드롭다운은 이름만 있는 `List` > `ListItem` 이고
    항목의 Value / Description / Help / auto_id 가 전부 비어 있다.
    → 중복이 있으면 **처음 일치하는 항목**을 고른다 (사용자 확정 2026-09-04).

    선택 결과는 콤보의 값으로 검증한다. 이 값은 두 콤보 모두 읽힌다
    (택배사 2026-09-04 / 박스 2026-09-07 실측).
    """
    before = ui.cell_value(combo).strip()

    if dry_run:
        log.info("[dry-run] %s 선택 예정: %r (현재 %r) — %s",
                 what, name, before, ui.describe(combo))
        return

    if before == name:
        log.info("%s 가 이미 %r 이다. 드롭다운을 열지 않는다.", what, name)
        return

    _open_dropdown(combo, what)

    try:
        ui.click_dropdown_item(pid, name, what)
    except ui.ControlNotFound as exc:
        raise ScreenError(
            f"{what} {name!r} 을(를) 드롭다운에서 찾지 못했다.\n"
            f"  현재 값: {before!r}\n"
            f"  {exc}"
        ) from exc

    try:
        wait_for(lambda: ui.cell_value(combo).strip() == name,
                 f"{what} {name!r} 반영", timeout=SETTINGS.timeouts.control)
    except WaitTimeout as exc:
        raise ScreenError(
            f"{name!r} 을(를) 골랐으나 {what} 콤보 값이 "
            f"{ui.cell_value(combo)!r} 그대로다."
        ) from exc

    log.info("%s 선택: %r → %r", what, before, name)


def select_courier(screen, name: str, pid: int | None, dry_run: bool = False) -> None:
    """[업체] 드롭다운에서 택배사를 고른다."""
    if not name:
        raise ScreenError(
            "택배사가 지정되지 않았다. 런처에서 [택배사] 를 입력하거나 "
            "config/settings.local.json 의 delivery_company 를 채울 것."
        )
    _select_from_dropdown(_courier_combo(screen), name, pid, "택배사", dry_run=dry_run)


def select_box(screen, name: str, pid: int | None, dry_run: bool = False) -> None:
    """[박스] 드롭다운에서 박스 규격을 고른다.

    항목 이름은 업체(계정)마다 다르다. **코드에 고정하지 않고** 사용자가 입력한 값을
    완전 일치로 찾는다.
    """
    if not name:
        raise ScreenError(
            "박스가 지정되지 않았다. 런처에서 [박스] 를 입력하거나 "
            "config/settings.local.json 의 delivery_box 를 채울 것."
        )
    _select_from_dropdown(_box_combo(screen), name, pid, "박스", dry_run=dry_run)


def normalized_mode(mode: str | None) -> str:
    """자동/수동 값을 확인한다. **틀린 값·빈 값으로 화면을 건드리기 전에** 막는다 (기본값 없음, 10-02)."""
    value = (mode or "").strip()
    if not value:
        raise ScreenError("물류관리 자동/수동이 비어 있다 (config/settings.local.json 의 logistics_mode). "
                          f"{' / '.join(MODES)} 중 하나를 고를 것.")
    if value not in MODES:
        raise ScreenError(
            f"물류관리 자동/수동 값이 {value!r} 이다. {' / '.join(MODES)} 중 하나여야 "
            "한다 (config/settings.local.json 의 logistics_mode).")
    return value


def _mode_combo(screen):
    return ui.find(screen, auto_id=MODE_COMBO_AUTO_ID, what="자동/수동 콤보")


def current_mode(screen) -> str:
    return ui.cell_value(_mode_combo(screen)).strip()


def _has_finish_button(screen, mode: str) -> bool:
    """그 모드의 마지막 버튼이 지금 화면에 있는지. **기다리지 않는다.**"""
    return ui.exists(screen, auto_id=FINISH_BUTTONS[mode][0], control_type="Button")


def select_mode(screen, mode: str, pid: int | None, dry_run: bool = False) -> str:
    """자동/수동 콤보를 `mode` 로 맞춘다. 맞춘 값을 돌려준다.

    **dry-run 에서도 바꾼다.** 등록모드처럼 화면 모드일 뿐 저장이 아니고,
    바꾸지 않으면 dry-run 에서 마지막 버튼이 있는지 확인할 수 없다.
    dry-run 이면 끝에 `run()` 이 원래 값으로 되돌린다 (`_restore_mode`).

    바꾼 뒤에는 **그 모드의 마지막 버튼이 나타나기를 기다린다.** 콤보 값만
    바뀌고 버튼 줄이 아직 안 바뀐 순간에 넘어가면, 끝에서 버튼을 못 찾는다.
    """
    mode = normalized_mode(mode)
    _select_from_dropdown(_mode_combo(screen), mode, pid, "자동/수동")
    name = FINISH_BUTTONS[mode][1]
    try:
        wait_for(lambda: _has_finish_button(screen, mode),
                 f"{mode} 의 [{name}] 버튼 등장", timeout=SETTINGS.timeouts.control)
    except WaitTimeout as exc:
        raise ScreenError(
            f"자동/수동을 {mode!r} 로 맞췄는데 [{name}] 버튼이 나타나지 않았다 "
            f"(콤보 값 {current_mode(screen)!r}).") from exc
    log.info("물류관리 자동/수동 = %r — 저장 뒤 [%s] 을 누른다%s", mode, name,
             " (dry-run 이라 누르지는 않는다)" if dry_run else "")
    return mode


def _restore_mode(screen, mode: str, pid: int | None) -> bool:
    """시험 실행이 바꾼 자동/수동을 `mode` 로 되돌린다. 되돌렸으면 True.

    ERPia 가 업체별로 기억해서, 두면 사람이 다음에 열 때 바뀐 값을 본다.
    중단됐으면 건드리지 않는다 (드롭다운을 연 채 멈출 수 있다).
    실패해도 올리지 않는다 — 시험 실행 결과나 원래 예외를 가리지 않게.
    """
    token = cancel.current()
    if token is not None and token.cancelled:
        log.warning("중단돼 자동/수동을 %r 로 되돌리지 않았다.", mode)
        return False
    try:
        _select_from_dropdown(_mode_combo(screen), mode, pid, "자동/수동 되돌리기")
    except Exception as exc:
        log.warning("자동/수동을 %r 로 되돌리지 못했다: %s", mode, exc)
        return False
    return True


def _apply(screen, button_auto_id: str, column: str, expected: str, what: str,
           dry_run: bool = False) -> str:
    """[적용]. 상단 그리드 **전체**에 걸린다 (사용자 확인 2026-09-04).

    적용 결과는 상단 그리드의 `column` 값으로 확인한다.
    """
    button = _button(screen, button_auto_id, f"{what} 적용 버튼")
    ui.click(button, f"{what} [적용]", dry_run=dry_run)
    if dry_run:
        return ""

    grid = top_grid(screen)

    def applied() -> bool:
        return any(
            # ★ 상단 그리드 오른쪽 컬럼이라 화면 밖일 수 있다. 기본 timeout
            #   이면 행마다 10초를 통째로 버린다 (2026-09-10 감사).
            #   확인 실패를 실패로 단정하지 않으므로 짧게 봐도 안전하다.
            ui.cell_text(row, column, timeout=ui.INFO_CELL_TIMEOUT).strip() == expected
            for row in ui.grid_rows(grid)
            if not ui.is_group_row(row)
        )

    try:
        wait_for(applied, f"상단 {column} 컬럼 반영",
                 timeout=SETTINGS.timeouts.control)
        log.info("적용 확인 — 상단 %s = %r", column, expected)
    except WaitTimeout:
        # 컬럼이 화면 밖이면 값을 읽을 수 없다. 실패로 단정하지 않고 남긴다.
        log.warning(
            "적용 후 상단 %s 컬럼에서 %r 을(를) 확인하지 못했다. "
            "컬럼이 화면 밖일 수 있다. 저장 결과로 판단할 것.",
            column, expected,
        )
    return expected


def apply_courier(screen, dry_run: bool = False) -> str:
    expected = "" if dry_run else current_courier(screen)
    return _apply(screen, COURIER_APPLY_AUTO_ID, TOP_COURIER_COLUMN, expected,
                  "택배사", dry_run=dry_run)


def apply_box(screen, dry_run: bool = False) -> str:
    expected = "" if dry_run else current_box(screen)
    return _apply(screen, BOX_APPLY_AUTO_ID, TOP_BOX_COLUMN, expected,
                  "박스", dry_run=dry_run)


# ------------------------------------------------------------------ 저장
def wait_saved(screen, main_window=None, timeout: float | None = None,
               info: dict | None = None) -> bool:
    """저장이 **정상적으로 끝났는지** 확인한다.

    판정 기준은 **[등록(I)] 버튼이 활성화되는 것**이다 (사용자 확인 2026-09-10).
    저장에 성공하면 팝업이 뜨지 않고 등록 버튼이 살아난다.

    ★ 실패했을 때는 메시지 팝업(예: `배송요금을 입력하세요`)이 떠 있을 수 있다.
      **[확인] 하나면 눌러 닫고** 그 글을 `info["message"]` 에 담는다 (`close_save_failure`).
    """
    timeout = SETTINGS.timeouts.control if timeout is None else timeout
    info = {} if info is None else info
    try:
        button = _button(screen, INSERT_BUTTON_AUTO_ID, "등록 버튼")
    except (ScreenError, ui.ControlNotFound) as exc:
        # ★ `_button()` 은 `ui.find` 를 쓰므로 `ui.ControlNotFound` 를 던진다.
        #   `ScreenError` 만 잡으면(둘은 형제다) 이 경로가 **한 번도 실행되지
        #   않고** 예외가 GUI 까지 올라가 ERPia 를 닫아 버린다 (2026-09-10 감사).
        log.warning("등록 버튼을 찾지 못해 저장 결과를 확인하지 못했다: %s", exc)
        info["message"] = close_save_failure(main_window)
        return False

    try:
        wait_for(lambda: button.is_enabled(), "등록 버튼 활성화", timeout=timeout)
    except WaitTimeout:
        log.warning("저장 뒤 %.0f초 안에 [등록] 이 활성화되지 않았다. 저장되지 않았다.",
                    timeout)
        info["message"] = close_save_failure(main_window)
        return False
    log.info("저장 확인 — [등록] 이 활성화됐다.")
    return True


def close_save_failure(main_window) -> str:
    """저장 실패 메시지 팝업을 **[확인] 으로 닫고** 그 글을 돌려준다. 못 닫았으면 빈 문자열.

    ★ 예전(09-10 확정)에는 누르지 않고 사람이 보게 뒀다. **09-22 사용자 확정으로 바꿨다** —
      [확인] 하나뿐인 알림이면 누르고 넘어간다. 글은 리포트 '확인할 것' 에 남는다.
      저장이 안 됐으므로 마지막 버튼(운송장출력/엑셀파일생성)은 **여전히 누르지 않는다.**
    """
    text = dialogs.dismiss_message_box(main_window)
    if not text:
        log.warning("저장 실패를 알리는 팝업을 닫지 못했다(없거나 고르는 팝업). "
                    "화면을 사람이 확인할 것.")
        return ""
    log.error("저장 실패 — 화면 메시지 %s. [확인]을 눌러 닫았다. "
              "마지막 버튼(운송장출력/엑셀파일생성)은 누르지 않는다.", text)
    return text


def press_finish(screen, mode: str, dry_run: bool = False) -> bool:
    """저장 뒤 **마지막 버튼**을 누른다. 여기까지가 자동화 범위다.

    | 자동/수동 | 버튼 |
    | --- | --- |
    | 자동 | [운송장출력] (사용자 확정 2026-09-10) |
    | 수동 | [엑셀파일생성] (사용자 확정 2026-09-17) |

    누른 뒤에 뜨는 창은 건드리지 않는다. 사람이 처리한다.
    """
    mode = normalized_mode(mode)
    auto_id, name = FINISH_BUTTONS[mode]
    label = name + (" [dry-run]" if dry_run else "")
    with step(log, label):
        button = _button(screen, auto_id, f"{name} 버튼")
        if dry_run:
            ui.click(button, name, dry_run=True)
            return False
        # 저장 버튼과 같은 이유로 **마우스 클릭만** 쓴다. Invoke 는 버튼 핸들러가
        # 창을 띄우면 반환되지 않고 63초 뒤 COMError 로 끝난다.
        ui.click(button, name, methods=("클릭",))
        log.info("%s 을 눌렀다. 이후 뜨는 창은 사람이 처리한다.", name)
        return True


def save(screen, main_window=None, pid: int | None = None,
         dry_run: bool = False) -> bool:
    """[저장(S)] 을 누른다.

    저장 결과 확인과 마지막 버튼([운송장출력]/[엑셀파일생성])은 `run()` 이 이어서 한다
    (사용자 확정 2026-09-10 — 예전에는 저장 클릭까지가 범위였다).

    **저장 직후에는 화면을 넓게 뒤지지 않는다.** 배송요금 누락 같은 입력 오류
    팝업이 떠 있으면 UIA 호출이 매번 63초씩 막힌다 (2026-09-04 실측).
    확인은 `wait_saved()` 에서 **등록 버튼 하나만** 본다.

    `main_window` / `pid` 는 호출부 호환을 위해 받지만 쓰지 않는다.
    """
    label = "물류관리 저장" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        upper, lower = top_grid(screen), bottom_grid(screen)
        before_top = ui.visible_row_count(upper)
        before_bottom = ui.visible_row_count(lower)

        button = _button(screen, SAVE_BUTTON_AUTO_ID, "저장 버튼")
        if dry_run:
            ui.click(button, "저장(S)", dry_run=True)
            log.warning("[dry-run] 저장하지 않았다. (상단 %d행 / 하단 %d행)",
                        before_top, before_bottom)
            return False

        if before_top == 0:
            log.info("상단 그리드가 비어 있다. 저장할 것이 없다.")
            return False

        ui.wait_enabled(button, "저장 버튼", timeout=SETTINGS.timeouts.control)
        # **마우스 클릭만 쓴다.** Invoke 는 버튼 핸들러가 모달 팝업을 띄우면
        # 반환되지 않고 63초 뒤 COMError 로 타임아웃한다. 저장은 이미 눌린 상태인데
        # 코드는 실패로 보고 다음 방법을 시도해 또 타임아웃한다
        # (2026-09-04 실행에서 3회 재현. 배송요금 누락 팝업).
        ui.click(button, "저장(S)", methods=("클릭",))
        log.info("저장 버튼을 눌렀다 (상단 %d행 / 하단 %d행).",
                 before_top, before_bottom)
        return True


def _save_and_print(screen, target, pid=None, dry_run: bool = False) -> None:
    """저장 → 저장 확인 → 마지막 버튼. CLI 의 `--step save` 가 쓴다.

    화면의 자동/수동을 **바꾸지 않고** 지금 값을 따른다.
    """
    mode = normalized_mode(current_mode(screen))
    if not save(screen, main_window=target.main_window(), pid=pid, dry_run=dry_run):
        return
    if wait_saved(screen, main_window=target.main_window()):
        press_finish(screen, mode, dry_run=dry_run)
    else:
        log.warning("저장 확인이 되지 않아 [%s] 을 누르지 않는다.",
                    FINISH_BUTTONS[mode][1])


# ------------------------------------------------------------------ 전체 흐름
def run(target, courier: str | None = None, box: str | None = None,
        mode: str | None = None, dry_run: bool = False) -> dict:
    """물류관리 전체 흐름.

    택배사와 박스를 **각각 고르고 각각 [적용]** 한 뒤 저장한다
    (사용자 확정 2026-09-07). 두 [적용] 은 서로 다른 버튼이다.

    `mode` 는 자동/수동이다. 저장 뒤 누르는 버튼이 달라진다 (`press_finish`).
    """
    courier = (courier or SETTINGS.delivery_company or "").strip()
    box = (box or SETTINGS.delivery_box or "").strip()
    # 틀린 값이면 **화면을 건드리기 전에** 멈춘다.
    mode = normalized_mode(mode or SETTINGS.logistics_mode)
    finish = FINISH_BUTTONS[mode][1]

    # `slips` 물류관리 대상 주문 수(하단 매출번호) / `slips_saved` 올라간 주문 수 /
    # `boxes` 저장할 박스(송장 = 배송전표) 수(상단). 모르면 None.
    # `_exact` 가 False 면 화면 밖 행이 있어 "N건 이상" 이다 (`count_slips`).
    result = {"orders": 0, "registered": 0, "courier": courier, "box": box,
              "mode": mode, "finish": finish,
              "save_clicked": False, "saved": False, "finish_clicked": False,
              "skipped": "", "slips": None, "slips_exact": False,
              "slips_saved": None, "slips_left": None, "save_message": "",
              "boxes": None, "boxes_exact": False}

    screen = open_screen(target)
    # ★ 시험 실행이면 끝에 **원래 값으로 되돌린다** (사용자 확정 2026-09-21).
    restore_to = current_mode(screen) if dry_run else ""
    try:
        # ★ 자동/수동은 **등록모드 준비보다 먼저** 맞춘다 (사용자: "처음에 선택").
        #   바꾸는 것이 등록모드나 하단 목록을 건드리더라도, 바로 뒤의
        #   `prepare()` 가 등록모드와 하단 조회를 다시 채운다.
        select_mode(screen, mode, pid=target.pid, dry_run=dry_run)
        result["orders"] = prepare(screen)
        lower = bottom_grid(screen)
        # 읽기만 한다. dry-run 에서도 "올릴 대상이 몇 건인지" 를 보여 줄 수 있다.
        result["slips"], result["slips_exact"] = count_slips(lower)
        log.info("물류관리 대상 주문 %s건%s", result["slips"],
                 "" if result["slips_exact"] else " (화면 밖 행이 있거나 못 셌다)")
        result["registered"] = individual_delivery(screen, pid=target.pid, dry_run=dry_run)
        if not result["registered"] and not dry_run:
            # 등록된 것이 없으면 택배사·박스·저장은 할 일이 없다.
            # 배송보류가 걸린 전표만 남은 정상 상황이다 (사용자 확정 2026-09-10).
            log.info("개별배송으로 등록된 행이 없다. 택배사·박스·저장을 건너뛴다.")
            result["skipped"] = "등록 대상 없음(배송보류 등)"
            return result

        select_courier(screen, courier, pid=target.pid, dry_run=dry_run)
        apply_courier(screen, dry_run=dry_run)

        select_box(screen, box, pid=target.pid, dry_run=dry_run)
        apply_box(screen, dry_run=dry_run)

        if not dry_run:
            # 저장 **전에** 센다 — 등록모드의 상단은 이번 개별배송분뿐이다. 저장 뒤 상단은
            # 조회 기간의 것을 보여 줄 수 있다. 저장은 상단 전체를 올린다.
            result["boxes"], result["boxes_exact"] = count_boxes(top_grid(screen))
            log.info("저장할 박스(송장) %s건%s", result["boxes"],
                     "" if result["boxes_exact"] else " (화면 밖 행이 있거나 못 셌다)")

        rows_before_save = ui.visible_row_count(lower)
        result["save_clicked"] = save(screen, main_window=target.main_window(),
                                      pid=target.pid, dry_run=dry_run)
        if not result["save_clicked"]:
            return result

        # 저장이 정상이면 팝업 없이 [등록] 이 활성화된다. 그때만 마지막 버튼을 누른다.
        # 저장이 안 됐는데 누르면 엉뚱한 전표가 나간다.
        saved_info: dict = {}
        result["saved"] = wait_saved(screen, main_window=target.main_window(),
                                     info=saved_info)
        result["save_message"] = saved_info.get("message", "")
        if result["saved"]:
            # ★ 저장 확인 **뒤에만** 읽는다 (실패 팝업이 떠 있으면 UIA 가 막힌다).
            #   마지막 버튼보다 **먼저** 읽는다 — 그 뒤에 뜨는 창은 사람 몫이다.
            #   실기: 09-22 주문 2 → 남은 0 (docs/archive/HANDOFF_20260928.md), 09-29 주문 2 → 남은 0.
            left, left_exact = slips_left_after_save(lower, rows_before_save)
            if left is not None and left_exact:
                result["slips_left"] = left
            if (left is not None and result["slips"] is not None
                    and result["slips_exact"] and left_exact):
                result["slips_saved"] = max(result["slips"] - left, 0)
            log.info("저장 뒤 하단에 남은 주문 %s건 → 올라간 주문 %s건", left,
                     result["slips_saved"])
            result["finish_clicked"] = press_finish(screen, mode, dry_run=dry_run)
        else:
            log.warning("저장 확인이 되지 않아 [%s] 을 누르지 않는다.", finish)
        return result
    finally:
        if restore_to and restore_to != mode and not _restore_mode(
                screen, restore_to, target.pid):
            result["mode_not_restored"] = restore_to


def _cli() -> int:
    parser = argparse.ArgumentParser(description="물류관리(물류처리) 자동화")
    parser.add_argument("--step", default="all",
                        choices=["all", "enter", "register", "courier", "box",
                                 "save"])
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--courier", default=None, help="택배사 이름 (완전 일치)")
    parser.add_argument("--box", default=None, help="박스 규격 이름 (완전 일치)")
    parser.add_argument("--mode", default=None, choices=list(MODES),
                        help="자동/수동. 주지 않으면 설정값(logistics_mode)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness
    from utils.logger import setup_logging

    setup_logging()
    ensure_dpi_awareness()

    if not SETTINGS.target_exe:
        log.error("config/settings.local.json 에 target_exe 가 없다.")
        return 1
    target = connect(SETTINGS.target_exe, pid=args.pid)
    if target is None:
        log.error("ERPia가 실행 중이 아니다. 먼저 실행하고 로그인할 것.")
        return 1

    courier = (args.courier or SETTINGS.delivery_company or "").strip()
    box = (args.box or SETTINGS.delivery_box or "").strip()

    if args.step == "all":
        result = run(target, courier=courier, box=box, mode=args.mode,
                     dry_run=args.dry_run)
        log.info("결과: %s", result)
        return 0

    screen = open_screen(target)
    if args.step == "enter":
        prepare(screen)
        return 0
    if args.step == "register":
        prepare(screen)
        individual_delivery(screen, pid=target.pid, dry_run=args.dry_run)
        return 0
    if args.step == "courier":
        select_courier(screen, courier, pid=target.pid, dry_run=args.dry_run)
        apply_courier(screen, dry_run=args.dry_run)
        return 0
    if args.step == "box":
        select_box(screen, box, pid=target.pid, dry_run=args.dry_run)
        apply_box(screen, dry_run=args.dry_run)
        return 0
    if args.step == "save":
        _save_and_print(screen, target, pid=target.pid,
             dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
