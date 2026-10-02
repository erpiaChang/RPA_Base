r"""[조작 도구] **느린 환경을 일부러 만든다.** CPU 를 부하로 묶는다.

대상 프로그램을 건드리지 않는다. 그래도 `test_` 인 이유는 **이 PC 전체가
느려지기 때문**이다. 돌고 있는 동안 다른 작업도 같이 느려진다.

    .venv\Scripts\python.exe -m tools.test_slow --seconds 300
    .venv\Scripts\python.exe -m tools.test_slow --seconds 300 --workers 4

## 왜 필요한가 (2026-09-17)

빠른 환경에서는 **실패 경로가 아예 발동하지 않아** 고친 것을 확인할 수 없는
자리가 있다. 대표가 엑셀업로드다 (`docs/archive/HANDOFF_20260917.md` 8-6).

    우리가 ScrollIntoView() 로 스크롤 → 프로그램이 다시 그리기 전에 위치를 읽음
    → 옛 좌표로 클릭 → 업로드가 안 됨

빠른 PC 에서는 다시 그리기가 클릭보다 먼저 끝나 **우연히** 맞는다. 그래서
`wait_rect_settled()` 가 막아 주는지를 볼 수 없었다. 여태 "배터리 + 절전
환경이 되면 확인한다" 로 미뤄 두고 있었다.

**전원 설정을 바꾸지 않고도 같은 조건을 만들 수 있다.** 느려지는 이유가
클럭이 아니라 **다시 그리기가 밀리는 것**이라, CPU 를 꽉 채우면 된다.
시스템 설정을 건드리지 않으니 되돌릴 것도 없다 — 끝나면 그냥 사라진다.

| 방법 | 되돌리기 | 관리자 권한 |
| --- | --- | --- |
| 전원 구성표 변경 (`powercfg`) | 값을 되돌려야 한다 | 필요할 수 있다 |
| **CPU 부하 (이 도구)** | **필요 없다. 끝나면 끝난다** | 필요 없다 |

## 쓰는 법

부하를 **뒤에서 돌려 놓고** 그 사이에 재려는 것을 돌린다.

1. 이 도구를 `--seconds` 로 넉넉히 주고 배경에서 띄운다
2. `tools.probe_speed` 로 **실제로 몇 배 느려졌는지 확인한다**
   (기준: 배터리+절전 실측이 1.9~3.9배. 그 대역에 들어와야 의미가 있다)
3. 재려는 것을 돌린다
4. 시간이 다 되면 부하는 스스로 끝난다

★ 몇 배 느려졌는지 **확인하지 않고 "느린 환경에서 돌렸다" 고 말하지 않는다.**
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue


def burn(deadline: float) -> None:
    """정해진 시각까지 계산만 한다. **잠들지 않는다** — 그게 목적이다."""
    value = 0.0
    while time.monotonic() < deadline:
        # 한 번에 조금씩 돌고 시각을 다시 본다. 상한을 넘겨 도는 일이 없게.
        for index in range(20_000):
            value += index ** 0.5
    # 계산 결과를 버리지 않는다. 버리면 최적화로 통째로 사라질 수 있다.
    if value < 0:
        print(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="느린 환경 만들기 (CPU 부하)")
    parser.add_argument("--seconds", type=float, required=True,
                        help="몇 초 동안 묶을지. 재려는 작업보다 넉넉히 준다")
    parser.add_argument("--workers", type=int, default=0,
                        help="부하 프로세스 수. 0 이면 CPU 코어 수")
    args = parser.parse_args()

    workers = args.workers or (os.cpu_count() or 4)
    deadline = time.monotonic() + args.seconds
    print(f"CPU {workers}개를 {args.seconds:.0f}초 동안 묶는다. "
          f"이 사이에 재려는 것을 돌린다.", flush=True)

    procs = [multiprocessing.Process(target=burn, args=(deadline,), daemon=True)
             for _ in range(workers)]
    for proc in procs:
        proc.start()
    try:
        for proc in procs:
            proc.join()
    except KeyboardInterrupt:
        print("중단됐다. 부하를 멈춘다.", flush=True)
        for proc in procs:
            proc.terminate()
        return 1
    print("부하를 멈췄다. 환경이 원래대로 돌아왔다.", flush=True)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
