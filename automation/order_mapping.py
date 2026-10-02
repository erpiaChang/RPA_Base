r"""주문매핑 매출처리 화면 (Phase 5~7).

근거: docs/CONTROLS.md "주문매핑 매출처리 화면" (2026-09-03 덤프 확정)

화면 진입 / 주문수집(자동·엑셀) / 매출처리 / 수집로그 읽기가 다 여기 있다 (09-04 완료).

    .venv\Scripts\python.exe -m automation.order_mapping --step enter --dry-run
    .venv\Scripts\python.exe -m automation.order_mapping --step enter
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation import sidebar  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from utils import dialogs, filedialog, ui, winprobe  # noqa: E402
from utils.logger import get_logger, step  # noqa: E402
from utils.wait import (  # noqa: E402
    WaitTimeout,
    stays_true,
    wait_for,
    wait_until_gone,
)

log = get_logger(__name__)

# --- 확정된 식별자 (docs/CONTROLS.md) ---
SCREEN_PANE_AUTO_ID = "Frm_Management_SiteOrderMapping"  # 화면 고유 Pane
SCREEN_TAB_TITLE = "주문매핑 매출처리"                      # 탭 이름
MENU_SEARCH_AUTO_ID = "sch_Menus"                        # 좌측 메뉴 검색창
MENU_SEARCH_BUTTON = "Search"

MENU_KEYWORD = "주문매핑"     # 메뉴 검색어 (폴백 경로)

# 좌측 세로 프로세스바의 '주문수집' 아이콘 = 정식 진입 경로 (`automation/sidebar.py`)
SIDEBAR_ENTRY_AUTO_ID = sidebar.ORDER_COLLECT
SIDEBAR_ENTRY = "주문수집"           # auto_id로 못 찾을 때 쓰는 텍스트 폴백
# 화면 내부 컨트롤 (docs/CONTROLS.md, 2026-09-03 덤프)
SITE_GRID_AUTO_ID = "gridCtrl_SiteList"    # 사이트 목록 그리드
ORDER_GRID_AUTO_ID = "gridCtrl_Order"      # 하단 주문 그리드
COLLECT_LOG_GRID_AUTO_ID = "gridCtrl_CollectLog"
MAX_COLLECT_LOG_PAGES = 20       # 수집로그를 끝까지 내릴 때의 상한 (`collect_counts_all`)
GET_BUTTON_AUTO_ID = "btn_Get"             # 가져오기

# 사이트 그리드 컬럼 이름 (셀 title = "<컬럼명> 행 N")
SITE_CODE_COLUMN = "사이트코드"
# 수집 RPA 가 넘겨주는 매니페스트는 사이트코드가 아니라 **사이트명**을 준다.
SITE_NAME_COLUMN = "사이트명"
# 업로드 버튼을 눌렀는데 파일 선택 창이 뜨지 않았을 때의 안내.
# ★ 이것은 **우리가 쓰는 문구**이고 ERPia 가 낸 메시지가 아니다. 예전 문구는
#   "사이트 수집 엑셀세팅이 되어있지 않습니다." 하나만 단정했는데, 2026-09-10
#   통합 실행에서 **같은 사이트·같은 파일이 22초 뒤 재시도로 성공**했다.
#   즉 세팅 문제가 아닌 경우도 있다. 확인하지 않은 원인을 단정하지 않는다.
EXCEL_SETTING_MISSING = (
    "엑셀업로드를 눌렀으나 파일 선택 창이 뜨지 않았다. 확인할 것: "
    "(1) 그 사이트에 수집 엑셀세팅이 되어 있는가 "
    "(2) 화면을 막 열어 아직 준비되지 않았는가 — 재시도로 성공하는 경우가 있다"
)
# 암호가 걸린 엑셀을 올리면 비밀번호를 묻는 창이 뜬다.
# 제목이 확정되지 않아 넓게 잡고, 본문·컨트롤을 로그에 남겨 확인한다.
PASSWORD_DIALOG_TITLE_RE = "비밀번호.*|암호.*|Password.*"
PASSWORD_PROMPT_TIMEOUT = 8.0
# 눌렀는지 확인하는 짧은 대기.
# ★ **첫 번째** 방법에 정식 dialog timeout 을 주고, 폴백에만 이 짧은 값을
#   쓴다. 이미 눌린 것을 '느리다' 고 판단해 또 누르지 않기 위해서다.
#   (2026-09-10 정정 — 예전 주석은 순서를 반대로 적었다)
CLICK_VERIFY_TIMEOUT = 4.0
# 엑셀 업로드 후 오류 팝업이 뜨는지 보는 대기.
UPLOAD_ERROR_TIMEOUT = 5.0
# 업로드 오류 팝업 제목. 실제 제목이 확인되면 좁힐 것.
UPLOAD_DIALOG_TITLE_RE = "ERPia|경고|알림|오류|에러|실패|Warning|Error"
UPLOAD_COLUMN = "엑셀업로드"

# 업로드 1건을 몇 번까지 시도할지. 무제한 재시도 금지.
# 첫 시도가 실패해도 **한 번 더** 올려 본다 (사용자 확정 2026-09-09).
# 2026-09-09 실측: 자동수집이 도는 중에 누른 첫 건이 파일 창을 못 띄우고 실패했고,
# 같은 파일을 나중에 단독으로 올렸을 때는 문제가 없었다. 일시적인 실패가 있다.
# 한 파일을 몇 번까지 올려 볼 것인가. **1회 재시도**다 (사용자 확정 2026-09-10:
# "엑셀 파일에 문제가 있는 경우이니 1번 정도만 재시도하고 다음 단계로 넘어가").
UPLOAD_ATTEMPTS = 2

# 업로드 실패의 종류. 사람이 확인할 것이 다르므로 구분해서 기록한다.
KIND_NOT_STARTED = "not_started"   # 파일 창이 안 떠서 올리지도 못했다
KIND_REJECTED = "rejected"         # 올렸는데 프로그램이 거부했다
KIND_LABELS = {KIND_NOT_STARTED: "업로드 실패", KIND_REJECTED: "등록 실패"}

# 엑셀수집이 다루는 확장자. 그 밖의 파일은 폴더에 있어도 무시한다.
EXCEL_SUFFIXES = (".xlsx", ".xls", ".xlsm")
# 파일명에서 사이트명을 끊는 구분자. `사이트A_20260908.xlsx` → `사이트A`
SITE_NAME_SEPARATOR = "_"

# --- 매출처리 (Phase 7) ---
SALES_BUTTON_AUTO_ID = "btn_DeleteSlip"    # title="매출처리". auto_id가 title과 다르다
SALES_BUTTON_TITLE = "매출처리"
UNSOLD_COUNT_AUTO_ID = "lbl_noSlcountno"   # "미매출 주문수 : n건" — 조회와 무관하게 ERPia 의 미매출 전체
# "조회된 주문수 : n건" — **그리드에 조회된 주문 수** (09-29 실측). 행 수는 주문 수가 아니다 — 마켓별 그룹 행이
# 섞인다 (3행 = 그룹 1 + 주문 2 → 2건, 사용자 지적). 좌측 숫자(행 번호)는 UIA 에 없어 이 라벨을 쓴다
SELECTED_COUNT_AUTO_ID = "lbl_selectcountno"
SALES_DIALOG_TITLE_RE = "^ERPia$"          # 매출처리 확인 팝업 (사용자 확인)
# 매출처리 방식은 설정 `sales_mode` 로 고른다 (10-01 사용자 요청).
#   전체     [매출처리] 단추 → '일괄' 확인 [예]. 조회되지 않은 미매출 건까지 일괄 처리한다
#   선택주문 조회된 주문 전부 체크 → 우클릭 → [선택주문 매출처리] (`select_all_orders`·`open_selected_sales`)
SALES_ALL, SALES_SELECTED = "전체", "선택주문"
SALES_MODES = (SALES_ALL, SALES_SELECTED)
SELECTED_SALES_MENU = "선택주문 매출처리"
CONTEXT_COLUMN = "사이트코드"               # 우클릭할 셀. 체크 칸을 누르면 체크가 뒤집힐 수 있다

# --- 물류대기 (Phase 8 진입) ---
LOGISTICS_WAIT_AUTO_ID = sidebar.LOGISTICS_WAIT
LOGISTICS_WAIT_NAME = "물류대기"

MAX_DIALOGS = 5  # 팝업 처리 상한. 무한 반복하지 않는다.
# 결과 알림 팝업. 이게 뜨면 매출처리가 끝난 것이라 더 기다리지 않는다.
SALES_DONE_RE = re.compile(r"성공|완료|처리되었습니다|처리 되었습니다")
# 다음 팝업을 기다리는 시간. 첫 팝업은 오래 걸리지만 그 뒤는 곧바로 뜬다.
NEXT_DIALOG_TIMEOUT = 3.0
STATUS_COLUMN = "상태"              # 수집로그 그리드
MARKET_COLUMN = "마켓명"            # 수집로그 그리드 — 실패한 사이트를 이름으로 알린다
COLLECTED_COLUMN = "수집여부"        # 수집로그 그리드의 체크박스 컬럼
PROGRESS_LOG_INTERVAL = 60.0        # 수집 대기 중 진행 상황 로그 간격(초)
# 수집로그 상태 중 **사람이 볼 필요가 있는 것**. 이름이 확정되지 않아 넓게 잡는다.
#
# ★ 이름이 `실패` 라고 **결함이라는 뜻이 아니다.** 아래 "수집 상태의 실제 의미" 참고.
COLLECT_FAIL_RE = re.compile(r"실패|오류|에러|Error|Fail")


class UploadNotStarted(RuntimeError):
    """엑셀을 **올리지도 못했다.** 업로드 버튼을 눌렀으나 파일 선택 창이 뜨지 않았다.

    "파일은 올라갔는데 프로그램이 거부한 것"과 구분한다. 사용자에게 알릴 때
    무엇을 확인해야 하는지가 다르기 때문이다.
    """


class NoUploadButton(UploadNotStarted):
    """그 사이트 행에 **[엑셀업로드] 버튼이 없다.** 누르지 않았다.

    다시 해도 같으므로 **재시도하지 않는다.** 사람이 ERPia 에서 그 사이트의
    수집 엑셀 설정을 해야 한다.
    """


# [엑셀업로드] 셀의 값 → 버튼(XL 아이콘)이 있는가 (2026-09-17 실측).
# 아이콘은 그림이라 자식 컨트롤이 없지만, **셀 값이 `1`/`0` 으로 나온다.**
# comp01 사이트 51곳에서 "값 1 = 눌렀을 때 파일 창이 뜸" 이 51곳 모두 맞았다.
# 이 표에 없는 값은 **모른다** — 예전처럼 눌러 보고 파일 창으로 판정한다.
UPLOAD_BUTTON_VALUES = {"1": True, "0": False}


def upload_button_present(cell) -> "bool | None":
    """[엑셀업로드] 버튼이 있으면 True, 없으면 False, 알 수 없으면 None."""
    try:
        value = ui.cell_value(cell).strip()
    except Exception as exc:
        log.info("엑셀업로드 셀 값을 읽지 못했다 (%s). 눌러 보고 판정한다.",
                 type(exc).__name__)
        return None
    present = UPLOAD_BUTTON_VALUES.get(value)
    if present is None:
        log.info("엑셀업로드 셀 값 %r 은 모르는 값이다. 눌러 보고 판정한다.", value)
    return present


class ScreenError(RuntimeError):
    """화면 진입 실패."""


# 한 파일의 실패로 처리할 예외들. **이 목록 밖은 코드 결함**이므로 그대로 올린다.
UPLOAD_FAILURES = (
    ScreenError,                  # 이 모듈의 화면 처리 실패
    ValueError,                   # 인자가 잘못됐다 (사이트명/경로 없음 등)
    filedialog.FileDialogError,   # 파일 선택 창을 못 다뤘다 (경로 거부 포함)
    ui.ControlNotFound,
    ui.ControlDisabled,
    WaitTimeout,
)


def is_open(main_window) -> bool:
    """주문매핑 화면이 이미 열려 있는지. 화면 고유 Pane의 존재로 판정한다."""
    # 화면 Pane 은 5층에 있다. 없을 때 12층까지 훑지 않는다(docs/RESOURCES.md 4번).
    return ui.exists(main_window, auto_id=SCREEN_PANE_AUTO_ID, control_type="Pane",
                     max_depth=ui.SCREEN_PANE_DEPTH)


def _activate_tab(main_window, dry_run: bool) -> bool:
    """이미 열려 있는 탭을 활성화한다. 탭이 없으면 False."""
    if not ui.exists(main_window, title=SCREEN_TAB_TITLE, control_type="TabItem"):
        return False
    tab = ui.find(
        main_window,
        title=SCREEN_TAB_TITLE,
        control_type="TabItem",
        what=f"탭 {SCREEN_TAB_TITLE!r}",
    )
    ui.click(tab, f"탭 {SCREEN_TAB_TITLE!r} 활성화", dry_run=dry_run)
    return True


def _find_sidebar_entry(main_window):
    """좌측 프로세스바의 '주문수집' 아이콘. 못 찾으면 None.

    1순위 `auto_id`, 안 되면 보이는 텍스트로 폴백한다.
    화면이 이미 열려 있으면 화면 안에도 '주문수집' TabItem이 있으므로 제외한다.
    """
    try:
        entry = ui.find(
            main_window,
            auto_id=SIDEBAR_ENTRY_AUTO_ID,
            timeout=SETTINGS.timeouts.control,
            what=f"프로세스바 {SIDEBAR_ENTRY!r}",
        )
        log.info("auto_id로 찾음: %s", ui.describe(entry))
        return entry
    except ui.ControlNotFound:
        log.warning("auto_id=%r 로 찾지 못했다. 텍스트로 재시도한다.", SIDEBAR_ENTRY_AUTO_ID)

    for item in ui.find_all(main_window, title_re=rf".*{SIDEBAR_ENTRY}.*"):
        try:
            if item.element_info.control_type == "TabItem":
                continue  # 화면 내부의 주문수집 탭은 진입 경로가 아니다
            if not item.is_visible():
                continue
        except Exception:
            continue
        log.info("텍스트로 찾음: %s", ui.describe(item))
        return item
    return None


def _open_from_sidebar(main_window, dry_run: bool) -> bool:
    """좌측 프로세스바의 '주문수집'을 눌러 화면을 연다. 못 찾으면 False."""
    log.info("좌측 프로세스바에서 %r 를 찾는다.", SIDEBAR_ENTRY)
    entry = _find_sidebar_entry(main_window)
    if entry is None:
        log.warning(
            "%r 를 찾지 못했다. 확인: .venv\\Scripts\\python.exe -m tools.dump_controls --pid <PID> --name 메인",
            SIDEBAR_ENTRY,
        )
        return False

    ui.click(entry, f"좌측 프로세스바 {SIDEBAR_ENTRY!r}", dry_run=dry_run)
    return True


def _open_from_menu(main_window, dry_run: bool) -> None:
    """좌측 메뉴 검색창으로 화면을 연다.

    메뉴 트리(`ElementTree`)의 대분류 14개는 덤프에 있지만 하위 항목은
    접혀 있어 확인되지 않았다. 계층에 의존하지 않는 검색창을 쓴다.
    검색 결과 목록의 식별자는 미확인이라 **보이는 텍스트로** 찾는다.
    """
    search = ui.find(
        main_window,
        auto_id=MENU_SEARCH_AUTO_ID,
        control_type="Edit",
        what="메뉴 검색창",
    )
    ui.set_text(search, MENU_KEYWORD, "메뉴 검색창", dry_run=dry_run)

    if dry_run:
        log.info("[dry-run] 검색 결과에서 %r 를 포함한 항목을 클릭할 예정", SCREEN_TAB_TITLE)
        return

    try:
        button = ui.find(
            main_window, title=MENU_SEARCH_BUTTON, control_type="Button",
            what="메뉴 검색 버튼", timeout=3,
        )
        ui.click(button, "메뉴 검색 실행")
    except ui.ControlNotFound:
        log.info("검색 버튼을 찾지 못했다. Enter로 검색한다.")
        search.type_keys("{ENTER}", set_foreground=False)

    candidate = _pick_menu_result(main_window)
    ui.click(candidate, f"메뉴 항목 {candidate.window_text()!r}")


def _pick_menu_result(main_window):
    """검색 결과 중 화면 이름을 포함한 항목을 고른다.

    결과 목록의 구조가 확인되지 않았으므로 후보를 로그에 남긴다.
    실패 시 무엇이 보였는지 알 수 있어야 한다.
    """
    def probe():
        seen = []
        for control_type in ("ListItem", "TreeItem", "MenuItem", "Text"):
            for item in ui.find_all(main_window, control_type=control_type):
                try:
                    text = (item.window_text() or "").strip()
                except Exception:
                    continue
                if not text:
                    continue
                seen.append(f"{control_type}:{text}")
                if MENU_KEYWORD in text:
                    return item
        probe.seen = seen  # 실패 시 보고용
        return None

    probe.seen = []
    try:
        return wait_for(probe, f"메뉴 검색 결과({MENU_KEYWORD})", timeout=SETTINGS.timeouts.control)
    except WaitTimeout as exc:
        preview = ", ".join(probe.seen[:25]) or "(후보 없음)"
        raise ScreenError(
            f"메뉴 검색 결과에서 {MENU_KEYWORD!r} 항목을 찾지 못했다.\n"
            f"  검색창에 입력은 됐다. 화면에 보인 항목: {preview}\n"
            "  tools/dump_controls.py 로 검색 결과 목록 구조를 확인할 것."
        ) from exc


def enter_order_mapping(target, dry_run: bool = False):
    """주문매핑 매출처리 화면으로 진입한다. 성공 시 화면 Pane을 돌려준다.

    1. 이미 열려 있으면 탭만 활성화
    2. 아니면 메뉴 검색창으로 연다
    3. 화면 고유 Pane 등장으로 진입을 확인한다
    """
    label = "주문매핑 매출처리 화면 진입" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        main_window = target.normalize()

        if is_open(main_window):
            log.info("이미 열려 있다. 탭을 활성화한다.")
            _activate_tab(main_window, dry_run)
        else:
            log.info("열려 있지 않다. 화면을 연다.")
            #  1) 탭이 남아 있으면 탭
            #  2) 좌측 프로세스바 '주문수집' (사용자가 알려준 정식 경로)
            #  3) 메뉴 검색창 (폴백)
            if not _activate_tab(main_window, dry_run):
                if not _open_from_sidebar(main_window, dry_run):
                    log.info("프로세스바 경로 실패. 메뉴 검색으로 시도한다.")
                    _open_from_menu(main_window, dry_run)

        if dry_run:
            log.warning(
                "[dry-run] 클릭하지 않았으므로 진입 여부를 검증하지 않았다. "
                "성공을 의미하지 않는다."
            )
            # 화면이 **이미 열려 있으면** Pane을 그대로 돌려준다. 읽기만 하는
            # 동작이라 dry-run에서도 안전하고, 이걸 None으로 두면 이후 단계를
            # 통째로 건너뛰어 dry-run이 아무것도 확인하지 못한다.
            try:
                pane = main_window.child_window(
                    auto_id=SCREEN_PANE_AUTO_ID, control_type="Pane"
                ).wrapper_object()
            except Exception:
                log.warning("[dry-run] 화면이 아직 열려 있지 않아 Pane을 얻지 못했다.")
                return None
            log.info("[dry-run] 이미 열려 있는 화면 Pane을 그대로 쓴다: %s",
                     ui.describe(pane))
            return pane

        try:
            pane = wait_for(
                lambda: main_window.child_window(
                    auto_id=SCREEN_PANE_AUTO_ID, control_type="Pane"
                ).wrapper_object(),
                f"화면 Pane({SCREEN_PANE_AUTO_ID})",
                timeout=SETTINGS.timeouts.window,
            )
        except WaitTimeout as exc:
            raise ScreenError(
                f"화면을 열었으나 {SCREEN_PANE_AUTO_ID} 를 찾지 못했다.\n"
                "메뉴 항목이 다른 화면을 열었을 수 있다."
            ) from exc

        log.info("진입 확인: %s", ui.describe(pane))
        return pane


def find_site_row(site_grid, value: str, column: str = SITE_CODE_COLUMN,
                  max_scrolls: int = 20):
    """사이트 그리드에서 `column` 값이 `value` 와 **완전히 같은** 행을 찾는다.

    기본은 `사이트코드`. 매니페스트로 받은 값은 `사이트명` 이므로
    `column=SITE_NAME_COLUMN` 으로 부른다. 부분 일치는 하지 않는다 —
    비슷한 이름의 다른 사이트에 업로드되면 되돌릴 수 없다.

    가상 스크롤이라 화면에 보이는 행만 UIA에 있다. 목록이 길어 화면 밖에 있으면
    아래로 스크롤해 가며 다시 본다.

    셀 값은 `ui.cell_text()` 로 읽는다. `window_text()` 를 쓰면 값이 아니라
    '사이트코드 행 2' 같은 식별자가 나온다 (2026-09-04에 이것 때문에 못 찾았다).
    """
    wanted = value.strip()
    if not wanted:
        raise ValueError(f"{column} 값이 비어 있다.")
    seen: list[str] = []

    def scan():
        for row in ui.grid_rows(site_grid):
            if ui.is_group_row(row):
                continue  # 마켓별 그룹 헤더 행
            code = ui.cell_text(row, column).strip()
            if not code:
                continue
            if code not in seen:
                seen.append(code)
            if code == wanted:
                return row
        return None

    row = scan()
    if row is None:
        log.info("화면에 보이는 %s: %s. 아래로 스크롤한다.", column, seen or "(없음)")

    for how in ("ScrollPattern", "키보드"):
        if row is not None:
            break
        for attempt in range(max_scrolls):
            # 키보드 스크롤은 기준 컨트롤이 필요하다. 지금 보이는 마지막 행을 쓴다.
            visible = ui.grid_rows(site_grid)
            anchor = visible[-1] if visible else None
            if not ui.scroll_grid(site_grid, "down", anchor=anchor, how=how):
                break

            before = len(seen)
            row = scan()
            if row is not None:
                log.info(
                    "%s %r 을(를) %s 로 %d번 스크롤해서 찾았다.",
                    column, wanted, how, attempt + 1,
                )
                break
            if len(seen) == before:
                # 새 행이 안 나왔다 = 끝까지 왔거나 스크롤이 안 먹었다.
                log.info("%s: %d번째 스크롤에서 새 행이 없다. 중단한다.", how, attempt + 1)
                break

    if row is None:
        raise ScreenError(
            f"사이트코드 {wanted!r} 인 행을 찾지 못했다.\n"
            f"  확인한 사이트코드: {seen or '(없음)'}"
        )

    log.info("%s %r 행 발견: %s", column, wanted, ui.describe(row))
    return row


def _collect_from_site(screen, dry_run: bool) -> None:
    """자동수집 — 사이트 전체선택 후 [가져오기]."""
    site_grid = ui.find(
        screen, auto_id=SITE_GRID_AUTO_ID, control_type="Table", what="사이트 그리드"
    )
    ui.header_select_all(site_grid, dry_run=dry_run)

    get_button = ui.find(
        screen, auto_id=GET_BUTTON_AUTO_ID, control_type="Button", what="가져오기 버튼"
    )
    ui.click(get_button, "가져오기", dry_run=dry_run)


def _collect_from_excel(screen, site_code: str, excel_path: str, pid: int | None,
                        dry_run: bool, site_name: str = "") -> None:
    """엑셀수집 — 지정한 사이트 행의 [엑셀업로드]를 눌러 엑셀을 올린다.

    사이트를 고르는 기준은 둘 중 하나다.

    - `site_name` : **사이트명 완전일치.** 수집 RPA 의 매니페스트가 주는 값이다
    - `site_code` : 사이트코드. 화면에서 직접 입력받을 때 쓴다

    둘 다 오면 `site_name` 을 쓴다.
    """
    if not site_code and not site_name:
        raise ValueError("엑셀수집에는 사이트명 또는 사이트코드가 필요하다.")
    if not excel_path:
        raise ValueError("엑셀수집에는 엑셀 경로가 필요하다.")

    path = Path(excel_path)
    if not path.is_file():
        raise ScreenError(f"엑셀 파일이 없다: {path}")
    if path.suffix.lower() not in EXCEL_SUFFIXES:
        raise ScreenError(f"엑셀 파일이 아니다: {path.name}")

    site_grid = ui.find(
        screen, auto_id=SITE_GRID_AUTO_ID, control_type="Table", what="사이트 그리드"
    )
    if site_name:
        row = find_site_row(site_grid, site_name, column=SITE_NAME_COLUMN)
        label = site_name
        # 로그용이다. 업로드는 사이트명으로 한다. 그래서 짧게만 기다린다.
        log.info("사이트명 %r 행: 사이트코드 %s", site_name,
                 ui.cell_text(row, SITE_CODE_COLUMN,
                              timeout=ui.INFO_CELL_TIMEOUT) or "(읽지 못함)")
    else:
        row = find_site_row(site_grid, site_code)
        label = site_code
        log.info("사이트 %s 행: %s", site_code,
                 ui.cell_text(row, SITE_NAME_COLUMN,
                              timeout=ui.INFO_CELL_TIMEOUT) or "(읽지 못함)")

    # 엑셀업로드 컬럼은 그리드 우측 끝에 있다. 가로 가상화 때문에 화면 밖이면
    # 셀이 UIA에 아예 없으므로, 스크롤해 가며 찾는다.
    upload_cell = ui.cell_scrolled(row, UPLOAD_COLUMN, grid=site_grid)
    # 누르기 직전에 **이 행의 셀을 다시 잡으려고** 행 번호를 들고 간다.
    # 셀 이름이 '엑셀업로드 행 16' 이라 행 번호가 곧 신원이다.
    upload_row = ui.row_number(row)
    log.info("엑셀업로드 셀: %s (행 %s)", ui.describe(upload_cell),
             upload_row if upload_row is not None else "?")

    # ★ **버튼이 없는 행은 누르지 않는다** (사용자 확정 2026-09-17).
    #   XL 아이콘은 그림이라 자식 컨트롤이 없다 — 2026-09-04 에는 그래서
    #   "판정 불가, 눌러 보고 파일 창으로 본다" 로 정했다. 그런데 **셀 값**이
    #   아이콘 유무를 준다(`1`/`0`, 51곳 대조 일치). 예전에는 버튼 없는 행을
    #   클릭 → Invoke → 재시도로 네 번 누르고 40초 넘게 기다린 뒤 실패로 적었다.
    #   dry-run 에서도 판정한다 — 미리보기에서 "올리지 못한다" 가 보여야 한다.
    present = upload_button_present(upload_cell)
    log.info("엑셀업로드 버튼: %s",
             {True: "있다", False: "없다", None: "알 수 없다 (눌러 보고 판정)"}[present])
    if present is False:
        raise NoUploadButton(
            f"사이트 {label} 행에 [엑셀업로드] 버튼이 없다. 누르지 않았다. "
            "ERPia 에서 그 사이트의 수집 엑셀 설정을 확인할 것.")

    if dry_run:
        log.info("[dry-run] 클릭 대상: %s", ui.describe(upload_cell))
        log.info("[dry-run] 파일 선택 Dialog에 넣을 경로: %s", path)
        return

    # Select 는 셀을 '선택'만 하고 아이콘은 누르지 않으므로 제외한다.
    # 누른 뒤 파일 선택 창이 뜨는지로 실제로 눌렸는지 확인하고, 안 뜨면 다음 방법.
    dialog = None
    last_error = ""
    for index, method in enumerate(ui.CELL_ICON_METHODS):
        # ★ **첫 시도에 넉넉한 시간을 준다.** 첫 방법이 실측상 되는 방법이므로,
        #   짧게 끊고 다음 방법으로 넘어가면 **이미 눌린 것을 또 누르게 된다.**
        #   창이 두 개 뜨거나 프로그램이 멈출 수 있어 가장 피해야 할 상황이다.
        #   폴백은 첫 방법이 정말로 안 먹었을 때만이라 짧게 본다.
        timeout = SETTINGS.timeouts.dialog if index == 0 else CLICK_VERIFY_TIMEOUT
        # 누르기 직전에 셀을 행 번호로 다시 잡는다 — 스크롤 직후나 ERPia 가 늦게 그리면 위치가 낡는다
        # (`ui.recheck_cell`).
        if upload_row is not None:
            upload_cell = ui.recheck_cell(site_grid, UPLOAD_COLUMN, upload_row,
                                          fallback=upload_cell)
        # 보이는 영역 아래에 걸쳐 잘린 행이면 셀 가운데가 가로 스크롤바 위다 — 그대로 누르면 스크롤바가 눌린다.
        # 보이게 올리고 다시 잡는다 (CONTROLS.md "정정 (2026-09-17) — 진짜 원인은 아래쪽에 걸쳐 잘린 행").
        if ui.reveal_cell(site_grid, upload_cell, "엑셀업로드 셀") \
                and upload_row is not None:
            upload_cell = ui.recheck_cell(site_grid, UPLOAD_COLUMN, upload_row,
                                          fallback=upload_cell)
        # 진단용: 클릭 전후 위치가 다르면 경고 (클릭이 헛나갔을 수 있다).
        before_click = ui.rect_of(upload_cell)
        ui.click(upload_cell, f"사이트 {label} 엑셀업로드", methods=(method,))
        after_click = ui.rect_of(upload_cell)
        if before_click != after_click:
            log.warning("엑셀업로드 셀이 **클릭 순간에** 움직였다: %s → %s "
                        "(클릭이 헛나갔을 수 있다)", before_click, after_click)
        else:
            log.info("클릭 전후 셀 위치 동일: %s", before_click)
        try:
            dialog = filedialog.wait_for_dialog(pid=pid, timeout=timeout)
            break
        except filedialog.FileDialogError as exc:
            # 진단 내용을 버리지 않는다. 여기를 삼켜서 원인 파악이 늦어진 적이 있다.
            last_error = str(exc)
            log.info("%s 로 눌렀으나 파일 선택 창을 찾지 못했다.", method)
            log.info("%s", last_error)

    if dialog is None:
        # 아이콘이 없는 사이트를 누르면 아무 일도 일어나지 않는다.
        raise UploadNotStarted(
            f"{EXCEL_SETTING_MISSING}\n"
            f"  사이트 {label} 의 엑셀업로드를 눌렀으나 "
            "파일 선택 창을 찾지 못했다.\n"
            f"  {last_error}"
        )

    how = filedialog.open_path(dialog, str(path))
    log.info("엑셀 파일 지정 완료 (%s)", how)
    filedialog.wait_closed(dialog)

    # 암호가 걸린 엑셀이면 비밀번호를 묻는다. 설정값으로 답한다.
    _handle_password_prompt(screen, pid, label)

    # 잘못된 엑셀이면 여기서 팝업이 뜬다. 넘어가지 않고 멈춘다.
    _check_upload_error(screen, pid)


def excel_password_for(site_name: str) -> str | None:
    """그 사이트의 엑셀 비밀번호. 설정에서만 읽는다. 코드에 두지 않는다."""
    table = getattr(SETTINGS, "excel_passwords", None) or {}
    if site_name and site_name in table:
        return table[site_name] or None
    return getattr(SETTINGS, "excel_password", None) or None


# 암호 걸린 .xlsx/.xlsm 은 ZIP 이 아니라 OLE 복합 문서로 저장된다(암호화 패키지).
# 그래서 첫 8바이트만 보면 **열지 않고도** 암호가 걸렸는지 안다. .xls 는 원래 OLE 라 모른다.
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_SIGNATURE = b"PK\x03\x04"


def password_state(head: bytes, suffix: str) -> bool | None:
    """파일 첫 8바이트로 판정한다. 걸렸으면 True, 아니면 False, **모르면 None** (.xls 등)."""
    if suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    if head.startswith(ZIP_SIGNATURE):
        return False
    return True if head[:8] == OLE_SIGNATURE else None


def needs_password(path) -> bool | None:
    """엑셀에 암호가 걸렸는가 (`password_state`). 파일을 못 읽으면 None."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError as exc:
        log.debug("엑셀 머리를 읽지 못했다: %s (%s)", path.name, type(exc).__name__)
        return None
    return password_state(head, path.suffix)


def _handle_password_prompt(screen, pid: int | None, site_label: str) -> bool:
    """암호가 걸린 엑셀이면 뜨는 비밀번호 창을 처리한다.

    뜨지 않으면 그냥 False. 뜨는데 설정에 비밀번호가 없으면 **중단**한다.
    아무 값이나 넣어 보지 않는다.
    """
    scopes = [screen]

    # 루프는 싼 확인(Win32)만, 비싼 확인(`find_in`)은 끝에 한 번.
    # 근거는 `_check_upload_error` 주석 참고.
    def probe():
        return dialogs.find_in_process(pid, PASSWORD_DIALOG_TITLE_RE)

    try:
        dialog = wait_for(probe, "엑셀 비밀번호 창", timeout=PASSWORD_PROMPT_TIMEOUT)
    except WaitTimeout:
        # 자식으로 떠서 Win32 스캔에 안 잡히는 경우를 여기서 한 번 받아 준다.
        dialog = dialogs.find_in(scopes, PASSWORD_DIALOG_TITLE_RE)
    if dialog is None:
        return False

    # 구조가 확정되지 않았다. 무엇이 떴는지 그대로 남긴다.
    log.info("비밀번호 창 제목: %r", dialog.window_text())
    log.info("비밀번호 창 본문: %s", dialogs.text_of(dialog) or "(없음)")
    log.info("비밀번호 창 버튼: %s", dialogs.buttons_of(dialog) or "(없음)")
    edits = ui.search(dialog, control_type="Edit")
    log.info("비밀번호 창 입력칸 %d개: %s", len(edits),
             [ui.describe(e) for e in edits[:3]])

    password = excel_password_for(site_label)
    if not password:
        raise ScreenError(
            f"엑셀에 비밀번호가 걸려 있는데 설정에 값이 없다 (사이트 {site_label}). "
            "config/settings.local.json 의 excel_passwords 에 "
            f'{{"{site_label}": "..."}} 를 넣을 것.')
    if not edits:
        raise ScreenError("비밀번호 창을 찾았으나 입력칸이 없다. 화면을 확인할 것.")

    ui.set_text(edits[0], password, "엑셀 비밀번호")
    dialogs.click_button(dialog, dialogs.CONFIRM_YES_RE, "확인")
    log.info("엑셀 비밀번호를 입력했다 (사이트 %s)", site_label)
    return True


def _check_upload_error(screen, pid: int | None) -> None:
    """엑셀 업로드 직후 뜨는 오류 팝업을 확인한다. 뜨면 중단한다.

    **잘못된 엑셀이면 팝업이 뜬다** (사용자 확인 2026-09-04).
    헤더가 안 맞는 파일을 그냥 넘기면 수집이 0건인 채로 매출처리까지 가버린다.

    팝업 제목은 아직 확정되지 않았다. 넓게 잡아두고 본문을 그대로 보고한다.
    """
    scopes = [screen]

    # ★ **싼 확인만 폴링에 둔다** (2026-09-11 실측).
    #   `find_in` 은 ERPia 창 트리를 훑어 **회당 2.7초**다. 그걸 폴링마다 부르면
    #   "팝업 없음" 을 알아내는 데만 수십 초가 든다(한 실행에서 16회 43.4초).
    #   `find_in_process` 는 Win32 최상위 창 스캔이라 0.7ms 다.
    #   그래서 **루프는 싼 것만 돌고, 비싼 확인은 끝에 딱 한 번** 한다.
    #   팝업이 자식으로 떠서 Win32 에 안 잡히는 경우를 그 한 번이 받아 준다.
    def probe():
        return dialogs.find_in_process(pid, UPLOAD_DIALOG_TITLE_RE)

    try:
        dialog = wait_for(probe, "엑셀 업로드 오류 팝업", timeout=UPLOAD_ERROR_TIMEOUT)
    except WaitTimeout:
        dialog = dialogs.find_in(scopes, UPLOAD_DIALOG_TITLE_RE)
    if dialog is None:
        log.info("업로드 오류 팝업 없음. 정상으로 본다.")
        return

    body = dialogs.text_of(dialog)
    title = dialog.window_text()
    log.error("업로드 팝업 제목: %r", title)
    log.error("업로드 팝업 본문: %s", body or "(본문 없음)")
    log.info("팝업 버튼: %s", dialogs.buttons_of(dialog) or "(없음)")

    # 확인을 눌러 팝업을 닫아 둔다. 남겨두면 다음 동작이 전부 막힌다.
    try:
        dialogs.click_button(dialog, dialogs.CONFIRM_YES_RE, "확인")
    except Exception as exc:
        log.warning("팝업을 닫지 못했다: %s", exc)

    raise ScreenError(
        f"엑셀 업로드 중 팝업이 떴다. 파일을 확인할 것.\n"
        f"  제목: {title}\n"
        f"  내용: {body or '(읽지 못함)'}"
    )


def wait_upload_settled(screen) -> bool:
    """엑셀 업로드 뒤, 다음 파일을 올려도 되는 상태인지 확인한다.

    예전에는 `wait_collect_started()` 로 **수집이 시작되는지**를 최대 15초 봤다.
    그런데 이 환경에서는 **수집 중에도 매출처리 버튼이 회색이 되지 않아**
    매번 15초를 통째로 버렸다 (2026-09-08 통합 실행에서 3회 모두).

    그래서 질문을 바꿨다 — "시작했는가" 가 아니라 **"지금 한가한가"** 를 본다.
    매출처리 버튼이 `collect_settle`(5초) 동안 **한 번도 비활성이 되지 않으면**
    한가한 것으로 보고 넘어간다. 그 사이 한 번이라도 회색이 되면 수집이 도는
    중이므로 **완료까지 기다린다.** 기다리는 조건은 그대로 두고, 헛기다림만 줄인다.
    """
    button = _sales_button(screen)
    if _enabled_stable(button):
        log.info("업로드 후 유휴 확인 — 다음으로 넘어간다.")
        return True

    log.info("수집이 도는 중이다. 끝날 때까지 기다린다.")
    wait_collect_done(screen)
    return False


def site_name_from_filename(name: str) -> str:
    """파일명 앞부분(첫 `_` 앞)을 사이트명으로 본다 (사용자 확정 2026-09-08).

    `사이트A_20260908.xlsx` → `사이트A`

    `_` 가 없으면 **빈 문자열**을 돌려준다. 파일명 전체를 사이트명으로 쓰지 않는다.
    이름 규칙을 지키지 않은 파일을 엉뚱한 사이트에 올리는 것보다 건너뛰는 편이 안전하다.
    """
    stem = Path(name).stem
    head, sep, _rest = stem.partition(SITE_NAME_SEPARATOR)
    return head.strip() if sep else ""


def excel_items_in(folder: str | Path) -> list[tuple[str, Path]]:
    """폴더 안의 엑셀을 `(사이트명, 경로)` 목록으로 만든다.

    - 하위 폴더는 보지 않는다. 지정한 폴더 바로 아래만 본다
    - 엑셀을 열어 둘 때 생기는 임시 파일(`~$...`)은 건너뛴다
    - 이름에 `_` 가 없어 사이트명을 못 뽑는 파일도 건너뛴다 (경고 로그를 남긴다)
    - 파일명 순서로 올린다. 실행할 때마다 순서가 달라지지 않게 한다
    """
    base = Path(folder)
    if not base.is_dir():
        raise ScreenError(f"엑셀 폴더가 없다: {base}")

    items: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for path in sorted(base.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.suffix.lower() not in EXCEL_SUFFIXES:
            continue
        if path.name.startswith("~$"):
            continue          # 엑셀이 열려 있을 때 생기는 임시 파일
        site = site_name_from_filename(path.name)
        if not site:
            skipped.append(path.name)
            continue
        items.append((site, path))

    for name in skipped:
        log.warning("사이트명을 뽑지 못해 건너뛴다(파일명이 `사이트명_...` 형식이 아니다): %s",
                    name)
    log.info("엑셀 폴더 %s — 대상 %d건: %s", base, len(items),
             [site for site, _ in items] or "(없음)")
    return items


def upload_excels(screen, items, pid: int | None = None,
                  dry_run: bool = False, on_item=None, on_uploaded=None) -> list[dict]:
    """엑셀 여러 개를 **각각의 사이트명**에 올린다.

    `items` 는 `(사이트명, 엑셀경로)` 를 순서대로 담은 목록이다.
    수집 RPA 의 매니페스트가 그대로 여기로 들어온다.

    한 건이 실패해도 나머지를 계속한다. **어느 것이 성공하고 어느 것이 실패했는지**
    돌려주고, 부르는 쪽이 매니페스트에 기록한다. 중간에 멈추면 이미 올린 것을
    다시 올릴지 판단할 근거가 없어진다.

    사이트명은 **완전일치**로 찾는다. 비슷한 이름의 다른 사이트에 올라가면
    되돌릴 수 없기 때문이다.

    `on_item` 은 **진행 상황을 알리는 통로**다(선택). 파일 하나를 시작할 때와
    끝낼 때 `on_item(index, total, site_name, entry_or_None)` 로 부른다.
    주지 않으면 아무 일도 하지 않는다 — **업로드 동작은 달라지지 않는다.**
    화면이 "2/3건" 을 실제 값으로 그릴 수 있게 하려고 뚫었다.

    `on_uploaded(entry)` 는 한 건이 **올라간 순간**(안정 대기·건수 세기 전) 부른다 — 통합 흐름이 그 자리에서
    매니페스트에 소비 표시를 한다. 그 뒤 대기에서 중단·실패해도 같은 엑셀을 다시 올리지 않게 (10-02).
    """

    def tell(index: int, total: int, site: str, entry=None) -> None:
        if on_item is None:
            return
        try:
            on_item(index, total, site, entry)
        except Exception as exc:
            # 진행 통지가 업로드를 막으면 안 된다.
            log.debug("진행 통지 실패(계속): %s", type(exc).__name__)
    results: list[dict] = []
    total = len(items)
    # ★ 파일마다 **새로 들어온 주문 수**를 미매출 주문수 차이로 센다 (사용자 요청 09-21).
    #   ERPia 는 업로드 건수를 알려 주지 않는다(성공이면 팝업도 없다). 라벨을 못 읽거나
    #   dry-run 이면 None — 화면은 "확인 못 함" 으로 쓴다. 라벨은 업로드 직후 바로 갱신된다
    #   (09-29 실기: 엑셀 3개 → 미매출 3→4→7→10).
    unsold = None if dry_run else unsold_number(screen)
    for index, (site_name, excel_path) in enumerate(items, start=1):
        label = f"엑셀업로드 {index}/{total} — {site_name}"
        log.info("%s: %s", label, excel_path)
        tell(index - 1, total, site_name)      # 시작 — 아직 끝낸 건 아니다
        # ★ `dry_run` 을 결과에 싣는다. dry-run 은 [엑셀업로드] 를 누르지 않고 끝나
        #   `ok=True` 가 되는데, 표시하지 않으면 요약·리포트에 **"성공"** 으로 찍혔다
        #   (HANDOFF 2-2, 09-21 수정). 올리지 않았다는 것을 화면이 말해야 한다.
        entry = {"site": site_name, "path": str(excel_path), "ok": False,
                 "error": None, "kind": None, "attempts": 0, "dry_run": dry_run,
                 "added": None, "warning": ""}
        if dry_run and needs_password(excel_path) and not excel_password_for(site_name):
            # ★ 시험 실행에서도 **실제로 올릴 때 막힐 것**을 미리 알린다 (09-21 검토:
            #   시험은 통과했는데 실제로는 실패했다). 올리지 않고 파일 머리만 본다.
            entry["warning"] = "실제로 올릴 때 비밀번호가 필요한데 등록된 비밀번호가 없습니다"
            log.warning("%s — [dry-run] 암호가 걸린 엑셀인데 설정에 비밀번호가 없다.", label)

        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            entry["attempts"] = attempt
            try:
                _collect_from_excel(screen, "", str(excel_path), pid, dry_run,
                                    site_name=site_name)
                entry["ok"] = True
                entry["error"] = None
                entry["kind"] = None
                break
            except NoUploadButton as exc:
                # 버튼이 없는 행이다. **다시 해도 같다** — 재시도하지 않는다.
                entry["error"], entry["kind"] = str(exc), KIND_NOT_STARTED
                log.error("%s 실패(%s): %s", label, KIND_LABELS[entry["kind"]],
                          entry["error"])
                break
            except UploadNotStarted as exc:
                entry["error"], entry["kind"] = str(exc), KIND_NOT_STARTED
            except UPLOAD_FAILURES as exc:
                # ★ **한 파일의 실패로 흐름을 죽이지 않는다.** 준비된 엑셀에
                #   문제가 있으면 그 파일만 실패로 남기고 다음으로 넘어간다
                #   (사용자 확정 2026-09-10). 예전에는 `FileDialogError` 가
                #   목록에 없어서 예외가 그대로 올라가 흐름이 멈췄다.
                entry["error"] = f"{type(exc).__name__}: {exc}"
                entry["kind"] = KIND_REJECTED

            log.error("%s %d/%d 실패(%s): %s", label, attempt, UPLOAD_ATTEMPTS,
                      KIND_LABELS[entry["kind"]], entry["error"])

            if not dry_run:
                # 실패하면 파일 선택 창이 열린 채 남을 수 있다. 정리하지 않으면
                # 다음 파일이 그 창을 보고 혼동한다.
                filedialog.close_if_open(pid)

            if attempt >= UPLOAD_ATTEMPTS:
                log.warning("%s — %d회 시도했지만 실패했다. **다음 단계로 넘어간다.** "
                            "엑셀 파일 자체를 확인할 것.", label, UPLOAD_ATTEMPTS)
                break
            if not dry_run:
                # 다시 누르기 전에 화면이 한가한지 확인한다. 바쁜 상태에서 또 누르면
                # 같은 이유로 또 실패하거나, 클릭이 겹칠 수 있다.
                wait_upload_settled(screen)
            log.info("%s — 한 번 더 올려 본다.", label)

        if entry["ok"] and not dry_run:
            if on_uploaded is not None:
                try:
                    on_uploaded(entry)
                except Exception as exc:        # noqa: BLE001 — 기록 때문에 업로드가 멈추면 안 된다
                    log.warning("%s — 올린 것을 기록하지 못했다(계속): %s", label, exc)
            # 업로드마다 수집이 돈다. 끝나기 전에 다음 파일을 올리면 겹친다.
            wait_upload_settled(screen)
            now = unsold_number(screen)
            entry["added"] = added_between(unsold, now)
            log.info("%s — 미매출 주문수 %s → %s (새 주문 %s건)", label, unsold, now,
                     entry["added"] if entry["added"] is not None else "?")
            unsold = now

        results.append(entry)
        tell(index, total, site_name, entry)   # 한 건 끝났다 (건수까지 센 뒤에 알린다)

    done = sum(1 for r in results if r["ok"])
    if dry_run:
        log.info("[dry-run] 엑셀업로드 — **올리지 않았다.** 대상 확인 %d / 실패 %d",
                 done, total - done)
    else:
        log.info("엑셀업로드 결과: 성공 %d / 실패 %d", done, total - done)
    return results


def log_upload_report(results, when=None) -> list[dict]:
    """업로드 실패를 **실행 맨 끝에 다시 한 번** 로그에 남긴다.

    중간 로그는 수백 줄에 묻힌다. 사람이 그날 로그를 열었을 때
    **마지막만 봐도 무엇이 등록되지 않았는지** 알 수 있어야 한다
    (사용자 확정 2026-09-09).

    실패를 두 종류로 나눠 적는다. 확인할 것이 다르기 때문이다.

    | 종류 | 뜻 | 사람이 볼 것 |
    | --- | --- | --- |
    | 업로드 실패 | 파일 선택 창이 뜨지 않아 **올리지도 못했다** | 그 사이트의 엑셀수집 설정 |
    | 등록 실패 | 파일은 올렸는데 프로그램이 **거부**했다 | 엑셀 내용·헤더·비밀번호 |
    """
    failed = [r for r in results if not r["ok"]]
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    log.info("=" * 62)
    if any(r.get("dry_run") for r in results):
        log.info("오늘(%s) 엑셀 업로드 — [dry-run] **올리지 않았다.** 대상 확인 %d건 / 실패 %d건",
                 stamp, len(results) - len(failed), len(failed))
    else:
        log.info("오늘(%s) 엑셀 업로드 결과 — 성공 %d건 / 실패 %d건",
                 stamp, len(results) - len(failed), len(failed))
    if not failed:
        log.info("등록되지 않은 엑셀은 없다.")
        log.info("=" * 62)
        return failed

    for kind in (KIND_NOT_STARTED, KIND_REJECTED):
        group = [r for r in failed if r["kind"] == kind]
        if not group:
            continue
        log.error("[%s] %d건", KIND_LABELS[kind], len(group))
        for item in group:
            log.error("   · %s — %s", item["site"], item["path"])
            log.error("     사유: %s", (item["error"] or "").splitlines()[0])
    log.error("위 엑셀은 **등록되지 않았다.** 확인한 뒤 다시 올릴 것.")
    log.info("=" * 62)
    return failed


def collect_orders(
    screen,
    source: str = "site",
    site_code: str | None = None,
    excel_path: str | None = None,
    pid: int | None = None,
    dry_run: bool = False,
    site_name: str | None = None,
) -> int:
    """주문수집. 수집 후 그리드의 **주문 수**(`조회된 주문수` 라벨, 09-29 — 행 수가 아니다)를 돌려준다.

    source
      "site"   자동수집 — 사이트 전체선택 후 [가져오기]
      "excel"  엑셀수집 — `site_name`(또는 `site_code`) 행의 [엑셀업로드] 로 `excel_path` 한 파일
    """
    if source not in ("site", "excel"):
        raise ValueError(f"알 수 없는 수집 방식: {source!r}")

    name = "자동수집" if source == "site" else f"엑셀수집({site_name or site_code})"
    label = f"주문수집({name})" + (" [dry-run]" if dry_run else "")

    with step(log, label):
        order_grid = ui.find(
            screen, auto_id=ORDER_GRID_AUTO_ID, control_type="Table", what="주문 그리드"
        )
        before = orders_in_grid(screen, order_grid)
        log.info("수집 전 조회된 주문 %d건", before)

        if source == "site":
            _collect_from_site(screen, dry_run)
        else:
            _collect_from_excel(screen, site_code or "", excel_path or "", pid,
                                dry_run, site_name=site_name or "")

        if dry_run:
            log.warning("[dry-run] 실제로 수집하지 않았다. 완료 판정도 하지 않았다.")
            return 0

        # 15초 안에 매출처리 버튼이 비활성이 되면 수집이 시작된 것이다.
        # 그렇지 않으면 **이미 끝난 것으로 보고 바로 다음 단계로 간다**
        # (사용자 확정 2026-09-07). 자동수집·엑셀수집 공통이다.
        if wait_collect_started(screen):
            wait_collect_done(screen)
        else:
            log.info("수집이 끝난 것으로 보고 다음 단계로 넘어간다.")

        # 여기까지 왔으면 수집은 끝났다. 그리드는 이미 최종 상태이므로 기다리지 않는다.
        # 예전에는 행 수가 안정될 때까지 최대 120초를 봤는데, 결과가 0건이면
        # 변화가 없어 120초를 통째로 버렸다 (2026-09-07 실행: 14:11:22 → 14:13:23).
        count = orders_in_grid(screen, order_grid)
        log.info("수집 완료. 조회된 주문 %d건 (수집 전 %d건)", count, before)

        # ★ 수집로그의 `실패` 는 **그 자리에서 눈에 띄게** 남긴다.
        #   예전에는 진행 상황 문자열에 섞여 INFO 로만 지나갔다 (2026-09-10).
        #   다만 **원인을 단정하지 않는다** — 아래 "수집 상태의 실제 의미" 참고.
        if source == "site":
            failures = collect_failures(collect_counts(screen))
            if failures:
                log.warning("자동수집 상태에 %s 이(가) 있다. "
                            "**주문이 없었거나 로그인에 실패한 것**이다 — "
                            "결함이 아닐 수 있다 (화면에 보이는 행 기준)",
                            ", ".join(f"'{k}' {v}건" for k, v in sorted(failures.items())))
        return count


def _cli() -> int:
    parser = argparse.ArgumentParser(description="주문매핑 매출처리 화면 자동화")
    parser.add_argument(
        "--step", required=True,
        choices=["enter", "collect", "sales", "logistics"],
    )
    parser.add_argument("--source", default="site", choices=["site", "excel"])
    parser.add_argument("--site-code", default=None, help="엑셀수집 대상 사이트코드")
    parser.add_argument("--excel", default=None, help="업로드할 엑셀 전체 경로")
    parser.add_argument("--pid", type=int, default=None,
                        help="대상 ERPia 인스턴스 PID. 생략하면 가장 최근 실행분")
    parser.add_argument("--dry-run", action="store_true", help="실제 클릭 없이 대상만 로그")
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

    if args.step == "enter":
        enter_order_mapping(target, dry_run=args.dry_run)
        return 0

    # collect 은 화면이 열려 있어야 한다. 진입까지 함께 처리한다.
    screen = enter_order_mapping(target, dry_run=args.dry_run)
    if screen is None:
        if args.dry_run:
            log.warning("[dry-run] 화면 Pane을 얻지 못해 수집 단계는 건너뛴다.")
            return 0
        log.error("화면 진입에 실패해 수집을 진행할 수 없다.")
        return 1
    if args.step == "collect":
        collect_orders(
            screen,
            source=args.source,
            site_code=args.site_code or SETTINGS.collect_site_code,
            excel_path=args.excel or SETTINGS.excel_path,
            pid=target.pid,
            dry_run=args.dry_run,
        )
    elif args.step == "sales":
        result = process_sales(
            screen, main_window=target.main_window(), dry_run=args.dry_run
        )
        log.info("매출처리 누른 방법: %s", result["clicked"])
        log.info("미매출 주문수: %s -> %s", result["before"], result["after"])
        # 매출처리 다음은 물류대기다 (docs/PROGRESS.md 업무 흐름)
        go_to_logistics_wait(target, dry_run=args.dry_run)
    elif args.step == "logistics":
        go_to_logistics_wait(target, dry_run=args.dry_run)
    return 0


# ====================================================================== Phase 7
def unsold_count(screen) -> str:
    """미매출 주문수 텍스트. 못 읽으면 빈 문자열.

    매출처리 전후 비교에 쓰고, **구간 실행에서 "지금 화면에 무엇이 남아
    있는지" 를 사람에게 보여 줄 때도** 쓴다 (2026-09-15 공개로 바꿨다).
    """
    try:
        label = ui.find(
            screen, auto_id=UNSOLD_COUNT_AUTO_ID, timeout=3, what="미매출 주문수"
        )
        return (label.window_text() or "").strip()
    except ui.ControlNotFound:
        return ""


def selected_count(screen) -> int | None:
    """`조회된 주문수` 라벨의 숫자 — 그리드에 조회된 **주문** 수. 못 읽으면 None."""
    try:
        label = ui.find(screen, auto_id=SELECTED_COUNT_AUTO_ID, timeout=3, what="조회된 주문수")
    except ui.ControlNotFound:
        return None
    return count_in(label.window_text() or "")


def order_rows(order_grid) -> list:
    """보이는 **주문** 행 — 마켓별 그룹 행은 뺀다."""
    return [row for row in ui.grid_rows(order_grid) if not ui.is_group_row(row)]


def orders_in_grid(screen, order_grid) -> int:
    """그리드의 주문 수 — `조회된 주문수` 라벨. 못 읽으면 보이는 주문 행 수(그룹 행 제외, 화면 기준)."""
    count = selected_count(screen)
    if count is not None:
        return count
    rows = len(order_rows(order_grid))
    log.info("조회된 주문수를 읽지 못했다. 보이는 주문 행(그룹 행 제외) %d개로 본다 (화면 기준).", rows)
    return rows


def count_in(text: str) -> int | None:
    """`'미매출 주문수 : 1,228건'` → 1228. 숫자가 없거나 둘 이상이면 None (고르지 않는다)."""
    numbers = re.findall(r"\d[\d,]*", text or "")
    if len(numbers) != 1:
        return None
    return int(numbers[0].replace(",", ""))


def unsold_number(screen) -> int | None:
    """미매출 주문수를 **숫자로.** 못 읽으면 None.

    그리드는 가상 스크롤이라 보이는 행만 세진다. **믿을 수 있는 총계는 이 라벨뿐이다**
    (`docs/archive/HANDOFF_20260917.md` 09-15). 수집 전후 차이로 "새로 들어온 주문 수" 를 센다 (09-21).
    """
    return count_in(unsold_count(screen))


def added_between(before: int | None, after: int | None) -> int | None:
    """수집 전후 미매출 주문수 차이. 하나라도 모르거나 줄었으면 None — 지어내지 않는다."""
    if before is None or after is None or after < before:
        return None
    return after - before


# 비싼 확인(`find_in`)을 몇 번에 한 번 섞을지. 1이면 매번(예전 동작)이다.
# ★ 이 팝업이 최상위 창인지 **확인된 바 없다.** 그래서 싼 확인만으로 끝내지
#   않는다. 싼 확인을 매번 하고 비싼 확인을 가끔 섞으면,
#   - 최상위로 뜨면: 싼 확인이 즉시 잡는다
#   - 자식으로 뜨면: 늦어도 이 주기 안에 비싼 확인이 잡는다
#   어느 쪽이든 **놓치지 않으면서** 비용은 이 배수만큼 준다.
#   (2026-09-11 실측: 비싼 확인은 회당 2.71초, 한 실행에서 16회 43.4초)
DEEP_PROBE_EVERY = 5


def _scope_pid(scopes) -> int | None:
    """범위에서 프로세스 id 를 얻는다. 싼 확인에 필요하다."""
    for scope in scopes:
        if scope is None:
            continue
        try:
            return scope.element_info.process_id
        except Exception as exc:
            log.debug("범위에서 pid 를 읽지 못했다: %s", type(exc).__name__)
    return None


def _wait_sales_dialog(scopes, what: str, timeout: float):
    """매출처리 팝업(제목 ERPia)을 기다린다. 안 뜨면 None."""
    pid = _scope_pid(scopes)
    state = {"tries": 0}

    def probe():
        state["tries"] += 1
        found = dialogs.find_in_process(pid, SALES_DIALOG_TITLE_RE)
        if found is not None:
            return found
        if state["tries"] % DEEP_PROBE_EVERY == 0:
            return dialogs.find_in(scopes, SALES_DIALOG_TITLE_RE)
        return None

    try:
        return wait_for(probe, what, timeout=timeout)
    except WaitTimeout:
        # 마지막으로 비싼 확인을 한 번. 주기에 걸리지 않고 끝났을 수 있다.
        return dialogs.find_in(scopes, SALES_DIALOG_TITLE_RE)


def _handle_of(dialog) -> int | None:
    """팝업의 창 핸들. 이미 사라졌으면 None."""
    try:
        return int(dialog.handle)
    except Exception:           # noqa: BLE001 — 핸들을 못 읽으면 닫힘을 기다릴 수 없을 뿐이다
        return None


def _wait_window_closed(pid: int | None, handle: int | None, what: str, timeout: float) -> None:
    """그 창(핸들)이 이 프로세스의 보이는 최상위 창에서 빠질 때까지. 안 빠져도 멈추지 않는다 —
    다음 팝업 대기가 이어서 본다. 자식으로 뜬 창이면 목록에 없어 곧바로 지나간다 (전과 같다)."""
    if pid is None or handle is None:
        return
    try:
        wait_until_gone(lambda: any(w.handle == handle for w in winprobe.top_windows(pid)),
                        what, timeout=timeout)
    except WaitTimeout:
        log.warning("%s — %.0f초 안에 닫히지 않았다", what, timeout)


def _dialog_timeout(index: int) -> float:
    """몇 번째 팝업을 얼마나 기다릴지.

    결과 팝업(`정상매출처리 성공`)은 **서버 처리가 끝난 뒤에** 뜬다.
    3초로 잡으면 놓친다. 2026-09-04 실행에서 놓쳐 팝업이 떠 있는 채로
    물류대기로 넘어갔고, 모달이라 화면이 열리지 않아 실패했다.
    """
    if index <= 1:
        return SETTINGS.timeouts.long_task
    return NEXT_DIALOG_TIMEOUT


def _wait_sales_dialogs_closed(scopes) -> None:
    """팝업이 모두 닫힐 때까지 기다린다.

    팝업이 떠 있으면 모달이라 다음 화면이 열리지 않는다.
    """
    pid = _scope_pid(scopes)
    state = {"tries": 0}

    def present():
        state["tries"] += 1
        found = dialogs.find_in_process(pid, SALES_DIALOG_TITLE_RE)
        if found is not None:
            return found
        # ★ "없다" 를 싼 확인만으로 단정하지 않는다. 자식으로 떠 있는데
        #   닫혔다고 보면 모달이 남은 채 다음 화면으로 가서 전부 막힌다.
        return dialogs.find_in(scopes, SALES_DIALOG_TITLE_RE)

    try:
        wait_until_gone(
            present,
            "매출처리 팝업",
            timeout=SETTINGS.timeouts.dialog,
        )
        log.info("매출처리 팝업이 모두 닫혔다.")
    except WaitTimeout:
        log.warning("매출처리 팝업이 아직 닫히지 않았다. "
                    "다음 화면이 열리지 않을 수 있다.")


def _handle_sales_dialogs(scopes) -> list[str]:
    """매출처리 후 뜨는 팝업을 처리한다. 읽은 본문 목록을 돌려준다.

    ★ **dry-run 에서는 부르지 않는다.** 버튼을 누르지 않았으니 팝업이 뜰 수
      없고, 기다려 봐야 상한을 통째로 버린 뒤 실패로 끝난다 (2026-09-11).
      그래서 이 함수는 "실제로 눌렀다" 를 전제로 한다.

    첫 팝업은 [매출처리] 단추의 '일괄' 확인 창이다 → [예] (`docs/CONTROLS.md` "매출처리 팝업은 2단이다").
    선택주문 메뉴의 확인 창이어도 같이 [예] 로 받는다.

    이후 팝업은 결과 알림으로 보고 닫되, 본문에 실패/오류가 있으면 중단한다.

    ★ **누른 팝업이 닫힌 뒤에 다음 팝업을 찾는다** (09-29 실계정). [예] 누르기는 비동기라 곧바로
      찾으면 **닫히는 중인 같은 확인 창**(제목 ERPia, 아직 보임)을 2번으로 잡아 빈 본문을 읽고,
      단추를 읽는 순간 창이 사라져 COMError(-2147220991) 로 멈췄다 (09-17 HANDOFF 3-1 과 같다).
      읽는 사이 사라진 창·단추 없는 창(처리 중 표시)은 누르지 않고 다음 팝업을 기다린다.
      몇 번째 팝업인지(결과 팝업 대기 시간)는 **누른 팝업 수**로 센다.
    """
    handled: list[str] = []
    clicked: dict[int, str] = {}        # 누른 창 핸들 → 그때 본문. 같은 창을 두 번 누르지 않는다
    pid = _scope_pid(scopes)

    for _ in range(MAX_DIALOGS):
        number = len(handled) + 1
        dialog = _wait_sales_dialog(
            scopes,
            f"매출처리 팝업 #{number}",
            timeout=_dialog_timeout(len(handled)),
        )
        if dialog is None:
            if not handled:
                raise ScreenError(
                    f"매출처리 확인 팝업(제목 {SALES_DIALOG_TITLE_RE})이 뜨지 않았다.\n"
                    "  매출처리 버튼이 실제로 눌리지 않았을 수 있다."
                )
            if len(handled) == 1:
                log.warning(
                    "결과 팝업이 %.0f초 안에 뜨지 않았다. "
                    "매출처리 결과를 화면에서 확인할 것.", _dialog_timeout(1),
                )
            break  # 더 뜨는 팝업이 없다

        handle = _handle_of(dialog)
        try:
            body = dialogs.text_of(dialog)
            buttons = dialogs.buttons_of(dialog)
        except Exception as exc:        # noqa: BLE001 — COMError·ElementNotAvailable: 읽는 사이 창이 없어졌다
            log.info("팝업 #%d 을 읽는 사이 창이 닫혔다(%s) — 다음 팝업을 기다린다",
                     number, type(exc).__name__)
            _wait_window_closed(pid, handle, f"매출처리 팝업 #{number} 닫힘", SETTINGS.timeouts.dialog)
            continue
        if handle is not None and clicked.get(handle) == body:
            # 누른 확인 창이 처리하는 동안 떠 있다 — [예] 를 또 누르면 안 된다
            log.info("팝업 #%d — 이미 누른 창이 아직 떠 있다. 닫히기를 기다린다", number - 1)
            _wait_window_closed(pid, handle, "누른 매출처리 팝업", SETTINGS.timeouts.long_task)
            continue
        log.info("팝업 #%d 본문: %s", number, body or "(본문 없음)")
        log.info("팝업 #%d 버튼: %s", number, buttons or "(없음)")
        if not buttons:
            log.info("팝업 #%d 에 단추가 없다 (처리 중 표시) — 누르지 않고 닫히기를 기다린다", number)
            _wait_window_closed(pid, handle, "단추 없는 매출처리 창", SETTINGS.timeouts.long_task)
            continue

        if dialogs.is_failure(body):
            dialogs.click_button(dialog, dialogs.CONFIRM_YES_RE, "확인")
            raise ScreenError(f"매출처리 중 오류 팝업: {body}")

        # CONFIRM_YES_RE 는 "예.*|확인.*|OK" 라서 "예(Y)" 와 "확인" 을 모두 받는다.
        # "아니오(N)" 은 걸리지 않는다.
        how = dialogs.click_button(dialog, dialogs.CONFIRM_YES_RE, "예/확인")
        log.info("팝업 #%d 처리: %s", number, how)
        handled.append(body)
        if handle is not None:
            clicked[handle] = body

        # 결과 알림("정상매출처리 성공")이 뜨면 끝이다.
        # 다음 팝업을 기다리며 시간을 버리지 않는다.
        if SALES_DONE_RE.search(body):
            log.info("결과 팝업을 확인했다. 매출처리를 마친다.")
            break
        _wait_window_closed(pid, handle, f"매출처리 팝업 #{number} 닫힘", SETTINGS.timeouts.dialog)

    else:
        raise ScreenError(f"팝업이 {MAX_DIALOGS}회를 넘겨 계속 뜬다. 중단한다.")

    _wait_sales_dialogs_closed(scopes)
    return handled


def _order_checks(order_grid) -> list[bool | None]:
    """보이는 주문 행(그룹 행 제외)의 체크 상태."""
    return [ui.check_state(row) for row in order_rows(order_grid)]


def _settled_checks(order_grid) -> list[bool | None]:
    """헤더를 누른 뒤 보이는 행이 **한쪽으로 모일 때까지** 잠깐 기다린다. 끝내 안 모이면 마지막 값."""
    last: list[bool | None] = []

    def probe():
        nonlocal last
        last = _order_checks(order_grid)
        return last if last and None not in last and len(set(last)) == 1 else None

    try:
        return wait_for(probe, "주문 체크 반영", timeout=CLICK_VERIFY_TIMEOUT)
    except WaitTimeout:
        return last


def select_all_orders(order_grid, dry_run: bool = False) -> None:
    """헤더 체크박스로 **조회된 주문 전부** 체크한다 (09-29 사용자 지시).

    헤더는 뒤집기다 — 이미 전부 체크돼 있었으면 풀린다. 보이는 행으로 확인하고, 풀렸으면 한 번 더 누른다.
    전부 체크됐는지 모르면 매출처리하지 않는다(체크 안 된 채 누르면 무엇이 처리될지 모른다).
    """
    ui.header_select_all(order_grid, dry_run=dry_run)
    if dry_run:
        return
    states = _settled_checks(order_grid)
    if states and not any(states):
        log.info("헤더를 누르니 체크가 풀렸다 — 이미 전부 체크돼 있었다. 한 번 더 누른다")
        ui.header_select_all(order_grid)
        states = _settled_checks(order_grid)
    if not states or not all(states):
        raise ScreenError(f"주문 전체 체크를 확인하지 못했다 (보이는 행 체크 상태: {states})")
    log.info("주문 전체 체크 — 보이는 %d행 확인", len(states))


def _context_row(order_grid):
    """우클릭할 주문 행 — 보이는 첫 주문 행(그룹 행 제외)."""
    rows = order_rows(order_grid)
    if not rows:
        raise ScreenError("우클릭할 주문 행이 없다.")
    return rows[0]


def open_selected_sales(screen, order_grid, dry_run: bool = False) -> str:
    """주문 행 우클릭 → [선택주문 매출처리]. 확인 창은 부르는 쪽이 처리한다."""
    row = _context_row(order_grid)
    ui.right_click(ui.cell(row, CONTEXT_COLUMN), "주문 행", dry_run=dry_run)
    if dry_run:
        # 우클릭을 안 했으니 메뉴가 없다 — 찾으면 20초를 버리고 '못 찾음' 으로 멈춘다
        log.info("[dry-run] 메뉴 [%s] 를 누르지 않는다", SELECTED_SALES_MENU)
        return "dry-run"
    # 마우스 클릭만 — 확인 창이 모달이라 Invoke 는 그 창이 닫힐 때까지 돌아오지 않는다 (09-29 실측)
    return ui.click_process_menu_item(_scope_pid([screen]), SELECTED_SALES_MENU, methods=("클릭",))


def _sales_button(screen):
    """매출처리 버튼. 수집 진행 여부 판정에도 쓴다."""
    return ui.find(
        screen, auto_id=SALES_BUTTON_AUTO_ID, what=f"{SALES_BUTTON_TITLE} 버튼"
    )


def wait_collect_started(screen, timeout: float | None = None) -> bool:
    """[가져오기] 직후 매출처리 버튼이 비활성이 되는지 `collect_start`(15초) 본다.

    **버튼이 회색이 되기까지 몇 초 걸린다 (사용자 확인 2026-09-03).**
    이 시간차 안에 완료 판정을 하면 수집이 시작하기도 전에 통과해 버린다.

    **15초 안에 비활성이 되지 않으면 수집이 이미 끝난 것으로 보고 False 를
    돌려준다** (사용자 확정 2026-09-07). 자동수집·엑셀수집 공통이다.

    예전에는 수집로그에 움직임이 있으면 30초를 더 봤다. 그런데 이 환경에서는
    **수집 중에도 버튼이 회색이 되지 않아** 매번 45초를 통째로 버렸다
    (2026-09-07 실행: 14:10:45 → 14:11:16). 수집로그의 `수집중` 은 수집할 것이
    없거나 사이트 설정이 잘못된 경우에도 뜨므로 판정에 쓸 수 없다.
    """
    button = _sales_button(screen)
    timeout = SETTINGS.timeouts.collect_start if timeout is None else timeout

    def disabled() -> bool:
        return not ui.is_enabled(button)

    try:
        wait_for(disabled, f"{SALES_BUTTON_TITLE} 버튼 비활성(수집 시작)", timeout=timeout)
    except WaitTimeout:
        log.info("%.0f초 안에 매출처리 버튼이 비활성이 되지 않았다. 진행 상황: %s",
                 timeout, _collect_progress(screen))
        return False
    log.info("수집 시작 확인 (매출처리 버튼 비활성).")
    return True


def collect_counts(screen) -> dict[str, int]:
    """수집로그 그리드의 상태별 건수. 못 읽으면 빈 dict. **진행 로그용이다.**

    가상 스크롤이라 **보이는 행만** 집계된다. 그래서 완료 판정에는 쓰지 않는다.
    결과 요약·실패 사이트 이름은 끝까지 내려 세는 `collect_counts_all` 을 쓴다 (09-22).
    컬럼은 CONTROLS.md "수집 로그 그리드 컬럼".
    """
    try:
        grid = ui.find(
            screen, auto_id=COLLECT_LOG_GRID_AUTO_ID, control_type="Table",
            timeout=3, what="수집로그 그리드",
        )
    except ui.ControlNotFound:
        return {}

    counts: dict[str, int] = {}
    for row in ui.grid_rows(grid):
        state = ui.cell_text(row, STATUS_COLUMN,
                             timeout=ui.INFO_CELL_TIMEOUT).strip() or "(빈값)"
        counts[state] = counts.get(state, 0) + 1
    return counts


def collect_counts_all(screen, failed_names: list | None = None) -> tuple[dict[str, int], bool]:
    """수집로그 그리드를 **끝까지 내려** 상태별 건수를 센다. `(건수, 끝까지 봤나)`.

    `collect_counts` 는 보이는 행만 센다 — 사이트가 많아 스크롤이 생기면 화면 밖 실패 사이트를
    이름도 수도 놓쳤다 (09-22 사용자 요청 "모든 행을"). 결과 요약·확인할 것은 이것을 쓴다.

    스크롤은 물류대기에서 확정된 `ui.scroll_down_verified` 다 — **행 번호가 바뀌었는지로** 판정하고,
    스크롤바가 없으면 아무것도 누르지 않는다. 내릴 때 키보드 폴백은 쓰지 않는다(마지막 행 포커스 = 선택).
    맨 위로 올릴 때(`ui.scroll_to_top`)는 이미 맨 위면 아무것도 안 하고, 스크롤바가 안 먹을 때만
    첫 행 Ctrl+Home 으로 떨어진다 (수집로그 행 선택은 데이터를 바꾸지 않는다).
    ★ 이 그리드의 스크롤은 **실측하지 않았다** (CONTROLS "수집 로그 그리드"). 못 내렸거나 상한에
      걸리면 `끝까지 봤나=False` → 부르는 쪽이 "N건 이상" 으로 적는다. 숫자를 지어내지 않는다.
    ★ 수집이 **끝난 뒤에만** 부른다. 진행 로그(`_collect_progress`)는 폴링마다 불려 스크롤하면
      갱신 중인 그리드와 부딪친다 — 그쪽은 `collect_counts` 그대로.
    """
    try:
        grid = ui.find(screen, auto_id=COLLECT_LOG_GRID_AUTO_ID, control_type="Table",
                       timeout=3, what="수집로그 그리드")
    except ui.ControlNotFound:
        return {}, False

    at_top = ui.scroll_to_top(grid)
    states: dict[int, str] = {}           # 행 번호 → 상태. 두 번 보인 행은 한 번만 센다
    markets: dict[int, str] = {}
    capped = True
    for page in range(MAX_COLLECT_LOG_PAGES):
        cells = ui.columns_values(grid, (STATUS_COLUMN, MARKET_COLUMN))
        # 행 번호는 **상태 셀을 실제로 읽은 행**에서 얻는다. 행 목록을 따로 훑으면 셀 탐색이
        # 실패한 페이지가 통째로 '(빈값)' 으로 굳고 다시 읽히지 않았다 (09-22 검토).
        fresh = sorted(n for n in cells[STATUS_COLUMN] if n not in states)
        for number in fresh:
            states[number] = (cells[STATUS_COLUMN].get(number) or "").strip() or "(빈값)"
            markets[number] = (cells[MARKET_COLUMN].get(number) or "").strip()
        if (not fresh and page) or not ui.scroll_down_verified(grid, methods=("ScrollBar",)):
            capped = False
            break
    if capped:
        log.warning("수집로그를 %d페이지까지 내렸는데 끝이 아니다.", MAX_COLLECT_LOG_PAGES)

    # 끝에 닿았나 — 스크롤바가 없거나 '페이지 아래로' 가 사라졌다 (`scroll_down_verified` 와 같은 판정).
    # 이름이 'Vertical' 이 아닌 세로 스크롤바('세로' 등)가 보이면 끝이라고 보지 않는다.
    bar = ui.vertical_scrollbar(grid)
    at_end = (ui.has_hidden_rows(grid) is False if bar is None else
              ui.scrollbar_button(grid, ui.SCROLL_CAN_DOWN_NAMES, bar=bar) is None)
    # 읽은 행 번호가 1 부터 **빠짐없이** 이어져야 끝까지 본 것이다 — 맨 위 판정(`min<=1`)이나
    # 스크롤이 한 페이지를 건너뛰면 가운데가 비어도 스크롤바만 보고 True 가 됐다 (09-22 검토)
    contiguous = bool(states) and sorted(states) == list(range(1, max(states) + 1))
    exact = contiguous and at_top and at_end and not capped

    counts: dict[str, int] = {}
    for number in sorted(states):
        state = states[number]
        counts[state] = counts.get(state, 0) + 1
        if failed_names is not None and COLLECT_FAIL_RE.search(state):
            name = markets[number]
            if name and name not in failed_names:
                failed_names.append(name)
    if not states:
        log.info("수집로그가 비어 있다 (이번 ERPia 에서 수집한 적이 없거나 읽지 못함).")
    else:
        log.info("수집로그 %d행 %s — %s", len(states), "끝까지 봄" if exact else "**끝까지 못 봄**",
                 ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return counts, exact


def collect_failures(counts: dict[str, int]) -> dict[str, int]:
    """상태 이름에 '실패'/'오류' 가 들어간 것만 골라 돌려준다.

    2026-09-10 통합 실행에서 `성공 1, 수집중 4, 실패 1` 이었는데 **경고도
    최종 요약도 없었다.** 사용자는 그 상태를 모른 채 지나간다. 엑셀업로드만
    요약에 남는 것은 앞뒤가 맞지 않는다.

    ## ★ 수집 상태의 실제 의미 (사용자 확정 2026-09-15)

    **셋 중 어느 것도 "그 사이트가 끝났다" 를 뜻하지 않는다.**

    | 표시 | 실제 의미 |
    | --- | --- |
    | `성공` | 끝났다는 뜻이 아니다. **수집된 주문이 0건일 수도 있다** |
    | `수집중` | 진행 중이 아닐 수 있다. **로그인에서 막힌 것**도 이렇게 보인다 |
    | `실패` | **주문이 없었거나**, **로그인에 실패했거나** — 둘이 섞여 있다 |

    그래서 여기서 고른 것을 **결함이라고 단정하면 안 된다.** 주문이 없어도
    `실패` 다. 부르는 쪽은 원인을 못 박지 말고 "확인해 보라" 까지만 적는다.

    같은 이유로 이 값들은 **완료 판정에 쓰지 않는다.** 완료는
    `wait_collect_started` / `wait_collect_done` 이 매출처리 버튼으로 판정한다.
    """
    return {k: v for k, v in counts.items()
            if COLLECT_FAIL_RE.search(k) and v}


def _collect_progress(screen) -> str:
    """진행 상황 한 줄. 로그용이다."""
    counts = collect_counts(screen)
    if not counts:
        return "(수집로그를 읽지 못함)"
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    return f"{summary} (화면에 보이는 행 기준)"


def _collect_log_grid(screen):
    try:
        return ui.find(
            screen, auto_id=COLLECT_LOG_GRID_AUTO_ID, control_type="Table",
            timeout=3, what="수집로그 그리드",
        )
    except ui.ControlNotFound:
        return None


def collect_checks(screen) -> tuple[int, int, int]:
    """수집로그의 '수집여부' 체크 상태를 센다. (체크됨, 안 됨, 못 읽음)

    가상 스크롤이라 **화면에 보이는 행만** 집계된다.
    """
    grid = _collect_log_grid(screen)
    if grid is None:
        return (0, 0, 0)

    checked = unchecked = unknown = 0
    for row in ui.grid_rows(grid):
        state = ui.is_checked(ui.cell(row, COLLECTED_COLUMN)) if _has_cell(
            row, COLLECTED_COLUMN) else None
        if state is True:
            checked += 1
        elif state is False:
            unchecked += 1
        else:
            unknown += 1
    return (checked, unchecked, unknown)


def _has_cell(row, column: str) -> bool:
    try:
        ui.cell(row, column)
        return True
    except ui.ControlNotFound:
        return False


def _enabled_stable(button, seconds: float | None = None) -> bool:
    """버튼이 `seconds` 동안 계속 활성인지 확인한다.

    [가져오기] 후 버튼이 회색이 되기까지 몇 초 걸린다. 한 번만 보고 판단하면
    그 틈에 "완료"로 오판한다. 잠깐이라도 비활성이면 아직 끝난 게 아니다.
    """
    seconds = SETTINGS.timeouts.collect_settle if seconds is None else seconds
    # 폴링 대기는 utils/wait.py 안에만 둔다. 업무 코드에 고정 대기를 두지 않는다.
    return stays_true(lambda: ui.is_enabled(button), "버튼 활성 유지", seconds)


def wait_collect_done(screen, timeout: float | None = None) -> None:
    """주문수집이 끝날 때까지 기다린다.

    **판정은 매출처리 버튼의 활성 여부 하나로 한다** (사용자 확정 2026-09-04).
    수집 중에는 회색이고, 끝나면 누를 수 있게 된다.

    수집로그의 '수집여부' 체크는 **판정에 쓰지 않는다.** 자동수집에서 수집할 수
    없는 사이트는 계속 `수집중` 으로 남기 때문에, 이 조건을 걸면 영원히 끝나지
    않는다. 진행 상황을 보여주는 로그 용도로만 쓴다.

    성공/실패는 가리지 않는다. 실제 운영에서는 사이트 1~2개만 쓰고 나머지는
    사용하지 않아 로그인 실패/수집 실패가 정상적으로 발생한다.

    수집로그의 상태 컬럼만으로 판정하지 않는다. 가상 스크롤이라 안 보이는
    행이 UIA에 없고, 사이트마다 끝나는 순서가 달라 마지막 행만 봐서도 안 된다.
    """
    timeout = SETTINGS.timeouts.collect if timeout is None else timeout
    button = _sales_button(screen)

    if _enabled_stable(button):
        log.info("매출처리 버튼이 활성 상태로 유지된다. 진행 중인 수집이 없다.")
        return

    started = time.monotonic()
    state = {"next_log": started}
    log.info(
        "주문수집이 진행 중이다. 끝날 때까지 기다린다 (최대 %.1f시간).",
        timeout / 3600,
    )

    def report() -> None:
        now = time.monotonic()
        if now < state["next_log"]:
            return
        state["next_log"] = now + PROGRESS_LOG_INTERVAL
        checked, unchecked, unknown = collect_checks(screen)
        log.info(
            "수집 대기 중... 경과 %.0f분 / 수집여부 체크 %d, 미체크 %d, 판정불가 %d / %s",
            (now - started) / 60, checked, unchecked, unknown,
            _collect_progress(screen),
        )

    def done() -> bool:
        report()
        return _enabled_stable(button)

    try:
        wait_for(done, f"{SALES_BUTTON_TITLE} 버튼 활성(수집 완료)", timeout=timeout)
    except WaitTimeout as exc:
        checked, unchecked, unknown = collect_checks(screen)
        raise ScreenError(
            f"{timeout / 3600:.1f}시간이 지나도 주문수집이 끝나지 않았다.\n"
            f"  수집여부 체크 {checked}, 미체크 {unchecked}, 판정불가 {unknown}\n"
            f"  진행 상황: {_collect_progress(screen)}"
        ) from exc

    checked, unchecked, unknown = collect_checks(screen)
    log.info(
        "주문수집 완료. 경과 %.1f분 / 수집여부 체크 %d건 / %s",
        (time.monotonic() - started) / 60, checked, _collect_progress(screen),
    )


def normalized_sales_mode(mode: str | None) -> str:
    """매출처리 방식 값을 확인한다. **빈 값·틀린 값이면 화면을 건드리기 전에** 막는다 (기본값 없음, 10-02)."""
    value = (mode or "").strip()
    if not value:
        raise ScreenError("매출처리 방식이 비어 있다 (config/settings.local.json 의 sales_mode). "
                          f"{' / '.join(SALES_MODES)} 중 하나를 고를 것.")
    if value not in SALES_MODES:
        raise ScreenError(
            f"매출처리 방식 값이 {value!r} 이다. {' / '.join(SALES_MODES)} 중 하나여야 "
            "한다 (config/settings.local.json 의 sales_mode).")
    return value


def process_sales(screen, main_window=None, wait_collect: bool = True,
                  dry_run: bool = False, mode: str | None = None) -> dict:
    """매출처리 → 확인 팝업 [예] → 결과 [확인]. 방식은 `mode`(없으면 설정 `sales_mode`).

    전체     [매출처리] 단추. 행은 선택하지 않는다. **조회되지 않은 미매출 건까지** 처리하므로
             결과에 `selected`(조회된 주문수)를 넣지 않는다 — 처리 수는 미매출 주문수 전후 차이다.
    선택주문 조회된 주문 전부 체크 → 우클릭 [선택주문 매출처리]. 결과에 `selected` 를 넣는다.

    **실제 매출 전표를 생성한다. 되돌릴 수 없다.**
    """
    label = "매출처리" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        mode = normalized_sales_mode(mode or getattr(SETTINGS, "sales_mode", None))
        log.info("매출처리 방식: %s", mode)
        if wait_collect:
            wait_collect_done(screen)

        # 조회된 주문이 0건이면 이 화면에서 할 일이 없다.
        # 매출처리를 눌러도 확인 팝업이 뜨지 않아 팝업 대기에서 멈춘다
        # (2026-09-04). 자동수집·엑셀수집 공통으로 그냥 넘어간다.
        # ★ 행 수로 보지 않는다 — 그룹 행이 섞인다 (09-29). `조회된 주문수` 라벨이 대상 수다
        order_grid = ui.find(screen, auto_id=ORDER_GRID_AUTO_ID,
                             control_type="Table", what="주문 그리드")
        selected = orders_in_grid(screen, order_grid)
        log.info("매출처리 대상 — 조회된 주문 %d건", selected)
        if selected == 0:
            log.info("조회된 주문이 0건이다. 매출처리할 건이 없어 건너뛴다.")
            return {"before": None, "after": None, "clicked": None,
                    "dialogs": [], "skipped": True, "selected": 0}

        checked, unchecked, unknown = collect_checks(screen)
        log.info("수집여부 — 체크 %d, 미체크 %d, 판정불가 %d", checked, unchecked, unknown)

        before = unsold_count(screen)
        log.info("처리 전 미매출 주문수: %s", before or "(읽지 못함)")

        if mode == SALES_SELECTED:
            select_all_orders(order_grid, dry_run=dry_run)
            clicked = open_selected_sales(screen, order_grid, dry_run=dry_run)
            target = {"selected": selected}
        else:
            button = _sales_button(screen)
            log.info("매출처리 버튼: %s", ui.describe(button))
            clicked = ui.click(button, SALES_BUTTON_TITLE, dry_run=dry_run)   # Invoke 는 곧바로 돌아온다 (09-29 실측)
            target = {}

        if dry_run:
            # ★ 단추·메뉴를 **누르지 않았다.** 그러니 확인 팝업은 뜰 수가 없다.
            #   예전에는 그래도 기다려서 long_task(120초)를 통째로 버리고
            #   `ScreenError` 로 흐름 전체를 끊었다 (2026-09-11 실측 124초:
            #   그 대기 한 종류가 전체 182.7초 중 116.8초를 먹었다).
            log.warning("[dry-run] 매출처리를 누르지 않았다. 확인 팝업도 기다리지 않는다.")
            log.warning("[dry-run] 실제로 처리하지 않았다. 완료 판정도 하지 않았다.")
            handled: list[str] = []
            return {"before": before, "after": before, "clicked": clicked,
                    "dialogs": handled, "skipped": False, **target}

        # 팝업이 화면 Pane 하위에 안 붙을 수 있어 메인 창까지 본다.
        handled = _handle_sales_dialogs([screen, main_window])

        after = unsold_count(screen)
        log.info("처리 후 미매출 주문수: %s", after or "(읽지 못함)")
        if before and after and before == after:
            log.warning("미매출 주문수가 그대로다(%s). 실제로 처리됐는지 확인할 것.", after)

        return {"before": before, "after": after, "clicked": clicked,
                "dialogs": handled, "skipped": False, **target}


# ====================================================================== Phase 8
def go_to_logistics_wait(target, dry_run: bool = False) -> str | None:
    """좌측 프로세스바의 '물류대기'로 이동한다. 새로 열린 탭 이름을 돌려준다.

    물류대기 화면의 고유 Pane auto_id는 아직 조사되지 않았다.
    그래서 **탭이 새로 생기는지**로 진입을 판정하고 그 이름을 로그에 남긴다.
    (추측한 auto_id를 쓰지 않기 위해서다)
    """
    label = "물류대기 화면 이동" + (" [dry-run]" if dry_run else "")
    with step(log, label):
        main_window = target.main_window()
        before = _tab_titles(main_window)
        log.info("이동 전 탭: %s", before or "(없음)")

        entry = ui.find(
            main_window,
            auto_id=LOGISTICS_WAIT_AUTO_ID,
            what=f"프로세스바 {LOGISTICS_WAIT_NAME!r}",
        )
        ui.click(entry, f"좌측 프로세스바 {LOGISTICS_WAIT_NAME!r}", dry_run=dry_run)

        if dry_run:
            log.warning("[dry-run] 클릭하지 않았으므로 진입 여부를 검증하지 않았다.")
            return None

        def new_tab():
            added = _tab_titles(main_window) - before
            return next(iter(added), None)

        try:
            opened = wait_for(new_tab, "물류대기 탭 등장", timeout=SETTINGS.timeouts.window)
        except WaitTimeout as exc:
            raise ScreenError(
                f"{LOGISTICS_WAIT_NAME} 를 눌렀으나 새 탭이 열리지 않았다.\n"
                f"  현재 탭: {sorted(_tab_titles(main_window))}"
            ) from exc

        log.info("물류대기 진입 확인. 새 탭: %r", opened)
        log.info("이 탭 이름을 docs/CONTROLS.md 에 기록할 것.")
        return opened


def _tab_titles(main_window) -> set[str]:
    names = set()
    for item in ui.find_all(main_window, control_type="TabItem"):
        try:
            name = (item.window_text() or "").strip()
        except Exception:
            continue
        if name:
            names.add(name)
    return names


if __name__ == "__main__":
    sys.exit(_cli())
