"""PreToolUse 훅: 실제 업무 프로그램을 조작할 수 있는 실행 명령을 차단하고 확인을 요구한다.

조사(read-only) 스크립트는 통과시킨다.
exit code 2로 차단하며, stderr가 Claude에게 전달된다.
"""
import json
import re
import sys

try:
    sys.stderr.reconfigure(encoding="utf-8")  # 콘솔 코드페이지가 cp949여도 한글 유지
    # ★ 입력도 UTF-8 로 읽는다 (2026-09-17). 기본(cp949)으로 읽으면 한글이 든 명령에서
    #   차단 메시지를 찍다가 예외(exit 1)로 끝나고, exit 1 은 **통과**로 처리된다.
    sys.stdin.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass  # 재설정 불가한 스트림. 검사 자체는 계속한다.

# 실행으로 간주하는 명령
#  `py`를 단독 단어로 잡으면 "login.py" 안의 py 에도 걸려서
#  파일명을 언급하기만 한 무해한 명령까지 차단된다. 인터프리터 호출 형태만 잡는다.
#  경로로 호출하는 형태(.venv/Scripts/python.exe)도 잡아야 하므로 앞에 / \ 를 허용한다.
#  실행 대상(모듈/스크립트)을 group(1) 로 잡는다. 한 줄에 여러 번 나오면 **전부** 본다.
#  인터프리터와 대상 사이의 옵션(-u, -3, -X utf8)도 건너뛴다. pythonw 도 같다 (2026-09-17 검토).
INTERP = r"(?:^|[\s;&|/\\\"'(])(?:python3?w?|py)(?:\.exe)?[\"']?"
OPTIONS = r"(?:\s+(?:-[XW]\s+\S+|-(?!m\b|c\b)[^\s\"']+))*"
RUN_PATTERN = re.compile(INTERP + OPTIONS + r"\s+(-m\s+\S+|[^\s\"']*\.py\b)")
#  대상 이름이 없는 실행 — `-c "..."`, `- <<EOF`, `< 파일` — 은 업무 코드를 불러오면 조작으로 본다.
INLINE_PATTERN = re.compile(
    INTERP + OPTIONS + r"\s*(?:-c\b|-(?=\s|$)|<)"
    r".*\b(automation|orchestrator|collect)\b", re.S
)

# 조사 전용으로 간주해 통과시킬 스크립트 (읽기 전용)
#  - survey/dump 등: 대상 프로그램을 읽기만 하는 조사 도구
#  - .claude/hooks: 훅 자체를 검증하는 스크립트. 대상 프로그램을 건드리지 않는다.
#  ★ **실행 대상의 이름이 이것으로 시작할 때만** 통과한다 (2026-09-17). 예전에는 명령
#    어디에든 `probe` 가 있으면 통과했다 (주석, `probe && test_flow`, `x/hooks/y.py`).
SAFE_NAME = re.compile(r"(survey|inspect|dump|probe|explore|list_windows)", re.I)
HOOKS_DIR = re.compile(r"(?:^|[\\/])\.claude[\\/]hooks[\\/][^\\/]+\.py$")
#  설치된 플러그인의 보조 스크립트(claude-security 의 scripts/*.py 등). 대상 프로그램과 무관하다 (2026-09-22).
PLUGIN_DIR = re.compile(r"[\\/]\.claude[\\/]plugins[\\/]")

# git 을 부르는 앞부분 — 경로(/usr/bin/git, …\git.exe)·따옴표·bash -c '…' 안, 그리고 앞 옵션까지
GIT = (r"(?:^|[\s;&|(\"'`/\\])git(?:\.exe)?[\"']?\s+"
       r"(?:(?:-c\s+\S+|-C\s+\S+|--[\w-]+(?:=\S+)?)\s+)*")

# 서버에 쓰는 명령 — 실서버(Supabase)·배포에 닿는다. 승인 없이는 막는다 (2026-09-22, `docs/SERVER_PLAN.md`).
#  supabase.com(문서) 은 막지 않는다. `*.supabase.co`(프로젝트 API) 와 `/rest/v1/` 만 본다.
SERVER_PATTERN = re.compile(
    r"(?:(?:^|[\s;&|(])supabase\s+(?:db\s+(?:push|reset)|functions\s+deploy|migration\s+up|link)\b"
    r"|(?:^|[\s;&|(])psql\b"
    r"|(?:^|[\s;&|(])wrangler(?:@\S+)?\s+(?:pages\s+)?deploy\b"     # npx wrangler@4 deploy 도 (10-02)
    r"|(?:curl|Invoke-RestMethod|Invoke-WebRequest|iwr|irm)\b[^\n]*?(?:https?://[\w-]+\.supabase\.co\b|/rest/v1/)"
    # GitHub 에 올리는 것·커밋 전 검사를 건너뛰는 것 (10-02) — 실값이 밖으로 나가는 길이다.
    # git 은 경로·.exe·따옴표·앞 옵션(-c x, -C x, --git-dir=…)을 붙여 불러도 본다 (10-02 검토)
    r"|" + GIT + r"(?:push|remote\s+(?:add|set-url))\b"
    r"|" + GIT + r"commit\b[^\n]*?\s-[A-Za-z]*n[A-Za-z]*\b"          # commit -n·-an = 검사 건너뛰기
    r"|core\.hooksPath(?!\s+\.githooks\b)"                            # 훅 자리를 바꾸거나 지운다 (.githooks 로 맞추는 것만 된다)
    r"|(?:^|[\s;&|(\"'`/\\])gh(?:\.exe)?[\"']?\s+(?:repo\s+(?:create|edit|rename|delete|sync)"
    r"|gist\s+create|release\s+(?:create|upload)"
    r"|api\b[^\n]*?(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--method\s+(?:POST|PUT|PATCH|DELETE)|\s-[fF]\s|--(?:raw-)?field|--input))"
    r"|(?:^|\s)--no-verify\b)",
    re.I,
)


def is_safe(target: str) -> bool:
    target = target.strip()
    if target.startswith("-m"):
        return bool(SAFE_NAME.match(target[2:].strip().rsplit(".", 1)[-1]))
    if HOOKS_DIR.search(target) or PLUGIN_DIR.search(target):
        return True
    return bool(SAFE_NAME.match(re.split(r"[\\/]", target)[-1]))


# rpa-verifier 가 실행해도 되는 명령의 **전체 모양.**
#  cd 인자에는 셸 기호를 못 쓴다. 꼬리는 tail/head 또는 **패턴 하나만** 받는 grep —
#  grep 에 파일을 주면 그 파일을 읽게 된다 (2026-09-17 검토: settings.local.json 노출).
VERIFIER_ALLOWED = re.compile(
    r"(?:cd\s+[\w/\\.:\"-]+\s*&&\s*)?"
    r"\.venv[/\\]Scripts[/\\]python\.exe\s+-m\s+tools\.probe_\w+"
    r"(?:\s+--?[\w-]+)*"
    r"(?:\s+2>&1)?"
    r"(?:\s*\|\s*(?:(?:tail|head)(?:\s+-n)?(?:\s+-?\d+)?"
    r"|grep(?:\s+-[A-Za-z]+)*\s+(?:\"[^\"`$\\]*\"|'[^']*'|[^\s;&|<>`$'\"\\]+)))?"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    # 서브에이전트 호출에는 `agent_id` / `agent_type` 이 붙어 온다 (2026-09-17 훅 입력으로 확인).
    agent = payload.get("agent_type") if payload.get("agent_id") else None

    # rpa-verifier 는 확인 도구(probe_*) 실행만 한다. 파일을 지우거나 고치는 명령도 막는다.
    if agent == "rpa-verifier" and not VERIFIER_ALLOWED.fullmatch(command.strip()):
        print(
            "[rpa-verifier 제한] 확인 도구 실행만 허용한다:\n"
            "  .venv/Scripts/python.exe -m tools.probe_<이름>  (앞에 cd, 뒤에 2>&1 / | tail 등 허용)\n"
            f"  받은 명령: {command.strip()}\n"
            "멈추고 메인에 보고할 것.",
            file=sys.stderr,
        )
        return 2

    targets = RUN_PATTERN.findall(command)
    risky = [t for t in targets if not is_safe(t)]
    if INLINE_PATTERN.search(command):
        risky.append("python -c / stdin (업무 코드 import)")
    if SERVER_PATTERN.search(command):
        risky.append("서버 쓰기 / 배포 (Supabase·psql·wrangler·실서버 요청)")
    if not risky:
        return 0
    # 사용자가 승인한 실행. 명령에 사유를 남겨야 통과한다.
    #   예) python -m automation.logistics_wait  # APPROVED-RUN: 물류대기 테스트
    #   ★ 메인만 쓸 수 있다. 서브에이전트는 사용자 승인을 받을 수 없다.
    if re.search(r"#\s*APPROVED-RUN:\s*\S+", command) and agent is None:
        return 0

    print(
        "[실행 확인 필요] 이 명령은 실제 업무 프로그램이나 실서버를 조작할 수 있다.\n"
        f"  {command.strip()}\n"
        "실행하지 말고 사용자에게 다음을 확인할 것:\n"
        "  1. 대상 프로그램이 테스트 환경인가, 운영 환경인가\n"
        "  2. 데이터를 변경/저장/전송하는 동작이 포함되는가\n"
        "  3. dry-run 옵션으로 먼저 확인할 수 있는가\n"
        "사용자가 승인하면 그때 다시 실행할 것.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # 판정 중 오류 → **막는다.** exit 1 은 Claude Code 가 통과로 처리한다.
        print(f"[실행 확인 훅 오류] {type(exc).__name__}: {exc} — 안전을 위해 막는다.",
              file=sys.stderr)
        sys.exit(2)
