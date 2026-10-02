r"""[확인 도구 - 읽기 전용] 지난 실행 이력(`orchestrator/history.py`) + [지난 실행] 창.

    .venv\Scripts\python.exe -m tools.probe_history

이력은 임시 파일(`logs/_probe_history.jsonl`)에 쓰고 끝에 지운다. 창은 띄우지 않는다(withdraw).
대상 프로그램을 건드리지 않는다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from config.settings import LOG_DIR  # noqa: E402
from orchestrator import history, steps  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
TEMP = LOG_DIR / "_probe_history.jsonl"


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def _steps(*states):
    return [{"id": f"s{i}", "name": f"단계{i}", "state": s, "started_at": 100.0 + i}
            for i, s in enumerate(states, 1)]


def check_record_load() -> list[bool]:
    log.info("▶ 기록·읽기")
    out = []
    TEMP.unlink(missing_ok=True)
    done = history.record(summary="메일 엑셀 1개 받음 / 전표 1건 생성", step_rows=_steps(steps.DONE, steps.DONE),
                          attention=["a"], trigger="직접 실행", report=Path("logs/report_x.html"),
                          modules=["메일 엑셀 받기", "물류관리"], started_at=1000.0, path=TEMP)
    history.record(summary="실패 — 로그인이 안 됐습니다", step_rows=_steps(steps.DONE, steps.FAILED),
                   attention=[], trigger="예약 실행", report=None, path=TEMP)
    history.record(summary="중단됨 — 사용자가 멈췄다", step_rows=_steps(steps.DONE, steps.CANCELLED),
                   attention=[], trigger="직접 실행", report="", path=TEMP)
    history.record(summary="끝나지 않은 단계: 물류대기 / ...", step_rows=_steps(steps.DONE, steps.FAILED, steps.DONE),
                   attention=["x", "y"], trigger="콘솔 실행", report="", path=TEMP)
    out.append(check("기록이 dict 로 돌아오고 걸린 시간·확인할 것 수가 든다",
                     done is not None and done["elapsed"] > 0 and done["attention"] == 1
                     and done["state"] == history.DONE, str(done)))
    loaded = history.load(path=TEMP)
    out.append(check("최근 것부터 4건", len(loaded) == 4 and loaded[0]["trigger"] == "콘솔 실행"))
    states = [e["state"] for e in loaded]
    out.append(check("끝난 모양 — 완료 / 실패 / 중단 / 끝나지 않은 단계 있음",
                     states == [history.UNFINISHED, history.CANCELLED, history.FAILED, history.DONE], str(states)))
    out.append(check("시작 시각이 없으면 단계 시작 시각 중 가장 이른 것",
                     loaded[2]["started_at"] == 101.0, str(loaded[2]["started_at"])))
    row = history.row_of(loaded[3])
    out.append(check("표 한 줄 — 기능 이름·결과·확인할 것",
                     row[1] == "직접 실행" and row[2] == "메일 엑셀 받기, 물류관리"
                     and row[3].startswith("완료 — 메일 엑셀") and row[5] == "1건", str(row)))
    out.append(check("기능이 없으면 '전체'", history.row_of(loaded[2])[2] == "전체"))
    out.append(check("걸린 시간 표기", history.took(115) == "1분 55초" and history.took(7) == "7초"
                     and history.took(0) == ""))

    with TEMP.open("a", encoding="utf-8") as fh:
        fh.write("이건 JSON 이 아니다\n")
    out.append(check("깨진 줄은 건너뛰고 나머지를 읽는다", len(history.load(path=TEMP)) == 4))

    keep = history.KEEP
    try:
        history.KEEP = 3
        history.record(summary="x", step_rows=[], attention=[], trigger="직접 실행", report="", path=TEMP)
        lines = [l for l in TEMP.read_text(encoding="utf-8").splitlines() if l.strip()]
        out.append(check("상한을 넘으면 오래된 줄부터 버린다", len(lines) == 3 and '"summary": "x"' in lines[-1],
                         str(len(lines))))
    finally:
        history.KEEP = keep
    out.append(check("이력 파일은 정리 훅이 지우라고 하지 않는 이름(.jsonl)",
                     history.HISTORY_PATH.suffix == ".jsonl"))
    return out


def check_window() -> list[bool]:
    log.info("▶ [지난 실행] 창")
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return []
    root.withdraw()
    out = []
    try:
        from gui.history_window import HistoryWindow

        window = HistoryWindow(root, loader=lambda limit: history.load(limit, path=TEMP))
        window.top.withdraw()
        # 위에서 상한 3으로 잘랐다 — 남은 것은 [끝나지 않음, 깨진 줄, x] 이라 읽히는 줄은 2개
        rows = [window.tree.item(i, "values") for i in window.tree.get_children()]
        out.append(check("표에 최근 것부터 줄이 들어간다", len(rows) == 2 and rows[0][3] == "완료 — x"
                         and rows[1][3].startswith("완료 (끝나지 않은 단계 있음)"), str(rows)))
        out.append(check("끝나지 않음 줄에 색 태그",
                         history.UNFINISHED in window.tree.item(window.tree.get_children()[1], "tags")))
        empty = HistoryWindow(root, loader=lambda limit: [])
        empty.top.withdraw()
        out.append(check("기록이 없으면 그렇다고 적는다", "없습니다" in empty.note_var.get()))

        from gui.run_app import TAB_HISTORY, RunWindow
        from utils import autorun
        run_window = RunWindow(root)
        run_window.open_history()
        out.append(check("실행 창은 [실행 기록] 탭 — 예전 [지난 실행] 이 여기로 온다 (09-29)",
                         run_window.tabs.select() == str(run_window.history_tab)
                         and TAB_HISTORY in run_window.tabs.tab(run_window.history_tab, "text")))
        # 건너뛴 예약은 기록 파일에 남는다 (10-01 — 전에는 앱을 끄면 사라졌다)
        run_window.history_pane.loader = lambda limit: history.load(limit, path=TEMP)
        keep_path = history.HISTORY_PATH
        history.HISTORY_PATH = TEMP
        try:
            run_window.autorunner = autorun.AutoRunner(
                run_window.autorun_pane.plan(), start=lambda: None, busy=lambda: False, locked=lambda: True,
                on_skip=run_window._on_autorun_skipped)
            first = time.time()
            run_window.autorunner._skip(first, "화면이 잠겨 있다")
            rows = [run_window.history_pane.tree.item(i, "values") for i in run_window.history_pane.tree.get_children()]
            out.append(check("★ 건너뛴 예약이 기록 파일에 남고 같은 표 맨 위에 들어간다",
                             rows and rows[0][3] == "건너뜀 — 화면이 잠겨 있다" and len(rows) == 3, str(rows[:2])))
            run_window.autorunner._skip(first + 600, "화면이 잠겨 있다")
            rows = [run_window.history_pane.tree.item(i, "values") for i in run_window.history_pane.tree.get_children()]
            out.append(check("★ 같은 이유로 이어진 건너뜀은 한 줄에 횟수로 (실제 실행 기록을 밀어내지 않게)",
                             len(rows) == 3 and "(2회, " in rows[0][3], str(rows[:1])))
            history.record(summary="x", step_rows=[], attention=[], trigger="직접 실행", report="", path=TEMP)
            run_window.autorunner._skip(first + 1200, "화면이 잠겨 있다")
            out.append(check("사이에 실행이 있으면 건너뜀은 새 줄", len(history.load(path=TEMP)) == 5))
            run_window._refresh_last()
            out.append(check("홈 '마지막 실행' 은 건너뜀이 아닌 실제 실행", "건너뜀" not in run_window.last_var.get(),
                             run_window.last_var.get()))
        finally:
            history.HISTORY_PATH = keep_path
            run_window.autorunner = None
        out.append(check("실행용 창은 기능 이름을 이력에 넘긴다",
                         run_window._module_names() and all(isinstance(n, str) for n in run_window._module_names())))
    finally:
        root.destroy()
    return out


def main() -> int:
    setup_logging()
    from tools import probe_guard

    try:
        # 실행 창을 만들면 그것만으로 서버 확인이 나간다 (09-28)
        with probe_guard.offline():
            results = [*check_record_load(), *check_window()]
    finally:
        TEMP.unlink(missing_ok=True)
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
