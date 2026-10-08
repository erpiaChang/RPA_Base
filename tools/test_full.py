r"""[실행 도구 — **실제로 조작한다**] 통합 흐름을 콘솔에서 끝까지 돌린다.

    .venv\Scripts\python.exe -m tools.test_full --dry-run
    .venv\Scripts\python.exe -m tools.test_full
    .venv\Scripts\python.exe -m tools.test_full --dry-run --modules orders,logistics
    set RPA_CUSTOMER=<업체 id> & .venv\Scripts\python.exe -m tools.test_full --dry-run --modules cx_…  (업체 빌드, 10-08)

메일에서 엑셀을 받아 저장하고, 이어서 프로그램을 실행해 업로드·매출처리·
물류대기·물류관리까지 간다. 입력값은 `config/settings.local.json` 에서 읽는다.
`--modules` 로 기능을 고르면 그것만 돈다(`orchestrator/modules.py` 의 id).
주지 않으면 설정 `run_modules` 를 쓴다.

**실데이터가 움직인다.** 먼저 `--dry-run` 으로 확인할 것.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from config.settings import SETTINGS  # noqa: E402
from orchestrator import erpia_flow, friendly, full_flow, history, modules, report, telemetry  # noqa: E402
from orchestrator.common import Hooks, Result  # noqa: E402
from utils.dpi import ensure_dpi_awareness  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="통합 흐름 실행")
    parser.add_argument("--dry-run", action="store_true",
                        help="메일을 열지 않고, 클릭 대상만 확인한다")
    parser.add_argument("--modules", default="",
                        help="고를 기능 (쉼표로): mail,orders,logistics_wait,logistics (업체 빌드면 cx_… 도)")
    parser.add_argument("--company", default="",
                        help="업체코드만 바꿔 로그인 (시험 계정 — 값은 명령줄에만, 설정에 쓰지 않는다)")
    args = parser.parse_args()

    log_path = setup_logging()
    log.info("로그: %s", log_path)
    ensure_dpi_awareness()

    SETTINGS.require("target_exe", "login_company_code", "login_user_id",
                     "login_password", "delivery_company", "delivery_box")

    options = erpia_flow.Options(
        exe=SETTINGS.target_exe,
        company=args.company.strip() or SETTINGS.login_company_code,
        user_id=SETTINGS.login_user_id,
        password=SETTINGS.login_password,
        sources=list(SETTINGS.collect_sources or []),          # 기본값 없음 (10-02)
        courier=SETTINGS.delivery_company,
        box=SETTINGS.delivery_box,
        modules=([m.strip() for m in args.modules.split(",") if m.strip()]
                 if args.modules else getattr(SETTINGS, "run_modules", None)),
    )
    log.info("고른 기능: %s", options.modules or "(전부)")

    started = datetime.now()
    hooks = Hooks(explain=friendly.explain)
    # 서버 보고 (09-22). 창과 달리 `on_step` 이 없어 보고기가 그 자리를 처음 채운다. dry-run 은 보내지 않는다.
    reporter = telemetry.from_settings(SETTINGS)
    if reporter is not None:
        reporter.start(hooks, trigger="콘솔 실행", dry_run=args.dry_run,
                       modules=[m.name for m in modules.normalize(options.modules)])
    outcome = "실패"
    try:
        result = full_flow.run(options, hooks=hooks, dry_run=args.dry_run)
        outcome = result.summary
    except Exception as exc:
        outcome = f"실패 — {hooks.failure_text(exc)}"
        log.error("통합 실행 실패: %s: %s", type(exc).__name__, exc)
        return 1
    finally:
        # 창(`erpia_app._run_worker`)과 같은 리포트·이력 — 콘솔 실행에서도 남긴다 (09-22)
        step_rows = hooks.report()
        path = report.write(Result(
            summary=outcome, steps=step_rows,
            details={"attention": list(hooks.attention), "trigger": "콘솔 실행"}))
        log.info("리포트: %s", path)
        entry = history.record(summary=outcome, step_rows=step_rows, attention=list(hooks.attention),
                               trigger="콘솔 실행", report=path,
                               modules=[m.name for m in modules.normalize(options.modules)],
                               started_at=started.timestamp())
        if reporter is not None:
            reporter.finish(entry)

    elapsed = (datetime.now() - started).total_seconds()
    log.info("통합 실행 완료 (%.1f초)", elapsed)
    log.info("%s", result.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
