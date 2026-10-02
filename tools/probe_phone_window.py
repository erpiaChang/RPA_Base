r"""[조사 도구 — 읽기 전용] [휴대폰 연결] **창 상태**와 그 상태에서 목록이 읽히는지.

인증번호를 못 받는 원인을 **PC 쪽**(탭·창 상태)과 **폰 쪽**(동기화 지연)으로 가른다.

    -m tools.probe_phone_window              # 지금 상태 (읽기)
    -m tools.probe_phone_window --dump       # 창 안 컨트롤 훑기 (어느 탭인지)
    -m tools.probe_phone_window --min-test   # 최소화→읽기→복원 (창만 조작)
    -m tools.probe_phone_window --watch 180 --interval 15   # 동기화가 사는지 지켜본다
    -m tools.probe_phone_window --refresh    # [새로 고침] 한 번 → 당겨오는지

- `--min-test` 는 **창 하나만** 최소화했다 되돌린다. 목록 건수가 줄면 창 상태가 원인이다
  (2026-09-21 실측: 14 → 14 → 14. **창 상태는 원인이 아니었다**).
- `--watch` 는 새로고침 버튼 이름의 `마지막 업데이트 N초 전` 과 메시지 탭의
  `읽지 않은 항목 N개`, 최신 본문을 표본으로 뽑는다. **폰 화면을 꺼 둔 채로** 돌려
  숫자가 계속 커지면 동기화가 멈춘 것, 작게 되돌아가면 살아 있는 것이다.
- ERPia·실계정 저장·문자 발송에는 손대지 않는다. 문자를 보내지 않으므로 "새 문자가
  화면 꺼짐 상태에서 몇 초 만에 오나" 는 실제 인증을 돌려야 나온다.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from collect import phonelink as pl  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402
from utils.wait import WaitTimeout, wait_for  # noqa: E402

log = get_logger(__name__)

_user32 = ctypes.windll.user32
_SM_CMONITORS = 80
_SW_MINIMIZE = 6
_SW_SHOWNOACTIVATE = 4


def _title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
    return buf.value


def _describe(hwnd: int) -> None:
    iconic = bool(_user32.IsIconic(wintypes.HWND(hwnd)))
    visible = bool(_user32.IsWindowVisible(wintypes.HWND(hwnd)))
    rect = wintypes.RECT()
    _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    fg = _user32.GetForegroundWindow()
    log.info("  최소화=%s 보임=%s 위치=(%d,%d,%d,%d) 포그라운드=%s",
             iconic, visible, rect.left, rect.top, rect.right, rect.bottom,
             hwnd == fg)


def _read_count() -> int:
    try:
        convs = pl.read_conversations()
    except pl.PhoneLinkError as exc:
        log.warning("  read_conversations 실패: %s", exc)
        return -1
    senders = pl.allowed_senders()
    matched = [c for c in convs
               if not senders or pl.normalize_number(c.sender) in senders]
    log.info("  대화 목록 %d건 (발신번호 일치 %d건)", len(convs), len(matched))
    for c in matched[:3]:
        log.info("    발신=%s 본문=%r 후보=%s",
                 c.sender[:16], (c.body or "")[:36], c.codes())
    return len(convs)


def _sync_sample() -> tuple[str, str, str, str]:
    """(연결상태, 새로고침버튼이름, 메시지탭이름, 최신 대상본문). **읽기만 한다.**

    새로고침 버튼 이름에 `마지막 업데이트 날짜 N초 전` 이 들어 있다 — 이 숫자가
    계속 커지면 동기화가 멈춘 것, 작게 되돌아가면 방금 당겨온 것이다.
    메시지 탭 이름에는 `읽지 않은 항목 N개` 가 들어 있다.
    """
    window = pl._window()
    status = pl.connection_status() or "(못 읽음)"
    refresh = pl._by_auto_id(window, pl.REFRESH_BUTTON_AUTO_ID)
    rname = (refresh.element_info.name or "") if refresh is not None else "(버튼 없음)"
    tab = pl._by_auto_id(window, pl.MESSAGING_TAB_AUTO_ID, control_type="TabItem")
    tname = (tab.element_info.name or "") if tab is not None else "(탭 없음)"
    newest = ""
    try:
        senders = pl.allowed_senders()
        for conv in pl.read_conversations():
            if senders and pl.normalize_number(conv.sender) not in senders:
                continue
            newest = (conv.body or "")[:44]
            break
        else:
            newest = "(대상 발신번호 대화 없음)"
    except pl.PhoneLinkError as exc:
        newest = f"(읽기 실패: {exc})"
    return status, rname, tname, newest


def _arg(flag: str, default: float) -> float:
    """`--watch 180` 처럼 뒤에 붙은 숫자. 없으면 기본값."""
    if flag not in sys.argv:
        return default
    at = sys.argv.index(flag)
    if at + 1 < len(sys.argv):
        try:
            return float(sys.argv[at + 1])
        except ValueError:
            log.warning("%s 뒤의 값(%r)을 숫자로 읽지 못했다. 기본값 %.0f 을 쓴다.",
                        flag, sys.argv[at + 1], default)
    return default


def _watch(seconds: float, interval: float) -> None:
    """정해진 시간 동안 표본을 뽑아 **동기화가 살아 있는지** 본다."""
    log.info("▶ %.0f초 동안 %.0f초 간격으로 지켜본다", seconds, interval)
    deadline = time.monotonic() + seconds
    first: tuple[str, str, str, str] | None = None
    changed: set[str] = set()
    count = 0
    labels = ("연결상태", "마지막 업데이트", "안읽음(탭)", "최신본문")
    while time.monotonic() < deadline:
        sample = _sync_sample()
        count += 1
        log.info("  [%02d] 상태=%s | %s", count, sample[0], sample[1])
        log.info("       탭=%r", sample[2])
        log.info("       최신본문=%r", sample[3])
        if first is None:
            first = sample
        else:
            for i, label in enumerate(labels):
                if sample[i] != first[i]:
                    changed.add(label)
        if time.monotonic() >= deadline:
            break
        # 관찰 간격. "다음 동기화" 를 알려 주는 UIA 이벤트가 없어 조건 대기로
        # 바꿀 수 없다. 조사 전용이고 위 deadline 으로 상한이 걸려 있다.
        time.sleep(interval)
    log.info("표본 %d개. 처음과 달라진 항목: %s", count,
             ", ".join(sorted(changed)) or "(없음)")


def main() -> int:
    setup_logging()
    monitors = _user32.GetSystemMetrics(_SM_CMONITORS)
    log.info("모니터 수 = %d", monitors)
    fg = _user32.GetForegroundWindow()
    log.info("현재 포그라운드 창 = %r", _title(fg))

    if not pl.app_window_exists():
        if "--min-test" in sys.argv:
            log.info("앱 창이 없다. 시험을 위해 띄운다(RPA 가 하는 것과 같다).")
            pl.launch_app()
        if not pl.app_window_exists():
            log.error("[휴대폰 연결] 앱 창이 없다. 앱을 먼저 실행할 것.")
            return 1

    window = pl._window()
    hwnd = window.element_info.handle
    log.info("[휴대폰 연결] 창 상태(지금):")
    _describe(hwnd)
    log.info("연결 상태 = %r  (연결됨 판정=%s)",
             pl.connection_status() or "(못 읽음)", pl.is_connected())
    reason = pl.offline_reason()
    log.info("앱 안내 = %s", reason or "(없음)")
    base = _read_count()

    if base < 0 or "--dump" in sys.argv:
        # 목록이 없으면 **어느 탭에 있나**부터 봐야 한다. 탐색용 컨트롤을 훑는다.
        log.info("▶ 창 안의 컨트롤(탐색 후보)을 훑는다")
        shown = 0
        for ctrl in window.descendants():
            try:
                info = ctrl.element_info
                kind = ctrl.friendly_class_name()
                aid = info.automation_id or ""
                name = (info.name or "")[:44]
            except Exception:
                continue
            if not (aid or name):
                continue
            log.info("   %-14s auto_id=%-34s name=%r", kind, aid, name)
            shown += 1
            if shown >= 140:
                log.info("   ... (140개에서 끊는다)")
                break

    if "--refresh" in sys.argv:
        # RPA 가 재연결 폴백으로 쓰는 것과 같은 버튼이다. 눌러서 **당겨오는지** 본다.
        log.info("▶ --refresh: [새로 고침] 을 한 번 누르고 전후를 견준다")
        before = _sync_sample()
        log.info("  전: %s", before[1])
        log.info("  전: 최신본문=%r", before[3])
        if pl._click_refresh():
            try:
                wait_for(lambda: _sync_sample()[1] != before[1],
                         "마지막 업데이트 시각이 바뀜", timeout=20.0, interval=1.0)
                log.info("  ★ 눌렀더니 마지막 업데이트 표기가 바뀌었다")
            except WaitTimeout:
                log.warning("  20초 안에 마지막 업데이트 표기가 바뀌지 않았다")
        after = _sync_sample()
        log.info("  후: %s", after[1])
        log.info("  후: 최신본문=%r", after[3])
        log.info("  최신본문 변화=%s", "있다" if after[3] != before[3] else "없다")

    if "--watch" in sys.argv:
        _watch(_arg("--watch", 180.0), _arg("--interval", 15.0))

    if "--ready-test" in sys.argv:
        # RPA 의 사전 점검(check_ready)을 **짧은 재연결 상한으로** 불러 본다.
        # 오프라인이면 "받을 수 없다" 로 막아야 한다 (문자를 요청하기 전에).
        short = _arg("--ready-test", 5.0)
        log.info("▶ --ready-test: check_ready(reconnect_timeout=%.0f초)", short)
        try:
            title = pl.check_ready(reconnect_timeout=short)
            log.info("  준비됨으로 판정: %r", title)
        except pl.PhoneLinkError as exc:
            log.info("  ★ 막혔다(기대한 동작): %s", exc)

    if "--min-test" not in sys.argv:
        log.info("판정 힌트: 모니터 1개 + 브라우저가 앱을 덮으면 이 상태가 재현된다. "
                 "--min-test 로 최소화했을 때 목록이 비는지 직접 확인할 수 있다.")
        return 0

    # 목록이 아예 없으면 원인이 창 상태가 아니라 **연결**이다. RPA 가 하는 것과
    # 같이 [다시 시도] 를 한 번 눌러 보되, 조사라 25초만 본다(5분을 쓰지 않는다).
    if base < 0 and not pl.is_connected():
        log.info("▶ 목록이 없다. RPA 와 같이 [다시 시도] 를 눌러 25초만 지켜본다")
        got = pl.reconnect(timeout=25.0)
        log.info("재연결 결과=%s 상태=%r", got, pl.connection_status() or "(못 읽음)")
        base = _read_count()

    log.info("▶ --min-test: 창을 최소화하고 다시 읽는다 (창 하나만 조작)")
    _user32.ShowWindow(wintypes.HWND(hwnd), _SW_MINIMIZE)
    time.sleep(1.5)   # UWP 가 정지 상태로 들어갈 틈을 준다 (조사용, 고정 대기 허용)
    _describe(hwnd)
    minimized = _read_count()

    log.info("▶ 복원(활성화 없이)하고 다시 읽는다")
    _user32.ShowWindow(wintypes.HWND(hwnd), _SW_SHOWNOACTIVATE)
    time.sleep(1.5)
    _describe(hwnd)
    restored = _read_count()

    log.info("결과: 정상 %d건 → 최소화 %d건 → 복원 %d건", base, minimized, restored)
    if base > 0 and minimized <= 0:
        log.info("★ 확인됨: 최소화하면 목록이 빈다. 모니터 1개에서 앱이 덮이면 "
                 "인증번호를 못 읽는다. → 읽기 전에 창을 복원해야 한다.")
    elif base > 0 and 0 < minimized < base:
        log.info("★ 부분: 최소화하면 일부만 읽힌다(virtualization).")
    else:
        log.info("최소화해도 건수가 유지됐다. 이 PC/앱 버전에서는 창 상태가 원인이 아닐 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
