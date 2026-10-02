"""PostToolUse 훅: 저장된 파일에서 금지 패턴을 검출해 경고한다.

차단하지 않고 경고만 한다. exit code 2로 stderr를 Claude에게 전달한다.
.py 는 RPA 규칙, .sql / .html / .js 는 서버 연동 규칙 (2026-09-22, `docs/SERVER_PLAN.md` D-10).
"""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8")   # 입력(JSON)은 UTF-8 이다 (2026-09-17)
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError) as exc:
    print(f"[check_rpa_rules] 스트림 인코딩 설정 실패: {exc}", file=sys.stderr)

# (정규식, 메시지)
PY_RULES = [
    (
        re.compile(r"^\s*(?!#).*\btime\.sleep\s*\(", re.M),
        "time.sleep() 고정 대기. 조건 대기(Window/Control 등장, enabled 확인) + timeout으로 대체할 것. "
        "UI Automation으로 상태 확인이 불가능한 경우에만 허용되며, 그 이유를 주석으로 남길 것.",
    ),
    (
        re.compile(r"^\s*(?!#).*\.wait_for_timeout\s*\(", re.M),
        "Playwright wait_for_timeout() 고정 대기 — time.sleep 과 같은 규칙. wait_for_selector / expect_download / "
        "wait_for_load_state 같은 조건 대기(timeout)로 대체할 것. 불가피하면 그 이유를 주석으로 남길 것.",
    ),
    (
        re.compile(r"^\s*(?!#).*pyautogui\.(click|moveTo|dragTo|mouseDown|mouseUp)\s*\(\s*-?\d", re.M),
        "pyautogui 절대좌표 조작. UI Automation(1순위) → 키보드(2순위) → 이미지 인식(3순위)을 "
        "먼저 검토했는지 확인할 것. 불가피하면 좌표를 config로 분리하고 docs/UI_SURVEY.md에 근거를 남길 것.",
    ),
    (
        re.compile(r"^\s*(?!#).*[\"'][A-Za-z]:\\\\?[^\"']*\.(exe|xlsx|xls|xlsm)[\"']", re.M | re.I),
        "EXE 또는 Excel 경로 하드코딩. config/settings.py에서 관리할 것.",
    ),
    (
        re.compile(r"except[^:]*:\s*(pass|\.\.\.)\s*$", re.M),
        "예외 무시(except: pass). 최소한 로깅하고, 실패 시 스크린샷을 저장할 것.",
    ),
    (
        re.compile(r"^\s*while\s+True\s*:", re.M),
        "무한 루프. Retry라면 최대 횟수를 제한할 것. 대기 루프라면 timeout을 둘 것.",
    ),
    (
        re.compile(r"^\s*(?!#).*(_create_unverified_context|check_hostname\s*=\s*False|verify_mode\s*=\s*ssl\.CERT_NONE|verify\s*=\s*False)", re.M),
        "인증서 검증을 끈다. 서버로 가는 연결은 기본 ssl 검증을 유지할 것 (SERVER_PLAN D-10).",
    ),
]

SQL_RULES = [
    (
        re.compile(r"security\s+definer(?![^;]*search_path)", re.I | re.S),
        "security definer 함수에 `set search_path = ''` 가 없다 (search_path 가로채기).",
    ),
    (
        re.compile(r"grant\s+(?:(?:select|insert|update|delete|all)[^;]*\bon\s+(?:table\s+)?(?!function)[\w.\"]+[^;]*\bto\s+anon\b"
                   r"|(?:insert|update|delete|all)[^;]*\bon\s+(?:table\s+)?(?!function)[\w.\"]+[^;]*\bto\s+authenticated\b)", re.I),
        "anon 에게 표 권한, 또는 authenticated 에게 쓰기 권한을 준다. 쓰기는 RPC 함수로만, 읽기는 select + RLS 로 (SERVER_PLAN D-10).",
    ),
    (
        re.compile(r"\buser_metadata\b|raw_user_meta_data", re.I),
        "user_metadata 는 사용자가 고칠 수 있다. 권한 판단에 쓰지 말 것 (app_metadata).",
    ),
]

WEB_RULES = [
    (
        re.compile(r"\.innerHTML\s*[+]?=|document\.write\s*\(|\binsertAdjacentHTML\s*\(|\beval\s*\("),
        "innerHTML / document.write / eval — 서버 문자열은 PC(빌드를 가진 누군가)가 보낸 것이다. textContent 만 쓸 것 (SERVER_PLAN D-10).",
    ),
    (
        re.compile(r"<script(?![^>]*\bintegrity=)[^>]*\bsrc=[\"']https?://[^>]*>", re.I),
        "CDN 스크립트에 integrity(SRI) 가 없다. 버전을 고정하고 integrity 를 붙일 것.",
    ),
    (
        re.compile(r"service_role", re.I),
        "웹 페이지에 service_role 이 나온다. 공개 키(anon)만 둘 것.",
    ),
]

RULES_BY_SUFFIX = {".py": PY_RULES, ".sql": SQL_RULES, ".html": WEB_RULES, ".js": WEB_RULES}

SKIP_DIRS = {".venv", "site-packages", "node_modules", "__pycache__"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    path_str = (payload.get("tool_input") or {}).get("file_path", "")
    path = Path(path_str)
    rules = RULES_BY_SUFFIX.get(path.suffix.lower())
    if not rules:
        return 0
    if SKIP_DIRS & set(path.parts):
        return 0
    # 훅 자신은 검사하지 않는다. 규칙 정의에 금지 패턴 문자열이 들어 있다.
    if path.parent.name == "hooks" and ".claude" in path.parts:
        return 0

    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return 0

    # 경로 하드코딩 규칙은 config/ 자신에는 적용하지 않는다.
    # 경로를 관리하라고 지정된 위치이고, 설명용 예시가 들어간다.
    if path.parent.name == "config":
        rules = [(p, m) for p, m in rules if "하드코딩" not in m]
    # html 안의 SQL 은 없다. sql 규칙은 CREATE TABLE 뒤에 RLS 가 있는지도 본다.
    findings = []
    for pattern, message in rules:
        for match in pattern.finditer(source):
            line_no = source.count("\n", 0, match.start()) + 1
            findings.append(f"  L{line_no}: {message}")
    if path.suffix.lower() == ".sql":
        findings += missing_rls(source)

    if not findings:
        return 0

    print(
        f"[규칙 경고] {path.name}\n"
        + "\n".join(findings)
        + "\n검토 후 정당한 사유가 있으면 주석으로 근거를 남기고 진행할 것.",
        file=sys.stderr,
    )
    return 2


TABLE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?\"?(\w+)\"?", re.I)
RLS = re.compile(r"alter\s+table\s+(?:public\.)?\"?(\w+)\"?\s+enable\s+row\s+level\s+security", re.I)


def missing_rls(source: str) -> list[str]:
    """`create table` 은 있는데 같은 파일에 RLS 켜는 문장이 없는 표."""
    enabled = {m.group(1).lower() for m in RLS.finditer(source)}
    out = []
    for match in TABLE.finditer(source):
        name = match.group(1)
        if name.lower() not in enabled:
            line_no = source.count("\n", 0, match.start()) + 1
            out.append(f"  L{line_no}: 표 {name} 에 RLS 를 켜는 문장이 없다 "
                       f"(alter table public.{name} enable row level security).")
    return out


if __name__ == "__main__":
    sys.exit(main())
