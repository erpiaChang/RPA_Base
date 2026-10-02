r"""[조사 도구 - 읽기 전용] 진행 파이프라인이 **제대로 그려지는지** 확인한다.

우리 tkinter 창만 만든다. 대상 프로그램을 건드리지 않는다.

    .venv\Scripts\python.exe -m tools.probe_pipeline          확인만 한다
    .venv\Scripts\python.exe -m tools.probe_pipeline --show   눈으로 보게 띄운다

## 왜 이 도구가 있나 (2026-09-16)

오버레이를 화면에서 찍어 보는 판정(`tools/probe_overlay.py`)은 **화면이 잠겨
있으면 할 수 없다.** 실제로 잠금 화면이 맨 위에 있어 판정을 건너뛴 적이 있다.

이 도구는 화면을 보지 않는다. **Canvas 에 무엇을 그렸는지 직접 읽는다** —
칸이 몇 개인지, 어떤 글자가 들어갔는지, 폭이 좁아지면 줄이 나뉘는지.
그리는 논리가 맞는지는 이것으로 다 확인할 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from gui.pipeline import (DARK, FULL, HUD, LIGHT,  # noqa: E402
                          PipelineView, TableView)
from orchestrator.steps import (DONE, PENDING, RUNNING,  # noqa: E402
                                SKIPPED, StepEvent)
from utils.dpi import ensure_dpi_awareness  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

TABLE_COLUMNS = ("사이트", "결과", "시도", "파일", "오류")
TABLE_ROWS = (
    ("사이트C", "성공", "1", "사이트C_1.xlsx", ""),
    ("사이트B", "실패", "2", "사이트B_2.xlsx", "파일 선택 창을 찾지 못했다"),
    ("사이트A", "성공", "1", "사이트A_3.xlsx", ""),
)

SHAPE = [
    ("사이트 로그인", DONE, "user01", 8.0),
    ("주문매핑 매출처리", DONE, "업로드 2/3건 (실패: 사이트B)", 48.4),
    ("자동수집", SKIPPED, "고르지 않았다", 0.0),
    ("매출처리", RUNNING, "미매출 주문수 4건", 12.0),
    ("물류대기", PENDING, "", 0.0),
    ("물류관리", PENDING, "", 0.0),
]


def events(when: float | None = None) -> list:
    """가짜 단계 사진들. 상태가 섞인 화면을 일부러 만든다."""
    when = when or time.time()
    out = []
    for position, (name, state, detail, elapsed) in enumerate(SHAPE, start=1):
        started = when - 300 + position * 30
        out.append(StepEvent(
            step_id=f"s{position}", name=name, index=position, total=len(SHAPE),
            state=state, detail=detail, elapsed=elapsed,
            # 대기 칸은 아직 시작하지 않았다. 실제 흐름도 0 을 넣는다.
            started_at=0.0 if state == PENDING else started,
            finished_at=0.0 if state in (RUNNING, PENDING) else started + elapsed,
            # 두 번째 칸에만 상세 표를 준다. 표가 있는 칸과 없는 칸을
            # 둘 다 확인해야 한다.
            columns=TABLE_COLUMNS if position == 2 else (),
            rows=TABLE_ROWS if position == 2 else ()))
    return out


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def _drawn(root, view: PipelineView, width: int) -> tuple[list, int]:
    """창을 그 폭으로 만든 뒤 다시 그리고, 그려진 글자와 칸 수를 돌려준다.

    ★ 캔버스에 `configure(width=...)` 만 하면 안 된다. grid 가 칸 너비에
      맞춰 도로 늘리거나 줄여서, 우리가 지정한 폭으로 그려지지 않는다.
      **창을 넓혀야** 캔버스가 실제로 그 폭이 된다.
    """
    root.geometry(f"{width + 40}x460")
    root.update()
    view._width = 0          # 폭이 바뀌었으니 다시 그리게 한다
    view._draw()
    root.update_idletasks()
    return view.drawn(), view.markers()


def _empty_says_so(table: TableView, root) -> bool:
    """상세가 없는 칸에서 빈 표만 남기지 않는지."""
    table.show("4/6 매출처리", (), ())
    root.update()
    said = any("없습니다" in text for text in table.drawn())
    table.show("2/6 주문매핑 매출처리", TABLE_COLUMNS, TABLE_ROWS)
    root.update()
    return said


def _truncates(table: TableView, root) -> bool:
    """높이가 모자랄 때 **몇 줄이 남았는지 말하는지.**

    말하지 않으면 사람이 **전부 본 줄 안다.** 그게 더 나쁘다.
    """
    many = tuple(TABLE_ROWS) * 20
    table.canvas.configure(height=120)
    root.update()
    table.show("많은 줄", TABLE_COLUMNS, many)
    root.update()
    said = any("더 있습니다" in text for text in table.drawn())
    shown = table.visible_rows()
    log.info("      (보이는 줄 %d / 전체 %d)", shown, len(many))
    return said and 0 < shown < len(many)


def _text_widths(view: PipelineView) -> list:
    """그려진 글자마다 (내용, 실제 픽셀 너비)."""
    out = []
    for shape in view.canvas.find_all():
        if view.canvas.type(shape) != "text":
            continue
        box = view.canvas.bbox(shape)
        if box:
            out.append((view.canvas.itemcget(shape, "text"),
                        box[2] - box[0]))
    return out


def _check_stages(root) -> list[bool]:
    """큰 단계 넷 + 그 아래 세부 단계 (09-29 사용자 요청). 계획은 진짜 `modules.plan` 으로 만든다."""
    from dataclasses import replace

    from gui.pipeline import FAILED, group_state
    from orchestrator import modules, steps
    from orchestrator.common import Hooks

    log.info("▶ 큰 단계 (09-29)")
    out = []
    plan = modules.plan(list(modules.ALL), ["excel", "site"])
    planned = steps.preview(plan)
    by_id = {event.step_id: event for event in planned}
    order = list(dict.fromkeys(event.stage for event in planned))
    out.append(check("★ 11단계가 큰 단계 넷으로 묶인다 (모듈 이름·실행 순서)",
                     len(plan) == 11 and order == [module.name for module in modules.ALL], str(order)))
    out.append(check("ERPia 실행·로그인은 주문수집·매출처리 안에 든다",
                     by_id["launch"].stage == by_id["login"].stage == modules.ORDERS.name))
    out.append(check("★ 매출처리는 2/4 다 (9/11 이 아니다)",
                     by_id["sales"].where == "2/4" and by_id["sales"].one_line().startswith("2/4 매출처리"),
                     by_id["sales"].one_line()))
    only = steps.preview(modules.plan([modules.LOGI_WAIT, modules.LOGI], []))
    out.append(check("물류만 고르면 실행·로그인은 물류대기에, 큰 단계는 둘",
                     [e.stage for e in only][:2] == [modules.LOGI_WAIT.name] * 2
                     and {e.stage_total for e in only} == {2}, str([(e.name, e.where) for e in only])))
    flat = steps.preview([replace(item, stage="") for item in plan])
    out.append(check("큰 단계가 없는 계획은 세부 번호 그대로 (다른 창)", flat[9].where == "10/11"))

    hooks = Hooks()
    hooks.begin(plan)
    run = next(r for r in hooks.runs if r.step.id == "sales")
    out.append(check("흐름의 기록도 큰 단계 번호를 든다 (사용자 로그 [2/4])",
                     run.where == "2/4" and run.event().stage_total == 4, run.where))

    done = {"preflight", "mail_login", "mailbox", "launch", "login", "screen", "excel"}
    states = {**{step_id: DONE for step_id in done}, "site": SKIPPED, "sales": RUNNING}
    shown = [replace(event, state=states.get(event.step_id, PENDING)) for event in planned]
    view = PipelineView(root, theme=LIGHT)
    view.grid(row=4, column=0, sticky="ew", padx=10)
    view.show_plan(shown)
    texts, marks = _drawn(root, view, 1000)
    joined = " | ".join(texts)
    out.append(check("★ 동그라미는 큰 단계마다 하나 (넷)", marks == 4, f"{marks}개"))
    out.append(check("큰 단계 이름과 세부 단계 이름이 다 들어간다",
                     all(name in joined for name in order)
                     and all(event.name in joined for event in planned), joined[:120]))
    out.append(check("큰 단계 상태 — 끝남 / 진행 중 · 5/6 / 실행 전",
                     "완료" in texts and "진행 중 · 5/6" in texts and texts.count("실행 전") == 2,
                     str([t for t in texts if "완료" in t or "진행" in t or "실행 전" in t])))
    out.append(check("큰 단계 상태 규칙",
                     group_state([replace(shown[0], state=DONE), replace(shown[0], state=SKIPPED)]) == DONE
                     and group_state([replace(shown[0], state=SKIPPED)]) == SKIPPED
                     and group_state([replace(shown[0], state=DONE), replace(shown[0], state=PENDING)]) == RUNNING
                     and group_state([replace(shown[0], state=FAILED), replace(shown[0], state=RUNNING)]) == FAILED))
    view.set_density(HUD)
    root.update()
    hud = view.drawn()
    out.append(check("★ HUD — 큰 단계 이름·2 / 4·세부 단계 이름",
                     modules.ORDERS.name in hud and "2 / 4" in hud and any(t.startswith("매출처리") for t in hud),
                     str(hud)))
    view.canvas.destroy()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="진행 파이프라인 확인 (읽기 전용)")
    parser.add_argument("--show", action="store_true",
                        help="창을 띄워 눈으로 보게 한다 (닫을 때까지 머문다)")
    args = parser.parse_args()

    setup_logging()
    ensure_dpi_awareness()
    results: list[bool] = []

    root = tk.Tk()
    root.title("[조사] 진행 파이프라인")
    root.geometry("900x420")
    root.columnconfigure(0, weight=1)

    sample = events()
    light = PipelineView(root, theme=LIGHT)
    light.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
    dark = PipelineView(root, theme=DARK)
    dark.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
    for view in (light, dark):
        view.show_plan(sample)
    root.update()

    log.info("▶ 그려진 내용")
    # 6칸이 한 줄에 들어갈 만큼 넓게 잡는다 (칸 최소폭 x 6 + 사이 + 여백).
    texts, cards = _drawn(root, light, 1000)
    joined = " | ".join(texts)
    results.append(check("단계마다 표식을 하나씩 그린다",
                         cards == len(SHAPE),
                         f"표식 {cards}개 / 단계 {len(SHAPE)}개"))
    results.append(check("단계 이름이 모두 들어간다",
                         all(any(name.startswith(text.rstrip("…")) or text.startswith(name[:6])
                                 for text in texts) for name, *_ in SHAPE),
                         joined[:90] + " ..."))
    results.append(check("상태 이름을 한국어로 보여 준다",
                         all(word in joined for word in
                             ("완료", "건너뜀", "진행 중", "실행 전"))))
    # ★ Stepper 는 **아직 안 한 칸에 번호**를 적는다 (끝난 칸은 표식이
    #   결과를 말한다). 4번 칸이 진행 중이므로 "4" 가 있어야 한다.
    results.append(check("아직 안 한 칸에 몇 번째인지 적는다",
                         "4" in texts and "5" in texts and "6" in texts,
                         joined[:70]))

    log.info("▶ 시각")
    # ★ **Stepper 에는 시각을 적지 않는다** (2026-09-16, 외부 검토 의견).
    #   8~11칸이 한 줄에 들어가면 이름·상태·시각 세 줄이 촘촘해져서
    #   무엇을 보는 화면인지 흐려진다. Stepper 의 목적은 진행률이 아니라
    #   **작업 위치 지도**다. 시각은 머리줄과 최근 작업 로그가 들고 있다.
    results.append(check("끝난 칸은 완료라고 말한다", "완료" in texts))
    results.append(check("★ 칸에 **시각을 적지 않는다** (자리를 먹는다)",
                         not any(t.count(":") == 2 for t in texts),
                         next((t for t in texts if t.count(":") == 2), "없다")))
    results.append(check("진행 중인 칸은 그렇다고 말한다",
                         "진행 중" in texts))
    results.append(check("★ 아직 시작하지 않은 칸에 시각을 지어내지 않는다",
                         not any(text.startswith("실행 전 ·") for text in texts),
                         next((t for t in texts if t.startswith("실행 전")), "실행 전")))

    log.info("▶ 폭에 맞춰 줄을 나눈다")
    _drawn(root, light, 1000)
    wide_per_row, _w, wide_rows = light._plan_layout(len(SHAPE))
    narrow_texts, narrow_cards = _drawn(root, light, 420)
    narrow_per_row, _w2, narrow_rows = light._plan_layout(len(SHAPE))
    results.append(check("넓으면 한 줄에 다 놓는다",
                         wide_rows == 1, f"한 줄에 {wide_per_row}개 / {wide_rows}줄"))
    results.append(check("좁으면 줄을 나눈다", narrow_rows > 1,
                         f"한 줄에 {narrow_per_row}개 / {narrow_rows}줄"))
    results.append(check("좁아져도 단계 수는 그대로다",
                         narrow_cards == len(SHAPE),
                         f"표식 {narrow_cards}개"))
    # ★ 진짜로 확인할 것은 "잘렸는가" 가 아니라 **칸 밖으로 넘치지 않는가** 다.
    #   칸 너비는 폭에 따라 달라지므로 "잘렸다" 를 기대하면 창 크기에 따라
    #   결과가 흔들린다 (2026-09-16에 실제로 그랬다).
    _per_row, card_w, _rows = light._plan_layout(len(SHAPE))
    over = [(t, w) for t, w in _text_widths(light) if w > card_w]
    results.append(check("좁아져도 글자가 칸 밖으로 넘치지 않는다",
                         not over, f"칸 너비 {card_w}px / 넘친 글자 {over[:2]}"))
    results.append(check("자를 때는 말줄임표를 붙인다",
                         light._fit("아주아주긴단계이름입니다", 40,
                                    light.font_small).endswith("…")
                         and light._fit("짧다", 400, light.font_small) == "짧다"))

    log.info("▶ 고르기")
    _drawn(root, light, 1000)
    picked: list = []
    light.on_pick = picked.append
    light.select("s4")
    results.append(check("칸을 고르면 그 사진을 돌려준다",
                         light.selected() is not None
                         and light.selected().step_id == "s4"))
    light.select("없는칸")
    results.append(check("없는 칸을 고르면 아무것도 고르지 않은 것이다",
                         light.selected() is None))

    log.info("▶ 어두운 배색")
    dark_texts, dark_cards = _drawn(root, dark, 1000)
    results.append(check("오버레이 배색으로도 같은 내용을 그린다",
                         dark_cards == len(SHAPE) and len(dark_texts) == len(texts),
                         f"칸 {dark_cards}개 / 글자 {len(dark_texts)}개"))

    log.info("▶ 코너 HUD 밀도")
    hud = PipelineView(root, theme=DARK, density=HUD)
    hud.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
    hud.show_plan(sample)
    root.update()
    hud._draw()
    root.update_idletasks()
    texts = hud.drawn()
    joined = " | ".join(texts)
    here = hud.current()
    results.append(check("★ 진행 중인 칸을 고른다 (마지막 칸이 아니다)",
                         here.state == RUNNING,
                         f"{here.index}/{here.total} {here.name} ({here.state})"))
    results.append(check("그 칸의 이름·상태·상세를 글로 쓴다",
                         here.name in joined and "미매출" in joined,
                         joined[:70]))
    results.append(check("★ 대기 칸 이름은 쓰지 않는다 (점 하나)",
                         not any("물류관리" in t for t in texts),
                         "대기 칸 이름이 글자로 나오면 자리를 먹는다"))
    results.append(check("★ 돌고 있다고 말한다 (멈춘 것으로 보이지 않게)",
                         any("자동 조작 중" in t for t in texts),
                         next((t for t in texts if "자동 조작 중" in t), "없다")))
    # ★ 이 칸은 건수를 셀 수 없다(`미매출 주문수 4건` 은 상세 문구일 뿐이다).
    #   그때 **막대를 그리지 않는다** — 전에는 단계 개수로 만든 전체 진행률을
    #   그렸는데 그건 지어낸 숫자였다.
    kinds = [hud.canvas.type(s) for s in hud.canvas.find_all()]
    results.append(check("★ 셀 수 없는 단계에는 **막대를 그리지 않는다**",
                         kinds.count("rectangle") == 0,
                         f"막대 {kinds.count('rectangle')}조각"))
    results.append(check("★ `진행률 없음` 이라고 쓰지 않는다 "
                         "(사람이 멈춘 것으로 읽는다)",
                         not any("진행률 없음" in t for t in texts)
                         and any("건수를 세지 않습니다" in t for t in texts),
                         next((t for t in texts if "세지 않습니다" in t), "")))
    results.append(check("★ 경과 시간이 나온다",
                         any(t.count(":") == 2 for t in texts),
                         next((t for t in texts if t.count(":") == 2), "")))

    log.info("▶ 밀도를 바꿔도 자료는 그대로다")
    hud.set_density(FULL)
    root.update_idletasks()
    results.append(check("전체로 바꾸면 단계가 다 나온다",
                         hud.markers() == len(SHAPE),
                         f"표식 {hud.markers()}개"))
    hud.set_density(HUD)
    root.update_idletasks()
    results.append(check("다시 HUD 로 돌아온다",
                         hud.density == HUD
                         and hud.markers() == 0
                         and hud.current().state == RUNNING))

    log.info("▶ 상세 표 (펼쳤을 때 보여 줄 것)")
    table = TableView(root, theme=DARK)
    table.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
    root.rowconfigure(3, weight=1)
    root.geometry("1040x620")
    root.update()
    table.show("2/6 주문매핑 매출처리  (3건)", TABLE_COLUMNS, TABLE_ROWS)
    root.update()
    drawn = table.drawn()
    joined = " | ".join(drawn)
    results.append(check("컬럼 이름이 다 들어간다",
                         all(name in joined for name in TABLE_COLUMNS),
                         joined[:70]))
    results.append(check("줄의 값이 들어간다",
                         "사이트C_1.xlsx" in joined and "사이트B" in joined))
    results.append(check("세 줄을 다 그린다", table.visible_rows() == 3,
                         f"보이는 줄 {table.visible_rows()} / 전체 "
                         f"{len(TABLE_ROWS)}"))
    results.append(check("★ 상세가 없으면 없다고 말한다",
                         _empty_says_so(table, root)))
    results.append(check("★ 자리가 모자라면 **잘렸다고 말한다**",
                         _truncates(table, root)))

    results += _check_stages(root)

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    if args.show:
        log.info("창을 닫으면 끝난다.")
        root.mainloop()
    else:
        root.destroy()
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
