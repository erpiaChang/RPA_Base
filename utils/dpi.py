r"""DPI 인식 설정.

배율(125%, 150%)이 걸린 PC에서 DPI 인식을 설정하지 않으면 Windows가 좌표를
가상화해서 컨트롤의 `rectangle()` 값과 실제 화면 위치가 어긋난다.
`click_input()`이 엉뚱한 곳을 누르게 된다.

**tkinter 창을 만들기 전에, 프로그램 시작 직후 한 번만 호출한다.**
"""
from __future__ import annotations

import ctypes

from utils.logger import get_logger

log = get_logger(__name__)

# SetProcessDpiAwareness 인자
PROCESS_PER_MONITOR_DPI_AWARE = 2

_APPLIED: str | None = None


def ensure_dpi_awareness() -> str:
    """DPI 인식을 설정하고 적용된 방식을 돌려준다. 두 번째 호출부터는 그대로 반환."""
    global _APPLIED
    if _APPLIED is not None:
        return _APPLIED

    # Windows 8.1+ : 모니터별 DPI 인식
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        _APPLIED = "per-monitor (SetProcessDpiAwareness)"
        log.info("DPI 인식: %s", _APPLIED)
        return _APPLIED
    except (AttributeError, OSError) as exc:
        # shcore가 없거나(구버전), 이미 설정된 경우 E_ACCESSDENIED가 난다
        log.debug("SetProcessDpiAwareness 실패: %s", exc)

    # 폴백: 시스템 DPI 인식
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            _APPLIED = "system (SetProcessDPIAware)"
        else:
            _APPLIED = "이미 설정됨 또는 실패"
    except (AttributeError, OSError) as exc:
        _APPLIED = f"설정 불가 ({type(exc).__name__})"
        log.warning("DPI 인식 설정 실패: %s. 배율 PC에서 좌표가 어긋날 수 있다.", exc)

    log.info("DPI 인식: %s", _APPLIED)
    return _APPLIED


def scale_info() -> dict:
    """현재 화면 크기와 DPI 배율. 로그에 남겨 환경 차이를 추적한다."""
    user32 = ctypes.windll.user32
    try:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
    except OSError:
        width = height = 0

    dpi = 96
    try:
        dpi = user32.GetDpiForSystem()
    except (AttributeError, OSError):
        pass  # Windows 10 1607 미만. 96으로 둔다.

    return {"width": width, "height": height, "dpi": dpi, "scale": round(dpi / 96, 2)}
