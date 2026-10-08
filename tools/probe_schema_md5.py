r"""[조사 도구 - 읽기 전용] `server/schema.sql` 함수 본문의 md5 — 실서버 `md5(prosrc)` 와 대조한다 (10-08).

SQL 을 손으로 옮겨 적용하므로 적용 뒤 전사 오류를 이것으로 본다. 서버 쪽은 승인 뒤 MCP 로:
    select n.nspname || '.' || p.proname, md5(p.prosrc) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('public', 'private') order by 1;

    .venv\Scripts\python.exe -m tools.probe_schema_md5 [이름 ...]
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "server" / "schema.sql"
FUNC = re.compile(r"create or replace function ([\w.]+)\(.*?\bas \$\$(.*?)\$\$;", re.S | re.I)


def bodies(text: str) -> dict[str, str]:
    """이름 → 본문(마지막 정의). 본문은 `$$` 사이 그대로 = 서버의 prosrc."""
    return {m.group(1): m.group(2) for m in FUNC.finditer(text.replace("\r\n", "\n"))}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")     # 서버 결과와 줄 단위로 맞대므로 \r 없이
    want = set(sys.argv[1:])
    for name, body in sorted(bodies(SCHEMA.read_text(encoding="utf-8")).items()):
        if not want or name in want or name.split(".")[-1] in want:
            print(f"{name}\t{hashlib.md5(body.encode('utf-8')).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
