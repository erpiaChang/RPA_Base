r"""인증번호 획득 — 경로 선택 창구.

업무 흐름(사이트 파일 — `collect/sites/`)은 이 모듈만 부른다. 어느 경로인지 몰라도 된다.

## 설정 세 가지

| 설정 | 값 | 뜻 |
| --- | --- | --- |
| `phone_os` | `android` / `ios` | 인증 문자를 받는 휴대폰 |
| `sms_source` | `phonelink` / `adb` / `auto` | 어떤 방법으로 읽을 것인가 |
| `adb_connection` | `usb` / `wireless` | adb 를 쓸 때의 연결 방식 |

## 경로별 전제

| 경로 | 케이블 | 네트워크 | 비고 |
| --- | --- | --- | --- |
| `phonelink` | 불필요 | **폰이 인터넷에 있으면 된다** | PC 의 [휴대폰 연결] 앱이 `연결됨` 이어야 한다. 폰에서 [모바일 데이터를 통한 동기화] 허용 |
| `adb` + `usb` | **필요** | 불필요 | 폰의 [개발자 옵션 → USB 디버깅] + 허용 |
| `adb` + `wireless` | 불필요 | **PC 와 폰이 같은 네트워크** | 한 번 페어링 후 `adb_wireless_address` |

`auto` 는 **phonelink 를 먼저** 보고, 준비가 안 됐으면 adb 로 넘어간다.

## iPhone

`phone_os="ios"` 는 **아직 검증하지 않았다.** adb 는 Android 전용이라 애초에 불가능하고,
[휴대폰 연결] 로 iPhone 문자를 읽는 경로는 이 프로젝트에서 실측한 적이 없다.
그래서 지금은 **명시적으로 중단**한다. 추측으로 동작하는 척하지 않는다.
(확장 지점: iPhone 을 연결해 `tools/probe_phone_window.py --dump` 로 조사한 뒤 여기를 푼다.)

두 경로 모두 **읽기만 한다.** 문자를 보내거나 지우지 않는다.
"""
from __future__ import annotations

from datetime import datetime

from config.settings import SETTINGS
from utils.logger import get_logger
from utils.wait import WaitTimeout

log = get_logger(__name__)

SOURCE_PHONELINK = "phonelink"
SOURCE_ADB = "adb"
SOURCE_AUTO = "auto"
SOURCES = (SOURCE_PHONELINK, SOURCE_ADB, SOURCE_AUTO)

OS_ANDROID = "android"
OS_IOS = "ios"
PHONE_OS = (OS_ANDROID, OS_IOS)

CONNECTION_USB = "usb"
CONNECTION_WIRELESS = "wireless"
CONNECTIONS = (CONNECTION_USB, CONNECTION_WIRELESS)


class AuthCodeError(RuntimeError):
    """인증번호를 얻지 못했다."""


class AuthCodeNotSupported(AuthCodeError):
    """설정 조합이 아직 지원되지 않는다. 추측으로 진행하지 않는다."""


class CodeNotArrived(AuthCodeError):
    """기다리는 시간 안에 인증 문자가 오지 않았다. 새로 요청하면 올 수 있다 (`webmail.login`)."""


def _reraise(exc: Exception) -> None:
    """읽기 경로의 실패를 `AuthCodeError` 로 올린다. 대기 시간 초과만 `CodeNotArrived`."""
    kind = CodeNotArrived if isinstance(exc.__cause__, WaitTimeout) else AuthCodeError
    raise kind(str(exc)) from exc


def _value(name: str, allowed: tuple[str, ...]) -> str:
    """설정값 하나. **기본값이 없다** (10-02) — 비었거나 목록 밖이면 멈춘다."""
    raw = (getattr(SETTINGS, name, None) or "").strip().lower()
    if raw not in allowed:
        raise AuthCodeError(
            f"settings 의 {name} 가 올바르지 않다: {getattr(SETTINGS, name, None)!r}. "
            f"{' / '.join(allowed)} 중 하나로 둘 것.")
    return raw


def _check_combination() -> tuple[str, str, str]:
    """설정 조합을 검사한다. 잘못된 조합은 실행 전에 막는다."""
    phone_os = _value("phone_os", PHONE_OS)
    source = _value("sms_source", SOURCES)
    # adb 연결 방식은 adb 를 쓸 때만 본다 — [휴대폰 연결] 만 쓰면 비어 있어도 된다
    connection = _value("adb_connection", CONNECTIONS) if source in (SOURCE_ADB, SOURCE_AUTO) else ""

    if phone_os == OS_IOS:
        raise AuthCodeNotSupported(
            "iPhone 은 아직 지원하지 않는다. adb 는 Android 전용이고, [휴대폰 연결] 로 "
            "iPhone 문자를 읽는 경로는 실측한 적이 없다. "
            "확인되기 전에는 추측으로 진행하지 않는다.")

    if source == SOURCE_ADB and connection == CONNECTION_WIRELESS \
            and not SETTINGS.adb_wireless_address:
        raise AuthCodeError(
            "adb_connection 이 wireless 인데 adb_wireless_address 가 비어 있다. "
            "tools/test_adb_wireless.py 로 페어링·연결한 뒤 다시 실행할 것.")

    # 고르는 말이 비면 아무 문자의 숫자를 인증번호로 읽는다 — 조용히 조건 없이 돌지 않는다 (기본값 없음, 10-02 검토)
    if not str(getattr(SETTINGS, "sms_keyword", None) or "").strip():
        raise AuthCodeError("settings 의 sms_keyword 가 비어 있다 — 인증 문자를 고를 말이 없다.")

    return phone_os, source, connection


def _phonelink_ready() -> bool:
    from collect import phonelink

    try:
        # ★ `auto` 에서만 부른다. 여기서 5분을 기다리면 adb 가 준비돼 있어도
        #   5분을 버린다. 다른 길이 있으니 **짧게** 보고 넘어간다 (2026-09-17).
        phonelink.check_ready(reconnect_timeout=phonelink.RECOVER_RECONNECT_WAIT)
        return True
    except phonelink.PhoneLinkError as exc:
        log.info("휴대폰 연결 경로를 쓸 수 없다: %s", exc)
        return False


def _adb_ready() -> bool:
    from collect import sms

    try:
        sms.check_device()
        return True
    except sms.SmsError as exc:
        log.info("adb 경로를 쓸 수 없다: %s", exc)
        return False


def resolve() -> str:
    """실제로 쓸 경로를 정한다. `auto` 면 준비된 쪽을 고른다."""
    _, source, connection = _check_combination()

    if source != SOURCE_AUTO:
        log.info("인증번호 경로: %s%s", source,
                 f" ({connection})" if source == SOURCE_ADB else "")
        return source

    log.info("인증번호 경로: auto — 준비된 쪽을 고른다")
    if _phonelink_ready():
        log.info("→ phonelink 로 진행한다")
        return SOURCE_PHONELINK
    if _adb_ready():
        log.info("→ adb 로 진행한다")
        return SOURCE_ADB
    raise AuthCodeError(
        "인증 문자를 읽을 수 있는 경로가 없다.\n"
        "  (1) [휴대폰 연결] 앱을 실행하고 폰이 '연결됨' 인지 확인하거나\n"
        "  (2) 폰을 USB 로 연결하고 USB 디버깅을 허용할 것.")


# 예약 전 점검(`precheck`)의 사람 말 — 기기 번호·설정 키를 넣지 않는다 (10-01)
PRECHECK_SETTING = "인증 문자 설정이 맞지 않습니다 — [설정] 탭 [인증 문자] 를 확인하세요"
PRECHECK_NO_ADB = ("USB 로 연결한 휴대폰을 찾지 못했습니다 — 케이블과 휴대폰의 "
                   "[USB 디버깅] 허용을 확인하세요")
PRECHECK_NO_WIRELESS = ("무선으로 휴대폰에 연결하지 못했습니다 — 휴대폰과 PC 가 같은 와이파이인지, "
                        "휴대폰의 [무선 디버깅] 이 켜져 있는지 확인하세요")


def precheck() -> tuple[bool | None, str]:
    """예약 전에 **보기만** 하는 점검 (10-01). `(준비됨, 사람 말)` — None 은 알 수 없음. 예외를 올리지 않는다.

    `preflight` 와 달리 앱을 띄우거나 [다시 시도] 를 누르지 않는다 — 사람이 PC 를 쓰는 중일 수 있고,
    [다시 시도] 는 실행마다 한 번이다 (09-10). 실행 때의 재연결(최대 5분)은 그대로다.
    """
    try:
        _, source, connection = _check_combination()
    except AuthCodeError:
        return False, PRECHECK_SETTING
    from collect import phonelink

    first: tuple[bool | None, str] = (None, "")
    if source in (SOURCE_PHONELINK, SOURCE_AUTO):
        first = phonelink.peek()
        if source == SOURCE_PHONELINK or first[0]:
            return first
    from collect import sms

    try:
        sms.check_device()                  # adb devices (무선이면 connect 먼저) — 폰은 건드리지 않는다
    except Exception as exc:                # noqa: BLE001 — SmsError·adb 없음·시간 초과. 점검이 예약 시계를 죽이면 안 된다
        log.info("adb 점검: %s", exc)
        if source == SOURCE_AUTO:
            return first
        return False, PRECHECK_NO_WIRELESS if connection == CONNECTION_WIRELESS else PRECHECK_NO_ADB
    return True, phonelink.PEEK_READY


def preflight() -> str:
    """실행 전에 설정과 준비 상태를 점검한다. 사람이 읽을 요약을 돌려준다."""
    phone_os, source, connection = _check_combination()
    chosen = resolve()

    lines = [
        f"휴대폰: {phone_os}",
        f"설정된 경로: {source}" + (f" ({connection})" if source == SOURCE_ADB else ""),
        f"실제 사용 경로: {chosen}",
    ]
    # 준비 상태를 볼 때 나는 예외는 **경로별 예외**다. 그대로 올리면 부르는 쪽이
    # `AuthCodeError` 만 잡고 있어 traceback 이 사용자에게 그대로 보인다
    # (2026-09-10 실측 — `sms_source` 를 phonelink 로 못 박고 앱을 닫아 둔 경우).
    if chosen == SOURCE_PHONELINK:
        from collect import phonelink

        try:
            title = phonelink.check_ready()
        except phonelink.PhoneLinkError as exc:
            raise AuthCodeError(str(exc)) from exc
        lines.append(f"휴대폰 연결 앱: {title} — 준비됨")
    else:
        from collect import sms

        try:
            serial = sms.check_device()
        except sms.SmsError as exc:
            raise AuthCodeError(str(exc)) from exc
        lines.append(f"adb 기기: {serial} — 준비됨")
        if connection == CONNECTION_WIRELESS:
            lines.append(f"무선 주소: {SETTINGS.adb_wireless_address}")

    summary = "\n  ".join(lines)
    log.info("사전 점검\n  %s", summary)
    return summary


def prepare() -> datetime:
    """인증번호를 **요청하기 직전에** 부른다. 기준 시각을 돌려준다.

    이 시각 **이후에 도착한** 문자만 인증번호로 쓴다.
    phonelink 는 이때 대화를 열어 둔다 (수신 시각이 대화창에만 있다).
    """
    source = resolve()
    if source == SOURCE_ADB:
        from collect import sms

        try:
            sms.check_device()
        except sms.SmsError as exc:
            raise AuthCodeError(str(exc)) from exc
        return datetime.now()

    from collect import phonelink

    try:
        return phonelink.prepare(keyword=SETTINGS.sms_keyword)
    except phonelink.PhoneLinkError as exc:
        raise AuthCodeError(str(exc)) from exc


def wait(since: datetime) -> str:
    """`prepare()` 가 준 기준 시각 이후에 도착한 인증번호."""
    source = resolve()
    keyword = SETTINGS.sms_keyword

    if source == SOURCE_ADB:
        from collect import sms

        try:
            return sms.wait_for_code(since=since, keyword=keyword)
        except sms.SmsError as exc:
            _reraise(exc)

    from collect import phonelink

    try:
        return phonelink.wait_for_code(since=since, keyword=keyword)
    except phonelink.PhoneLinkError as exc:
        _reraise(exc)
