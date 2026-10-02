r"""[개발 전용 — 배포 준비] 웹 대시보드를 `build/web/` 에 만든다 (10-02). 파일만 쓴다.

git 의 `web/` 에는 Supabase 주소·공개 키 대신 자리표시(`__SUPABASE_URL__`·`__SUPABASE_PUBLISHABLE_KEY__`)가 있다.
`.env` 의 값으로 바꿔 `build/web/` 에 쓰고, `deploy_web.bat` 이 그 폴더를 올린다 (`web/wrangler.jsonc`).

    .venv\Scripts\python.exe -m tools.build_web
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PROJECT_ROOT  # noqa: E402
from utils import envfile  # noqa: E402

WEB = PROJECT_ROOT / "web"
OUT = PROJECT_ROOT / "build" / "web"
ASSETS = ("index.html", "app.js", "config.js", "_headers")       # 올리는 것은 이 넷뿐 (README·wrangler 설정은 안 올린다)
# 자리표시 → .env 키와 꼴
FILL = {
    "__SUPABASE_URL__": ("SUPABASE_URL", re.compile(r"https://[a-z0-9]+\.supabase\.co")),
    "__SUPABASE_PUBLISHABLE_KEY__": ("SUPABASE_PUBLISHABLE_KEY", re.compile(r"sb_publishable_[A-Za-z0-9_-]+")),
}


class BuildError(RuntimeError):
    """만들 수 없다. 메시지가 사람이 볼 이유다."""


def values(env: dict[str, str]) -> dict[str, str]:
    """{자리표시: 값}. 비었거나 꼴이 틀리면 BuildError — 반쯤 바뀐 대시보드를 올리지 않는다."""
    out = {}
    for mark, (key, shape) in FILL.items():
        value = env.get(key, "")
        if not shape.fullmatch(value):
            raise BuildError(f".env 의 {key} 가 비었거나 꼴이 틀렸다 (꼴은 .env.example)")
        out[mark] = value
    return out


def fill(text: str, marks: dict[str, str]) -> str:
    for mark, value in marks.items():
        text = text.replace(mark, value)
    return text


def build(env: dict[str, str] | None = None, out: Path = OUT) -> Path:
    marks = values(envfile.read() if env is None else env)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ASSETS:
        text = fill((WEB / name).read_text(encoding="utf-8"), marks)
        if "__SUPABASE_" in text:
            raise BuildError(f"{name} 에 바꾸지 못한 자리표시가 남았다")
        (out / name).write_text(text, encoding="utf-8", newline="")
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass  # 재설정 불가한 스트림. 출력만 깨질 뿐 만드는 것은 계속한다.
    try:
        path = build()
    except BuildError as exc:
        print(f"[중단] {exc}")
        return 1
    print(f"만들었다: {path} ({', '.join(ASSETS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
