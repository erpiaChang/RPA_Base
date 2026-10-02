r"""[실행 도구 — **연결을 맺는다**] USB 없이 무선으로 문자를 읽을 수 있게 한다.

adb 무선 디버깅(Wireless debugging)을 쓴다. **읽는 코드는 USB 때와 완전히 같다.**
케이블만 없어진다.

폰 쪽 준비 (Android 11 이상)

    설정 → 개발자 옵션 → 무선 디버깅  켜기
      · [기기 페어링]  →  `IP:페어링포트` 와 6자리 코드가 뜬다   ← 페어링에 쓴다
      · 무선 디버깅 화면 상단  →  `IP:연결포트`                  ← 연결에 쓴다
    두 포트는 **다르다.**

PC 쪽

    1) 페어링 (처음 한 번)
       .venv\Scripts\python.exe -m tools.test_adb_wireless --pair 192.0.2.50:37199 --code 123456

    2) 연결 + 확인 (설정에 주소를 저장한다)
       .venv\Scripts\python.exe -m tools.test_adb_wireless --connect 192.0.2.50:41253

    3) 이후에는 케이블 없이 그냥 돌리면 된다
       .venv\Scripts\python.exe -m tools.test_collect --dry-run

제약

- PC 와 폰이 **같은 네트워크**에 있어야 한다. 폰이 밖에 있으면 이 방법으로는 안 된다.
- 폰을 재부팅하거나 Wi-Fi 를 바꾸면 **연결 포트가 바뀐다.** 페어링은 유지되지만
  `adb_wireless_address` 는 다시 넣어야 한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from config import settings as settings_mod  # noqa: E402
from collect import sms  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="adb 무선 디버깅 연결 (USB 없이 문자 읽기)")
    parser.add_argument("--pair", metavar="IP:포트",
                        help="폰의 [기기 페어링] 화면에 뜬 주소")
    parser.add_argument("--code", metavar="6자리",
                        help="--pair 와 함께. 폰에 뜬 페어링 코드")
    parser.add_argument("--discover", action="store_true",
                        help="mDNS 로 무선 디버깅 주소를 찾아 자동 연결한다")
    parser.add_argument("--connect", metavar="IP:포트",
                        help="무선 디버깅 화면 상단 주소. 성공하면 설정에 저장한다")
    args = parser.parse_args()

    if not args.pair and not args.connect and not args.discover:
        parser.print_help()
        return 2

    setup_logging()

    try:
        if args.pair:
            if not args.code:
                log.error("--pair 에는 --code 가 필요하다 (폰에 뜬 6자리).")
                return 2
            sms.pair(args.pair, args.code)

        if args.discover and not args.connect:
            candidates = sms.discover()
            if not candidates:
                log.error("무선 디버깅 기기를 찾지 못했다. 폰에서 [무선 디버깅]이 켜져 "
                          "있는지, PC 와 같은 네트워크인지 확인할 것.")
                return 1
            args.connect = candidates[0]
            log.info("찾은 주소로 연결한다: %s", args.connect)

        if args.connect:
            sms.connect(args.connect)
            sms.check_device()
            messages = sms.read_messages(limit=1)
            log.info("무선으로 문자 %d건을 읽었다. 최근 수신 %s",
                     len(messages),
                     messages[0].received.strftime("%m-%d %H:%M") if messages else "-")
            settings_mod.save_local(adb_wireless_address=args.connect)
            log.info("설정에 저장했다: adb_wireless_address=%s", args.connect)
            log.info("이제 USB 케이블 없이 수집 RPA 를 돌릴 수 있다.")
    except sms.SmsError as exc:
        log.error("%s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
