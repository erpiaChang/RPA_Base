r"""PreToolUse 훅: 허용된 폴더 밖의 파일 접근을 차단한다.

허용 범위
  - %USERPROFILE%\Desktop\RPA   (프로젝트 루트, CLAUDE_PROJECT_DIR)
  - %USERPROFILE%\.claude       (Claude Code 설정)

Read / Write / Edit / NotebookEdit / Glob / Grep 는 경로 인자를 검사하고,
Bash 는 명령 문자열에서 절대경로를 추출해 검사한다.

예외가 필요하면 Bash 명령에 사유를 남긴다:
    # ALLOW-OUTSIDE: <이유>

exit code 2로 차단하며, stderr가 Claude에게 전달된다.
"""
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stderr.reconfigure(encoding="utf-8")  # 콘솔 코드페이지가 cp949여도 한글 유지
    sys.stdin.reconfigure(encoding="utf-8")   # 입력(JSON)도 UTF-8 이다 (2026-09-17)
except (AttributeError, OSError):
    pass  # 재설정 불가한 스트림. 검사 자체는 계속한다.

OVERRIDE = "ALLOW-OUTSIDE:"

PATH_KEYS = ("file_path", "notebook_path", "path")

# 서브에이전트는 실계정 비밀번호가 든 설정을 읽지 않는다 (사용자 확정 2026-09-17).
# 10-02: git 밖 비밀이 늘었다 — `.env`(웹 키·구운 값 씨앗), 구운 설정·씨앗 파일
SECRET_NAMES = ("settings.local.json", "settings.baked.json", "baked.key")
ENV_RE = re.compile(r"(?<![\w.-])\.env(?![\w.-])", re.I)   # `.env` 만 — `.env.example` 은 아니다. Windows 는 대소문자를 안 가린다
# Grep 은 경로 대신 **범위**로 새어 나갈 수 있다. 범위가 config/ 나 프로젝트 전체면
# 파일 종류를 좁혔을 때만 통과시킨다.
SECRET_DIR = "config"
BROAD_GLOBS = {"*", "**", "**/*", "*.*"}


def _scope(path: str) -> str:
    """Grep 범위를 프로젝트 기준 상대 꼴로 — 절대경로로 주면 config/ 검사를 비켜 갔다 (10-02 조사 사고)."""
    text = path.strip().strip('"').replace("\\", "/")
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if text and project and Path(text).is_absolute():
        try:
            text = Path(text).resolve().relative_to(Path(project).resolve()).as_posix()
        except (OSError, ValueError):
            return text
    return text.strip("./")


# 셸로 파일 내용을 재귀·와일드카드로 훑으면 이름 검사를 비켜 비밀 파일까지 읽힌다 (10-02 검토). 서브에이전트는 Grep 도구로
RECURSIVE_RE = re.compile(
    r"\b[ef]?grep\b[^|;&\n]*\s-[A-Za-z]*[rR]|\brg\b|\bfindstr\b[^|;&\n]*\s/s\b|-Recurse\b|\bSelect-String\b"
    r"|\bfind\b[^|;&\n]*-exec\b|\bxargs\b|config[\\/]+\*", re.I)


def secret_violation(tool: str, tool_input: dict) -> str | None:
    """서브에이전트가 비밀 설정을 읽게 되는 호출이면 이유를, 아니면 None."""
    text = json.dumps(tool_input, ensure_ascii=False)
    for name in SECRET_NAMES:
        if name in text.lower():
            return f"{name} 을(를) 가리킨다"
    if ENV_RE.search(text):
        return ".env 를 가리킨다"
    if tool in ("Bash", "PowerShell") and RECURSIVE_RE.search(tool_input.get("command", "")):
        return "셸로 파일 내용을 재귀·와일드카드로 훑는다 (Grep 도구를 type/glob 으로 좁혀 쓸 것)"
    if tool != "Grep":
        return None
    path = _scope(tool_input.get("path") or "").lower()
    scope_hits_config = path in ("", ".", SECRET_DIR) or path.startswith(SECRET_DIR + "/")
    if not scope_hits_config:
        return None
    glob = (tool_input.get("glob") or "").strip()
    file_type = (tool_input.get("type") or "").strip()
    if file_type and file_type != "json":
        return None
    if glob and glob not in BROAD_GLOBS and "json" not in glob and not glob.lstrip("./").startswith(SECRET_DIR):
        return None
    return "검색 범위에 config/ 가 들어 있다 (glob 이나 type 으로 좁힐 것)"

# Windows 절대경로(C:\... / C:/...)와 Git Bash 형식(/c/...)을 추출한다.
# 구분자 문자 클래스에 백슬래시를 쓰지 않기 위해, 드라이브 문자 뒤는
# "공백/따옴표/셸 메타문자가 아닌 문자"로 받는다. 과다 매칭은 normalize()가 걸러낸다.
# 앞의 lookbehind는 URL(http://...)의 "p:" 같은 오탐을 막는다.
_STOP = "[^ 	\"'|;&>()]*"
ABS_PATH_RE = re.compile(
    "(?<![A-Za-z0-9])[A-Za-z]:" + _STOP
    + "|(?<![A-Za-z0-9/])/[a-zA-Z]/" + _STOP
)


def allowed_roots() -> list[Path]:
    roots = []
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        roots.append(Path(project))
    roots.append(Path.home() / ".claude")
    resolved = []
    for r in roots:
        try:
            resolved.append(r.resolve())
        except OSError:
            continue
    return resolved


def normalize(raw: str) -> Path | None:
    """Git Bash 형식(/c/Users/...)을 포함해 절대경로로 정규화한다."""
    text = raw.strip().strip("\"'")
    if not text:
        return None
    if re.fullmatch(r"/[a-zA-Z]/.*", text):
        text = f"{text[1].upper()}:/{text[3:]}"
    try:
        path = Path(text)
        if not path.is_absolute():
            return None  # 상대경로는 작업 디렉터리(=프로젝트) 기준이므로 허용
        return Path(os.path.normpath(str(path)))
    except (OSError, ValueError):
        return None


def is_inside(path: Path, roots: list[Path]) -> bool:
    target = os.path.normcase(str(path))
    for root in roots:
        base = os.path.normcase(str(root))
        if target == base or target.startswith(base + os.sep) or target.startswith(base + "/"):
            return True
    return False


def violations_from_command(command: str, roots: list[Path]) -> list[str]:
    bad = []
    for match in ABS_PATH_RE.findall(command):
        path = normalize(match)
        if path is None:
            continue
        if not is_inside(path, roots):
            bad.append(str(path))
    return bad


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if payload.get("agent_id"):
        reason = secret_violation(tool, tool_input)
        if reason:
            print(
                f"[비밀 설정 차단] 서브에이전트는 비밀 설정({', '.join(SECRET_NAMES)}, .env)을 읽지 않는다 — {reason}.\n"
                "  키 이름은 config/fields_collect.py / fields_erpia.py / docs/SETTINGS.md 에 있다.\n"
                "  값이 필요하면 멈추고 메인에 보고할 것.",
                file=sys.stderr,
            )
            return 2

    roots = allowed_roots()
    if not roots:
        return 0

    # PowerShell 도구도 명령 문자열로 온다 (2026-09-17 검토 — 예전에는 매처에 없어 통째로 빠졌다).
    if tool in ("Bash", "PowerShell"):
        command = tool_input.get("command", "")
        # 사유 표기는 **메인만** 쓸 수 있다. 서브에이전트 호출에는 `agent_id` 가 붙어 온다
        # (2026-09-17 훅 입력으로 확인). 서브에이전트는 사용자 승인을 받을 수 없다.
        if OVERRIDE in command and not payload.get("agent_id"):
            return 0
        bad = violations_from_command(command, roots)
    else:
        bad = []
        for key in PATH_KEYS:
            raw = tool_input.get(key)
            if not isinstance(raw, str):
                continue
            path = normalize(raw)
            if path is not None and not is_inside(path, roots):
                bad.append(str(path))

    if not bad:
        return 0

    unique = sorted(dict.fromkeys(bad))
    print(
        "[경로 차단] 허용된 폴더 밖의 경로에 접근하려 한다.\n"
        + "\n".join(f"  {p}" for p in unique)
        + "\n허용 범위:\n"
        + "\n".join(f"  {r}" for r in roots)
        + "\n\n프로젝트 안의 상대경로를 쓰거나, 대상 프로그램 경로처럼 실제로 필요한 값이면\n"
        "config/settings.local.json 에 두고 코드에서 읽을 것.\n"
        "조사 목적으로 불가피하면 Bash 명령에 사유를 남길 것: # ALLOW-OUTSIDE: <이유>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # 판정 중 오류 → **막는다.** exit 1 은 Claude Code 가 통과로 처리한다.
        print(f"[경로 훅 오류] {type(exc).__name__}: {exc} — 안전을 위해 막는다.",
              file=sys.stderr)
        sys.exit(2)
