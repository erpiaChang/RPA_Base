r"""로그인 자동화 (Phase 4).

근거: docs/UI_SURVEY.md "화면: 로그인" (2026-09-03 조사)

    Window   auto_id="LoginForm"  (title은 "로그인     v842"처럼 버전이 붙는다)
    업체코드  Edit  auto_id="txt_Admin_Code"
    아이디    Edit  auto_id="txt_ID"
    비밀번호  Edit  auto_id="txt_Password"
    로그인    Text  auto_id="lbl_LoginBtn"   ← Button이 아니다

각 입력 Edit 안에 자식 Edit이 있고 그 auto_id는 숫자(핸들 계열)다.
실행마다 바뀌므로 쓰지 않는다. 부모의 txt_* 만 쓴다.

로그인 버튼이 Text control이라 Invoke가 먹는지 확인되지 않았다.
Invoke → 컨트롤 클릭 → Enter 순서로 시도하고, 실제로 무엇이 통했는지 로그에 남긴다.
(클릭은 컨트롤 위치 기준이다. 화면 절대좌표를 하드코딩하지 않는다.)

로그인 직후 경고 팝업이 뜨는 계정이 있다.
  "입력된 전화번호가 없어 2차 인증을 할 수 없습니다." → [확인(O)]
이 팝업은 닫고 계속 진행한다. 그 외의 팝업은 로그인 실패로 보고 내용을 그대로 보고한다.

비밀번호 변경 요구 창(`Frm_ChangePassword`)은 늘 [다음에 변경하기] 를 누른다 (사용자 확정 10-07).
그 단추가 없거나 꺼져 있으면(강제 변경) 누르지 않고 `LoginRejected` — 사람이 바꿔야 한다.

비밀번호는 로그에 남기지 않는다.
"""
from __future__ import annotations

import re
import time

from pywinauto import Desktop

from config.settings import SETTINGS
from utils.logger import get_logger, save_screenshot, step
from utils.process import screen_locked
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

LOGIN_FORM_AUTO_ID = "LoginForm"
LOGIN_TITLE_RE = "로그인.*"

FIELD_AUTO_IDS = {
    "업체코드": "txt_Admin_Code",
    "아이디": "txt_ID",
    "비밀번호": "txt_Password",
}
LOGIN_BUTTON_AUTO_ID = "lbl_LoginBtn"

# 로그인 직후 나타날 수 있는 팝업.
# auto_id는 조사되지 않았다. 창 제목과 버튼 텍스트로 찾는다.
# 실제 확인된 제목: "경고"(2차 인증 안내), "로그인 실패"(중복 로그인 / 계정 오류)
DIALOG_TITLE_RE = re.compile(r"경고|알림|오류|에러|실패|Warning|Error", re.I)
CONFIRM_BUTTON_TITLE_RE = "확인.*|예.*|OK"

# 닫고 계속 진행해도 되는 팝업 (2차 인증 미설정 안내)
CONTINUABLE_RE = re.compile(r"2\s*차\s*인증|전화번호가 없어")

# 같은 아이디로 이미 로그인돼 있다. 기존 인스턴스를 닫고 다시 시도한다.
DUPLICATE_RE = re.compile(r"동시에 같은 아이디")

# 계속해도 소용없는 팝업. 즉시 중단한다.
FATAL_PATTERNS = (
    (re.compile(r"잘못된 아이디|비밀번호입니다"), "아이디 또는 비밀번호가 올바르지 않습니다."),
)

MAX_DIALOGS = 5  # 팝업 처리 상한. 무한 반복하지 않는다.

# 비밀번호 변경 요구 창 — 로그인 창의 자식 (CONTROLS.md 로그인 절, 10-07 실측)
PASSWORD_CHANGE_AUTO_ID = "Frm_ChangePassword"
LATER_BUTTON_AUTO_ID = "btn_NextTime"
PASSWORD_CHANGE_REASON = "ERPia 가 비밀번호 변경을 요구한다 — [다음에 변경하기] 가 없다 (강제 변경)"


class LoginError(RuntimeError):
    """로그인 실패."""


class DuplicateLogin(LoginError):
    """같은 아이디로 이미 로그인돼 있다.

    기존 인스턴스를 닫으면 다시 시도할 수 있다.
    """

    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text
        super().__init__(
            "이미 같은 아이디로 로그인되어 있습니다.\n  프로그램 메시지: " + raw_text
        )


class LoginRejected(LoginError):
    """프로그램이 로그인을 거부했다 (계정 오류, 중복 로그인 등).

    재시도해도 같은 결과가 나오므로 즉시 중단한다.
    """

    def __init__(self, reason: str, raw_text: str) -> None:
        self.reason = reason
        self.raw_text = raw_text
        super().__init__(f"{reason}\n  프로그램 메시지: {raw_text}")


def _desktop() -> Desktop:
    return Desktop(backend=SETTINGS.backend)


def _windows_strict(pid: int | None = None, **criteria):
    """조건에 맞는 최상위 창. pid를 주면 그 프로세스로만 제한한다 (폴백 없음).

    다른 인스턴스의 창을 우리 것으로 착각하면 안 되는 판정에 쓴다.
    (예: 이미 로그인된 다른 인스턴스의 메인 창)
    """
    desktop = _desktop()
    if pid is not None:
        criteria["process"] = pid
    return desktop.windows(**criteria)


def _windows(pid: int | None = None, **criteria):
    """조건에 맞는 최상위 창. pid로 못 찾으면 전역으로 한 번 더 찾는다.

    프로그램이 창을 다른 프로세스로 띄우는 경우를 위한 폴백이다.
    """
    try:
        found = _windows_strict(pid, **criteria)
        if found:
            return found
    except Exception as exc:
        log.debug("pid=%s 로 창 검색 실패: %s", pid, exc)
    return _desktop().windows(**criteria)


def find_login_window(pid: int | None = None, timeout: float | None = None):
    """로그인 창을 찾는다. auto_id 우선, 실패하면 title로 재시도한다."""
    timeout = SETTINGS.timeouts.window if timeout is None else timeout

    try:
        found = wait_for(
            lambda: _windows(pid, auto_id=LOGIN_FORM_AUTO_ID),
            f'로그인 창(auto_id="{LOGIN_FORM_AUTO_ID}")',
            timeout=timeout,
        )
    except WaitTimeout:
        log.warning("auto_id로 로그인 창을 찾지 못했다. title로 재시도한다.")
        try:
            found = wait_for(
                lambda: _windows(pid, title_re=LOGIN_TITLE_RE),
                f'로그인 창(title_re="{LOGIN_TITLE_RE}")',
                timeout=timeout,
            )
        except WaitTimeout as exc:
            raise LoginError(
                f"{timeout:.0f}초 안에 로그인 창을 찾지 못했다.\n"
                "프로그램이 이미 로그인된 상태이거나, 창 구조가 조사 시점과 다를 수 있다.\n"
                "tools.survey_windows 로 현재 창을 확인할 것."
            ) from exc

    if len(found) > 1:
        log.warning("로그인 창으로 보이는 창이 %d개다. 첫 번째를 쓴다.", len(found))
    return _desktop().window(handle=found[0].handle)


def _resolve(win, auto_id: str, name: str):
    try:
        return win.child_window(auto_id=auto_id).wrapper_object()
    except Exception as exc:
        raise LoginError(
            f'{name} Control을 찾지 못했다 (auto_id="{auto_id}"): {type(exc).__name__}: {exc}\n'
            "화면 구조가 조사 시점과 달라졌을 수 있다. docs/UI_SURVEY.md 를 다시 조사할 것."
        ) from exc


def _mask(value: str) -> str:
    """계정 문자열을 부분만 남긴다. `user01` → `us**01`.

    ★ 배포본은 로그를 exe 옆에 쌓는다. 업체코드·아이디를 그대로 남기면
      로그 파일 하나로 계정이 새어 나간다. 그렇다고 통째로 가리면 "다른 계정을
      넣었나" 를 확인할 수 없어, **앞 2·뒤 2자만** 남긴다 (2026-09-10 감사).
    """
    if not value:
        return "(빈값)"
    if len(value) <= 4:
        return value[0] + "*" * (len(value) - 1)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _fill(control, value: str, name: str, secret: bool = False) -> None:
    """입력창에 값을 넣는다. Value Pattern 우선, 실패하면 키 입력."""
    shown = "*" * len(value) if secret else _mask(value)

    try:
        control.set_focus()
    except Exception as exc:
        log.debug("%s 포커스 실패(계속 진행): %s", name, exc)

    try:
        control.set_edit_text(value)
        how = "Value Pattern"
    except Exception as exc:
        log.warning("%s: Value Pattern 입력 실패(%s). 키 입력으로 전환한다.", name, type(exc).__name__)
        try:
            control.type_keys("^a{BACKSPACE}", set_foreground=False)
            control.type_keys(value, with_spaces=True, set_foreground=False)
            how = "키 입력"
        except Exception as exc2:
            raise LoginError(f"{name} 입력 실패: {type(exc2).__name__}: {exc2}") from exc2

    log.info("%s 입력: %s (%s)", name, shown, how)

    if secret:
        return  # 비밀번호는 되읽지 않는다 (마스킹되어 확인이 무의미하고, 로그에 남기지 않는다)

    try:
        actual = control.get_value()
    except Exception as exc:
        log.debug("%s 값 확인 불가(계속 진행): %s", name, exc)
        return
    if actual != value:
        raise LoginError(f"{name} 입력이 반영되지 않았다. 기대={value!r} 실제={actual!r}")


def _click_login(win) -> str:
    """로그인 버튼을 누른다. Invoke → 클릭 → Enter 순으로 시도한다."""
    button = _resolve(win, LOGIN_BUTTON_AUTO_ID, "로그인 버튼")

    try:
        button.invoke()
        return "Invoke Pattern"
    except Exception as exc:
        log.info("로그인 버튼 Invoke 불가(%s). 컨트롤 클릭으로 전환한다.", type(exc).__name__)

    try:
        button.click_input()  # 컨트롤 위치 기준 클릭. 절대좌표 하드코딩이 아니다.
        return "컨트롤 클릭"
    except Exception as exc:
        log.warning("로그인 버튼 클릭 실패(%s). Enter 키로 전환한다.", type(exc).__name__)

    try:
        password = _resolve(win, FIELD_AUTO_IDS["비밀번호"], "비밀번호")
        password.set_focus()
        password.type_keys("{ENTER}", set_foreground=False)
        return "Enter 키"
    except Exception as exc:
        raise LoginError(
            f"로그인 버튼을 누를 수 없다: {type(exc).__name__}: {exc}\n"
            "Invoke / 클릭 / Enter 모두 실패했다."
        ) from exc


# ------------------------------------------------------------------ 팝업 처리
def _dialog_text(dialog) -> str:
    """팝업 안의 Static 텍스트를 모아 한 줄로 만든다."""
    parts = []
    try:
        for child in dialog.descendants(control_type="Text"):
            try:
                text = child.window_text().strip()
            except Exception:
                continue
            if text:
                parts.append(text)
    except Exception as exc:
        log.debug("팝업 본문 읽기 실패: %s", exc)
    return " / ".join(parts)


def _find_dialog(pid: int | None, login_win, login_handle: int):
    """경고성 팝업을 찾는다. 없으면 None, 있으면 (본문 범위, 확인 버튼).

    이 프로그램의 팝업은 **최상위 창이 아니라 로그인 창의 자식**으로 뜬다.
    `Desktop.windows()`는 top_level_only=True 라서 잡히지 않는다 (2026-09-03 확인).
    그래서 두 군데를 본다.

    1. 최상위 창 중 제목이 팝업 계열인 것
    2. 로그인 창 하위의 "확인" 계열 Button
       (조사된 로그인 화면에는 확인 Button이 없다. 최소화/복원/닫기와 lbl_LoginBtn 뿐이다.
        따라서 확인 Button의 등장 자체가 팝업 신호다.)
    """
    # 1) 최상위 창
    for win in _windows(pid):
        try:
            if win.handle == login_handle:
                continue
            title = win.window_text()
        except Exception:
            continue
        if title and DIALOG_TITLE_RE.search(title):
            dialog = _desktop().window(handle=win.handle)
            try:
                button = dialog.child_window(
                    title_re=CONFIRM_BUTTON_TITLE_RE, control_type="Button"
                ).wrapper_object()
            except Exception:
                button = None
            return (dialog.wrapper_object(), button)

    # 2) 로그인 창 하위에서 팝업 창을 제목으로 직접 찾는다 (제목: "경고", "로그인 실패")
    try:
        for child in login_win.descendants(control_type="Window"):
            name = (child.element_info.name or "").strip()
            if not name or not DIALOG_TITLE_RE.search(name):
                continue
            try:
                button = _confirm_buttons(child)
            except Exception:
                button = []
            return (child, button[0] if button else None)
    except Exception as exc:
        log.debug("로그인 창 하위 Window 검색 실패: %s", exc)

    # 3) 제목으로 못 찾으면 "확인" 버튼의 존재로 판단한다
    try:
        buttons = _confirm_buttons(login_win)
    except Exception as exc:
        log.debug("로그인 창 하위 버튼 검색 실패: %s", exc)
        return None

    for button in buttons:
        try:
            if not button.is_visible():
                continue
        except Exception:
            continue
        return (_dialog_root(button, login_handle), button)

    return None


def _confirm_buttons(node) -> list:
    """`node` 아래 확인 계열 Button.

    ★ `descendants()` 는 `title_re` 를 받지 않는다(TypeError). 예전 코드는 그 예외가
      `except` 에 묻혀 늘 "버튼 없음" → Enter 로 닫았다 (09-22 조사, CONTROLS.md 로그인 팝업).
    """
    return [button for button in node.descendants(control_type="Button")
            if re.match(CONFIRM_BUTTON_TITLE_RE, button.window_text() or "")]


def _dialog_root(button, login_handle: int):
    """확인 버튼에서 위로 올라가며 팝업의 본문 범위를 찾는다.

    로그인 창까지 올라가 버리면 화면 전체 텍스트를 본문으로 읽게 되므로,
    로그인 창 직전까지만 올라간다.
    """
    node = button
    best = button
    for _ in range(6):
        try:
            parent = node.parent()
        except Exception:
            break
        if parent is None:
            break
        try:
            if parent.handle == login_handle:
                break  # 로그인 창까지 왔다. 더 올라가지 않는다.
            name = parent.element_info.name or ""
            ctype = parent.element_info.control_type
        except Exception:
            break
        best = parent
        if ctype == "Window" and DIALOG_TITLE_RE.search(name):
            break  # 팝업 창을 찾았다
        node = parent
    return best


def _dismiss(dialog, button) -> str:
    """팝업의 확인 버튼을 누른다. 무엇으로 닫았는지 돌려준다."""
    if button is None:
        log.warning("확인 버튼을 찾지 못했다. Enter 키로 닫는다.")
        dialog.set_focus()
        dialog.type_keys("{ENTER}", set_foreground=False)
        return "Enter 키"

    label = button.window_text()
    try:
        button.invoke()
        return f"[{label}] Invoke"
    except Exception as exc:
        log.debug("확인 버튼 Invoke 불가(%s). 클릭한다.", type(exc).__name__)
        button.click_input()  # 컨트롤 위치 기준 클릭
        return f"[{label}] 클릭"


def _present(spec):
    """한 번만 찾아 본다 (기다리지 않는다). 없으면 None."""
    try:
        return spec.wrapper_object() if spec.exists(timeout=0) else None
    except Exception as exc:
        log.debug("찾기 실패: %s", exc)
        return None


def _password_change(login_win):
    """비밀번호 변경 요구 창(spec). 없으면 None."""
    spec = login_win.child_window(auto_id=PASSWORD_CHANGE_AUTO_ID, control_type="Window")
    return spec if _present(spec) is not None else None


def _press_later(window) -> str:
    """[다음에 변경하기]. 없거나 꺼져 있으면 누르지 않고 멈춘다 ([변경하기] 는 절대 누르지 않는다)."""
    button = _present(window.child_window(auto_id=LATER_BUTTON_AUTO_ID, control_type="Button"))
    try:
        usable = button is not None and button.is_visible() and button.is_enabled()
    except Exception:
        usable = False
    if not usable:
        body = _present(window)
        raise LoginRejected(PASSWORD_CHANGE_REASON, _dialog_text(body) if body is not None else "")
    return _dismiss(window, button)


# ------------------------------------------------------------------ 완료 대기
def _wait_logged_in(
    company_code: str,
    user_id: str,
    login_win,
    login_handle: int,
    pid: int | None,
    timeout: float,
) -> str:
    """로그인 창이 사라지고 메인 창이 뜰 때까지 기다린다.

    도중에 경고 팝업이 뜨면 내용을 읽고 처리한다.
    2차 인증 안내는 닫고 계속, 그 외에는 실패로 본다.
    """
    main_title_re = f"{re.escape(company_code)}.*{re.escape(user_id)}"
    deadline = time.monotonic() + timeout

    def probe():
        change = _password_change(login_win)
        if change is not None:
            return ("password", change)
        dialog = _find_dialog(pid, login_win, login_handle)
        if dialog is not None:
            return ("dialog", dialog)
        # 메인 창 판정은 이 프로세스로만 한정한다.
        # 다른 인스턴스가 이미 로그인해 둔 메인 창을 성공으로 착각하면 안 된다.
        if not _windows_strict(pid, auto_id=LOGIN_FORM_AUTO_ID):
            found = _windows_strict(pid, title_re=main_title_re)
            if found:
                return ("main", found[0].window_text())
        return None

    # 팝업이 여러 번 뜰 수 있다. 상한을 두고 반복한다.
    deferred = False
    for _ in range(MAX_DIALOGS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            kind, value = wait_for(probe, "로그인 완료 또는 팝업", timeout=remaining)
        except WaitTimeout:
            break

        if kind == "main":
            return value

        if kind == "password":
            if deferred:
                raise LoginError("비밀번호 변경 창이 다시 떴다.")
            deferred = True
            log.warning("ERPia 가 비밀번호 변경을 요구했다 — %s (늘 미룬다, 사용자 확정 10-07)",
                        _press_later(value))
            try:
                wait_for(lambda: _password_change(login_win) is None, "비밀번호 변경 창 닫힘",
                         timeout=max(1.0, deadline - time.monotonic()))
            except WaitTimeout as exc:
                raise LoginError("[다음에 변경하기] 를 눌렀는데 비밀번호 변경 창이 닫히지 않았다.") from exc
            continue

        dialog, button = value
        text = _dialog_text(dialog)
        how = _dismiss(dialog, button)
        log.info("팝업 닫음 %s: %s", how, text or "(본문 없음)")

        if CONTINUABLE_RE.search(text):
            log.info("2차 인증 미설정 안내다. 계속 진행한다.")
            continue

        if DUPLICATE_RE.search(text):
            raise DuplicateLogin(text)

        for pattern, reason in FATAL_PATTERNS:
            if pattern.search(text):
                raise LoginRejected(reason, text)

        raise LoginRejected(
            "로그인 중 알 수 없는 팝업이 떴습니다.", text or "(본문을 읽지 못했다)"
        )

    raise LoginError(_diagnose(pid, login_win, timeout, main_title_re))


def _diagnose(pid: int | None, login_win, timeout: float, main_title_re: str) -> str:
    """실패 원인을 사람이 읽을 수 있게 정리한다.

    팝업을 못 찾은 경우를 다음 번에 바로 알 수 있도록,
    로그인 창 안에 무엇이 있는지도 함께 남긴다.
    """
    lines = [f"{timeout:.0f}초 안에 로그인이 완료되지 않았다."]

    # 실행 중에 화면이 잠기면 마우스 클릭이 전달되지 않아 버튼이 눌리지 않는다.
    # 시작 시점에는 안 잠겨 있었어도 그 사이에 잠길 수 있다 (2026-09-04에 3회 겪었다).
    if screen_locked():
        lines.append(
            "**화면이 잠겨 있다.** 잠금 화면에서는 마우스 클릭이 프로그램에 "
            "전달되지 않는다. 계정 문제가 아니다. 잠금을 해제하고 다시 실행할 것."
        )

    if _windows_strict(pid, auto_id=LOGIN_FORM_AUTO_ID):
        lines.append("로그인 창이 그대로 남아 있다. 계정 정보가 틀렸거나 버튼이 눌리지 않았다.")
    else:
        lines.append(f"로그인 창은 닫혔으나 메인 창(title_re={main_title_re})을 찾지 못했다.")

    titles = []
    for win in _desktop().windows():
        try:
            text = win.window_text()
        except Exception:
            continue
        if text.strip():
            titles.append(text)
    if titles:
        lines.append("최상위 창: " + ", ".join(titles[:10]))

    lines.extend(_describe_login_children(login_win))

    shot = save_screenshot("login_timeout")
    if shot:
        lines.append(f"스크린샷: {shot}")
    lines.append(
        "팝업이 화면에 떠 있는데 감지되지 않았다면 다음을 실행해 트리를 확인할 것:\n"
        r"  .venv\Scripts\python.exe -m tools.dump_controls --pid <PID> --name 로그인"
    )
    return "\n".join(lines)


def _describe_login_children(login_win) -> list[str]:
    """로그인 창 하위에서 팝업의 흔적을 찾아 요약한다."""
    lines = []
    try:
        buttons = login_win.descendants(control_type="Button")
        labels = [b.window_text() for b in buttons if b.window_text().strip()]
        lines.append(f"로그인 창 하위 Button: {labels or '(없음)'}")
    except Exception as exc:
        lines.append(f"로그인 창 하위 Button 조회 실패: {type(exc).__name__}: {exc}")

    try:
        children = login_win.descendants(control_type="Window")
        names = [c.element_info.name for c in children if (c.element_info.name or "").strip()]
        lines.append(f"로그인 창 하위 Window: {names or '(없음)'}")
    except Exception as exc:
        lines.append(f"로그인 창 하위 Window 조회 실패: {type(exc).__name__}: {exc}")
    return lines


def login(
    company_code: str,
    user_id: str,
    password: str,
    pid: int | None = None,
    timeout: float | None = None,
) -> str:
    """로그인 창을 찾아 값을 채우고 로그인한다. 성공 시 메인 창 title을 돌려준다.

    pid를 주면 그 프로세스의 창만 대상으로 한다(여러 인스턴스 구분).

    **같은 아이디로 이미 로그인돼 있으면 기존 인스턴스를 닫고 한 번 더 시도한다**
    (사용자 확정 2026-09-04). 서버가 동시 로그인을 막기 때문이다.
    """
    timeout = SETTINGS.timeouts.login if timeout is None else timeout

    with step(log, "로그인"):
        # 잠금 화면에서는 마우스 클릭이 대상 프로그램에 전달되지 않는다.
        # 로그인 버튼은 Invoke 가 안 되고 click_input() 뿐이라 여기서 막힌다.
        # 그대로 두면 "계정 정보가 틀렸다"로 오해하게 되므로 먼저 확인한다.
        if screen_locked():
            raise LoginError(
                "화면이 잠겨 있어 자동화를 진행할 수 없다.\n"
                "  잠금 화면에서는 마우스 클릭이 프로그램에 전달되지 않는다.\n"
                "  화면 잠금을 해제한 뒤 다시 실행할 것."
            )

        try:
            return _login_once(company_code, user_id, password, pid, timeout)
        except DuplicateLogin as exc:
            log.warning("%s", exc)

        from automation.application import close_others

        exe = SETTINGS.target_exe
        if not exe:
            raise LoginError(
                "동시 로그인 팝업이 떴으나 config/settings.local.json 에 "
                "target_exe 가 없어 기존 인스턴스를 닫을 수 없다."
            )

        closed = close_others(exe, keep_pid=pid,
                              title_re=f"{re.escape(company_code)}.*{re.escape(user_id)}")
        if not closed:
            # 동시 로그인은 **이 PC 안에서만** 막힌다 (사용자 확정 09-22). 못 찾았으면 관리자
            # 권한으로 뜬 인스턴스(UIPI, `close_others` 의 error)이거나 아직 종료 중인 것이다.
            raise LoginError(
                "이미 같은 아이디로 로그인되어 있으나 닫을 다른 인스턴스를 찾지 못했다.\n"
                "  이 PC 에서 같은 아이디로 켜 둔 ERPia 를 닫고 다시 실행할 것."
            )

        log.info("기존 인스턴스를 닫았다. 다시 로그인한다.")
        return _login_once(company_code, user_id, password, pid, timeout)


def _login_once(
    company_code: str,
    user_id: str,
    password: str,
    pid: int | None,
    timeout: float,
) -> str:
    """로그인 1회 시도. 중복 로그인 팝업이 뜨면 `DuplicateLogin`."""
    win = find_login_window(pid=pid)
    login_handle = win.wrapper_object().handle
    log.info("로그인 창 확인: %s (handle=%s)", win.window_text(), login_handle)

    pairs = (
        ("업체코드", company_code, False),
        ("아이디", user_id, False),
        ("비밀번호", password, True),
    )
    for name, value, secret in pairs:
        control = _resolve(win, FIELD_AUTO_IDS[name], name)
        _fill(control, value, name, secret=secret)

    how = _click_login(win)
    log.info("로그인 버튼 실행: %s", how)

    title = _wait_logged_in(company_code, user_id, win, login_handle, pid, timeout)
    log.info("로그인 성공. 메인 창: %s", title)
    return title
