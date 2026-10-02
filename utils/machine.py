r"""이 PC 를 구별하는 값 — 서버 확인·PC 바인딩(`orchestrator/telemetry.verify`)이 쓴다 (09-28).

Windows 가 설치될 때 만드는 `MachineGuid` 다. 관리자 권한 없이 읽히고 재부팅·업데이트에도 안 바뀐다.
서버에는 sha256 만 남는다. 못 읽으면 빈 문자열 — 그러면 등록하지 않는다 (엉뚱한 값으로 묶지 않는다).
"""
from __future__ import annotations

from utils.logger import get_logger

log = get_logger(__name__)

_KEY = r"SOFTWARE\Microsoft\Cryptography"


def machine_id() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value = str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
    except OSError as exc:
        log.warning("MachineGuid 를 읽지 못했다: %s", exc)
        return ""
    return value
