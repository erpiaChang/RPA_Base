r"""[조사 도구 - 읽기 전용] 물류대기 보류 루프의 **표식 체크가 실제로 판정에 쓰이는지** 본다.

**대상 프로그램을 건드리지 않는다.** 가짜 그리드만 쓴다.

    .venv\Scripts\python.exe -m tools.probe_hold_loop

## 왜 이 도구가 있나 (2026-09-15)

`mark_bottom_row()` 는 하단에 체크를 하나 남기고, 다음 상품에서
`wait_bottom_reloaded()` 가 **그 체크가 풀렸는지로 재조회 완료를 판정**하도록
설계돼 있다. 사용자 질문: **정말 그 체크를 근거로 판정하는가, 그냥 체크만 하는가.**

눈으로는 구별이 안 된다. 둘 다 "체크했다" 로그가 남고, 판정은 대개 통과한다.
그래서 **어느 행에 남기고 어느 행을 읽는지**를 가짜 그리드로 찍어 본다.

실제 루프의 순서는 이렇다 (`hold_shortage_items`).

    check_matching_rows()   ← 하단을 **끝까지** 훑는다. 스크롤이 맨 아래에 남는다
    hold_delivery()
    mark_bottom_row()       ← **지금 보이는 첫 행** 에 표식. 맨 아래 근처다
    --- 다음 상품 ---
    focus_top_row()         ← 안에서 scroll_bottom_top() 으로 **맨 위로** 올린다
    wait_bottom_reloaded()  ← **맨 위 첫 행(1행)** 의 체크를 읽는다

즉 남긴 행과 읽는 행이 다르다. 그 사실을 여기서 확인한다.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from automation import logistics_wait  # noqa: E402
from utils import ui, winprobe  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


class FakeRow:
    """가짜 하단 행. 체크 상태만 들고 있다."""

    def __init__(self, number: int, checked: bool = False, group: bool = False):
        self.number = number
        self.checked = checked
        self.group = group

    def __str__(self) -> str:
        return f"행 {self.number}"


class FakeGrid:
    """가상 스크롤을 흉내낸 가짜 그리드. **보이는 행만** 돌려준다."""

    VIEW = 20

    def __init__(self, count: int):
        self.rows = [FakeRow(n) for n in range(1, count + 1)]
        self.offset = 0          # 맨 위 보이는 행의 인덱스

    def visible(self) -> list[FakeRow]:
        return self.rows[self.offset:self.offset + self.VIEW]

    def scroll_top(self) -> bool:
        self.offset = 0
        return True

    def scroll_to_end(self) -> None:
        """`check_matching_rows` 가 끝까지 훑고 난 상태를 흉내낸다."""
        self.offset = max(0, len(self.rows) - self.VIEW)

    def checked_numbers(self) -> list[int]:
        return [r.number for r in self.rows if r.checked]


@contextmanager
def fake_ui(grid: FakeGrid):
    """`logistics_wait` 가 부르는 `ui` 함수만 가짜로 바꾼다. 끝나면 되돌린다."""
    real = {name: getattr(ui, name) for name in
            ("grid_rows", "is_group_row", "check_row", "check_state",
             "row_number", "scroll_to_top")}
    try:
        ui.grid_rows = lambda g: g.visible()
        ui.is_group_row = lambda row: row.group
        ui.row_number = lambda row: row.number
        ui.check_state = lambda row: row.checked
        ui.scroll_to_top = lambda g: g.scroll_top()

        def check_row(row, checked: bool = True, dry_run: bool = False) -> bool:
            # ★ 실물과 같게 만든다 — **이미 원하는 상태면 누르지 않고 True.**
            #   그래서 이미 체크된 행에 표식을 남기면 아무 일도 일어나지 않는데
            #   로그에는 `표식을 남겼다` 로 남는다 (2026-09-10 실행에서 관찰됐다).
            if row.checked is checked:
                return True
            row.checked = checked
            return True

        ui.check_row = check_row
        yield
    finally:
        for name, func in real.items():
            setattr(ui, name, func)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "그렇다" if ok else "아니다", name,
             f" - {detail}" if detail else "")
    return ok


@contextmanager
def fake_codes(grid: FakeGrid, codes_by_row, busy: list[float]):
    """`wait_bottom_ready` 가 보는 것만 가짜로 바꾼다.

    `codes_by_row` 는 부를 때마다 `{행 번호: 상품코드}` 를 돌려주는 함수다.
    `busy` 는 왕복 시간(초) 목록 — 앞에서부터 하나씩 꺼내 쓴다(바쁨을 흉내낸다).
    """
    real_values = ui.column_values
    real_roundtrip = winprobe.ui_roundtrip
    try:
        ui.column_values = lambda g, column: codes_by_row()
        winprobe.ui_roundtrip = lambda handle, timeout=1.0: (
            busy.pop(0) if busy else 0.0001)
        yield
    finally:
        ui.column_values = real_values
        winprobe.ui_roundtrip = real_roundtrip


def check_new_wait() -> list[bool]:
    """표식을 대신하는 `wait_bottom_ready` 가 실제로 판정하는지 본다."""
    log.info("▶ 새 판정 — wait_bottom_ready")
    out = []
    grid = FakeGrid(20)

    # 1) 고른 코드가 보이면 빨리 끝난다.
    with fake_codes(grid, lambda: {1: "AAA", 2: "AAA", 3: "BBB"}, []):
        started = time.monotonic()
        count = logistics_wait.wait_bottom_ready(grid, "AAA", handle=1)
        spent = time.monotonic() - started
        out.append(check("코드가 보이면 1초 안에 끝난다",
                         count == 3 and spent < 1.0, f"{spent:.2f}초, {count}행"))

    # 2) ★ 행 번호가 같아도 **코드가 다르면** 재조회를 알아본다.
    #    행 번호는 재조회마다 1부터 다시 매겨져서 지문으로 쓸 수 없다.
    changing = [{1: "OLD", 2: "OLD"}, {1: "OLD", 2: "OLD"},
                {1: "NEW", 2: "NEW"}]
    step_box = {"i": 0}

    def next_codes():
        value = changing[min(step_box["i"], len(changing) - 1)]
        step_box["i"] += 1
        return value

    with fake_codes(grid, next_codes, []):
        started = time.monotonic()
        logistics_wait.wait_bottom_ready(grid, "NEW", handle=1)
        out.append(check("행 번호가 같아도 코드가 바뀌면 알아본다",
                         step_box["i"] >= 3,
                         f"{step_box['i']}번 읽고 끝냈다"))

    # 3) ERPia 가 바쁘면 기다린다. 한가해진 뒤에야 끝낸다.
    busy = [0.01] * 6          # 처음 6번은 밀려 있다
    with fake_codes(grid, lambda: {1: "AAA"}, busy):
        started = time.monotonic()
        logistics_wait.wait_bottom_ready(grid, "AAA", handle=1)
        spent = time.monotonic() - started
        out.append(check("ERPia 가 바쁜 동안은 완료로 보지 않는다",
                         spent >= 0.6 and not busy, f"{spent:.2f}초"))

    # 4) 코드가 끝내 안 보이면 **상한을 다 쓰지 않고** 없는 것으로 확정한다.
    with fake_codes(grid, lambda: {1: "OTHER"}, []):
        started = time.monotonic()
        logistics_wait.wait_bottom_ready(grid, "AAA", handle=1, timeout=20.0)
        spent = time.monotonic() - started
        out.append(check("주문이 없으면 상한 20초를 다 쓰지 않는다",
                         2.0 <= spent < 5.0, f"{spent:.2f}초에 확정"))
    return out


def check_scroll_rule() -> list[bool]:
    """수직 스크롤바가 없으면 **스크롤을 시도조차 하지 않는지** 본다."""
    log.info("▶ 스크롤 규칙 — 스크롤바가 없으면 시도하지 않는다")
    out = []
    tried: list[str] = []
    real_bar = ui.vertical_scrollbar
    real_once = ui._scroll_down_once
    real_numbers = ui.visible_row_numbers
    try:
        ui.visible_row_numbers = lambda g: {1, 2, 3}
        ui._scroll_down_once = lambda g, method, before, bar=None: tried.append(method)
        ui.vertical_scrollbar = lambda g: None
        answer = ui.scroll_down_verified(object())
        out.append(check("스크롤바가 없으면 수단을 하나도 시도하지 않는다",
                         answer is False and not tried,
                         f"시도한 수단 {tried or '없음'}"))

        # ★ 스크롤바는 있는데 **맨 아래**인 경우. `페이지 아래로` 가 사라진다.
        #   화살표(`아래로 선`)는 끝에서도 남아 있어서 그것으로 판정하면 안 된다.
        ui.vertical_scrollbar = lambda g: object()
        real_button = ui.scrollbar_button
        try:
            ui.scrollbar_button = lambda g, names, bar=None: (
                None if names is ui.SCROLL_CAN_DOWN_NAMES else object())
            tried.clear()
            answer = ui.scroll_down_verified(object(), rounds=1)
            out.append(check("맨 아래면(`페이지 아래로` 없음) 시도하지 않는다",
                             answer is False and not tried,
                             f"시도한 수단 {tried or '없음'}"))

            ui.scrollbar_button = lambda g, names, bar=None: object()
            tried.clear()
            ui.scroll_down_verified(object(), rounds=1)
            out.append(check("더 내릴 수 있으면 예전처럼 시도한다",
                             bool(tried), f"시도한 수단 {tried}"))
        finally:
            ui.scrollbar_button = real_button
    finally:
        ui.vertical_scrollbar = real_bar
        ui._scroll_down_once = real_once
        ui.visible_row_numbers = real_numbers
    return out


@contextmanager
def fake_sorted_grid(grid: FakeGrid, codes: dict[int, str], holds: dict[int, str],
                     slips: dict[int, str] | None = None):
    """`check_matching_rows` / `verify_holds` 가 읽는 셀·스크롤을 가짜로 바꾼다.

    스크롤은 한 번에 한 화면(VIEW)씩 내려간다. 훑은 화면 수를 `grid.pages` 에 센다.
    `slips` 는 행 번호 → 전표번호 (09-29).
    """
    real = {name: getattr(ui, name) for name in ("columns_values", "scroll_down_verified")}
    grid.pages = 1

    def columns_values(g, columns):
        numbers = [r.number for r in g.visible()]
        out = {}
        for column in columns:
            if column == logistics_wait.BOTTOM_CODE_COLUMN:
                out[column] = {n: codes.get(n, "") for n in numbers}
            elif column == logistics_wait.HOLD_COLUMN:
                out[column] = {n: holds.get(n, "0") for n in numbers}
            elif column == logistics_wait.SLIP_NUMBER_COLUMN:
                out[column] = {n: (slips or {}).get(n, "") for n in numbers}
            else:
                out[column] = {r.number: ui.CHECK_ON if r.checked else "선택안됨"
                               for r in g.visible()}
        return out

    def scroll_down(g, methods=None, rounds=None):
        if g.offset + g.VIEW >= len(g.rows):
            return False
        g.offset += g.VIEW
        g.pages += 1
        return True

    try:
        ui.columns_values = columns_values
        ui.scroll_down_verified = scroll_down
        with fake_ui(grid):
            yield
    finally:
        for name, func in real.items():
            setattr(ui, name, func)


def check_sorted_scan() -> list[bool]:
    """하단 정렬(2026-09-17) — 묶음이 끝나면 멈추고, 안 모였으면 끝까지 본다."""
    log.info("▶ 정렬된 하단 스캔 — 묶음 끝에서 멈춘다")
    out = []
    lw = logistics_wait
    # 60행: 1~20 A / 21~35 B / 36~60 C. 한 화면 20행.
    base = {n: "A" if n <= 20 else "B" if n <= 35 else "C" for n in range(1, 61)}

    def run(code, grouped, codes=base, holds=None):
        grid = FakeGrid(60)
        with fake_sorted_grid(grid, codes, holds or {}):
            checked, _, already = lw.check_matching_rows(
                grid, code, dry_run=True, grouped=grouped)
        return checked, already, grid.pages

    checked, _, pages = run("B", True)
    out.append(check("정렬돼 있으면 B 묶음이 끝난 화면(2)에서 멈춘다",
                     checked == 15 and pages == 2, f"체크 {checked}, 화면 {pages}"))
    checked, _, pages = run("B", False)
    out.append(check("정렬을 모르면 예전처럼 끝까지(3화면) 본다",
                     checked == 15 and pages == 3, f"체크 {checked}, 화면 {pages}"))
    checked, _, pages = run("A", True)
    out.append(check("첫 화면이 전부 A 면 다음 화면에서 끝을 알아본다",
                     checked == 20 and pages == 2, f"체크 {checked}, 화면 {pages}"))

    scattered = dict(base)
    # 헤더는 오름차순이라는데 **훑은 범위 안에서** B 가 다시 나온다 (C 뒤의 38행).
    # ★ 멈춘 지점보다 **뒤에서** 다시 나오는 것은 원리적으로 볼 수 없다 —
    #   그 부분은 헤더 상태(`ensure_bottom_sorted`)를 믿는다.
    scattered[38] = "B"
    checked, _, pages = run("B", True, codes=scattered)
    out.append(check("훑은 범위에서 다시 나온 코드를 보면 끝까지 본다",
                     checked == 16 and pages == 3, f"체크 {checked}, 화면 {pages}"))

    checked, _, pages = run("Z", True)
    out.append(check("대상 코드가 없으면 끝까지 본다(멈출 근거가 없다)",
                     checked == 0 and pages == 3, f"체크 {checked}, 화면 {pages}"))

    held = {n: "1" for n in range(21, 36)}
    checked, already, pages = run("B", True, holds=held)
    out.append(check("이미 보류된 묶음도 끝에서 멈춘다",
                     checked == 0 and already == 15 and pages == 2,
                     f"체크 {checked}, 이미 {already}, 화면 {pages}"))

    # --- 실제로 누르는 경로 (dry_run=False). 범위 체크를 가짜로 바꾼다 ---
    def run_real(range_check):
        grid = FakeGrid(60)
        real_range = lw.check_rows_by_range
        lw.check_rows_by_range = lambda g, rows, pid: range_check(g, rows)
        try:
            with fake_sorted_grid(grid, base, {}):
                checked, _, _ = lw.check_matching_rows(grid, "B", grouped=True)
        finally:
            lw.check_rows_by_range = real_range
        return checked, grid.pages, [n for n in grid.checked_numbers()]

    def range_ok(g, rows):
        for r in rows:
            r.checked = True
        return True

    checked, pages, marked = run_real(range_ok)
    out.append(check("범위 체크가 되면 묶음 끝(2화면)에서 멈춘다",
                     checked == 15 and pages == 2 and marked == list(range(21, 36)),
                     f"체크 {checked}, 화면 {pages}"))

    checked, pages, marked = run_real(lambda g, rows: False)
    out.append(check("범위 체크가 실패하면 하나씩 눌러 다 체크한다",
                     checked == 15 and pages == 2 and marked == list(range(21, 36)),
                     f"체크 {checked}, 화면 {pages}"))

    def range_fails_and_scrolls(g, rows):
        g.offset += 7          # 실패하면서 그리드가 내려가 21~27행이 위로 밀려났다
        return False

    checked, pages, marked = run_real(range_fails_and_scrolls)
    out.append(check("★ 행이 밀려나면 묶음이 끝나도 멈추지 않는다 (끝까지 본다)",
                     pages == 3 and checked == 8,
                     f"체크 {checked} (21~27 은 못 봤다 — 경고가 남는다), 화면 {pages}"))

    grid = FakeGrid(60)
    with fake_sorted_grid(grid, base, {n: "1" for n in range(21, 31)}):
        result = lw.verify_holds(grid, {"B": 15}, grouped=True)
    # 모자라면 잠깐 뒤 **한 번 더** 훑는다 (09-28). 두 번 다 묶음 끝에서 멈춰 화면 1+1+1 = 3
    out.append(check("보류 확인도 모자라면 묶음 끝에서 멈추고(두 번 훑어도) 부족을 알린다",
                     result["short"] == {"B": (15, 10)} and grid.pages == 3,
                     f"부족 {result['short']}, 화면 {grid.pages}"))
    return out


class _FakeHeader:
    def __init__(self, action):
        self.action = action
        self.clicks = 0

    def legacy_properties(self):
        return {"DefaultAction": self.action}


def check_ensure_sorted() -> list[bool]:
    """헤더 상태를 보고 **필요할 때만 한 번** 누르는지 본다."""
    log.info("▶ 하단 정렬 보장 — 필요할 때만 한 번 누른다")
    out = []
    lw = logistics_wait
    real_header, real_click = ui.column_header, ui.click
    try:
        for start, want_ok, want_clicks, name in (
            (lw.SORT_NEXT_DESC, True, 0, "이미 오름차순이면 누르지 않는다"),
            (lw.SORT_NEXT_ASC, True, 1, "정렬이 없으면 한 번 누른다"),
            (lw.SORT_NEXT_NONE, True, 1, "내림차순('정렬 제거')이면 한 번 누른다"),
            ("알 수 없음", False, 0, "모르는 상태면 누르지 않고 끝까지 훑게 한다"),
        ):
            header = _FakeHeader(start)
            ui.column_header = lambda g, column, h=header: h
            # 실물처럼 누를 때마다 없음 → 오름 → 내림 → 오름 으로 바뀐다.
            cycle = {lw.SORT_NEXT_ASC: lw.SORT_NEXT_DESC,
                     lw.SORT_NEXT_DESC: lw.SORT_NEXT_NONE,
                     lw.SORT_NEXT_NONE: lw.SORT_NEXT_DESC}

            def click(ctrl, what, dry_run=False, methods=None, h=header):
                h.clicks += 1
                h.action = cycle[h.action]
                return "클릭"

            ui.click = click
            ok = lw.ensure_bottom_sorted(object())
            out.append(check(name, ok is want_ok and header.clicks == want_clicks,
                             f"결과 {ok}, 누른 수 {header.clicks}"))

        # 눌렀는데 상태가 안 바뀌면 상한 뒤 False (정렬을 믿지 않는다)
        header = _FakeHeader(lw.SORT_NEXT_ASC)
        ui.column_header = lambda g, column: header
        ui.click = lambda ctrl, what, dry_run=False, methods=None: "클릭"
        real_timeout = lw.SORT_SETTLE_TIMEOUT
        lw.SORT_SETTLE_TIMEOUT = 0.5
        try:
            ok = lw.ensure_bottom_sorted(object())
        finally:
            lw.SORT_SETTLE_TIMEOUT = real_timeout
        out.append(check("눌러도 오름차순이 안 되면 False", ok is False, f"결과 {ok}"))

        def missing(g, column):
            raise ui.ControlNotFound("헤더 없음")

        ui.column_header = missing
        ok = lw.ensure_bottom_sorted(object())
        out.append(check("헤더를 못 찾으면 False (예외를 올리지 않는다)", ok is False,
                         f"결과 {ok}"))
    finally:
        ui.column_header, ui.click = real_header, real_click
    return out


@contextmanager
def fake_paged_grid(grid: FakeGrid, column: str, values: dict[int, str]):
    """`ui.scan_column` 이 보는 것 — 셀·행 번호·스크롤·스크롤바 — 을 가짜 그리드로 (09-29)."""
    names = ("columns_values", "visible_row_numbers", "scroll_down_verified",
             "vertical_scrollbar", "scrollbar_button", "has_hidden_rows")
    real = {name: getattr(ui, name) for name in names}
    at_end = lambda g: g.offset + g.VIEW >= len(g.rows)      # noqa: E731

    def scroll_down(g, methods=None, rounds=None):
        if at_end(g):
            return False
        g.offset += g.VIEW
        return True

    try:
        ui.columns_values = lambda g, cols: {c: {r.number: values.get(r.number, "") for r in g.visible()
                                                 if not r.group} for c in cols}
        ui.visible_row_numbers = lambda g: {r.number for r in g.visible()}
        ui.scroll_down_verified = scroll_down
        ui.vertical_scrollbar = lambda g: object() if len(g.rows) > g.VIEW else None
        ui.scrollbar_button = lambda g, names, bar=None: (
            None if names is ui.SCROLL_CAN_DOWN_NAMES and at_end(g) else object())
        ui.has_hidden_rows = lambda g: len(g.rows) > g.VIEW
        with fake_ui(grid):
            yield
    finally:
        for name, func in real.items():
            setattr(ui, name, func)


def check_slip_counts() -> list[bool]:
    """건수는 행이 아니라 **전표**로 센다 (09-29 사용자 지적). 가짜 그리드·가짜 함수로."""
    log.info("▶ 전표 수 — 행 수가 아니다 (09-29)")
    out = []
    lw = logistics_wait
    from orchestrator import erpia_flow

    # 45행 = 그룹 행 1·23 + 상품 행 43. 전표 하나에 상품 3행 → 전표 15개. 한 화면 20행이라 3화면
    grid = FakeGrid(45)
    for number in (1, 23):
        grid.rows[number - 1].group = True
    goods = [r.number for r in grid.rows if not r.group]
    values = {n: f"S{i // 3 + 1}" for i, n in enumerate(goods)}
    with fake_paged_grid(grid, "전표번호", values):
        count, exact = ui.distinct_count(grid, "전표번호")
        back = grid.offset
        _, capped = ui.scan_column(grid, "전표번호", pages=1)
    out.append(check("★ 45행(그룹 2 + 상품 43) = 전표 15 — 끝까지 내려 중복 없이, 정확",
                     count == 15 and exact is True and back == 0, f"{count} / {exact} / 되돌림 {back}"))
    out.append(check("한 화면만 보고 멈추면 정확하지 않다", capped is False))
    empty = FakeGrid(0)
    with fake_paged_grid(empty, "전표번호", {}):
        out.append(check("빈 그리드는 0건이 정확한 값", ui.distinct_count(empty, "전표번호") == (0, True)))

    # [일반] 탭 — 전표번호는 가로로 넘겨 읽고, 못 찾아도 반드시 맨 앞으로 되돌린다
    calls: list = []
    saved = (ui.reveal_column, ui.distinct_count, ui.columns_home)
    try:
        ui.columns_home = lambda g: calls.append("home")
        ui.distinct_count = lambda g, c: (calls.append(c), (2, True))[1]
        ui.reveal_column = lambda g, c: False
        missing = lw.count_general_slips(object())
        missing_calls = list(calls)
        calls.clear()
        ui.reveal_column = lambda g, c: True
        found = lw.count_general_slips(object())
    finally:
        ui.reveal_column, ui.distinct_count, ui.columns_home = saved
    out.append(check("[일반] 전표번호를 못 찾으면 모른다(None) + 가로 스크롤은 되돌린다",
                     missing == (None, False) and missing_calls == ["home"], str(missing_calls)))
    out.append(check("[일반] 찾으면 전표번호로 센 뒤 되돌린다",
                     found == (2, True) and calls == ["전표번호", "home"], str(calls)))

    # 저장 — 넘긴 수 = 저장 전후 전표 차이 (8행 = 전표 2, 09-29 실측 모양)
    def save(slip_counts):
        rows = iter([8, 0])
        slips = iter(slip_counts)
        names = ("activate_tab", "click_search", "_general_grid", "_wait_rows",
                 "count_general_slips", "_screen_pid", "process_handle")
        real = {name: getattr(lw, name) for name in names}
        real_ui = (ui.header_select_all, ui.find, ui.click, ui.visible_row_count)
        info: dict = {}
        try:
            lw.activate_tab = lambda s, name: None
            lw.click_search = lambda s: None
            lw._general_grid = lambda s: object()
            lw._wait_rows = lambda g, what, timeout=None: next(rows)
            lw.count_general_slips = lambda g: next(slips)
            lw._screen_pid = lambda s: None
            lw.process_handle = lambda pid: 0
            ui.header_select_all = lambda g, dry_run=False: "클릭"
            ui.find = lambda *a, **k: object()
            ui.click = lambda *a, **k: "클릭"
            ui.visible_row_count = lambda g: 0
            moved = lw.save_general(object(), info=info)
        finally:
            for name, func in real.items():
                setattr(lw, name, func)
            ui.header_select_all, ui.find, ui.click, ui.visible_row_count = real_ui
        return moved, info.get("exact")

    out.append(check("★ [일반] 8행 저장 → 넘긴 주문 2건 (행 차이 8 이 아니다), 정확",
                     save([(2, True), (0, True)]) == (2, True), str(save([(2, True), (0, True)]))))
    out.append(check("전표 수를 모르면 행 차이는 '저장됨' 표시일 뿐 — 정확하지 않다고 알린다",
                     save([(None, False), (0, True)]) == (8, False)))

    # 보류 — 부족 상품 둘이 같은 전표에 있으면 주문은 한 번만. 번호를 못 읽은 행은 행마다 자리표(`?상품:행`)
    held = [{"code": "A", "slips": ["OT1", "OT2"], "checked": 2},
            {"code": "B", "slips": ["OT2", "OT3"], "checked": 2},
            {"code": "C", "slips": ["?C:5"], "checked": 1}]
    out.append(check("★ 보류 주문 = 전표 중복 없이 (OT1·OT2·OT3 + 번호 못 읽은 행 1) = 4 (행 합 5 가 아니다)",
                     erpia_flow.held_orders(held) == 4, str(erpia_flow.held_orders(held))))
    out.append(check("전표 목록이 없는 예전 결과는 행 수만큼 센다",
                     erpia_flow.held_orders([{"code": "D", "checked": 2}]) == 2))
    text = erpia_flow.hold_detail({"items": [dict(item, state="보류함", name=f"상품{item['code']}")
                                             for item in held],
                                   "saved": 2, "saved_exact": True})
    out.append(check("요약도 주문 4건 · 넘긴 주문 2건", "주문 4건" in text and "넘긴 주문 2건" in text, text))
    mixed = [dict(held[0], state="보류함"), dict(held[1], state="보류 확인 실패")]
    total, ok, failed = erpia_flow.held_order_counts(mixed)
    out.append(check("★ 진행 건수 — 한 전표가 확인·실패 양쪽에 들어가지 않는다 (확인 + 실패 ≤ 전체)",
                     (total, ok, failed) == (3, 1, 2) and ok + failed <= total, f"{total}/{ok}/{failed}"))

    # 매출처리 — 대상은 `조회된 주문수` 라벨 (3행 = 그룹 1 + 주문 2 → 2건), 처리 수는 미매출 라벨 차이
    sold = {"before": "3건", "after": "1건", "selected": 2, "dialogs": [], "skipped": False}
    text = erpia_flow.sales_detail(sold)
    table = dict(erpia_flow.sales_table(sold))
    out.append(check("★ 매출처리 — '조회된 주문 2건 중 2건 매출처리' + 표에 대상 줄",
                     text.startswith("조회된 주문 2건 중 2건 매출처리")
                     and table.get("매출처리 대상 (조회된 주문)") == "2건", f"{text} / {table}"))
    out.append(check("라벨을 못 읽었으면 예전 문구 그대로",
                     erpia_flow.sales_detail(dict(sold, selected=None)).startswith("주문 2건 매출처리")))

    # 하단에서 체크한 행의 전표번호를 모은다
    grid = FakeGrid(60)
    base = {n: "A" if n <= 20 else "B" if n <= 35 else "C" for n in range(1, 61)}
    slip_map = {n: f"OT{n}" for n in range(1, 61)}
    collected: set = set()
    with fake_sorted_grid(grid, base, {}, slips=slip_map):
        checked, _, _ = lw.check_matching_rows(grid, "B", dry_run=True, grouped=True, slips=collected)
    out.append(check("체크한 행의 전표번호를 모은다 (B 15행 → 전표 15개)",
                     checked == 15 and collected == {f"OT{n}" for n in range(21, 36)}, f"{len(collected)}"))
    # 번호를 일부 못 읽고(24·25행), 일부는 이미 보류(30~35행)
    grid = FakeGrid(60)
    partial = {n: v for n, v in slip_map.items() if n not in (24, 25)}
    checked_slips: set = set()
    already_slips: set = set()
    with fake_sorted_grid(grid, base, {n: "1" for n in range(30, 36)}, slips=partial):
        checked, _, already = lw.check_matching_rows(grid, "B", dry_run=True, grouped=True,
                                                     slips=checked_slips, already_slips=already_slips)
    out.append(check("못 읽은 행도 한 건씩 (빠지지 않는다) + 이미 보류 행은 따로 전표로",
                     checked == 9 and len(checked_slips) == 9 and {"?B:24", "?B:25"} <= checked_slips
                     and already == 6 and already_slips == {f"OT{n}" for n in range(30, 36)},
                     f"체크 {checked}/{sorted(checked_slips)[:3]} · 이미 {already}/{len(already_slips)}"))

    # 물류관리 상단 — 박스 줄(배송업체 있음) 아래 상품 줄(배송업체 빈칸). 박스 = 송장 = 배송전표 (09-29 실측:
    # 하단 주문 2건 → 박스 31개, 62줄). 한 박스에 상품이 여러 줄인 모양도 본다
    from automation import logistics
    grid = FakeGrid(62)
    lines = {n: "택배사A" if n % 2 else "" for n in range(1, 63)}
    with fake_paged_grid(grid, logistics.TOP_COURIER_COLUMN, lines):
        boxes = logistics.count_boxes(grid)
    out.append(check("★ 물류관리 박스 = 배송업체가 찬 줄 (62줄 → 31), 끝까지 내려 정확",
                     boxes == (31, True) and grid.offset == 0, f"{boxes} / 되돌림 {grid.offset}"))
    grid = FakeGrid(10)
    lines = {1: "택배사A", 5: "택배사A", 7: "택배사A", 9: "택배사A"}   # 첫 박스에 상품 3줄
    with fake_paged_grid(grid, logistics.TOP_COURIER_COLUMN, lines):
        boxes = logistics.count_boxes(grid)
    out.append(check("상품 여러 줄 박스도 한 건 (10줄 → 4)", boxes == (4, True), str(boxes)))
    grid = FakeGrid(6)
    with fake_paged_grid(grid, logistics.TOP_COURIER_COLUMN, {}):
        boxes = logistics.count_boxes(grid)
    out.append(check("줄은 있는데 배송업체를 하나도 못 읽으면 모른다(None) — 0 이라 하지 않는다",
                     boxes == (None, False), str(boxes)))
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 표식을 남기는 행 vs 읽는 행 (실제 루프 순서)")
    grid = FakeGrid(440)
    with fake_ui(grid):
        grid.scroll_to_end()          # check_matching_rows 가 끝까지 훑은 뒤
        logistics_wait.mark_bottom_row(grid, "상품 A 처리 완료")
        marked = grid.checked_numbers()
        results.append(check("표식은 1행이 아니라 **보이던 첫 행**에 남는다",
                             marked == [421], f"체크된 행 {marked}"))

        logistics_wait.scroll_bottom_top(grid)   # focus_top_row 안에서 하는 일
        started = time.monotonic()
        answer = logistics_wait.wait_bottom_reloaded(grid, timeout=2.0)
        spent = time.monotonic() - started
        results.append(check("판정은 **표식을 남긴 행을 보지 않는다** (1행을 본다)",
                             answer is True and spent < 0.5,
                             f"{spent:.2f}초에 True — 기다리지 않았다"))
        results.append(check("표식은 **그대로 남아 있다** (풀린 것이 아니다)",
                             grid.checked_numbers() == [421],
                             f"체크된 행 {grid.checked_numbers()}"))

    log.info("▶ 거짓 타임아웃 — 1행이 다른 이유로 체크돼 있으면")
    grid = FakeGrid(440)
    with fake_ui(grid):
        # `check_matching_rows` 가 보류 대상으로 1행을 체크한 상태다.
        grid.rows[0].checked = True
        started = time.monotonic()
        answer = logistics_wait.wait_bottom_reloaded(grid, timeout=1.0)
        spent = time.monotonic() - started
        results.append(check("표식과 무관하게 **상한을 다 쓰고 실패**한다",
                             answer is False and spent >= 1.0,
                             f"{spent:.2f}초 — 실제 실행의 `10초 안에 표식이 "
                             "풀리지 않았다` 와 같은 모양"))

    log.info("▶ 이미 체크된 행에 표식을 남기면")
    grid = FakeGrid(40)
    with fake_ui(grid):
        grid.rows[0].checked = True      # 보류 대상으로 이미 체크돼 있다
        before = grid.checked_numbers()
        answer = logistics_wait.mark_bottom_row(grid, "상품 B 처리 완료")
        results.append(check("아무것도 누르지 않았는데 **남겼다고 보고**한다",
                             answer is True and grid.checked_numbers() == before,
                             "새로 체크된 행이 없다"))

    log.info("▶ 표식이 판정에 쓰이는 유일한 경우")
    grid = FakeGrid(12)      # 한 화면(20행)에 다 들어간다 → 보이는 첫 행 = 1행
    with fake_ui(grid):
        logistics_wait.mark_bottom_row(grid, "상품 C 처리 완료")
        marked = grid.checked_numbers()
        logistics_wait.scroll_bottom_top(grid)
        rows_are_same = marked == [1]
        results.append(check("하단이 **한 화면에 다 들어갈 때만** 같은 행을 본다",
                             rows_are_same, f"체크된 행 {marked}"))

    results += check_new_wait()
    results += check_scroll_rule()
    results += check_no_menu()
    results += check_sorted_scan()
    results += check_ensure_sorted()
    results += check_slip_counts()

    log.info("결과: 확인 %d / 어긋남 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


class _FakeTarget:
    def main_window(self):
        return object()


def check_no_menu() -> list[bool]:
    """★ 물류대기를 쓰지 않는 계정 — **건너뛰되, 섣불리 단정하지 않는다** (2026-09-17).

    실측(09-17): 그런 계정은 `imgLbl_HoldLogistics` 가 트리에서
    아예 빠지고 `imgLbl_Logistics` 는 있다.
    """
    log.info("▶ ★ 물류대기가 없는 계정은 건너뛴다")
    real_find, real_open = ui.find, logistics_wait.is_open
    out: list[bool] = []

    def menu(*present):
        def find(parent, auto_id=None, **kwargs):
            if auto_id in present:
                return object()
            raise ui.ControlNotFound(f"{auto_id} 없음")
        return find

    wait_id, logi_id = logistics_wait.SIDEBAR_LOGISTICS_WAIT, logistics_wait.SIDEBAR_LOGISTICS
    try:
        logistics_wait.is_open = lambda main_window: False
        ui.find = menu(wait_id, logi_id)
        out.append(check("물류대기가 있으면 있다고 본다",
                         logistics_wait.menu_available(_FakeTarget()) is True))
        ui.find = menu(logi_id)
        out.append(check("★ 물류처리만 있고 물류대기가 없으면 **없다**",
                         logistics_wait.menu_available(_FakeTarget()) is False))
        ui.find = menu()
        try:
            logistics_wait.menu_available(_FakeTarget())
            out.append(check("★ 둘 다 안 보이면 **단정하지 않고** 멈춘다", False,
                             "예외 없이 넘어갔다"))
        except logistics_wait.ScreenError:
            out.append(check("★ 둘 다 안 보이면 **단정하지 않고** 멈춘다", True))
        logistics_wait.is_open = lambda main_window: True
        out.append(check("화면이 이미 열려 있으면 있다고 본다 (메뉴를 찾지 않는다)",
                         logistics_wait.menu_available(_FakeTarget()) is True))

        logistics_wait.is_open = lambda main_window: False
        ui.find = menu(logi_id)
        result = logistics_wait.run(_FakeTarget(), dry_run=True)
        out.append(check("★ 없으면 run() 이 화면을 열지 않고 `no_menu` 로 돌려준다",
                         bool(result.get("no_menu")) and result["saved"] == 0
                         and result["items"] == [], str(result.get("no_menu"))))
        out.append(check("`skipped`(이미 보류 상품 수)는 숫자 그대로다",
                         result["skipped"] == 0))
    finally:
        ui.find, logistics_wait.is_open = real_find, real_open
    return out


if __name__ == "__main__":
    raise SystemExit(main())
