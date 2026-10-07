r"""[조작 - 데이터는 바꾸지 않는다] 물류대기 [상품별 재고검토] 에서 찾기(Ctrl+F) 점프와 조회 중 로딩 창을 잰다 (10-07).

사용자 요청 "실제로 그런지 시험이 필요" — 다른 RPA 는 Ctrl+F 가 그리드 안 패널이 아니라 **따로 뜨는 '찾기' 창**(입력칸 `txt_Key`)이고
`Desktop(uia).windows()` 는 놓쳐 Win32 로 잡는다고 적었다. 우리 기록(`docs/CONTROLS.md` 찾기 패널 '꺼져 있다', ERROR_HANDLING #7)과 다르다.
같이 재는 것: [조회]·상단 행 선택 때 그리드를 덮는 자식 창(로딩 표시)이 생기는지, 우리 완료 판정과 견주어 언제 사라지는지.

누르는 것: 물류대기 메뉴·[재고검토] 탭·[조회]·상단 행 하나(하단 재조회)·하단 정렬 머리글·찾기 창 입력. 저장·보류·물류처리·체크는 누르지 않는다.
상품코드는 실행 중 그리드에서 읽는다 (코드에 실값 없음).

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
from utils.wait import WaitTimeout, wait_for  # noqa: E402

FIND_TITLE = "찾기"          # 다른 팀 사본 기준 — 이 시험이 확인하는 값
FIND_EDIT_ID = "txt_Key"     # 같은 출처
SAFE_CODE = re.compile(r"^[0-9A-Za-z_-]+$")   # type_keys 특수문자(^ % + ~ () {}) 가 든 코드는 보내지 않는다
FIND_OPEN_TIMEOUT = 3.0
FIND_CLOSE_TIMEOUT = 3.0
JUMP_TIMEOUT = 5.0
WATCH_INTERVAL = 0.05
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
        say(f"[자식 창] 기준선 {len(self.base)}개, 새로 생긴 창 {len(self.seen)}개")
        for handle, rec in sorted(self.seen.items(), key=lambda item: item[1]["first"]):
            rect = rec["rect"]
            cover = {name: round(overlap(rect, grid), 2) for name, grid in self.grids.items()}
            alive = "지금도 보임" if end - rec["last"] < 0.2 else f"사라짐 {rec['last']:.2f}s"
            say(f"  hwnd={handle} class={rec['cls']!r} 크기={(rect[2] - rect[0], rect[3] - rect[1])} 그리드 덮음={cover} "
                f"생김 {rec['first']:.2f}s / {alive} (떠 있던 {rec['last'] - rec['first']:.2f}초)")
        for start, done, label in (("search_click", "rows_ok", "[조회] → 우리 판정(_wait_rows)"),
                                   ("top_click", "bottom_ready", "상단 행 선택 → 우리 판정(wait_bottom_ready)")):
            if start not in marks or done not in marks:
                continue
            covers = [rec["last"] for rec in self.seen.values()
                      if marks[start] - 0.05 <= rec["first"] <= marks[done] + 5
                      and any(overlap(rec["rect"], grid) >= 0.5 for grid in self.grids.values())]
            ours = marks[done] - marks[start]
            if not covers:
                say(f"  {label}: 그리드를 덮는 새 창이 안 잡혔다 (우리 판정 {ours:.2f}초)")
                continue
            gone = max(covers) - marks[start]
            say(f"  {label}: 우리 판정 {ours:.2f}초 / 덮개가 사라진 때 {gone:.2f}초 / 차이 {ours - gone:+.2f}초 "
                "(음수면 우리가 덮개가 남은 채 끝났다고 봤다)")


def finders(pid: int, main: int) -> tuple[list[int], list[int]]:
    """'찾기' 창 — (Win32 EnumWindows 로 찾은 것, Desktop(uia) 로 찾은 것)."""
    by_enum = [w.handle for w in winprobe.top_windows(pid) if w.title == FIND_TITLE and w.handle != main]
    try:
        from pywinauto import Desktop
        by_uia = [int(w.handle) for w in Desktop(backend="uia").windows(title=FIND_TITLE, process=pid)
                  if int(w.handle) != main]
    except Exception as exc:                # noqa: BLE001 — 조사: 실패도 결과다
        say(f"  Desktop(uia).windows 오류 {type(exc).__name__}: {exc}")
        by_uia = []
    return by_enum, by_uia


def wait_finder(pid: int, main: int) -> tuple[int | None, dict]:
    started = now()
    first: dict = {"Win32": None, "UIA": None}
    handle = None
    while now() - started < FIND_OPEN_TIMEOUT and None in first.values():
        by_enum, by_uia = finders(pid, main)
        if by_enum and first["Win32"] is None:
            first["Win32"], handle = round(now() - started, 2), by_enum[0]
        if by_uia and first["UIA"] is None:
            first["UIA"] = round(now() - started, 2)
            handle = handle or by_uia[0]
        threading.Event().wait(0.1)         # 조사 도구의 짧은 폴링 간격 (상한 FIND_OPEN_TIMEOUT)
    return handle, first


def close_finder(pid: int, main: int, handle: int) -> bool:
    _user32.PostMessageW(wintypes.HWND(handle), WM_CLOSE, 0, 0)
    try:
        wait_for(lambda: handle not in finders(pid, main)[0], "찾기 창 닫힘", timeout=FIND_CLOSE_TIMEOUT)
    except WaitTimeout:
        say("** 찾기 창이 안 닫혔다 — 멈춘다. 손으로 닫아 주세요 **")
        return False
    say("찾기 창을 닫았다")
    return True


def ensure_foreground(pid: int, what: str) -> None:
    """키를 보내기 전 — 맨 앞 창이 이 ERPia 가 아니면 멈춘다 (키가 다른 프로그램으로 가면 안 된다)."""
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError(f"{what}: 맨 앞 창이 ERPia 가 아니다 (pid {owner.value}) — 키를 보내지 않고 멈춘다")


def launch_and_login():
    """흐름과 같은 순서 — 새 인스턴스 → 업데이트 처리 → 로그인 (`orchestrator/erpia_flow.launch_stage`·`login_stage`)."""
    from automation import updater
    from automation.application import start_new_instance
    from automation.login import login

    exe = SETTINGS.target_exe
    target = start_new_instance(exe, work_dir=str(Path(exe).parent))
    updater.settle(exe, target.pid)
    login(SETTINGS.login_company_code, SETTINGS.login_user_id, SETTINGS.login_password, pid=target.pid)
    say(f"ERPia 를 켜고 로그인했다 — pid={target.pid}")
    return target


def pick_grid(lw, top, bottom, args) -> tuple:
    """점프를 잴 그리드 — 부족 상품이 있으면 하단(다른 RPA 와 같은 곳), 없으면 상단."""
    ui.scroll_to_top(top)
    for row in ui.grid_rows(top):
        if ui.is_group_row(row) or not ui.cell_text(row, lw.SHORTAGE_COLUMN).strip():
            continue
        code = ui.cell_text(row, lw.TOP_CODE_COLUMN).strip()
        return "하단", bottom, lw.BOTTOM_CODE_COLUMN, (row, code)
    say("부족 상품이 없다 — 상단 그리드로 찾기를 잰다")
    return "상단", top, lw.TOP_CODE_COLUMN, None


def main() -> int:
    parser = argparse.ArgumentParser(description="찾기 점프·로딩 창 측정 (데이터 안 바꿈)")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--pid", type=int, help="이미 로그인된 ERPia")
    where.add_argument("--launch", action="store_true", help="흐름처럼 ERPia 를 켜고 설정 계정으로 로그인")
    parser.add_argument("--dry-run", action="store_true", help="화면을 열거나 키를 보내지 않는다")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-sort", action="store_true", help="하단 정렬을 누르지 않는다")
    args = parser.parse_args()
    if not args.dry_run and not args.yes:
        print("--dry-run 이나 --yes 가 필요하다")
        return 2

    setup_logging()
    from automation import logistics_wait as lw
    from automation.application import connect
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    target = launch_and_login() if args.launch else connect(SETTINGS.target_exe, pid=args.pid)
    if target is None:
        print(f"pid={args.pid} 에 붙지 못했다")
        return 2
    pid = target.pid
    main_hwnd = lw.process_handle(pid)
    say(f"pid={pid} 메인 창 hwnd={main_hwnd}")
    say(f"찾기 창 지금: {finders(pid, main_hwnd)}")
    if args.dry_run:
        say("[dry-run] 여기까지. 화면을 열거나 키를 보내지 않았다.")
        return 0

    marks: dict = {}
    screen = lw.open_screen(target)
    lw.activate_tab(screen, lw.TAB_STOCK_REVIEW)
    top = lw._grid(screen, lw.TOP_GRID_AUTO_ID, "상단")
    bottom = lw._bottom_grid(screen)
    grids = {"상단": rect_of(top), "하단": rect_of(bottom)}
    say(f"그리드 위치 {grids}")
    watch = Watch(main_hwnd, grids)
    watch.start()
    finder = None
    try:
        marks["search_click"] = now()
        lw.click_search(screen)
        lw._wait_rows(top, "상단 재고 그리드", timeout=SETTINGS.timeouts.dialog)
        marks["rows_ok"] = now()

        name, grid, column, top_pick = pick_grid(lw, top, bottom, args)
        handle = lw.process_handle(pid)
        if top_pick:
            row, top_code = top_pick
            marks["top_click"] = now()
            lw.focus_top_row(row, top_code)
            shown = lw.wait_bottom_ready(bottom, top_code, handle)
            marks["bottom_ready"] = now()
            say(f"하단 재조회 — 우리 판정 끝, 보이는 {shown}행")
            if not args.no_sort:
                say(f"하단 오름차순: {lw.ensure_bottom_sorted(bottom, handle)} (표시 순서만 바뀐다)")

        started = now()
        values, exact = ui.scan_column(grid, column)
        scan_time = now() - started
        say(f"[지금 방식] {name} '{column}' 끝까지 훑기 — {len(values)}행 {'끝까지 봄' if exact else '**끝까지 못 봄**'} {scan_time:.1f}초")
        ui.scroll_to_top(grid)
        if not values:
            say("그리드가 비었다 — 점프를 잴 수 없다. 멈춘다.")
            return 1
        code = values[max(values)].strip()
        first_no = min(no for no, value in values.items() if value.strip() == code)
        first_screen = {value.strip() for value in ui.column_values(grid, column).values()}
        say(f"점프 대상: 마지막 행의 코드 (첫 일치 행 번호 {first_no}) / 첫 화면에 이미 보임: {code in first_screen}")
        if not SAFE_CODE.match(code):
            say("코드에 특수문자가 있어 키로 보내지 않는다. 멈춘다.")
            return 1

        before = ui.column_values(grid, column)
        for attempt in (1, 2):
            ui.bring_forward(grid, f"{name} 그리드")
            if attempt == 2:        # 그리드에 포커스를 준다 — 코드 칸 (체크박스 칸 아님)
                ui.click(ui.cell(ui.grid_rows(grid)[0], column), f"{name} 코드 칸(포커스용)", methods=("클릭",))
            ensure_foreground(pid, "Ctrl+F")
            marks["ctrl_f"] = now()
            target.main_window().type_keys("^f", set_foreground=True)
            finder, first = wait_finder(pid, main_hwnd)
            say(f"[찾기 창] 시도 {attempt}{' (칸을 눌러 포커스)' if attempt == 2 else ''}: hwnd={finder} / "
                f"처음 잡힌 시각(초) {first} — None 이면 그 방법으로는 못 봄")
            if finder:
                break
        if not finder:
            say("찾기 창이 안 떴다. 이 pid 의 최상위 창: "
                + str([(w.handle, w.class_name, w.title) for w in winprobe.top_windows(pid)]))
            return 3
        from pywinauto import Application

        win = Application(backend="uia").connect(handle=finder, timeout=5).window(handle=finder)
        edits = win.descendants(control_type="Edit")
        say(f"찾기 창 class={winprobe._class_of(finder)!r} 크기={winprobe.rect_of(finder)} "
            f"Edit auto_id={[e.element_info.automation_id for e in edits]} "
            f"단추={[b.window_text() for b in win.descendants(control_type='Button')]}")
        key = next((e for e in edits if e.element_info.automation_id == FIND_EDIT_ID), None)
        if key is None:
            say(f"입력칸 {FIND_EDIT_ID} 가 없다 — 닫고 멈춘다 (위 Edit 목록이 조사 결과)")
            return 3
        win.set_focus()
        ensure_foreground(pid, "찾기 입력")
        marks["type"] = now()
        key.type_keys("^a" + code + "{ENTER}", set_foreground=True)
        try:
            wait_for(lambda: ui.column_values(grid, column) != before, "점프로 화면이 바뀜", timeout=JUMP_TIMEOUT,
                     interval=0.2)
            changed = round(now() - marks["type"], 2)
        except WaitTimeout:
            changed = None
        jump_time = now() - marks["type"]
        say(f"[점프] 입력 뒤 화면이 바뀌기까지 {changed}초 (None = 안 바뀜)")
        if not close_finder(pid, main_hwnd, finder):
            return 3
        finder = None
        after = ui.column_values(grid, column)
        hits = sorted(no for no, value in after.items() if (value or "").strip() == code)
        say(f"[점프 뒤] 보이는 행 {min(after) if after else None}~{max(after) if after else None}, 대상 일치 행 {hits[:5]}")
        if hits:
            say(f"  첫 일치 행 {hits[0]} (훑기로 안 첫 일치 {first_no}) → "
                f"{'첫 일치 행으로 갔다' if hits[0] == first_no else '첫 일치 행이 아니다'}")
        else:
            say("  점프했는데 대상 코드가 화면에 없다")
        say(f"[비교] 찾기 점프 {jump_time:.1f}초 vs 끝까지 훑기 {scan_time:.1f}초")
        ui.scroll_to_top(grid)
    finally:
        if finder:
            close_finder(pid, main_hwnd, finder)
        watch.stop()
        watch.report(marks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
