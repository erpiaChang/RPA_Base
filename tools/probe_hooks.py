r"""[조사 도구 - 읽기 전용] 프로젝트 훅(confirm_run / guard_paths / guard_secrets)이 **막을 것을 막는지** 본다.

대상 프로그램을 건드리지 않는다. 훅 스크립트에 가짜 입력(JSON)을 넣고 종료 코드만 본다.

    .venv\Scripts\python.exe -m tools.probe_hooks

## 왜 있나 (2026-09-17)

PreToolUse 훅은 **exit 2 일 때만** 막는다. 예외(exit 1)는 통과로 처리된다.
실제로 한글이 든 명령에서 훅이 예외로 끝나 조작 명령이 통과할 수 있었다.
훅을 고치면 이 도구로 다시 확인한다. 입력은 실제와 같이 **UTF-8 바이트**로 넣는다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
V = ".venv/Scripts/python.exe"
BASH_ROOT = "/" + ROOT.drive[0].lower() + ROOT.as_posix()[2:]     # git bash 꼴 (/c/...) — 경로를 파일에 박지 않는다 (10-02)
MAIN: dict = {}
VERIFIER = {"agent_id": "t1", "agent_type": "rpa-verifier"}
GENERAL = {"agent_id": "t2", "agent_type": "general-purpose"}
OUTSIDE = "C:" + "/Win" + "dows/win.ini"      # 문자열 그대로 두면 이 도구를 실행하는 명령이 막힌다
OUTSIDE_PS = "C:" + "\\Win" + "dows"

# (누가, 도구, 명령, 기대 exit) — 0 통과 / 2 차단
RUN_CASES = [
    (MAIN, "Bash", f"{V} -m tools.probe_hold_loop", 0),
    (MAIN, "Bash", f"{V} -m tools.probe_hold_loop  # 한글 주석", 0),
    (MAIN, "Bash", "python -m tools.survey_windows --save && python -m tools.dump_controls --pid 1", 0),
    (MAIN, "Bash", f"{V} .claude/hooks/confirm_run.py", 0),
    (MAIN, "Bash", f'{V} -c "import json; print(1)"', 0),
    (MAIN, "Bash", "grep -n python docs/PROGRESS.md", 0),
    (MAIN, "Bash", f"{V} -m tools.test_flow", 2),
    (MAIN, "Bash", f"{V} -m tools.test_flow --yes  # 한글 사유", 2),
    (MAIN, "Bash", f"{V} -m tools.test_flow  # probe 결과 확인", 2),
    (MAIN, "Bash", f"{V} -m tools.probe_gui && {V} -m tools.test_flow --yes", 2),
    (MAIN, "Bash", f"{V} -m automation.logistics_wait --step hold", 2),
    (MAIN, "Bash", f"cd x && {V} main.py", 2),
    (MAIN, "Bash", f'{V} -c "from automation import logistics_wait"', 2),
    (MAIN, "Bash", f"{V} -u -m automation.order_mapping", 2),
    (MAIN, "Bash", "py -3 -m automation.order_mapping", 2),
    (MAIN, "Bash", f"{V} -X utf8 -m collect.mail", 2),
    (MAIN, "Bash", ".venv/Scripts/pythonw.exe -m automation.order_mapping", 2),
    (MAIN, "Bash", f"{V} - <<'EOF'\nimport automation.logistics_wait as m\nEOF", 2),
    (MAIN, "Bash", f"{V} automation/hooks/x.py", 2),
    (MAIN, "Bash", f"{V} -m tools.test_flow  # APPROVED-RUN: 한글 사유", 0),
    (MAIN, "PowerShell", "& .venv\\Scripts\\python.exe -m tools.test_flow --yes", 2),
    (MAIN, "PowerShell", "& .venv\\Scripts\\python.exe -m tools.probe_hold_loop", 0),
    (GENERAL, "Bash", f"{V} -m tools.test_flow  # APPROVED-RUN: 서브가 붙임", 2),
    (GENERAL, "Bash", f"{V} -m tools.probe_hold_loop", 0),
    (GENERAL, "Bash", "ls logs", 0),
    (VERIFIER, "Bash", f"cd {BASH_ROOT} && {V} -m tools.probe_hold_loop", 0),
    (VERIFIER, "Bash", f"{V} -m tools.probe_progress 2>&1 | tail -20", 0),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report | grep 결과", 0),
    (VERIFIER, "Bash", f'{V} -m tools.probe_report | grep -E "결과|실패"', 0),
    (VERIFIER, "Bash", "ls logs", 2),
    (VERIFIER, "Bash", "rm -f logs/x.log", 2),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report > logs/out.txt", 2),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report && rm -rf docs", 2),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report | tail -5; rm x", 2),
    (VERIFIER, "Bash", f"{V} -m tools.test_flow  # APPROVED-RUN: x", 2),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report | grep $(rm x)", 2),
    (VERIFIER, "Bash", f"cd x;id && {V} -m tools.probe_report", 2),
    (VERIFIER, "Bash", f"{V} -m tools.probe_report | grep -a . config/settings.local.json", 2),
    # 서버 쓰기·배포 (2026-09-22). 문서 사이트와 플러그인 스크립트는 통과.
    (MAIN, "Bash", "supabase db push", 2),
    (MAIN, "Bash", "cd server && supabase functions deploy ingest", 2),
    (MAIN, "Bash", "psql postgresql://x@db.abc.supabase.co/postgres -f server/schema.sql", 2),
    (MAIN, "Bash", "wrangler pages deploy web", 2),
    (MAIN, "Bash", "cd web && npx wrangler@4 deploy", 2),
    (MAIN, "Bash", "npx wrangler@4 whoami", 0),
    (MAIN, "Bash", "curl -X POST https://abcd.supabase.co/rest/v1/rpc/ingest -d '{}'", 2),
    (MAIN, "PowerShell", "Invoke-RestMethod -Method Post -Uri https://abcd.supabase.co/rest/v1/rpc/ingest", 2),
    (MAIN, "Bash", "curl -X POST https://abcd.supabase.co/rest/v1/rpc/ingest  # APPROVED-RUN: 실서버 왕복", 0),
    (GENERAL, "Bash", "curl https://abcd.supabase.co/rest/v1/runs  # APPROVED-RUN: 서브가 붙임", 2),
    (MAIN, "Bash", "curl -s https://supabase.com/docs/guides/cron", 0),
    (MAIN, "Bash", "supabase gen types --linked > web/types.ts", 0),
    (MAIN, "Bash", "python3 /c/Users/x/.claude/plugins/cache/claude-plugins-official/claude-security/0.11.0/scripts/save_result.py --x", 0),
    (MAIN, "Bash", f"{V} -m tools.test_telemetry_send", 2),
    # GitHub 에 올리는 것·검사 건너뛰기 (10-02). 읽기·로컬 커밋은 통과
    (MAIN, "Bash", "git push -u origin main", 2),
    (MAIN, "Bash", "git -c core.hooksPath=x push", 2),
    (MAIN, "Bash", "git remote add origin https://github.com/x/y.git", 2),
    (MAIN, "Bash", "git remote set-url origin https://github.com/x/y.git", 2),
    (MAIN, "Bash", 'cd x && "/c/Program Files/GitHub CLI/gh.exe" repo create y --private', 2),
    (MAIN, "Bash", "gh repo create y --private --source . --push", 2),
    (MAIN, "Bash", 'git commit --no-verify -m "x"', 2),
    (MAIN, "Bash", "git push origin main  # APPROVED-RUN: 첫 업로드", 0),
    (GENERAL, "Bash", "git push  # APPROVED-RUN: 서브가 붙임", 2),
    (MAIN, "Bash", "git status && git log --oneline -3", 0),
    (MAIN, "Bash", 'git commit -m "push 기록 정리"', 0),
    (MAIN, "Bash", "gh repo view x --json name", 0),
    (MAIN, "Bash", "git remote -v", 0),
    # 10-02 검토 — 다른 꼴로 불러도, 검사를 다른 길로 꺼도
    (MAIN, "Bash", "git.exe push origin main", 2),
    (MAIN, "Bash", "git -C . push", 2),
    (MAIN, "Bash", "git --no-pager push", 2),
    (MAIN, "Bash", "bash -c 'git push'", 2),
    (MAIN, "Bash", 'git commit -n -m "x"', 2),
    (MAIN, "Bash", 'git commit -anm "x"', 2),
    (MAIN, "Bash", "git -c core.hooksPath=nul commit -m x", 2),
    (MAIN, "Bash", "git config --unset core.hooksPath", 2),
    (MAIN, "Bash", "git config core.hooksPath .githooks", 0),
    (MAIN, "Bash", "gh gist create a.txt", 2),
    (MAIN, "Bash", "gh release create v1", 2),
    (MAIN, "Bash", "gh api -X PUT repos/x/y/contents/a", 2),
    (MAIN, "Bash", "gh api user --jq .login", 0),
    (MAIN, "Bash", 'git commit -m "기록 정리" && git log --oneline -1', 0),
]

# guard_secrets — 비밀 키는 settings.local.json 에만 (2026-09-22).
#  가짜 키는 이어 붙여 만든다 — 이 파일을 저장할 때 훅이 막지 않게.
_HEAD = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
_SIG = "abcdefghijklmnopqrstuvwxyz0123456789"
ANON_JWT = ".".join((_HEAD, "eyJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJhbm9uIiwiaWF0IjoxfQ", _SIG))
SERVICE_JWT = ".".join((_HEAD, "eyJpc3MiOiJzdXBhYmFzZSIsInJvbGUiOiJzZXJ2aWNlX3JvbGUiLCJpYXQiOjF9", _SIG))
SB_SECRET = "sb_" + "secret_abcdefghijklmnop"
RESEND = "re_" + "AbCdEfGh_1234567890abcdefghijklmnop"
DEVICE = "rpa_" + "A" * 44
SECRET_WRITE_CASES = [
    (MAIN, "Write", {"file_path": "web/index.html", "content": f"const KEY = '{ANON_JWT}';"}, 0),
    (MAIN, "Write", {"file_path": "web/index.html", "content": f"const KEY = '{SERVICE_JWT}';"}, 2),
    (MAIN, "Edit", {"file_path": "server/schema.sql", "old_string": "x", "new_string": f"-- {SB_SECRET}"}, 2),
    (MAIN, "Write", {"file_path": "docs/x.md", "content": f"키: {RESEND}"}, 2),
    (MAIN, "Write", {"file_path": "orchestrator/telemetry.py", "content": f"KEY = '{DEVICE}'"}, 2),
    (MAIN, "Write", {"file_path": "config/settings.local.json", "content": f'{{"server_build_id": "{DEVICE}"}}'}, 0),
    # Windows 도구 입력은 역슬래시 경로다 (10-02 검토 — 전에는 막혔다)
    (MAIN, "Write", {"file_path": str(ROOT / "config" / "settings.local.json"),
                     "content": f'{{"server_build_id": "{DEVICE}"}}'}, 0),
    (MAIN, "Write", {"file_path": "docs/x.md", "content": "토큰: " + "enr_" + "B" * 44}, 2),
    (MAIN, "Write", {"file_path": "llm/worker.py", "content": "KEY = '" + "llmw_" + "C" * 43 + "'"}, 2),
    (MAIN, "Write", {"file_path": "llm/worker.py", "content": 'KEY_PREFIX = "llmw_"  # llm_worker_key'}, 0),
    (MAIN, "Write", {"file_path": "web/index.html", "content": "sb_publishable_abcdefghijklmnop re_compile rpa_1"}, 0),
    (GENERAL, "Write", {"file_path": "web/index.html", "content": f"'{SERVICE_JWT}'"}, 2),
]

SECRET = "config/settings.local.json"
# 서브에이전트만 막는다. 메인은 그대로 읽을 수 있다.
SECRET_CASES = [
    (MAIN, "Read", {"file_path": SECRET}, 0),
    (GENERAL, "Read", {"file_path": SECRET}, 2),
    (VERIFIER, "Read", {"file_path": SECRET}, 2),
    (GENERAL, "Bash", {"command": f"cat {SECRET}"}, 2),
    (GENERAL, "Grep", {"pattern": "password", "path": "config"}, 2),
    (GENERAL, "Grep", {"pattern": "password"}, 2),
    (GENERAL, "Grep", {"pattern": "password", "path": "config", "type": "py"}, 0),
    (GENERAL, "Grep", {"pattern": "password", "glob": "*.md"}, 0),
    (GENERAL, "Grep", {"pattern": "password", "path": "automation"}, 0),
    (MAIN, "Grep", {"pattern": "password", "path": "config"}, 0),
    # 10-02: 절대경로·config/** 로 비켜 가던 구멍, git 밖 비밀이 늘었다 (.env·구운 파일)
    (GENERAL, "Grep", {"pattern": "password", "path": str(ROOT)}, 2),
    (GENERAL, "Grep", {"pattern": "password", "path": str(ROOT / "config")}, 2),
    (GENERAL, "Grep", {"pattern": "password", "glob": "config/**"}, 2),
    (GENERAL, "Grep", {"pattern": "password", "path": str(ROOT), "glob": "*.py"}, 0),
    (GENERAL, "Read", {"file_path": str(ROOT / ".env")}, 2),
    (VERIFIER, "Bash", {"command": f"{V} -m tools.probe_leaks && type .env"}, 2),
    (GENERAL, "Read", {"file_path": str(ROOT / ".env.example")}, 0),
    (GENERAL, "Read", {"file_path": "build/baked/baked.key"}, 2),
    (MAIN, "Read", {"file_path": str(ROOT / ".env")}, 0),
    # 셸로 재귀·와일드카드로 훑으면 이름 검사를 비켜 간다 (10-02 검토) — 서브에이전트만 막는다
    (GENERAL, "Bash", {"command": "grep -rn login ."}, 2),
    (GENERAL, "Bash", {"command": "findstr /s /i login *.json"}, 2),
    (GENERAL, "PowerShell", {"command": "Select-String -Path cfg -Pattern x"}, 2),
    (GENERAL, "Bash", {"command": "cat config/*"}, 2),
    (GENERAL, "Bash", {"command": "grep -c x docs/HANDOFF.md"}, 0),
    (MAIN, "Bash", {"command": "grep -rn login docs"}, 0),
    # Windows 는 대소문자를 안 가린다 — 같은 파일이다 (10-02)
    (GENERAL, "Read", {"file_path": str(ROOT / ".ENV")}, 2),
    (GENERAL, "Read", {"file_path": "Config/Settings.Local.json"}, 2),
    (GENERAL, "Grep", {"pattern": "password", "path": "CONFIG"}, 2),
]

# 실서버 SQL (09-28): 사용자 결정 뒤 `-- APPROVED-SQL:` 을 붙인 것만, 메인만. 읽기 도구는 그냥 통과
SQL_CASES = [
    (MAIN, "mcp__supabase-rw__execute_sql", {"query": "select 1"}, 2),
    (MAIN, "mcp__supabase-rw__apply_migration", {"name": "x", "query": "create table t (id int)"}, 2),
    (MAIN, "mcp__supabase-rw__execute_sql", {"query": "-- APPROVED-SQL: 스키마 재적용\nselect 1"}, 0),
    (GENERAL, "mcp__supabase-rw__execute_sql", {"query": "-- APPROVED-SQL: 시험\nselect 1"}, 2),
    (MAIN, "mcp__supabase-rw__list_tables", {"schemas": ["public"]}, 0),
    (MAIN, "mcp__supabase-rw__get_advisors", {"type": "security"}, 0),
]

GUARD_CASES = [
    (MAIN, "Bash", f"cat {OUTSIDE}  # 한글", 2),
    (MAIN, "Bash", f"cat {OUTSIDE}  # ALLOW" + "-OUTSIDE: 시험", 0),
    (GENERAL, "Bash", f"cat {OUTSIDE}  # ALLOW" + "-OUTSIDE: 시험", 2),
    (MAIN, "PowerShell", f"Test-Path {OUTSIDE_PS}", 2),
    (MAIN, "PowerShell", "Get-ChildItem logs", 0),
    (MAIN, "Bash", "ls logs", 0),
]


# 규칙 경고 (PostToolUse, 경고만 — exit 2 가 경고). 훅이 파일을 읽으므로 logs/ 에 잠깐 쓴다
_WAIT = "wait_for" + "_timeout"      # 이 파일 자체가 경고에 걸리지 않게 나눠 둔다
RULE_CASES = [
    (f"page.{_WAIT}(500)\n", 2),                                   # Playwright 고정 대기 (09-30)
    (f"    def {_WAIT}(self, ms):\n        return None\n", 0),     # 가짜 Page 의 메서드 정의
    (f"# page.{_WAIT}(1) 는 쓰지 않는다\n", 0),                     # 주석
]


def run_hook(name: str, who: dict, tool: str, tool_input) -> int:
    if isinstance(tool_input, str):
        tool_input = {"command": tool_input}
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input, **who}
    result = subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, cwd=ROOT, timeout=30,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    return result.returncode


TICKET = ROOT / "logs" / "_sql_ticket.json"
PROJECT = "proj0test"
SQL_OK = {"project_id": PROJECT, "query": "-- APPROVED-SQL: 시험\ndrop function if exists f()"}


def elicit(who: dict, message: str) -> str:
    payload = {"hook_event_name": "Elicitation", "mcp_server_name": "supabase-rw", "mode": "form",
               "message": message, "requested_schema": {"type": "object", "properties": {}}, **who}
    result = subprocess.run(
        [sys.executable, str(HOOKS / "sql_elicit.py")],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True, cwd=ROOT, timeout=30,
        env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    try:
        return json.loads(result.stdout)["hookSpecificOutput"]["action"]
    except (ValueError, KeyError, TypeError):
        return f"exit {result.returncode}"


def elicit_cases() -> list[tuple[str, bool]]:
    """표가 있을 때만·한 번만·같은 프로젝트·메인만·60초 안에만 수락 (10-08). 끝나면 표를 지운다."""
    msg = f"This SQL includes destructive operations. Run it on project {PROJECT}?"
    out = []
    try:
        TICKET.unlink(missing_ok=True)
        out.append(("표 없음 → 거절", elicit(MAIN, msg) == "decline"))
        run_hook("guard_sql.py", MAIN, "mcp__supabase-rw__execute_sql", SQL_OK)
        out.append(("메인 승인 SQL → 표 남김", TICKET.exists()))
        out.append(("표 있음·같은 프로젝트 → 수락", elicit(MAIN, msg) == "accept"))
        out.append(("한 번 쓰면 지운다 → 다음은 거절", elicit(MAIN, msg) == "decline"))
        run_hook("guard_sql.py", MAIN, "mcp__supabase-rw__execute_sql", SQL_OK)
        out.append(("다른 프로젝트 → 거절", elicit(MAIN, msg.replace(PROJECT, "other")) == "decline"))
        run_hook("guard_sql.py", MAIN, "mcp__supabase-rw__execute_sql", SQL_OK)
        out.append(("서브에이전트의 확인 창 → 거절", elicit(GENERAL, msg) == "decline"))
        TICKET.write_text(json.dumps({"at": 0, "project_id": PROJECT}), encoding="utf-8")
        out.append(("오래된 표 → 거절", elicit(MAIN, msg) == "decline"))
        run_hook("guard_sql.py", GENERAL, "mcp__supabase-rw__execute_sql", SQL_OK)
        out.append(("서브에이전트 승인 SQL → 표 없음", not TICKET.exists()))
        run_hook("guard_sql.py", MAIN, "mcp__supabase-rw__execute_sql", {"project_id": PROJECT, "query": "drop table t"})
        out.append(("승인 없는 SQL → 표 없음", not TICKET.exists()))
    finally:
        TICKET.unlink(missing_ok=True)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    passed = failed = 0
    for hook, cases in (("confirm_run.py", RUN_CASES), ("guard_paths.py", GUARD_CASES),
                        ("guard_paths.py", SECRET_CASES), ("guard_secrets.py", SECRET_WRITE_CASES),
                        ("guard_sql.py", SQL_CASES)):
        print(f"▶ {hook}")
        for who, tool, command, want in cases:
            got = run_hook(hook, who, tool, command)
            ok = got == want
            passed += ok
            failed += not ok
            label = who.get("agent_type", "main")
            shown = str(command).replace("\n", "⏎")
            print(f"  {'통과' if ok else '실패'}  exit {got} (기대 {want})  {label}/{tool}  {shown}")
    print("▶ sql_elicit.py (guard_sql 의 표로 MCP 확인 창에 답한다)")
    for label, ok in elicit_cases():
        passed += ok
        failed += not ok
        print(f"  {'통과' if ok else '실패'}  {label}")
    print("▶ check_rpa_rules.py")
    for index, (source, want) in enumerate(RULE_CASES):
        path = ROOT / "logs" / f"_probe_rules_{index}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(source, encoding="utf-8")
        try:
            got = run_hook("check_rpa_rules.py", MAIN, "Write", {"file_path": str(path)})
        finally:
            path.unlink(missing_ok=True)
        ok = got == want
        passed += ok
        failed += not ok
        print(f"  {'통과' if ok else '실패'}  exit {got} (기대 {want})  {source.strip()[:40]}")
    print(f"결과: 통과 {passed} / 실패 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
