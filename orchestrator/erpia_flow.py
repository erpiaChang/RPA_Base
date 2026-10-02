r"""ERPia 업무 흐름. 실행 → 로그인 → 주문수집 → 매출처리 → 물류대기 → 물류관리.

주문수집은 **자동수집과 엑셀수집을 최소 1개, 최대 2개** 고른다
(사용자 확정 2026-09-08). 둘 다 고르면 **엑셀수집 → 자동수집** 순으로 이어서 한다
(순서 근거는 아래 `SOURCES` 주석).

엑셀수집은 **폴더**를 받는다. 그 안의 엑셀을 파일명에서 뽑은 사이트명으로
각각 업로드한다. 파일 하나만 올리던 방식은 없앴다. 통합 흐름과 같은 방식이며,
대상 목록을 폴더에서 얻느냐 매니페스트에서 얻느냐만 다르다.

이 모듈은 `automation/` 만 쓴다. 메일 수집 쪽을 import 하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from automation import updater
from automation.application import connect, start_new_instance
from automation.login import login
from automation import logistics, logistics_wait
from config.settings import SETTINGS
from automation.order_mapping import (
    ScreenError,
    added_between,
    collect_counts_all,
    collect_failures,
    collect_orders,
    count_in,
    enter_order_mapping,
    excel_items_in,
    log_upload_report,
    normalized_sales_mode,
    process_sales,
    unsold_count,
    unsold_number,
    upload_excels,
)
from orchestrator import friendly, report, steps, steps_erpia
from orchestrator.common import Hooks, Result
from utils import dialogs
from utils.logger import get_logger, step, user_log

log = get_logger(__name__)
user = user_log()

SOURCE_SITE = "site"     # 자동수집 — 사이트 전체선택 후 [가져오기]
SOURCE_EXCEL = "excel"   # 엑셀수집 — 폴더 안의 엑셀을 사이트명별로 업로드

# 실행 순서는 **엑셀수집 → 자동수집** (사용자 확정 09-09) — 자동수집이 도는 중에 엑셀업로드를 누르면
# 파일 창이 안 뜬다 (09-09 실측).
SOURCES = (SOURCE_EXCEL, SOURCE_SITE)

SOURCE_LABELS = {SOURCE_SITE: "자동수집", SOURCE_EXCEL: "엑셀수집"}


@dataclass
class Options:
    """흐름에 필요한 입력. 모두 화면에서 받는다."""

    exe: str
    company: str
    user_id: str
    password: str
    # 주문수집 방식. 최소 1개. 고른 순서와 상관없이 **엑셀수집 → 자동수집** 으로 실행한다.
    sources: list[str] = field(default_factory=list)      # 기본값 없음 (10-02) — 비면 normalized_sources 가 막는다
    # 엑셀수집 대상 **폴더**. `sources` 에 엑셀수집이 있을 때만 쓴다.
    excel_dir: str = ""
    courier: str = ""
    box: str = ""
    # 물류관리 자동/수동. 저장 뒤 [운송장출력] / [엑셀파일생성] 이 갈린다.
    # 비우면 설정값(`logistics_mode`). 그것도 비면 물류관리를 시작하지 않는다 (기본값 없음, 10-02)
    mode: str = ""
    dry_run: bool = False
    # 구간 실행 — **이 단계부터** 한다 (`steps_erpia` 의 id). 비우면 처음부터.
    # 앞 단계를 하지 않으므로 **이미 로그인된 인스턴스에 붙는다**(`attach`).
    start_step: str = ""
    # 고른 기능(`orchestrator/modules.py` 의 id). **통합·실행용만 본다.**
    # None 이면 전부. 이 흐름(ERPia 단독)은 보지 않는다.
    modules: list[str] | None = None

    def normalized_sources(self) -> list[str]:
        """실행 순서대로 정리한다. 값이 틀렸으면 여기서 막는다."""
        unknown = [s for s in self.sources if s not in SOURCES]
        if unknown:
            raise ValueError(f"알 수 없는 수집 방식: {unknown}")
        chosen = [s for s in SOURCES if s in self.sources]
        if not chosen:
            raise ValueError("주문수집 방식을 최소 하나 골라야 한다 (자동수집 / 엑셀수집).")
        return chosen


def attach(exe: str):
    """**이미 떠 있는** 인스턴스에 붙는다. 구간 실행에서만 쓴다.

    구간 실행의 전제는 "앞 단계는 이미 됐다" 다. 새 인스턴스를 띄우면 로그인도
    화면 진입도 처음부터라 전제가 깨진다. 그래서 붙는다.

    붙을 것이 없으면 **막는다.** 여기서 새로 띄워 주면 사용자는 물류대기부터
    시작한다고 생각하는데 실제로는 처음부터 도는 것이 된다.
    """
    target = connect(exe)
    if target is None:
        raise ScreenError(
            "구간 실행은 이미 로그인된 인스턴스에 붙어서 한다.\n"
            "  ERPia 가 실행 중이 아니다. 처음부터 실행할 것.")
    target.normalize()      # 최소화 상태면 좌표 클릭이 통하지 않는다
    titles = [t for t in target.window_titles() if t]
    log.info("구간 실행 — 기존 인스턴스에 붙었다. pid=%s, 창: %s",
             target.pid, ", ".join(titles[:3]) or "(제목 없음)")
    return target


def screen_state_for(step, screen, uploads=None) -> str:
    """구간 실행 확인 창에 보여 줄 **지금 화면 상태.** 모르면 빈 문자열.

    ## 어디까지 읽을 수 있나 (2026-09-15)

    | 시작점 | 보여 주는 것 |
    | --- | --- |
    | 엑셀수집 | 올릴 파일 목록. **ERPia 를 건드리지 않고** 알 수 있다 |
    | 매출처리 | 미매출 주문수. 이미 처리됐으면 0건으로 보인다 |
    | 자동수집 / 물류대기 / 물류관리 | **없다** |

    마지막 줄이 정직한 한계다. 물류 화면의 행 수는 그 화면을 열어야 알 수 있고,
    화면을 여는 것 자체가 그 단계의 일이다. 확인창을 띄우려고 미리 열면
    "확인 전에 이미 조작한" 것이 된다. 그래서 읽지 않고, 정책 문구만 보여 준다.
    """
    if step.id == steps_erpia.EXCEL.id and uploads is not None:
        if not uploads:
            return "올릴 엑셀이 없다."
        names = ", ".join(site for site, _ in uploads[:5])
        more = "" if len(uploads) <= 5 else f" 외 {len(uploads) - 5}건"
        return f"올릴 엑셀 {len(uploads)}건 — {names}{more}"
    if step.id == steps_erpia.SALES.id and screen is not None:
        count = unsold_count(screen)
        if count:
            # 읽은 값을 그대로 보여 준다. 우리가 해석해서 줄이면 근거가 약해진다.
            # 값만 찍으면 무엇의 개수인지 알 수 없어서 이름을 붙인다 (실측 '2건').
            return f"미매출 주문수 {count} (0건이면 이미 처리된 것이다)"
    return ""


# --- 화면에 보여 줄 상세 표 ------------------------------------------------
#
# ★ **표는 여기서 만든다.** 공용 GUI 코드(`gui/common.py`)는 배포본마다 기능이
#   갈려서 "엑셀 목록" 인지 "보류 목록" 인지 알 수 없다. 기능을 아는 쪽이 만들어
#   넘기고, 화면은 컬럼 이름과 줄만 받아 그린다 (`steps.StepEvent.columns`).
#
# ★ **없는 숫자를 만들지 않는다.** 그리드 행 수는 가상 스크롤이라 총 건수가
#   아니다(410행이 20행으로 보인 전례). 총계는 ERPia 가 직접 주는 라벨만 쓰고,
#   모르는 칸은 `-` 로 둔다. 대시보드에서 틀린 숫자는 없는 숫자보다 나쁘다.

#
# ★ **사람 말로 쓴다** (사용자 요청 2026-09-21). 사용자는 로그를 읽지 못한다 — 상품코드·
#   pid·행 수 대신 상품명·엑셀명·건수. 건수는 셋 중 하나다: 정확하면 `N건`, 화면 밖 행이
#   있어 끝까지 못 셌으면 `N건 이상`, 못 읽었으면 `확인 못 함` (`count_text`).

UPLOAD_COLUMNS = ("사이트", "엑셀 파일", "결과", "새 주문", "사유", "위치")
SALES_COLUMNS = ("항목", "값")
HOLD_COLUMNS = ("상품명", "부족수량", "결과", "이번에 보류한 주문", "이미 보류돼 있던 주문")
# 시험 실행은 보류를 걸지 않는다 — '보류한' 이라 쓰면 실제로 건 것처럼 읽혔다 (09-21 2차 검토)
HOLD_COLUMNS_TRIAL = ("상품명", "부족수량", "결과", "보류할 주문 (예정)", "이미 보류돼 있던 주문")
SITE_COLUMNS = ("항목", "값")
LOGI_COLUMNS = ("항목", "값")

# 물류대기 상품별 결과(`logistics_wait.hold_shortage_items` 의 state) → 사람 말
HOLD_STATES = {
    "보류함": "배송보류 설정함",
    "보류 확인 실패": "배송보류를 걸었지만 확인되지 않음",
    "이미 보류": "이미 보류돼 있음",
    "대상 없음": "보류할 주문을 찾지 못함",
    "제외": "보류 제외 상품이라 보류 안 함",
    "보류 예정": "보류 예정 (시험 실행)",
}
# 이번에 보류를 건(시험 실행은 걸) 상품. 이것만 적고 센다 (사용자 확정 09-22)
HELD_STATES = ("보류함", "보류 확인 실패", "보류 예정")
UNSOLD_HINT = ("남은 미매출 주문은 상품 매핑이 없거나 매출처리할 수 없는 사유가 있는 "
               "주문입니다. ERPia 주문매핑 화면에서 확인하세요.")
SITE_FAIL_HINT = ("사이트 상태 '실패'는 주문이 없었거나 사이트 로그인이 안 된 경우입니다 "
                  "(ERPia 화면에서도 둘을 구분하지 않습니다).")
NO_WAIT_MENU = "이 계정은 물류대기를 쓰지 않아 건너뜁니다"
NO_SLIPS = "개별배송으로 올릴 주문 0건"
NO_SCREEN = "주문매핑 화면을 열지 못해 건너뜀"
TRIAL = "시험 실행"       # 사용자에게는 'dry-run' 대신 이 말을 쓴다 (09-21 검토)
# ★ "아무것도 바꾸지 않는다" 고 쓰지 않는다 — 물류관리 [자동/수동] 콤보는 시험 실행에서도
#   맞췄다가 끝에 되돌린다 (`logistics.run`). 되돌리기가 실패할 수 있어 바꾸지 않는 것만 적는다.
TRIAL_NOTICE = (f"{TRIAL}입니다 — 엑셀 업로드·가져오기·매출처리·배송보류·저장은 누르지 않고 "
                "무엇을 하게 될지만 확인합니다.")
# 끝 결과(알림·자동 실행 기록)의 첫 줄. 이것이 없으면 시험 결과를 실제 처리로 읽었다 (09-21 2차 검토).
TRIAL_SUMMARY = f"{TRIAL} — ERPia의 주문·전표·보류는 바꾸지 않았습니다"

# 멈춘 뒤 **다시 실행하면** 이미 끝난 단계가 어떻게 되나 (`Hooks.rerun_notes`).
# 근거는 각 단계의 `replay` (steps_erpia / steps_collect). **확인된 것만** 적는다 —
# 자동수집이 같은 주문을 두 번 넣지 않는지는 확인한 적이 없어 적지 않는다.
RERUN_COMMON = {
    # 받아 둔 엑셀은 매니페스트에 남아 다음 실행에서 올린다 (09-21 2차 검토: 빠지는 줄 알았다)
    "mailbox": "받은 메일은 다시 받지 않고, 받아 둔 엑셀은 그대로 씁니다.",
    "sales": "이미 매출처리한 주문은 다시 처리하지 않습니다.",
    "logistics_wait": "이미 건 배송보류는 그대로 둡니다.",
}
RERUN_FULL = {**RERUN_COMMON,
              "excel": "이미 올린 엑셀은 다시 올리지 않습니다 (못 올린 것만 다시 올립니다)."}
RERUN_FOLDER = {**RERUN_COMMON,
                "excel": "폴더의 엑셀을 다시 올립니다 — 이미 올린 엑셀은 폴더에서 빼 두세요."}


def count_text(value: int | None, exact: bool = True) -> str:
    """건수 한 칸. 모르면 `확인 못 함`, 끝까지 못 셌으면 `N건 이상`. **지어내지 않는다.**"""
    if value is None:
        return "확인 못 함"
    return f"{value}건" if exact else f"{value}건 이상"


def added_text(values: list) -> str:
    """파일(또는 회차)별 새 주문 수를 합친 한 마디. 일부만 알면 그렇다고 쓴다."""
    if not values:
        return ""
    known = [value for value in values if value is not None]
    if len(known) == len(values):
        return f"새 주문 {sum(known)}건"
    if known:
        return f"새 주문 {sum(known)}건 이상 (일부 확인 못 함)"
    return "새 주문 수 확인 못 함"


def product_label(item: dict) -> str:
    """상품은 **이름**으로 보여 준다. 이름을 못 읽었을 때만 코드."""
    return item.get("name") or item.get("code") or "-"


def upload_table(results: list[dict]) -> list[tuple]:
    """엑셀수집 — 파일별로 한 줄. 파일명과 폴더는 리포트에서 눌러 열 수 있다."""
    rows = []
    for entry in results:
        path = entry.get("path")
        # dry-run 은 누르지 않고 끝나 ok 가 된다. **성공이라고 쓰지 않는다.**
        if not entry.get("ok"):
            outcome, added = "못 올림", "-"
        elif entry.get("dry_run"):
            outcome, added = f"안 올림 ({TRIAL})", "-"
        else:
            outcome, added = "올림", count_text(entry.get("added"))
        folder = Path(path).parent if path else None
        rows.append((
            entry.get("site") or "-",
            report.path_link(path),
            outcome,
            added,
            friendly.upload_reason(entry.get("error") or "") or entry.get("warning") or "",
            report.path_link(folder, str(folder)) if folder else "-",
        ))
    return rows


def site_detail(result: dict, dry_run: bool) -> str:
    if dry_run:
        return f"확인만 함 ({TRIAL}) · [가져오기] 안 누름"
    added = result.get("added")
    text = f"새 주문 {added}건" if added is not None else "수집 끝 (새 주문 수 확인 못 함)"
    failures = result.get("failures") or {}
    if failures:
        names = result.get("failed_names") or []
        text += f" · 사이트 {site_failure_summary(failures)}"
        if names:
            text += f": {', '.join(names)}"
    if result.get("counts") and not result.get("counts_exact", True):
        # 수집 로그를 끝까지 못 봤다 (`collect_counts_all`). 실패 종류가 여럿이어도 한 번만 적는다
        text += " (사이트 상태는 일부만 읽음 — 더 있을 수 있음)"
    return text


def site_table(result: dict) -> list[tuple]:
    """자동수집 — 새로 들어온 주문 수 + 상태별 사이트 수.

    ★ `성공`/`수집중`/`실패` 는 **완료 여부가 아니다** (사용자 확정 2026-09-15).
      그래서 상태 이름을 그대로 보여 준다. 수집로그는 **끝까지 내려** 센다(09-22) —
      못 내렸으면 `counts_exact` 가 False 라 "N건 이상". 사이트 단위도 `건` (사용자 확정 09-22).
    """
    counts = result.get("counts") or {}
    rows = [("새로 들어온 주문", count_text(result.get("added")))]
    rows += [(f"사이트 — {state}", count_text(count, result.get("counts_exact", True)))
             for state, count in sorted(counts.items())]
    if result.get("failed_names"):
        rows.append(("상태가 '실패'인 사이트", ", ".join(result["failed_names"])))
    if result.get("failures"):
        rows.append(("참고", SITE_FAIL_HINT))
    return rows


def _unsold(result: dict) -> tuple[int | None, int | None]:
    return count_in(result.get("before") or ""), count_in(result.get("after") or "")


def sales_detail(result: dict, dry_run: bool = False) -> str:
    """매출처리 한 줄. 처리 건수 = 미매출 주문수 처리 전 − 후 (ERPia 라벨)."""
    if result.get("skipped"):
        return "매출처리할 주문이 없어 건너뜀"
    before, after = _unsold(result)
    selected = result.get("selected")       # 조회된 주문수 — 선택주문 방식만 넣는다. 전체(단추)는 없다 → "주문 N건"
    if dry_run:
        target = f"조회된 주문 {selected}건 · " if selected is not None else ""
        return (f"확인만 함 ({TRIAL}) · [매출처리] 안 누름 · {target}"
                f"지금 ERPia에 있는 미매출 주문 {count_text(before)}")
    done = before - after if before is not None and after is not None \
        and before >= after else None
    if done is not None and selected is not None:
        text = f"조회된 주문 {selected}건 중 {done}건 매출처리"
    else:
        text = (f"주문 {done}건 매출처리" if done is not None
                else "매출처리함 (처리 건수 확인 못 함)")
    if after:
        text += f" · 남은 미매출 주문 {after}건"
    return text


def sales_table(result: dict, dry_run: bool = False) -> list[tuple]:
    """매출처리 — ERPia 가 준 `미매출 주문수` 라벨과 팝업 문구를 옮긴다."""
    if result.get("skipped"):
        return [("처리", "건너뜀 — 매출처리할 주문이 없음")]
    before, after = _unsold(result)
    done = before - after if before is not None and after is not None \
        and before >= after else None
    rows = [("매출처리 대상 (조회된 주문)", count_text(result.get("selected")))] \
        if result.get("selected") is not None else []
    rows += [("처리 전 미매출 주문", count_text(before)),
             ("처리 후 미매출 주문", "-" if dry_run else count_text(after)),
             ("이번에 매출처리", f"누르지 않음 ({TRIAL})" if dry_run else count_text(done))]
    for text in result.get("dialogs") or []:
        rows.append(("ERPia 메시지", text))
    if after and not dry_run:
        rows.append(("참고", UNSOLD_HINT))
    return rows


def hold_detail(result: dict, dry_run: bool = False) -> str:
    """물류대기 한 줄 — 보류한 상품(이름)·주문, 저장.

    부족 상품은 **이번에 보류를 걸었을 때만** 적는다 (사용자 확정 09-22 — 이미 보류돼
    있거나 안 건 상품까지 적으면 필요 없는 글이다).
    """
    if result.get("no_menu"):
        return NO_WAIT_MENU
    parts = [f"확인만 함 ({TRIAL})"] if dry_run else []
    held = [item for item in result.get("items") or [] if item.get("state") in HELD_STATES]
    if held:
        orders = held_orders(held)
        names = ", ".join(product_label(item) for item in held[:3]) + (
            f" 외 {len(held) - 3}종" if len(held) > 3 else "")
        verb = "보류 예정" if dry_run else "배송보류"
        # 상품 이름은 '종' 옆에 — 주문 수 뒤에 두면 주문 이름으로 읽혔다 (09-21 2차 검토)
        parts.append(f"재고 부족으로 {verb} 상품 {len(held)}종({names}) · 주문 {orders}건")
    saved = result.get("saved") or 0
    if dry_run:
        parts.append("[저장] 안 누름")
    elif result.get("save_message"):
        # 저장 뒤 알림이 떴다 — '넘길 주문 없음' 으로 쓰면 확인할 것과 어긋난다 (09-22 검토)
        parts.append("저장 뒤 ERPia 알림이 떠 저장되지 않았을 수 있음")
    elif result.get("saved_exact"):
        # 물류대기 [저장] = 보류 안 된 주문을 물류관리로 넘긴다 (PROCESS.md Phase 8→9).
        parts.append(f"물류관리로 넘긴 주문 {saved}건" if saved else "물류관리로 넘길 주문 없음")
    else:
        # 화면 밖 행이 있어 보이는 행 차이는 건수가 아니다 (`save_general` 의 info).
        parts.append("물류관리로 넘김 (건수는 화면에 다 보이지 않아 셀 수 없음)" if saved
                     else "저장 (건수 확인 못 함)")
    return " · ".join(parts)


def _item_slips(item: dict, key: str = "slips", rows_key: str = "checked") -> set[str]:
    """한 상품의 주문(전표) 집합. 번호를 못 읽은 행은 `logistics_wait` 가 행마다 자리표를 넣는다.
    전표 목록이 없는 결과(예전 형식)면 행 수만큼 자리표 — 한 상품의 행은 전표마다 하나다."""
    if key in item:
        return set(item[key] or [])
    return {f"?{item.get('code')}:{index}" for index in range(item.get(rows_key) or 0)}


def held_orders(held: list[dict]) -> int:
    """보류한 **주문(전표) 수** — 상품끼리 겹치는 전표는 한 번만 (09-29 사용자 지적: 행이 아니라 전표)."""
    return len(set().union(*(_item_slips(item) for item in held)))


def held_order_counts(held: list[dict]) -> tuple[int, int, int]:
    """`(전체, 보류 확인, 확인 실패)` 주문 수 — **전표마다 한 번만** 가른다. 한 전표에 실패한 상품이
    하나라도 있으면 실패, 전부 '보류함' 이면 확인. 그래서 확인 + 실패 ≤ 전체다 (09-29 검토 — 상품별로
    따로 세면 한 전표가 양쪽에 들어가 '1/1건 (성공 1, 실패 1)' 이 됐다)."""
    slips: set[str] = set()
    failed: set[str] = set()
    not_ok: set[str] = set()
    for item in held:
        state = item.get("state") or ""
        mine = _item_slips(item)
        slips |= mine
        if "실패" in state:
            failed |= mine
        if state != "보류함":
            not_ok |= mine
    return len(slips), len(slips - not_ok), len(failed)


def _slip_count(item: dict) -> int:
    """한 상품의 보류 주문 수 (전표 기준)."""
    return len(_item_slips(item))


def _quantity(value) -> str:
    """부족수량 칸. ERPia 가 준 숫자에 단위를 붙인다 (09-21 검토: 단위가 없어 헷갈렸다)."""
    text = str(value or "").strip()
    return f"{text}개" if text and text[-1].isdigit() else (text or "-")


def hold_table(result: dict) -> list[tuple]:
    """물류대기 — **이번에 보류를 건 상품만** 한 줄씩 (사용자 확정 09-22). 안 건 상품은
    적지 않는다 — 제외·대상 없음은 '확인할 것' 이 말한다 (`logistics_wait_stage`)."""
    return [(product_label(item),
             _quantity(item.get("shortage")),
             HOLD_STATES.get(item.get("state") or "", item.get("state") or "-"),
             f"{_slip_count(item)}건",
             f"{len(_item_slips(item, 'already_slips', 'already'))}건")
            for item in (result.get("items") or []) if item.get("state") in HELD_STATES]


def _boxes_text(result: dict) -> str:
    """만든 개별배송 전표(송장 = 박스) 수. 한 주문이 박스 여럿으로 나뉠 수 있어 주문 수와 다르다."""
    return count_text(result.get("boxes"), result.get("boxes_exact", False))


def logistics_detail(result: dict, dry_run: bool = False) -> str:
    """물류관리 한 줄 — **만든 개별배송 전표(송장) 수** (사용자 확정 09-22) + 올린 주문 수. 못 만들었으면
    '올릴 주문 0건'. 택배사·박스·마지막 버튼 등은 사용자에게 필요 없다 — 문제는 '확인할 것'.
    전표는 박스(송장 = 배송전표)다 — 주문이 아니다 (09-29 사용자 확인·실측 주문 2건 → 송장 31)."""
    if dry_run:
        slips = count_text(result.get("slips"), result.get("slips_exact", False))
        return f"확인만 함 ({TRIAL}) · [저장] 안 누름 · 개별배송으로 올릴 주문 {slips}"
    if result.get("saved"):
        if result.get("boxes") is None:
            return "개별배송 전표(송장) 생성 (건수 확인 못 함)"
        orders = result.get("slips_saved")
        return (f"개별배송 전표(송장) {_boxes_text(result)} 생성"
                + (f" · 주문 {orders}건" if orders is not None else ""))
    if result.get("save_clicked"):
        return "개별배송 전표(송장) 0건 생성 — 저장되지 않음"
    return NO_SLIPS


def logistics_table(result: dict, dry_run: bool = False) -> list[tuple]:
    """물류관리 — 만든 전표(송장)와 올린 주문 (`logistics_detail` 과 같은 이유)."""
    if dry_run:
        slips = count_text(result.get("slips"), result.get("slips_exact", False))
        return [("개별배송으로 올릴 주문", f"{slips} ({TRIAL} — 저장 안 누름)")]
    if result.get("saved"):
        return [("생성한 개별배송 전표(송장)", _boxes_text(result)),
                ("올라간 주문", count_text(result.get("slips_saved")))]
    if result.get("save_clicked"):
        return [("생성한 개별배송 전표(송장)", "0건 — 저장되지 않음")]
    return [("개별배송으로 올릴 주문", "0건")]


def _needs_order_screen(hooks: Hooks) -> bool:
    """이번 실행에 **주문매핑 화면이 필요한가.**

    물류대기·물류관리는 각자 화면을 열고 `target` 만 쓴다. 구간 실행으로 물류만
    할 때 주문매핑을 열면, 이미 물류 화면에 있던 것을 되돌려 놓는 셈이 된다.
    """
    users = (steps_erpia.EXCEL, steps_erpia.SITE, steps_erpia.SALES)
    in_plan = {item.id for item in hooks.plan}
    # 계획에 없는 단계는 세지 않는다. `want()` 는 계획에 없으면 True 를 주므로
    # 그것만 보면 고르지도 않은 수집방식 때문에 화면을 열게 된다.
    return any(item.id in in_plan and hooks.want(item) for item in users)


def collect_all(screen, options: Options, pid: int | None,
                hooks: Hooks, dry_run: bool,
                uploads: list[tuple[str, Path]] | None = None,
                site_failures: dict[str, int] | None = None,
                site_info: dict | None = None,
                on_uploaded=None) -> tuple[int, list[dict]]:
    """고른 방식대로 주문을 수집한다. `(자동수집 후 행 수, 업로드 결과)`.

    `on_uploaded(entry)` 는 엑셀 한 건이 **올라간 순간** 불린다 (`upload_excels`) — 통합 흐름의 소비 표시 (10-02).

    `uploads` 를 주면 그것을 올린다(통합 흐름의 매니페스트). 주지 않으면
    `options.excel_dir` 폴더를 훑는다. 두 흐름이 같은 코드를 쓰게 하려는 것이다.

    `site_failures` 를 주면 **자동수집 실패 집계를 그 dict 에 담아 준다.**
    `site_info` 를 주면 자동수집 결과(새 주문 수·사이트 상태)를 담는다 (`site_detail`).
    부르는 쪽이 요약에 넣을 수 있게 하려는 것이다.
    """
    if site_failures is None:
        site_failures = {}
    if site_info is None:
        site_info = {}
    chosen = options.normalized_sources()
    log.info("주문수집 방식: %s",
             " + ".join(SOURCE_LABELS[s] for s in chosen))

    upload_results: list[dict] = []
    if SOURCE_EXCEL in chosen and not hooks.want(steps_erpia.EXCEL):
        log.info("구간 실행 — 엑셀수집은 시작점보다 앞이다. 하지 않는다.")
    elif SOURCE_EXCEL in chosen:
        items = excel_items_in(options.excel_dir) if uploads is None else uploads
        if items:
            # 끝난 건은 진행 콜백이 여기 모은다 — `upload_results` 는 `upload_excels` 가 돌아온 뒤에야 채워져
            # 도는 동안의 성공·실패 건수에 못 쓴다. `with` **앞에** 둔다 (아래 참고).
            finished: list[dict] = []
            with hooks.stage(steps_erpia.EXCEL, f"엑셀 {len(items)}개") as run:
                # ★ 화면이 "2/3건" 을 **실제 값으로** 그릴 수 있게 한 건씩
                #   알린다. 업로드 동작은 달라지지 않는다
                #   (`upload_excels` 의 `on_item` 주석 참고).

                def told(done, total, site, entry):
                    if entry is not None:
                        finished.append(entry)
                        tell_upload(entry, hooks.running_label())
                    hooks.count(
                        steps_erpia.EXCEL, total=total, done=done,
                        ok=sum(1 for e in finished if e.get("ok")),
                        failed=sum(1 for e in finished if not e.get("ok")),
                        activity=(f"{site} 엑셀을 올리는 중"
                                  if entry is None else
                                  f"{site} — "
                                  f"{'올림' if entry.get('ok') else '못 올림'}"))

                hooks.count(steps_erpia.EXCEL, total=len(items), done=0,
                            activity="엑셀을 올리기 시작합니다")
                upload_results = upload_excels(screen, items, pid=pid,
                                               dry_run=dry_run,
                                               on_item=told, on_uploaded=on_uploaded)
                run.result = upload_results
                run.detail = upload_summary(upload_results)
                run.columns = UPLOAD_COLUMNS
                run.rows = upload_table(upload_results)
                # 통합 흐름(`uploads` 를 줌)은 못 올린 것을 매니페스트에 남겨 다음에 다시 올린다.
                attend_uploads(hooks, upload_results, manifest=uploads is not None)
                hooks.count(
                    steps_erpia.EXCEL, total=len(items),
                    done=len(upload_results),
                    ok=sum(1 for e in upload_results if e.get("ok")),
                    failed=sum(1 for e in upload_results if not e.get("ok")))
            # ★ ERPia 알림을 닫고 넘어왔으면(`Hooks.recover`) `upload_results` 가 비어 있다.
            #   **이미 올린 것을 잃으면** 통합은 `consumed_at` 을 안 찍어 다음에 **또 올린다** (09-22 검토).
            if not upload_results and finished:
                upload_results = list(finished)
                attend_uploads(hooks, upload_results, manifest=uploads is not None)
        else:
            # 건너뛴 것은 실패가 아니다. 화면에도 그렇게 보여야 한다.
            log.warning("올릴 엑셀이 없다. 엑셀수집을 건너뛴다.")
            hooks.skip(steps_erpia.EXCEL, "올릴 엑셀 없음")

    rows = 0
    if SOURCE_SITE in chosen and not hooks.want(steps_erpia.SITE):
        log.info("구간 실행 — 자동수집은 시작점보다 앞이다. 하지 않는다.")
    elif SOURCE_SITE in chosen:
        with hooks.stage(steps_erpia.SITE) as run:
            # 새로 들어온 주문 수 = 미매출 주문수 차이 (그리드 행 수는 가상 스크롤이라
            # 총 건수가 아니다). 실기 확인 09-22·09-29 (docs/archive/HANDOFF_20260928.md·HANDOFF_20260929.md).
            before = None if dry_run else unsold_number(screen)
            rows = collect_orders(screen, source=SOURCE_SITE, pid=pid,
                                  dry_run=dry_run)
            log.info("자동수집 후 조회된 주문 %d건", rows)
            site_result: dict = {"added": None, "counts": {}, "failures": {}}
            if not dry_run:
                after = unsold_number(screen)
                site_result["added"] = added_between(before, after)
                log.info("자동수집 — 미매출 주문수 %s → %s", before, after)
                # 자동수집 실패는 **요약에도** 남긴다. 엑셀업로드 실패만 요약에
                # 나오면, 사이트 수집이 실패한 것을 사용자가 모른 채 지나간다.
                failed_names: list[str] = []
                # 수집로그를 **끝까지 내려** 센다 (09-22). 못 내렸으면 exact=False → "N건 이상"
                counts, exact = collect_counts_all(screen, failed_names=failed_names)
                site_failures.update(collect_failures(counts))
                site_result.update(counts=counts, failures=dict(site_failures),
                                   failed_names=failed_names, counts_exact=exact)
                run.result = site_result
                if site_failures:
                    where = ", ".join(failed_names) or f"{sum(site_failures.values())}건"
                    more = "" if exact else " 수집 로그를 끝까지 보지 못해 더 있을 수 있습니다."
                    # 로그인 정보 확인 안내는 넣지 않는다 — 다른 화면에서 볼 일이다 (사용자 확정 09-22)
                    hooks.attend(f"자동수집 시 실패한 사이트입니다 ({where}) — 주문이 "
                                 f"없었거나 사이트 로그인이 안 된 것입니다.{more}")
                elif counts and not exact:
                    # 보인 부분에 실패가 없어도 **안 보인 부분**에는 있을 수 있다 (09-22 검토)
                    hooks.attend("자동수집 사이트 상태를 끝까지 읽지 못했습니다 — 읽은 사이트에는 "
                                 "'실패'가 없지만 나머지는 ERPia 자동수집 화면에서 확인하세요.")
                # ★ 상태 이름을 그대로 센다. `성공`/`실패` 는 완료 여부가
                #   아니지만(`site_table` 주석), **개수 자체는 실제 값**이다.
                # 끝까지 못 셌으면 넘기지 않는다 — 대시보드가 '12 / 12건 100%' 로 그린다 (09-22 검토)
                if counts and exact:
                    hooks.count(
                        steps_erpia.SITE, total=sum(counts.values()),
                        done=sum(counts.values()),
                        ok=counts.get("성공", 0), failed=counts.get("실패", 0))
                run.columns = SITE_COLUMNS
                run.rows = site_table(site_result)
            else:
                # 시험 실행도 수집로그를 **읽기만** 한다 (지난 수집의 상태). 파일 로그에만 남긴다 —
                # 끝까지 내리는 경로를 실제 화면에서 확인하려는 것이다 (09-22, 스크롤은 실측 전).
                # 참고용이라 실패해도 시험 실행을 멈추지 않는다.
                try:
                    collect_counts_all(screen)
                except Exception as exc:        # noqa: BLE001 — 로그만 남기는 참고 읽기
                    log.warning("시험 실행 — 수집로그를 읽지 못했다: %s", exc)
            run.detail = site_detail(site_result, dry_run)
            site_info.update(site_result)

    return rows, upload_results


def site_failure_summary(failures: dict[str, int]) -> str:
    """자동수집 상태 중 눈에 띄게 남길 것을 한 줄로. 단위는 사이트 수지만 **`건`** 으로 쓴다
    (사용자 확정 09-22).

    ★ `실패` 는 **결함이라는 뜻이 아니다.** 주문이 없어도 실패로 나온다
    (`order_mapping.collect_failures` 의 "수집 상태의 실제 의미").
    """
    return ", ".join(f"{state} {count}건" for state, count in sorted(failures.items()))


def upload_summary(results: list[dict]) -> str:
    """엑셀수집 한 줄. 몇 개 중 몇 개를 올렸고 새 주문이 몇 건인지, 못 올린 사이트."""
    if not results:
        return "올린 엑셀 없음"
    if any(r.get("dry_run") for r in results):
        text = f"엑셀 {len(results)}개 확인 ({TRIAL} — 올리지 않음)"
        warned = [r["site"] for r in results if r.get("warning")]
        return text + (f" · 실제로는 못 올릴 엑셀: {', '.join(warned)}" if warned else "")
    ok = [r for r in results if r["ok"]]
    text = f"엑셀 {len(results)}개 중 {len(ok)}개 올림"
    added = added_text([r.get("added") for r in ok])
    if added:
        text += f" · {added}"
    failed = [r["site"] for r in results if not r["ok"]]
    if failed:
        # 화면에도 어느 사이트가 빠졌는지 남긴다. 숫자만 보면 무엇이 빠졌는지 모른다.
        # 괄호 안에 괄호를 넣지 않는다 (09-21 검토: '…17건 (못 올림: …))' 가 읽기 어려웠다).
        text += f" · 못 올림: {', '.join(failed)}"
    return text


def tell_upload(entry: dict, where: str = "") -> None:
    """엑셀 한 건이 끝날 때마다 사용자 로그에 한 줄. `where` 는 `[7/11 엑셀수집] ` 꼴."""
    name = Path(entry.get("path") or "").name or entry.get("site") or "엑셀"
    if not entry.get("ok"):
        # 사유는 바로 뒤 '확인할 것' 줄이 말한다 (`attend_uploads`). 두 번 쓰지 않는다.
        user.warning("%s엑셀 %s — 못 올림", where, name)
    elif entry.get("dry_run"):
        user.info("%s엑셀 %s — 올릴 대상 확인 (%s)%s", where, name, TRIAL,
                  f" · {entry['warning']}" if entry.get("warning") else "")
    else:
        user.info("%s엑셀 %s — 올림, 새 주문 %s", where, name, count_text(entry.get("added")))


def attend_uploads(hooks: Hooks, results: list[dict], manifest: bool) -> None:
    """못 올린 엑셀을 **확인할 것**에 넣는다. 그 엑셀의 주문은 아직 ERPia 에 없다.

    순서는 **무엇 → 왜·할 일 → 그 뒤** (09-21 2차 검토: '다음 실행 때 다시 올립니다' 가 앞에
    오면 가만히 둬도 올라가는 줄 알았다. 원인을 고치지 않으면 또 못 올린다).
    """
    for entry in results:
        name = Path(entry.get("path") or "").name or entry.get("site") or "엑셀"
        if not entry.get("ok"):
            after = (" 고친 뒤 실행하면 이 엑셀을 다시 올립니다 (고치지 않으면 다음에도 못 올립니다)."
                     if manifest else "")
            hooks.attend(f"{entry.get('site')} 엑셀({name})을 못 올렸습니다. "
                         f"{friendly.upload_reason(entry.get('error') or '')} "
                         f"이 엑셀의 주문은 아직 ERPia에 없습니다 — 오늘 처리해야 하면 "
                         f"ERPia에 직접 올리세요.{after}")
        elif entry.get("warning"):
            hooks.attend(f"{entry.get('site')} 엑셀({name}) — {entry['warning']}. "
                         "이 사이트의 엑셀 비밀번호를 담당자에게 알려 등록해 달라고 하세요. "
                         "등록하지 않으면 실제 실행에서 이 엑셀은 올라가지 않습니다.")


# --- 단계 묶음 — `erpia_flow.run` 과 `full_flow` 가 **같은 코드**를 쓴다 --------------
# 예전에는 두 흐름에 복사본이 있어 어긋났다 (full_flow 에는 물류대기 건수가 빠져 있었다).

def launch_stage(options: Options, hooks: Hooks):
    """프로그램 실행. 이미 실행 중이어도 새 인스턴스를 띄운다.

    ★ `step()` 으로 감싼다. 실행 직후 실패(스플래시·UAC·기존 인스턴스)는
      화면을 봐야 아는 경우가 많은데, 예전에는 스크린샷이 남지 않았다 (2026-09-10 감사).
    """
    with hooks.stage(steps_erpia.LAUNCH):
        with step(log, "프로그램 실행"):
            # UAC 안내처럼 **사람이 지금 해야 할 일**은 사용자 로그에도 남긴다 (`notice`).
            target = start_new_instance(
                options.exe, work_dir=str(Path(options.exe).parent),
                status=hooks.notice, alert=hooks.alert)
        log.info("ERPia pid=%s", target.pid)
    return target


def login_stage(options: Options, target, hooks: Hooks) -> str:
    """로그인. 메인 창 제목을 돌려준다 (제목에 업체코드·아이디가 있어 화면에는 안 쓴다)."""
    with hooks.stage(steps_erpia.LOGIN) as logged_in:
        # ★ 업데이트가 필요하면 로그인 창 대신 업데이트가 먼저
        #   돈다. 그대로 로그인 창을 찾으면 "창이 없다" 로 끝난다.
        blocked = updater.settle(options.exe, target.pid, status=hooks.notice, alert=hooks.alert)
        main_title = login(options.company, options.user_id, options.password,
                           pid=target.pid)
        if blocked:
            log.info("업데이트 처리 — %s", blocked)
            logged_in.detail = "업데이트를 마친 뒤 로그인"
    return main_title


def popup_closer(target):
    """단계가 실패했을 때 ERPia 메시지 팝업([확인] 하나)을 닫아 보는 함수 (`Hooks.recover`)."""
    return lambda: dialogs.dismiss_message_box(target.main_window())


def skip_order_steps(hooks: Hooks) -> None:
    """주문매핑 화면을 못 열었다 (ERPia 알림을 닫고 넘어왔다). 그 화면을 쓰는 단계는 할 수 없다."""
    planned = {item.id for item in hooks.plan}
    for step in (steps_erpia.EXCEL, steps_erpia.SITE, steps_erpia.SALES):
        if step.id in planned:
            hooks.skip(step, NO_SCREEN)


def sales_stage(screen, target, hooks: Hooks, dry_run: bool) -> tuple[dict, str]:
    """매출처리. `(결과, 요약 조각)`."""
    result: dict = {}       # ERPia 알림을 닫고 넘어오면 블록 안이 안 채워진다 (`Hooks.recover`)
    with hooks.stage(steps_erpia.SALES) as sold:
        # ★ dry_run 을 반드시 넘긴다. 매출처리는 **실제 전표를 만들고
        #   되돌릴 수 없다.** 예전에는 이 인자를 빼먹어서 `--dry-run` 이
        #   실전표를 만들었다 (2026-09-10 감사).
        result = process_sales(screen, main_window=target.main_window(),
                               dry_run=dry_run)
        sold.result = result
        sold.columns, sold.rows = SALES_COLUMNS, sales_table(result, dry_run)
        sold.detail = sales_detail(result, dry_run)
        if result["skipped"]:
            # 수집 0건. **전표를 만들지 않았으므로 완료가 아니라 건너뜀이다.**
            sold.state = steps.SKIPPED
        elif not dry_run:
            before, after = _unsold(result)
            if before is not None and after is not None and before == after:
                # 눌렀지만 처리된 주문이 0건 — 매출처리할 수 없는 미매출 주문만 남아 있다. 전표를 만들지
                # 않았으므로 역시 **건너뜀** (사용량에서 세지 않는다, docs/BILLING.md 1절)
                sold.state = steps.SKIPPED
    return result, sold.detail


def logistics_wait_stage(target, hooks: Hooks, dry_run: bool) -> tuple[dict, str]:
    """물류대기. `(결과, 요약 조각)`."""
    result: dict = {}       # `sales_stage` 와 같은 이유
    with hooks.stage(steps_erpia.LOGI_WAIT) as waited:
        result = logistics_wait.run(target, dry_run=dry_run)
        if result.get("save_message"):
            hooks.attend(f"물류대기 [저장] 뒤 ERPia 알림 '{result['save_message']}' — [확인]을 "
                         "누르고 넘어갔습니다. 저장되지 않았을 수 있으니 ERPia 물류대기 화면에서 "
                         "확인하세요.")
        waited.result = result
        waited.columns = HOLD_COLUMNS_TRIAL if dry_run else HOLD_COLUMNS
        waited.rows = hold_table(result)
        if not waited.rows:
            # 이번에 건 보류가 없다 — 부족 상품 칸을 비워 두지 않고 한 줄로 (사용자 확정 09-22)
            waited.columns = SALES_COLUMNS
            waited.rows = [("배송보류", NO_WAIT_MENU if result.get("no_menu") else
                            "보류할 주문 없음" if dry_run else "이번에 건 배송보류 없음")]
        waited.detail = hold_detail(result, dry_run)
        # ★ 보류를 건 상품이 있을 때만 센다 (09-22 — 안 건 상품까지 세면 '1건 중 0건 성공').
        #   없으면 0 으로 두고 화면은 그리지 않는다.
        items = result.get("items") or []
        held = [item for item in items if item.get("state") in HELD_STATES]
        if result.get("no_menu"):
            # 물류대기를 쓰지 않는 계정이다. 할 일이 없어 **건너뜀**이다.
            waited.state = steps.SKIPPED
        elif not dry_run and not held and result.get("general_rows") == 0:
            # 건 보류도, 저장할 주문도 없었다 — 할 일이 없어 **건너뜀** (사용량에서 세지 않는다,
            # docs/BILLING.md 1절). 지난 보류만 남아 [저장]을 눌렀는데 줄지 않는 경우는 가리지 못한다
            waited.state = steps.SKIPPED
        if held:
            # 주문(전표) 수로 센다 — 요약의 '주문 N건' 과 같은 단위 (09-29, 예전에는 상품 종수였다)
            orders, confirmed, unconfirmed = held_order_counts(held)
            hooks.count(steps_erpia.LOGI_WAIT, total=orders, done=orders,
                        ok=confirmed, failed=unconfirmed)
        for item in items:
            name, state = product_label(item), item.get("state")
            short = _quantity(item.get("shortage"))
            # 이름 뒤에 바로 조사를 붙이지 않는다 (받침에 따라 은/는이 갈린다) — '상품' 을 둔다.
            if state == "제외":
                did = ("실제 실행에서도 배송보류를 걸지 않습니다" if dry_run
                       else "배송보류를 걸지 않았습니다")
                hooks.attend(f"'{name}' 상품은 재고가 {short} 부족하지만 보류 제외 상품이라 "
                             f"{did} — 출고 전에 재고를 확인하세요.")
            elif state == "보류 확인 실패":
                hooks.attend(f"'{name}' 상품에 배송보류를 걸었지만 확인되지 않았습니다 — "
                             "ERPia 물류대기 화면에서 보류가 걸렸는지 확인하세요.")
            elif state == "대상 없음":
                hooks.attend(f"'{name}' 상품은 재고가 {short} 부족한데 보류할 주문을 찾지 "
                             "못했습니다 — ERPia 물류대기 화면에서 확인하세요.")
    return result, f"물류대기: {waited.detail}"


def logistics_stage(target, options: Options, hooks: Hooks,
                    dry_run: bool) -> tuple[dict, str]:
    """물류관리. `(결과, 요약 조각)`."""
    result: dict = {}       # `sales_stage` 와 같은 이유
    with hooks.stage(steps_erpia.LOGI) as shipped:
        result = logistics.run(target, courier=options.courier, box=options.box,
                               mode=options.mode, dry_run=dry_run)
        shipped.result = result
        shipped.columns = LOGI_COLUMNS
        shipped.rows = logistics_table(result, dry_run)
        shipped.detail = logistics_detail(result, dry_run)
        log.info("물류관리 결과 — 송장 %s건 · 주문 %s건 (상단 %s행), 택배사 %s, 박스 %s, %s",
                 result.get("boxes"), result.get("slips_saved"), result.get("registered"),
                 result.get("courier"), result.get("box"), result.get("mode"))
        if result.get("skipped"):
            # 개별배송으로 올릴 전표가 없었다 — 저장하지 않았으므로 완료가 아니라 **건너뜀** (매출처리와 같다.
            # 사용량에서 세지 않는다, docs/BILLING.md 1절)
            shipped.state = steps.SKIPPED
        if result.get("mode_not_restored"):
            hooks.attend(f"{TRIAL}에서 물류관리 [자동/수동]을 '{result['mode']}'(으)로 바꿨는데 "
                         f"원래 값 '{result['mode_not_restored']}'(으)로 되돌리지 못했습니다 — "
                         "ERPia 물류관리 화면에서 직접 맞추세요.")
        if not dry_run:
            if result.get("save_clicked") and not result.get("saved"):
                said = (f"ERPia 알림 '{result['save_message']}' 은 [확인]을 눌러 닫았습니다"
                        if result.get("save_message") else
                        "ERPia 화면에 뜬 안내(배송요금·주소 누락 등)를 확인하세요")
                hooks.attend(f"물류관리 [저장]을 눌렀지만 저장되지 않았습니다 — {said}. "
                             f"[{result.get('finish') or '운송장출력'}]은 누르지 않았습니다.")
                # 저장 실패는 사용량에서 뺀다 (실패는 세지 않는다 — 서버 9절이 failed_items 를 본다).
                # 단계는 그대로 둔다: 실패로 바꾸면 '끝나지 않은 단계' 요약이 붙어 사실과 다르다 (저장 버튼은 눌렀다)
                hooks.count(steps_erpia.LOGI, failed=1)
            if result.get("slips_left"):
                hooks.attend(f"물류관리 아래 목록에 주문 {result['slips_left']}건이 남았습니다 "
                             "(배송보류 등으로 올라가지 않은 주문) — ERPia 물류관리 화면에서 확인하세요.")
            if result.get("skipped") and result.get("slips"):
                hooks.attend(f"물류관리 발송 대기 주문 {count_text(result['slips'], result.get('slips_exact', False))}"
                             " 중 개별배송으로 올라간 것이 없습니다 — 배송보류가 걸려 있는지 확인하세요.")
    return result, f"물류관리: {shipped.detail}"


def run(options: Options, hooks: Hooks | None = None) -> Result:
    hooks = hooks or Hooks()
    hooks.recover = None        # 로그인 **뒤에** 넣는다. 실행·로그인 실패는 원래대로 멈춘다
    chosen = options.normalized_sources()   # 틀렸으면 프로그램을 띄우기 전에 막는다
    normalized_sales_mode(getattr(SETTINGS, "sales_mode", None))     # 매출처리 방식도 (10-02)
    # 구간 실행은 화면이 아니라 **옵션**으로 온다. 화면이 없는 경로(콘솔 도구)도
    # 같은 방법으로 쓸 수 있어야 한다.
    if options.start_step:
        hooks.start_step = options.start_step
    # ERPia 단독은 폴더의 엑셀을 **다시 올린다** (매니페스트 보호가 없다).
    hooks.rerun_notes = hooks.rerun_notes or dict(RERUN_FOLDER)
    # 이번 실행이 지나갈 단계를 **먼저** 알린다. 고르지 않은 수집방식은 들어가지 않는다.
    hooks.begin(steps_erpia.plan(chosen))
    if options.dry_run:
        user.info(TRIAL_NOTICE)

    # 이 블록 안에서 도는 **모든 대기가 중단 토큰을 본다** (`utils/cancel.py`).
    with hooks.running():
        if hooks.want(steps_erpia.LAUNCH):
            # 이미 실행 중이어도 새 인스턴스를 띄운다.
            # ★ `step()` 으로 감싼다. 실행 직후 실패(스플래시·UAC·기존 인스턴스)는
            #   화면을 봐야 아는 경우가 많은데, 예전에는 스크린샷이 남지 않았다
            #   (2026-09-10 감사).
            target = launch_stage(options, hooks)
        else:
            # 구간 실행. 앞 단계가 이미 됐다는 전제이므로 **붙는다.**
            target = attach(options.exe)
        hooks.on_target(target)

        if hooks.want(steps_erpia.LOGIN):
            main_title = login_stage(options, target, hooks)
        else:
            main_title = target.main_window().window_text()
        hooks.on_login(main_title)
        # 여기부터 단계가 실패해도 ERPia 알림([확인] 하나)이 떠 있으면 닫고 다음 단계로 (09-22)
        hooks.recover = popup_closer(target)

        # 주문매핑 화면은 **뒷 단계가 쓰기 때문에** 확보한다. 재진입이 무해해서
        # (6-2 표: 안전) 이미 열려 있으면 그대로 쓴다. 다만 물류 단계만 할 때는
        # 이 화면이 필요 없으므로 열지 않는다 — 열면 물류 화면에서 되돌아온다.
        screen = None
        if _needs_order_screen(hooks):
            with hooks.stage(steps_erpia.SCREEN) as entered:
                screen = enter_order_mapping(target)
                if not hooks.want(steps_erpia.SCREEN):
                    entered.detail = "구간 실행 — 뒷 단계에 필요해서 확인만 한다"
        else:
            hooks.skip(steps_erpia.SCREEN, "구간 실행 — 물류 단계에는 필요 없다")

        # 위험한 구간을 시작점으로 골랐으면 **여기서** 확인받는다.
        # 화면을 확보한 뒤라야 "지금 무엇이 남아 있는지" 를 함께 보여 줄 수 있다.
        first = hooks.first_wanted()
        if first is not None and first.needs_confirm:
            preview = None
            if first.id == steps_erpia.EXCEL.id and SOURCE_EXCEL in chosen:
                # ERPia 를 건드리지 않고 알 수 있다. 폴더만 본다.
                preview = excel_items_in(options.excel_dir)
            hooks.approve_start(first, screen_state_for(first, screen, preview))

        site_failures: dict[str, int] = {}
        site_info: dict = {}
        # 화면 진입이 ERPia 알림으로 끝나지 않았으면 (`Hooks.recover`) 그 화면을 쓰는 단계는 못 한다
        screen_lost = screen is None and _needs_order_screen(hooks)
        if screen_lost:
            skip_order_steps(hooks)
            rows, upload_results = 0, []
        else:
            rows, upload_results = collect_all(screen, options, target.pid, hooks,
                                               dry_run=options.dry_run,
                                               site_failures=site_failures,
                                               site_info=site_info)
        log.info("주문수집 완료 — 조회된 주문 %d건", rows)

        # ★ 요약을 **조각으로 모은다.** 구간 실행에서는 안 한 단계가 있는데,
        #   예전처럼 이어 붙이면 하지도 않은 단계가 요약에 남는다.
        parts: list[str] = []
        if upload_results:
            parts.append(upload_summary(upload_results))
        if site_info:
            parts.append(f"자동수집: {site_detail(site_info, options.dry_run)}")

        result: dict = {}
        if screen_lost:
            parts.append(f"주문수집·매출처리: {NO_SCREEN}")
        elif hooks.want(steps_erpia.SALES):
            result, text = sales_stage(screen, target, hooks, options.dry_run)
            parts.append(text)

        wait_result: dict = {}
        if hooks.want(steps_erpia.LOGI_WAIT):
            wait_result, text = logistics_wait_stage(target, hooks, options.dry_run)
            parts.append(text)

        logi: dict = {}
        if hooks.want(steps_erpia.LOGI):
            logi, text = logistics_stage(target, options, hooks, options.dry_run)
            parts.append(text)

        # 알림을 닫고 넘어간 단계가 있으면 맨 앞에 — 없으면 "완료" 로 읽힌다 (09-22 검토)
        if hooks.unfinished_note():
            parts.insert(0, hooks.unfinished_note())
        if options.dry_run:
            parts.insert(0, TRIAL_SUMMARY)
        summary = " / ".join(parts) or "실행한 단계가 없습니다"

        # 저장까지 끝난 뒤에 **맨 마지막으로** 업로드 실패를 다시 적는다.
        # 중간 로그는 수백 줄에 묻힌다. 사람이 로그 끝만 봐도 알 수 있어야 한다.
        if upload_results:
            log_upload_report(upload_results)
        if site_failures:
            # 업로드 실패와 **같은 자리**에 남긴다. 로그 끝만 봐도 알 수 있어야 한다.
            log.warning("오늘 자동수집 상태 — %s. 주문이 없었거나 로그인에 "
                        "실패한 것이다(결함이 아닐 수 있다).",
                        site_failure_summary(site_failures))

        return Result(summary=summary, target=target,
                      details={"rows": rows, "uploads": upload_results,
                               "sales": result, "site_failures": site_failures,
                               "logistics_wait": wait_result, "logistics": logi},
                      steps=hooks.report())
