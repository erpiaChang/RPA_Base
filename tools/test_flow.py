r"""[개발 전용 / 조작] ERPia 전체 흐름을 콘솔에서 돌리고 구간별 시간을 잰다.

GUI를 거치지 않고 로그인~물류관리 저장까지 한 번에 실행한다.
속도 회귀를 확인하거나, 특정 구간만 눈으로 보며 고칠 때 쓴다.

    .venv\Scripts\python.exe -m tools.test_flow
    .venv\Scripts\python.exe -m tools.test_flow --sources excel --box 박스A
    .venv\Scripts\python.exe -m tools.test_flow --dry-run
    .venv\Scripts\python.exe -m tools.test_flow --from logistics_wait --yes

`--from` 은 **구간 실행**이다. 앞 단계를 하지 않으므로 **이미 로그인된 인스턴스에
붙는다** (없으면 막힌다). 되돌릴 수 없는 구간은 확인을 받는데, 콘솔에는 물어볼
창이 없어서 `--yes` 를 주지 않으면 시작하지 않는다.

**실제 데이터를 저장한다.** `--dry-run` 은 물류대기/물류관리의 저장을 누르지 않는다.
값을 주지 않으면 `config/settings.local.json` 을 그대로 쓴다.
빌드에는 포함되지 않는다(`build_*.spec` 의 `DEV_ONLY`).

흐름은 **제품 코드(`orchestrator/erpia_flow.run`)를 그대로 부른다.**
여기에 흐름을 따로 적어 두면 제품이 바뀔 때 같이 낡아, 도구로 확인한 것이
실제로 도는 것과 달라진다 (2026-09-10 에 실제로 그렇게 됐다).
"""
from __future__ import annotations

import argparse
import sys
import time
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except OSError as exc:
            # 파이프로 넘길 때 실패할 수 있다. 출력 인코딩만 영향받으므로 계속 간다.
            print(f"[경고] 출력 인코딩 설정 실패: {exc}", file=sys.stderr)

from config.settings import SETTINGS  # noqa: E402
from utils import cancel  # noqa: E402
from utils.dpi import ensure_dpi_awareness  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402
from orchestrator import erpia_flow, steps_erpia  # noqa: E402
from orchestrator.common import Hooks  # noqa: E402

log = get_logger("test_flow")


class Stopwatch:
    """구간별 소요 시간. 끝에 표로 찍는다."""

    def __init__(self, monitor=None) -> None:
        self.marks: list[tuple[str, float]] = [("시작", time.monotonic())]
        self.monitor = monitor

    def mark(self, name: str) -> None:
        self.marks.append((name, time.monotonic()))
        log.info("[구간] %s", name)
        if self.monitor is not None:
            self.monitor.mark(name)

    def report(self) -> None:
        """구간 표. **소요 시간을 그 구간의 이름에 붙인다.**

        ★ 2026-09-10 정정. 예전에는 `다음 마크 시간 - 이전 마크 시간` 을
          **다음 마크의 이름**으로 찍었다. 그래서 `물류대기 5.9초 /
          물류관리 267.7초` 처럼 실제와 뒤바뀐 표가 나왔다 (267.7초는 물류대기다).
        """
        print("=" * 64)
        for (name, at), (_, nxt) in zip(self.marks, self.marks[1:]):
            print(f"  {nxt - at:7.1f}초  {name}")
        last_name, last_at = self.marks[-1]
        print(f"  {'':7}   {last_name} (마지막 마크)")
        print(f"  {last_at - self.marks[0][1]:7.1f}초  합계")


def main() -> int:
    parser = argparse.ArgumentParser(description="전체 흐름 실행 (개발용)")
    parser.add_argument("--sources", nargs="+", choices=["site", "excel"],
                        default=None,
                        help="주문수집 방식. 최소 1개 최대 2개. 기본은 설정값")
    parser.add_argument("--courier", default=None, help="택배사 (완전 일치)")
    parser.add_argument("--box", default=None, help="박스 (완전 일치)")
    parser.add_argument("--dry-run", action="store_true", help="저장을 누르지 않는다")
    parser.add_argument("--from", dest="start_step",
                        choices=[item.id for item in steps_erpia.ALL],
                        help="구간 실행 — 이 단계부터 한다 (이미 떠 있는 "
                             "인스턴스에 붙는다)")
    parser.add_argument("--yes", action="store_true",
                        help="구간 실행 확인에 '예' 로 답한다. **되돌릴 수 없는 "
                             "단계도 그대로 실행한다**")
    parser.add_argument("--trace", action="store_true",
                        help="컨트롤 탐색을 집계한다 (조건별 횟수·시간·캐시 후보)")
    parser.add_argument("--resources", action="store_true",
                        help="CPU/메모리/핸들을 샘플링한다")
    args = parser.parse_args()

    setup_logging()
    ensure_dpi_awareness()

    if not SETTINGS.target_exe:
        log.error("config/settings.local.json 에 target_exe 가 없다.")
        return 1

    sources = list(args.sources or SETTINGS.collect_sources or [])     # 기본값 없음 (10-02) — 비면 흐름이 막는다
    courier = (args.courier or SETTINGS.delivery_company or "").strip()
    box = (args.box or SETTINGS.delivery_box or "").strip()
    log.info("설정: 수집=%s / 택배사=%r / 박스=%r / dry_run=%s",
             sources, courier, box, args.dry_run)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    stack = ExitStack()
    monitor = trace = None
    if args.resources:
        from tools.probe_resources import ResourceMonitor
        monitor = stack.enter_context(ResourceMonitor())
    if args.trace:
        from tools.probe_search_trace import SearchTrace
        trace = stack.enter_context(SearchTrace())

    # 흐름이 중간에 실패해도 계측 결과는 낸다. 실패한 실행이야말로 보고 싶은 것이다.
    code = 1
    try:
        with stack:
            code = _flow(args, sources, courier, box, Stopwatch(monitor))
    except cancel.Cancelled as exc:
        # 실패가 아니다. traceback 을 뿌리지 않고 왜 멈췄는지만 남긴다.
        # `Cancelled` 는 BaseException 이라 아래 `except Exception` 에 걸리지 않는다.
        log.warning("멈췄다 — %s", exc)
        code = 2
    except Exception:
        log.exception("흐름이 실패했다. 계측 결과만 남기고 예외를 다시 올린다.")
        raise
    finally:
        _measure_report(monitor, trace, stamp)
    return code


def _measure_report(monitor, trace, stamp: str) -> None:
    if monitor is not None:
        monitor.report()
        print(f"자원 CSV: {monitor.save_csv(f'logs/res_{stamp}.csv')}")
    if trace is not None:
        trace.report()
        print(f"탐색 CSV: {trace.save_csv(f'logs/trace_{stamp}.csv')}")


def _console_confirm(question: str) -> bool:
    """`--yes` 를 준 실행에서 확인을 대신 받는다. 무엇에 동의한 것인지 남긴다."""
    for line in question.splitlines():
        log.warning("  | %s", line)
    log.warning("--yes 가 주어졌다. 위 내용에 동의한 것으로 보고 진행한다.")
    return True


def _flow(args, sources: list[str], courier: str, box: str,
          watch: "Stopwatch") -> int:
    """제품 흐름을 그대로 부른다. 구간 표시는 흐름이 알려 주는 상태 문구로 찍는다."""
    options = erpia_flow.Options(
        exe=SETTINGS.target_exe,
        company=SETTINGS.login_company_code,
        user_id=SETTINGS.login_user_id,
        password=SETTINGS.login_password,
        sources=sources,
        excel_dir=SETTINGS.excel_dir or "",
        courier=courier,
        box=box,
        dry_run=args.dry_run,
        start_step=args.start_step or "",
    )
    result = erpia_flow.run(options, hooks=Hooks(
        status=watch.mark,
        on_target=lambda target: watch.mark(f"프로그램 실행 (pid {target.pid})"),
        on_login=lambda title: watch.mark(f"로그인 ({title})"),
        # 콘솔에는 물어볼 창이 없다. `--yes` 를 주지 않으면 **시작하지 않는다.**
        # 되돌릴 수 없는 단계를 "아무도 안 막았으니" 로 해석하면 안 된다.
        confirm=(_console_confirm if args.yes else None),
    ))
    watch.mark("완료")
    watch.report()
    print("=" * 64)
    print(f"  {result.summary}")
    for key, value in result.details.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
