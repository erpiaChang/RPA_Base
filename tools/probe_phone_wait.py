r"""[조사 도구 - 읽기 전용] 휴대폰 연결이 끊겼을 때 **얼마나 기다리는지** 확인한다.

[휴대폰 연결] 앱을 건드리지 않는다. 앱 쪽 함수를 가짜로 바꿔 끼운다.

    .venv\Scripts\python.exe -m tools.probe_phone_wait

## 왜 이 도구가 있나 (2026-09-17)

사용자 요청: 연결이 안 되면 [다시 시도]/새로 고침 뒤 **최대 5분** 기다리고,
**붙으면 5분 전이라도 바로 넘어간다.** 예전에는 25초만 기다렸다.

5분을 실제로 기다려 확인할 수는 없으니, 기다리는 **상한 값**과 **붙은 순간
돌아오는지**를 따로 본다.

| 어디서 끊겼나 | 상한 | 이유 |
| --- | --- | --- |
| 시작할 때 (인증 요청 전) | **5분** | 사람이 폰을 집어 연결을 켤 시간 |
| 인증번호를 기다리는 도중 | 25초 | 문자 유효시간이 2분이다 |
| `sms_source=auto` 의 사전 점검 | 25초 | adb 라는 다른 길이 있다 |
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from collect import auth_code, phonelink  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


class FakeApp:
    """[휴대폰 연결] 앱 흉내. `connect_after` 번째 확인부터 붙는다."""

    def __init__(self, connect_after: int | None) -> None:
        self.connect_after = connect_after
        self.checks = 0
        self.presses: list[str] = []

    def is_connected(self) -> bool:
        self.checks += 1
        return self.connect_after is not None and self.checks > self.connect_after


def patched(app: FakeApp):
    """`phonelink` 의 앱 접근을 가짜로 바꾼다. 되돌릴 함수를 돌려준다."""
    saved = {name: getattr(phonelink, name) for name in (
        "is_connected", "connection_status", "offline_reason",
        "_retry_button", "_press", "POLL")}
    phonelink.is_connected = app.is_connected
    phonelink.connection_status = lambda: "오프라인"
    phonelink.offline_reason = lambda: ""
    phonelink._retry_button = lambda: object()
    phonelink._press = lambda button, what: app.presses.append(what)
    phonelink.POLL = 0.01
    phonelink.reset_retry_state()

    def restore() -> None:
        for name, value in saved.items():
            setattr(phonelink, name, value)
        phonelink.reset_retry_state()
    return restore


def check_list_first() -> list[bool]:
    """목록 우선 + 대화창 폴백 인증번호 찾기 (2026-09-18).

    대화창이 비어도(오늘 실측) 목록에서 잡고, 지난 번호(baseline)는 거르고,
    발신번호로 좁히고, 목록이 모호하면 대화창으로 폴백하는지 본다.
    """

    Conv = phonelink.Conversation
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in ("read_conversations", "read_messages")}
    saved_senders = SETTINGS.sms_senders
    try:
        SETTINGS.sms_senders = ["080-000-0000"]
        # 요청 전 목록을 찍은 뒤라고 둔다(기준이 없으면 목록 경로를 쓰지 않는다 — 09-21).
        phonelink._LIST_BASELINE["taken"] = True

        # a) 대화창이 비어도 목록에 새 번호가 있으면 목록에서 잡는다 (오늘의 실패 상황)
        phonelink._SEEN_BODIES.clear()
        phonelink.read_messages = lambda: []
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True,
                 body="[사이트A] 인증번호는 499152 입니다. [2분 이내 입력]", raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("★ 대화창이 비어도 목록에서 인증번호를 잡는다",
                         hit == ("ok", "499152"), str(hit)))

        # b) 요청 전에 이미 있던 본문(baseline)은 고르지 않는다. 새 본문이면 고른다.
        phonelink._SEEN_BODIES.clear()
        phonelink._SEEN_BODIES.add("[사이트A] 인증번호는 499152 입니다. [2분 이내 입력]")
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("요청 전 본문(baseline)이면 건너뛴다", hit is None, str(hit)))
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True,
                 body="[사이트A] 인증번호는 771234 입니다. [2분 이내 입력]", raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("baseline 에 없는 새 본문이면 고른다",
                         hit == ("ok", "771234"), str(hit)))

        # c) 발신번호가 다르면 목록에서 고르지 않는다(대화창도 비어 최종 None)
        phonelink._SEEN_BODIES.clear()
        phonelink.read_messages = lambda: []
        phonelink.read_conversations = lambda: [
            Conv(sender="1588-0000", unread=True, body="[광고] 인증번호는 111111", raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("발신번호가 다르면 목록에서 안 고른다", hit is None, str(hit)))

        # d) 목록 본문에 번호가 여럿이면 대화창으로 폴백해 고른다
        phonelink._SEEN_BODIES.clear()
        now = datetime.now()
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True,
                 body="인증번호 안내 2026 관련 555444 입니다", raw="")]
        phonelink.read_messages = lambda: [(now, "인증번호는 555444 입니다")]
        hit = phonelink._find_code(now, "인증번호")
        out.append(check("★ 목록에 번호 여럿이면 대화창 폴백으로 고른다",
                         hit == ("ok", "555444"), str(hit)))

        # e) snapshot 이 대상 발신번호 본문만 baseline 에 넣는다
        phonelink._SEEN_BODIES.clear()
        phonelink.read_messages = lambda: []
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=False, body="지난 인증번호 000000", raw=""),
            Conv(sender="1588-0000", unread=False, body="남의 번호 999999", raw="")]
        phonelink._snapshot_bodies()
        out.append(check("snapshot 은 대상 발신번호 본문만 baseline 에 넣는다",
                         "지난 인증번호 000000" in phonelink._SEEN_BODIES
                         and "남의 번호 999999" not in phonelink._SEEN_BODIES,
                         str(sorted(phonelink._SEEN_BODIES))))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
        SETTINGS.sms_senders = saved_senders
        phonelink._SEEN_BODIES.clear()
        phonelink._LIST_BASELINE["taken"] = False
    return out


class _Selector:
    """UIA SelectionItem 흉내. `Select()` 를 부르면 골랐다고 표시한다."""

    def __init__(self, ctrl) -> None:
        self.ctrl = ctrl

    def Select(self) -> None:           # noqa: N802 — UIA 쪽 이름이다
        self.ctrl.selected = True


class FakeCtrl:
    def __init__(self, auto_id: str = "", name: str = "") -> None:
        self.element_info = type("Info", (), {"automation_id": auto_id, "name": name})()
        self.selected = False
        self.iface_selection_item = _Selector(self)

    def descendants(self, control_type: str | None = None):
        return []


class FakeWindow:
    def __init__(self, controls: dict) -> None:
        self.controls = controls
        self.keys: list[str] = []

    def descendants(self, control_type: str | None = None):
        return self.controls.get(control_type, [])

    def type_keys(self, keys: str, set_foreground: bool = True) -> None:
        self.keys.append(keys)


def check_codes_and_alt_tab() -> list[bool]:
    """인증번호 후보 고르기·다른 앱 판의 [메시지] 탭 id (10-07). 값은 가짜다."""
    log.info("▶ 인증번호 후보 (하이픈·한글 붙은 숫자) / 다른 판의 [메시지] 탭")
    find = phonelink.CODE_RE.findall
    # 전화번호 꼴은 쓰지 않는다 (probe_leaks 가 막는다) — 하이픈 번호는 대표번호 꼴, 긴 숫자는 9 로 시작
    out = [check("문의 번호(하이픈)는 빼고 인증번호만", find("[Web발신] 인증번호 123456 (문의 1588-1234)") == ["123456"]),
           check("하이픈 날짜는 빼고 인증번호만", find("2026-10-07 인증번호 123456") == ["123456"]),
           check("하이픈 번호·긴 숫자만 있으면 후보 없음", find("1588-1234") == [] and find("99912345678") == []),
           check("한글에 붙은 숫자('2026년'·'15000원')는 전처럼 후보가 아니다 — 잘못 고르지 않게",
                 find("2026년 10월 결제 15000원 인증번호 [123456]") == ["123456"]),
           check("adb 경로(sms)도 같은 규칙", __import__("collect.sms", fromlist=["sms"]).CODE_RE.pattern
                 == phonelink.CODE_RE.pattern),
           check("후보가 여럿이면 여럿 (멈추는 정책 그대로)", find("인증번호 123456 / 654321") == ["123456", "654321"]),
           check("영문에 붙은 숫자는 후보가 아니다", find("A1234") == [])]
    saved = phonelink._window
    try:
        alt = FakeCtrl(auto_id="ChatNodeAutomationId", name="메시지(Ctrl+1)")
        boxes: dict = {"TabItem": [alt]}

        def window_now():
            boxes["List"] = [FakeCtrl(auto_id=phonelink.LIST_AUTO_ID)] if alt.selected else []
            return FakeWindow(boxes)

        phonelink._window = window_now
        phonelink.ensure_messages_tab()
        out.append(check("다른 판 id 의 [메시지] 탭도 고른다 (이름이 메시지일 때)", alt.selected))
        phonelink._window = lambda: FakeWindow({"TabItem": [FakeCtrl(auto_id="ChatNodeAutomationId", name="채팅 앱")]})
        try:
            phonelink.ensure_messages_tab()
            refused = False
        except phonelink.PhoneLinkError:
            refused = True
        out.append(check("다른 판 id 라도 이름이 메시지가 아니면 쓰지 않는다", refused))
        main = FakeCtrl(auto_id=phonelink.MESSAGING_TAB_AUTO_ID, name="메시지(Ctrl+1)")
        other = FakeCtrl(auto_id="ChatNodeAutomationId", name="메시지")
        boxes = {"TabItem": [other, main]}

        def both():
            boxes["List"] = [FakeCtrl(auto_id=phonelink.LIST_AUTO_ID)] if main.selected or other.selected else []
            return FakeWindow(boxes)

        phonelink._window = both
        phonelink.ensure_messages_tab()
        out.append(check("둘 다 있으면 기존 id 를 고른다", main.selected and not other.selected))
    finally:
        phonelink._window = saved
    return out


def check_messages_tab() -> list[bool]:
    """[메시지] 탭 맞추기 + [다시 시도] 이름 판정 (2026-09-21).

    앱을 새로 띄우면 마지막에 보던 탭이 열려 `CVSListView` 가 트리에 없었다.
    그 상태를 흉내 내어, 탭을 **클릭이 아니라 UIA 선택**으로 고르는지 본다.
    """
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in ("_window",)}
    try:
        # a) 이미 [메시지] 탭이면 아무것도 하지 않는다
        tab = FakeCtrl(auto_id=phonelink.MESSAGING_TAB_AUTO_ID, name="메시지(Ctrl+1)")
        listing = FakeCtrl(auto_id=phonelink.LIST_AUTO_ID)
        window = FakeWindow({"List": [listing], "TabItem": [tab]})
        phonelink._window = lambda: window
        phonelink.ensure_messages_tab()
        out.append(check("이미 [메시지] 탭이면 탭을 건드리지 않는다",
                         not tab.selected and not window.keys))

        # b) 목록이 없으면 TabItem 을 **UIA 선택**으로 고른다 (클릭하지 않는다)
        tab = FakeCtrl(auto_id=phonelink.MESSAGING_TAB_AUTO_ID, name="메시지(Ctrl+1)")
        listing = FakeCtrl(auto_id=phonelink.LIST_AUTO_ID)
        boxes: dict = {"TabItem": [tab]}
        moved = FakeWindow(boxes)

        def window_now():
            # 탭을 고르고 나서야 목록이 트리에 나타난다 (실제 앱과 같다)
            boxes["List"] = [listing] if tab.selected else []
            return moved

        phonelink._window = window_now
        phonelink.ensure_messages_tab()
        out.append(check("★ 목록이 없으면 [메시지] 탭을 UIA 선택으로 고른다",
                         tab.selected, f"단축키 사용={moved.keys}"))
        out.append(check("탭을 고를 때 단축키를 먼저 쓰지 않는다", not moved.keys))

        # c) [메시지] 탭 자체가 없으면(오프라인 안내 화면) 사람이 읽을 오류를 낸다
        phonelink._window = lambda: FakeWindow({})
        try:
            phonelink.ensure_messages_tab()
            ok = False
        except phonelink.PhoneLinkError:
            ok = True
        out.append(check("[메시지] 탭이 없으면 PhoneLinkError 를 낸다", ok))

        # d) 같은 auto_id 를 쓰는 '앱 표시' 는 [다시 시도] 가 아니다
        wrong = FakeCtrl(auto_id=phonelink.RETRY_BUTTON_AUTO_ID, name="앱 표시")
        phonelink._window = lambda: FakeWindow({"Button": [wrong]})
        out.append(check("★ auto_id 가 같아도 이름이 '앱 표시' 면 안 누른다",
                         phonelink._retry_button() is None))

        right = FakeCtrl(auto_id=phonelink.RETRY_BUTTON_AUTO_ID, name="다시 시도")
        phonelink._window = lambda: FakeWindow({"Button": [right]})
        out.append(check("이름이 '다시 시도' 면 그 버튼을 쓴다",
                         phonelink._retry_button() is right))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
    return out


def check_old_code_safety() -> list[bool]:
    """**지난 인증번호를 넣지 않는가** — 틀린 번호가 반복되면 계정이 잠긴다 (2026-09-21 검토).

    - 본문이 요청 전과 같으면 대화가 **미읽음으로 돌아와도** 고르지 않는다.
      (폰 쪽 미읽음이 동기화로 되살아난 것과 '같은 번호 재발송' 을 구별할 수 없었다.)
    - 요청 전 목록을 **못 찍었으면** 목록 경로를 쓰지 않는다 (기준이 비면 지난 번호가 새 것이 된다).
    """
    Conv = phonelink.Conversation
    same = "[사이트A] 인증번호는 850320 입니다. [2분 이내 입력]"
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in ("read_conversations", "read_messages")}
    saved_senders = SETTINGS.sms_senders
    try:
        SETTINGS.sms_senders = ["080-000-0000"]
        phonelink.read_messages = lambda: []

        # a) 요청 전과 같은 본문 — 미읽음으로 돌아와도 고르지 않는다
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=False, body=same, raw="")]
        phonelink._snapshot_bodies()
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True, body=same, raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("★ 본문이 같으면 미읽음으로 돌아와도 지난 번호를 고르지 않는다",
                         hit is None, str(hit)))

        # b) 요청 전 목록을 못 찍었다 → 목록에 번호가 보여도 쓰지 않는다
        def broken():
            raise phonelink.PhoneLinkError("UIA 가 잠깐 끊겼다")

        phonelink.read_conversations = broken
        phonelink._snapshot_bodies()
        out.append(check("요청 전 목록을 못 찍으면 기준 없음으로 표시한다",
                         phonelink._LIST_BASELINE["taken"] is False))
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True, body=same, raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("★ 기준이 없으면 목록에 번호가 있어도 고르지 않는다 (대화창만 본다)",
                         hit is None, str(hit)))

        # c) 정상 스냅샷이면 기준 있음, 새 본문은 고른다
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=False, body=same, raw="")]
        phonelink._snapshot_bodies()
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=True,
                 body="[사이트A] 인증번호는 612345 입니다. [2분 이내 입력]", raw="")]
        hit = phonelink._find_code(datetime.now(), "인증번호")
        out.append(check("기준이 있으면 새 본문의 번호는 고른다",
                         phonelink._LIST_BASELINE["taken"] is True
                         and hit == ("ok", "612345"), str(hit)))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
        SETTINGS.sms_senders = saved_senders
        phonelink._SEEN_BODIES.clear()
        phonelink._LIST_BASELINE["taken"] = False
    return out


def check_covered_window() -> list[bool]:
    """가려진 [휴대폰 연결] 창을 **좌표로 누르지 않는가** (2026-09-21 검토).

    모니터가 1개면 인증 중인 브라우저가 이 앱을 덮는다. 앞으로 가져오지 못한 채
    누르면 브라우저(메일 사이트 인증 화면)의 엉뚱한 곳이 눌린다.
    """
    out: list[bool] = []
    clicks: list[str] = []
    item = SimpleNamespace(
        element_info=SimpleNamespace(
            name="080-000-0000와의 대화. 메시지 미리 보기. 인증번호는 111111 입니다"),
        click_input=lambda: clicks.append("click"))
    listing = SimpleNamespace(descendants=lambda control_type=None: [item])
    saved = {"_require_conversation_list": phonelink._require_conversation_list,
             "bring_forward": phonelink.ui.bring_forward}
    saved_senders = SETTINGS.sms_senders
    try:
        SETTINGS.sms_senders = ["080-000-0000"]
        phonelink._require_conversation_list = lambda: listing

        phonelink.ui.bring_forward = lambda ctrl, what: False
        opened = phonelink.open_conversation("인증번호")
        out.append(check("★ 창을 앞으로 못 가져오면 대화를 누르지 않는다",
                         opened is False and not clicks, f"클릭 {clicks}"))

        phonelink.ui.bring_forward = lambda ctrl, what: True
        opened = phonelink.open_conversation("인증번호")
        out.append(check("앞으로 가져오면 누른다", opened is True and clicks == ["click"]))
    finally:
        phonelink._require_conversation_list = saved["_require_conversation_list"]
        phonelink.ui.bring_forward = saved["bring_forward"]
        SETTINGS.sms_senders = saved_senders
    return out


def check_offline_not_ready() -> list[bool]:
    """오프라인이면 **캐시된 목록에 속아 '준비됨' 이라 하지 않는가** (2026-09-21 실측).

    오프라인이어도 `read_conversations()` 는 지난 동기화 내용을 돌려준다. 예전에는
    그걸 보고 준비됨이라 해서, 올 수 없는 문자를 120초 기다리고 문자 한 통을 버렸다.
    """
    Conv = phonelink.Conversation
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in (
        "ensure_app", "_window", "is_connected", "reconnect", "read_conversations",
        "connection_status")}
    try:
        phonelink.ensure_app = lambda: None
        phonelink._window = lambda: type(
            "W", (), {"window_text": lambda self: "휴대폰과 연결",
                      "descendants": lambda self, control_type=None: []})()
        phonelink.connection_status = lambda: "오프라인"
        # 오프라인이어도 목록은 읽힌다 — 실측 그대로 흉내 낸다
        phonelink.read_conversations = lambda: [
            Conv(sender="080-000-0000", unread=False, body="지난 인증번호 850320", raw="")]

        phonelink.is_connected = lambda: False
        phonelink.reconnect = lambda timeout=None: False
        try:
            phonelink.check_ready()
            ok = False
        except phonelink.PhoneLinkError as exc:
            ok = "받을 수 없다" in str(exc)
        out.append(check("★ 오프라인+재연결 실패면 목록이 읽혀도 준비됨이라 하지 않는다", ok))

        phonelink.reconnect = lambda timeout=None: True
        phonelink.is_connected = lambda: False
        out.append(check("재연결이 되면 준비됨으로 넘어간다",
                         phonelink.check_ready() == "휴대폰과 연결"))

        called: list[float | None] = []
        phonelink.is_connected = lambda: True
        phonelink.reconnect = lambda timeout=None: called.append(timeout) or True
        phonelink.check_ready()
        out.append(check("연결돼 있으면 재연결을 부르지 않는다", not called, str(called)))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
    return out


def check_transient_status() -> list[bool]:
    """`'새로 고치는 중...'` 을 끊김으로 오판하지 않는가 (2026-09-21).

    실측: 이 상태를 끊김으로 보고 새로 고침을 **또** 눌러 사전 점검 한 단계에서
    97초를 버렸다. 그 동안에도 대화 목록은 읽혔다.
    """
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in (
        "connection_status", "_retry_button", "_press", "_click_refresh",
        "offline_reason", "POLL")}
    try:
        # --- 연결 판정은 **정확히 일치** — 일시 상태는 '붙음' 이 아니다 (09-21 검토)
        cases = [
            ("연결됨", True, "'연결됨' 은 연결됨"),
            ("Connected", True, "영어 'Connected' 도 연결됨"),
            ("새로 고치는 중...", False,
             "★ '새로 고치는 중' 은 **붙은 것이 아니다** (오프라인 차단을 뚫지 않는다)"),
            ("Disconnected", False, "★ 영어 'Disconnected' 를 연결로 보지 않는다 (부분 일치 금지)"),
            ("Not connected", False, "영어 'Not connected' 도 연결 아님"),
            ("오프라인", False, "'오프라인' 은 끊김이다"),
            ("", True, "못 읽으면 판정하지 않는다(True)"),
        ]
        for status, expected, label in cases:
            phonelink.connection_status = lambda s=status: s
            got = phonelink.is_connected()
            out.append(check(label, got is expected, f"{status!r} → {got}"))

        # --- 일시 상태면 reconnect 는 **아무것도 누르지 않고** 붙기만 기다린다
        presses: list[str] = []
        phonelink._retry_button = lambda: object()
        phonelink._press = lambda button, what: presses.append(what)
        phonelink._click_refresh = lambda: presses.append("새로 고침") or True
        phonelink.offline_reason = lambda: ""
        phonelink.POLL = 0.01
        phonelink.reset_retry_state()

        reads = {"n": 0}

        def settling():
            reads["n"] += 1
            return "새로 고치는 중..." if reads["n"] <= 3 else "연결됨"

        phonelink.connection_status = settling
        got = phonelink.reconnect(timeout=2.0)
        out.append(check("★ '새로 고치는 중' 이면 누르지 않고 기다리다 붙으면 True",
                         got is True and presses == [], f"결과 {got}, 누름 {presses}"))

        phonelink.connection_status = lambda: "새로 고치는 중..."
        got = phonelink.reconnect(timeout=0.3)
        out.append(check("끝내 안 붙으면 False, 그래도 누르지 않는다",
                         got is False and presses == [], f"결과 {got}, 누름 {presses}"))

        phonelink.connection_status = lambda: "오프라인"
        got = phonelink.reconnect(timeout=0.3)
        out.append(check("진짜 끊김(오프라인)이면 [다시 시도] 를 한 번 누른다",
                         presses == ["[다시 시도]"], str(presses)))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
        phonelink.reset_retry_state()
    return out


def check_prepare_blocks_offline() -> list[bool]:
    """`prepare()` 가 끊긴 채로 **문자를 요청하게 두지 않는가** (09-21 검토).

    예전에는 `reconnect()` 결과를 버리고 대화를 열어 요청으로 넘어갔다.
    """
    out: list[bool] = []
    saved = {n: getattr(phonelink, n) for n in (
        "check_ready", "is_connected", "reconnect", "open_conversation",
        "connection_status")}
    opened: list[str] = []
    try:
        phonelink.check_ready = lambda reconnect_timeout=None: "가짜"
        phonelink.is_connected = lambda: False
        phonelink.reconnect = lambda timeout=None: False
        phonelink.connection_status = lambda: "오프라인"
        phonelink.open_conversation = lambda keyword: opened.append(keyword) or True
        try:
            phonelink.prepare("인증번호")
            ok = False
        except phonelink.PhoneLinkError:
            ok = True
        out.append(check("★ 요청 직전에 끊겼고 못 붙으면 요청하지 않는다 (대화도 안 연다)",
                         ok and not opened, f"연 대화 {opened}"))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
    return out


def check_peek() -> list[bool]:
    """예약 전 휴대폰 점검 (10-01) — 사람이 PC 를 쓰는 중에 돈다. 앱을 띄우거나·누르거나·탭을 바꾸지 않는다."""
    import subprocess
    from types import SimpleNamespace

    from collect import sms

    touched: list[str] = []
    quiet = ("launch_app", "ensure_app", "_press", "_click_refresh", "ensure_messages_tab", "reconnect")
    saved = {n: getattr(phonelink, n) for n in (*quiet, "connection_status", "offline_reason")}
    saved_sms = (sms.check_device, sms.subprocess.run, sms._adb_path)
    saved_settings = {n: getattr(SETTINGS, n) for n in ("phone_os", "sms_source", "adb_connection",
                                                        "adb_wireless_address")}
    retry_before = (dict(phonelink._RETRY_STATE), dict(phonelink._LAUNCH_STATE))
    out = []
    try:
        for name in quiet:
            setattr(phonelink, name, lambda *a, _n=name, **k: touched.append(_n))
        phonelink.offline_reason = lambda: "SM-A000N 에서 Windows와 연결이(가) 꺼져 있습니다"

        def closed():
            raise phonelink.PhoneLinkError("창 없음")

        cases = []
        for status in (closed, lambda: "연결됨", lambda: "오프라인", lambda: "새로 고치는 중...", lambda: ""):
            phonelink.connection_status = status
            cases.append(phonelink.peek())
        out.append(check("★ 보기만 한다 — 앱 띄우기·누르기·탭 바꾸기·재연결을 한 번도 부르지 않는다",
                         touched == [] and (dict(phonelink._RETRY_STATE), dict(phonelink._LAUNCH_STATE)) == retry_before,
                         str(touched)))
        out.append(check("창 없음·맞추는 중·못 읽음 → 모름(None), 연결됨 → 준비됨, 오프라인 → 안 됨 ('못 읽음 = 연결됨' 으로 보지 않는다)",
                         [c[0] for c in cases] == [None, True, False, None, None], str(cases)))
        out.append(check("사유에 기기 이름이 없다 (앱 안내는 파일 로그에만)", all("SM-" not in c[1] for c in cases)))

        SETTINGS.phone_os, SETTINGS.adb_wireless_address = "android", ""
        sms.check_device = lambda: (_ for _ in ()).throw(sms.SmsError("기기가 준비되지 않았다(SERIAL00002 unauthorized)"))
        SETTINGS.sms_source, SETTINGS.adb_connection = "adb", "usb"
        adb_bad = auth_code.precheck()
        SETTINGS.adb_connection, SETTINGS.adb_wireless_address = "wireless", "192.0.2.50:5555"
        wireless_bad = auth_code.precheck()
        SETTINGS.adb_connection, SETTINGS.adb_wireless_address = "usb", ""
        SETTINGS.sms_source = "auto"
        phonelink.connection_status = lambda: "오프라인"
        sms.check_device = lambda: "SERIAL00002"
        auto_ok = auth_code.precheck()
        SETTINGS.phone_os = "ios"
        ios = auth_code.precheck()
        out.append(check("adb 가 안 되면 안 됨 + 고정 사유 (시리얼 없음) / auto 는 adb 가 되면 준비됨 / 설정이 틀리면 예외 없이 안 됨",
                         adb_bad == (False, auth_code.PRECHECK_NO_ADB) and "SERIAL" not in adb_bad[1]
                         and wireless_bad == (False, auth_code.PRECHECK_NO_WIRELESS)
                         and "192.168" not in wireless_bad[1]
                         and auto_ok[0] is True and ios == (False, auth_code.PRECHECK_SETTING),
                         f"{adb_bad} / {wireless_bad} / {auto_ok} / {ios}"))

        captured: dict = {}
        sms._adb_path = lambda: "adb"
        sms.subprocess.run = lambda *a, **k: captured.update(k) or SimpleNamespace(stdout="", stderr="", returncode=0)
        sms._run("devices", "-l")
        out.append(check("adb 호출에 콘솔 창을 띄우지 않는다 (창 없는 빌드본에서 깜박이지 않게)",
                         captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)
        sms.check_device, sms.subprocess.run, sms._adb_path = saved_sms
        for name, value in saved_settings.items():
            setattr(SETTINGS, name, value)
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 상한 값")
    results.append(check("★ 시작할 때 기다리는 상한은 5분이다",
                         SETTINGS.timeouts.phone_connect == 300.0,
                         f"{SETTINGS.timeouts.phone_connect:.0f}초"))
    results.append(check("인증번호 대기 중 복구는 짧다 (문자 유효시간 2분 안)",
                         phonelink.RECOVER_RECONNECT_WAIT
                         < SETTINGS.timeouts.sms_code,
                         f"{phonelink.RECOVER_RECONNECT_WAIT:.0f}초 < "
                         f"{SETTINGS.timeouts.sms_code:.0f}초"))

    log.info("▶ ★ 붙으면 **5분을 다 쓰지 않고** 바로 돌아온다")
    app = FakeApp(connect_after=3)
    restore = patched(app)
    try:
        started = time.monotonic()
        ok = phonelink.reconnect()           # 상한 5분
        spent = time.monotonic() - started
    finally:
        restore()
    results.append(check("붙었다고 돌려준다", ok is True))
    results.append(check("★ 붙은 순간 돌아온다 (5분 상한인데 1초도 안 걸린다)",
                         spent < 1.0, f"{spent:.3f}초"))
    results.append(check("[다시 시도] 는 한 번만 눌렀다", app.presses == ["[다시 시도]"],
                         str(app.presses)))

    log.info("▶ 끝내 안 붙으면 상한에서 멈추고, **더 누르지 않는다**")
    app = FakeApp(connect_after=None)
    restore = patched(app)
    try:
        started = time.monotonic()
        ok = phonelink.reconnect(timeout=0.3)
        spent = time.monotonic() - started
        again = phonelink.reconnect(timeout=0.3)   # 두 번째 부름
    finally:
        restore()
    results.append(check("안 붙었다고 돌려준다", ok is False))
    results.append(check("상한에서 멈춘다", 0.25 <= spent < 2.0, f"{spent:.2f}초"))
    results.append(check("두 번째 부름에서 **다시 누르지 않는다**",
                         again is False and len(app.presses) == 1,
                         f"누른 횟수 {len(app.presses)}"))

    log.info("▶ 부르는 자리마다 상한이 맞게 들어간다")
    seen: dict[str, float | None] = {}
    saved = {name: getattr(phonelink, name) for name in (
        "reconnect", "is_connected", "ensure_app", "_window",
        "read_conversations", "open_conversation", "check_ready")}
    try:
        phonelink.reconnect = lambda timeout=None: seen.__setitem__("last", timeout) or True
        phonelink.is_connected = lambda: False
        phonelink.ensure_app = lambda: None
        phonelink._window = lambda: type("W", (), {"window_text": lambda self: "가짜"})()
        phonelink.read_conversations = lambda: ["대화"]
        phonelink.open_conversation = lambda keyword: True

        phonelink.check_ready()
        results.append(check("★ 시작 점검(check_ready)은 기본 상한(5분)을 쓴다",
                             seen.get("last") is None,
                             f"넘긴 값 {seen.get('last')} (None = 5분)"))

        phonelink._recover(None, {"tries": 0})
        results.append(check("★ 인증번호 대기 중 복구는 25초를 쓴다",
                             seen.get("last") == phonelink.RECOVER_RECONNECT_WAIT,
                             f"넘긴 값 {seen.get('last')}"))

        asked: dict[str, float | None] = {}
        phonelink.check_ready = lambda reconnect_timeout=None: (
            asked.__setitem__("t", reconnect_timeout) or "가짜")
        auth_code._phonelink_ready()
        results.append(check("★ `auto` 의 사전 점검은 짧게 본다 (adb 로 넘어갈 수 있게)",
                             asked.get("t") == phonelink.RECOVER_RECONNECT_WAIT,
                             f"넘긴 값 {asked.get('t')}"))
    finally:
        for name, value in saved.items():
            setattr(phonelink, name, value)

    log.info("▶ 목록 우선 + 대화창 폴백 인증번호 찾기 (2026-09-18)")
    results.extend(check_list_first())

    log.info("▶ 오프라인일 때 준비됨 판정 (2026-09-21)")
    results.extend(check_offline_not_ready())

    log.info("▶ 지난 번호를 넣지 않는가 — 계정 잠김 방지 (2026-09-21 검토)")
    results.extend(check_old_code_safety())

    log.info("▶ 가려진 창을 누르지 않는가 (2026-09-21 검토)")
    results.extend(check_covered_window())

    log.info("▶ 끊긴 채로 문자를 요청하지 않는가 (2026-09-21 검토)")
    results.extend(check_prepare_blocks_offline())

    log.info("▶ 연결 상태 일시 판정 (2026-09-21)")
    results.extend(check_transient_status())

    log.info("▶ [메시지] 탭 맞추기 + [다시 시도] 이름 판정 (2026-09-21)")
    results.extend(check_messages_tab())
    results.extend(check_codes_and_alt_tab())

    log.info("▶ 예약 전 점검은 보기만 한다 (2026-10-01)")
    results.extend(check_peek())

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 미검증: 실제 앱에서 끊긴 뒤 사람이 폰에서 연결을 켜 **5분 안에 "
             "붙는 장면**은 보지 않았다. 여기서는 상한과 돌아오는 시점만 본다.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
