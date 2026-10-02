r"""다운로드 폴더 규칙.

사용자 확정(2026-09-08):

    <기준경로>\YYYY-MM-DD\
        2026-09-08_001_사이트A\   주문내역.xlsx
        2026-09-08_002_사이트B\     주문내역.xlsx

- **차수는 날짜별로 001 부터 다시 시작한다.**
- 같은 사이트 메일이 하루에 두 번 오면 차수만 다른 두 폴더가 생긴다.
- 기준경로는 코드에 쓰지 않는다. `config/settings.local.json` 의 `download_base_dir`.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from config.settings import SETTINGS
from utils.logger import get_logger

log = get_logger(__name__)

# 2026-09-08_001_사이트A
FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{3})_(.+)$")
# 폴더명에 쓸 수 없는 문자. 사이트명이 그대로 들어가므로 걸러 둔다.
UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def base_dir() -> Path:
    """다운로드 기준경로. 설정이 비어 있으면 즉시 중단한다."""
    SETTINGS.require("download_base_dir")
    return Path(SETTINGS.download_base_dir)


def date_dir(day: date | None = None, create: bool = True) -> Path:
    """`<기준경로>\\YYYY-MM-DD` 폴더."""
    day = day or date.today()
    path = base_dir() / day.strftime("%Y-%m-%d")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def next_sequence(day: date | None = None) -> int:
    """그 날짜 폴더 안에서 다음 차수. 폴더가 없으면 1."""
    day = day or date.today()
    folder = date_dir(day, create=False)
    if not folder.exists():
        return 1
    used = []
    for child in folder.iterdir():
        if not child.is_dir():
            continue
        matched = FOLDER_RE.match(child.name)
        if matched:
            used.append(int(matched.group(2)))
    return max(used) + 1 if used else 1


def safe_name(site: str) -> str:
    """사이트명을 폴더명으로 쓸 수 있게 다듬는다. 이름 자체는 바꾸지 않는다."""
    cleaned = UNSAFE_RE.sub("_", site).strip()
    if not cleaned:
        raise ValueError("사이트명이 비어 있다.")
    return cleaned


def make_item_dir(site: str, day: date | None = None) -> tuple[Path, int]:
    """`YYYY-MM-DD_차수_사이트명` 폴더를 만들고 (경로, 차수) 를 돌려준다."""
    day = day or date.today()
    sequence = next_sequence(day)
    name = f"{day.strftime('%Y-%m-%d')}_{sequence:03d}_{safe_name(site)}"
    path = date_dir(day) / name
    path.mkdir(parents=True, exist_ok=False)   # 이미 있으면 차수 계산이 틀린 것이다
    log.info("저장 폴더 생성: %s", path)
    return path, sequence
