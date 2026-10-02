"""프로젝트 루트의 `.env` 를 읽는다 (10-02). git 에 올리지 않는 값 — 꼴은 `.env.example`.

`KEY=VALUE` 줄만 본다. `#` 줄·빈 줄은 건너뛴다. 따옴표는 벗긴다. 없으면 빈 사전 (빌드본에는 없다).
다른 모듈을 부르지 않는다 — `utils/secret.py` 가 설정보다 먼저 부른다.
"""
from __future__ import annotations

from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def read(path: Path | None = None) -> dict[str, str]:
    try:
        lines = (path or ENV_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        out[key] = value.strip('"').strip("'")
    return out
