r"""[실행 도구 — **실제로 조작한다**] 수집 RPA 를 콘솔에서 돌린다.

    .venv\Scripts\python.exe -m tools.test_collect --check     (설정 조합·준비 상태만. 로그인 안 함)
    .venv\Scripts\python.exe -m tools.test_collect --dry-run   (읽기만. 권장 순서)
    .venv\Scripts\python.exe -m tools.test_collect --sms-only  (문자 읽기만 확인)
    .venv\Scripts\python.exe -m tools.test_collect             (실제 다운로드)

`--dry-run` 은 **메일을 열지 않는다.** 대상만 골라 보고한다.
메일을 열면 읽음 처리되므로, 실제 실행 전에 먼저 이걸로 대상을 확인한다.

실계정으로 실제 메일함에 접속한다. 기본 실행은 메일을 **열고**(읽음 처리)
첨부를 내려받는다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from collect import sms  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def _check() -> int:
    """설정 조합과 준비 상태만 점검한다. 로그인하지 않는다."""
    from collect import auth_code

    try:
        auth_code.preflight()
    except auth_code.AuthCodeError as exc:
        log.error("%s", exc)
        return 1
    log.info("사전 점검 통과. 수집을 실행할 수 있다.")
    return 0


def _sms_only() -> int:
    """설정된 경로로 문자가 읽히는지 확인한다. 인증을 요청하지는 않는다."""
    from collect import auth_code

    try:
        source = auth_code.resolve()
    except auth_code.AuthCodeError as exc:
        log.error("%s", exc)
        return 1

    if source == auth_code.SOURCE_PHONELINK:
        from collect import phonelink

        phonelink.check_ready()
        for item in phonelink.read_conversations()[:5]:
            log.info("  %s  읽지않음=%s  숫자후보=%s",
                     item.sender, item.unread, item.codes())
        return 0

    sms.check_device()
    for message in sms.read_messages(limit=5):
        log.info("  %s  %s  숫자후보=%s",
                 message.received.strftime("%m-%d %H:%M"), message.address,
                 message.codes())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="수집 RPA 실행")
    parser.add_argument("--dry-run", action="store_true",
                        help="메일을 열지 않고 대상만 고른다")
    parser.add_argument("--check", action="store_true",
                        help="설정 조합과 준비 상태만 점검한다 (로그인하지 않는다)")
    parser.add_argument("--sms-only", action="store_true",
                        help="문자 읽기만 확인하고 끝낸다")
    args = parser.parse_args()

    log_path = setup_logging()
    log.info("로그: %s", log_path)
    started = datetime.now()

    if args.check:
        return _check()

    if args.sms_only:
        return _sms_only()

    # ★ **제품 흐름을 그대로 부른다.** 예전에는 여기에 흐름을 따로 적어 둬서
    #   사전점검(`auth_code.preflight`)과 실패 시 스크린샷(`step()`)이 빠졌고,
    #   집계도 달라서 정상 실행이 exit 1 을 냈다 (2026-09-10 감사).
    from orchestrator import collect_flow

    try:
        result = collect_flow.run(dry_run=args.dry_run)
    except Exception:
        log.exception("수집 실패")
        return 1

    elapsed: timedelta = datetime.now() - started
    log.info("수집 종료 (%.1f초) — %s", elapsed.total_seconds(), result.summary)
    # ★ 이번 실행의 실패만 본다. `skipped`(엑셀 첨부가 아님)는 실패가 아니다.
    return 0 if result.details.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
