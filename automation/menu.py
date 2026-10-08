r"""메뉴 검색으로 화면 열기 — 좌측 아이콘이 없는 화면(업체 전용 메뉴 등)을 이름으로 연다 (10-08).

`docs/CONTROLS.md` "메뉴 검색으로 화면 열기" (10-06 실기): `sch_Menus` 를 누르고 메뉴명을 치면 따로 뜨는 목록이 아니라
**`acd_Nav` 트리가 걸러져** 그 항목(`TreeItem`, auto_id 없음, 이름 = 메뉴명)만 남는다. 누르면 같은 이름의 메인 탭이 생긴다.

| 함정 (실측) | 그래서 |
| --- | --- |
| 이미 열린 화면은 메뉴를 눌러도 그 탭으로 가지 않는다 (60초 기다림) | 같은 이름의 메인 탭이 있으면 그 탭을 누른다 |
| 걸러지는 동안 트리가 다시 배치된다 | 항목이 트리 안에 보이고 **두 번 연속 같은 자리**일 때 누른다 |
| 키가 다른 칸(막 열린 화면의 그리드)으로 새면 `^a`·글자가 들어간다 | 키보드 포커스가 검색 칸 안일 때만 친다 (`winprobe.focus_handle`) |
| 걸러진 트리가 남으면 다음 화면 이동이 막힌다 | 연 뒤 검색 칸을 비운다 |

연 화면이 맞는지는 부르는 쪽이 `ready()` 로 본다 (그 화면만의 컨트롤) — 탭 이름만으로는 화면 안의 서브탭과 헷갈린다.
"""
from __future__ import annotations

import re

from utils import ui, winprobe
from utils.logger import get_logger
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

SEARCH_AUTO_ID = "sch_Menus"
NAV_AUTO_ID = "acd_Nav"
ITEM_WAIT = 15.0        # 걸러진 트리에 항목이 나타나기를 기다리는 상한
ITEM_POLL = 0.5
OPEN_WAIT = 30.0        # 항목·탭을 누른 뒤 화면이 뜨기를 기다리는 상한
_KEY_SPECIAL = re.compile(r"([+^%~(){}\[\]])")


class MenuError(RuntimeError):
    """메뉴로 화면을 열지 못했다."""


def literal_keys(text: str) -> str:
    """`type_keys` 에서 특수 문자가 명령으로 읽히지 않게 — 글자 그대로 친다."""
    return _KEY_SPECIAL.sub(r"{\1}", text)


def open_by_menu(main_window, name: str, ready, timeout: float = OPEN_WAIT):
    """`name` 메뉴의 화면을 연다. 이미 열렸으면 그 탭을 누른다. `ready()` 가 참이 될 때까지 기다려 그 값을 돌려준다.

    `ready()` 는 그 화면만의 컨트롤(폼)을 찾아 돌려주는 함수 — 없으면 None. 못 열면 `MenuError`.
    """
    found = ready()
    if found:
        log.info("'%s' 화면이 이미 앞에 있다", name)
        return found
    tab = _main_tab(main_window, name)
    if tab is not None:
        log.info("'%s' 화면이 이미 열려 있어 그 탭을 누른다", name)
        ui.click(tab, f"탭 '{name}'", methods=("클릭",))      # 10-07 실측: 탭은 눌러서 옮겼다 (Select 는 미확인)
    else:
        _search_and_click(main_window, name)
    try:
        return wait_for(ready, f"'{name}' 화면", timeout=timeout)
    except WaitTimeout as exc:
        raise MenuError(f"'{name}' 메뉴를 눌렀으나 화면이 열리지 않았다 ({timeout:.0f}초)") from exc
    finally:
        if tab is None:
            clear_search(main_window)


def _main_tab(main_window, name: str):
    """이미 열린 화면의 메인 탭 — 이름이 같고 창 안에 다 보이는 TabItem. 없으면 None."""
    for item in ui.find_all(main_window, control_type="TabItem"):
        try:
            if (item.window_text() or "").strip() == name and ui.rect_of(item) and ui._on_screen(item.rectangle()):
                return item
        except Exception:
            continue
    return None


def _search_and_click(main_window, name: str) -> None:
    search = ui.find(main_window, auto_id=SEARCH_AUTO_ID, what="메뉴 검색창")
    nav = ui.find(main_window, auto_id=NAV_AUTO_ID, what="메뉴 트리")
    _focus_search(main_window, search)
    search.type_keys("^a{BACKSPACE}", set_foreground=False)
    search.type_keys(literal_keys(name), with_spaces=True, set_foreground=False)
    log.info("메뉴 검색: '%s'", name)
    item = _settled_item(nav, name)
    if item is None:
        clear_search(main_window)
        raise MenuError(f"메뉴 검색 결과에 '{name}' 가 없다 ({ITEM_WAIT:.0f}초) — 이 계정에 메뉴가 없거나 이름이 다르다")
    ui.click(item, f"메뉴 '{name}'", methods=("클릭",))


def _focus_search(main_window, search) -> None:
    """검색 칸을 눌러 키보드 포커스를 옮긴다. 포커스가 그 칸 안이 아니면 키를 보내지 않고 멈춘다."""
    ui.click(search, "메뉴 검색창", methods=("클릭",))
    handle = getattr(search, "handle", None)
    try:
        wait_for(lambda: handle and winprobe.is_within(winprobe.focus_handle(main_window.handle), handle),
                 "메뉴 검색창 포커스", timeout=3.0)
    except WaitTimeout as exc:
        raise MenuError("메뉴 검색창에 키보드 포커스가 오지 않아 입력하지 않았다") from exc


def _settled_item(nav, name: str):
    """걸러진 트리에서 이름이 `name` 인 항목 — 트리 안에 보이고 두 번 연속 같은 자리일 때. 없으면 None."""
    last: list = [None]

    def settled():
        here, item = None, None
        box = nav.rectangle()
        for candidate in ui.find_all(nav, control_type="TreeItem"):
            if (candidate.window_text() or "").strip() != name:
                continue
            r = candidate.rectangle()
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            if r.width() > 0 and r.height() > 0 and box.left <= cx <= box.right and box.top <= cy <= box.bottom:
                here, item = (r.left, r.top, r.right, r.bottom), candidate
                break
        same = here is not None and here == last[0]
        last[0] = here
        return item if same else None

    try:
        return wait_for(settled, f"메뉴 항목 '{name}'", timeout=ITEM_WAIT, interval=ITEM_POLL)
    except WaitTimeout:
        return None


def clear_search(main_window) -> None:
    """검색 칸을 비워 트리를 되돌린다. 포커스가 칸 안일 때만 친다. 실패해도 화면 이동 결과는 그대로 (로그만)."""
    try:
        search = ui.find(main_window, auto_id=SEARCH_AUTO_ID, what="메뉴 검색창", timeout=2)
        _focus_search(main_window, search)
        search.type_keys("^a{BACKSPACE}", set_foreground=False)
    except Exception as exc:                 # noqa: BLE001 — 비우기는 덤이다
        log.warning("메뉴 검색창을 비우지 못했다 (%s) — 다음 메뉴 이동이 걸러진 트리에서 막힐 수 있다",
                    type(exc).__name__)
