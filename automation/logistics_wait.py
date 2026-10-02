r"""물류대기 관리 화면 (Phase 8).

매출처리된 전표 중 **재고가 부족한 건에 배송보류를 걸고, 정상 건만 물류처리로 넘긴다.**

근거: docs/PROCESS.md "Phase 8" + docs/CONTROLS.md "물류대기 관리 화면"

    .venv\Scripts\python.exe -m automation.logistics_wait --dry-run
    .venv\Scripts\python.exe -m automation.logistics_wait
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation import sidebar  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from utils import dialogs, ui, winprobe  # noqa: E402
from utils.logger import get_logger, step  # noqa: E402
from utils.wait import WaitTimeout, pause, wait_for  # noqa: E402

log = get_logger(__name__)

# --- 확정된 식별자 (docs/CONTROLS.md) ---
SCREEN_PANE_AUTO_ID = "Frm_Management_HoldLogisticsStandard"
SCREEN_TAB_TITLE = "물류대기 관리"   # 메인 창의 화면 탭 이름 (2026-09-04 확인)
SIDE_TAB_AUTO_ID = "tab_Data"

TAB_GENERAL = "일반"
# name에 개행이 들어 있다. '상품별 재고검토' 로는 찾지 못한다.
TAB_STOCK_REVIEW = "상품별\n재고검토"

TOP_GRID_AUTO_ID = "gridCtrl_BGSVDataTop"
BOTTOM_GRID_AUTO_ID = "gridCtrl_BGSVDataBottomOrder"

GENERAL_GRID_AUTO_ID = "gridCtrl_NormalDataLeft"  # 일반 탭 그리드 (2026-09-04 확인)

SEARCH_BUTTON_AUTO_ID = "btn_Search"
SAVE_BUTTON_AUTO_ID = "btn_Save"

SHORTAGE_COLUMN = "부족수량"      # 값이 있으면 부족, 비어 있으면 정상
TOP_CODE_COLUMN = "상품코드"       # 상단 그리드
# 상단 그리드 상품명 — **화면에 보여 줄 이름** (사용자 요청 09-21: 코드 대신 이름).
# CONTROLS.md "상품별 재고검토 탭 내부" 의 상단은 `ERP 상품명`(띄어 씀), 하단은 `ERP상품코드`(붙여 씀)라
# 헤더에서 실제 이름을 한 번 찾는다 (`_name_column`). 못 찾으면 이름 없이 간다.
TOP_NAME_COLUMNS = ("ERP 상품명", "ERP상품명")
NAME_READ_TIMEOUT = 1.0             # 이름은 표시용이다. 못 읽어도 흐름은 계속한다
BOTTOM_CODE_COLUMN = "ERP상품코드"  # 하단 그리드
HOLD_COLUMN = "보류"                # 값이 있으면(X 아이콘) 이미 보류된 건
# 주문(= 전표 = 박스) 수를 셀 컬럼 — 재고검토 하단·[일반] 탭 공통 (09-29 실측). 행은 상품이라 한 전표가
# 여러 행이다 ([일반] 8행 = 전표 2). [일반] 탭에서는 오른쪽 밖이라 가로로 넘겨 읽는다 (`count_general_slips`)
SLIP_NUMBER_COLUMN = "전표번호"

# 완전 일치로 찾는다. '배송보류(주문단위)' 가 함께 있어 부분 일치는 위험하다.
MENU_HOLD = "배송보류"
# 우클릭 메뉴의 **범위 일괄 체크** (2026-09-10 실측으로 확인).
# 한 행씩 체크하면 410행에 4분 34초가 걸렸다. 범위로 묶으면 몇 번으로 끝난다.
MENU_RANGE_CHECK = "선택영역 체크"
# 범위로 묶을 최소 행 수. 이보다 적으면 그냥 하나씩 누르는 편이 빠르고 안전하다.
RANGE_CHECK_MIN = 3


# 좌측 프로세스바 (`automation/sidebar.py`)
SIDEBAR_LOGISTICS_WAIT = sidebar.LOGISTICS_WAIT
SIDEBAR_LOGISTICS = sidebar.LOGISTICS

MAX_SCROLLS = 50         # 하단 그리드를 훑는 스크롤 상한

# --- 하단 정렬 (2026-09-17 재도입, 사용자 요청) ---
# 하단 `ERP상품코드` 헤더를 한 번 누르면 오름차순으로 같은 코드가 모이고,
# 행 번호도 보이는 순서대로 다시 매겨진다 (09-17 실측).
# 그러면 대상 코드 묶음이 끝나는 곳에서 스캔을 멈출 수 있다.
# 정렬 상태는 헤더의 Legacy `DefaultAction` 이 **다음 클릭 동작**으로 알려 준다.
# 누르면 **정렬 없음 → 오름차순 → 내림차순 → 오름차순** 으로 바뀐다 (실측).
# 내림차순의 라벨이 `정렬 제거` 인데, 실제로 한 번 누르면 정렬이 풀리지 않고
# **오름차순이 된다** (2026-09-17, 두 번 누르게 만들었다가 실측으로 고쳤다).
SORT_NEXT_ASC = "오름차순 정렬"     # 지금 정렬 없음
SORT_NEXT_DESC = "내림차순 정렬"    # 지금 오름차순 ← 원하는 상태
SORT_NEXT_NONE = "정렬 제거"        # 지금 내림차순
# 오름차순까지 몇 번 눌러야 하나. 여기 없는 값이면 누르지 않는다.
SORT_CLICKS_TO_ASC = {SORT_NEXT_DESC: 0, SORT_NEXT_ASC: 1, SORT_NEXT_NONE: 1}
SORT_SETTLE_TIMEOUT = 10.0
# [조회] 를 다시 눌러도, 상단 행을 바꿔 하단이 재조회돼도 정렬은 남는다.
# 화면 탭을 닫았다 열면 풀린다 (2026-09-17 실측).


def excluded_codes() -> set[str]:
    """**배송보류를 걸지 않을 상품코드.** 설정에서만 읽는다.

    부족수량이 있어도 이 목록에 있으면 건너뛴다 (사용자 요청 2026-09-10).
    완전일치로 비교한다 — 비슷한 코드에 보류가 걸리면 되돌리기 번거롭다.
    """
    values = getattr(SETTINGS, "hold_exclude_codes", None) or []
    return {str(v).strip() for v in values if str(v).strip()}

# 상단 행을 바꾸면 하단이 다시 그려지면서 체크가 풀린다. 그때까지 기다리는 상한.
# 실측 2.16초 (2026-09-09). 넉넉히 잡되 무한 대기는 두지 않는다.
BOTTOM_RELOAD_TIMEOUT = 10.0

# --- 하단 재조회 판정 (2026-09-15 실측으로 새로 만들었다) ---------------------
# 실측: 상단 행을 고르면 하단 재조회는 **0.1초 안에** 끝난다. 왕복 피크 3.7ms,
# 유휴 0.12ms. 그래서 상한이 아니라 **폴링 간격**이
# 곧 소요시간이 된다. 간격을 짧게 잡는다.
BOTTOM_READY_INTERVAL = 0.15
BOTTOM_READY_ROUNDS = 2          # 고른 코드가 보이고 이만큼 연속으로 같으면 완료
BOTTOM_MISSING_ROUNDS = 6        # 코드가 안 보일 때, 조용한 상태가 이만큼 이어지면
BOTTOM_MISSING_GRACE = 2.0       # + 이 시간은 지나야 '주문이 없다' 로 확정한다
BOTTOM_READY_TIMEOUT = 20.0
# 왕복이 이 이상이면 ERPia UI 스레드가 밀려 있다고 본다 (유휴 0.1ms / 조회 6.3ms).
BUSY_ROUNDTRIP = 0.001

# 저장 뒤 행 수가 그대로일 때, 한가한 상태가 이만큼 이어지면 '변화 없음' 으로
# 확정한다. 예전에는 상한(120초)을 통째로 썼다.
SAVE_IDLE_ROUNDS = 6
SAVE_IDLE_GRACE = 3.0


class ScreenError(RuntimeError):
    """물류대기 화면 처리 실패."""


# ------------------------------------------------------------------ 진입/탭
def is_open(main_window) -> bool:
    # 화면 Pane 은 5층에 있다. 없을 때 12층까지 훑지 않는다(docs/RESOURCES.md 4번).
    return ui.exists(main_window, auto_id=SCREEN_PANE_AUTO_ID, control_type="Pane",
                     max_depth=ui.SCREEN_PANE_DEPTH)


# --- 물류대기를 쓰지 않는 계정 (2026-09-17) ---
# 계정마다 프로세스바 구성이 다르다. 물류대기를 쓰지 않는 계정은
# `imgLbl_HoldLogistics` 가 **트리에서 아예 빠진다** (숨김 항목까지 나열해도 없다).
# 그런 계정은 매출처리 뒤 **바로 물류관리로 간다**
# (사용자 확정 2026-09-17). 예전에는 10초 찾다가 흐름이 멈췄다.
#
# ★ "없다" 는 **[물류처리] 는 보이는데 [물류대기] 만 없을 때만** 단정한다.
#   [물류처리] 도 안 보이면 프로세스바가 아직 안 그려졌거나 다른 문제다 —
#   그때 건너뛰면 원래 있어야 할 물류대기를 조용히 빼먹는다.
MENU_CHECK_TIMEOUT = 3.0     # [물류처리] 가 보인 뒤 [물류대기] 를 더 기다리는 시간
# 프로세스바 항목은 **깊이 5** 에 있다 (2026-09-17 실측). 깊이를 묶으면 없는 항목을
# 찾는 한 번이 5.3초 → 1.6초가 된다. 화면 Pane 과 같은 상한(6)을 쓴다.
SIDEBAR_DEPTH = ui.SCREEN_PANE_DEPTH
NO_MENU_REASON = "이 계정의 프로세스바에 물류대기가 없다 — 물류관리로 바로 간다"


def menu_available(target) -> bool:
    """이 계정에 [물류대기] 메뉴가 있는가.

    있으면 True, **[물류처리] 는 있는데 [물류대기] 가 없으면** False.
    프로세스바 자체가 안 보이면 판단할 수 없으므로 `ScreenError`.
    """
    main_window = target.main_window()
    if is_open(main_window):
        return True
    # 먼저 [물류처리] 로 **프로세스바가 그려졌는지** 확인한다. 물류대기를 쓰는
    # 계정에도 늘 있는 항목이다(물류대기 다음에 누른다).
    # ★ 순서를 이렇게 둔 이유: 처음에는 물류대기 → 물류처리 → 물류대기로 찾아
    #   건너뛰는 데 13.7초가 걸렸다 (2026-09-17 실측). 찾기 한 번이 수 초다.
    #   순서를 바꾸고 깊이를 묶어 6.8초가 됐다.
    try:
        ui.find(main_window, auto_id=SIDEBAR_LOGISTICS, max_depth=SIDEBAR_DEPTH,
                what="프로세스바 '물류처리'")
    except ui.ControlNotFound as exc:
        raise ScreenError(
            "프로세스바에 '물류처리' 가 보이지 않는다. 화면이 아직 준비되지 않았을 "
            "수 있어 물류대기가 있는지 판단하지 않는다."
        ) from exc
    # 프로세스바가 그려진 뒤에도 [물류대기] 가 없으면 **없는 것**이다.
    try:
        ui.find(main_window, auto_id=SIDEBAR_LOGISTICS_WAIT, max_depth=SIDEBAR_DEPTH,
                timeout=MENU_CHECK_TIMEOUT, what="프로세스바 '물류대기'")
        return True
    except ui.ControlNotFound:
        return False


def open_screen(target):
    """좌측 프로세스바에서 물류대기로 들어간다. 화면 Pane을 돌려준다.

    ★ **dry-run 에서도 실제로 누른다.** 화면을 여는 것은 데이터를 바꾸지 않는데,
      예전에는 이 클릭까지 건너뛰어 화면이 열리지 않았고 그 뒤 모든 단계가
      확인되지 않았다. 열릴 수 없는 창을 기다리느라 18초를 쓰기도 했다
      (2026-09-10 실측).
    """
    with step(log, "물류대기 화면 진입"):
        main_window = target.main_window()

        # ★ 예전에는 열려 있으면 `auto_id="Tab"` 아래에서 화면 탭을 찾았다. 그 auto_id 는
        #   주문매핑 **내부** 탭이고 화면 탭의 부모는 auto_id 가 비어 있어 늘 3초 뒤 실패했고,
        #   그러면 **화면 밖 Pane 을 들고** 진행했다 (09-28 검토). 탭은 주문매핑처럼
        #   부모 없이 제목으로 찾는다 (`order_mapping._activate_tab`)
        opened = is_open(main_window)
        if opened and ui.screen_visible(main_window, SCREEN_PANE_AUTO_ID, "물류대기 화면"):
            log.info("이미 열려 있고 화면에 보인다.")
        elif opened and ui.exists(main_window, title=SCREEN_TAB_TITLE, control_type="TabItem"):
            log.info("이미 열려 있다. 탭을 앞으로 가져온다 (다시 열면 조회가 초기화된다).")
            item = ui.find(main_window, title=SCREEN_TAB_TITLE, control_type="TabItem",
                           what=f"탭 {SCREEN_TAB_TITLE!r}")
            ui.click(item, f"탭 {SCREEN_TAB_TITLE!r}", methods=("Select", "클릭"))
        else:
            entry = ui.find(
                main_window, auto_id=SIDEBAR_LOGISTICS_WAIT, what="프로세스바 '물류대기'"
            )
            ui.click(entry, "좌측 프로세스바 '물류대기'")

        try:
            pane = wait_for(
                lambda: ui.find(main_window, max_depth=ui.SCREEN_PANE_DEPTH,
                                auto_id=SCREEN_PANE_AUTO_ID,
                                control_type="Pane", timeout=2, what="물류대기 화면"),
                f"화면 Pane({SCREEN_PANE_AUTO_ID})",
                timeout=SETTINGS.timeouts.window,
            )
        except WaitTimeout as exc:
            raise ScreenError(
                f"물류대기를 눌렀으나 {SCREEN_PANE_AUTO_ID} 를 찾지 못했다."
            ) from exc

        log.info("진입 확인: %s", ui.describe(pane))
        return pane


def activate_tab(screen, name: str) -> None:
    """좌측 세로탭을 활성화한다. **dry-run 에서도 한다** (데이터를 바꾸지 않는다)."""
    tab = ui.find(screen, auto_id=SIDE_TAB_AUTO_ID, control_type="Tab", what="좌측 세로탭")
    item = ui.find(tab, title=name, control_type="TabItem", what=f"탭 {name!r}")
    ui.click(item, f"탭 {name.replace(chr(10), ' ')!r}",
             methods=("Select", "클릭"))


def click_search(screen) -> None:
    """[조회(F)]. 조회조건은 화면 기본값 그대로 쓴다.

    **dry-run 에서도 누른다.** 조회는 읽기다. 조회하지 않으면 그리드가 비어
    이후 판정을 아무것도 확인할 수 없다.
    """
    button = ui.find(screen, auto_id=SEARCH_BUTTON_AUTO_ID, control_type="Button",
                     what="조회 버튼")
    ui.click(button, "조회(F)")


# ------------------------------------------------------------------ 그리드
def _grid(parent, auto_id: str, what: str):
    return ui.find(parent, auto_id=auto_id, control_type="Table", what=what)


def _wait_rows(grid, what: str, timeout: float | None = None) -> int:
    """행 수가 안정될 때까지 기다린다. 0행이어도 정상일 수 있다."""
    timeout = SETTINGS.timeouts.long_task if timeout is None else timeout
    state = {"count": -1, "same": 0}

    def settled():
        count = ui.visible_row_count(grid)
        if count == state["count"]:
            state["same"] += 1
        else:
            state["count"], state["same"] = count, 0
        return state["same"] >= 2

    try:
        wait_for(settled, f"{what} 안정화", timeout=timeout)
    except WaitTimeout:
        log.info("%s 행 수가 안정되지 않았다. 현재 %d행으로 진행한다.", what, state["count"])
    log.info("%s: 화면에 보이는 %d행", what, state["count"])
    return max(state["count"], 0)


def _bottom_grid(screen):
    return _grid(screen, BOTTOM_GRID_AUTO_ID, "하단 주문정보 그리드")


# 보류 컬럼의 값. 2026-09-04 실제 데이터로 확인했다.
#   '1' = 보류됨 (보류사유 '미분류')
#   '0' = 미보류 (보류사유 빈값)
# ★ "비어 있지 않으면 보류" 로 보면 안 된다. `'0'` 도 비어 있지 않다.
#   2026-09-04 에 그렇게 보다가 미보류 건을 전부 건너뛴 적이 있다.
HELD_VALUES = {"1", "true", "y", "yes"}


def _sort_action(bottom_grid) -> str | None:
    """하단 `ERP상품코드` 헤더의 DefaultAction. 못 읽으면 None."""
    try:
        header = ui.column_header(bottom_grid, BOTTOM_CODE_COLUMN)
        return (header.legacy_properties().get("DefaultAction") or "").strip()
    except Exception as exc:
        log.info("하단 헤더의 정렬 상태를 읽지 못했다: %s: %s", type(exc).__name__, exc)
        return None


def ensure_bottom_sorted(bottom_grid, handle: int = 0) -> bool:
    """하단을 `ERP상품코드` 오름차순으로 둔다. 오름차순이 확인되면 True.

    이미 오름차순이면 누르지 않는다 — 한 번 더 누르면 내림차순이 된다.
    **dry-run 에서도 한다.** 표시 순서만 바뀌고 데이터는 그대로다.

    False 면 부르는 쪽은 예전처럼 **끝까지 훑는다.** 정렬은 속도를 위한 것이지
    정확성의 전제가 아니다. 스캔은 True 여도 읽은 순서가 정말 모였는지 따로 본다
    (`_GroupTracker`).
    """
    action = _sort_action(bottom_grid)
    clicks = SORT_CLICKS_TO_ASC.get(action)
    if clicks is None:
        log.info("하단 정렬 상태를 모른다(%r). 정렬하지 않고 끝까지 훑는다.", action)
        return False
    if clicks == 0:
        return True

    # ★ 누른 뒤 **오름차순이 된 것을 확인한다.** 안 되면 더 누르지 않는다 —
    #   다시 누르면 내림차순이 될 수 있다. 끝까지 훑는 쪽으로 간다.
    try:
        header = ui.column_header(bottom_grid, BOTTOM_CODE_COLUMN)
        ui.click(header, f"하단 헤더 {BOTTOM_CODE_COLUMN!r} (정렬)", methods=("클릭",))
    except Exception as exc:
        log.info("하단 헤더를 누르지 못했다(끝까지 훑는다): %s: %s",
                 type(exc).__name__, exc)
        return False
    if not _wait_sort_action(bottom_grid, SORT_NEXT_DESC, handle):
        log.warning("하단 헤더를 눌렀는데 %.0f초 안에 오름차순이 되지 않았다 "
                    "(누르기 전 %r, 지금 %r). 끝까지 훑는다.",
                    SORT_SETTLE_TIMEOUT, action, _sort_action(bottom_grid))
        return False
    log.info("하단을 %s 오름차순으로 정렬했다 (누르기 전 %r).", BOTTOM_CODE_COLUMN, action)
    return True


def _wait_sort_action(bottom_grid, want: str, handle: int) -> bool:
    """헤더 상태가 `want` 가 되고 ERPia 가 한가해질 때까지. 됐으면 True."""
    state = {"idle": 0}

    def settled() -> bool:
        if _sort_action(bottom_grid) != want:
            state["idle"] = 0
            return False
        if handle and winprobe.ui_roundtrip(handle) >= BUSY_ROUNDTRIP:
            state["idle"] = 0
            return False
        state["idle"] += 1
        return state["idle"] >= BOTTOM_READY_ROUNDS

    try:
        wait_for(settled, f"하단 헤더 상태 {want!r}", timeout=SORT_SETTLE_TIMEOUT,
                 interval=BOTTOM_READY_INTERVAL)
        return True
    except WaitTimeout:
        return False


class _GroupTracker:
    """정렬된 하단에서 **대상 코드 묶음이 끝났는지** 본다.

    행을 **행 번호 순서로** 먹인다. 헤더가 오름차순이라고 해도 믿지 않고,
    읽은 순서가 정말 모여 있는지 스스로 확인한다.

    | 본 것 | 판단 |
    | --- | --- |
    | 대상 코드 묶음 뒤에 다른 코드가 왔다 | `ended` — 더 볼 필요가 없다 |
    | 한 번 끝난 코드가 다시 나왔다 / 행 번호가 건너뛰었다 | `broken` — 끝까지 훑는다 |

    코드 비교는 "다시 나왔는가" 만 본다. 오름·내림 **순서 규칙**은 보지 않는다 —
    ERPia 의 문자열 정렬 규칙이 파이썬과 같다는 근거가 없다.

    ★ 한계: 멈춘 지점보다 **뒤에서** 다시 나오는 행은 볼 수 없다. 그 부분은
      헤더 상태(`ensure_bottom_sorted` 가 오름차순을 확인한 것)를 믿는다.
    """

    def __init__(self, code: str, enabled: bool):
        self.code = code
        self.enabled = enabled
        self.last_number = 0
        self.current: str | None = None
        self.closed: set[str] = set()
        self.broken = False

    def feed(self, cells: dict[int, str], numbers) -> None:
        """이 화면의 행 번호들과 `{행 번호: 코드}`. 이미 본 번호는 건너뛴다."""
        if not self.enabled or self.broken:
            return
        for number in sorted(n for n in numbers if n is not None):
            if number <= self.last_number:
                continue
            if number != self.last_number + 1:
                self._break(f"행 번호가 {self.last_number} 다음에 {number} 로 건너뛰었다")
                return
            self.last_number = number
            value = (cells.get(number) or "").strip()
            if value == self.current:
                continue
            if value in self.closed:
                self._break(f"코드 {value!r} 가 행 {number} 에서 다시 나왔다")
                return
            if self.current is not None:
                self.closed.add(self.current)
            self.current = value

    def _break(self, why: str) -> None:
        self.broken = True
        log.warning("하단이 정렬돼 모여 있다고 봤는데 아니다 (%s). 끝까지 훑는다.", why)

    @property
    def ended(self) -> bool:
        return self.enabled and not self.broken and self.code in self.closed


def verify_holds(bottom_grid, expected: dict[str, int], grouped: bool = False) -> dict:
    """**정말로 보류가 걸렸는지** 지금 하단에 보이는 것을 훑어 확인한다.

    `expected` 는 `{상품코드: 이번에 체크한 행 수}` 다. 실제 보류 수가 그보다
    적으면 **걸리지 않은 행이 있다는 뜻**이므로 경고로 남긴다.

    ## ★ 반드시 **그 상품을 보류한 직후에** 부른다 (2026-09-15 정정)

    예전에는 맨 마지막에 **한 번만** 부르면서 그때까지의 상품코드를 전부 넘겼다.
    "걸 때마다 확인하면 전수 스캔이라 느려진다" 는 이유였는데, 그 전제가 틀렸다.

    **하단은 상단에서 고른 상품으로 걸러진다.** 그래서 마지막에 훑으면 화면에는
    **마지막 상품의 주문만** 있고, 앞의 상품코드는 한 건도 찾지 못한다 —
    보류가 멀쩡히 걸렸는데도 `보류 확인 실패` 가 뜬다. 보류를 2건 이상 걸 때만
    드러나서, 1건짜리 실행에서는 보이지 않았다.

    기대한 수를 채우면 **더 훑지 않고 끝낸다.** 확인이 목적이지 전수 조사가
    목적이 아니다.
    """
    if not expected:
        return {"checked_codes": 0, "ok": 0, "short": {}}

    held, seen = _scan_holds(bottom_grid, expected, grouped)
    if any(held[c] < expected[c] for c in expected):
        # 보류 값이 늦게 그려질 수 있다 (가설, 09-28 검토). 모자랄 때만 한 번 더 본다
        pause(HOLD_SETTLE, "보류 값 갱신을 기다렸다가 한 번 더 확인한다")
        held, seen = _scan_holds(bottom_grid, expected, grouped)

    short = {c: (expected[c], held[c]) for c in expected if held[c] < expected[c]}
    for code, (want, got) in short.items():
        log.warning("보류 확인 실패 — 상품코드 %s: %d행을 체크했는데 보류된 행은 %d개다.",
                    code, want, got)
    if short:
        log.warning("위 상품은 보류가 덜 걸렸다. 물류대기 화면에서 확인할 것.")
    else:
        log.info("보류 확인 — 상품 %d종 전부 체크한 만큼 보류됐다 (훑은 행 %d).",
                 len(expected), len(seen))
    return {"checked_codes": len(expected), "ok": len(expected) - len(short),
            "short": short}


HOLD_SETTLE = 1.5   # 초. 보류 확인이 모자랄 때 다시 훑기 전 쉬는 시간


def _scan_holds(bottom_grid, expected: dict[str, int], grouped: bool):
    """하단을 위에서부터 훑어 `{상품코드: 보류된 행 수}` 와 훑은 행 번호를 돌려준다."""
    scroll_bottom_top(bottom_grid)
    held: dict[str, int] = {code: 0 for code in expected}
    seen: set[int] = set()
    # 정렬돼 있으면 그 코드 묶음이 끝난 곳에서 멈춘다 (상품 하나씩 부를 때만).
    tracker = _GroupTracker(next(iter(expected)), grouped and len(expected) == 1)

    for attempt in range(MAX_SCROLLS):
        if attempt == MAX_SCROLLS - 1:
            # ★ 상한을 다 쓴 것과 "끝까지 봤다"를 구별해 남긴다. 예전에는
            #   둘이 같은 모양으로 끝나서, 다 못 본 것을 정상 종료로 봤다
            #   (2026-09-10 감사).
            log.warning("보류 확인 스캔이 상한 %d페이지를 다 썼다. "
                        "그리드를 끝까지 보지 못했을 수 있다.", MAX_SCROLLS)
        new_rows = 0
        # 두 컬럼을 한 번에 읽는다 (`ui.columns_values` 주석 — 컬럼마다 부르면
        # 그 횟수만큼 하위 트리를 끝까지 훑는다).
        cells = ui.columns_values(bottom_grid, (BOTTOM_CODE_COLUMN, HOLD_COLUMN))
        codes = cells[BOTTOM_CODE_COLUMN]
        holds = cells[HOLD_COLUMN]
        numbers = [ui.row_number(row) for row in ui.grid_rows(bottom_grid)]
        for number in numbers:
            if number is None or number in seen:
                continue
            seen.add(number)
            new_rows += 1
            # 그룹 행에는 `ERP상품코드` 셀이 없어 아래에서 걸러진다.
            # 행마다 또 훑는 `is_group_row` 는 쓰지 않는다 (2026-09-15).
            code = (codes.get(number) or "").strip()
            if code not in held:
                continue
            if (holds.get(number) or "").strip().lower() in HELD_VALUES:
                held[code] += 1
        tracker.feed(codes, numbers)

        if all(held[c] >= expected[c] for c in expected):
            # 기대한 수를 채웠다. 더 훑을 이유가 없다.
            break
        if tracker.ended:
            log.info("보류 확인 — 정렬된 하단에서 %s 묶음이 끝났다. 더 훑지 않는다.",
                     tracker.code)
            break
        if new_rows == 0 and attempt > 0:
            break
        if not ui.scroll_down_verified(bottom_grid):
            break
    return held, seen


def _row_by_number(grid, number):
    """지금 화면에 있는 행 중 그 번호를 가진 것. 없으면 None."""
    for row in ui.grid_rows(grid):
        if ui.row_number(row) == number:
            return row
    return None


def check_rows_by_range(grid, rows, pid: int | None) -> bool:
    """연속된 행들을 **한 번에** 체크한다. 성공 여부를 돌려준다.

    첫 행을 클릭하고 마지막 행을 Shift+클릭해 범위를 잡은 뒤,
    우클릭 메뉴의 `선택영역 체크` 를 누른다.

    **눌렀다고 믿지 않는다.** 누른 뒤 체크 컬럼을 다시 읽어 확인한다.
    확인은 **행 번호**로 한다 — 그리드가 다시 그려지면 들고 있던 행 객체가
    낡아서, 그것으로 읽으면 멀쩡히 체크된 것도 실패로 보인다 (2026-09-10 실측).
    """
    if len(rows) < RANGE_CHECK_MIN:
        return False
    numbers = [ui.row_number(r) for r in rows]
    first, last = rows[0], rows[-1]
    try:
        ui.click(first, "범위 시작 행 선택", methods=("클릭",))
        ui.shift_click(last, "범위 끝 행 선택")
        ui.right_click(last, "선택 범위")
        ui.click_process_menu_item(pid, MENU_RANGE_CHECK)
    except Exception as exc:
        log.info("범위 일괄 체크에 실패했다(하나씩 누르는 방식으로 간다): %s: %s",
                 type(exc).__name__, exc)
        return False

    checks = ui.column_values(grid, ui.CHECK_COLUMN)
    visible = [n for n in numbers if n in checks]
    if not visible:
        log.info("범위 일괄 체크 뒤 확인할 행이 화면에 없다. 하나씩 누른다.")
        return False
    missed = [n for n in visible if (checks.get(n) or "").strip() != ui.CHECK_ON]
    if missed:
        log.info("범위 일괄 체크 뒤에도 체크되지 않은 행이 있다: %s. 하나씩 누른다.",
                 missed[:5])
        return False
    log.info("범위 일괄 체크 — 행 %s~%s (%d행, 화면에서 %d행 확인)",
             numbers[0], numbers[-1], len(rows), len(visible))
    return True


def check_matching_rows(bottom_grid, code: str, dry_run: bool = False,
                        pid: int | None = None, grouped: bool = False,
                        slips: set | None = None, already_slips: set | None = None):
    """하단 그리드에서 ERP상품코드가 `code` 인 행을 전부 체크한다.

    (체크한 수, 마지막으로 체크한 행, 이미 보류된 수) 를 돌려준다.
    `slips`/`already_slips` 를 주면 체크한 행·이미 보류된 행의 **전표번호**를 담는다 — 주문 수는
    행이 아니라 전표로 센다 (09-29 사용자 지적: 한 전표의 상품이 여러 행. 부족 상품이 둘이면
    같은 전표가 두 번 세졌다).

    **화면의 '파란색' 이 이 조건이다.** UIA로 배경색은 읽을 수 없어서
    상단 선택 행과 같은 상품코드인지로 판별한다 (스크린샷 03·04·05 대조).

    **우클릭 전에 같은 코드를 가진 행을 전부 체크한다.** 가상 스크롤이라
    보이는 행만 UIA에 있으므로 스크롤해 가며 **끝까지** 훑는다.

    `grouped` 가 False 면 같은 코드가 흩어져 있을 수 있으므로 일치하는 코드를
    만난 뒤에도 그리드 끝까지 본다 (2026-09-04 확정).
    True 면(하단이 `ERP상품코드` 오름차순, 2026-09-17) **그 코드 묶음이 끝난
    화면에서 멈춘다.** 읽은 순서가 정말 모여 있지 않으면 끝까지 본다.

    이미 보류된 행은 체크하지 않는다.
    """
    checked = already = 0
    last_row = None
    seen_rows: set[int] = set()
    seen_codes: set[str] = set()
    log_once: dict = {}
    tracker = _GroupTracker(code, grouped)
    # 체크하려다 화면에서 밀려난 행. **스캔 내내** 들고 다닌다 — 남아 있으면 묶음이
    # 끝났어도 멈추지 않고, 끝까지 못 찾으면 경고한다 (2026-09-17 검토 반영.
    # 예전에는 화면마다 잊어버려 조용히 누락될 수 있었다).
    pending: set[int] = set()
    scroll_bottom_top(bottom_grid)   # 위쪽 행을 놓치지 않으려면 맨 위에서 시작한다

    for attempt in range(MAX_SCROLLS):
        if attempt == MAX_SCROLLS - 1:
            # 여기서 상한이 닿으면 **체크하지 못한 행이 남은 채** 보류가 걸린다.
            log.warning("하단 스캔이 상한 %d페이지를 다 썼다 (상품코드 %s). "
                        "체크하지 못한 행이 남았을 수 있다.", MAX_SCROLLS, code)
        rows = ui.grid_rows(bottom_grid)
        new_rows = 0
        todo: list = []          # 이 화면에서 체크해야 할 행들 (순서대로)

        # ★ 세 컬럼을 **한 번의 탐색으로** 읽는다 (2026-09-15).
        #   예전에는 컬럼마다 따로 불러 페이지당 하위 트리를 세 번 훑었다.
        #   `find_elements` 는 조건과 무관하게 끝까지 훑으므로 그대로 3배다
        #   (509행 스캔 118초의 큰 몫). 근거는 `ui.columns_values` 주석.
        cells = ui.columns_values(
            bottom_grid, (BOTTOM_CODE_COLUMN, HOLD_COLUMN, ui.CHECK_COLUMN, SLIP_NUMBER_COLUMN))
        codes = cells[BOTTOM_CODE_COLUMN]
        holds = cells[HOLD_COLUMN]
        checks = cells[ui.CHECK_COLUMN]
        slip_of = cells.get(SLIP_NUMBER_COLUMN, {})

        def note_slip(number, into: set | None = None) -> None:
            # 번호를 못 읽은 행은 **그 행 하나를 주문 하나로** 센다 (상품·행 번호로 만든 자리표) —
            # 상품 전체를 행 수로 되돌리면 읽은 전표와 겹치거나 못 읽은 행이 빠졌다 (09-29 검토)
            into = slips if into is None else into
            if into is not None:
                into.add((slip_of.get(number) or "").strip() or f"?{code}:{number}")

        page_numbers: list = []   # 범위 체크 뒤에는 행 객체가 낡으므로 번호를 모아 둔다
        for row in rows:
            number = ui.row_number(row)
            page_numbers.append(number)
            if number is None or number in seen_rows:
                continue
            seen_rows.add(number)
            new_rows += 1
            pending.discard(number)   # 다시 보였다. 아래에서 다시 판정한다

            # ★ 그룹 행 확인(`ui.is_group_row`)을 뺐다 (2026-09-15). 그것은
            #   **행마다 하위 트리를 또 훑는다** — 한 화면이면 17번이다.
            #   여기서는 필요 없다: 그룹 행에는 `ERP상품코드` 셀이 없어서
            #   아래 비교에서 어차피 걸러진다. 훑은 행 수(`new_rows`)는 그대로
            #   세므로 스크롤 종료 판정도 달라지지 않는다.
            value = (codes.get(number) or "").strip()
            seen_codes.add(value)
            if value != code:
                continue

            hold_value = (holds.get(number) or "").strip().lower()
            if log_once is not None and not log_once.get("done"):
                log_once["done"] = True
                log.info("보류 셀 판정 근거 — 값=%r (행 %s)", hold_value, number)
            if hold_value in HELD_VALUES:
                already += 1
                note_slip(number, already_slips)
                continue

            if (checks.get(number) or "").strip() == ui.CHECK_ON:
                # 이미 체크돼 있다. 다시 누르면 해제된다.
                checked += 1
                note_slip(number)
                last_row = row
                continue
            todo.append(row)

        todo_numbers = [ui.row_number(r) for r in todo]
        if todo and not dry_run:
            # ★ 한 화면에 연속으로 몰려 있으면 **범위로 한 번에** 체크한다.
            #   한 행씩 누르면 수백 번의 클릭이 그대로 시간이 된다.
            contiguous = all(b - a == 1
                             for a, b in zip(todo_numbers, todo_numbers[1:]))
            if contiguous and check_rows_by_range(bottom_grid, todo, pid):
                checked += len(todo)
                for number in todo_numbers:
                    note_slip(number)
                last_row = _row_by_number(bottom_grid, todo_numbers[-1]) or todo[-1]
                todo_numbers = []

        if todo_numbers:
            # 범위 체크를 못 썼거나 실패했다. 하나씩 누른다.
            # **행을 다시 찾는다.** 위에서 화면이 다시 그려졌으면 들고 있던
            # 행 객체는 낡아서 셀을 못 찾는다 (2026-09-10 실측으로 겪었다).
            fresh = {ui.row_number(r): r for r in ui.grid_rows(bottom_grid)}
            for number in todo_numbers:
                row = fresh.get(number)
                if row is None:
                    # 화면에서 밀려났다. 다음 스크롤에서 다시 보도록 되돌린다.
                    seen_rows.discard(number)
                    pending.add(number)
                    continue
                ui.check_row(row, checked=True, dry_run=dry_run)
                checked += 1
                note_slip(number)
                last_row = row

        tracker.feed(codes, page_numbers)
        if tracker.ended and not pending:
            log.info("정렬된 하단에서 %s 묶음이 끝났다. 더 훑지 않는다 (행 %d까지).",
                     code, tracker.last_number)
            break
        if new_rows == 0 and attempt > 0:
            break

        # 스크롤이 실제로 먹었는지 **행 번호 변화로** 확인한다.
        # 예외가 없다고 스크롤된 것이 아니다 (2026-09-04에 여기서 6건을 놓쳤다).
        if not ui.scroll_down_verified(bottom_grid):
            break

    if pending:
        # 아래로만 내리므로 위로 밀려난 행은 다시 볼 수 없다. 보류 확인도 이 행을
        # 세지 않으므로(`expected` 에 없다) **여기서 알리지 않으면 아무도 모른다.**
        log.warning("ERP상품코드 %s: 체크하려던 행 %s 이(가) 화면에서 밀려나 "
                    "체크하지 못했다. 물류대기 화면에서 확인할 것.",
                    code, sorted(pending)[:10])
    if checked == 0 and already == 0:
        log.warning("ERP상품코드 %r 인 행을 찾지 못했다. 확인한 코드: %s",
                    code, sorted(seen_codes)[:10] or "(없음)")
    else:
        log.info("ERP상품코드 %s: 체크 %d행, 이미 보류 %d행 (훑은 행 %d)",
                 code, checked, already, len(seen_rows))
    return checked, last_row, already


def hold_delivery(row, pid: int | None, dry_run: bool = False) -> None:
    """체크된 행에서 우클릭 → [배송보류].

    순서가 중요하다 (사용자 확인 2026-09-04).
      상단 부족 행 선택 → 하단에서 같은 상품코드 행의 **체크박스 체크** → 우클릭

    메뉴는 행의 자식으로도, 단순 최상위 Menu 로도 잡히지 않는다.
    프로세스 전체에서 MenuItem 을 직접 찾는다.
    """
    ui.right_click(row, "하단 주문 행", dry_run=dry_run)
    if dry_run:
        log.info("[dry-run] 우클릭 후 [%s] 를 누를 예정", MENU_HOLD)
        return

    try:
        ui.click_process_menu_item(pid, MENU_HOLD)
    except ui.ControlNotFound as exc:
        raise ScreenError(str(exc)) from exc


# ------------------------------------------------------------------ 업무 흐름
def scroll_bottom_top(bottom_grid) -> None:
    """하단 그리드를 맨 위로 올린다.

    직전 상품에서 아래까지 스크롤한 상태가 남아 있으면, 그 위에 있는 행을
    한 번도 보지 못한다 (2026-09-04에 실제로 놓쳤다).
    """
    ui.scroll_to_top(bottom_grid)


def _screen_pid(screen) -> int | None:
    """화면이 속한 프로세스 id. 못 읽으면 None (그러면 유휴 확인을 건너뛴다)."""
    try:
        return screen.element_info.process_id
    except Exception as exc:
        log.debug("화면의 PID 를 읽지 못했다: %s", type(exc).__name__)
        return None


def process_handle(pid: int | None) -> int:
    """그 프로세스의 메인 창 핸들. 못 얻으면 0 (그러면 유휴 확인을 건너뛴다)."""
    if not pid:
        return 0
    return winprobe.main_window_handle(pid)


def wait_bottom_ready(bottom_grid, code: str, handle: int = 0,
                      timeout: float | None = None) -> int:
    """상단에서 고른 상품으로 하단이 **다시 조회되기를** 기다린다. 보이는 행 수를 돌려준다.

    하단은 상단 선택으로 재조회된다 — 고른 상품의 주문 + 그룹상품이면 구성품 행도 뜬다 (09-15 실측).
    행 번호 집합(지문)으로는 판정 못 한다: 재조회마다 1 부터 다시 매겨져 행 수가 같은 상품끼리는 같다.
    표식 행·행 번호 안정만 보면 재조회 **시작 전**을 완료로 봐 누락된다 (같은 화면이 20행/410행,
    docs/archive/TEST_20260904-0910.md). 그래서 원하는 상태 자체를 본다:

    | 상태 | 판정 |
    | --- | --- |
    | 고른 코드가 보임 + 화면 안정 + ERPia 한가 | 완료 |
    | 코드가 안 보이는데 안정·한가가 길게 이어짐 | 그 상품 주문 없음 → 진행 |
    | ERPia 바쁨 | 계속 기다림 (상한 `BOTTOM_READY_TIMEOUT`) |
    """
    timeout = BOTTOM_READY_TIMEOUT if timeout is None else timeout
    state = {"mark": None, "same": 0, "count": 0, "seen": False}
    started = time.monotonic()

    def ready() -> bool:
        codes = ui.column_values(bottom_grid, BOTTOM_CODE_COLUMN)
        state["count"] = len(codes)
        # 행 번호와 값을 함께 본다. 번호만 보면 상품이 바뀌어도 같아 보인다.
        current = tuple(sorted(codes.items()))
        if current == state["mark"]:
            state["same"] += 1
        else:
            state["mark"], state["same"] = current, 0
        if handle and winprobe.ui_roundtrip(handle) >= BUSY_ROUNDTRIP:
            state["same"] = 0        # 아직 밀려 있다. 안정으로 치지 않는다
            return False
        if any((value or "").strip() == code for value in codes.values()):
            state["seen"] = True
            return state["same"] >= BOTTOM_READY_ROUNDS
        # 고른 코드가 안 보인다. 조용한 상태가 **길게** 이어질 때만 없다고 본다.
        return (state["same"] >= BOTTOM_MISSING_ROUNDS
                and time.monotonic() - started >= BOTTOM_MISSING_GRACE)

    try:
        wait_for(ready, f"하단 재조회({code})", timeout=timeout,
                 interval=BOTTOM_READY_INTERVAL)
    except WaitTimeout:
        log.warning("하단이 %.0f초 안에 상품코드 %s 로 정리되지 않았다. "
                    "있는 그대로 훑는다 — 일부를 못 볼 수 있다.", timeout, code)
        return state["count"]
    spent = time.monotonic() - started
    if state["seen"]:
        log.info("하단 재조회 완료 — 상품코드 %s, 보이는 %d행 (%.2f초).",
                 code, state["count"], spent)
    else:
        log.info("하단에 상품코드 %s 인 행이 없다. 화면이 조용해 그대로 진행한다 "
                 "(%.2f초, 보이는 %d행).", code, spent, state["count"])
    return state["count"]


def mark_bottom_row(bottom_grid, why: str) -> bool:
    """[제품 흐름은 안 쓴다 — `wait_bottom_ready` 를 쓴다] 하단 첫 보이는 행에 표식 체크를 남긴다.

    판정에 못 쓰는 이유: 남기는 행(스크롤이 끝난 위치의 첫 보이는 행)과 `wait_bottom_reloaded` 가 읽는
    행(맨 위 1행)이 다르다 → 즉시 통과하거나 상한을 다 쓴다. 이미 체크된 행이면 `ui.check_row` 가 누르지 않고
    True 를 준다. `tools/probe_hold_loop.py` 가 이 결함을 가짜 그리드로 재현하려고 이 함수를 부른다.
    체크만 하고 우클릭은 하지 않으므로 데이터는 바뀌지 않는다.
    """
    for candidate in ui.grid_rows(bottom_grid):
        if ui.is_group_row(candidate):
            continue
        try:
            ok = ui.check_row(candidate, checked=True)
        except ui.ControlNotFound as exc:
            log.info("표식 체크를 남기지 못했다(계속): %s", exc)
            return False
        if not ok:
            log.info("표식 체크가 걸리지 않았다(계속): %s", why)
            return False
        log.info("하단 행 %s 에 표식 체크를 남겼다 (%s). 다음 상단 행을 선택하면 풀려야 한다.",
                 ui.row_number(candidate), why)
        return True

    log.info("하단에 체크할 행이 없어 표식을 남기지 못했다 (%s).", why)
    return False


def wait_bottom_reloaded(bottom_grid, timeout: float | None = None) -> bool:
    """[제품 흐름에서 쓰지 않는다 — 2026-09-15] 표식 체크가 풀리는지로 판정한다.

    ★ **읽는 행이 표식을 남긴 행이 아니다.** 왜 못 쓰는지는 `mark_bottom_row`
      주석과 `tools/probe_hold_loop.py` 에 있다. 제품 흐름은
      `wait_bottom_ready()` 를 쓴다. 조사 도구가 참조하므로 남겨 둔다.

    ---

    원래 판정 기준은 직전에 남긴 **표식 체크가 풀렸는지**였다 (사용자 설계).
    하단 첫 행의 체크 셀이 `선택안됨` 이 되면 재조회가 끝난 것으로 봤다.

    표식이 없거나 상태를 읽지 못하면 기다리지 않고 True 를 돌려준다.
    "모르는 것"을 "안 끝난 것"으로 보면 매번 타임아웃을 버린다.
    """
    timeout = BOTTOM_RELOAD_TIMEOUT if timeout is None else timeout

    def cleared():
        rows = [r for r in ui.grid_rows(bottom_grid) if not ui.is_group_row(r)]
        if not rows:
            return False
        state = ui.check_state(rows[0])
        if state is None:
            return True          # 읽지 못한다. 기다릴 근거가 없다
        return state is False

    try:
        wait_for(cleared, "하단 그리드 재조회(표식 체크 풀림)", timeout=timeout)
        log.info("하단 재조회 확인 — 표식이 풀렸다.")
        return True
    except WaitTimeout:
        log.warning("%.0f초 안에 표식이 풀리지 않았다. 하단이 아직 갱신 중일 수 있다.",
                    timeout)
        return False


def focus_top_row(row, code: str) -> None:
    """상단 행을 선택한다. **그것이 하단을 그 상품코드로 다시 조회시킨다.**

    ## 주의 — 예전 주석이 틀렸다 (2026-09-15 정정)

    여기에는 "하단은 상단 선택으로 걸러지지 않는다. 전체 주문이 그대로 있고
    스크롤만 될 뿐이다" 라고 적혀 있었다. **사실이 아니다.**

    상단 부족 행을 고르면 **그 상품코드로 조회**가 돌아 하단에 그 상품의 주문이
    뜬다. 그 상품이 그룹상품이면 **구성품도 함께** 뜬다 (사용자 확정 2026-09-15,
    같은 날 실측: 내 코드 9행 + 다른 코드 9종 9행).

    틀린 전제 때문에 "기다리지 않는다" 가 됐고, 그래서 **다시 조회 중인 하단을
    훑어** 같은 화면이 20행으로도 410행으로도 보였다. 조용한 누락이다.

    지금은 부르는 쪽에서 `wait_bottom_ready()` 로 재조회 완료를 기다린다.
    여기서 하단을 맨 위로 올리던 것도 **뺐다** — `check_matching_rows()` 가
    시작할 때 어차피 올리고, 재조회 중인 그리드를 스크롤하는 것은 무의미하다.

    상단 선택을 바꾸면 **하단의 체크가 풀린다** (사용자 확인 2026-09-04).
    그래서 상품마다 반복해도 이전 상품의 체크가 섞이지 않는다.
    """
    ui.click(row, f"상단 행 선택({code})", methods=("클릭",))


def _name_column(grid) -> str | None:
    """상단 그리드의 상품명 컬럼 이름. 헤더에 없으면 None."""
    names = {(header.element_info.name or "").strip()
             for header in ui.search(grid, control_type="Header")}
    return next((name for name in TOP_NAME_COLUMNS if name in names), None)


def hold_shortage_items(screen, pid: int | None = None, dry_run: bool = False) -> dict:
    """상품별 재고검토 — 부족 건마다 배송보류를 건다.

    조회는 **탭 전환 시 1회만** 한다. 배송보류를 걸어도 상단 목록은 변하지 않는다
    (사용자 확인 2026-09-04).

    상단 그리드도 가상 스크롤이라 **스크롤해 가며 부족 행을 전부** 처리한다.
    """
    label = "부족 재고 배송보류" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        activate_tab(screen, TAB_STOCK_REVIEW)
        click_search(screen)
        if dry_run:
            # 조회까지는 했다. 체크와 보류만 누르지 않는다.
            log.warning("[dry-run] 조회는 했다. 체크·보류·저장은 누르지 않는다.")

        top_grid = _grid(screen, TOP_GRID_AUTO_ID, "상단 재고 그리드")
        _wait_rows(top_grid, "상단 재고 그리드")
        name_column = _name_column(top_grid)
        if name_column is None:
            log.warning("상단 그리드에서 상품명 컬럼(%s)을 찾지 못했다. "
                        "화면에는 상품코드를 보여 준다.", " / ".join(TOP_NAME_COLUMNS))
        # ★ **맨 위에서 시작한다** (2026-09-15 실측으로 드러난 구멍).
        #   `조회` 는 스크롤 위치를 되돌리지 않는다. 직전 실행이나 직전 행 선택이
        #   그리드를 아래에 두고 끝났으면, 위쪽 행을 **한 번도 보지 못한 채**
        #   "더 내릴 수 없다" 로 끝난다 — 조용한 누락이다.
        #   실측: 같은 화면을 두 번 돌렸는데 24행(부족 13) / 15행(부족 10) 이 나왔고,
        #   두 번째는 상단이 11~24행에 있는 채로 시작한 것이 원인이었다.
        #   하단 스캔은 `scroll_bottom_top()` 으로 이미 이렇게 하고 있었다.
        ui.scroll_to_top(top_grid)

        # 상단은 **정렬하지 않는다** (2026-09-04 확정). 끝까지 훑는다.
        # 하단은 `ERP상품코드` 로 **정렬한다** (2026-09-17 사용자 요청으로 재도입).
        # 09-04 에는 정렬 뒤 배송보류가 제대로 걸리지 않아 뺐는데, 원인 분석은
        # 없었다. 지금은 **체크하기 전에** 정렬하고, 정렬이 확인될 때만
        # 묶음 끝에서 멈춘다 (`ensure_bottom_sorted`, `_GroupTracker`).
        bottom_grid = _bottom_grid(screen)

        processed: set[str] = set()
        held = total_checked = skipped = failed = 0
        # 하단 재조회가 끝났는지 볼 때 ERPia 가 바쁜지 함께 본다 (값싼 Win32 왕복).
        # 못 얻으면 0 이고, 그러면 화면 안정만으로 판정한다 — 막지 않는다.
        handle = process_handle(pid)
        excluded = excluded_codes()
        excluded_hit: list[str] = []
        expected: dict[str, int] = {}   # {상품코드: 이번에 체크한 행 수}
        # 보류 확인 결과를 **상품마다** 모은다. 마지막에 한 번 훑는 방식은
        # 하단이 걸러지기 때문에 쓸 수 없다 (`verify_holds` 주석).
        verify: dict = {"checked_codes": 0, "ok": 0, "short": {}}
        # ★ **상품별 내역.** 숫자만으로는 "어떤 상품이 보류됐는지" 를 알 수 없어
        #   로그를 뒤져야 했다 (사용자 요청 2026-09-15). 화면에 그대로 보여 준다.
        items: list[dict] = []
        if excluded:
            log.info("보류 제외 상품코드 %d개: %s", len(excluded), sorted(excluded))

        seen_top: set[int] = set()
        normal = 0

        for page in range(MAX_SCROLLS):
            if page == MAX_SCROLLS - 1:
                # 부족 상품을 찾는 루프다. 상한이 닿으면 **보류를 걸지 못한
                # 상품이 남은 채** 정상 종료로 보인다.
                log.warning("상단 스캔이 상한 %d페이지를 다 썼다. "
                            "부족 상품을 다 보지 못했을 수 있다.", MAX_SCROLLS)
            fresh = []
            new_rows = 0
            for row in ui.grid_rows(top_grid):
                number = ui.row_number(row)
                if number is None or number in seen_top:
                    continue
                seen_top.add(number)
                new_rows += 1

                if ui.is_group_row(row):
                    continue
                # **부족수량이 비어 있으면 정상이다. 건드리지 않는다.**
                # 값은 한 번만 읽어 아래까지 들고 간다 (셀 읽기도 탐색이다).
                shortage = ui.cell_text(row, SHORTAGE_COLUMN).strip()
                if not shortage:
                    normal += 1
                    continue

                code = ui.cell_text(row, TOP_CODE_COLUMN).strip()
                name = (ui.cell_text(row, name_column, timeout=NAME_READ_TIMEOUT).strip()
                        if name_column and code and code not in processed else "")
                if code and code in excluded:
                    # 설정으로 제외한 상품이다. 부족해도 보류를 걸지 않는다.
                    if code not in excluded_hit:
                        excluded_hit.append(code)
                        log.info("상품코드 %s 는 보류 제외 목록에 있다. 건너뛴다.", code)
                        items.append({"code": code, "name": name, "shortage": shortage,
                                      "state": "제외", "checked": 0, "already": 0})
                    processed.add(code)
                    continue
                if code and code not in processed:
                    fresh.append((row, code, shortage, name))

            if page == 0:
                log.info("첫 화면: 부족 %d개, 정상 %d개", len(fresh), normal)

            for row, code, shortage, name in fresh:
                processed.add(code)
                log.info("[%d번째] 상품코드 %s %s(부족수량 %s)", len(processed), code,
                         f"{name} " if name else "", shortage)

                # ★ 상단 행을 고르면 **하단이 그 상품코드로 다시 조회된다.**
                #   (예전 주석은 "걸러지지 않는다" 였고 틀렸다 — `focus_top_row` 참고)
                focus_top_row(row, code)

                # 재조회가 끝나기 전에 훑으면 **조용히 누락된다.** 실측 0.1초면
                # 끝나지만, 오래 걸리면 끝까지 기다린다 (`wait_bottom_ready`).
                # ★ dry-run 에서도 한다. 읽기만 하는 동작이고, 여기까지 해야
                #   **무엇을 보류하게 되는지**를 미리 확인할 수 있다 (2026-09-15).
                #   예전 dry-run 은 상단을 고르는 데서 끊어서, 하단 스캔이
                #   제대로 도는지 쓰기 없이 확인할 방법이 없었다.
                wait_bottom_ready(bottom_grid, code, handle)
                # 상품마다 확인한다. 이미 오름차순이면 헤더 한 번 읽는 값이다.
                grouped = ensure_bottom_sorted(bottom_grid, handle)

                slips: set = set()
                already_slips: set = set()
                checked, last_row, already = check_matching_rows(
                    bottom_grid, code, pid=pid, dry_run=dry_run, grouped=grouped,
                    slips=slips, already_slips=already_slips)

                # 상품별 내역을 여기서 한 줄 남긴다. 아래 분기가 `state` 를 정한다.
                # `slips` — 체크한 행의 전표번호. 주문 수는 이것을 상품끼리 합쳐 중복 없이 센다
                item = {"code": code, "name": name, "shortage": shortage,
                        "checked": checked, "already": already, "state": "",
                        "slips": sorted(slips), "already_slips": sorted(already_slips)}
                items.append(item)

                if dry_run:
                    item["state"] = "보류 예정" if checked else (
                        "이미 보류" if already else "대상 없음")
                    log.info("[dry-run] 상품코드 %s: %d행을 체크해 보류할 예정 "
                             "(이미 보류 %d행). 누르지 않는다.",
                             code, checked, already)
                    continue
                if already and checked == 0:
                    item["state"] = "이미 보류"
                    log.info("상품코드 %s: %d행이 이미 보류다. 다음 상품으로 넘어간다.",
                             code, already)
                    skipped += 1
                elif last_row is None:
                    item["state"] = "대상 없음"
                    log.warning("상품코드 %s: 체크할 행이 없어 배송보류를 건너뛴다.", code)
                    failed += 1
                else:
                    hold_delivery(last_row, pid)
                    held += 1
                    total_checked += checked
                    expected[code] = expected.get(code, 0) + checked
                    log.info("상품코드 %s: %d행 배송보류", code, checked)

                    # ★ 확인은 **여기서** 한다. 하단은 지금 이 상품으로 걸러져
                    #   있으므로, 나중에 한 번에 확인하면 마지막 상품밖에 못 본다
                    #   (`verify_holds` 주석). 기대 수를 채우면 바로 끝낸다.
                    result = verify_holds(bottom_grid, {code: checked}, grouped=grouped)
                    verify["ok"] += result.get("ok", 0)
                    verify["short"].update(result.get("short", {}))
                    verify["checked_codes"] += 1
                    # 확인까지 통과했는지 한 줄에 담는다. 숫자만 보면 "걸었다" 와
                    # "걸었다고 보고했다" 를 구별할 수 없다.
                    item["state"] = "보류함" if code not in result.get("short", {}) \
                        else "보류 확인 실패"

                # ★ 예전에는 여기서 하단에 **표식 체크**를 하나 남겼다. 없앴다
                #   (2026-09-15). 남기는 행과 읽는 행이 달라 판정이 되지 않았고,
                #   마지막 상품 뒤의 표식은 읽히지도 않았다. 상품당 클릭 한 번을
                #   버리고 가끔 10초를 거짓으로 썼다. 근거는 `mark_bottom_row`
                #   주석과 `tools/probe_hold_loop.py`.

            # 상단 그리드를 아래로 밀어 나머지 행을 본다.
            # **정렬 순서를 믿고 중간에 멈추지 않는다.** 부족수량이 문자열로
            # 정렬되면 순서가 뒤죽박죽이라, 빈 값을 만났다고 끊으면 아래에 남은
            # 부족 행을 놓친다. 끝까지 훑는다.
            # ★ 스크롤을 **시도하는 것 자체가 부작용**이다. 키보드로 내리려면
            #   마지막 행에 `set_focus()` 해야 하고, 그것은 그 행을 **선택**한다.
            #   물류대기에서 상단 선택이 바뀌면 하단이 다시 조회된다.
            #   그래서 새로 본 행이 없으면 아예 시도하지 않는다 (2026-09-10).
            if new_rows == 0 and page > 0:
                break  # 이 화면에서 새로 본 행이 없다. 끝까지 왔다.
            # 상단은 `rounds=1` 이다. 실패한 시도마다 마지막 행이 선택되고,
            # 그러면 하단이 불필요하게 다시 조회된다. 시도를 최소로 줄인다.
            if not ui.scroll_down_verified(top_grid, rounds=1):
                break

        if verify["short"]:
            log.warning("보류 확인 — 기대에 못 미친 상품 %d건: %s",
                        len(verify["short"]), verify["short"])

        log.info("배송보류 완료 — 훑은 행 %d개 (부족 %d개, 정상 %d개), "
                 "%d개 처리, 총 %d행 (이미 보류 %d개, 실패 %d개, 제외 %d개)",
                 len(seen_top), len(processed), normal,
                 held, total_checked, skipped, failed, len(excluded_hit))
        return {"shortage": len(processed), "held": held,
                "checked": total_checked, "skipped": skipped,
                "excluded": excluded_hit, "verify": verify,
                # 상품별 내역. 화면이 "어떤 상품이 보류됐는지" 를 이것으로 그린다.
                "items": items}


def _general_grid(screen):
    """일반 탭의 그리드."""
    try:
        return ui.find(screen, auto_id=GENERAL_GRID_AUTO_ID, control_type="Table",
                       timeout=5, what="일반 탭 그리드")
    except ui.ControlNotFound:
        log.info("확정 auto_id(%s)로 못 찾았다. 남은 Table 중에서 찾아본다.",
                 GENERAL_GRID_AUTO_ID)

    # 확정값으로 못 찾으면 화면이 바뀐 것이다. 남은 Table 중에서 찾고 로그에 남긴다.
    known = {TOP_GRID_AUTO_ID, BOTTOM_GRID_AUTO_ID}
    for table in ui.find_all(screen, control_type="Table"):
        try:
            auto_id = table.element_info.automation_id or ""
        except Exception:
            continue
        if auto_id and auto_id not in known:
            log.warning("일반 탭 그리드를 %r 로 찾았다. docs/CONTROLS.md 를 갱신할 것.",
                        auto_id)
            return table

    raise ScreenError(
        f"일반 탭의 그리드({GENERAL_GRID_AUTO_ID})를 찾지 못했다.\n"
        "  tools/probe_screen.py 를 [일반] 탭이 열린 상태에서 실행해 확인할 것."
    )


def count_general_slips(grid) -> tuple[int | None, bool]:
    """[일반] 탭의 **전표(주문) 수**. `(수, 정확한가)`.

    행은 상품이라 한 전표가 여러 행이다 (09-29 실측: 8행 = 전표 2). `전표번호` 는 오른쪽 밖이라
    가로로 넘겨 읽고, 끝까지 내려 중복 없이 센다. 읽은 뒤 가로 스크롤을 맨 앞으로 돌린다 —
    전체선택 헤더(맨 앞 컬럼)가 보여야 한다.
    """
    try:
        if not ui.reveal_column(grid, SLIP_NUMBER_COLUMN):
            log.warning("[일반] 탭에서 %s 컬럼을 찾지 못했다. 주문 수를 모른다.", SLIP_NUMBER_COLUMN)
            return None, False
        return ui.distinct_count(grid, SLIP_NUMBER_COLUMN)
    finally:
        ui.columns_home(grid)


def save_general(screen, dry_run: bool = False, info: dict | None = None) -> int:
    """일반 탭 — 조회 → 전체선택 → 저장.

    저장 확인 팝업은 없다 (사용자 확인 2026-09-04).

    **완료 판정은 그리드 행 수 변화로 한다.** 체크 상태는 읽을 수 없다.
    이 그리드의 체크 셀은 값이 항상 `'선택'`(컬럼 이름)이라 상태를 알 수 없다
    (2026-09-04 실측). 저장되면 처리된 건이 물류대기에서 빠져 행 수가 준다.

    **건수는 행이 아니라 전표로 센다** (09-29 사용자 지적 — 8행 = 전표 2). 저장 전후
    `count_general_slips` 의 차이다. `info["exact"]` 가 True 면 돌려준 수가 **넘긴 전표 수**이고,
    False 면(전표번호를 못 읽음·끝까지 못 봄) 저장됐는지만 뜻하는 **행 차이**다 — 화면은 숫자를 뺀다.
    """
    info = {} if info is None else info
    info["exact"] = False
    label = "물류대기 저장" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        activate_tab(screen, TAB_GENERAL)
        click_search(screen)

        grid = _general_grid(screen)
        before = _wait_rows(grid, "일반 탭 그리드")
        info["before"] = before         # 0 이면 저장할 것이 없었다 — 사용량에서 '할 일 없음' 판정에 쓴다
        if dry_run:
            log.warning("[dry-run] 조회는 했다(%d행). 전체선택·저장은 하지 않는다.", before)
            return 0
        if before == 0:
            log.info("저장할 행이 없다.")
            info["exact"] = True
            return 0

        slips_before, exact_before = count_general_slips(grid)
        info["slips_before"] = slips_before
        log.info("저장 전 [일반] %d행 = 전표 %s건%s", before, slips_before,
                 "" if exact_before else " (끝까지 못 셌다)")
        ui.header_select_all(grid)
        log.info("전체선택 완료. 저장한다. (처리 전 %d행)", before)

        save = ui.find(screen, auto_id=SAVE_BUTTON_AUTO_ID, control_type="Button",
                       what="저장 버튼")
        # **마우스 클릭만 쓴다.** Invoke 는 버튼 핸들러가 모달 팝업을 띄우면
        # 반환되지 않고 63초 뒤 타임아웃한다 (2026-09-04 실행에서 재현).
        ui.click(save, "저장(S)", methods=("클릭",))

        # 저장되면 처리된 건이 목록에서 빠진다 — 행 수가 줄면 완료. 전부 보류라 정상 건이 없으면
        # 행 수가 그대로다 (09-29 실기: 8행 그대로·한가 3.1초로 판정). `logistics.prepare()` 와 같은 패턴:
        #
        #   | 상태 | 판정 |
        #   | --- | --- |
        #   | 행 수가 바뀌었다 | 즉시 완료 |
        #   | 그대로 + ERPia **한가** | 유예 뒤 '변화 없음' 으로 확정 |
        #   | 그대로 + ERPia **바쁨** | 계속 기다린다 (상한까지) — 안전장치 |
        #
        #   한계: 저장이 **백그라운드 스레드**에서 돌면 UI 는 한가해 보인다.
        #   그때는 실제로 저장됐는데 0행으로 보고할 수 있다 — 예전 타임아웃
        #   경로와 **결과가 같고**, 다만 120초가 아니라 몇 초로 끝난다.
        handle = process_handle(_screen_pid(screen))
        state = {"rows": before, "idle": 0}
        started = time.monotonic()

        def settled() -> bool:
            state["rows"] = ui.visible_row_count(grid)
            if state["rows"] != before:
                return True
            if handle and winprobe.ui_roundtrip(handle) >= BUSY_ROUNDTRIP:
                state["idle"] = 0       # 아직 저장 중이다. 유예를 다시 센다
            else:
                state["idle"] += 1
            return (state["idle"] >= SAVE_IDLE_ROUNDS
                    and time.monotonic() - started >= SAVE_IDLE_GRACE)

        try:
            wait_for(settled, "저장 후 목록 갱신",
                     timeout=SETTINGS.timeouts.long_task)
        except WaitTimeout:
            log.warning(
                "저장 후 %.0f초 동안 행 수가 %d행 그대로다. "
                "저장이 안 됐거나 처리할 정상 건이 없었을 수 있다.",
                SETTINGS.timeouts.long_task, before,
            )
            return 0
        if state["rows"] == before:
            log.warning("저장 뒤에도 %d행 그대로고 ERPia 는 한가하다 (%.1f초). "
                        "처리할 정상 건이 없었을 수 있다(전부 보류 상태).",
                        before, time.monotonic() - started)
            info["exact"] = True        # 아무것도 빠지지 않았다 — 넘긴 전표 0 이 정확한 값이다
            return 0

        after = _wait_rows(grid, "일반 탭 그리드")
        if after >= before:
            # 화면에 보이는 행 수는 가상 스크롤 때문에 총 건수가 아니다.
            # 처리할 정상 건이 없었을 수도 있어 실패로 보지는 않는다.
            log.warning("저장했으나 목록이 %d행 그대로다. "
                        "처리할 정상 건이 없었을 수 있다(전부 보류 상태).", after)
            info["exact"] = True
            return 0
        slips_after, exact_after = count_general_slips(grid) if after else (0, True)
        if (slips_before is not None and slips_after is not None and exact_before and exact_after
                and slips_before >= slips_after):
            info["exact"] = True
            log.info("저장 완료 — 전표 %d건 → %d건 (%d행 → %d행)",
                     slips_before, slips_after, before, after)
            return slips_before - slips_after
        log.info("저장 완료 — %d행 → %d행 (전표 수는 셀 수 없음: 전 %s / 후 %s)",
                 before, after, slips_before, slips_after)
        return before - after


def go_to_logistics(target) -> None:
    """좌측 프로세스바 [물류처리] → 물류관리 화면 (Phase 9).

    **dry-run 에서도 이동한다.** 화면 이동은 데이터를 바꾸지 않는다.
    """
    with step(log, "물류처리 화면 이동"):
        main_window = target.main_window()
        entry = ui.find(main_window, auto_id=SIDEBAR_LOGISTICS,
                        what="프로세스바 '물류처리'")
        ui.click(entry, "좌측 프로세스바 '물류처리'")


def run(target, dry_run: bool = False) -> dict:
    """물류대기 전체 흐름.

    물류대기가 **없는 계정**이면 아무것도 하지 않고 `no_menu` 에 이유를 담아
    돌려준다. 물류관리로의 이동은 `logistics.run()` 이 스스로 한다
    (`logistics.open_screen` 이 [물류처리] 를 누른다).

    ★ 결과의 `skipped` 는 **이미 보류라 건너뛴 상품 수**다. 단계를 건너뛴 표시는
      `no_menu` 로 따로 둔다.
    """
    if not menu_available(target):
        log.warning(NO_MENU_REASON)
        return {"shortage": 0, "held": 0, "checked": 0, "skipped": 0,
                "excluded": [], "verify": {}, "items": [], "saved": 0,
                "no_menu": NO_MENU_REASON}
    screen = open_screen(target)
    result = hold_shortage_items(screen, pid=target.pid, dry_run=dry_run)
    saved_info: dict = {}
    result["saved"] = save_general(screen, dry_run=dry_run, info=saved_info)
    # 화면 밖 행이 있었으면 `saved` 는 건수가 아니다. 화면은 이것을 보고 숫자를 뺀다.
    result["saved_exact"] = saved_info.get("exact", False)
    result["general_rows"] = saved_info.get("before")   # 저장 전 일반 탭 행 수 (조회 못 했으면 None)
    # 저장해도 줄지 않았으면 저장 실패 알림이 떠 있을 수 있다. [확인] 하나면 닫고 넘어간다
    # (09-22). 떠 있는 채로 두면 [물류처리] 이동이 막힌다.
    result["save_message"] = ("" if dry_run or result["saved"] else
                              dialogs.dismiss_message_box(target.main_window()) or "")
    go_to_logistics(target)
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description="물류대기 관리 자동화")
    parser.add_argument("--step", default="all",
                        choices=["all", "enter", "hold", "save", "logistics"])
    parser.add_argument("--pid", type=int, default=None)
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

    if args.step == "logistics":
        go_to_logistics(target)
        return 0

    screen = open_screen(target)

    if args.step == "enter":
        return 0
    if args.step in ("all", "hold"):
        hold_shortage_items(screen, pid=target.pid, dry_run=args.dry_run)
    if args.step in ("all", "save"):
        save_general(screen, dry_run=args.dry_run)
    if args.step == "all":
        go_to_logistics(target)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
