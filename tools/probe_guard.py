r"""[확인 도구 공용] 확인 도구가 **실서버에 닿지 않게** 막는다 (2026-09-28).

09-23 에 개발 설정에 기기 키가 들어간 채 `probe_modules` 를 돌려, 대시보드에 가짜 실행 4건이
올라갔다. 그때는 도구마다 키를 비웠는데, 09-28 에 키 이름이 바뀌자(`server_device_key` →
`server_build_id`) 비우던 코드가 **조용히 아무것도 안 막는 상태**가 됐다.

그래서 이름을 여기 한 곳에만 둔다. 서버 설정 키가 바뀌면 `SERVER_KEYS` 만 고친다.

## 왜 창을 만드는 도구까지 막아야 하나

실행 창은 뜨는 순간 `telemetry.verify` 로 서버에 확인을 보내고, 서버는 **처음 보는 빌드를 그 PC 에
묶는다**. 확인 도구가 창을 만들면 그것만으로 빌드가 묶여 버려, 정작 사용자 PC 에서는 못 쓰게 된다.

    with probe_guard.offline():
        return _main()
"""
from __future__ import annotations

from contextlib import contextmanager

from config.settings import SETTINGS

# 하나라도 비면 `telemetry.from_settings` 가 보고기를 만들지 않고 `verify` 는 그냥 통과한다.
SERVER_KEYS = ("server_build_id", "server_url", "server_anon_key")


@contextmanager
def offline():
    """이 블록 안에서는 서버 설정이 비어 있다. 끝나면 원래 값으로 되돌린다."""
    keep = {key: getattr(SETTINGS, key, None) for key in SERVER_KEYS}
    for key in SERVER_KEYS:
        setattr(SETTINGS, key, "")
    try:
        yield
    finally:
        for key, value in keep.items():
            setattr(SETTINGS, key, value)
