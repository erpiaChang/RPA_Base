r"""[조사 도구 — 읽기 전용] 이 PC 가 지금 얼마나 느린지 잰다.

대상 프로그램을 건드리지 않는다. **전원 상태와 우리 쪽 속도만** 본다.

    .venv\Scripts\python.exe -m tools.probe_speed
    .venv\Scripts\python.exe -m tools.probe_speed --json    다른 도구가 읽을 형태로

## 왜 필요한가

노트북을 **충전기에서 뽑으면** Windows 가 CPU 를 낮춘다. 그러면 UIA 탐색이
느려지고, 코드에 박아 둔 상한(예: 컨트롤 10초)을 그냥 넘겨 버린다.
그때 나오는 말은 "느리다" 가 아니라 **"컨트롤을 찾지 못했다"** 다
(2026-09-11 사용자 관찰: 주문매핑에서 엑셀업로드 셀을 못 찾았다).

같은 PC 에서 **충전기 연결 / 분리** 두 번 돌려 비교하면 배율이 나온다.

## 무엇을 재나

| 항목 | 뜻 |
| --- | --- |
| 전원 | 충전기 연결 여부, 배터리 % |
| CPU 클럭 | 현재 / 최대. 낮아져 있으면 **그만큼 느려진 것**이다 |
| CPU 벤치 | 순수 계산 고정량. 환경 영향을 가장 직접 받는다 |
| UIA 창 열거 | `Desktop().windows()` 1회 |
| UIA 탐색 | 창 하나에서 컨트롤을 훑는 데 걸리는 시간 |

UIA 탐색 시간이 중요하다. **코드의 모든 상한이 이 값의 배수여야** 한다.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
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

# 기준값. **개발 PC 에서 충전기를 연결하고 실측했다 (2026-09-11, 클럭 고정).**
# 추측값이 아니다. 다른 PC 가 이보다 느리면 그 비율만큼 상한을 늘려야 한다.
#
# ★ 배율은 **CPU 벤치로만** 계산한다. UIA 수치는 그때 열려 있는 창 수에 따라
#   크게 흔들려서 PC 간 비교 기준으로 쓸 수 없다. 참고로만 찍는다.
REFERENCE = {
    "cpu_bench": 0.016,       # 초 (실측)
    "uia_windows": 0.057,     # 초 (실측, 창 12개일 때 — 참고용)
    "uia_search": 0.009,      # 초 (실측 — 참고용)
}
BENCH_LOOPS = 400_000
UIA_ROUNDS = 3


class PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def power() -> dict:
    """전원 상태. 읽지 못하면 값이 None 이다."""
    status = PowerStatus()
    try:
        ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not ok:
        return {"error": "GetSystemPowerStatus 실패"}

    ac = {0: "배터리", 1: "충전기 연결", 255: "알 수 없음"}.get(
        status.ACLineStatus, str(status.ACLineStatus))
    percent = status.BatteryLifePercent
    return {
        "ac": ac,
        "on_battery": status.ACLineStatus == 0,
        "battery_percent": None if percent == 255 else percent,
    }


def cpu_clock() -> dict:
    """현재 / 최대 클럭(MHz). 못 읽으면 None.

    레지스트리의 `~MHz` 는 **최대값**이라 지금 낮아진 것을 알 수 없다.
    그래서 WMI 로 현재 클럭을 읽는다. 실패하면 조용히 넘어간다.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Processor | "
             "Select-Object -First 1 CurrentClockSpeed,MaxClockSpeed) | ConvertTo-Json"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}"}
    if out.returncode != 0 or not out.stdout.strip():
        return {"error": "클럭을 읽지 못했다"}
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"error": "클럭 출력 형식을 모른다"}
    current = data.get("CurrentClockSpeed")
    maximum = data.get("MaxClockSpeed")
    ratio = None
    if current and maximum:
        ratio = round(current / maximum, 2)
    return {"current_mhz": current, "max_mhz": maximum, "ratio": ratio}


def cpu_bench() -> float:
    """순수 계산 고정량. 환경이 나쁘면 이 값이 그대로 커진다."""
    started = time.perf_counter()
    total = 0
    for i in range(BENCH_LOOPS):
        total += i * i % 7
    return time.perf_counter() - started


def uia_bench() -> dict:
    """UIA 가 얼마나 느린지. **상한을 정할 때 기준이 되는 값이다.**"""
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")

    listing = []
    windows = []
    for _ in range(UIA_ROUNDS):
        started = time.perf_counter()
        windows = desktop.windows()
        listing.append(time.perf_counter() - started)

    searching = []
    target = windows[0] if windows else None
    for _ in range(UIA_ROUNDS):
        started = time.perf_counter()
        if target is not None:
            try:
                target.descendants(control_type="Button")
            except Exception:
                # 창이 사라졌을 수 있다. 측정만 하는 도구라 계속한다.
                pass
        searching.append(time.perf_counter() - started)

    return {
        "windows": round(statistics.median(listing), 3),
        "search": round(statistics.median(searching), 3),
        "window_count": len(windows),
    }


def suggest_scale(measured: dict) -> float:
    """기준 대비 몇 배 느린가.

    **CPU 벤치만 쓴다.** UIA 수치는 그때 열려 있는 창 수에 좌우돼 PC 간 비교가
    안 된다. 클럭 비율이 읽히면 그것도 함께 보고 **더 나쁜 쪽**을 따른다.
    1.0 아래로는 내리지 않는다 — 빠른 PC 라고 상한을 줄일 이유가 없다.
    """
    ratios = [1.0]
    if measured["cpu_bench"] and REFERENCE["cpu_bench"]:
        ratios.append(measured["cpu_bench"] / REFERENCE["cpu_bench"])
    clock_ratio = (measured.get("clock") or {}).get("ratio")
    if clock_ratio:
        ratios.append(1 / clock_ratio)      # 클럭이 절반이면 2배 느리다고 본다
    return round(max(ratios), 2)


def measure() -> dict:
    return {
        "power": power(),
        "clock": cpu_clock(),
        "cpu_bench": round(cpu_bench(), 3),
        "uia": uia_bench(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PC 속도 측정 (읽기 전용)")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력한다")
    args = parser.parse_args()

    result = measure()
    result["scale"] = suggest_scale(result)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    power_info = result["power"]
    clock = result["clock"]
    uia = result["uia"]

    print("### 전원")
    if "error" in power_info:
        print(f"    읽지 못했다: {power_info['error']}")
    else:
        percent = power_info["battery_percent"]
        print(f"    {power_info['ac']}"
              + (f" / 배터리 {percent}%" if percent is not None else ""))

    print("### CPU 클럭")
    if "error" in clock:
        print(f"    읽지 못했다: {clock['error']}")
    else:
        print(f"    현재 {clock['current_mhz']}MHz / 최대 {clock['max_mhz']}MHz"
              f"  (최대 대비 {clock['ratio']}배)")

    print("### 속도")
    print(f"    CPU 벤치      {result['cpu_bench']:.3f}초"
          f"  (기준 {REFERENCE['cpu_bench']}초)")
    print(f"    UIA 창 열거   {uia['windows']:.3f}초"
          f"  (참고 {REFERENCE['uia_windows']}초, 지금 창 {uia['window_count']}개)")
    print(f"    UIA 탐색      {uia['search']:.3f}초"
          f"  (참고 {REFERENCE['uia_search']}초)")
    print("    * UIA 두 줄은 열려 있는 창 수에 좌우된다. 배율 계산에는 쓰지 않는다.")

    print(f"\n### 권장 배율: {result['scale']}배")
    if result["scale"] >= 1.5:
        print("    상한을 이만큼 늘리지 않으면 '컨트롤을 찾지 못했다' 가 난다.")
    else:
        print("    기준 PC 와 비슷하다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
