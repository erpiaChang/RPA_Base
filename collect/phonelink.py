r"""인증 문자 읽기 — Windows [휴대폰 연결] 앱 (UIA).

USB 케이블도, 같은 Wi-Fi 도 필요 없다. 폰이 인터넷에 연결돼 있으면 된다
(앱 설정에서 **모바일 데이터를 통한 동기화**를 허용해야 한다).

조사 결과 (2026-09-08)

    List  auto_id="CVSListView"  name='대화'
      ListItem  name='080-000-0000와의 대화. 읽지 않은 메시지. 메시지 미리 보기.
                     [Web발신]\n[사이트A] 인증번호는  123456 입니다. [2분 이내 입력]'

★ **대화를 열지 않아도 된다.** `ListItem` 의 `name` 하나에 발신번호·읽음여부·본문이
  전부 들어 있다. 그래서 인증번호는 **목록에서 먼저** 찾고, 목록에 없을 때만 대화창을
  연다(폴백). 그 밖의 조작은 [메시지] 탭 선택·[다시 시도]/새로 고침뿐이다 (09-18·09-21).

★ 목록에는 **수신 시각이 없다.** adb 경로와 달리 "언제 온 문자인가"로 거를 수 없다.
  그래서 인증번호 요청 **직전에 목록을 찍어 두고(baseline), 그 뒤에 새로 나타난
  항목**만 본다. (`_find_code_from_list`)

★ **[메시지] 탭에 있어야 목록이 존재한다** (2026-09-21). 앱을 새로 띄우면 마지막에
  보던 탭이 열리는데, [앱] 탭이면 `CVSListView` 가 트리에 아예 없어 인증번호를 못
  읽었다. 이제 읽기 전에 `ensure_messages_tab()` 이 탭을 맞춘다(클릭 대신 UIA 선택).

★ 대화창(ConversationPane)은 **폴백**이다. 목록으로 못 고를 때(미리보기에 번호가
  여럿 등)만 대화를 열어 수신시각으로 거른다(`_find_code_from_pane`). 2026-09-18
  실측: 앱이 '연결됨' 이어도 대화창이 비어(스레드 0건) 대화창만으로는 못 읽는 일이
  있었다 — 그때도 **목록에는 번호가 있었다.** 그래서 목록을 먼저 본다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from config.settings import SETTINGS
from utils import ui
from utils.logger import get_logger
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

WINDOW_TITLE_RE = r"휴대폰.*연결|Phone Link"
LIST_AUTO_ID = "CVSListView"
POLL = 2.0                      # 목록 확인 간격(초)

# --- [메시지] 탭 (2026-09-21 실측) ---
# ★ `ms-phone:` 로 앱을 띄우면 **마지막에 보던 탭**이 열린다. [앱] 탭으로 열리면
#   대화 목록(CVSListView)이 트리에 **아예 없어** 인증번호를 못 읽었다
#   (실측: 상태 '연결됨' + 메시지 탭에 읽지 않은 항목 9개인데 목록 컨트롤 없음).
#     TabItem auto_id="MessagingNodeAutomationId" name='메시지(Ctrl+1). 읽지 않은 항목 9개'
# ★ **클릭하지 않고 UIA 선택으로 고른다** — 창이 뒤에 있거나 가려져 있어도 된다
#   (모니터가 1개면 브라우저가 앱을 덮는다).
MESSAGING_TAB_AUTO_ID = "MessagingNodeAutomationId"
MESSAGING_TAB_HOTKEY = "^1"
TAB_WAIT = 10.0                 # 탭을 옮긴 뒤 목록이 나타나기를 기다리는 상한(초)

# 연결 상태 (2026-09-10 확정값)
#   Button auto_id="ConnectivityCardOpenButton"  name='연결됨'
#     Text auto_id="ConnectivityStatusTextBlock" name='오프라인'  ← 실제 상태는 여기
#   Button auto_id="RefreshButton"               name='새로 고침(Ctrl+R 또는 F5), ...'
# ★ 카드 **버튼 이름**은 '연결됨' 으로 고정돼 있고, 실제 상태는 안쪽 Text 다.
#   버튼 이름으로 판정하면 끊겨 있어도 연결됐다고 본다 (그래서 카드 버튼 id 는 쓰지 않는다).
CONNECTIVITY_STATUS_AUTO_ID = "ConnectivityStatusTextBlock"
REFRESH_BUTTON_AUTO_ID = "RefreshButton"
# ★ **정확히 일치**로 본다. 부분 일치면 영어 화면의 'Disconnected' / 'Not connected'
#   가 'connected' 를 품어 연결로 보인다 (09-21 검토).
CONNECTED_STATES = ("연결됨", "connected")
# ★★ **일시 상태 — 끊긴 것도, 붙은 것도 아니다** (2026-09-21 실측).
#     Text auto_id="ConnectivityStatusTextBlock" name='새로 고치는 중...'
#   새로 고침 직후 이 상태가 되고 **1분 넘게 머문다.** 그동안 목록은 읽힌다.
#   - 끊김으로 보고 새로 고침을 **또** 누르면 이 상태를 되살려 최대 5분을 버린다
#     (실측 97초). → `reconnect()` 는 이 상태면 **누르지 않고** 붙기만 기다린다.
#   - 붙은 것으로 보면 안 된다. 오프라인에서 새로 고침을 누르면 이 상태를 거치는데,
#     그걸 연결로 보면 캐시 목록으로 '준비됨' 이 돼 문자를 헛되이 요청한다 (09-21 검토).
#   실측한 문구만 쓴다.
TRANSIENT_STATES = ("새로 고치는 중",)
# 연결이 끊기면 나타나는 [다시 시도] 버튼 (2026-09-10 실측으로 확정).
#   Button auto_id="SendPermissionNotificationButton"  name='다시 시도'
# 함께 나타나는 안내 문구도 읽어 둔다. 사용자가 무엇을 해야 하는지 알려 준다.
#   Text auto_id="TitleTextBlock"       '모바일 장치에서 Windows와 연결이(가) 꺼져 있습니다.'
#   Text auto_id="DescriptionTextBlock" '모바일 장치에서 빠른 설정창 > ... 켭니다.'
RETRY_BUTTON_AUTO_ID = "SendPermissionNotificationButton"
# auto_id 가 바뀌었을 때의 폴백. 이름으로도 찾는다.
RETRY_NAME_RE = re.compile(r"다시 시도|다시 연결|재연결|Retry|Try again")
REASON_AUTO_IDS = ("TitleTextBlock", "DescriptionTextBlock")
# 재연결 후 붙기를 기다리는 상한은 **두 가지다** (2026-09-17).
#   - 시작할 때(인증 요청 전): `SETTINGS.timeouts.phone_connect` (5분).
#     사람이 폰을 집어 [Windows와 연결] 을 켤 시간을 준다. 붙으면 바로 넘어간다.
#   - 인증번호를 **기다리는 도중**: 아래 25초. 문자 유효시간이 2분이라
#     여기서 5분을 기다리면 붙어도 이미 만료된 번호만 남는다.
RECOVER_RECONNECT_WAIT = 25.0
# ★ [다시 시도] 는 **한 번만** 누른다 (사용자 확정 2026-09-10).
#   폰에서 [Windows와 연결] 을 다시 켠 경우에는 한 번으로 붙는다.
#   폰에서 연결을 끊어 둔 경우에는 **몇 번을 눌러도 붙지 않는다.**
#   그러니 반복해서 누르는 것은 시간만 버리는 일이다.
RETRY_PRESSES = 1
RECOVER_ATTEMPTS = 2            # 대기 중 복구를 시도하는 횟수 상한

# --- 앱 자동 실행 (2026-09-15) ---
# ★★ **설치 경로를 쓰지 않는다.** [휴대폰 연결] 은 스토어(UWP) 앱이라
#    설치 위치가 **PC 마다 다르고 업데이트할 때마다 바뀐다**
#    (`C:\Program Files\WindowsApps\Microsoft.YourPhone_1.2.3.0_x64__8wek.../`).
#    그래서 아래 셋은 전부 **Windows 가 그때그때 풀어 주는 식별자**다.
#
#    1. 프로토콜 `ms-phone:` — 앱이 스스로 등록한다. 가장 싸고 PC 와 무관하다
#    2. `Get-StartApps` 로 AppID 를 **찾아서** `shell:AppsFolder` 로 연다
#    3. `Get-AppxPackage` 의 PackageFamilyName 으로 같은 것을 시도
#
#    1번이 거의 항상 된다. 2·3번은 프로토콜 등록이 깨진 PC 를 위한 폴백이다.
LAUNCH_PROTOCOLS = ("ms-phone:", "ms-yourphone:")
LAUNCH_PACKAGE_NAME = "Microsoft.YourPhone"     # 스토어 패키지 이름(경로 아님)
LAUNCH_APPID_RE = re.compile(r"YourPhone|PhoneLink", re.IGNORECASE)
LAUNCH_WAIT = 40.0              # 앱 창이 뜨기를 기다리는 상한(초)
LAUNCH_POLL = 1.0
POWERSHELL_TIMEOUT = 20.0

# [다시 시도] 를 몇 번 눌렀는가. **한 실행 안에서** 센다.
# `check_ready()` 는 사전점검·대기 준비 등 여러 곳에서 불리므로, 호출마다
# 세면 결국 여러 번 누르게 된다. 그래서 모듈에 하나만 둔다.
_RETRY_STATE = {"pressed": 0}
# 앱을 띄워 봤는가. 같은 이유로 **한 실행에서 한 번만** 시도한다.
_LAUNCH_STATE = {"tried": False}

# 인증번호를 요청하기 **전** 대화에 있던 본문들 — 수신 시각이 분 단위라 같은 분 재실행이면 직전 번호가
# 시각 조건을 통과한다. 요청 직전 목록을 찍어 두고 **그 안에 없는 문자만** 쓴다.
_SEEN_BODIES: set[str] = set()
# 요청 직전 목록을 실제로 찍었는가. 못 찍었으면(baseline 이 비면 지난 번호를 새 것으로 고른다) 목록 경로를
# 쓰지 않고 수신 시각이 있는 대화창만 본다.
_LIST_BASELINE = {"taken": False}
# 본문이 baseline 과 같은데 대화가 미읽음으로 돌아온 경우는 **고르지 않는다** — 같은 번호 재발송과 폰 쪽
# 미읽음 동기화를 구별할 수 없고, 만료된 번호를 반복해 넣으면 계정이 잠긴다. 기다리다 실패하는 쪽이 안전하다.

CODE_RE = re.compile(r"\b(\d{4,8})\b")
PANE_AUTO_ID = "ConversationPane"
# ★ ConversationPane 의 control_type 은 **Group** 이다 (2026-09-10 실측).
#   예전에는 `window.descendants()` 로 창 전체를 훑어 이 하나를 찾았다.
#   그 한 번의 호출이 **50초**를 먹었다 (2026-09-10 통합 실행 관찰).
PANE_CONTROL_TYPE = "Group"
BODY_AUTO_ID = "MessageBody"
# 대화창 항목 이름 끝의 수신 시각. 예) "... . 9 8, 2026 2:51 오후."
# 앞뒤에 LTR 마크(U+200E)가 섞여 들어오므로 먼저 제거하고 맞춘다.
TIME_RE = re.compile(
    r"(\d{1,2})\s+(\d{1,2}),\s*(\d{4})\s+(\d{1,2}):(\d{2})\s*(오전|오후|AM|PM)")
LTR_MARKS = ("‎", "‏")
# '080-000-0000와의 대화. 읽지 않은 메시지. 메시지 미리 보기. <본문>'
ITEM_RE = re.compile(r"^(?P<sender>.+?)와의 대화(?P<rest>.*)$", re.S)
UNREAD_MARK = "읽지 않은 메시지"
# 발신번호 비교용. 화면 표기가 '080-000-0000' / '+82 10-...' 처럼 제각각이라
# **숫자만 남겨** 비교한다. 그래도 부분 일치는 하지 않는다 — 전체가 같아야 한다.
DIGITS_RE = re.compile(r"\D+")


class PhoneLinkError(RuntimeError):
    """휴대폰 연결 앱에서 문자를 읽지 못했다."""


@dataclass(frozen=True)
class Conversation:
    sender: str
    unread: bool
    body: str
    raw: str

    def codes(self) -> list[str]:
        return CODE_RE.findall(self.body)


def normalize_number(value: str) -> str:
    """전화번호에서 숫자만 남긴다. 하이픈·공백·괄호 표기 차이를 흡수한다."""
    return DIGITS_RE.sub("", value or "")


def allowed_senders() -> list[str]:
    """설정된 발신번호 목록(숫자만). 비어 있으면 발신자 조건을 걸지 않는다."""
    return [n for n in (normalize_number(v) for v in (SETTINGS.sms_senders or [])) if n]


def _window():
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    from pywinauto import Desktop

    for window in Desktop(backend="uia").windows():
        try:
            title = window.window_text() or ""
        except Exception as exc:
            log.debug("창 제목을 읽지 못했다(무시): %s", exc)
            continue
        if re.search(WINDOW_TITLE_RE, title):
            return window
    raise PhoneLinkError(
        "[휴대폰 연결] 앱 창을 찾지 못했다. 앱을 실행하고 폰이 '연결됨' 인지 확인할 것.")


def app_window_exists() -> bool:
    """[휴대폰 연결] 창이 떠 있나. 예외를 올리지 않는다."""
    try:
        _window()
    except PhoneLinkError:
        return False
    return True


def _powershell(command: str) -> str:
    """PowerShell 한 줄을 돌리고 stdout 을 돌려준다. 실패하면 빈 문자열."""
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=POWERSHELL_TIMEOUT,
        )
    except Exception as exc:
        log.debug("PowerShell 실패(%s): %s", type(exc).__name__, command)
        return ""
    if out.returncode != 0:
        log.debug("PowerShell 반환 %d: %s", out.returncode, command)
        return ""
    return out.stdout.strip()


def _shell_app_ids() -> list[str]:
    """이 PC 에서 [휴대폰 연결] 을 여는 **AppID** 후보.

    `Get-StartApps` 는 시작 메뉴에 등록된 앱의 AppID 를 준다. 이 값을
    `shell:AppsFolder` 뒤에 붙이면 설치 경로를 몰라도 앱이 열린다.
    실패하면 `Get-AppxPackage` 의 PackageFamilyName 으로 같은 것을 만든다.
    """
    ids: list[str] = []

    listing = _powershell(
        "Get-StartApps | ForEach-Object { $_.AppID }")
    for line in listing.splitlines():
        line = line.strip()
        if line and LAUNCH_APPID_RE.search(line):
            ids.append(line)

    if not ids:
        family = _powershell(
            f"(Get-AppxPackage -Name {LAUNCH_PACKAGE_NAME}).PackageFamilyName")
        family = family.splitlines()[0].strip() if family else ""
        if family:
            # 매니페스트의 Application Id 는 이 앱에서 'App' 이다.
            ids.append(f"{family}!App")

    return ids


def launch_app() -> bool:
    """[휴대폰 연결] 앱을 띄운다. 창이 뜨면 True.

    **설치 경로를 쓰지 않는다** (위 `LAUNCH_PROTOCOLS` 주석). 스토어 앱이라
    경로가 PC 마다 다르고 업데이트마다 바뀌기 때문이다.

    앱이 뜨더라도 **연결까지 된다는 보장은 없다.** 폰 쪽에서 [Windows와 연결]
    을 꺼 두었으면 창만 뜨고 오프라인이다. 그 뒤는 `reconnect()` 가 맡는다.
    """
    import os

    for protocol in LAUNCH_PROTOCOLS:
        try:
            os.startfile(protocol)      # noqa: S606 — 경로가 아니라 등록된 프로토콜이다
        except OSError as exc:
            log.debug("프로토콜 %s 실패: %s", protocol, type(exc).__name__)
            continue
        log.info("[휴대폰 연결] 앱을 띄웠다 (프로토콜 %s). 창을 기다린다.", protocol)
        if _wait_app_window():
            return True

    for app_id in _shell_app_ids():
        # explorer 에게 넘긴다. shell:AppsFolder 는 설치 위치를 Windows 가 푼다.
        target = "shell:AppsFolder" + chr(92) + app_id
        try:
            os.startfile(target)        # noqa: S606 — 경로가 아니라 셸 식별자다
        except OSError as exc:
            log.debug("AppsFolder %s 실패: %s", app_id, type(exc).__name__)
            continue
        log.info("[휴대폰 연결] 앱을 띄웠다 (AppID %s). 창을 기다린다.", app_id)
        if _wait_app_window():
            return True

    log.warning("[휴대폰 연결] 앱을 띄우지 못했다. 프로토콜·AppID 를 모두 시도했다.")
    return False


def _wait_app_window() -> bool:
    """앱 창이 뜨기를 기다린다. 떴으면 True."""
    try:
        wait_for(app_window_exists, "[휴대폰 연결] 앱 창",
                 timeout=LAUNCH_WAIT, interval=LAUNCH_POLL)
    except WaitTimeout:
        log.warning("%.0f초 안에 [휴대폰 연결] 창이 뜨지 않았다.", LAUNCH_WAIT)
        return False
    return True


def ensure_app() -> None:
    """창이 없으면 앱을 띄운다. **한 실행에서 한 번만** 시도한다.

    ★ 예전에는 창이 없으면 그 자리에서 `PhoneLinkError` 로 끝났다. 그래서
      **재연결·새로고침 코드에 닿지도 못했다** (2026-09-15 실행에서 확인:
      앱 프로세스는 살아 있는데 창이 트레이로 내려가 있어 즉시 중단).

    띄우지 못하면 여기서 막지 않는다. 뒤이어 `_window()` 가 사람이 읽을
    안내와 함께 예외를 올린다.
    """
    if app_window_exists():
        return
    if _LAUNCH_STATE["tried"]:
        log.info("[휴대폰 연결] 앱 실행을 이미 시도했다. 다시 띄우지 않는다.")
        return
    _LAUNCH_STATE["tried"] = True
    log.warning("[휴대폰 연결] 창이 없다. 앱을 띄워 본다.")
    launch_app()


def reset_retry_state() -> None:
    """[다시 시도] 누른 횟수를 되돌린다. **실행을 시작할 때 부른다.**

    ★ 이 상태는 프로세스 전역이다. GUI 는 같은 프로세스에 머물며 [실행]을
      여러 번 누르므로, 되돌리지 않으면 1회차에 한 번 누른 뒤 2회차부터는
      **버튼을 아예 누르지 않는다.** 폰에서 [Windows와 연결]을 다시 켜고
      재실행하는 가장 흔한 복구 시나리오가 막힌다 (2026-09-10 감사).
    """
    _RETRY_STATE["pressed"] = 0
    # 앱 실행 시도도 같이 되돌린다. 같은 이유다 — GUI 는 프로세스에 머물며
    # [실행] 을 여러 번 누르므로, 되돌리지 않으면 2회차부터 앱을 못 띄운다.
    _LAUNCH_STATE["tried"] = False


def retry_presses_left() -> int:
    """[다시 시도] 를 더 누를 수 있는 횟수."""
    return max(0, RETRY_PRESSES - _RETRY_STATE["pressed"])


def check_ready(reconnect_timeout: float | None = None) -> str:
    """앱이 떠 있고 대화 목록이 잡히는지 확인한다.

    `reconnect_timeout` — 끊겨 있을 때 붙기를 기다리는 상한 (기본 `SETTINGS.timeouts.phone_connect` 5분).
    순서: 앱 창이 없으면 띄운다(`ensure_app`) → **연결 상태를 목록보다 먼저** 본다 (끊기면 앱이 목록 대신
    안내 화면을 띄워 `CVSListView` 가 없다 — 목록부터 찾으면 재연결 기회 없이 죽는다) → 목록.
    """
    ensure_app()
    window = _window()
    title = window.window_text()
    if not is_connected() and not reconnect(timeout=reconnect_timeout):
        # 오프라인이어도 `read_conversations()` 는 지난 동기화 목록을 그대로 준다 — 준비됨으로 보면
        # 올 수 없는 인증 문자를 요청해 기다린다. 요청 **전에** 멈춘다.
        raise PhoneLinkError(
            f"휴대폰이 '{connection_status() or '오프라인'}' 이다. 대화 목록은 지난 "
            "동기화 내용이라 **새 인증 문자를 받을 수 없다.** 폰 화면을 켜고 "
            "[Windows와 연결] 이 켜져 있는지 확인할 것 — 화면이 오래 꺼져 있으면 "
            "연결이 끊긴다.")
    items = read_conversations()
    if not items:
        raise PhoneLinkError(
            "대화 목록이 비어 있다. 폰에서 [Windows에 연결] 앱의 동기화 설정 "
            "(Wi-Fi 또는 **모바일 데이터를 통한 동기화**)을 확인할 것.")
    log.info("휴대폰 연결 앱 준비됨: %r (대화 %d건)", title, len(items))
    return title


def _parse(name: str) -> Conversation | None:
    matched = ITEM_RE.match(name.strip())
    if not matched:
        return None
    sender = matched.group("sender").strip()
    rest = matched.group("rest")
    unread = UNREAD_MARK in rest
    # '메시지 미리 보기' 뒤부터가 본문이다. 표기가 '.' 유무로 갈려 양쪽 다 본다.
    body = rest
    for marker in ("메시지 미리 보기.", "메시지 미리 보기"):
        if marker in rest:
            body = rest.split(marker, 1)[1]
            break
    return Conversation(sender=sender, unread=unread, body=body.strip(), raw=name)


def _conversation_list(window):
    """대화 목록 컨트롤(CVSListView). 없으면 None. **[메시지] 탭에만 있다.**

    Desktop().windows() 는 UIAWrapper 를 주고 child_window 가 없으므로 descendants
    로 찾는다. descendants 는 auto_id 를 조건으로 받지 않아(IUIA.build_condition)
    control_type 으로 좁힌 뒤 automation_id 로 고른다.
    """
    for ctrl in window.descendants(control_type="List"):
        if ctrl.element_info.automation_id == LIST_AUTO_ID:
            return ctrl
    return None


def ensure_messages_tab(window=None) -> None:
    """[메시지] 탭으로 옮긴다. 이미 목록이 보이면 **아무것도 하지 않는다.**

    앱을 새로 띄우면 마지막에 보던 탭([앱]·[통화]·[사진])이 열리는데, 그때는
    대화 목록이 트리에 없어 인증번호를 읽을 수 없다 (2026-09-21 실측).
    클릭이 아니라 UIA 선택 → 단축키 순으로 고른다 (창이 가려져 있어도 된다).
    """
    window = _window() if window is None else window
    if _conversation_list(window) is not None:
        return
    tab = _by_auto_id(window, MESSAGING_TAB_AUTO_ID, control_type="TabItem")
    if tab is None:
        raise PhoneLinkError(
            f"[메시지] 탭({MESSAGING_TAB_AUTO_ID})을 찾지 못했다. 앱이 오프라인 "
            "안내 화면일 수 있다. 폰이 '연결됨' 인지 확인할 것.")
    log.warning("[메시지] 탭이 아니다 (앱이 마지막에 보던 탭으로 열렸다). 탭을 옮긴다.")
    try:
        tab.iface_selection_item.Select()
        log.info("[메시지] 탭을 골랐다 (UIA 선택).")
    except Exception as exc:
        log.debug("탭 UIA 선택 불가(%s). 단축키로 시도한다.", type(exc).__name__)
        try:
            # ★ 앞으로 가져온 뒤 보낸다. `set_foreground=False` 면 키가 **지금 포커스가
            #   있는 창**(인증 중이면 브라우저)으로 간다 (`utils/ui.bring_forward` 주석).
            window.type_keys(MESSAGING_TAB_HOTKEY, set_foreground=True)
            log.info("[메시지] 탭 단축키(Ctrl+1)를 보냈다.")
        except Exception as key_exc:
            raise PhoneLinkError(
                f"[메시지] 탭으로 옮기지 못했다: {key_exc}") from key_exc
    try:
        wait_for(lambda: _conversation_list(_window()) is not None,
                 "대화 목록([메시지] 탭)", timeout=TAB_WAIT, interval=0.5)
    except WaitTimeout as exc:
        raise PhoneLinkError(
            f"[메시지] 탭으로 옮겼지만 {TAB_WAIT:.0f}초 안에 대화 목록"
            f"({LIST_AUTO_ID})이 나타나지 않았다.") from exc


def _require_conversation_list():
    """대화 목록 컨트롤. **[메시지] 탭이 아니면 옮긴 뒤** 다시 찾는다."""
    window = _window()
    listing = _conversation_list(window)
    if listing is not None:
        return listing
    ensure_messages_tab(window)
    # 탭을 옮기면 트리가 바뀐다. 창을 다시 잡는다.
    listing = _conversation_list(_window())
    if listing is None:
        raise PhoneLinkError(
            f"대화 목록 컨트롤({LIST_AUTO_ID})이 없다. [메시지] 탭인지 확인할 것.")
    return listing


def read_conversations() -> list[Conversation]:
    """대화 목록을 읽는다. **클릭하지 않는다.** 파일로 저장하지도 않는다."""
    try:
        items = _require_conversation_list().descendants(control_type="ListItem")
    except PhoneLinkError:
        raise
    except Exception as exc:
        raise PhoneLinkError(f"대화 목록을 읽지 못했다: {exc}") from exc

    found = []
    for item in items:
        try:
            name = item.element_info.name or ""
        except Exception as exc:
            log.debug("항목 이름을 읽지 못했다(무시): %s", exc)
            continue
        parsed = _parse(name)
        if parsed is not None:
            found.append(parsed)
    return found


def _clean(text: str) -> str:
    for mark in LTR_MARKS:
        text = text.replace(mark, "")
    return text


def parse_received(name: str) -> "datetime | None":
    """대화창 항목 이름 끝의 수신 시각을 읽는다. 못 읽으면 None."""
    matched = TIME_RE.search(_clean(name))
    if not matched:
        return None
    month, day, year, hour, minute, meridiem = matched.groups()
    hour = int(hour) % 12
    if meridiem in ("오후", "PM"):
        hour += 12
    try:
        return datetime(int(year), int(month), int(day), hour, int(minute))
    except ValueError as exc:
        log.warning("수신 시각을 해석하지 못했다(%s): %s", name[:40], exc)
        return None


def open_conversation(keyword: str | None) -> bool:
    """인증 문자가 오는 대화를 연다. **연 대화는 읽음 처리된다.**

    목록 항목에는 수신 시각이 없다. 시각은 대화창(ConversationPane)에만 있으므로
    시각으로 판정하려면 대화를 열어야 한다.
    """
    listing = _require_conversation_list()

    senders = allowed_senders()
    for item in listing.descendants(control_type="ListItem"):
        name = item.element_info.name or ""
        parsed = _parse(name)
        if parsed is None:
            continue
        if senders:
            # 번호 **완전일치**. 부분 일치를 허용하면 그 번호를 포함하는 다른
            # 번호의 대화를 열 수 있다.
            if normalize_number(parsed.sender) not in senders:
                continue
        elif keyword and keyword not in name:
            continue
        # ★★ **앞으로 가져온 뒤에만** 누른다. 좌표 클릭은 창이 가려져 있으면 **위에 있는
        #   창**을 누른다 (`utils/ui.bring_forward` 주석). 모니터가 1개면 인증 중인
        #   브라우저가 이 앱을 덮고 있어, 메일 사이트 인증 화면의 엉뚱한 곳을 누를 수 있었다
        #   (09-21 검토). 못 가져오면 누르지 않는다 — 대화창 없이도 목록으로 읽는다.
        if not ui.bring_forward(item, "휴대폰 연결 대화"):
            log.warning("[휴대폰 연결] 창을 앞으로 가져오지 못해 대화를 열지 않는다.")
            return False
        item.click_input()
        log.info("대화를 열었다 (%s)", parsed.sender[:20])
        return True

    log.warning("인증 문자 대화를 목록에서 찾지 못했다 (발신번호=%s, keyword=%r)",
                senders or "(조건 없음)", keyword)
    return False


def _conversation_pane(window):
    """열린 대화창(Group). 없으면 None."""
    for ctrl in window.descendants(control_type=PANE_CONTROL_TYPE):
        if (ctrl.element_info.automation_id or "") == PANE_AUTO_ID:
            return ctrl
    return None


def read_messages() -> list[tuple["datetime | None", str]]:
    """열린 대화의 메시지들. `(수신시각, 본문)`. **클릭하지 않는다.**"""
    window = _window()
    pane = _conversation_pane(window)
    panes = [pane] if pane is not None else []
    if not panes:
        raise PhoneLinkError("대화창(ConversationPane)이 없다. 대화를 먼저 열어야 한다.")

    found = []
    for item in panes[0].descendants(control_type="ListItem"):
        name = item.element_info.name or ""
        received = parse_received(name)
        body = ""
        for child in item.descendants(control_type="Text"):
            if (child.element_info.automation_id or "") == BODY_AUTO_ID:
                body = child.element_info.name or ""
                break
        if body:
            found.append((received, body))
    return found


# --------------------------------------------------------------- 연결 상태
def _by_auto_id(window, auto_id: str, control_type: str = "Button"):
    for ctrl in window.descendants(control_type=control_type):
        if (ctrl.element_info.automation_id or "") == auto_id:
            return ctrl
    return None


def connection_status() -> str:
    """폰 연결 상태 문자열. 못 읽으면 빈 문자열.

    `ConnectivityStatusTextBlock` 을 읽는다. 카드 **버튼 이름**은 끊겨 있어도
    '연결됨' 으로 남아 있어 판정에 쓸 수 없다 (2026-09-10 실측).
    """
    window = _window()
    text = _by_auto_id(window, CONNECTIVITY_STATUS_AUTO_ID, control_type="Text")
    if text is None:
        return ""
    try:
        return (text.element_info.name or "").strip()
    except Exception as exc:
        log.debug("연결 상태를 읽지 못했다: %s", type(exc).__name__)
        return ""


def status_is_transient(status: str) -> bool:
    """`'새로 고치는 중...'` 처럼 **맞추는 중**인가. 끊긴 것도 붙은 것도 아니다."""
    return (status or "").strip().startswith(TRANSIENT_STATES)


def is_connected() -> bool:
    """상태가 **연결됨**인가. **읽지 못하면 판정하지 않고 True** 로 둔다.

    상태를 못 읽었다고 끊겼다고 단정하면, 앱 버전이 바뀌었을 때 멀쩡한 연결에
    재연결을 반복한다. 그래서 "끊겼다고 확인된 경우"에만 False 다.

    ★ 일시 상태(`'새로 고치는 중...'`)는 **False** 다 — 붙었다고 확인된 게 아니다.
      그 상태에서 새로 고침을 또 누르지 않게 하는 것은 `reconnect()` 가 맡는다.
    """
    status = connection_status()
    if not status:
        return True
    text = status.strip()
    return text in CONNECTED_STATES or text.lower() in CONNECTED_STATES


def peek() -> tuple[bool | None, str]:
    """**보기만 한다** (예약 전 점검, 10-01) — 앱을 띄우거나, 단추를 누르거나, 탭을 바꾸거나, 창을 앞으로
    가져오지 않는다. 사람이 PC 를 쓰는 중에 돈다. `(준비됨, 사람 말)` — None 은 알 수 없음. 예외를 올리지 않는다.

    `is_connected()` 처럼 '못 읽음 = 연결됨' 으로 보지 않는다 — 점검에서는 '확인 못 함' 이 '준비됨' 으로 보이면 안 된다.
    """
    try:
        status = connection_status().strip()
    except PhoneLinkError:
        return None, PEEK_CLOSED
    except Exception as exc:                            # noqa: BLE001 — 점검이 예약 시계를 죽이면 안 된다
        log.info("휴대폰 연결 상태를 읽지 못했다: %s", type(exc).__name__)
        return None, PEEK_UNKNOWN
    if not status:
        return None, PEEK_UNKNOWN
    if status in CONNECTED_STATES or status.lower() in CONNECTED_STATES:
        return True, PEEK_READY
    if status_is_transient(status):
        return None, PEEK_SYNCING                       # 끊긴 것도 붙은 것도 아니다 — 다음 점검에서 본다
    try:
        reason = offline_reason()
    except Exception as exc:                            # noqa: BLE001 — 안내 문구는 덤이다
        reason = type(exc).__name__
    log.info("휴대폰 연결 점검 — 상태 %r / %s", status, reason)   # 앱 안내에 기기 이름이 있어 파일 로그에만
    return False, PEEK_OFFLINE


# `peek()` 의 사람 말 — 기기 이름·번호·설정 키를 넣지 않는다
PEEK_READY = "준비됨"
PEEK_CLOSED = "PC 의 [휴대폰 연결] 앱이 꺼져 있어 확인하지 못했습니다 (실행할 때 켭니다)"
PEEK_UNKNOWN = "[휴대폰 연결] 앱의 연결 상태를 읽지 못했습니다"
PEEK_SYNCING = "휴대폰 연결을 맞추는 중입니다"
PEEK_OFFLINE = ("휴대폰이 PC 와 연결돼 있지 않습니다 — 휴대폰 화면을 켜고 "
                "[Windows와 연결] 이 켜져 있는지 확인하세요")


def offline_reason() -> str:
    """앱이 화면에 띄운 '왜 끊겼는가' 안내. 없으면 빈 문자열.

    실측 예: `모바일 장치에서 Windows와 연결이(가) 꺼져 있습니다. /
    모바일 장치에서 빠른 설정창 > Windows와 연결(으)로 이동하여 ... 켭니다.`
    사용자가 무엇을 해야 하는지 그대로 알려 주는 문구라 로그에 남긴다.
    """
    lines = []
    for text in _window().descendants(control_type="Text"):
        info = text.element_info
        if (info.automation_id or "") not in REASON_AUTO_IDS:
            continue
        name = (info.name or "").strip()
        # 창 제목('휴대폰과 연결')도 TitleTextBlock 이다. 그것은 안내가 아니다.
        if not name or name in lines or name == "휴대폰과 연결":
            continue
        lines.append(name)
    return " / ".join(lines)


def _retry_button():
    """[다시 시도] 버튼. 연결이 끊겼을 때만 나타난다. 없으면 None."""
    window = _window()
    found = _by_auto_id(window, RETRY_BUTTON_AUTO_ID)
    if found is not None:
        # ★ 같은 auto_id 를 **다른 버튼이 재사용한다** — [앱] 탭에서는 이 auto_id 가
        #   '앱 표시' 다 (2026-09-21 실측). 그걸 누르면 재연결이 아니라 앱 권한
        #   알림을 폰으로 보낸다. 그래서 이름까지 맞는지 본다.
        name = ""
        try:
            name = (found.element_info.name or "").strip()
        except Exception as exc:
            log.debug("[다시 시도] 후보 이름을 읽지 못했다: %s", type(exc).__name__)
        if not name or RETRY_NAME_RE.search(name):
            return found
        log.info("auto_id 는 같지만 이름이 %r 이다. [다시 시도] 가 아니다. 이름으로 찾는다.",
                 name[:20])
    # auto_id 가 바뀌었을 수 있다. 이름으로도 찾아 본다.
    for button in window.descendants(control_type="Button"):
        try:
            name = (button.element_info.name or "").strip()
        except Exception as exc:
            log.debug("버튼 이름을 읽지 못했다: %s", type(exc).__name__)
            continue
        if name and RETRY_NAME_RE.search(name):
            log.info("[다시 시도] 를 이름으로 찾았다: %r (auto_id 가 바뀌었을 수 있다)",
                     name)
            return button
    return None


def _press(button, what: str) -> None:
    """WinUI 버튼을 누른다. Invoke 를 먼저 쓴다.

    연결 카드가 `click_input()` 으로는 아무 반응이 없었다 (2026-09-10 실측).
    """
    try:
        button.iface_invoke.Invoke()
        log.info("%s 를 눌렀다 (Invoke).", what)
        return
    except Exception as exc:
        log.debug("%s Invoke 불가(%s). 클릭으로 전환한다.", what, type(exc).__name__)
    # 좌표 클릭은 앞으로 가져온 뒤에만 한다 (`open_conversation` 과 같은 이유).
    if not ui.bring_forward(button, what):
        raise PhoneLinkError(f"{what} 를 누르지 못했다 — 창을 앞으로 가져오지 못했다.")
    button.click_input()
    log.info("%s 를 눌렀다 (클릭).", what)


def reconnect(timeout: float | None = None) -> bool:
    """끊긴 연결을 다시 붙인다. 붙었으면 True.

    `timeout` — 누른 뒤 붙기를 기다리는 상한 (기본 `SETTINGS.timeouts.phone_connect` 5분). 붙는 즉시 돌아온다.
    **[다시 시도] 는 한 실행에 한 번만** (사용자 확정 09-10, `_RETRY_STATE`) — 폰에서 끊어 둔 상태면
    몇 번 눌러도 안 붙는다.

    식별자 (09-10 확정)

    | 요소 | 값 |
    | --- | --- |
    | [다시 시도] | `Button auto_id="SendPermissionNotificationButton"` |
    | 실제 상태 | `Text auto_id="ConnectivityStatusTextBlock"` (끊기면 `'오프라인'`) |
    | 새로 고침 | `Button auto_id="RefreshButton"` |

    [다시 시도] 가 없으면 새로 고침으로 대신한다. 그것도 **한 번만** 누른다.

    일시 상태(`'새로 고치는 중...'`, `status_is_transient`)면 **누르지 않고** 붙기만 기다린다 — 누르면 그 상태를
    처음부터 다시 시작한다.
    """
    timeout = SETTINGS.timeouts.phone_connect if timeout is None else timeout
    status = connection_status()
    if status_is_transient(status):
        log.info("연결 상태가 %r 이다. 맞추는 중이라 누르지 않고 최대 %.0f초 기다린다.",
                 status, timeout)
        try:
            wait_for(is_connected, "휴대폰 연결 맞추기", timeout=timeout, interval=POLL)
        except WaitTimeout:
            log.error("%.0f초 안에 연결되지 않았다 (상태 %r).", timeout, connection_status())
            return False
        log.info("연결됐다 (상태 %r).", connection_status())
        return True

    log.warning("휴대폰 연결이 끊겼다 (상태 %r). 다시 연결을 시도한다.", status)
    reason = offline_reason()
    if reason:
        log.warning("앱 안내: %s", reason)

    if not retry_presses_left():
        # 이미 눌렀다. 폰에서 끊어 둔 상태면 더 눌러도 붙지 않는다.
        log.warning("[다시 시도] 를 이미 %d번 눌렀다. 다시 누르지 않는다.",
                    _RETRY_STATE["pressed"])
        return is_connected()

    button = _retry_button()
    if button is not None:
        _RETRY_STATE["pressed"] += 1
        _press(button, "[다시 시도]")
    else:
        log.info("[다시 시도] 버튼이 없다(%s). 새로 고침으로 대신한다.",
                 RETRY_BUTTON_AUTO_ID)
        _RETRY_STATE["pressed"] += 1
        if not _click_refresh():
            return False

    log.warning("휴대폰이 붙기를 최대 %.0f초 기다린다. 폰에서 [Windows와 연결] 이 "
                "켜져 있는지 확인해 주세요. 붙으면 바로 다음으로 넘어간다.", timeout)
    try:
        wait_for(is_connected, "휴대폰 재연결", timeout=timeout, interval=POLL)
    except WaitTimeout:
        # 여기서 **다시 누르지 않는다.** 폰에서 끊어 둔 상태면 눌러도 붙지 않는다.
        log.error("%.0f초 안에 연결되지 않았다 (상태 %r). 더 누르지 않는다.",
                  timeout, connection_status())
        again = offline_reason()
        log.error("폰에서 [Windows와 연결] 을 켤 것. %s", again or "")
        return False

    log.info("다시 연결됐다 (상태 %r).", connection_status())
    return True


def _click_refresh() -> bool:
    """새로 고침을 **한 번** 누른다. 버튼이 없으면 단축키(Ctrl+R)로 대신한다."""
    window = _window()
    button = _by_auto_id(window, REFRESH_BUTTON_AUTO_ID)
    if button is not None:
        _press(button, "새로 고침")
        return True

    log.warning("새로 고침 버튼(%s)을 찾지 못했다. 단축키로 시도한다.",
                REFRESH_BUTTON_AUTO_ID)
    try:
        window.set_focus()
        window.type_keys("^r", set_foreground=False)
    except Exception as exc:
        log.warning("Ctrl+R 도 보내지 못했다: %s", type(exc).__name__)
        return False
    log.info("Ctrl+R 을 보냈다.")
    return True


def prepare(keyword: str | None = None) -> datetime:
    """인증번호를 **요청하기 직전**에 부른다. 기준 시각을 돌려준다.

    대화를 미리 열어 둔다. 열어야 메시지별 수신 시각을 읽을 수 있다.
    """
    check_ready()
    # 끊긴 채로 인증번호를 요청하면 문자가 도착해도 읽을 수 없다. **요청 전에** 붙여 두고,
    # 못 붙으면 요청하지 않는다 — 실계정 문자 한 통과 2분을 버린다 (09-21 검토).
    if not is_connected() and not reconnect():
        raise PhoneLinkError(
            f"휴대폰이 '{connection_status() or '오프라인'}' 이다. 인증번호를 요청하지 "
            "않는다. 폰 화면을 켜고 [Windows와 연결] 을 확인할 것.")
    open_conversation(keyword)
    _snapshot_bodies()
    return datetime.now()


def _snapshot_bodies() -> None:
    """요청 직전의 본문을 찍어 둔다(목록 + 대화창 양쪽). 실패해도 대기를 막지 않는다.

    목록에는 발신번호별로 최신 미리보기가 한 줄씩 있다. 대상 발신번호 대화의 현재
    본문을 baseline 으로 잡아 두면, 새 문자가 오기 전까지는 그 본문이 그대로라
    **지난 인증번호를 다시 고르지 않는다**(시각 대신 diff). 대화창은 폴백용으로 같이 찍는다.
    """
    _SEEN_BODIES.clear()
    _LIST_BASELINE["taken"] = False
    senders = allowed_senders()
    try:
        for conv in read_conversations():
            if senders and normalize_number(conv.sender) not in senders:
                continue
            if conv.body:
                _SEEN_BODIES.add(conv.body)
        _LIST_BASELINE["taken"] = True
    except PhoneLinkError as exc:
        # ★ 기준 없이 목록을 보면 지난 번호를 새 것으로 고른다. 이번 대기는 목록을
        #   쓰지 않고 수신 시각이 있는 대화창만 본다 (`_find_code_from_list`).
        log.warning("요청 전 목록을 찍어 두지 못했다(이번엔 대화창만 본다): %s", exc)
    try:
        for _, body in read_messages():
            if body:
                _SEEN_BODIES.add(body)
    except PhoneLinkError:
        # 대화창이 없거나 비어 있어도 목록 스냅샷만으로 충분하다.
        pass
    log.info("요청 전 본문 %d건을 기억한다(목록+대화창). 이 중에서는 인증번호를 고르지 않는다.",
             len(_SEEN_BODIES))


def _recover(keyword: str | None, state: dict) -> None:
    """대화창(`ConversationPane`)이 사라졌을 때의 복구 — `RECOVER_ATTEMPTS` 번까지. 대화창이 사라지는 흔한
    원인은 폰 연결 끊김(앱 `오프라인`)이라, 연결부터 되살리고(짧게) 대화창을 다시 연다."""
    if state["tries"] >= RECOVER_ATTEMPTS:
        return
    state["tries"] += 1
    log.info("복구 시도 %d/%d — 연결 상태와 대화창을 확인한다.",
             state["tries"], RECOVER_ATTEMPTS)
    try:
        if not is_connected():
            # 누른 횟수는 `reconnect()` 안에서 모듈 상태로 센다.
            # 이미 한 번 눌렀으면 거기서 바로 돌아온다.
            # ★ 짧게 기다린다. 인증번호 유효시간(2분) 안이라 5분은 의미가 없다.
            reconnect(timeout=RECOVER_RECONNECT_WAIT)
        # 연결이 멀쩡해도 대화창이 닫혀 있을 수 있다. 다시 연다.
        open_conversation(keyword)
    except PhoneLinkError as exc:
        log.warning("복구하지 못했다(계속 기다린다): %s", exc)


def _find_code_from_list(keyword: str | None) -> tuple[str, object] | None:
    """대화 **목록**에서 새 인증번호를 찾는다. 대화창을 열지 않는다.

    목록은 발신번호별로 최신 미리보기가 한 줄이라, 대상 발신번호 대화의 본문이
    baseline(`_SEEN_BODIES`)에 없으면 그게 새 문자다. 시각이 없어도 diff 로 지난
    번호를 거른다. 스레드창이 비어도(오늘 실측) 목록은 채워지므로 여기서 잡는다.
    """
    if not _LIST_BASELINE["taken"]:
        return None          # 기준이 없다. 지난 번호를 고를 수 있어 쓰지 않는다
    senders = allowed_senders()
    try:
        convs = read_conversations()
    except PhoneLinkError as exc:
        log.debug("목록 조회 실패(대화창으로 폴백): %s", exc)
        return None
    for conv in convs:   # 최신순. 발신번호가 맞는 첫 항목이 그 발신처의 최신이다
        if senders and normalize_number(conv.sender) not in senders:
            continue
        body = conv.body
        # 본문이 요청 전과 같으면 **미읽음으로 돌아왔어도** 고르지 않는다 (`_LIST_BASELINE` 아래 주석).
        if not body or body in _SEEN_BODIES:
            continue
        if keyword and keyword not in body:
            continue
        codes = CODE_RE.findall(body)
        if len(codes) == 1:
            log.info("인증번호를 찾았다 (목록, %s, %s**)", conv.sender[:12], codes[0][:2])
            return ("ok", codes[0])
        if len(codes) > 1:
            # 미리보기에 번호가 여럿이면 목록만으로는 못 가린다 → 대화창 폴백에 맡긴다.
            log.debug("목록 본문에 번호 여럿(%s). 대화창으로 확인한다.", codes)
            return None
    return None


def _find_code(since: datetime, keyword: str | None,
               state: dict | None = None) -> tuple[str, object] | None:
    """새 인증번호를 찾는다. **목록을 먼저** 보고, 못 고르면 대화창으로 폴백한다.

    목록이 안정적이라 평소엔 여기서 끝난다. 목록이 비었거나 번호가 모호할 때만
    예전 방식(대화창 + 수신시각)으로 넘어간다.
    """
    hit = _find_code_from_list(keyword)
    if hit is not None:
        return hit
    return _find_code_from_pane(since, keyword, state)


def _find_code_from_pane(since: datetime, keyword: str | None,
                         state: dict | None = None) -> tuple[str, object] | None:
    """`since` **이후에 도착한** 메시지에서만 인증번호를 찾는다 (대화창 폴백)."""
    try:
        messages = read_messages()
    except PhoneLinkError as exc:
        log.warning("대화 조회 실패(재시도): %s", exc)
        if state is not None:
            _recover(keyword, state)
        return None

    # 시각을 못 읽은 메시지는 **쓰지 않는다.** 지난 인증번호를 넣으면 계정이 잠긴다.
    fresh = []
    for order, (received, body) in enumerate(messages):
        if received is None:
            continue
        if received < since.replace(second=0, microsecond=0):
            continue
        # ★ 시각이 분 단위라 같은 분의 **직전 인증번호**도 여기까지 통과한다.
        #   요청 전에 이미 있던 본문이면 새 문자가 아니다.
        if body in _SEEN_BODIES:
            log.debug("요청 전에 이미 있던 문자다. 건너뛴다.")
            continue
        if keyword and keyword not in body:
            continue
        fresh.append((received, order, body))

    # 가장 **나중에** 온 것부터 본다. 같은 분에 여러 건이 오면 마지막이 우리 것이다.
    # 시각이 같으면 **대화창에 나중에 놓인 것**이 나중에 온 것이다 (분 단위라
    # 같은 분의 두 건은 시각만으로 못 가린다).
    fresh.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for received, _order, body in fresh:
        codes = CODE_RE.findall(body)
        if len(codes) == 1:
            log.info("인증번호를 찾았다 (수신 %s, %s**)",
                     received.strftime("%H:%M"), codes[0][:2])
            return ("ok", codes[0])
        if len(codes) > 1:
            return ("ambiguous", codes)
    return None


def wait_for_code(
    since: datetime,
    timeout: float | None = None,
    keyword: str | None = None,
) -> str:
    """새 인증번호가 올 때까지 기다린다 (`_find_code` — **대화 목록 먼저**, 못 고르면 대화창 폴백).

    - 목록: 대상 발신번호 대화의 미리보기가 요청 직전 baseline(`_SEEN_BODIES`)에 없으면 새 문자다.
    - 대화창 폴백: `since` 이후 수신 시각인 메시지만. 시각은 **분 단위**라 기준도 초를 버리고 비교하고,
      같은 분의 직전 번호는 `_SEEN_BODIES` 로 거른다. 시각을 못 읽은 메시지는 쓰지 않는다. 여럿이면 가장 나중 것.
    - 숫자 후보가 여러 개면 추측하지 않고 중단한다 (지난 번호를 넣으면 계정이 잠긴다).
    """
    timeout = SETTINGS.timeouts.sms_code if timeout is None else timeout
    log.info("인증 문자 대기 (휴대폰 연결, 최대 %.0f초, 기준 %s, 연결 %r)",
             timeout, since.strftime("%H:%M:%S"), connection_status() or "(못 읽음)")
    state = {"tries": 0}
    try:
        kind, value = wait_for(
            lambda: _find_code(since, keyword, state),
            "인증 문자 도착",
            timeout=timeout,
            interval=POLL,
        )
    except WaitTimeout as exc:
        raise PhoneLinkError(
            f"{timeout:.0f}초 안에 인증 문자가 오지 않았다. [휴대폰 연결] 앱이 "
            "'연결됨' 인지, 폰의 동기화 설정이 켜져 있는지 확인할 것.") from exc

    if kind == "ambiguous":
        raise PhoneLinkError(
            f"문자에서 숫자 후보가 여러 개 나왔다: {value}. 추측하지 않고 중단한다.")
    return str(value)
