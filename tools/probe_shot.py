r"""[조사 도구 - 읽기 전용] 오버레이를 **그림 파일로 찍는다.**

대상 프로그램을 건드리지 않는다. 우리 창만 띄웠다 닫는다.

    .venv\Scripts\python.exe -m tools.probe_shot
    .venv\Scripts\python.exe -m tools.probe_shot --out logs
    .venv\Scripts\python.exe -m tools.probe_shot --stages     큰 단계가 붙은 계획 (실행용 창 모양, 끝난 결과 띠 포함)

## 왜 이 도구가 있나 (2026-09-16)

지금까지 대시보드를 여러 번 고치면서 **한 번도 눈으로 본 적이 없다.**
확인 도구는 "글자가 있는가 / 대비가 몇 대 몇인가" 를 셀 뿐이라,
`글자가 다 있고 대비도 맞는데 보기에 이상한` 상태를 잡지 못한다.

실제로 그런 일이 여러 번 났다 — 카드가 통째로 투명해진 것, 단추 자리가
움직인 것, 글귀가 잘린 것 전부 **사용자가 화면을 보고** 알려 줬다.

이 도구는 그 사이를 메운다. 화면을 PNG 로 남기면 사람도 보고, 다음에
고칠 때 **전후를 나란히 놓고** 비교할 수 있다.

★ 찍은 그림은 `logs/` 에 둔다. **산출물이 아니라 중간 파일**이라 보고 나면
  지운다 (`CLAUDE.md` 의 "로그 / 임시 산출물").
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from gui.overlay import Overlay  # noqa: E402
from utils.dpi import ensure_dpi_awareness  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)


def _grab(box=None):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=box, all_screens=False)


def _geometry(overlay) -> tuple:
    """두 창의 자리와 크기. 이것이 두 번 같으면 자리를 잡은 것이다."""
    out = []
    for window in (overlay.status, overlay.bar):
        window.update_idletasks()
        out.append((window.winfo_rootx(), window.winfo_rooty(),
                    window.winfo_width(), window.winfo_height(),
                    bool(window.winfo_viewable())))
    return tuple(out)


def _settled(root, overlay, timeout: float = 3.0) -> bool:
    """창이 **더 움직이지 않을 때까지** 기다린다. 상한이 있다.

    `wait_for` 를 쓰지 않는 이유: 그쪽은 중단 토큰을 보고 로그를 남기는
    업무용 대기다. 여기서는 tkinter 를 계속 돌려 줘야(`update()`) 창이
    실제로 움직이므로 루프를 직접 돈다.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        root.update()
        now = _geometry(overlay)
        if now == last:
            return True
        last = now
    log.warning("창이 %.1f초 안에 자리를 잡지 않았다. 그대로 찍는다.", timeout)
    return False


def _shot(root, overlay, name: str, out: Path, whole: bool = False) -> Path:
    """지금 화면을 찍는다. `whole` 이면 화면 전체, 아니면 카드 + 막대만."""
    # ★ 고정 대기를 쓰지 않는다. **창이 실제로 그 자리에 놓였는지**를 보고
    #   기다린다 — 자리를 잡기 전에 찍으면 옛 화면이 남는다.
    _settled(root, overlay)

    box = None
    if not whole:
        overlay.status.update_idletasks()
        overlay.bar.update_idletasks()
        boxes = []
        for window in (overlay.status, overlay.bar):
            if window.winfo_viewable():
                x, y = window.winfo_rootx(), window.winfo_rooty()
                boxes.append((x, y, x + window.winfo_width(),
                              y + window.winfo_height()))
        if boxes:
            pad = 12
            box = (min(b[0] for b in boxes) - pad,
                   min(b[1] for b in boxes) - pad,
                   max(b[2] for b in boxes) + pad,
                   max(b[3] for b in boxes) + pad)

    image = _grab(box)
    path = out / f"shot_{name}.png"
    image.save(path)
    log.info("  찍었다: %s  (%dx%d)", path.name, image.width, image.height)
    return path


# `--stages` — 가짜 9칸을 앞에서부터 큰 단계로 나눈다 (실행용 창의 계획 모양, 09-29)
STAGES = (("주문수집·매출처리", 5), ("물류대기", 1), ("물류관리", 3))


def _staged(plan: list) -> list:
    from dataclasses import replace

    out, position = [], 0
    for number, (name, size) in enumerate(STAGES, start=1):
        out += [replace(event, stage=name, stage_index=number, stage_total=len(STAGES))
                for event in plan[position:position + size]]
        position += size
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="오버레이를 그림으로 찍는다")
    parser.add_argument("--out", default="logs", help="저장할 폴더")
    parser.add_argument("--stages", action="store_true", help="큰 단계가 붙은 계획으로 찍는다 (파일 이름 앞 stage_)")
    args = parser.parse_args()
    prefix = "stage_" if args.stages else ""

    setup_logging()
    ensure_dpi_awareness()

    try:
        import PIL  # noqa: F401
    except ImportError:
        log.error("Pillow 가 없어 찍을 수 없다. requirements.txt 참고.")
        return 1

    import tkinter as tk

    from tools.probe_overlay import fake_plan

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 아래에 깔릴 창. 실제로는 대상 프로그램 자리다 — 밝은 회색으로 둔다.
    under = tk.Tk()
    under.title("[조사] 아래 창 (대상 프로그램 자리)")
    under.geometry("1600x900+100+60")
    under.configure(bg="#f2f2f2")
    tk.Label(under, bg="#f2f2f2", fg="#555555",
             text="여기가 대상 프로그램 자리입니다",
             font=("Segoe UI", 20)).pack(expand=True)
    under.update()

    overlay = Overlay(under, on_stop=lambda: None, title="주문 자동화",
                      on_report=lambda: None)
    plan = _staged(fake_plan()) if args.stages else fake_plan()
    overlay.show_plan(plan)
    for event in plan:
        overlay.update(event)

    made = []
    log.info("▶ 접힘 (HUD) — 평소 모습")
    overlay.set_expanded(False)
    made.append(_shot(under, overlay, f"{prefix}1_hud", out))

    log.info("▶ 펼침 — 상세 표가 **있는** 칸")
    overlay.set_expanded(True)
    overlay.pipeline.select("s3")
    overlay._on_pick(overlay.pipeline.selected())
    made.append(_shot(under, overlay, f"{prefix}2_full_table", out))

    log.info("▶ 펼침 — 상세 표가 **없는** 칸 (칸이 접히는지 본다)")
    overlay.pipeline.select("s6")
    overlay._on_pick(overlay.pipeline.selected())
    made.append(_shot(under, overlay, f"{prefix}3_full_notable", out))

    log.info("▶ 실패로 끝났을 때")
    overlay.set_busy(False)
    overlay.finish("failed", "", "ScreenError: 조회 버튼을 찾지 못했다")
    made.append(_shot(under, overlay, f"{prefix}4_failed", out))

    log.info("▶ 화면 전체 — 얼마나 덮는지")
    made.append(_shot(under, overlay, f"{prefix}5_whole", out, whole=True))

    # 09-29 실기: 실제 요약은 한 줄이 길다 — 줄바꿈 없이 잘렸고, 접은 판에서도 잘렸다
    log.info("▶ 완료 — 실제 길이의 요약 (줄바꿈)")
    overlay.finish("done", "확인할 것 1건 (리포트 맨 위) · 메일 엑셀 3개 받음 · 엑셀 3개 중 3개 올림 · "
                   "새 주문 7건 · 자동수집: 새 주문 0건 · 사이트 실패 1건: 몰A · 조회된 주문 9건 중 "
                   "9건 매출처리 · 남은 미매출 주문 1건 · 물류대기: 재고 부족으로 배송보류 상품 2종(상품A, "
                   "상품B) · 주문 11건 · 물류관리로 넘긴 주문 2건 · 물류관리: 개별배송 전표(송장) "
                   "31건 생성 · 주문 2건")
    overlay.set_expanded(True)
    made.append(_shot(under, overlay, f"{prefix}6_done_long", out))
    log.info("▶ 끝난 뒤 접었을 때 (결과 띠 없이 작은 판)")
    overlay.set_expanded(False)
    made.append(_shot(under, overlay, f"{prefix}7_done_hud", out))

    overlay.close()
    under.destroy()
    log.info("그림 %d장을 %s 에 남겼다. **보고 나면 지운다.**",
             len(made), out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
