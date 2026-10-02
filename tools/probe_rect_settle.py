r"""[조사 도구 — 읽기 전용] `rect_of` / `wait_rect_settled` / `recheck_cell` 회귀 확인.

**대상 프로그램을 건드리지 않는다.** 가짜 컨트롤만 쓴다.

    .venv\Scripts\python.exe -m tools.probe_rect_settle

## 왜 이 도구가 있나 (2026-09-15)

세 실행을 헤맸던 원인이 기능이 아니라 **계측이 죽어 있던 것**이었다.

    tuple(ctrl.rectangle())   # TypeError: 'RECT' object is not iterable

`RECT` 는 ctypes 구조체라 iterable 이 아니다. 비교가 매번 예외로 빠져
`wait_rect_settled()` 가 **항상 즉시 True** 였고, 그 상태로 "위치가 안 변했다" 는
틀린 결론을 두 번 냈다. 조용히 무동작이 되는 버그라 실행 로그로는 안 보인다.

그래서 **가짜 컨트롤도 진짜 `RECT` 를 돌려준다.** 평범한 튜플로 만들면
바로 이 버그를 못 잡는다 (실제로 못 잡았다).

느린 PC 에서만 재현되는 "클릭 순간에 셀이 움직인다" 를 좋은 PC 에서 확인할 수는
없다. 확인할 수 있는 것은 **움직이는 동안 기다리는 논리가 실제로 도는지**까지다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from pywinauto import win32structures  # noqa: E402

from utils import ui  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


class FakeCell:
    """읽을 때마다 정해 둔 위치를 차례로 돌려주는 가짜 셀.

    ★ **진짜 `RECT` 를 돌려준다.** 튜플로 바꾸면 이 도구의 의미가 없어진다.
    """

    def __init__(self, rects: list[tuple[int, int, int, int]], name: str = "가짜 셀"):
        self._rects = list(rects)
        self._name = name
        self.reads = 0

    def rectangle(self):
        index = min(self.reads, len(self._rects) - 1)
        self.reads += 1
        return win32structures.RECT(*self._rects[index])

    def __str__(self) -> str:
        return self._name


class BrokenCell:
    """위치를 읽을 수 없는 컨트롤. 판단 불가일 때 흐름을 막지 않는지 본다."""

    def rectangle(self):
        raise RuntimeError("창이 사라졌다")


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" — {detail}" if detail else "")
    return ok


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ rect_of")
    cell = FakeCell([(890, 520, 959, 543)])
    results.append(check("RECT 를 튜플로 읽는다",
                         ui.rect_of(cell) == (890, 520, 959, 543),
                         str(ui.rect_of(cell))))
    results.append(check("못 읽으면 None (예외를 올리지 않는다)",
                         ui.rect_of(BrokenCell()) is None))

    log.info("▶ wait_rect_settled")
    # 실측에서 본 두 단계 이동: 컬럼 정렬(가로 139px) → 행 정렬(세로 24px) → 정착
    moving = FakeCell([(890, 520, 959, 543),
                       (751, 520, 820, 543),
                       (751, 496, 820, 519)], "두 단계로 움직이는 셀")
    settled = ui.wait_rect_settled(moving, "두 단계로 움직이는 셀")
    results.append(check("움직이는 동안 기다렸다가 멎으면 True",
                         settled and moving.reads > 3,
                         f"읽기 {moving.reads}회, 마지막 {ui.rect_of(moving)}"))
    results.append(check("멎은 뒤의 위치가 마지막 위치다",
                         ui.rect_of(moving) == (751, 496, 820, 519)))

    # 계속 움직이면 상한에서 포기한다. 포기해도 예외를 올리지 않는다.
    forever = FakeCell([(x, 520, x + 69, 543) for x in range(900, 300, -3)],
                       "멎지 않는 셀")
    results.append(check("끝까지 안 멎으면 False (예외 없이 진행)",
                         ui.wait_rect_settled(forever, "멎지 않는 셀",
                                              timeout=0.5) is False,
                         f"읽기 {forever.reads}회"))

    results.append(check("위치를 못 읽으면 막지 않는다 (True)",
                         ui.wait_rect_settled(BrokenCell(), "읽을 수 없는 셀") is True))

    log.info("▶ recheck_cell")
    fresh = FakeCell([(751, 520, 820, 543), (751, 496, 820, 519)], "다시 잡은 셀")
    stale = FakeCell([(890, 520, 959, 543)], "처음 잡은 셀")

    real_find = ui.find
    try:
        ui.find = lambda *a, **kw: fresh        # 그리드에서 다시 잡히는 상황
        got = ui.recheck_cell(object(), "엑셀업로드", 16, fallback=stale)
        results.append(check("다시 잡은 셀을 돌려준다", got is fresh))
        results.append(check("돌려준 셀의 위치가 멎은 값이다",
                             ui.rect_of(got) == (751, 496, 820, 519)))

        def not_found(*a, **kw):
            raise ui.ControlNotFound("없다")

        ui.find = not_found                     # 다시 잡지 못하는 상황
        results.append(check("못 잡으면 처음 것을 그대로 쓴다",
                             ui.recheck_cell(object(), "엑셀업로드", 16,
                                             fallback=stale) is stale))
    finally:
        ui.find = real_find

    results += _check_reveal()
    results += _check_upload_button()
    results += _check_ui_review()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


def _check_ui_review() -> list[bool]:
    """09-28 `utils/ui.py` 검토에서 고친 것들 — 가짜 컨트롤로만 본다."""
    import ctypes
    from types import SimpleNamespace

    log.info("▶ ★ ui.py 검토 수정 (09-28)")
    out: list[bool] = []

    # 1) 키 입력 폴백: pywinauto 키 문법 글자를 감싼다
    typed: list[str] = []

    def no_value(_):
        raise RuntimeError("Value Pattern 없음")

    edit = SimpleNamespace(set_focus=lambda: None, set_edit_text=no_value,
                           type_keys=lambda keys, **k: typed.append(keys))
    ui.set_text(edit, "주문 (1)+a%^~{x}.xlsx", "시험 입력칸")
    out.append(check("키 입력 폴백은 `+^%~(){}` 를 글자로 넣는다",
                     typed[-1] == "주문 {(}1{)}{+}a{%}{^}{~}{{}x{}}.xlsx", typed[-1]))

    # 2) 화면 밖이면 Shift+클릭·우클릭을 하지 않는다
    clicks: list[str] = []
    real_onscreen = ui.ensure_onscreen
    ui.ensure_onscreen = lambda ctrl, what: False
    try:
        away = SimpleNamespace(click_input=lambda **k: clicks.append("shift"),
                               right_click_input=lambda: clicks.append("right"))
        for fn in (ui.shift_click, ui.right_click):
            try:
                fn(away, "화면 밖 셀")
            except ui.ControlNotFound:
                continue
        out.append(check("★ 창이 화면 밖이면 Shift+클릭·우클릭을 하지 않는다 (예외)",
                         clicks == [], str(clicks)))
    finally:
        ui.ensure_onscreen = real_onscreen

    # 3) 체크 상태는 잠깐 늦게 반영될 수 있다 — 기다렸다가 성공으로 본다
    real = (ui.check_state, ui.cell, ui.click)
    reads = {"n": 0}

    def late_state(row):
        reads["n"] += 1
        return reads["n"] >= 4               # 처음(누르기 전)과 직후 두 번은 옛 값

    try:
        ui.check_state = late_state
        ui.cell = lambda row, column, timeout=None: object()
        ui.click = lambda *a, **k: "클릭"
        out.append(check("체크가 늦게 반영돼도 기다렸다가 True",
                         ui.check_row(object(), checked=True) is True, f"읽기 {reads['n']}회"))
        ui.check_state = lambda row: False
        out.append(check("끝내 안 바뀌면 False",
                         ui.check_row(object(), checked=True) is False))
    finally:
        ui.check_state, ui.cell, ui.click = real

    # 4) 모니터 배율을 읽는다 (바탕화면 창 = 주 모니터)
    scale = ui._window_scale(ctypes.windll.user32.GetDesktopWindow())
    out.append(check("모니터 배율을 읽는다 (1.0~4.0)", 1.0 <= scale <= 4.0, f"{scale}"))
    return out


class FocusCell:
    """포커스를 받으면 **위로 올라오는** 가짜 셀. 진짜 `RECT` 를 돌려준다."""

    def __init__(self, before, after):
        self.before, self.after = before, after
        self.focused = 0

    def set_focus(self):
        self.focused += 1

    def rectangle(self):
        return win32structures.RECT(*(self.after if self.focused else self.before))


def _check_reveal() -> list[bool]:
    """★ 아래에 걸쳐 잘린 행의 셀 — **누르기 전에 보이게 하는가** (2026-09-17).

    실측: 셀 가운데가 가로 스크롤바 위라 클릭이 스크롤바에 떨어졌다.
    """
    log.info("▶ ★ 잘린 행의 셀은 누르기 전에 보이게 한다 (`reveal_cell`)")
    area = (318, 160, 920, 522)                 # comp01 실측 영역
    real_area = ui.grid_data_area
    out: list[bool] = []
    try:
        ui.grid_data_area = lambda grid: area
        clipped = FocusCell((710, 520, 779, 543), (710, 496, 779, 519))
        acted = ui.reveal_cell(object(), clipped, "시험 셀")
        out.append(check("가운데가 영역 밖이면 포커스로 올린다",
                         acted and clipped.focused == 1,
                         f"포커스 {clipped.focused}회"))
        out.append(check("올린 뒤 가운데가 영역 안이다",
                         ui._center_inside(clipped, area) is True))
        inside = FocusCell((710, 496, 779, 519), (0, 0, 0, 0))
        out.append(check("이미 안쪽이면 **아무것도 하지 않는다**",
                         not ui.reveal_cell(object(), inside, "시험 셀")
                         and inside.focused == 0))
        # 가운데 y=152 — 머리줄(아래 끝 160) 안쪽. ★ 160 에 두면 안 된다:
        #   Win32 사각형의 아래 끝은 포함하지 않아 160 은 이미 데이터 영역이다.
        header = FocusCell((710, 140, 779, 164), (710, 184, 779, 207))
        out.append(check("★ 머리줄에 걸친 셀도 올린다 (머리줄을 누르면 정렬이 바뀐다)",
                         ui.reveal_cell(object(), header, "시험 셀")
                         and header.focused == 1))
        ui.grid_data_area = lambda grid: None
        out.append(check("영역을 못 읽으면 막지 않는다 (예전처럼 누른다)",
                         not ui.reveal_cell(object(), FocusCell((0, 0, 1, 1), (0, 0, 1, 1)),
                                            "시험 셀")))
    finally:
        ui.grid_data_area = real_area
    return out


def _check_upload_button() -> list[bool]:
    """★ [엑셀업로드] 버튼이 없는 행은 **누르지 않고, 다시 하지도 않는다** (2026-09-17)."""
    from automation import order_mapping as om

    log.info("▶ ★ 버튼 없는 행은 누르지 않는다")
    real_value = ui.cell_value
    out: list[bool] = []
    try:
        for value, expected in (("1", True), ("0", False), ("", None), ("Y", None)):
            ui.cell_value = lambda cell, v=value: v
            out.append(check(f"셀 값 {value!r} → {expected}",
                             om.upload_button_present(object()) is expected))

        def broken(cell):
            raise RuntimeError("못 읽음")
        ui.cell_value = broken
        out.append(check("값을 못 읽으면 '알 수 없음' (눌러 보고 판정)",
                         om.upload_button_present(object()) is None))
    finally:
        ui.cell_value = real_value

    calls = []
    real_collect = om._collect_from_excel
    real_close = om.filedialog.close_if_open
    try:
        def no_button(*args, **kwargs):
            calls.append(1)
            raise om.NoUploadButton("버튼 없음")
        om._collect_from_excel = no_button
        om.filedialog.close_if_open = lambda pid=None: False
        results = om.upload_excels(object(), [("가짜사이트", Path("가짜.xlsx"))],
                                   dry_run=True)
        entry = results[0]
        out.append(check("★ 버튼이 없으면 **재시도하지 않는다** (한 번만 부른다)",
                         len(calls) == 1 and entry["attempts"] == 1,
                         f"부른 횟수 {len(calls)}"))
        out.append(check("실패로 남고 '업로드 실패' 로 분류된다",
                         not entry["ok"] and entry["kind"] == om.KIND_NOT_STARTED,
                         str(entry["error"])))
        out.append(check("버튼 없음은 '못 올렸다' 의 한 갈래다",
                         issubclass(om.NoUploadButton, om.UploadNotStarted)))
    finally:
        om._collect_from_excel = real_collect
        om.filedialog.close_if_open = real_close
    return out


if __name__ == "__main__":
    raise SystemExit(main())
