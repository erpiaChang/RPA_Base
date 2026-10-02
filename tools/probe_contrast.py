r"""[조사 도구 - 읽기 전용] 화면 색 대비와 글자 크기 위계를 잰다.

**아무것도 띄우지 않는다.** 색 상수만 계산한다.

    .venv\Scripts\python.exe -m tools.probe_contrast

## 왜 이 도구가 있나 (2026-09-16)

오버레이를 여러 번 고치면서 **글자 대비만 보고 "잘 보인다" 고 판단했다.**
글자는 7~15:1 로 멀쩡했다. 그런데 사용자는 계속 "안 보인다" 고 했다.

재 보니 원인은 **글자가 아니라 구조**였다.

| 요소 | 그때 값 | 뜻 |
| --- | --- | --- |
| 진행막대 홈 | 1.14:1 | **남은 양이 안 보인다** |
| 카드 테두리 | 1.34:1 | 영역 구분이 안 보인다 |
| 화살표 | 1.66:1 | 단계 연결이 안 보인다 |
| [숨기기] 단추 바탕 | 1.30:1 | **단추가 단추로 안 보인다** |

WCAG 2.1 은 둘을 나눠 본다. 글자는 4.5:1(SC 1.4.3), **테두리·막대·표식 같은
비텍스트는 3.0:1**(SC 1.4.11). 우리는 뒤쪽을 아예 보지 않고 있었다.

★ 이 도구는 **눈을 대신하지 않는다.** 대비가 기준을 넘는 것과 "보기 좋은 것"은
  다르다. 다만 기준 아래인 것은 **누가 봐도 안 보인다** — 그것만은 여기서 막는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from gui import overlay, pipeline  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

# WCAG 2.1 하한
TEXT_MIN = 4.5       # SC 1.4.3 본문 글자
LARGE_MIN = 3.0      # SC 1.4.3 큰 글자 (18pt 이상, 또는 14pt bold)
NONTEXT_MIN = 3.0    # SC 1.4.11 테두리·막대·표식 같은 비텍스트


def _linear(channel: float) -> float:
    return (channel / 12.92 if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4)


def luminance(color: str) -> float:
    parts = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (_linear(c) for c in parts)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: str, other: str) -> float:
    a, b = luminance(one), luminance(other)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def at_least(name: str, fg: str, bg: str, floor: float) -> bool:
    got = contrast(fg, bg)
    return check(f"{name} ({floor:.1f} 이상)", got >= floor,
                 f"{got:.2f}:1  {fg} on {bg}")


def main() -> int:
    setup_logging()
    results: list[bool] = []

    log.info("▶ 오버레이 카드 — 글자")
    for name, fg, bg in (("본문 / 카드", overlay.FOREGROUND, overlay.CARD),
                         ("보조 / 카드", overlay.MUTED, overlay.CARD),
                         ("본문 / 판", overlay.FOREGROUND, overlay.PANEL),
                         ("보조 / 판", overlay.MUTED, overlay.PANEL)):
        results.append(at_least(name, fg, bg, TEXT_MIN))

    log.info("▶ 오버레이 카드 — **비텍스트** (여기를 안 보다가 놓쳤다)")
    results.append(at_least("카드 테두리 / 카드", overlay.EDGE, overlay.CARD,
                            NONTEXT_MIN))

    log.info("▶ 조작 막대 — 단추가 **단추로 보이는가**")
    results.append(at_least("★ [중단] 바탕 / 막대", overlay.STOP_BG,
                            overlay.BAR_BG, NONTEXT_MIN))
    results.append(at_least("★ [숨기기] 바탕 / 막대", overlay.HIDE_BG,
                            overlay.BAR_BG, NONTEXT_MIN))
    results.append(at_least("막대 테두리 / 막대", overlay.BAR_EDGE,
                            overlay.BAR_BG, NONTEXT_MIN))
    log.info("▶ 조작 막대 — 단추 글자")
    results.append(at_least("[중단] 글자", overlay.STOP_FG, overlay.STOP_BG,
                            TEXT_MIN))
    results.append(at_least("[숨기기] 글자", overlay.HIDE_FG, overlay.HIDE_BG,
                            TEXT_MIN))
    results.append(at_least("막대 글자", overlay.FOREGROUND, overlay.BAR_BG,
                            TEXT_MIN))

    for label, theme in (("DARK", pipeline.DARK), ("LIGHT", pipeline.LIGHT)):
        bg = theme["canvas"]
        log.info("▶ %s 테마 — 글자", label)
        results.append(at_least(f"{label} 단계 이름", theme["name"], bg, TEXT_MIN))
        results.append(at_least(f"{label} 상세", theme["detail"], bg, TEXT_MIN))

        log.info("▶ %s 테마 — 비텍스트", label)
        for key in ("edge", "arrow", "arrow_done"):
            results.append(at_least(f"{label} {key}", theme[key], bg,
                                    NONTEXT_MIN))

        # ★ **진행막대의 홈은 배경이 아니라 `채운 부분` 과 겨룬다.**
        #   얼마나 찼는지는 그 경계로 읽히기 때문이다. 이 짝을 여태 재지
        #   않아서, 홈을 배경 대비로 밝게 맞췄다가 채움과 가까워져
        #   **막대가 평평해 보였다** (2026-09-16 화면에서 확인).
        #   막대의 범위는 테두리(`edge`)가 보여 준다.
        log.info("▶ %s 테마 — **막대 홈 vs 채움** (여태 안 재던 짝)", label)
        for state in ("running", "done", "failed", "cancelled", "pending"):
            results.append(at_least(f"{label} 막대 채움({state}) / 홈",
                                    theme["card"][state][2], theme["track"],
                                    NONTEXT_MIN))

        log.info("▶ %s 테마 — 상태 칸 (테두리 %.1f / 글자 %.1f)",
                 label, NONTEXT_MIN, TEXT_MIN)
        for state, (fill, edge, text) in theme["card"].items():
            results.append(at_least(f"{label} {state} 테두리", edge, bg,
                                    NONTEXT_MIN))
            results.append(at_least(f"{label} {state} 글자", text, fill,
                                    TEXT_MIN))

    log.info("▶ ★ 상태를 **색만으로** 구분하지 않는다")
    # 색각 이상이나 흑백 화면에서도 상태를 알 수 있어야 한다.
    # 색 말고 무엇이 있는지 확인한다 — 표식 글자와 상태 이름 둘이다.
    results.append(check("상태마다 표식 글자가 따로 있다",
                         len(set(pipeline.MARKS.values()))
                         == len(pipeline.MARKS),
                         " ".join(f"{k}={v}" for k, v in
                                  pipeline.MARKS.items())))
    results.append(check("상태마다 이름 글자가 따로 있다",
                         len(set(pipeline.STATE_LABELS.values()))
                         == len(pipeline.STATE_LABELS),
                         " / ".join(pipeline.STATE_LABELS.values())))

    results += _check_hierarchy()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 대비가 기준을 넘는 것과 '보기 좋은 것' 은 다르다. 이 도구는 "
             "**기준 아래를 막을 뿐** 눈을 대신하지 않는다.")
    return 0 if all(results) else 1


# 이보다 작은 글자는 쓰지 않는다. 오버레이는 대상 프로그램 위에 떠서 뒤가
# 복잡한데, 그 위의 8pt 는 읽으려면 눈을 가까이 대야 한다.
MIN_POINT = 9


def _check_hierarchy() -> list[bool]:
    """글자 크기 **위계**를 본다. 창은 숨겨 둔 채로 만든다.

    ★ 사람이 대시보드에서 가장 먼저 찾는 것은 "지금 무엇을 하고 있나" 다.
      눈은 큰 글자로 먼저 가므로 **현재 단계 이름이 가장 커야 한다.**
      한때 `전체 진행률 %`(15pt)와 제목(14pt)이 현재 단계(12pt)보다 컸다 —
      위계가 뒤집혀 있었고, 그래서 "대시보드로서 만족스럽지 않다" 고 느껴졌다.
    """
    import re
    import tkinter as tk

    out: list[bool] = []
    root = tk.Tk()
    root.withdraw()          # 만들지만 보이지 않는다
    try:
        stepper = pipeline.PipelineView(root, theme=pipeline.DARK)
        current = pipeline.CurrentView(root, theme=pipeline.DARK)
        table = pipeline.TableView(root, theme=pipeline.DARK)

        sizes = {
            "현재 단계 이름": current.font_name.cget("size"),
            "현재 판 큰 숫자": current.font_big.cget("size"),
            "현재 판 머리": current.font_head.cget("size"),
            "현재 판 본문": current.font_body.cget("size"),
            "Stepper 단계 이름": stepper.font_name.cget("size"),
            "Stepper 상세": stepper.font_small.cget("size"),
            "Stepper 표식": stepper.font_mark.cget("size"),
            "표 제목": table.font_title.cget("size"),
            "표 머리": table.font_head.cget("size"),
            "표 칸": table.font_cell.cget("size"),
        }
        log.info("▶ 글자 크기 — %s",
                 " / ".join(f"{k} {v}" for k, v in sizes.items()))

        # 오버레이가 직접 박아 둔 크기도 함께 본다 (`("Segoe UI", N, ...)`).
        source = Path(overlay.__file__).read_text(encoding="utf-8")
        literals = [int(m) for m in
                    re.findall(r'font=\("Segoe UI",\s*(\d+)', source)]
        log.info("▶ 오버레이가 직접 박은 크기 — %s", sorted(set(literals)))

        biggest = max(sizes["현재 단계 이름"], 0)
        out.append(check("★ 현재 단계 이름이 화면에서 **가장 크다**",
                         all(biggest > v for k, v in sizes.items()
                             if k != "현재 단계 이름")
                         and all(biggest > v for v in literals),
                         f"현재 단계 {biggest}pt / 나머지 최대 "
                         f"{max([v for k, v in sizes.items() if k != '현재 단계 이름'] + literals)}pt"))

        out.append(check(f"★ {MIN_POINT}pt 보다 작은 글자를 쓰지 않는다",
                         all(v >= MIN_POINT for v in sizes.values())
                         and all(v >= MIN_POINT for v in literals),
                         f"가장 작은 것 {min(list(sizes.values()) + literals)}pt"))

        gap = sizes["Stepper 단계 이름"] - sizes["Stepper 상세"]
        out.append(check("★ Stepper 단계 이름이 상세보다 2pt 이상 크다 "
                         "(1pt 차이는 위계가 아니다)", gap >= 2,
                         f"{sizes['Stepper 단계 이름']} vs "
                         f"{sizes['Stepper 상세']} (차이 {gap})"))
    finally:
        root.destroy()
    return out


if __name__ == "__main__":
    raise SystemExit(main())
