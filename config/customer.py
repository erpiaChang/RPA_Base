r"""업체 전용 RPA 의 틀 (`docs/CUSTOMERS.md`). **업체 이름·화면 값은 여기 두지 않는다** — 업체 패키지는 git 밖 `customers/<id>/`.

빌드가 업체 하나를 고르면 그 id 를 설정 `customer` 로 굽고 잠근다(`config/settings.LOCKED_KEYS`). 그 업체 패키지만
번들에 들어간다(`build_run.spec`). 개발 폴더는 `settings.local.json` 의 `customer` 로 고른다. 비면 원본 그대로다.

업체 패키지 `customers/<id>/__init__.py` 는 `PROFILE` 하나를 둔다. **가볍게** 둔다 — `config.settings` 가 설정을 읽는
도중에 불러 칸 정의를 얻는다. 거기서 `config.settings`·`utils.ui`·`orchestrator` 를 맨 위에서 import 하면 순환이 난다
(기능·화면 코드는 `modules_factory` 와 함수 안에서 부른다).

원본의 끼움 자리 (업체가 없을 때의 동작):

| 자리 | 누가 부르나 | 업체가 없으면 |
| --- | --- | --- |
| `Profile.fields` | `config/settings.py`(DEFAULTS 에 더함) · `gui/run_app.py`([설정] 업체 묶음) · `orchestrator/remote.py`(웹) | 칸 없음 |
| `Profile.base` | `orchestrator/modules.py` — 원본 기능 중 이 빌드가 쓰는 것 (None = 전부) | 전부 |
| `Profile.modules_factory()` | `orchestrator/modules.py` — 업체 기능(`Module`, id 는 `cx_`). 원본 기능 뒤에 돈다 | 없음 |
| `Profile.rehearse(area)` | `orchestrator/full_flow.py` — 그 영역을 누르지 않고 확인만 할지 (`REHEARSE_AREAS`) | 아니오 |
| `Profile.hold` | `orchestrator/full_flow.py` → `logistics_wait.run(hold=)` — 재고 부족 배송보류를 거나 | 건다 |
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PREFIX = "cx_"          # 업체 전용 설정 키·기능 id 의 머리 — 원본과 섞이지 않고 서버·웹이 이것으로 가린다
KINDS = ("choice", "list", "text", "bool")
SETTING_KEY = "customer"
PACKAGE = "customers"
# 원본 영역 — `rehearse` 가 받는 이름. 업체 기능은 제 id 로 묻는다
REHEARSE_AREAS = ("collect", "sales", "logistics_wait", "logistics")


@dataclass(frozen=True)
class Field:
    key: str                        # cx_ 로 시작
    label: str                      # 화면·웹에 보이는 이름
    kind: str                       # KINDS
    default: object = None          # 안전한 쪽 (꺼짐·시험 실행)
    choices: tuple[str, ...] = ()   # choice 만
    help: str = ""


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    fields: tuple[Field, ...] = ()
    base: tuple[str, ...] | None = None
    modules_factory: Callable[[], tuple] | None = None
    rehearse: Callable[[str], bool] | None = None
    hold: bool = True


def raw_id(*paths: Path) -> str:
    """설정 파일들에서 `customer` 를 먼저 읽는다 — 칸 정의(DEFAULTS)를 정하기 전이라 `Settings` 를 못 쓴다.
    앞 파일(구운 값)에 있으면 그것이 이긴다 (`LOCKED_KEYS` 와 같은 규칙). 못 읽는 파일은 건너뛴다 — 오류는 본 읽기가 낸다."""
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig")).get(SETTING_KEY)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


ENV_KEY = "RPA_CUSTOMER"


def available() -> list[str]:
    """개발 폴더에 있는 업체 id (`customers/<id>/__init__.py`) — 빌드 프로그램이 고를 목록. 없으면 빈 목록."""
    root = Path(__file__).resolve().parent.parent / PACKAGE
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "__init__.py").is_file() and not p.name.startswith(("_", ".")))


def dev_override(frozen: bool) -> str:
    """개발 폴더 전용 — 환경변수 `RPA_CUSTOMER` 로 업체를 고른다 (시험 도구·확인 도구가 설정 파일을 고치지 않게).
    빌드본은 보지 않는다 — 업체는 구운 값으로만 정한다."""
    import os
    return "" if frozen else os.environ.get(ENV_KEY, "").strip()


def load(customer_id: str | None) -> Profile | None:
    """그 업체의 Profile. id 가 비면 None (원본 그대로). 없는 업체면 ImportError 그대로 — 잘못 구운 빌드를 원본으로
    돌리면 업체가 원한 '시험 실행' 이 빠진 채 실데이터가 바뀐다."""
    if not customer_id:
        return None
    if not customer_id.replace("_", "").isalnum() or not customer_id.isascii():
        raise ValueError(f"업체 id 꼴이 아니다: {customer_id!r}")
    profile = importlib.import_module(f"{PACKAGE}.{customer_id}").PROFILE
    bad = [f.key for f in profile.fields if not f.key.startswith(PREFIX) or f.kind not in KINDS]
    if bad:
        raise ValueError(f"업체 칸 정의가 틀렸다: {bad}")
    return profile
