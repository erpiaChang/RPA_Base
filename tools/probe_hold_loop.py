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
from utils import dialogs, ui, winprobe  # noqa: E402
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


class _FakeLoading:
    """`logistics_wait.Loading` 대신 — 덮개·바쁨을 부를 때마다 목록 앞에서 하나씩 꺼낸다 (다 쓰면 없음)."""

    def __init__(self, covers=(), busies=()):
        self.covers, self.busies = list(covers), list(busies)
        self.handle = 1

    def covering(self, rect) -> bool:
        return self.covers.pop(0) if self.covers else False

    def busy(self) -> bool:
        return self.busies.pop(0) if self.busies else False


def check_wait_rows() -> list[bool]:
    """[조회] 완료 판정 (10-07 실측: 덮개가 사라진 0.26초 뒤에도 0행, 1초 안에 행) — 0행을 바로 믿지 않는다."""
    log.info("▶ [조회] 완료 판정 — 0행은 조용한 시간이 이어질 때만 (10-07)")
    out = []
    lw = logistics_wait
    real = (ui.visible_row_count, ui.rect_of, lw.ZERO_ROWS_QUIET, winprobe.child_windows)

    def rows_by_time(*steps):
        """(초, 행 수) — 그 시각이 지나면 그 행 수. 시작부터 잰다."""
        started = time.monotonic()

        def count(grid):
            spent = time.monotonic() - started
            return next((n for at, n in reversed(steps) if spent >= at), 0)
        return count

    def run(count, loading=None, timeout=20.0):
        ui.visible_row_count = count
        started = time.monotonic()
        rows = lw._wait_rows(object(), "가짜 그리드", timeout=timeout, loading=loading)
        return rows, time.monotonic() - started

    try:
        ui.rect_of = lambda grid: (0, 0, 100, 100)
        lw.ZERO_ROWS_QUIET = 1.5          # 확인 시간을 줄인다 — 늦게 붙는 행(1초)보다 넉넉한 것은 실물과 같다
        rows, spent = run(rows_by_time((0.0, 0), (1.0, 5)))
        out.append(check("★ 조회 직후 0행이다가 1초 뒤 행이 붙으면 그 행을 기다린다 (예전 판정은 0.6초에 0)",
                         rows == 5 and spent < 3.0, f"{rows}행, {spent:.2f}초"))
        rows, spent = run(rows_by_time((0.0, 0), (2.5, 5)), _FakeLoading(covers=[False] + [True] * 6))
        out.append(check("★ 0행 → 덮개 → 0행 → 행 (실측 순서) — 덮개 동안은 0행 유예를 세지 않는다",
                         rows == 5, f"{rows}행, {spent:.2f}초"))
        rows, spent = run(rows_by_time((0.0, 8), (1.0, 3)), _FakeLoading(covers=[True] * 4))
        out.append(check("★ 덮개가 떠 있는 동안 보이는 옛 행(8)은 세지 않는다 → 덮개 뒤 3행",
                         rows == 3, f"{rows}행, {spent:.2f}초"))
        rows, spent = run(lambda grid: 0)
        out.append(check("진짜 빈 결과는 조용한 시간 뒤 0행 — 상한(20초)을 다 쓰지 않는다",
                         rows == 0 and lw.ZERO_ROWS_QUIET <= spent < lw.ZERO_ROWS_QUIET + 1.5, f"{spent:.2f}초"))
        rows, spent = run(lambda grid: 0, _FakeLoading(busies=[True] * 5))
        out.append(check("ERPia 가 바쁜 동안은 0행 유예를 세지 않는다",
                         rows == 0 and spent >= lw.ZERO_ROWS_QUIET + 1.0, f"{spent:.2f}초"))
        rows, spent = run(lambda grid: 5, _FakeLoading(busies=[True] * 1000))
        out.append(check("★ 바쁨이 계속돼도(느린 PC 오탐) 행이 보이면 끝난다 — 바쁨은 0행에만 쓴다",
                         rows == 5 and spent < 2.0, f"{rows}행, {spent:.2f}초"))
        rows, spent = run(lambda grid: 7, _FakeLoading(covers=[True] * 1000), timeout=1.0)
        out.append(check("★ 덮개가 끝내 안 사라지면 상한에서 멈추고 마지막으로 읽은 행 수를 준다 (0 이 아니다)",
                         rows == 7 and 1.0 <= spent < 2.5, f"{rows}행, {spent:.2f}초"))

        # 덮개 판정 — 누르기 전 기준선에 없던 창이 그리드를 절반 넘게 덮을 때만
        grid_rect = (0, 0, 1000, 300)
        windows = {10: grid_rect, 11: (0, 0, 1000, 600)}
        winprobe.child_windows = lambda handle: dict(windows)
        loading = lw.Loading.__new__(lw.Loading)
        loading.handle, loading.base = 1, set(windows)
        out.append(check("기준선에 있던 창(그리드 자신·부모 패널)은 덮개가 아니다",
                         loading.covering(grid_rect) is False))
        windows[20] = (990, 0, 1007, 300)                   # 새로 생긴 스크롤바 (1% 남짓)
        out.append(check("새로 생긴 작은 창(스크롤바)은 덮개가 아니다", loading.covering(grid_rect) is False))
        windows[21] = (0, 0, 1000, 300)                     # 그리드와 같은 크기 (10-07 실측 모양)
        out.append(check("★ 새로 생겨 그리드를 덮는 창은 덮개다", loading.covering(grid_rect) is True))
        loading.handle = 0
        out.append(check("메인 창 핸들을 못 얻었으면 덮개를 보지 않는다 (조용한 시간만으로)",
                         loading.covering(grid_rect) is False and loading.busy() is False))
    finally:
        ui.visible_row_count, ui.rect_of, lw.ZERO_ROWS_QUIET, winprobe.child_windows = real
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


class _Box:
    """`rectangle()` 의 높이만 흉내낸다."""

    def __init__(self, height: int):
        self._height = height

    def rectangle(self):
        return self

    def height(self) -> int:
        return self._height


def check_find_jump() -> list[bool]:
    """찾기(Ctrl+F)로 묶음까지 건너뛰기 (10-07) — 맨 위부터 FIND_AFTER_PAGES 장을 읽고도 없을 때만, 못 하면 맨 위부터."""
    log.info("▶ 찾기로 묶음까지 건너뛰기 (10-07)")
    out = []
    lw = logistics_wait
    # 200행, 한 화면 20행. far: B 가 101~115 (6장째) / near: B 가 41~55 (3장째 — 데이터 많은 계정 실측 모양)
    far = {n: "A" if n <= 100 else "B" if n <= 115 else "C" for n in range(1, 201)}
    near = {n: "A" if n <= 40 else "B" if n <= 55 else "C" for n in range(1, 201)}
    real = {name: getattr(lw, name) for name in ("find_code", "_worth_finding")}
    calls: list = []

    def jump_to_b(grid, code, pid):          # 101행이 보이게 화면을 옮긴 모양 (실측: 윗행이 보인다)
        calls.append(code)
        grid.offset = 95
        return 101, "A"

    def jump_then_fail(grid, code, pid):     # 화면을 엉뚱한 데로 옮기고 시작을 확인하지 못했다
        calls.append(code)
        grid.offset = 150
        return None

    def run(code="B", pid=1, grouped=True, find=jump_to_b, worth=True, codes=far):
        calls.clear()
        lw.find_code = find
        lw._worth_finding = lambda grid: worth
        grid = FakeGrid(200)
        with fake_sorted_grid(grid, codes, {}):
            checked, _, _ = lw.check_matching_rows(grid, code, dry_run=True, pid=pid, grouped=grouped)
        return checked, grid.pages, list(calls)

    try:
        checked, pages, called = run()
        out.append(check("★ 3장을 읽고도 없고 아래가 많으면 찾기로 건너뛴다 (맨 위부터 6장 → 4장, 체크 15 그대로)",
                         checked == 15 and pages == 4 and called == ["B"], f"체크 {checked}, 화면 {pages}, 찾기 {called}"))
        checked, pages, called = run(codes=near)
        out.append(check("★ 묶음이 3장 안에 있으면 찾지 않는다 (실측 모양 — 찾기가 더 느리다)",
                         checked == 15 and pages == 3 and not called, f"체크 {checked}, 화면 {pages}, 찾기 {called}"))
        checked, pages, called = run(worth=False)
        out.append(check("아래가 적으면(찾기가 더 느리면) 찾지 않고 내려 읽는다",
                         checked == 15 and pages == 6 and not called, f"체크 {checked}, 화면 {pages}, 찾기 {called}"))
        checked, pages, called = run(find=lambda g, c, p: calls.append(c))
        out.append(check("찾기가 안 되면 맨 위부터 다시 끝까지 — 빠지는 행 없음",
                         checked == 15 and pages == 8 and called == ["B"], f"체크 {checked}, 화면 {pages}"))
        checked, pages, called = run(find=jump_then_fail)
        out.append(check("★ 찾기가 화면을 옮겨 놓고 실패해도 맨 위부터 다시 센다 (옮겨진 데서 이어 읽지 않는다)",
                         checked == 15 and called == ["B"], f"체크 {checked}, 화면 {pages}"))
        checked, pages, called = run(pid=None)
        out.append(check("pid 가 없으면(확인 도구의 가짜 그리드 포함) 찾기를 부르지 않는다",
                         checked == 15 and pages == 6 and not called, f"찾기 {called}"))
        checked, pages, called = run(grouped=False)
        out.append(check("정렬을 확인 못 했으면 찾지 않는다 (건너뛴 위쪽에 없다는 근거가 정렬이다)",
                         checked == 15 and not called, f"찾기 {called}"))
        checked, pages, called = run(code="A")
        out.append(check("첫 화면에 그 코드가 있으면 찾지 않는다", checked == 100 and not called, f"찾기 {called}"))

        # 보류 확인도 같은 자리에서 건너뛴다
        lw.find_code, lw._worth_finding = jump_to_b, (lambda grid: True)
        calls.clear()
        grid = FakeGrid(200)
        with fake_sorted_grid(grid, far, {n: "1" for n in range(101, 116)}):
            result = lw.verify_holds(grid, {"B": 15}, grouped=True, pid=1)
        out.append(check("★ 보류 확인도 3장 뒤 찾기로 건너뛴다",
                         result["short"] == {} and grid.pages == 3 and calls == ["B"],
                         f"부족 {result['short']}, 화면 {grid.pages}, 찾기 {calls}"))
    finally:
        for name, func in real.items():
            setattr(lw, name, func)

    out += _check_group_start()
    out += _check_worth_finding()
    out += _check_find_code()
    out += _check_type_find()
    return out


def _check_group_start() -> list[bool]:
    """찾기로 간 화면에서 묶음 시작 확인 — 정확히 같은 첫 행 + 그 윗행(다른 코드)."""
    out = []
    lw = logistics_wait
    codes = {n: "A" if n <= 40 else "B" if n <= 55 else "C" for n in range(1, 61)}
    real = (ui.column_values, ui.scroll_up_line)
    grid = FakeGrid(60)
    try:
        ui.column_values = lambda g, column: {r.number: codes.get(r.number, "") for r in g.visible()}

        def line_up(g):
            if g.offset == 0:
                return False
            g.offset -= 1
            return True

        ui.scroll_up_line = line_up
        for offset, code, want, name in (
            (35, "B", (41, "A"), "윗행(40, A)이 보이면 그 행이 묶음 시작"),
            (40, "B", (41, "A"), "첫 일치 행이 맨 위면 한 줄 올려 윗행을 보고 확인한다"),
            (45, "B", None, "★ 묶음 가운데로 갔으면(한 줄 올려도 윗행이 같은 코드) 시작을 확인 못 한다 → None"),
            (0, "A", (1, None), "1행이 일치하면 1행이 시작"),
            (0, "Z", None, "정확히 같은 행이 없으면(일부만 같은 곳으로 갔다) None"),
        ):
            grid.offset = offset
            got = lw._group_start(grid, code)
            out.append(check(name, got == want, f"{got}"))
    finally:
        ui.column_values, ui.scroll_up_line = real
    return out


def _check_worth_finding() -> list[bool]:
    """찾기를 쓸 가치 — 스크롤바 '페이지 아래로' ÷ 썸 높이 = 아래 남은 화면 × 페이지 비용 > 찾기 비용."""
    out = []
    lw = logistics_wait
    real = (ui.vertical_scrollbar, ui.scrollbar_button, ui.find_all)
    try:
        def bar(below, thumb):
            ui.vertical_scrollbar = lambda g: object()
            ui.scrollbar_button = lambda g, names, bar=None: None if below is None else _Box(below)
            ui.find_all = lambda parent, control_type=None, title_re=None: [_Box(thumb)] if thumb else []

        bar(202, 171)                       # 10-07 실측 (36행·한 화면 18행 → 1.2장)
        out.append(check("아래가 1.2장뿐이면(실측 모양) 찾지 않는다 — 남은 장 × 페이지 비용 < 찾기 비용",
                         lw._worth_finding(object()) is False, f"{lw._pages_below(object()):.2f}장"))
        bar(271, 102)                       # 데이터 많은 계정 실측 (61행 → 2.7장)
        out.append(check("아래가 2.7장이어도(데이터 많은 계정 61행) 찾지 않는다", lw._worth_finding(object()) is False,
                         f"{lw._pages_below(object()):.2f}장"))
        bar(600, 100)
        out.append(check("아래가 6장이면 찾는다", lw._worth_finding(object()) is True))
        bar(None, 171)
        out.append(check("맨 아래(페이지 아래로 없음)면 찾지 않는다 — 그 코드는 이 하단에 없다",
                         lw._worth_finding(object()) is False))
        bar(300, None)
        out.append(check("썸을 못 읽으면 찾는다 (아래가 있다는 것은 안다)", lw._worth_finding(object()) is True))
        ui.vertical_scrollbar = lambda g: None
        out.append(check("스크롤바가 없으면(한 화면) 찾지 않는다", lw._worth_finding(object()) is False))
    finally:
        ui.vertical_scrollbar, ui.scrollbar_button, ui.find_all = real
    return out


class _FakeMain:
    """ERPia 메인 창 — Ctrl+F 를 받으면 찾기 창이 뜬다 (`late` 면 창 목록을 그만큼 더 읽은 뒤에)."""

    def __init__(self, windows: dict, late: int = 0):
        self.handle, self.windows, self.keys, self.late = 100, windows, [], late

    def type_keys(self, keys, set_foreground=True):
        self.keys.append(keys)
        if keys == "^f" and not self.late:
            self.windows[200] = logistics_wait.FIND_TITLE


class _FakeFindGrid:
    def __init__(self, main):
        self.main = main

    def top_level_parent(self):
        return self.main


def _check_find_code() -> list[bool]:
    """찾기 본체 — 맨 앞 창 확인·늘 닫기·실패하면 None. 실제 창·키는 쓰지 않는다 (전부 가짜)."""
    from utils.cancel import Cancelled

    out = []
    lw = logistics_wait
    real_ui = (ui.grid_rows, ui.cell, ui.click, ui.bring_forward)
    real_lw = (lw._type_find, lw._group_start, lw.FIND_OPEN_TIMEOUT, lw.FIND_CLOSE_TIMEOUT)
    real_wp = (winprobe.top_windows, winprobe.focus_handle, winprobe.is_within, winprobe.ui_roundtrip)
    real_close = dialogs.post_close

    def run(foreground=True, focused=True, typed=True, code="B01", late=0, stuck=False, leftover=False):
        windows = {100: "comp01 - user01"}
        if leftover:
            windows[250] = lw.FIND_TITLE                   # 앞서 남은 찾기 창
        main = _FakeMain(windows, late)
        closed: list = []
        clicks: list = []
        columns: list = []

        def top_windows(pid=None, visible_only=True):
            if main.late and main.keys:                     # Ctrl+F 뒤 목록을 `late` 번 더 읽어야 뜬다
                main.late -= 1
                if not main.late:
                    windows[200] = lw.FIND_TITLE
            return [winprobe.Window(h, "WindowsForms10", t, 7, True) for h, t in windows.items()]

        winprobe.top_windows = top_windows
        winprobe.focus_handle = lambda handle: 500
        winprobe.is_within = lambda child, ancestor: focused
        # 늦게 뜨는 창은 ERPia 가 밀려 있을 때 생긴다 — 창이 뜰 때까지 바쁘다
        winprobe.ui_roundtrip = lambda handle, timeout=1.0: 0.01 if main.late else 0.0001
        ui.grid_rows = lambda g: [FakeRow(1)]
        ui.cell = lambda row, column, timeout=None: columns.append(column) or "코드 칸"
        ui.click = lambda ctrl, what, **k: clicks.append(what) or "클릭"
        ui.bring_forward = lambda ctrl, what: foreground

        def type_find(finder, c, grid, pid, before):
            if isinstance(typed, BaseException):
                raise typed
            if typed == "done":
                windows[300] = lw.FIND_DONE_TITLE
            return typed is True

        def post_close(handle):
            closed.append(handle)
            if not stuck:
                windows.pop(handle, None)

        lw._type_find, dialogs.post_close = type_find, post_close
        lw._group_start = lambda grid, c: (41, "A")
        lw.FIND_OPEN_TIMEOUT, lw.FIND_CLOSE_TIMEOUT = 0.3, 0.5
        try:
            got = lw.find_code(_FakeFindGrid(main), code, 7)
        except BaseException as exc:          # noqa: BLE001 — 취소·멈춤이 그대로 올라오는지 본다
            got = exc
        return got, main.keys, sorted(set(closed)), clicks, columns

    try:
        got, keys, closed, clicks, columns = run()
        out.append(check("찾기 성공 → 묶음 시작, 찾기 창은 닫는다", got == (41, "A") and keys == ["^f"]
                         and closed == [200], f"{got} / 키 {keys} / 닫음 {closed}"))
        out.append(check("포커스는 하단 'ERP상품코드' 칸을 눌러 준다 (행 가운데는 다른 컬럼이다)",
                         columns == [lw.BOTTOM_CODE_COLUMN] and len(clicks) == 1, f"{columns} / {clicks}"))
        got, keys, closed, clicks, _ = run(foreground=False)
        out.append(check("★ ERPia 가 맨 앞이 아니면 아무것도 누르지 않는다 (클릭도 Ctrl+F 도)",
                         got is None and keys == [] and clicks == [], f"키 {keys} / 클릭 {clicks}"))
        got, keys, closed, clicks, _ = run(focused=False)
        out.append(check("★ 키보드 포커스가 하단 그리드에 없으면 Ctrl+F 를 보내지 않는다 (상단 찾기가 열린다)",
                         got is None and keys == [], f"키 {keys}"))
        got, keys, closed, clicks, _ = run(late=6)
        out.append(check("★ 찾기 창이 늦게 떠도(여는 대기 뒤) 닫는다 — 남겨 두지 않는다",
                         got is None and closed == [200], f"{got} / 닫음 {closed}"))
        got, keys, closed, clicks, _ = run(stuck=True)
        out.append(check("★ 찾기 창이 끝내 안 닫히면 멈춘다 (가려진 채 계속 누르지 않는다)",
                         isinstance(got, lw.ScreenError), f"{type(got).__name__}"))
        got, keys, closed, clicks, _ = run(leftover=True)
        out.append(check("앞서 남은 찾기 창이 있으면 먼저 닫고 찾는다",
                         got == (41, "A") and closed == [200, 250], f"{got} / 닫음 {closed}"))
        got, keys, closed, _, _ = run(typed=False)
        out.append(check("이동이 안 되면 None (맨 위부터 훑는다) — 찾기 창은 닫는다",
                         got is None and closed == [200], f"{got} / 닫음 {closed}"))
        got, keys, closed, _, _ = run(typed="done")
        out.append(check("없는 값('완료' 알림)이면 None — 알림과 찾기 창을 둘 다 닫는다",
                         got is None and sorted(closed) == [200, 300], f"닫음 {closed}"))
        got, keys, closed, _, _ = run(typed=RuntimeError("UIA 오류"))
        out.append(check("중간에 오류가 나도 None — 찾기 창은 닫는다", got is None and closed == [200],
                         f"{got} / 닫음 {closed}"))
        got, keys, closed, _, _ = run(typed=Cancelled())
        out.append(check("★ [중단](취소)이면 그대로 올라가되 찾기 창 닫기는 보낸다",
                         isinstance(got, Cancelled) and closed == [200], f"{type(got).__name__} / 닫음 {closed}"))
        got, keys, closed, _, _ = run(code="A+1")
        out.append(check("키 특수문자가 든 코드는 찾지 않는다 (아무것도 누르지 않는다)",
                         got is None and keys == [] and closed == [], f"키 {keys}"))
    finally:
        ui.grid_rows, ui.cell, ui.click, ui.bring_forward = real_ui
        lw._type_find, lw._group_start, lw.FIND_OPEN_TIMEOUT, lw.FIND_CLOSE_TIMEOUT = real_lw
        winprobe.top_windows, winprobe.focus_handle, winprobe.is_within, winprobe.ui_roundtrip = real_wp
        dialogs.post_close = real_close
    return out


class _FakeEdit:
    def __init__(self):
        self.keys: list = []

    def type_keys(self, keys, set_foreground=True):
        self.keys.append(keys)


class _FakeDesktop:
    """`pywinauto.Desktop` 대신 — window(handle=).wrapper_object() 가 가짜 찾기 창."""

    def __init__(self, backend=None):
        pass

    def window(self, handle=None):
        return self

    def wrapper_object(self):
        return self

    handle = 200

    def set_focus(self):
        return self


def _check_type_find() -> list[bool]:
    """찾기 창에 입력 — 맨 앞이 찾기 창일 때만 키, '완료'·정확 일치 판정 (가짜 창)."""
    import pywinauto

    out = []
    lw = logistics_wait
    real = (pywinauto.Desktop, ui.find, ui.wait_foreground, ui.column_values, winprobe.top_windows,
            lw.FIND_JUMP_TIMEOUT)

    def run(foreground=True, visible=("A01",), done=False):
        edit = _FakeEdit()
        pywinauto.Desktop = _FakeDesktop
        ui.find = lambda parent, **k: edit
        ui.wait_foreground = lambda window, what: foreground
        ui.column_values = lambda grid, column: dict(enumerate(visible, 1)) if edit.keys else {1: "A01"}
        windows = [winprobe.Window(300, "WindowsForms10", lw.FIND_DONE_TITLE, 7, True)] if done else []
        winprobe.top_windows = lambda pid=None, visible_only=True: windows if edit.keys else []
        lw.FIND_JUMP_TIMEOUT = 0.5
        return lw._type_find(200, "B01", object(), 7, set()), edit.keys

    try:
        got, keys = run(foreground=False)
        out.append(check("★ 찾기 창이 맨 앞이 아니면 코드+Enter 를 보내지 않는다", got is False and keys == [],
                         f"{got} / 키 {keys}"))
        got, keys = run(visible=("A01", "B01"))
        out.append(check("코드가 보이면 도착", got is True and keys == ["^aB01{ENTER}"], f"{got} / 키 {keys}"))
        got, keys = run(done=True)
        out.append(check("'완료' 알림이면 없는 값 — 도착 아님", got is False, f"{got}"))
        got, keys = run(visible=("XB01", "B012"))
        out.append(check("일부만 같은 코드(XB01·B012)만 보이면 도착이 아니다 (정확히 같아야)", got is False, f"{got}"))
    finally:
        (pywinauto.Desktop, ui.find, ui.wait_foreground, ui.column_values, winprobe.top_windows,
         lw.FIND_JUMP_TIMEOUT) = real
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
    class SavingMark:
        """`Loading` 대신 — 만들어지는 순간을 기록한다."""
        handle = 0
        record: list = []

        def __init__(self, screen):
            SavingMark.record.append("저장 기준선")

    def save(slip_counts, many=False, rows_seq=(8, 0), visible=0):
        rows = iter(rows_seq)
        slips = iter(slip_counts)
        names = ("activate_tab", "click_search", "_general_grid", "_wait_rows",
                 "count_general_slips", "_screen_pid", "process_handle", "Loading", "SAVE_IDLE_GRACE")
        real = {name: getattr(lw, name) for name in names}
        real_ui = (ui.header_select_all, ui.find, ui.click, ui.visible_row_count, ui.has_hidden_rows)
        info: dict = {}
        record = SavingMark.record
        record.clear()
        try:
            lw.activate_tab = lambda s, name: None
            lw.click_search = lambda s: "조회 기준선"
            lw._general_grid = lambda s: object()
            lw._wait_rows = lambda g, what, timeout=None, loading=None: record.append(loading) or next(rows)
            lw.count_general_slips = lambda g: next(slips)
            lw._screen_pid = lambda s: None
            lw.process_handle = lambda pid: 0
            lw.Loading, lw.SAVE_IDLE_GRACE = SavingMark, 0.2
            ui.header_select_all = lambda g, dry_run=False: "클릭"
            ui.find = lambda *a, **k: object()
            ui.click = lambda ctrl, what, **k: record.append(what) or "클릭"
            ui.visible_row_count = lambda g: visible
            ui.has_hidden_rows = lambda g: many
            moved = lw.save_general(object(), info=info)
        finally:
            for name, func in real.items():
                setattr(lw, name, func)
            ui.header_select_all, ui.find, ui.click, ui.visible_row_count, ui.has_hidden_rows = real_ui
        return moved, info.get("exact")

    out.append(check("★ [일반] 8행 저장 → 넘긴 주문 2건 (행 차이 8 이 아니다), 정확",
                     save([(2, True), (0, True)]) == (2, True), str(save([(2, True), (0, True)]))))
    wiring = list(SavingMark.record)
    out.append(check("★ [일반] [조회]·[저장] 의 로딩 기준선이 _wait_rows 로 넘어가고, 저장 기준선은 누르기 전에 잡는다",
                     wiring[:3] == ["조회 기준선", "저장 기준선", "저장(S)"] and isinstance(wiring[3], SavingMark),
                     str([w if isinstance(w, str) else type(w).__name__ for w in wiring])))
    out.append(check("전표 수를 모르면 행 차이는 '저장됨' 표시일 뿐 — 정확하지 않다고 알린다",
                     save([(None, False), (0, True)]) == (8, False)))
    got = save([(9, True), (7, True)], many=True, rows_seq=(18, 18), visible=18)
    out.append(check("★ [일반] 이 한 화면을 넘으면 보이는 행 수(18 그대로)로 '0건' 이라 하지 않고 전표를 다시 센다 (9→7 = 2)",
                     got == (2, True), str(got)))

    # [조회] 기준선은 누르기 전에 잡는다 (누른 뒤면 덮개가 기준선에 들어가 덮개를 못 본다)
    order: list = []
    real_c = (ui.find, ui.click, lw.Loading)
    try:
        ui.find = lambda *a, **k: object()
        ui.click = lambda ctrl, what, **k: order.append(what) or "클릭"
        lw.Loading = lambda screen: order.append("기준선") or "기준선"
        mark = lw.click_search(object())
    finally:
        ui.find, ui.click, lw.Loading = real_c
    out.append(check("★ click_search — 기준선을 [조회] 누르기 전에 잡아 돌려준다",
                     order == ["기준선", "조회(F)"] and mark == "기준선", str(order)))

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
    results += check_wait_rows()
    results += check_scroll_rule()
    results += check_no_menu()
    results += check_sorted_scan()
    results += check_find_jump()
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
