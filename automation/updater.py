r"""대상 프로그램이 **업데이트가 필요할 때** 로그인 창 대신 뜨는 것들을 처리한다.

실행했는데 로그인 창이 나오지 않고 업데이트가 먼저 도는 경우가 있다.
그때 화면에 뜨는 것은 둘 중 하나다.

| 무엇 | 누가 띄우나 | 우리가 누를 수 있나 |
| --- | --- | --- |
| 프로그램 자신의 업데이트 안내 창 | 대상 프로그램 | **누를 수 있다** |
| `사용자 계정 컨트롤`(UAC) 동의 창 | Windows (`consent.exe`) | **누를 수 없다. 설계상 그렇다** |

## UAC 동의 창은 누를 수 없다 (09-17 실측, 보안 데스크톱이 꺼진 가장 유리한 조건에서)

| 무엇 | 결과 |
| --- | --- |
| `consent.exe` 가 떴다는 것 | 알 수 있다 — **이름으로** 찾는다 (`consent_pids`. SYSTEM 권한이라 경로로 열면 놓친다) |
| 창 핸들·제목·위치 | 읽힌다 (`사용자 계정 컨트롤`) |
| 창 안의 자식 HWND / UIA 자식 | 0개 (XAML) — 단추를 볼 수 없다 |
| 보낸 입력 | 닿지 않는다 (ESC 무시) |

좌표로 찍는 우회는 하지 않는다 (좌표 추측 금지 + 입력이 막혀 있고, 누구의 승격 요청인지 모른다).
경위는 `docs/archive/HANDOFF_20260917.md` 32절.

이 파일이 하는 일:
1. 프로그램 자신의 업데이트 안내 창이면 누른다
2. UAC 동의 창이면 사람에게 알리고 [예] 를 누를 때까지 기다린다
3. 업데이터가 도는 동안 기다렸다가 끝나면 흐름을 잇는다

## 아예 안 뜨게 하려면

RPA 를 **관리자 권한으로 실행**하면 대상 프로그램이 그 권한을 물려받아
업데이터가 따로 승격을 요청하지 않는다. 그러면 이 창 자체가 뜨지 않는다.
(권한 문제 전반은 `docs/UI_SURVEY.md` 의 "권한(Integrity Level) 문제" 참고.)
"""
from __future__ import annotations

import os
import re

from pywinauto.application import process_get_modules

from config.settings import SETTINGS
from utils import dialogs, ui, winprobe
from utils.logger import get_logger
from utils.process import pids_by_name, screen_locked
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

# --- 상태 ---------------------------------------------------------------
NONE = "none"            # 업데이트와 무관하다
DIALOG = "dialog"        # 프로그램 자신의 안내 창. 누를 수 있다
CONSENT = "consent"      # UAC 동의 창. 사람이 눌러야 한다
UPDATING = "updating"    # 업데이터가 도는 중

STATE_LABELS = {
    NONE: "업데이트 아님",
    DIALOG: "업데이트 안내 창",
    CONSENT: "UAC 동의 창",
    UPDATING: "업데이트 진행 중",
}

# Windows 의 UAC 동의 UI. 이름이 고정이라 상수로 둔다 (대상 프로그램 것이 아니다).
CONSENT_EXE = "consent.exe"

# [예] 를 누른 뒤 창이 사라지기를 기다리는 상한. 짧게 둔다 — 눌리지 않는 것이
# 정상이라, 여기서 오래 기다리면 사람에게 알리는 것만 늦어진다.
CONSENT_GONE_TIMEOUT = 2.0

# 업데이터로 볼 실행 파일 이름. **대상 프로그램과 같은 폴더에 있는 것만** 본다.
# 특정 제품 파일명을 박아 두지 않으려고 이름 조각으로 찾는다.
UPDATER_NAME_RE = re.compile(r"update", re.IGNORECASE)

# 업데이트 안내 창으로 볼 제목. 확인된 값이 아니라 **후보**다 —
# 못 찾으면 추측해서 누르지 않고 화면의 창 목록을 로그에 남긴다.
UPDATE_TITLE_RE = "업데이트|업그레이드|Update|Upgrade"


def _running() -> list[tuple[int, str]]:
    """(pid, 실행 파일 경로) 목록. 경로를 읽지 못하는 것은 건너뛴다."""
    out = []
    for entry in process_get_modules():
        pid, module = entry[0], entry[1]
        if module:
            out.append((pid, module))
    return out


def consent_pids() -> list[int]:
    """UAC 동의 창(`consent.exe`)이 떠 있으면 그 PID 들.

    ★ **이름으로 찾는다.** 경로로 찾으면 못 찾는다 — `consent.exe` 는 SYSTEM
      권한이라 우리가 프로세스를 열 수 없고, 모듈 경로를 읽는 방식은 그런
      프로세스를 통째로 건너뛴다. 실측으로 확인했다 (2026-09-17, 머리말).

      이름만 보는 것이 위험한 경우가 보통 있지만(같은 이름의 다른 프로그램),
      이 이름은 Windows 자신의 것이라 해당하지 않는다.
    """
    return pids_by_name(CONSENT_EXE)


def updater_pids(exe: str) -> list[int]:
    """대상 프로그램 **옆 폴더**에서 도는 업데이터의 PID 들.

    폴더를 따지는 이유: 이름에 `update` 가 든 프로세스는 Windows 에도 많다.
    같은 폴더 것만 봐야 남의 업데이터를 우리 것으로 착각하지 않는다.
    """
    if not exe:
        return []
    home = os.path.normcase(os.path.dirname(os.path.abspath(exe)))
    target = os.path.normcase(os.path.abspath(exe))
    out = []
    for pid, module in _running():
        path = os.path.normcase(os.path.abspath(module))
        if path == target:
            continue                      # 대상 프로그램 자신이다
        if os.path.dirname(path) == home and UPDATER_NAME_RE.search(
                os.path.basename(path)):
            out.append(pid)
    return sorted(out)


def find_update_dialog(pid: int | None):
    """대상 프로그램 **자신이** 띄운 업데이트 안내 창. 없으면 None."""
    if pid is None:
        return None
    return dialogs.find_in_process(pid, UPDATE_TITLE_RE)


def state(exe: str, pid: int | None = None) -> tuple[str, str]:
    """지금 무엇에 막혀 있는지. `(상태, 사람에게 보여 줄 말)`.

    보는 차례가 중요하다. **누를 수 있는 것을 먼저** 본다.
    """
    dialog = find_update_dialog(pid)
    if dialog is not None:
        return DIALOG, f"업데이트 안내 창: {dialog.window_text()!r}"

    consent = consent_pids()
    if consent:
        where = consent_where()
        return CONSENT, (
            "Windows 의 [사용자 계정 컨트롤] 창이 떠 있다 "
            f"(consent.exe pid={consent}"
            + (f", {where}" if where else "") + ")")

    running = updater_pids(exe)
    if running:
        return UPDATING, f"업데이터가 도는 중이다 (pid={running})"

    # 창을 볼 수 없는데 입력 데스크톱도 열리지 않는다 = 보안 데스크톱이 올라와 있다.
    # 잠금 화면일 수도 있어서 **단정하지 않고** 그대로 말한다.
    if screen_locked():
        return CONSENT, ("입력 데스크톱을 열 수 없다. UAC 동의 창이나 잠금 화면이 "
                         "떠 있을 수 있다")
    return NONE, ""


def consent_windows() -> list:
    """UAC 동의 창의 최상위 창들.

    ★ **제목으로 찾지 않는다.** 이 창의 제목은 빈 문자열일 때가 있다
      (실측: 뜨는 순간엔 `''`, 잠시 뒤 `사용자 계정 컨트롤`). 제목 기준
      탐색은 그 사이에 창을 놓친다.
    """
    out = []
    for pid in consent_pids():
        out += winprobe.top_windows(pid)
    return out


def consent_where() -> str:
    """사람에게 "어디에 떴는지" 를 말해 주기 위한 한 줄."""
    spots = []
    for window in consent_windows():
        rect = winprobe.rect_of(window.handle)
        if rect:
            spots.append(f"화면 ({rect[0]}, {rect[1]}) 위치")
    return " / ".join(spots)


def _try_click_consent() -> bool:
    """UAC 동의 창의 [예] 를 **누를 수 있으면** 눌러 본다.

    ★ 실측(2026-09-17)에서는 누를 수 없었다. 창은 잡히는데 **안이 비어
      있다** — 자식 창 0개, UIA 자식 0개. 게다가 보낸 입력이 창에 닿지도
      않는다(ESC 로 확인). 그래서 대개 False 를 돌려준다.

      그때 **창 위치에서 단추 자리를 추측해 찍지 않는다.** 눌리지도 않고,
      남의 승격 요청에 [예] 를 찍을 수 있는 코드가 된다.

      다른 Windows 구성에서 단추가 보일 여지만 남겨 두고, 판정은
      **"눌렀다" 가 아니라 "창이 사라졌다"** 로 한다.
    """
    windows = consent_windows()
    for window in windows:
        log.info("UAC 동의 창: handle=%s title=%r class=%r 위치=%s",
                 window.handle, window.title,
                 getattr(window, "class_name", ""),
                 winprobe.rect_of(window.handle))
    if screen_locked():
        log.info("화면이 잠겼거나 보안 데스크톱이 올라와 있다. "
                 "**우리는 이 창을 누를 수 없다.**")
        return False

    for window in windows:
        button = _yes_button(window.handle)
        if button is None:
            continue
        try:
            how = ui.click(button, "UAC [예]")
        except Exception as exc:
            log.info("UAC [예] 를 누르지 못했다(정상이다): %s: %s",
                     type(exc).__name__, exc)
            continue
        # 누른 것과 눌린 것은 다르다. 창이 사라졌는지로 판정한다.
        try:
            wait_for(lambda: not consent_pids(), "UAC 동의 창 사라짐",
                     timeout=CONSENT_GONE_TIMEOUT)
        except WaitTimeout:
            log.info("UAC [예] 를 눌렀지만(%s) 창이 그대로다. **눌리지 않았다.**",
                     how)
            return False
        log.warning("UAC 동의 창을 눌렀다 (%s). 단추가 보이는 구성이다.", how)
        return True

    log.info("UAC 창의 단추를 하나도 볼 수 없다(권한 경계). 사람이 눌러야 한다.")
    return False


def _yes_button(handle: int):
    """핸들로 붙어 [예] 단추를 찾는다. 못 보면 None. **예외를 올리지 않는다.**"""
    import re

    from pywinauto import Desktop

    try:
        wrapper = Desktop(backend=SETTINGS.backend).window(handle=handle)
        buttons = wrapper.descendants(control_type="Button")
    except Exception as exc:
        log.debug("UAC 창 안을 볼 수 없다: %s: %s", type(exc).__name__, exc)
        return None
    for item in buttons:
        try:
            if re.search(dialogs.CONFIRM_YES_RE, item.window_text() or ""):
                return item
        except Exception as exc:
            log.debug("단추 이름을 읽지 못했다: %s", type(exc).__name__)
    return None


def settle(exe: str, pid: int | None = None, status=None,
           timeout: float | None = None, alert=None) -> str:
    """업데이트가 걸려 있으면 **끝날 때까지 처리하고 기다린다.**

    돌려주는 값은 무엇을 했는지 한 줄이다. 막힌 것이 없으면 빈 문자열.
    `status` 는 사람에게 보여 줄 통로다(GUI 상태줄 / 오버레이).
    `alert(종류, 글)` 은 사람 손이 **지금** 필요할 때 서버로 알리는 통로다 — 업체 담당자 메일 (10-01, `Hooks.alert`).

    ★ 이 함수는 **아무것도 저장하지 않는다.** 창을 누르거나 기다릴 뿐이다.
    """
    timeout = SETTINGS.timeouts.update if timeout is None else timeout
    kind, detail = state(exe, pid)
    if kind == NONE:
        return ""

    log.warning("로그인 창 대신 업데이트 관련 상태다 — %s. %s",
                STATE_LABELS[kind], detail)
    done = []

    if kind == DIALOG:
        dialog = find_update_dialog(pid)
        if dialog is not None:
            _say(status, f"업데이트 안내 창을 확인한다: {dialog.window_text()}")
            try:
                how = dialogs.click_button(dialog, dialogs.CONFIRM_YES_RE,
                                           "업데이트 [예]")
                done.append(f"안내 창 확인({how})")
            except Exception as exc:
                # 누를 단추를 못 찾았다. **추측해서 다른 것을 누르지 않는다.**
                log.warning("업데이트 안내 창에서 누를 단추를 찾지 못했다: %s. "
                            "창의 단추: %s", exc, dialogs.buttons_of(dialog))
                _say(status, "업데이트 안내 창을 사람이 확인해 주세요.")
        kind, detail = state(exe, pid)

    if kind == CONSENT:
        text = ("Windows [사용자 계정 컨트롤] 창에서 [예] 를 눌러 주세요. "
                "(RPA 는 이 창을 대신 누를 수 없습니다)")
        _say(status, text)
        # 동의 창이 실제로 있을 때만 — 잠금 화면만으로는(같은 CONSENT 판정) 부르지 않는다 (10-02)
        if alert is not None and consent_pids():
            try:
                alert("uac", text)          # 무인 실행이면 PC 앞에 사람이 없다 — 메일로 부른다
            except Exception as exc:        # noqa: BLE001 — 알림 때문에 업데이트 대기가 깨지면 안 된다
                log.warning("UAC 대기 알림을 보내지 못했다: %s", exc)
        if _try_click_consent():
            done.append("UAC 동의 창을 눌렀다")
        else:
            done.append("UAC 동의는 사람이 눌렀다")
        _wait_gone(lambda: not consent_pids(), "UAC 동의 창이 닫히기", timeout)

    if updater_pids(exe):
        _say(status, "업데이트가 끝나기를 기다립니다...")
        _wait_gone(lambda: not updater_pids(exe), "업데이터 종료", timeout)
        done.append("업데이트 완료")

    summary = " / ".join(done) if done else STATE_LABELS.get(kind, kind)
    log.info("업데이트 처리 끝 — %s", summary)
    return summary


def _wait_gone(condition, what: str, timeout: float) -> None:
    """조건이 참이 될 때까지 기다린다. 시간이 다 돼도 **막지는 않는다.**

    업데이트는 사람 손을 타므로 얼마나 걸릴지 모른다. 여기서 예외를 올리면
    사람이 아직 누르는 중인데 흐름이 실패로 끝난다. 다음 단계(로그인 창
    찾기)가 어차피 다시 기다리므로, 여기서는 알리고 넘긴다.
    """
    try:
        wait_for(condition, what, timeout=timeout)
        log.info("%s 확인", what)
    except WaitTimeout:
        log.warning("%s 를 %.0f초 안에 확인하지 못했다. 그대로 다음으로 넘어간다.",
                    what, timeout)


def _say(status, text: str) -> None:
    log.warning(text)
    if status is not None:
        try:
            status(text)
        except Exception as exc:
            log.debug("상태 통지 실패: %s", type(exc).__name__)
