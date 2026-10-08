r"""[조작 - 데이터는 바꾸지 않는다] 물류대기 [상품별 재고검토] 에서 찾기(Ctrl+F) 점프와 조회 중 로딩 창을 잰다 (10-07).

1차(10-07 오후): 찾기 창(`txt_Key`, Win32 로만 잡힘)·[조회] 로딩 덮개 — 결과는 `docs/CONTROLS.md` "찾기(Ctrl+F) 창·조회 로딩 창".
2차(이 판): 사용자 요청 "하단을 정렬했는데 첫 화면에 보류할 상품이 없을 때만 찾기 — 열기·입력·이동 시간을 고려" 의 근거.
  ① 새 [조회] 판정(`_wait_rows(loading=)`)이 행이 붙을 때까지 기다리는지 (끝난 2초 뒤 다시 센다)
  ② 그리드마다 화면 밖 행·수직 스크롤바 요소(썸 크기로 아래 남은 화면 수를 셀 수 있나)
  ③ 한 페이지 내리기+읽기 시간 (지금 방식의 비용)
  ④ 화면 밖 코드로 찾기 — 열기·입력·이동·닫기 시간, 떨어진 자리(첫 일치 행·그 윗행이 보이나),
     아래에 포커스를 두고 위쪽 코드를 찾나(맨 위부터 찾나), 코드 일부로도 가나(포함 검색인가)
  ⑤ 부족 상품마다 하단을 정렬했을 때 첫 화면에 그 코드가 없는 경우가 있나 — 있으면 그 상품으로 ③·④ 를 하단에서

누르는 것: 물류대기 메뉴·[재고검토] 탭·[조회]·상단/하단 행(포커스 — 상단은 하단을 다시 조회시킨다)·하단 정렬 머리글·
스크롤바·찾기 창 입력. 저장·보류·물류처리·체크는 누르지 않는다. 상품코드는 실행 중 그리드에서 읽는다 (코드에 실값 없음).

    .venv\Scripts\python.exe -m tools.test_find_jump --launch --dry-run   # ERPia 를 켜고 로그인만, 화면은 안 연다
    .venv\Scripts\python.exe -m tools.test_find_jump --launch --yes       # 켜고 시험
    .venv\Scripts\python.exe -m tools.test_find_jump --pid <PID> --yes    # 떠 있는 ERPia 로
"""
from __future__ import annotations

import argparse
import ctypes
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from config.settings import SETTINGS  # noqa: E402
from utils import ui, winprobe  # noqa: E402
from utils.logger import setup_logging  # noqa: E402
from utils.wait import WaitTimeout, pause, wait_for  # noqa: E402

FIND_TITLE = "찾기"          # 1차 실측 (10-07)
FIND_EDIT_ID = "txt_Key"     # 1차 실측 (10-07)
SAFE_CODE = re.compile(r"^[0-9A-Za-z_-]+$")   # type_keys 특수문자(^ % + ~ () {}) 가 든 코드는 보내지 않는다
FIND_OPEN_TIMEOUT = 3.0
FIND_CLOSE_TIMEOUT = 3.0
JUMP_TIMEOUT = 5.0
WATCH_INTERVAL = 0.05
RECHECK_AFTER = 2.0          # ① [조회] 판정이 끝나고 이만큼 뒤 다시 센다
NOT_THERE = "ZQ9X7Y"         # 그리드에 없을 값 — 없는 값을 찾으면 알림이 뜨나
WM_CLOSE = 0x0010

_user32 = ctypes.windll.user32
_CHILD_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
T0 = time.perf_counter()


def now() -> float:
    return time.perf_counter() - T0


def say(msg: str) -> None:
    print(f"[{now():7.2f}s] {msg}", flush=True)


def children(hwnd: int) -> dict:
    """메인 창의 보이는 자식 창 {hwnd: (class, rect)} — Win32 만 (UIA 보다 싸고 그리드 덮개가 보인다)."""
    out: dict = {}

    def collect(handle, _lparam):
        if _user32.IsWindowVisible(handle):
            out[int(handle)] = (winprobe._class_of(handle), winprobe.rect_of(handle))
        return True

    _user32.EnumChildWindows(wintypes.HWND(hwnd), _CHILD_PROC(collect), 0)
    return out


def rect_of(ctrl) -> tuple:
    box = ctrl.rectangle()
    return box.left, box.top, box.right, box.bottom


def overlap(rect, grid) -> float:
    """grid 면적 중 rect 가 덮는 비율."""
    width = min(rect[2], grid[2]) - max(rect[0], grid[0])
    height = min(rect[3], grid[3]) - max(rect[1], grid[1])
    area = max(grid[2] - grid[0], 1) * max(grid[3] - grid[1], 1)
    return max(width, 0) * max(height, 0) / area


class Watch(threading.Thread):
    """기준선에 없던 자식 창이 생기고 사라지는 시각을 남긴다. 계측기 자체(표본 수·최대 간격)도 찍는다."""

    def __init__(self, hwnd: int, grids: dict):
        super().__init__(daemon=True)
        self.hwnd, self.grids = hwnd, grids
        self.base = set(children(hwnd))
        self.seen: dict = {}
        self.samples, self.max_gap = 0, 0.0
        self._stop = threading.Event()

    def run(self) -> None:
        last = None
        while not self._stop.is_set():
            moment = now()
            snap = children(self.hwnd)
            self.samples += 1
            if last is not None:
                self.max_gap = max(self.max_gap, moment - last)
            last = moment
            for handle, (cls, rect) in snap.items():
                if handle in self.base or rect is None:
                    continue
                record = self.seen.setdefault(handle, {"cls": cls, "rect": rect, "first": moment, "last": moment})
                record["last"] = moment
            self._stop.wait(WATCH_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2)

    def report(self, marks: dict) -> None:
        end = now()
        say(f"[계측기] 표본 {self.samples}개, 최대 간격 {self.max_gap:.2f}초 (0.3초를 넘으면 아래 시각을 믿지 않는다)")
        covers = [rec for rec in self.seen.values()
                  if any(overlap(rec["rect"], grid) >= 0.5 for grid in self.grids.values())]
        say(f"[자식 창] 기준선 {len(self.base)}개, 새로 생긴 창 {len(self.seen)}개 (그리드를 절반 넘게 덮은 것 {len(covers)}개)")
        for rec in sorted(covers, key=lambda item: item["first"])[:10]:
            alive = "지금도 보임" if end - rec["last"] < 0.2 else f"사라짐 {rec['last']:.2f}s"
            say(f"  덮개 생김 {rec['first']:.2f}s / {alive} (떠 있던 {rec['last'] - rec['first']:.2f}초)")
        if "search_click" in marks and "rows_ok" in marks:
            mine = [rec["last"] for rec in covers if marks["search_click"] - 0.05 <= rec["first"] <= marks["rows_ok"] + 5]
            ours = marks["rows_ok"] - marks["search_click"]
            if mine:
                gone = max(mine) - marks["search_click"]
                say(f"  [조회] → 새 판정 {ours:.2f}초 / 덮개가 사라진 때 {gone:.2f}초 / 차이 {ours - gone:+.2f}초 "
                    "(음수면 덮개가 남은 채 끝났다)")
            else:
                say(f"  [조회] → 새 판정 {ours:.2f}초 / 덮개가 안 잡혔다")


def finders(pid: int, main: int) -> tuple[list[int], list[int]]:
    """'찾기' 창 — (Win32 EnumWindows 로 찾은 것, Desktop(uia) 로 찾은 것)."""
    by_enum = [w.handle for w in winprobe.top_windows(pid) if w.title == FIND_TITLE and w.handle != main]
    return by_enum, []


def wait_finder(pid: int, main: int) -> int | None:
    try:
        wait_for(lambda: bool(finders(pid, main)[0]), "찾기 창", timeout=FIND_OPEN_TIMEOUT, interval=0.05)
    except WaitTimeout:
        return None
    return finders(pid, main)[0][0]


def close_finder(pid: int, main: int, handle: int) -> bool:
    _user32.PostMessageW(wintypes.HWND(handle), WM_CLOSE, 0, 0)
    try:
        wait_for(lambda: handle not in finders(pid, main)[0], "찾기 창 닫힘", timeout=FIND_CLOSE_TIMEOUT,
                 interval=0.05)
    except WaitTimeout:
        raise RuntimeError("찾기 창이 안 닫혔다 — 멈춘다. 손으로 닫아 주세요")
    return True


def ensure_foreground(pid: int, what: str) -> None:
    """키를 보내기 전 — 맨 앞 창이 이 ERPia 가 아니면 멈춘다 (키가 다른 프로그램으로 가면 안 된다)."""
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError(f"{what}: 맨 앞 창이 ERPia 가 아니다 (pid {owner.value}) — 키를 보내지 않고 멈춘다")


def launch_and_login(company: str | None = None):
    """흐름과 같은 순서 — 새 인스턴스 → 업데이트 처리 → 로그인 (`orchestrator/erpia_flow.launch_stage`·`login_stage`).
    `company` 를 주면 업체코드만 바꾼다 (데이터가 많은 시험 업체 — 아이디·비밀번호는 설정 값, 틀리면 로그인이 한 번에 멈춘다)."""
    from automation import updater
    from automation.application import start_new_instance
    from automation.login import login

    exe = SETTINGS.target_exe
    target = start_new_instance(exe, work_dir=str(Path(exe).parent))
    updater.settle(exe, target.pid)
    login(company or SETTINGS.login_company_code, SETTINGS.login_user_id, SETTINGS.login_password, pid=target.pid)
    say(f"ERPia 를 켜고 로그인했다 — pid={target.pid}")
    return target


# ------------------------------------------------------------------ ② 스크롤바
def bar_parts(grid) -> list[tuple]:
    """수직 스크롤바 안의 요소 (종류, 이름, 높이 px). 스크롤바가 없으면 []."""
    bar = ui.vertical_scrollbar(grid)
    if bar is None:
        return []
    parts = []
    for child in bar.children():
        try:
            box = child.rectangle()
            parts.append((child.element_info.control_type, (child.element_info.name or "").strip(),
                          box.bottom - box.top))
        except Exception as exc:       # noqa: BLE001 — 조사: 못 읽은 것도 결과다
            parts.append(("?", type(exc).__name__, 0))
    return parts


def pages_below(parts) -> float | None:
    """'페이지 아래로' 높이 ÷ 썸 높이 = 아래에 남은 화면 수 (추정). 썸이 안 잡히면 None."""
    below = next((h for _kind, name, h in parts if name in ui.SCROLL_CAN_DOWN_NAMES), None)
    if below is None:
        return 0.0 if parts else None
    thumb = next((h for kind, _name, h in parts if kind == "Thumb"), None)
    return round(below / thumb, 1) if thumb else None


def has_more(grid) -> bool:
    bar = ui.vertical_scrollbar(grid)
    return bar is not None and ui.scrollbar_button(grid, ui.SCROLL_CAN_DOWN_NAMES, bar=bar) is not None


def report_grid(name: str, grid) -> None:
    parts = bar_parts(grid)
    say(f"[{name}] 보이는 {ui.visible_row_count(grid)}행 / 스크롤바 요소 {parts or '없음'} / "
        f"아래 남은 화면 추정 {pages_below(parts)}")


# ------------------------------------------------------------------ ③ 지금 방식의 비용
def page_cost(name: str, grid, columns, pages: int = 3) -> float | None:
    """한 페이지 내리기 + 읽기 시간(초) 평균. 맨 위에서 시작하고 끝나면 맨 위로."""
    ui.scroll_to_top(grid)
    spent = []
    for _ in range(pages):
        started = now()
        if not ui.scroll_down_verified(grid, rounds=1):
            break
        moved = now() - started
        ui.columns_values(grid, columns)
        spent.append((moved, now() - started - moved))
    ui.scroll_to_top(grid)
    if not spent:
        say(f"[{name}] 페이지 비용: 내릴 페이지가 없다")
        return None
    scroll = sum(m for m, _ in spent) / len(spent)
    read = sum(r for _, r in spent) / len(spent)
    say(f"[{name}] 페이지 비용 {len(spent)}번 평균 — 내리기 {scroll:.2f}초 + 읽기({len(columns)}컬럼) {read:.2f}초 "
        f"= {scroll + read:.2f}초")
    return scroll + read


def visible_codes(grid, column) -> set[str]:
    return {(value or "").strip() for value in ui.column_values(grid, column).values()}


def scroll_until(name: str, grid, column, code: str, limit: int = 40) -> None:
    """맨 위에서 code 가 보일 때까지 페이지를 내린다 — 지금 방식으로 그 자리까지 가는 비용."""
    ui.scroll_to_top(grid)
    started, pages = now(), 0
    while code not in visible_codes(grid, column):
        if pages >= limit or not ui.scroll_down_verified(grid, rounds=1):
            say(f"[{name}] 지금 방식: {pages}페이지 내려도 코드가 안 보였다 ({now() - started:.2f}초)")
            ui.scroll_to_top(grid)
            return
        pages += 1
    say(f"[{name}] 지금 방식: 맨 위에서 {pages}페이지 내려 코드가 보였다 — {now() - started:.2f}초")
    ui.scroll_to_top(grid)


# ------------------------------------------------------------------ ④ 찾기
def center_column(row) -> str:
    """행 가운데를 누르면 어느 컬럼 칸이 눌리나 — 셀 제목 '<컬럼> 행 N' 에서."""
    box = row.rectangle()
    middle = (box.left + box.right) // 2
    for item in ui.search(row, control_type="DataItem"):
        cell = item.rectangle()
        if cell.left <= middle < cell.right:
            return (item.element_info.name or "").rsplit(" 행 ", 1)[0]
    return "?"


def find_once(target, pid: int, main_hwnd: int, grid, column, expect: str, focus_row, label: str,
              query: str | None = None, focus_cell: bool = False) -> dict:
    """focus_row(또는 그 행의 column 칸)를 눌러 포커스 → Ctrl+F → txt_Key 에 query(기본 expect)+Enter → expect 가 보일 때까지 → 닫기."""
    query = expect if query is None else query
    out: dict = {}
    if not SAFE_CODE.match(query):
        say(f"[{label}] 찾을 값에 특수문자가 있어 키로 보내지 않는다")
        return out
    ui.bring_forward(grid, f"{label} 그리드")
    if focus_cell:
        ui.click(ui.cell(focus_row, column), f"{label} 포커스 칸({column})", methods=("클릭",))
        out["편집칸 생김"] = len(ui.search(grid, control_type="Edit"))
    else:
        ui.click(focus_row, f"{label} 포커스 행", methods=("클릭",))
    ensure_foreground(pid, "Ctrl+F")
    windows_before = {w.handle for w in winprobe.top_windows(pid)}
    started = now()
    target.main_window().type_keys("^f", set_foreground=True)
    finder = wait_finder(pid, main_hwnd)
    out["열기"] = round(now() - started, 2)
    if not finder:
        say(f"[{label}] 찾기 창이 안 떴다 ({out})")
        return out
    try:
        from pywinauto import Application

        mark = now()
        win = Application(backend="uia").connect(handle=finder, timeout=5).window(handle=finder)
        key = next((e for e in win.descendants(control_type="Edit")
                    if e.element_info.automation_id == FIND_EDIT_ID), None)
        out["입력칸 잡기"] = round(now() - mark, 2)
        if key is None:
            say(f"[{label}] 입력칸 {FIND_EDIT_ID} 가 없다 ({out})")
            return out
        win.set_focus()
        if int(_user32.GetForegroundWindow() or 0) != finder:
            say(f"[{label}] 맨 앞 창이 찾기 창이 아니다 — 키를 보내지 않는다 ({out})")
            return out
        mark = now()
        key.type_keys("^a" + query + "{ENTER}", set_foreground=True)
        out["입력"] = round(now() - mark, 2)
        mark = now()
        try:
            wait_for(lambda: expect in visible_codes(grid, column), "찾기로 코드가 보임",
                     timeout=JUMP_TIMEOUT, interval=0.1)
            out["이동"] = round(now() - mark, 2)
        except WaitTimeout:
            out["이동"] = None
        out["찾기 창 남음"] = finder in finders(pid, main_hwnd)[0]
        out["그 사이 뜬 창"] = [(w.class_name, w.title) for w in winprobe.top_windows(pid)
                            if w.handle not in windows_before and w.handle != finder]
    finally:
        mark = now()
        out["닫기"] = round(now() - mark, 2) if close_finder(pid, main_hwnd, finder) else None
    out["합계"] = round(now() - started, 2)
    values = ui.column_values(grid, column)
    hits = sorted(n for n, value in values.items() if (value or "").strip() == expect)
    out["보이는 행"] = (min(values), max(values)) if values else None
    out["첫 일치 행"] = hits[0] if hits else None
    out["윗행 보임"] = bool(hits) and (hits[0] == 1 or hits[0] - 1 in values)
    say(f"[{label}] {out}")
    return out


def find_tests(name: str, target, pid: int, main_hwnd: int, grid, column) -> None:
    """화면 밖 행이 있는 그리드에서 — 첫 화면 밖 코드로 지금 방식 비용·찾기 비용·떨어진 자리, 맨 위부터 찾나, 포함 검색인가."""
    started = now()
    values, exact = ui.scan_column(grid, column)
    scanned = now() - started
    ui.scroll_to_top(grid)
    first_screen = visible_codes(grid, column)
    later = [(n, (v or "").strip()) for n, v in sorted(values.items())
             if (v or "").strip() and (v or "").strip() not in first_screen and SAFE_CODE.match((v or "").strip())]
    say(f"[{name}] 전체 {len(values)}행{'' if exact else ' (끝까지 못 봄)'} (한 컬럼 끝까지 {scanned:.1f}초) / "
        f"첫 화면 밖에만 있는 코드 {len(later)}행")
    if not later:
        return
    number, code = later[-1]
    first_no = min(n for n, v in values.items() if (v or "").strip() == code)
    say(f"[{name}] 찾을 코드: 행 {number} 의 코드 (그 코드의 첫 행 {first_no}, 첫 화면 {len(first_screen)}종)")
    if len(values) <= 3 * max(len(first_screen), 18):
        scroll_until(name, grid, column, code)          # 큰 그리드는 위 '끝까지' 시간으로 갈음한다
    say(f"[{name}] 1행 가운데를 누르면 눌리는 칸: {center_column(ui.grid_rows(grid)[0])!r}")
    # 2차 1회(10-07): 행 가운데를 눌러 포커스 → 이 코드로 안 움직였다. 코드 칸을 눌러 포커스를 준다 (다른 팀 방식)
    find_once(target, pid, main_hwnd, grid, column, code, ui.grid_rows(grid)[0], f"{name} 찾기 — 1행 코드 칸 포커스",
              focus_cell=True)
    # 한 페이지 내려 맨 아래 코드 칸에 포커스를 둔 채, 화면 위 밖에만 있는 코드를 찾는다 — 가면 맨 위부터(또는 돌아서) 찾는다
    ui.scroll_to_top(grid)
    ui.scroll_down_verified(grid, rounds=1)
    rows = ui.grid_rows(grid)
    shown = ui.visible_row_numbers(grid)
    if rows and shown:
        last_row: dict = {}
        for n, v in values.items():
            if (v or "").strip():
                last_row[(v or "").strip()] = max(n, last_row.get((v or "").strip(), 0))
        above = sorted((n, c) for c, n in last_row.items() if n < min(shown) and SAFE_CODE.match(c))
        if above:
            up_number, up_code = above[0]
            find_once(target, pid, main_hwnd, grid, column, up_code, rows[-1],
                      f"{name} 화면 위에만 있는 코드(마지막 행 {up_number}) — 보이는 {min(shown)}~{max(shown)} 맨 아래 칸 포커스",
                      focus_cell=True)
        else:
            say(f"[{name}] 보이는 {min(shown)}행 위에서 끝나는 코드가 없다 — 위쪽 찾기는 건너뛴다")
    # 코드 일부(뒤 6자)로 찾는다 — 가면 포함 검색이다
    if len(code) > 6:
        ui.scroll_to_top(grid)
        find_once(target, pid, main_hwnd, grid, column, code, ui.grid_rows(grid)[0], f"{name} 코드 뒤 6자로",
                  query=code[-6:], focus_cell=True)
    # 없는 값을 찾는다 — 알림 창이 뜨나 (뜨면 흐름이 닫아야 한다)
    ui.scroll_to_top(grid)
    before = {(w.handle, w.title) for w in winprobe.top_windows(pid)}
    find_once(target, pid, main_hwnd, grid, column, NOT_THERE, ui.grid_rows(grid)[0], f"{name} 없는 값",
              focus_cell=True)
    new = [(w.class_name, w.title) for w in winprobe.top_windows(pid) if (w.handle, w.title) not in before]
    say(f"[{name} 없는 값] 닫은 뒤 새로 남은 최상위 창: {new or '없음'}")
    ui.scroll_to_top(grid)


def compare_paths(lw, pid: int, grid, code: str, label: str) -> None:
    """실제 흐름 그대로 — 그 상품의 `check_matching_rows`(dry-run, 체크 안 누름)를 찾기 경로(pid 있음)와 맨 위부터(pid 없음)로
    한 번씩 돌려 고른 행·시간을 견준다. 보류 확인(`verify_holds`, 읽기만)도 두 길로."""
    ui.scroll_to_top(grid)
    started = now()
    jumped = lw.check_matching_rows(grid, code, dry_run=True, pid=pid, grouped=True)
    t_jump = now() - started
    ui.scroll_to_top(grid)
    started = now()
    scanned = lw.check_matching_rows(grid, code, dry_run=True, pid=None, grouped=True)
    t_scan = now() - started
    same = (jumped[0], jumped[2]) == (scanned[0], scanned[2])
    say(f"[{label} 체크 dry-run] 찾기 경로: 체크 {jumped[0]}·이미 보류 {jumped[2]} {t_jump:.1f}초 / "
        f"맨 위부터: 체크 {scanned[0]}·이미 보류 {scanned[2]} {t_scan:.1f}초 / {'같다' if same else '** 다르다 **'}")
    if not jumped[2]:
        say(f"[{label} 보류 확인] 이미 보류된 행이 없어 견줄 것이 없다 (기대 0 이면 첫 화면에서 끝난다)")
        ui.scroll_to_top(grid)
        return
    expected = {code: jumped[2]}                            # 이미 보류된 수만큼 — 읽기만 한다
    started = now()
    held_jump = lw.verify_holds(grid, expected, grouped=True, pid=pid)
    t_vjump = now() - started
    started = now()
    held_scan = lw.verify_holds(grid, expected, grouped=True, pid=None)
    t_vscan = now() - started
    say(f"[{label} 보류 확인] 찾기 경로 {held_jump['short'] or '모자람 없음'} {t_vjump:.1f}초 / "
        f"맨 위부터 {held_scan['short'] or '모자람 없음'} {t_vscan:.1f}초")
    ui.scroll_to_top(grid)


def top_find_tests(lw, target, pid: int, main_hwnd: int, top) -> None:
    page_cost("상단", top, (lw.TOP_CODE_COLUMN, lw.SHORTAGE_COLUMN))
    find_tests("상단", target, pid, main_hwnd, top, lw.TOP_CODE_COLUMN)


# ------------------------------------------------------------------ ⑤ 하단
def bottom_survey(lw, target, pid: int, main_hwnd: int, top, bottom, limit: int, deep: int,
                  mechanics: bool = True) -> None:
    """부족 상품마다 상단 행을 골라 하단을 정렬하고 — 실제 흐름이 찾기를 쓸지(`_worth_finding`) 센다.
    쓰는 상품은 `deep` 개까지 찾기 경로와 맨 위부터를 견준다(dry-run). 큰 하단 하나로 찾기 자체(간 자리·돌아서·포함·없는 값)도 잰다."""
    handle = lw.process_handle(pid)
    column = lw.BOTTOM_CODE_COLUMN
    ui.scroll_to_top(top)
    seen: set[int] = set()
    done = compared = 0
    measured, mechanics = False, not mechanics
    tally = {"아래 더 있음": 0, "첫 화면에 없음": 0, "찾기 씀": 0}
    for page in range(lw.MAX_SCROLLS):
        fresh = 0
        for row in ui.grid_rows(top):
            number = ui.row_number(row)
            if number is None or number in seen:
                continue
            seen.add(number)
            fresh += 1
            if ui.is_group_row(row) or not ui.cell_text(row, lw.SHORTAGE_COLUMN).strip():
                continue
            code = ui.cell_text(row, lw.TOP_CODE_COLUMN).strip()
            lw.focus_top_row(row, code)
            started = now()
            shown = lw.wait_bottom_ready(bottom, code, handle)
            ready = now() - started
            grouped = lw.ensure_bottom_sorted(bottom, handle)
            ui.scroll_to_top(bottom)
            on_first = code in visible_codes(bottom, column)
            more = has_more(bottom)
            pages = lw._pages_below(bottom) if more else 0.0
            would = bool(grouped and not on_first and more and SAFE_CODE.match(code) and lw._worth_finding(bottom))
            done += 1
            tally["아래 더 있음"] += more
            tally["첫 화면에 없음"] += not on_first
            tally["찾기 씀"] += would
            say(f"  [하단 {done}] 재조회 {ready:.1f}초·보이는 {shown}행 / 정렬 {grouped} / 첫 화면에 그 코드 {on_first} / "
                f"아래 남은 화면 {pages if pages is None else round(pages, 1)} / 실제 흐름이 찾기를 쓴다 {would}")
            if more and not measured:
                measured = True
                say(f"  [하단] 스크롤바 요소 {bar_parts(bottom)}")
                page_cost("하단", bottom, (column, lw.HOLD_COLUMN, ui.CHECK_COLUMN, lw.SLIP_NUMBER_COLUMN))
            if would and compared < deep:
                compared += 1
                compare_paths(lw, pid, bottom, code, f"하단 {done}")
            if more and (pages or 0) >= 2 and not mechanics:
                mechanics = True                                   # 큰 하단에서 찾기가 어디에 떨어지나
                find_tests("하단", target, pid, main_hwnd, bottom, column)
            if done >= limit:
                say(f"[하단] 상한 {limit}개까지 봤다 — {tally}")
                return
        if fresh == 0 and page > 0:
            break
        if not ui.scroll_down_verified(top, rounds=1):
            break
    say(f"[하단] 부족 상품 {done}개 — {tally}, 견줌 {compared}번")


def top_row_of(lw, top, code: str):
    """상단에서 그 상품코드 행 (맨 위부터 내려가며). 없으면 None."""
    ui.scroll_to_top(top)
    for _ in range(lw.MAX_SCROLLS):
        for row in ui.grid_rows(top):
            if not ui.is_group_row(row) and ui.cell_text(row, lw.TOP_CODE_COLUMN).strip() == code:
                return row
        if not ui.scroll_down_verified(top, rounds=1):
            return None
    return None


def watch_requery(lw, pid: int, top, bottom, first: str, second: str, seconds: float) -> None:
    """first 상품을 골라 하단이 그 상품으로 찬 뒤, second 를 고르고 `seconds` 동안 하단이 언제·어떻게 바뀌나 본다.
    `wait_bottom_ready` 의 판정 시각도 같이 찍는다 (코드가 안 보이면 '조용함+2초' 로 끝내는 갈래)."""
    handle = lw.process_handle(pid)
    column = lw.BOTTOM_CODE_COLUMN
    rect = rect_of(bottom)

    def observe(code: str, started: float, until: float) -> None:
        """하단이 바뀔 때마다 한 줄 — 보이는 행·코드·덮개·그 상품 보임·아래 남은 화면·왕복."""
        base = set(children(handle))
        last = None
        while now() - started < until:
            codes = visible_codes(bottom, column)
            covers = sum(1 for h, (_cls, r) in children(handle).items()
                         if h not in base and r and overlap(r, rect) >= 0.5)
            state = (ui.visible_row_count(bottom), tuple(sorted(codes)[:6]), covers > 0, lw._pages_below(bottom))
            if state != last:
                say(f"  [{now() - started:5.1f}s] 보이는 {state[0]}행 · 코드 {list(state[1])} · 덮개 {state[2]} · "
                    f"그 상품 보임 {code in codes} · 아래 남은 화면 {state[3]} · "
                    f"왕복 {winprobe.ui_roundtrip(handle) * 1000:.1f}ms")
                last = state
            threading.Event().wait(0.5)                     # 시험 도구의 관찰 간격 (상한 until)

    for code, label in ((first, "먼저"), (second, "다음")):
        row = top_row_of(lw, top, code)
        if row is None:
            say(f"[재조회] 상단에 {label} 상품이 없다")
            return
        started = now()
        lw.focus_top_row(row, code)
        count = lw.wait_bottom_ready(bottom, code, handle, timeout=60)     # 흐름과 같은 판정
        say(f"[재조회] {label} 상품 — wait_bottom_ready 끝 {now() - started:.1f}초, 보이는 {count}행")
        ui.scroll_to_top(bottom)
        observe(code, started, seconds if label == "다음" else 15.0)      # 판정 뒤에도 하단이 바뀌나


def find_check(lw, pid: int, top, bottom, product: str) -> None:
    """그 상품을 골라 정렬한 하단에서, 첫 화면 밖 코드로 **제품 `find_code`** 를 부른다 — 맨 앞 창·하단 포커스 확인 포함.
    상단에 포커스를 둔 채로도 한 번 부른다 (포커스 확인이 막아야 한다)."""
    handle = lw.process_handle(pid)
    column = lw.BOTTOM_CODE_COLUMN
    row = top_row_of(lw, top, product)
    if row is None:
        say("[find_code] 상단에 그 상품이 없다")
        return
    lw.focus_top_row(row, product)
    lw.wait_bottom_ready(bottom, product, handle, timeout=60)
    lw.ensure_bottom_sorted(bottom, handle)
    values, _ = ui.scan_column(bottom, column)
    ui.scroll_to_top(bottom)
    first_screen = visible_codes(bottom, column)
    later = [(n, (v or "").strip()) for n, v in sorted(values.items())
             if (v or "").strip() and (v or "").strip() not in first_screen]
    if not later:
        say("[find_code] 첫 화면 밖 코드가 없다")
        return
    number, code = later[-1]
    first_no = min(n for n, v in values.items() if (v or "").strip() == code)
    say(f"[find_code] 하단 그리드 핸들 {getattr(bottom, 'handle', None)} / 찾을 코드의 첫 행 {first_no}")
    started = now()
    say(f"[find_code] 결과 {lw.find_code(bottom, code, pid)} — {now() - started:.2f}초 (기대 첫 행 {first_no})")
    say(f"[find_code] 남은 찾기·완료 창 {lw._finder_windows(pid)}")
    ui.scroll_to_top(bottom)
    lw.focus_top_row(top_row_of(lw, top, product), product)          # 포커스를 상단에 둔다
    lw.wait_bottom_ready(bottom, product, handle, timeout=60)
    real_click = ui.click
    try:
        ui.click = lambda ctrl, what, **k: "건너뜀"                 # 코드 칸 클릭을 빼서 포커스가 상단에 남게
        say(f"[find_code 상단 포커스] 결과 {lw.find_code(bottom, code, pid)} (None 이어야 — Ctrl+F 를 안 보낸다)")
    finally:
        ui.click = real_click
    say(f"[find_code] 남은 찾기·완료 창 {lw._finder_windows(pid)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="찾기 점프·로딩 창 측정 (데이터 안 바꿈)")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--pid", type=int, help="이미 로그인된 ERPia")
    where.add_argument("--launch", action="store_true", help="흐름처럼 ERPia 를 켜고 설정 계정으로 로그인")
    parser.add_argument("--dry-run", action="store_true", help="화면을 열거나 키를 보내지 않는다")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--limit", type=int, default=30, help="하단을 볼 부족 상품 수 상한")
    parser.add_argument("--deep", type=int, default=2, help="찾기를 쓰는 상품 중 두 길을 견줄 수 (dry-run)")
    parser.add_argument("--survey-only", action="store_true",
                        help="상단 찾기 시험·하단 찾기 동작 시험을 건너뛰고 상품별 판정과 두 길 견주기만")
    parser.add_argument("--requery", nargs=2, metavar=("먼저", "다음"), default=None,
                        help="두 상품코드 — 먼저 것을 고른 뒤 다음 것을 고르고 하단이 바뀌는 것을 본다 (값은 명령줄에만)")
    parser.add_argument("--seconds", type=float, default=30.0, help="--requery 로 지켜볼 시간")
    parser.add_argument("--find-check", default=None, metavar="상품코드",
                        help="그 상품의 하단에서 제품 find_code 를 부른다 (포커스 확인 포함, 값은 명령줄에만)")
    parser.add_argument("--company", default=None,
                        help="--launch 의 업체코드 (아이디·비밀번호는 설정 값). 설정과 다른 시험 업체용 — 값은 명령줄에만")
    args = parser.parse_args()
    if not args.dry_run and not args.yes:
        print("--dry-run 이나 --yes 가 필요하다")
        return 2

    setup_logging()
    from automation import logistics_wait as lw
    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    target = launch_and_login(args.company) if args.launch else connect(SETTINGS.target_exe, pid=args.pid)
    if target is None:
        print(f"pid={args.pid} 에 붙지 못했다")
        return 2
    pid = target.pid
    main_hwnd = lw.process_handle(pid)
    say(f"pid={pid} 메인 창 hwnd={main_hwnd}")
    if args.dry_run:
        say("[dry-run] 여기까지. 화면을 열거나 키를 보내지 않았다.")
        return 0

    marks: dict = {}
    screen = lw.open_screen(target)
    lw.activate_tab(screen, lw.TAB_STOCK_REVIEW)
    top = lw._grid(screen, lw.TOP_GRID_AUTO_ID, "상단")
    bottom = lw._bottom_grid(screen)
    if args.find_check:
        lw._wait_rows(top, "상단 재고 그리드", loading=lw.click_search(screen))
        find_check(lw, pid, top, bottom, args.find_check)
        return 0
    if args.requery:
        lw._wait_rows(top, "상단 재고 그리드", loading=lw.click_search(screen))
        watch_requery(lw, pid, top, bottom, args.requery[0], args.requery[1], args.seconds)
        return 0
    grids = {"상단": rect_of(top), "하단": rect_of(bottom)}
    watch = Watch(main_hwnd, grids)
    watch.start()
    try:
        marks["search_click"] = now()
        loading = lw.click_search(screen)
        rows = lw._wait_rows(top, "상단 재고 그리드", loading=loading)
        marks["rows_ok"] = now()
        say(f"① [조회] 새 판정 — 보이는 {rows}행 ({marks['rows_ok'] - marks['search_click']:.2f}초)")
        pause(RECHECK_AFTER, "판정 뒤 행이 늦게 붙는지 한 번 더 센다")
        again = ui.visible_row_count(top)
        say(f"① {RECHECK_AFTER:.0f}초 뒤 다시 세기 — {again}행 "
            f"{'(같다)' if again == rows else '** 다르다 — 판정이 일렀다 **'}")
    finally:
        watch.stop()
        watch.report(marks)

    report_grid("상단", top)
    if args.survey_only:
        say("[상단] --survey-only — 상단 찾기 시험은 건너뛴다")
    elif has_more(top):
        top_find_tests(lw, target, pid, main_hwnd, top)
    else:
        say("[상단] 화면 밖 행이 없다 — 상단 찾기 시험은 건너뛴다")
    bottom_survey(lw, target, pid, main_hwnd, top, bottom, args.limit, args.deep, mechanics=not args.survey_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
