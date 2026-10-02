r"""Windows 파일 선택 Dialog 제어.

폴더를 하나씩 클릭해서 찾지 않는다. **파일명 입력창에 전체 경로를 직접 넣고 연다.**
(docs/REQUIREMENTS.md 6장)

표준 Windows Dialog(`#32770`) 다 — 확정값은 `docs/CONTROLS.md` "파일 선택 창" (09-08 실측).
폴백을 여러 겹 두었고, 실패하면 무엇이 보였는지 로그에 남긴다.
"""
from __future__ import annotations

import re

from pywinauto import Desktop
from pywinauto.findwindows import find_elements

from config.settings import SETTINGS
from utils import winprobe
from utils.logger import get_logger
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

DIALOG_CLASS = "#32770"                       # 표준 Windows 공통 대화상자
DIALOG_TITLE_RE = "열기|Open|불러오기|찾아보기|Browse|업로드"
FILENAME_LABEL_RE = "파일 이름.*|File name.*"
OPEN_BUTTON_RE = "열기.*|Open.*|확인.*"

# ★ 표준 공용 대화상자의 **컨트롤 ID**. 이름보다 이쪽이 안정적이다.
# Windows 공용 대화상자는 언어·버전·해상도와 무관하게 이 ID 를 쓴다
# (IDOK=1 / IDCANCEL=2 / 파일명 Edit=1148).
OPEN_BUTTON_AUTO_ID = "1"
FILENAME_EDIT_AUTO_ID = "1148"

# 이름으로 찾을 때 걸리지만 **눌러서는 안 되는** 것.
# 파일명 입력창 오른쪽의 자동완성 화살표가 name='열기' 로 잡힌다.
NOT_OPEN_BUTTON_AUTO_IDS = ("DropDown",)
# [열기] 를 누른 뒤 창이 닫히는지 보는 짧은 대기.
# 이 환경에서는 Invoke·클릭이 **정상적으로** 실패하고 입력창 Enter가 답이다
# (경로를 입력하면 창에 사이드바가 생겨 구조가 바뀐다. 사용자 확인 2026-09-04).
# 어차피 두 번은 실패하므로 확인 대기를 짧게 잡는다.
CONFIRM_TIMEOUT = 2.0


class FileDialogError(RuntimeError):
    """파일 선택 Dialog 처리 실패."""


def _desktop() -> Desktop:
    return Desktop(backend=SETTINGS.backend)


def _describe_window(win) -> str:
    try:
        info = win.element_info
        return (f"{info.name!r} class={info.class_name!r} "
                f"pid={info.process_id} type={info.control_type}")
    except Exception:
        return "(읽지 못함)"


def _wrap_handle(handle: int):
    """창 핸들을 UIA 래퍼로. **후보를 찾은 뒤에만** 부른다 — 비싸다."""
    return _desktop().window(handle=handle).wrapper_object()


class _TopWindow:
    """`winprobe.Window` 를 `consider()` 가 읽는 모양으로 맞춘다.

    UIA 요소를 만들지 않는다. 이름·클래스·pid 만 보고 거르기 위해서다.
    """

    __slots__ = ("name", "class_name", "handle", "process_id")

    def __init__(self, window) -> None:
        self.name = window.title
        self.class_name = window.class_name
        self.handle = window.handle
        self.process_id = window.pid


def _search_elements(**criteria) -> list:
    """조건에 맞는 요소를 찾는다. 실패하면 빈 목록."""
    try:
        return find_elements(backend=SETTINGS.backend, **criteria)
    except Exception as exc:
        log.debug("find_elements(%s) 실패: %s", criteria, exc)
        return []


def _candidates(pid: int | None) -> list:
    """파일 선택 창 후보를 돌려준다.

    **최상위 창만 봐서는 안 된다.** 이 프로그램의 팝업은 부모 창의 자식으로
    뜨는 경우가 있다 (로그인 팝업에서 이미 겪었다. docs/CONTROLS.md).
    2026-09-04에 `엑셀 불러오기` 창이 화면에 떠 있는데도 못 찾았다.

    그래서 두 갈래로 본다.
      1. 최상위 창
      2. **해당 프로세스의 트리 전체** (top_level_only=False)

    제목도 클래스도 환경에 따라 다르므로 둘 중 하나만 맞으면 후보로 본다.
    다만 pid를 알면 그 프로세스로 한정한다. 다른 프로그램의 '열기' 창에
    경로를 입력하는 사고를 막기 위해서다.
    """
    seen: set = set()
    found: list[tuple[int, int]] = []      # (점수, 창 핸들)

    def consider(element_or_wrapper) -> None:
        try:
            info = getattr(element_or_wrapper, "element_info", element_or_wrapper)
            name = info.name or ""
            class_name = info.class_name or ""
            handle = info.handle
            process_id = info.process_id
        except Exception:
            return
        if handle in seen:
            return
        if pid is not None and process_id != pid:
            return

        score = 0
        if class_name == DIALOG_CLASS:
            score += 2
        if re.search(DIALOG_TITLE_RE, name):
            score += 2
        if score < 2:
            return
        seen.add(handle)
        found.append((score, handle))

    # 1) **싼 Win32 최상위 창 스캔** (0.7ms). 대부분 여기서 끝난다.
    for window in winprobe.top_windows(pid):
        consider(_TopWindow(window))
    if found:
        found.sort(key=lambda item: item[0], reverse=True)
        return [_wrap_handle(handle) for _score, handle in found]

    # 2) 못 찾았을 때만 UIA. **`top_level_only=True` 로 좁힌다.**
    #    False 는 부모 없이 쓰면 데스크톱 전체 트리를 훑는다 (실측 3.0초/회).
    if pid is not None:
        for criteria in (
            {"class_name": DIALOG_CLASS},
            {"title_re": DIALOG_TITLE_RE, "control_type": "Window"},
        ):
            for element in _search_elements(process=pid, top_level_only=True, **criteria):
                consider(element)

    found.sort(key=lambda item: item[0], reverse=True)
    return [_wrap_handle(handle) for _score, handle in found]


def wait_for_dialog(pid: int | None = None, timeout: float | None = None):
    """파일 선택 Dialog가 뜰 때까지 기다린다."""
    timeout = SETTINGS.timeouts.dialog if timeout is None else timeout

    def probe():
        matches = _candidates(pid)
        return matches[0] if matches else None

    try:
        window = wait_for(probe, "파일 선택 Dialog", timeout=timeout)
    except WaitTimeout as exc:
        raise FileDialogError(
            f"{timeout:.0f}초 안에 파일 선택 Dialog를 찾지 못했다.\n"
            f"  찾은 조건: class={DIALOG_CLASS!r} 또는 제목={DIALOG_TITLE_RE!r}\n"
            f"  현재 최상위 창:\n    " + _window_list(pid)
        ) from exc

    log.info("파일 선택 Dialog: %s", _describe_window(window))
    return _desktop().window(handle=window.handle)


def _window_list(pid: int | None) -> str:
    """진단용. **대상 프로세스의 창만** 보여준다.

    예전에는 데스크톱의 최상위 창을 전부 찍었다. 카카오톡·탐색기·브라우저까지
    16줄이 나오고, 업로드가 한 번 실패하면 같은 목록이 세 번 찍혀
    실패 원인 한 줄이 50줄에 묻혔다 (2026-09-10 통합 실행: 로그 373줄 중 43줄).
    **다른 프로그램의 창은 이 실패와 아무 상관이 없다.**
    """
    lines = []
    try:
        others = 0
        for win in _desktop().windows():
            try:
                same = pid is None or win.element_info.process_id == pid
            except Exception:
                same = False
            if same:
                lines.append("[최상위] " + _describe_window(win))
            else:
                others += 1
        if others:
            lines.append(f"(다른 프로그램의 최상위 창 {others}개는 생략)")
    except Exception as exc:
        lines.append(f"(최상위 열거 실패: {exc})")

    # ★ 진단 목록에도 **싼 스캔**을 쓴다. 여기는 실패했을 때 도는 코드라
    #   느리면 실패 보고가 그만큼 늦어진다 (예전엔 여기서만 6초씩 더 들었다).
    for window in winprobe.top_windows(pid, visible_only=False):
        lines.append(f"[창] {window.title!r} class={window.class_name!r} "
                     f"pid={window.pid} visible={window.visible}")

    return ("\n    ").join(lines[:20]) or "(없음)"


def is_closed(dialog) -> bool:
    try:
        return not dialog.exists()
    except Exception:
        return True


def _closed_within(dialog, timeout: float) -> bool:
    """창이 닫히기를 잠깐 기다린다. 닫혔으면 True."""
    try:
        wait_for(lambda: is_closed(dialog), "파일 선택 Dialog 닫힘", timeout=timeout)
        return True
    except WaitTimeout:
        return False


def _find_filename_edit(dialog):
    from utils import ui

    for criteria in (
        {"auto_id": FILENAME_EDIT_AUTO_ID, "control_type": "Edit"},
        {"title_re": FILENAME_LABEL_RE, "control_type": "Edit"},
        {"control_type": "Edit"},
    ):
        matches = ui.search(dialog, **criteria)
        if matches:
            return matches[0]
    raise FileDialogError(
        "파일명 입력창(Edit)을 찾지 못했다.\n"
        f"  Dialog 안의 컨트롤: {_describe_children(dialog)}"
    )


def _find_open_button(dialog):
    from utils import ui

    # 1순위 — 컨트롤 ID. 이 창에서 [열기] 는 **SplitButton auto_id='1'** 이다
    # (2026-09-08 조사, name='열기(O)').
    # **control_type 을 반드시 함께 건다.** 파일 목록의 ListItem 에도 숫자 auto_id 가
    # 붙어서, auto_id 만으로 찾으면 목록의 파일이 걸린다 (2026-09-08 확인).
    for control_type in ("SplitButton", "Button"):
        matches = ui.search(dialog, auto_id=OPEN_BUTTON_AUTO_ID,
                            control_type=control_type)
        if matches:
            return matches[0]

    # 2순위 — 이름. 다만 **자동완성 화살표(auto_id='DropDown')를 걸러낸다.**
    # 이름만으로 찾으면 그 화살표 2개가 먼저 걸린다. 2026-09-08 이전 코드가
    # 실제로 그것을 눌렀고, 실패해서 [입력창 Enter] 폴백으로 우연히 동작하고 있었다.
    for control_type in ("SplitButton", "Button"):
        matches = [
            item for item in ui.search(dialog, title_re=OPEN_BUTTON_RE,
                                       control_type=control_type)
            if (item.element_info.automation_id or "") not in NOT_OPEN_BUTTON_AUTO_IDS
        ]
        if not matches:
            continue
        if len(matches) > 1:
            log.info("[열기] 후보가 %d개다 (%s). 첫 번째를 쓴다:",
                     len(matches), control_type)
            for item in matches:
                log.info("    %s", ui.describe(item))
        return matches[0]
    return None


def open_path(dialog, path: str, dry_run: bool = False) -> str:
    """파일명 입력창에 전체 경로를 넣고 연다. 사용한 방법을 돌려준다.

    폴더를 하나씩 클릭하지 않는다 (docs/REQUIREMENTS.md 6장).

    **[열기] 는 눌렀는지를 창이 닫히는지로 확인한다.**
    Invoke 는 예외 없이 '성공'하면서 실제로는 아무 일도 안 하는 경우가 있다
    (2026-09-04에 경로까지 입력해 놓고 여기서 멈췄다).
    """
    from utils import ui

    if dry_run:
        log.info("[dry-run] 파일 경로 입력 예정: %s", path)
        return "dry-run"

    edit = _find_filename_edit(dialog)
    log.info("파일명 입력창: %s", ui.describe(edit))

    # ★ **먼저 비운다.** 2026-09-10 실행에서 파일명 칸에 남아 있던 글자가
    #   앞에 붙어 경로가 깨졌고(`ns` + 경로), Windows 가
    #   `파일 이름이 올바르지 않습니다` 로 거부했다.
    written = _set_filename(edit, path)
    if written is None:
        # ★ **값이 다르면 [열기] 를 누르지 않는다.** 예전에는 경고만 남기고
        #   눌렀다 — 엉뚱한 파일을 열 수 있다.
        raise FileDialogError(
            "파일 경로를 제대로 넣지 못했다. [열기] 를 누르지 않았다.\n"
            f"  넣으려던 값: {path}\n"
            f"  파일명 칸: {(ui.cell_value(edit) or '')!r}"
        )

    button = _find_open_button(dialog)
    if button is None:
        log.info("[열기] 버튼을 찾지 못했다. Dialog 안의 컨트롤: %s",
                 _describe_children(dialog))

    attempts = []
    if button is not None:
        label = button.window_text() or "열기"
        attempts.append((
            f"[{label}] Invoke",
            lambda: ui.click(button, f"[{label}] 버튼", methods=("Invoke",)),
        ))
        attempts.append((
            f"[{label}] 클릭",
            lambda: ui.click(button, f"[{label}] 버튼", methods=("클릭",)),
        ))
    attempts.append((
        "입력창에서 Enter",
        lambda: edit.type_keys("{ENTER}", set_foreground=False),
    ))

    for index, (name, action) in enumerate(attempts):
        last = index == len(attempts) - 1
        try:
            action()
        except Exception as exc:
            log.info("%s 실패: %s", name, exc)
            continue
        if _closed_within(dialog, SETTINGS.timeouts.dialog if last else CONFIRM_TIMEOUT):
            log.info("파일 선택 창이 닫혔다 (%s).", name)
            return name
        log.info("%s 후에도 창이 닫히지 않았다. 다음 방법을 시도한다.", name)

    raise FileDialogError(
        "경로를 입력했으나 파일 선택 창이 닫히지 않았다.\n"
        f"  입력한 경로: {path}\n"
        f"  읽은 값: {written!r}\n"
        f"  Dialog 안의 컨트롤: {_describe_children(dialog)}"
    )


def _edit_value(edit) -> "str | None":
    """입력칸의 **값**만 읽는다. 읽을 수 없으면 None.

    ★ `ui.cell_value()` 를 쓰면 안 된다 (2026-09-11 확정).
      그 함수는 값이 비면 폴백으로 내려가 **하위 Text / `window_text()`** 를
      읽는다. 파일명 칸은 이름이 `'파일 이름(N):'` 이라, 정상적으로 비웠는데도
      그 라벨이 돌아와 **"안 비워졌다"** 가 된다.
      실제로 매 업로드마다 `파일명 칸을 비우지 못했다` 경고가 찍혔는데,
      비우기는 성공하고 있었다 — **확인하는 쪽이 틀렸다.**
    """
    try:
        value = edit.iface_value.CurrentValue
    except Exception as exc:
        log.debug("파일명 칸 Value Pattern 없음: %s", type(exc).__name__)
        return None
    return str(value or "").strip()


def _clear_filename(edit) -> bool:
    """파일명 칸을 비운다. 비었는지 확인해서 돌려준다.

    값을 **읽을 수 없으면** 실패로 보지 않는다. 어차피 뒤에서 경로를 넣고
    되읽어 확인하므로(`_set_filename`), 그쪽이 진짜 관문이다.
    """
    for how, action in (("Value Pattern", lambda: edit.set_edit_text("")),
                        ("키 입력", lambda: edit.type_keys("^a{DEL}",
                                                          set_foreground=False))):
        try:
            action()
        except Exception as exc:
            log.debug("파일명 칸 비우기 실패(%s): %s", how, type(exc).__name__)
            continue
        value = _edit_value(edit)
        if value is None:
            log.debug("파일명 칸 값을 읽을 수 없다(%s). 비웠다고 보고 진행한다.", how)
            return True
        if not value:
            return True
        log.info("파일명 칸이 %s 로는 비워지지 않았다 (남은 값 %r). 다음 방법을 시도한다.",
                 how, value)
    return False


def _set_filename(edit, path: str) -> "str | None":
    """파일명 칸에 경로를 넣고 **되읽어 확인한다.** 맞으면 그 값, 아니면 None.

    비우기 → 넣기 → 확인을 **두 번까지** 한다. 남은 글자가 앞에 붙어 경로가
    깨지는 일이 실제로 있었다 (2026-09-10).
    """
    from utils import ui

    want = path.strip()
    for attempt in (1, 2):
        if not _clear_filename(edit):
            log.warning("파일명 칸을 비우지 못했다 (%d회차).", attempt)
        ui.set_text(edit, path, "파일 경로")
        written = (ui.cell_value(edit) or "").strip()
        if written == want:
            return written
        log.warning("파일명이 다르다 (%d회차). 넣은 값=%r / 읽은 값=%r",
                    attempt, path, written)
    return None


def _describe_children(dialog) -> str:
    from utils import ui

    parts = []
    for item in ui.search(dialog)[:20]:
        try:
            info = item.element_info
            parts.append(f"{info.control_type}({info.name!r})")
        except Exception:
            continue
    return ", ".join(parts) or "(읽지 못함)"


def close_if_open(pid: int | None = None) -> bool:
    """열려 있는 파일 선택 창을 닫는다. 닫았으면 True.

    한 파일이 실패한 뒤 창이 남아 있으면 **다음 파일이 그 창을 보고 혼동한다.**
    그래서 다음으로 넘어가기 전에 정리한다 (2026-09-10).
    """
    from utils import ui

    matches = _candidates(pid)
    if not matches:
        return False
    window = _desktop().window(handle=matches[0].handle)
    for how, action in (
        ("취소 버튼", lambda: ui.click(
            ui.find(window, title_re="취소.*|Cancel", control_type="Button",
                    timeout=2, what="취소 버튼"), "[취소]")),
        ("Esc", lambda: window.type_keys("{ESC}", set_foreground=False)),
    ):
        try:
            action()
        except Exception as exc:
            log.debug("파일 선택 창 닫기 실패(%s): %s", how, type(exc).__name__)
            continue
        if _closed_within(window, CONFIRM_TIMEOUT):
            log.info("남아 있던 파일 선택 창을 닫았다 (%s).", how)
            return True
    log.warning("남아 있는 파일 선택 창을 닫지 못했다. 다음 단계가 영향을 받을 수 있다.")
    return False


def wait_closed(dialog, timeout: float | None = None) -> None:
    """Dialog가 닫힐 때까지 기다린다."""
    timeout = SETTINGS.timeouts.dialog if timeout is None else timeout

    def gone() -> bool:
        try:
            return not dialog.exists()
        except Exception:
            return True

    try:
        wait_for(gone, "파일 선택 Dialog 닫힘", timeout=timeout)
    except WaitTimeout as exc:
        raise FileDialogError(
            "파일을 열었으나 Dialog가 닫히지 않았다. 경로가 잘못됐을 수 있다."
        ) from exc
