r"""[조사 도구 - 읽기 전용] 실행 리포트 HTML 이 제대로 만들어지는지 확인한다.

**대상 프로그램을 건드리지 않는다.** 가짜 결과로 만들어 본다.

    .venv\Scripts\python.exe -m tools.probe_report            확인만 한다
    .venv\Scripts\python.exe -m tools.probe_report --open     표본을 만들어 연다

## 왜 이 도구가 있나 (2026-09-15)

리포트는 **실행이 끝난 뒤에만** 만들어진다. 그래서 잘못 만들어도 다음 실행을
끝까지 돌려야 알 수 있고, 실행 한 번이 몇 분이다. 여기서 가짜 결과로 미리 본다.

확인하는 것

1. 단계·상세 표가 **빠짐없이** 들어가는가
2. `<`, `&`, 따옴표가 든 값이 **HTML 을 깨뜨리지 않는가** (escape)
3. 실패·중단으로 끝난 실행에도 만들어지는가
4. 상세가 없는 단계에서도 터지지 않는가
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

from orchestrator import report, steps  # noqa: E402
from orchestrator.common import Result  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def sample() -> Result:
    """실제 실행이 남기는 것과 **같은 모양**의 가짜 결과."""
    import time

    # 2026-09-16 09:17:00 을 고정으로 쓴다. 시각이 그대로 찍히는지
    # 보려면 매번 달라지는 값이면 안 된다.
    when = time.mktime((2026, 9, 16, 9, 17, 0, 0, 0, -1))
    return Result(
        summary="업로드 2/3건 (실패: 사이트B) / 매출처리 완료 — 미매출 주문수 4건 → 2건",
        steps=[
            {"id": "launch", "name": "ERPia 실행", "state": steps.DONE,
             "detail": "pid=1234", "progress": "", "error": "", "elapsed": 2.0,
             "result": None, "columns": (), "rows": [],
             "started_at": when - 2, "finished_at": when},
            {"id": "excel", "name": "엑셀수집", "state": steps.DONE,
             "detail": "업로드 2/3건", "progress": "", "error": "", "elapsed": 48.4,
             "result": None,
             "columns": ("사이트", "결과", "시도", "파일", "오류"),
             "rows": [("사이트C", "성공", "1", "사이트C_1.xlsx", ""),
                      # ★ HTML 을 깨뜨릴 수 있는 값을 일부러 넣는다.
                      ("사이트B", "실패", "2", "사이트B_2.xlsx",
                       '파일 선택 창을 찾지 못했다 <b>&"강조"</b>'),
                      ("사이트A", "성공", "1", "사이트A_3.xlsx", "")]},
            {"id": "sales", "name": "매출처리", "state": steps.SKIPPED,
             "detail": "수집된 주문 0건", "progress": "", "error": "", "elapsed": 5.9,
             "result": None, "columns": ("항목", "값"),
             "rows": [("미매출 주문수 (처리 전)", "-")]},
            {"id": "logistics_wait", "name": "물류대기", "state": steps.FAILED,
             "detail": "", "progress": "상품 3/13", "elapsed": 26.6,
             "error": "ScreenError: 조회 버튼을 찾지 못했다", "result": None,
             "columns": ("상품코드", "부족수량", "처리", "체크한 행", "이미 보류"),
             "rows": [("8800000000047", "790,422", "보류함", "12", "0")]},
            {"id": "logistics", "name": "물류관리", "state": steps.PENDING,
             "detail": "", "progress": "", "error": "", "elapsed": 0.0,
             "result": None, "columns": (), "rows": []},
        ])


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="실행 리포트 확인 (읽기 전용)")
    parser.add_argument("--open", action="store_true",
                        help="표본 리포트를 만들어 브라우저로 연다")
    args = parser.parse_args()

    setup_logging()
    results: list[bool] = []
    html = report.build(sample())

    log.info("▶ 리포트 내용")
    results.append(check("단계 이름이 모두 들어간다",
                         all(name in html for name in
                             ("ERPia 실행", "엑셀수집", "매출처리",
                              "물류대기", "물류관리"))))
    results.append(check("상세 표의 값이 들어간다",
                         "8800000000047" in html and "사이트C_1.xlsx" in html))
    results.append(check("요약이 들어간다", "미매출 주문수 4건" in html))
    # 09-21: 제목이 "멈춘 곳" 으로 바뀌었다 (사람 말 문구 + 접힌 기술 정보).
    # 09-22: 알림을 닫고 넘어간 단계도 여기 나온다 → "멈추거나 끝나지 않은 곳".
    results.append(check("실패한 단계를 따로 보여 준다",
                         "멈추거나 끝나지 않은 곳" in html and "조회 버튼을 찾지 못했다" in html))
    results.append(check("상태 이름을 한국어로 보여 준다",
                         "건너뜀" in html and "실행 전" in html))
    results.append(check("끝난 시각을 날짜까지 보여 준다",
                         "2026-09-16 09:17:00" in html and "끝난 시각" in html))
    results.append(check("★ 시각이 없는 단계는 빈 칸으로 둔다 (지어내지 않는다)",
                         html.count("2026-09-16 09:17:00") == 1,
                         "시각을 넣은 단계는 하나뿐이다"))

    log.info("▶ 깨뜨리지 않는가")
    # ★ 원본에 있던 `<b>` 가 그대로 남아 있으면 HTML 이 섞여 들어간 것이다.
    results.append(check("값에 든 태그를 escape 한다",
                         "&lt;b&gt;" in html and '파일 선택 창을 찾지 못했다 <b>' not in html))
    results.append(check("따옴표와 & 도 escape 한다", "&amp;" in html))
    results.append(check("상세가 없는 단계에서도 터지지 않는다",
                         html.count("<section class='detail'>") == 3,
                         "상세를 담은 단계만 절이 생긴다"))

    log.info("▶ 빈 결과 / 실패한 실행")
    empty = report.build(Result(summary="", steps=[]))
    results.append(check("단계가 하나도 없어도 만들어진다",
                         "<h1>" in empty and "상세를 담은 단계가 없습니다" in empty))
    broken = report.build(Result(summary="실패 — 로그인 창을 찾지 못했다", steps=[]))
    results.append(check("실패로 끝난 실행도 만들어진다",
                         "로그인 창을 찾지 못했다" in broken))

    if args.open:
        path = report.write(sample())
        if path:
            import os

            log.info("표본 리포트: %s", path)
            os.startfile(str(path))   # noqa: S606 (Windows 전용)
        else:
            results.append(check("표본을 파일로 쓴다", False))

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
