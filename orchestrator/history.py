r"""지난 실행 이력 — 실행이 끝날 때마다 한 줄씩 `logs/history.jsonl` 에 남긴다 (사용자 확정 2026-09-22).

리포트(`orchestrator/report.py`)는 실행마다 파일이 따로라 지난 실행을 한눈에 볼 곳이 없었다.
전체 로그를 다시 읽지 않는다 — 끝나는 자리(`gui/erpia_app._run_worker`, `tools/test_full`)에서
**이미 손에 있는 것**(요약·단계 결과·확인할 것·리포트 경로)만 적는다.

한 줄이 JSON 하나다. 창(`gui/history_window.py`)이 읽고, 나중에 웹 대시보드가 읽을 수 있게
구조를 둔다 (회의록 4번 — 홈페이지에서 이력 보기). 이 파일은 기능 이름을 모른다 — 어느
배포본에나 들어간다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from config.settings import LOG_DIR
from orchestrator import steps
from utils.logger import get_logger

log = get_logger(__name__)

HISTORY_PATH = LOG_DIR / "history.jsonl"
KEEP = 500          # 이보다 많으면 오래된 줄부터 버린다
# 건너뜀(GUI 스레드)과 실행 끝(작업 스레드)이 같은 파일을 읽고 고쳐 쓴다
_LOCK = threading.Lock()

DONE, UNFINISHED, FAILED, CANCELLED = "done", "unfinished", "failed", "cancelled"
SKIPPED = "skipped"     # 건너뛴 예약 회차 (10-01 부터 남긴다 — 전에는 앱을 끄면 사라졌다). 같은 이유가 이어지면 한 줄
STATE_LABELS = {DONE: "완료", UNFINISHED: "완료 (끝나지 않은 단계 있음)",
                FAILED: "실패", CANCELLED: "중단", SKIPPED: "건너뜀"}


def state_of(summary: str, step_rows: list[dict]) -> str:
    """끝난 모양 하나. 요약 첫머리(`_run_worker` 의 outcome)와 단계 상태로 가른다."""
    if summary.startswith("중단됨"):
        return CANCELLED
    if summary.startswith("실패"):
        return FAILED
    # ERPia 알림을 닫고 넘어간 단계(09-22)·메일이 멈추고 넘어간 단계(09-30)는 실패로 남지만 실행은 끝까지 간다
    if any(row.get("state") == steps.FAILED for row in step_rows):
        return UNFINISHED
    return DONE


def record(*, summary: str, step_rows: list[dict], attention: list[str], trigger: str,
           report: Path | str | None, modules: list[str] = (), started_at: float | None = None,
           path: Path | None = None) -> dict | None:
    """한 줄 남긴다. **실패해도 예외를 올리지 않는다** — 이력 때문에 실행이 실패로 끝나면 안 된다."""
    path = path or HISTORY_PATH
    finished = time.time()
    if started_at is None:
        starts = [row.get("started_at") for row in step_rows if row.get("started_at")]
        started_at = min(starts) if starts else finished
    entry = {
        "finished_at": finished,
        "started_at": started_at,
        "elapsed": round(max(finished - started_at, 0.0), 1),
        "trigger": trigger,
        "modules": list(modules),
        "state": state_of(summary, step_rows),
        "summary": (summary or "").splitlines()[0] if summary else "",
        "attention": len(attention),
        "report": str(report) if report else "",
        "steps": [{"id": row.get("id"), "name": row.get("name"), "state": row.get("state")}
                  for row in step_rows],
    }
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim(path)
    except OSError as exc:
        log.warning("실행 이력을 쓰지 못했다(계속): %s", exc)
        return None
    return entry


def record_skipped(planned_at: float, reason: str, modules: list[str] = (),
                   path: Path | None = None) -> dict | None:
    """건너뛴 예약 회차 한 줄. **바로 앞 줄이 같은 이유·같은 기능의 건너뜀이면 그 줄의 횟수만 올린다** —
    밤새 잠긴 화면(간격 실행이면 수십 회)이 실제 실행 기록을 밀어내지 않게. 실패해도 예외를 올리지 않는다."""
    path = path or HISTORY_PATH
    summary = (reason or "").splitlines()[0] if reason else ""
    try:
        with _LOCK:
            lines = _lines(path)
            last = _parse(lines[-1]) if lines else None
            if (last is not None and last.get("state") == SKIPPED and last.get("summary") == summary
                    and last.get("modules") == list(modules)):
                last["count"] = int(last.get("count") or 1) + 1
                last["finished_at"] = planned_at
                lines[-1] = json.dumps(last, ensure_ascii=False)
                _rewrite(path, lines)
                return last
            entry = {"finished_at": planned_at, "started_at": planned_at, "elapsed": 0, "trigger": "예약 실행",
                     "modules": list(modules), "state": SKIPPED, "summary": summary, "attention": 0,
                     "report": "", "steps": [], "count": 1}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim(path)
            return entry
    except OSError as exc:
        log.warning("건너뛴 예약을 이력에 쓰지 못했다(계속): %s", exc)
        return None


def _parse(line: str) -> dict | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _lines(path: Path) -> list[str]:
    """깨진 바이트는 바꿔 읽는다 — 그 줄만 못 읽고(`_parse` 가 버린다) 파일 전체는 읽힌다."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []


def _rewrite(path: Path, lines: list[str]) -> None:
    """통째로 다시 쓸 때는 임시 파일에 쓰고 바꿔 끼운다 — 쓰다 꺼져도 기록이 비지 않는다."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _trim(path: Path) -> None:
    lines = _lines(path)
    if len(lines) > KEEP:
        _rewrite(path, lines[-KEEP:])


def load(limit: int = 100, path: Path | None = None) -> list[dict]:
    """최근 것부터. 깨진 줄은 건너뛴다 (한 줄 때문에 표가 비면 안 된다)."""
    path = path or HISTORY_PATH
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in _lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            log.warning("실행 이력에 읽을 수 없는 줄이 있다(건너뜀): %s", line[:60])
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return list(reversed(out))[:limit]


def clock(stamp: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(stamp)) if stamp else ""


def took(seconds: float | None) -> str:
    if not seconds:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}분 {secs}초" if minutes else f"{secs}초"


def row_of(entry: dict) -> tuple[str, str, str, str, str, str]:
    """표 한 줄 — (시각, 어떻게, 기능, 결과, 걸린 시간, 확인할 것)."""
    state = STATE_LABELS.get(entry.get("state") or "", entry.get("state") or "")
    summary = entry.get("summary") or ""
    attention = entry.get("attention") or 0
    # 요약이 이미 "실패 — ..." 로 시작하면 상태를 또 붙이지 않는다 ("실패 — 실패 — ..." 로 보였다, 09-29)
    result = summary if summary.startswith(state) else f"{state} — {summary}" if summary else state
    count = entry.get("count") or 1
    if count > 1:                       # 이어진 건너뜀 (10-01) — 처음~마지막 예약 시각
        first, last = (clock(entry.get(key))[-5:] for key in ("started_at", "finished_at"))
        result += f" ({count}회, {first}~{last})"
    return (clock(entry.get("finished_at")), entry.get("trigger") or "",
            ", ".join(entry.get("modules") or []) or "전체",
            result,
            took(entry.get("elapsed")), f"{attention}건" if attention else "")
