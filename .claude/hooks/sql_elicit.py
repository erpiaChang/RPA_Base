"""Elicitation 훅 (supabase-rw): Supabase MCP 의 파괴적 SQL 확인 창에 대신 답한다 (2026-10-08).

VS Code 확장은 그 창을 띄우지 못하고 바로 거절한다 (10-08 실측). 이 훅에는 SQL 본문이 오지 않는다.
그래서 guard_sql 이 메인 + APPROVED-SQL 을 통과시킬 때 남긴 표(logs/_sql_ticket.json, 한 번 쓰고 지운다)가
TICKET_SEC 안에 있고 같은 프로젝트일 때만 수락, 그 밖에는 거절.
"""
import json
import os
import sys
import time

TICKET_SEC = 60


def ticket_path():
    return os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", "."), "logs", "_sql_ticket.json")


def decide(payload, ticket, now):
    if payload.get("agent_id") or payload.get("agent_type"):
        return "decline"
    if not ticket or now - float(ticket.get("at", 0)) > TICKET_SEC:
        return "decline"
    project = str(ticket.get("project_id", ""))
    if not project or project not in str(payload.get("message", "")):
        return "decline"
    return "accept"


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        payload = {}
    path = ticket_path()
    try:
        with open(path, encoding="utf-8") as f:
            ticket = json.load(f)
        os.remove(path)                       # 한 번만 쓴다
    except (OSError, ValueError):
        ticket = None
    action = decide(payload, ticket, time.time())
    out = {"hookEventName": "Elicitation", "action": action}
    if action == "accept":
        out["content"] = {}
    print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
