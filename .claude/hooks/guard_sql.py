"""PreToolUse 훅 (mcp__supabase-rw__*): **실서버 SQL 실행은 사용자가 결정한 것만** 통과시킨다 (2026-09-28).

사용자 확정 09-28: 스키마 SQL 을 Claude 가 MCP 로 직접 적용하되, **적용 전에 사용자의 결정을 받는다.**
`execute_sql` / `apply_migration` 은 SQL 본문에 `-- APPROVED-SQL: <사유>` 가 있어야 통과한다
(`confirm_run` 의 `APPROVED-RUN` 과 같은 관례 — 사용자가 승인한 뒤에만 붙인다). 서브에이전트는 붙여도 막는다.
읽기 도구(list_tables, get_advisors, get_logs 등)는 그냥 통과한다. 파괴적 도구는 settings 의 deny 가 막는다.
통과시킬 때 표(`logs/_sql_ticket.json`)를 남긴다 — drop 이 든 SQL 의 MCP 확인 창은 `sql_elicit.py` 가 그 표로 답한다 (10-08).
"""
import json
import os
import re
import sys
import time

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass  # 재설정 불가한 스트림. 검사 자체는 계속한다.

WRITE_TOOLS = {"execute_sql", "apply_migration"}
APPROVED = re.compile(r"--\s*APPROVED-SQL:\s*\S+")
TICKET = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", "."), "logs", "_sql_ticket.json")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = str(payload.get("tool_name", ""))
    name = tool.rsplit("__", 1)[-1]
    if name not in WRITE_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    sql = str(tool_input.get("query", "")) + "\n" + str(tool_input.get("sql", ""))
    agent = payload.get("agent_id") or payload.get("agent_type")
    if APPROVED.search(sql) and agent is None:
        # 확인 창 표 (sql_elicit.py) — 이 호출의 파괴적 SQL 확인에 60초 안에 한 번만 쓰인다 (10-08)
        try:
            with open(TICKET, "w", encoding="utf-8") as f:
                json.dump({"at": time.time(), "project_id": str(tool_input.get("project_id", ""))}, f)
        except OSError as e:
            print(f"[확인 창 표를 못 남김] {e} — drop 이 든 SQL 은 거절된다", file=sys.stderr)
        return 0
    print("[실서버 SQL 확인 필요] 사용자가 결정하기 전에는 실행하지 않는다.\n"
          "  SQL 을 보여 주고 승인을 받은 뒤, 본문 첫 줄에 `-- APPROVED-SQL: <사유>` 를 붙여 다시 실행할 것.\n"
          "  서브에이전트는 실행할 수 없다 (메인만).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
