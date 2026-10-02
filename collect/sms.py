r"""인증 문자 읽기 (Android + adb).

**폰에는 아무것도 설치하지 않는다.** adb 는 PC 쪽 도구이고, 폰에서 켜는 것은
개발자 옵션의 USB 디버깅뿐이다. 대신 **USB 연결이 전제**다.

사용자 지시(2026-09-08): 휴대폰을 연결할 때는 **메시지만 읽을 수 있어야 한다.**
그래서 adb 명령을 화이트리스트로 묶었다. 아래 두 가지 외에는 실행하지 않는다.

    adb devices -l
    adb shell content query --uri content://sms/inbox ...

`pull` / `push` / `install` / 화면 조작은 코드에 없다. 명령은 `_run()` 한 곳에서만
나가고, 허용 목록에 없으면 실행 전에 막는다.

실측(2026-09-08, 갤럭시 SM-A000N):

- `READ_SMS` 문제 없이 읽힌다
- `--sort` 값은 **따옴표로 감싸야 한다.** adb 가 인자를 이어붙여 폰 셸에 넘기므로
  감싸지 않으면 `date DESC` 가 쪼개져 `Unsupported argument: DESC` 로 실패한다
- `content query` 는 **실패해도 종료코드 0** 에 사용법을 출력한다.
  `Row:` 로 시작하는 줄이 있는지로만 성공을 판정한다
- `date` 는 epoch 밀리초
- 본문은 **여러 줄**이다. 줄 단위로 파싱하면 `[Web발신]` 만 남는다
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.settings import SETTINGS
from utils.logger import get_logger
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

# ── 허용 명령. 이 목록 밖은 실행하지 않는다 ────────────────────────────────
ALLOWED = (
    ("devices", "-l"),
    ("shell", "content", "query"),
    # 무선 디버깅 연결. 데이터를 읽는 명령이 아니라 **연결을 맺는** 명령이다.
    ("pair",),
    ("connect",),
    ("disconnect",),
    # 무선 디버깅 포트는 폰을 재부팅하면 바뀐다. mDNS 로 찾으면 매번 묻지 않아도 된다.
    ("mdns", "services"),
)
SMS_URI = "content://sms/inbox"
FORBIDDEN = ("pull", "push", "install", "uninstall", "root", "reboot",
             "forward", "rm", "am", "pm", "settings", "input", "screencap")

CMD_TIMEOUT = 20                 # adb 응답 상한(초)
CONNECT_TIMEOUT = 60             # pair/connect 는 더 오래 걸린다(실측 2026-09-08)
SMS_POLL = 2.0                   # 문자 도착 확인 간격(초)
CODE_RE = re.compile(r"\b(\d{4,8})\b")
# 발신번호 비교용. 표기 차이를 흡수하려고 숫자만 남긴다. 부분 일치는 하지 않는다.
DIGITS_RE = re.compile(r"\D+")
# ★ 문자 본문은 **여러 줄**이다. adb 출력은 한 행이 한 줄이 아니므로 줄 단위로
#   파싱하면 첫 줄(`[Web발신]`)만 남고 인증번호가 사라진다(실측 2026-09-08).
#   행 시작 표시(`Row: N `)로 통째로 잘라야 한다.
ROW_SPLIT_RE = re.compile(r"^Row: \d+ ", re.M)


class SmsError(RuntimeError):
    """문자를 읽지 못했다."""


@dataclass(frozen=True)
class Message:
    address: str
    received: datetime
    body: str

    def codes(self) -> list[str]:
        return CODE_RE.findall(self.body)


def normalize_number(value: str) -> str:
    """전화번호에서 숫자만 남긴다."""
    return DIGITS_RE.sub("", value or "")


def allowed_senders() -> list[str]:
    """설정된 발신번호 목록(숫자만). 비어 있으면 발신자 조건을 걸지 않는다."""
    return [n for n in (normalize_number(v) for v in (SETTINGS.sms_senders or [])) if n]


def _adb_path() -> str:
    configured = SETTINGS.adb_path
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("adb")
    if not found:
        raise SmsError(
            "adb 를 찾지 못했다. config/settings.local.json 의 adb_path 를 채울 것.")
    return found


def _check_allowed(args: tuple[str, ...]) -> None:
    for bad in FORBIDDEN:
        if bad in args:
            raise PermissionError(f"이 코드가 보내지 않는 adb 명령이다: {bad}")
    if not any(args[:len(prefix)] == prefix for prefix in ALLOWED):
        raise PermissionError(f"허용되지 않은 adb 명령이다: {' '.join(args)}")
    if args[0] == "shell" and SMS_URI not in args:
        raise PermissionError("shell 은 문자 조회(content://sms/inbox)에만 쓴다.")


def _run(*args: str) -> subprocess.CompletedProcess:
    _check_allowed(args)
    adb = _adb_path()
    # 페어링·연결은 네트워크를 타서 20초를 넘길 수 있다.
    timeout = CONNECT_TIMEOUT if args[0] in ("pair", "connect") else CMD_TIMEOUT
    log.debug("adb %s", " ".join(args))
    return subprocess.run(
        [adb, *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        # 창 없는 빌드본에서 콘솔 창이 깜박이지 않게 — 예약 전 점검은 사람이 PC 를 쓰는 중에 돈다 (10-01)
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def pair(address: str, code: str) -> str:
    """무선 디버깅 **페어링**. 폰의 [기기 페어링] 화면에 뜬 주소와 6자리 코드가 필요하다.

    페어링용 포트와 연결용 포트는 **다르다.** 페어링은 한 번만 하면 되고,
    이후에는 `connect()` 만 쓴다.
    """
    result = _run("pair", address, code)
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if "Successfully paired" not in output:
        raise SmsError(f"무선 디버깅 페어링 실패: {output[:200]}")
    log.info("무선 디버깅 페어링 성공: %s", address)
    return output


def discover() -> list[str]:
    """mDNS 로 무선 디버깅 중인 기기의 `IP:포트` 를 찾는다.

    폰이 같은 네트워크에서 무선 디버깅을 켜 두면 `_adb-tls-connect._tcp` 로 광고된다.
    페어링 대기 중인 것(`_adb-tls-pairing._tcp`)은 연결용이 아니라 제외한다.
    """
    result = _run("mdns", "services")
    found = []
    for line in (result.stdout or "").splitlines():
        if "_adb-tls-connect" not in line:
            continue
        parts = line.split()
        if parts:
            found.append(parts[-1])
    log.info("무선 디버깅 후보: %s", found or "(없음)")
    return found


def connect(address: str) -> str:
    """무선 디버깅 **연결**. 폰의 [무선 디버깅] 화면 상단 `IP:포트` 를 쓴다."""
    result = _run("connect", address)
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if "connected to" not in output:
        raise SmsError(f"무선 연결 실패: {output[:200]}")
    log.info("무선 연결: %s", output)
    return output


def check_device() -> str:
    """연결된 기기를 확인하고 시리얼을 돌려준다. 없으면 즉시 중단한다.

    `adb_wireless_address` 가 설정돼 있으면 **먼저 무선 연결을 시도한다.**
    USB 케이블이 없어도 되는 경로다.
    """
    if SETTINGS.adb_wireless_address:
        try:
            connect(SETTINGS.adb_wireless_address)
        except SmsError as exc:
            # USB 로 붙어 있을 수도 있다. 아래에서 기기 목록으로 다시 판정한다.
            log.warning("무선 연결에 실패했다(USB 로 붙어 있는지 확인한다): %s", exc)
    result = _run("devices", "-l")
    devices = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
    if not devices:
        raise SmsError(
            "연결된 Android 기기가 없다. 둘 중 하나로 연결할 것. "
            "(1) USB: 케이블 연결 + [개발자 옵션 → USB 디버깅] + 폰의 허용 창 승인. "
            "파일 전송(MTP)만으로는 잡히지 않는다. "
            "(2) 무선: [개발자 옵션 → 무선 디버깅] 을 켜고 한 번 페어링한 뒤, "
            "settings 의 adb_wireless_address 에 `IP:포트` 를 넣을 것. "
            "PC 와 폰이 **같은 네트워크**에 있어야 한다.")
    # `adb devices -l` 한 줄 = "<serial> <state> ...". state 가 `device` 인 것만 쓸 수 있다 (unauthorized·offline 제외)
    ready = [line for line in devices if line.split()[1:2] == ["device"]]
    if not ready:
        raise SmsError(f"기기가 준비되지 않았다({devices[0]}). unauthorized 면 폰 화면의 USB 디버깅 허용 창을 "
                       "승인할 것, offline 이면 케이블·무선 디버깅을 다시 연결할 것.")
    serial = ready[0].split()[0]
    log.info("문자 읽기 기기: %s", ready[0])
    return serial


def _parse_row(row: str) -> Message | None:
    """`address=..., date=..., body=...` 한 줄을 Message 로."""
    fields: dict[str, str] = {}
    # body 에 ', ' 가 들어갈 수 있어 앞의 두 칸만 잘라내고 나머지를 body 로 본다.
    match = re.match(r"address=(.*?), date=(\d+), body=(.*)$", row, re.S)
    if not match:
        return None
    fields["address"], raw_date, fields["body"] = match.groups()
    try:
        received = datetime.fromtimestamp(int(raw_date) / 1000)
    except (OverflowError, OSError, ValueError) as exc:
        log.warning("문자 수신시각을 읽지 못했다: %s", exc)
        return None
    return Message(fields["address"].strip(), received, fields["body"].strip())


def read_messages(limit: int = 10) -> list[Message]:
    """받은 문자를 **최신순**으로 읽는다. 파일로 저장하지 않는다."""
    result = _run(
        "shell", "content", "query",
        "--uri", SMS_URI,
        "--projection", "address:date:body",
        "--sort", '"date DESC"',
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()

    rows = [part.strip() for part in ROW_SPLIT_RE.split(output)[1:] if part.strip()]
    if not rows:
        if "Permission Denial" in output or "Permission Denial" in error:
            raise SmsError("권한이 없어 문자를 읽지 못했다 (READ_SMS).")
        raise SmsError(f"문자를 읽지 못했다. adb 응답: {output[:200] or error[:200] or '(없음)'}")

    messages = []
    for row in rows[:limit]:
        message = _parse_row(row)
        if message is not None:
            messages.append(message)
    return messages


def _find_code(since: datetime, keyword: str | None) -> tuple[str, object] | None:
    """한 번 조회한다. `("ok", 코드)` 또는 `("ambiguous", 후보들)` 또는 None."""
    try:
        messages = [m for m in read_messages(limit=10) if m.received >= since]
    except SmsError as exc:
        # 조회가 일시적으로 실패할 수 있다. 로그만 남기고 다음 폴링에서 다시 본다.
        log.warning("문자 조회 실패(재시도): %s", exc)
        return None

    senders = allowed_senders()
    if senders:
        # 번호 **완전일치**. 비어 있으면 발신자 조건을 걸지 않는다.
        messages = [m for m in messages if normalize_number(m.address) in senders]
    if keyword:
        messages = [m for m in messages if keyword in m.body]

    for message in messages:
        codes = message.codes()
        if len(codes) == 1:
            log.info("인증번호를 찾았다 (발신 %s, 수신 %s)",
                     message.address, message.received.strftime("%H:%M:%S"))
            return ("ok", codes[0])
        if len(codes) > 1:
            return ("ambiguous", codes)
    return None


def wait_for_code(
    since: datetime,
    timeout: float | None = None,
    keyword: str | None = None,
) -> str:
    """`since` **이후에 도착한** 문자에서 인증번호를 찾아 돌려준다.

    - `since` 이전 문자는 보지 않는다. 지난 인증번호를 다시 넣으면 안 된다.
    - 후보가 여러 개면 **추측하지 않고 실패**시킨다. 틀린 값을 넣으면 계정이 잠긴다.
    - 못 찾으면 timeout 후 실패로 중단한다 (사용자 확정: 사람 입력 폴백 없음).
    """
    timeout = SETTINGS.timeouts.sms_code if timeout is None else timeout
    log.info("인증 문자 대기 (최대 %.0f초, 기준시각 %s)",
             timeout, since.strftime("%H:%M:%S"))
    try:
        kind, value = wait_for(
            lambda: _find_code(since, keyword),
            "인증 문자 도착",
            timeout=timeout,
            interval=SMS_POLL,
        )
    except WaitTimeout as exc:
        raise SmsError(
            f"{timeout:.0f}초 안에 인증 문자가 오지 않았다. "
            "폰이 USB 로 연결돼 있는지, 문자가 실제로 도착했는지 확인할 것.") from exc

    if kind == "ambiguous":
        raise SmsError(
            f"문자에서 숫자 후보가 여러 개 나왔다: {value}. "
            "잘못 입력하면 계정이 잠기므로 추측하지 않고 중단한다.")
    return str(value)
