r"""진행 상황을 tkinter Canvas 에 그린다. 모든 배포본에 들어가므로 **기능 이름을 적지 않는다**
(`orchestrator.steps.StepEvent` 를 받아 그리기만 한다 — `gui/common.py` 와 같은 규칙).

| 밀도 | 그림 |
| --- | --- |
| `FULL` (본 창·오버레이 펼침) | 가로 한 줄 Stepper (단계 동그라미 + 이름·상태·시각). 큰 단계(`Step.stage`)가 있으면 큰 단계 동그라미 + 아래 세부 단계 줄 (`_draw_stages`). 현재 단계 상세는 `CurrentView` |
| `HUD` (오버레이 코너) | 지금 보여 줄 단계 하나(`current()`) + 경과 시간 |

Canvas 인 이유: 오버레이가 클릭을 통과시키는 Win32 확장 스타일이 tkinter 창에 붙어 있다 (`gui/overlay.py`).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# 상태 이름. `orchestrator.steps` 를 import 하지 않는다 — GUI 공통 코드는
# 흐름 쪽을 모르는 편이 낫고, 여기서 쓰는 것은 문자열 값뿐이다.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
CANCELLED = "cancelled"

# 칸 안에 찍는 표식. 상태를 색만으로 구분하지 않는다 — 색각 이상이나
# 흑백 화면에서도 읽혀야 한다.
MARKS = {
    PENDING: "○",
    RUNNING: "◆",
    DONE: "●",
    SKIPPED: "–",
    FAILED: "✕",
    CANCELLED: "■",
}

# ★ **색은 계산해서 넣었다. 감으로 고르지 않는다** (2026-09-16).
#
# 처음 값은 글자 대비는 좋았는데(7~15:1) **테두리·화살표·진행막대 홈이
# 1.1~1.7:1** 이었다. 글자는 읽히는데 **구조가 배경에 잠겨** 어디가 칸이고
# 어디가 막대인지 보이지 않는 상태다. 그래서 대시보드가 "허전하다" 고 느껴진다.
#
# 기준은 WCAG 2.1 이다.
#   · 글자            4.5:1 이상 (SC 1.4.3)
#   · 테두리·막대·표식 3.0:1 이상 (SC 1.4.11 Non-text Contrast)
#
# 고칠 때는 **색상과 채도를 지키고 명도만** 필요한 만큼 옮겼다. 그래서 색감은
# 그대로고 구조만 드러난다. 값을 손으로 바꾸면 이 기준이 깨지므로,
# `tools/probe_contrast.py` 가 전수 검사한다.
LIGHT = {
    "canvas": "#eef1f5",
    "edge": "#748daa",
    # HUD 가 자기 판으로 깔 색. **배경색과 달라야** 한다 —
    # 오버레이는 배경색을 투명하게 비우기 때문이다(`gui/overlay.py`).
    "panel": "#ffffff",
    "arrow": "#7a8da0",
    "arrow_done": "#589873",
    # ★ 막대의 **홈**이다. 배경이 아니라 **채운 부분**과 3:1 이어야 한다 —
    #   얼마나 찼는지가 그 경계로 읽히기 때문이다. 막대의 범위는 테두리
    #   (`edge`)로 보인다. 한때 이 값을 배경 대비로 맞췄다가 채움과
    #   가까워져 **막대가 평평해 보였다** (2026-09-16 화면에서 확인).
    "track": "#cbd4df",
    "name": "#1d2630",
    "detail": "#5d6875",
    "card": {
        PENDING:   ("#ffffff", "#748daa", "#6a7888"),
        RUNNING:   ("#e6f1ff", "#2b7fd4", "#0b5fa5"),
        DONE:      ("#ecf7f0", "#459b69", "#1b7a3d"),
        SKIPPED:   ("#f3f5f8", "#758daa", "#64707d"),
        FAILED:    ("#fdecee", "#de6373", "#b00020"),
        CANCELLED: ("#fff6e8", "#b98023", "#a06000"),
    },
}

DARK = {
    # ★ 오버레이 카드 안에 놓이므로 **카드와 같은 바탕**을 쓴다.
    #   판이 카드보다 밝으면 층이 하나 더 생겨 화면이 시끄러워진다.
    "canvas": "#121b26",
    "panel": "#121b26",
    # 판의 테두리. 색 대신 이것으로 영역을 나눈다.
    "edge": "#4e6981",
    "arrow": "#52687f",
    "arrow_done": "#3f8a63",
    "track": "#344a61",
    "name": "#eef3f8",
    "detail": "#9dabb9",
    "card": {
        PENDING:   ("#161f2a", "#516882", "#8996a4"),
        RUNNING:   ("#12304f", "#4da3ff", "#8ecbff"),
        DONE:      ("#12281c", "#32724a", "#5ad18a"),
        SKIPPED:   ("#161d26", "#53687f", "#8996a4"),
        FAILED:    ("#2e181c", "#ab434f", "#ff8a8a"),
        CANCELLED: ("#2f2514", "#826028", "#ffc06b"),
    },
}

STATE_LABELS = {
    PENDING: "실행 전",
    RUNNING: "진행 중",
    DONE: "완료",
    SKIPPED: "건너뜀",
    FAILED: "실패",
    CANCELLED: "중단",
}
# 끝난 것으로 보는 상태. 전체 진행 막대를 채울 때 쓴다.
FINISHED = (DONE, SKIPPED)

# --- 밀도 : **같은 자료를 얼마나 크게 보여 줄 것인가** ---------------
#
# 대상 프로그램 위에 띄울 때는 덮는 면적이 곧 비용이다. 실행 중에 정작
# 필요한 칸은 **하나**뿐인데 전부 같은 크기로 그리면 화면 절반을 먹는다
# (2026-09-16 실측: 9칸이 y=0~564 = 화면의 51.8% 를 걸쳤다).
FULL = "full"        # Stepper (+ 큰 단계). 본 창과 오버레이 "펼침"
HUD = "hud"          # 보여 줄 단계 하나 + 경과. 오버레이 코너


def place(event) -> str:
    """사람에게 보일 위치 — 큰 단계가 있으면 `2/4`, 없으면 세부 단계 번호 `9/11` (09-29)."""
    total = getattr(event, "stage_total", 0) or 0
    if total:
        return f"{event.stage_index}/{total}"
    return f"{event.index}/{event.total}"


def group_state(events) -> str:
    """큰 단계의 상태 — 세부 단계들로 정한다. 실패·중단·진행이 먼저 보인다."""
    states = [event.state for event in events]
    for state in (FAILED, CANCELLED, RUNNING):
        if state in states:
            return state
    if all(state in FINISHED for state in states):
        return DONE if DONE in states else SKIPPED
    if any(state in FINISHED for state in states):
        return RUNNING          # 세부 단계 사이 — 이 큰 단계는 아직 끝나지 않았다
    return PENDING


def _hms(seconds: float) -> str:
    """`00:04:21`. 초 단위 실수를 사람이 읽는 시간으로."""
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    """모서리가 둥근 사각형. Canvas 에는 이 도형이 없어 곡선으로 만든다."""
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class PipelineView:
    """단계들을 가로로 늘어놓는다. **단계 이름을 스스로 알지 못한다.**

    `update()` / `show_plan()` 은 **GUI 스레드에서만** 부른다. 흐름은 작업
    스레드에서 도므로 `root.after(0, view.update, event)` 로 넘긴다.
    """

    CARD_H = 78
    CARD_MIN_W = 128
    CARD_MAX_W = 260
    GAP = 30            # 칸 사이 (`_plan_layout`)
    PAD = 12
    LINE_H = 19         # HUD 한 줄 높이 (`_draw_hud`)

    # Stepper(가로 한 줄) 치수. 칸을 크게 그리던 때의 값을 대신한다.
    STEP_MIN_W = 96     # 한 단계가 차지할 최소 가로
    # ★ 78 -> 62. **시각 줄을 뺐다** (2026-09-16) — 동그라미 + 이름 + 상태
    #   세 줄이면 충분하고, 남은 자리는 아래 현재 단계 판이 쓴다.
    STEP_H = 62
    DOT_R = 13          # 동그라미 반지름
    SUB_H = 18          # 큰 단계 아래 세부 단계 한 줄 (09-29)

    def __init__(self, parent, theme: dict | None = None, on_pick=None,
                 scale: float = 1.0, show_bar: bool = True,
                 card_min_w: int | None = None, card_max_w: int | None = None,
                 card_h: int | None = None, fill_height: bool = False,
                 density: str = FULL) -> None:
        self.theme = theme or LIGHT
        self.density = density
        self.on_pick = on_pick
        self.scale = max(scale, 1.0)
        self.show_bar = show_bar
        # ★ 남는 높이를 칸에 나눠 준다. 오버레이처럼 넓은 자리를 받았을 때
        #   칸이 작으면 화면만 차지하고 정작 보여 주는 것이 없다.
        self.fill_height = fill_height
        self._overrides = {"CARD_MIN_W": card_min_w, "CARD_MAX_W": card_max_w,
                           "CARD_H": card_h}
        # 단계 순서를 지킨다. dict 는 넣은 순서를 유지한다.
        self.events: dict[str, object] = {}
        self.order: list[str] = []
        self.picked: str = ""
        # 화면에 그린 도형 -> step_id. 클릭한 칸을 알아내는 데 쓴다.
        self._shapes: dict[int, str] = {}

        for name in ("CARD_H", "CARD_MIN_W", "CARD_MAX_W", "GAP", "PAD", "LINE_H",
                     "STEP_MIN_W", "STEP_H", "DOT_R", "SUB_H"):
            setattr(self, name, int(getattr(PipelineView, name) * self.scale))
        for name, value in self._overrides.items():
            if value:
                setattr(self, name, int(value * self.scale))

        self.canvas = tk.Canvas(parent, bg=self.theme["canvas"],
                                highlightthickness=0, bd=0,
                                height=self.CARD_H + self.PAD * 2)
        self.canvas.bind("<Configure>", self._on_resize, add="+")
        self.canvas.bind("<Button-1>", self._on_click, add="+")

        base = tkfont.nametofont("TkDefaultFont")
        size = abs(base.cget("size")) or 9
        # ★ **`int(size * 1.05)` 는 아무 일도 하지 않았다** (2026-09-16 계산).
        #   기본 글꼴이 9pt 라 `int(9 * 1.05)` = 9 다. 키우려고 쓴 곱셈이
        #   반올림에서 사라져서 단계 이름(9)과 상세(8)가 **1pt 차이**였고,
        #   위계가 사실상 없었다. 곱셈을 버리고 **더하기로 못 박는다.**
        self.font_name = base.copy()
        self.font_name.configure(size=size + 2, weight="bold")
        # 8pt 를 쓰지 않는다. 오버레이는 대상 프로그램 위에 떠서 뒤가
        # 복잡한데, 그 위의 8pt 는 읽으려면 눈을 가까이 대야 한다.
        self.font_small = base.copy()
        self.font_small.configure(size=max(size, 9), weight="normal")
        self.font_small_bold = base.copy()          # 지금 도는 세부 단계
        self.font_small_bold.configure(size=max(size, 9), weight="bold")
        self.font_mark = base.copy()
        self.font_mark.configure(size=size + 1, weight="bold")
        # HUD 는 칸 하나만 보여 주므로 그 칸을 크게 쓴다.
        self.font_hud_name = base.copy()
        self.font_hud_name.configure(size=max(int(size * 1.5), 13),
                                     weight="bold")
        self.font_hud_order = base.copy()
        self.font_hud_order.configure(size=max(size, 9), weight="bold")

        self._width = 0

    # ------------------------------------------------------------------ 배치
    def grid(self, **kwargs) -> None:
        self.canvas.grid(**kwargs)

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    # ------------------------------------------------------------------ 자료
    def set_density(self, density: str) -> None:
        """밀도를 바꾼다. 자료는 그대로 두고 **그리는 방식만** 바꾼다."""
        if density == self.density:
            return
        self.density = density
        self._width = 0          # 폭 판정을 새로 하게 한다
        self._draw()

    def reset(self) -> None:
        self.events.clear()
        self.order.clear()
        self.picked = ""
        self._draw()

    def show_plan(self, events) -> None:
        """실행 **전에** 계획을 그려 둔다."""
        self.events.clear()
        self.order.clear()
        for event in events:
            self._remember(event)
        self._draw()

    def update(self, event) -> None:
        """단계 하나를 갱신한다. 처음 보는 단계면 뒤에 붙인다."""
        self._remember(event)
        self._draw()

    def _remember(self, event) -> None:
        if event.step_id not in self.events:
            self.order.append(event.step_id)
        self.events[event.step_id] = event

    def selected(self):
        """고른 칸의 마지막 사진. 고르지 않았으면 None."""
        return self.events.get(self.picked)

    def stage_groups(self) -> list[tuple[str, list]]:
        """큰 단계별 세부 단계 사진 (09-29). 큰 단계가 없는 단계가 하나라도 있으면 빈 목록 — 예전처럼 그린다."""
        groups: dict[str, list] = {}
        for step_id in self.order:
            event = self.events[step_id]
            stage = getattr(event, "stage", "")
            if not stage:
                return []
            groups.setdefault(stage, []).append(event)
        return list(groups.items())

    def select(self, step_id: str) -> None:
        self.picked = step_id if step_id in self.events else ""
        self._draw()

    # ------------------------------------------------------------------ 입력
    def _on_resize(self, event) -> None:
        # 1px 차이로 다시 그리지 않는다. 그리는 비용이 아깝다.
        if abs(event.width - self._width) < 4:
            return
        self._width = event.width
        self._draw()

    def _on_click(self, event) -> None:
        for shape in self.canvas.find_overlapping(event.x, event.y,
                                                  event.x, event.y):
            step_id = self._shapes.get(shape)
            if step_id:
                self.picked = step_id
                self._draw()
                if self.on_pick is not None:
                    self.on_pick(self.events[step_id])
                return

    # ------------------------------------------------------------------ 그리기
    def _plan_layout(self, count: int) -> tuple[int, int, int]:
        """(한 줄에 몇 개, 칸 너비, 줄 수). 폭에 맞춰 줄을 나눈다."""
        usable = max(self.canvas.winfo_width(), 200) - self.PAD * 2
        per_row, card_w = 1, self.CARD_MIN_W
        for candidate in range(count, 0, -1):
            width = (usable - (candidate - 1) * self.GAP) / candidate
            if width >= self.CARD_MIN_W:
                per_row, card_w = candidate, width
                break
        card_w = int(min(card_w, self.CARD_MAX_W))
        rows = (count + per_row - 1) // per_row
        return per_row, card_w, rows

    def _draw(self) -> None:
        self.canvas.delete("all")
        self._shapes.clear()
        if not self.order:
            return
        if self.density == HUD:
            self._draw_hud()
        else:
            self._draw_full()

    # ----------------------------------------------------- 코너 HUD (작게)
    def current(self):
        """지금 보여 줄 칸 하나. 진행 중 > 실패 > 중단 > 마지막으로 끝난 것.

        계획이 비어 있으면 None.

        실패를 진행 중 다음으로 두는 이유: 실패하면 흐름이 멈추므로 그 뒤에
        진행 중인 칸이 없다. **사람이 봐야 하는 것은 실패한 칸**이다.
        """
        events = [self.events[step_id] for step_id in self.order]
        if not events:
            return None        # 아직 계획을 받지 못했다
        for state in (RUNNING, FAILED, CANCELLED):
            for event in events:
                if event.state == state:
                    return event
        done = [e for e in events if e.state in FINISHED]
        return done[-1] if done else events[0]

    # 코너 HUD 크기 (배율 전). 자세한 것은 펼쳐서 본다 — 크게 하지 않는다 (사용자 요청 09-16).
    # 오버레이는 여기에 테두리 여백을 더한다 (`Overlay.size`).
    HUD_W = 270
    HUD_H = 147

    def hud_size(self) -> tuple:
        """코너 HUD 에 필요한 크기. 부르는 쪽이 창을 이만큼 잡는다."""
        return (int(self.HUD_W * self.scale), int(self.HUD_H * self.scale))

    def _draw_hud(self) -> None:
        """보여 줄 단계 **하나**(`current()`)와 경과 시간만 그린다. 단계별 점열은 두지 않는다 —
        HUD 는 1~2초 보고 상태를 아는 자리다 (단계 목록은 펼쳐서 본다)."""
        canvas = self.canvas
        width = max(canvas.winfo_width(), self.hud_size()[0])
        event = self.current()
        _fill, _border, accent = self.theme["card"].get(
            event.state, self.theme["card"][PENDING])

        pad = int(12 * self.scale)
        inner = pad + 2
        limit = width - inner * 2
        line = int(self.LINE_H)

        # 1줄: 무슨 단계 / 몇 번째. 큰 단계가 있으면 **큰 단계** 이름과 번호 (09-29)
        top = pad + int(8 * self.scale)
        stage = getattr(event, "stage", "")
        order = place(event).replace("/", " / ")
        canvas.create_text(inner, top, anchor="w",
                           text=self._fit(stage or event.name,
                                          limit - self.font_hud_order.measure(order)
                                          - 12, self.font_hud_name),
                           fill=self.theme["name"], font=self.font_hud_name)
        canvas.create_text(inner + limit, top, anchor="e", text=order,
                           fill=self.theme["detail"], font=self.font_hud_order)

        # 2줄: 상태 · 경과. **돌고 있으면 그렇다고 말한다** (아래 머리말 참고)
        top += int(24 * self.scale)
        running = event.state == RUNNING
        spent = sum(e.elapsed or 0 for e in self.events.values())
        state_text = ("자동 조작 중" if running
                      else self._when(event, limit))
        canvas.create_text(inner, top, anchor="w",
                           text=f"● {state_text} · 경과 {_hms(spent)}"
                           if running else f"{state_text} · 경과 {_hms(spent)}",
                           fill=accent, font=self.font_small)

        # 3줄: 지금 무엇을 하고 있나. **한 줄만** 쓴다. 큰 단계면 세부 단계 이름이 앞에 온다
        top += int(20 * self.scale)
        detail = (getattr(event, "error", "")
                  or getattr(event, "activity", "") or event.detail)
        if stage:
            detail = f"{event.name} — {detail}" if detail else event.name
        if detail:
            canvas.create_text(inner, top, anchor="w",
                               text=self._fit(detail, limit, self.font_small),
                               fill=self.theme["detail"], font=self.font_small)
        top += line

        # 4줄: 건수. **셀 수 있을 때만** 숫자를 적는다
        ratio = getattr(event, "ratio", None)
        if ratio is not None:
            counts = f"{event.done_items} / {event.total_items}건 · {ratio:.0%}"
            canvas.create_text(inner, top, anchor="w", text=counts,
                               fill=self.theme["name"], font=self.font_small)
            # ★ 성공 건수는 적지 않는다. 정상은 기본값이라 볼 필요가 없고,
            #   **실패만 적으면 훨씬 빨리 읽힌다** (외부 검토 의견 2026-09-16).
            if event.failed_items:
                canvas.create_text(inner + limit, top, anchor="e",
                                   text=f"실패 {event.failed_items}건",
                                   fill=self.theme["card"][FAILED][2],
                                   font=self.font_small)
            top += int(16 * self.scale)
            thick = max(int(6 * self.scale), 5)
            canvas.create_rectangle(inner, top, inner + limit, top + thick,
                                    width=1, outline=self.theme["edge"],
                                    fill=self.theme["track"])
            if ratio > 0:
                canvas.create_rectangle(inner, top, inner + int(limit * ratio),
                                        top + thick, width=0, fill=accent)
            top += thick + int(10 * self.scale)
        else:
            # ★ **막대를 그리지 않는다.** 전에는 여기서 단계 개수로 만든
            #   전체 진행률을 그렸는데 그건 지어낸 숫자였다.
            #   그리고 `진행률 없음` 이라고 쓰지 않는다 — 사람이 그것을
            #   **멈춘 것**으로 읽는다 (외부 검토 의견 2026-09-16).
            canvas.create_text(inner, top, anchor="w",
                               text="이 단계는 건수를 세지 않습니다",
                               fill=self.theme["detail"], font=self.font_small)
            top += line


    # -------------------------------------------------------- 전체 (카드)
    # ------------------------------------------------- 가로 Stepper (전체)
    def _draw_full(self) -> None:
        """단계를 **가로 한 줄 Stepper**(동그라미 + 이름·상태·시각)로 그린다. 큰 단계가 있으면
        `_draw_stages`. 현재 단계 상세는 아래 `CurrentView` 가 따로 크게 보여 준다."""
        groups = self.stage_groups()
        if groups:
            self._draw_stages(groups)
            return
        canvas = self.canvas
        count = len(self.order)
        width = max(canvas.winfo_width(), 320)
        per_row = max(1, min(count, (width - self.PAD * 2) // self.STEP_MIN_W))
        rows = (count + per_row - 1) // per_row
        step_w = (width - self.PAD * 2) / per_row
        height = self.PAD * 2 + rows * self.STEP_H
        if canvas.winfo_height() != height:
            canvas.configure(height=height)

        for position, step_id in enumerate(self.order):
            row, column = divmod(position, per_row)
            center = self.PAD + step_w * column + step_w / 2
            top = self.PAD + row * self.STEP_H
            event = self.events[step_id]
            # 잇는 선은 **같은 줄 안에서만.** 줄이 바뀌는 자리에 그으면
            # 왼쪽 끝으로 돌아가는 흐름을 가리켜 거꾸로 읽힌다.
            if column < per_row - 1 and position < count - 1:
                self._draw_link(center, center + step_w, top + self.DOT_R + 4,
                                event.state)
            self._draw_step(event, center, top, step_w)

    def _draw_link(self, x1: float, x2: float, y: float, state: str) -> None:
        passed = state in FINISHED
        self.canvas.create_line(x1 + self.DOT_R + 6, y, x2 - self.DOT_R - 6, y,
                                fill=self.theme["arrow_done"] if passed
                                else self.theme["arrow"],
                                width=2 if passed else 1)

    def _draw_step(self, event, center: float, top: int, room: float) -> None:
        """동그라미 + 이름 + 상태.

        ## ★ 단계별 시각을 뺐다 (2026-09-16, 외부 검토 의견)

        전에는 칸마다 끝난 시각까지 적었다. 8~11칸이 한 줄에 들어가면
        **이름·상태·시각 세 줄이 촘촘해져** 무엇을 보는 화면인지 흐려진다.

        Stepper 의 목적은 진행률이 아니라 **작업 위치 지도**다 — 앞에 무엇이
        끝났고 지금 어디며 뒤에 무엇이 남았는가. 시각은 그 목적에 필요하지
        않고, 필요하면 아래 최근 작업 로그와 상세 표에 있다.
        """
        self._draw_node(event.step_id, event.index, event.name, event.state,
                        STATE_LABELS.get(event.state, event.state), center, top, room)

    def _draw_node(self, step_id: str, number: int, name: str, state: str, label: str,
                   center: float, top: int, room: float) -> None:
        """동그라미 + 이름 + 상태 한 칸. 세부 단계 칸과 큰 단계 칸이 같이 쓴다."""
        _fill, _border, accent = self.theme["card"].get(state, self.theme["card"][PENDING])
        here = state == RUNNING
        radius = self.DOT_R + (2 if here else 0)
        y = top + self.DOT_R + 4

        # 동그라미는 **테두리와 표식으로** 상태를 말한다. 통째로 칠하지 않는다
        # — 칠하면 화면이 색으로 시끄러워지고 대기 칸까지 눈에 띈다.
        inside = self.theme["panel"] if not here else accent
        shape = self.canvas.create_oval(center - radius, y - radius,
                                        center + radius, y + radius,
                                        fill=inside, outline=accent,
                                        width=2 if here else 1)
        self._shapes[shape] = step_id
        # ★ 아직 안 한 칸에는 **몇 번째인지**를 적는다. 끝난 칸은 표식이
        #   결과를 말하므로 번호가 필요 없다 (레퍼런스 디자인과 같다).
        mark = (str(number) if state in (PENDING, RUNNING)
                else MARKS.get(state, "○"))
        text_color = self.theme["panel"] if here else accent
        item = self.canvas.create_text(center, y, text=mark, fill=text_color,
                                       font=self.font_mark)
        self._shapes[item] = step_id

        limit = int(room) - 8
        text = self.canvas.create_text(
            center, y + radius + 12, text=self._fit(name, limit, self.font_name),
            fill=self.theme["name"] if here else self.theme["detail"],
            font=self.font_name if here else self.font_small)
        self._shapes[text] = step_id

        self.canvas.create_text(center, y + radius + 28, text=self._fit(label, limit, self.font_small),
                                fill=accent, font=self.font_small)

    # ------------------------------------------- 큰 단계 + 세부 단계 (09-29)
    def _draw_stages(self, groups) -> None:
        """큰 단계를 가로로, 그 아래에 세부 단계를 세로로 (09-29 사용자 요청).

        사람은 11단계가 아니라 **메일 → 주문수집·매출처리 → 물류대기 → 물류관리** 4단계로 느낀다.
        매출처리가 9/11 이면 거의 끝난 것처럼 보이는데 실제로는 절반이다. 큰 단계 칸은 세부 단계로
        상태를 정하고(`group_state`), 칸을 누르면 그 큰 단계의 첫 세부 단계를 고른다.
        """
        canvas = self.canvas
        count = len(groups)
        width = max(canvas.winfo_width(), 320)
        column = (width - self.PAD * 2) / count
        most = max(len(events) for _name, events in groups)
        gap = int(6 * self.scale)           # 큰 단계 상태 글자와 세부 단계 첫 줄 사이
        height = self.PAD * 2 + self.STEP_H + gap + most * self.SUB_H
        if canvas.winfo_height() != height:
            canvas.configure(height=height)

        top = self.PAD
        for position, (name, events) in enumerate(groups):
            center = self.PAD + column * position + column / 2
            state = group_state(events)
            if position < count - 1:
                self._draw_link(center, center + column, top + self.DOT_R + 4, state)
            label = STATE_LABELS.get(state, state)
            if state == RUNNING:
                finished = sum(1 for event in events if event.state in FINISHED)
                label = f"{label} · {finished}/{len(events)}"
            self._draw_node(events[0].step_id, position + 1, name, state, label,
                            center, top, column)
            self._draw_substeps(events, center - column / 2 + 10, top + self.STEP_H + gap, column - 16)

    def _draw_substeps(self, events, x: float, y: int, room: float) -> None:
        """세부 단계 한 줄씩 — 표식 + 이름. 지금 도는 것만 굵게."""
        for offset, event in enumerate(events):
            here = event.state == RUNNING
            accent = self.theme["card"].get(event.state, self.theme["card"][PENDING])[2]
            font = self.font_small_bold if here else self.font_small
            text = f"{MARKS.get(event.state, '○')} {event.name}"
            item = self.canvas.create_text(
                x, y + offset * self.SUB_H + self.SUB_H // 2, anchor="w",
                text=self._fit(text, int(room), font),
                fill=self.theme["detail"] if event.state == PENDING else accent, font=font)
            self._shapes[item] = event.step_id

    def _when(self, event, limit: int) -> str:
        """`완료 · 09:17:00` 한 줄. 자리가 되면 날짜까지 붙인다."""
        label = STATE_LABELS.get(event.state, event.state)
        if event.state == PENDING:
            # ★ 대기 칸에는 시각을 붙이지 않는다. 아직 아무 일도
            #   없었으므로 보여 줄 시각이 없다 — 있으면 지어낸 것이다.
            return label
        if event.state == RUNNING:
            return f"{label} · {event.elapsed:.0f}초" if event.elapsed else label
        clock = event.clock(with_date=True) if hasattr(event, "clock") else ""
        if clock:
            wide = f"{label} · {clock}"
            if self.font_small.measure(wide) <= limit:
                return wide
            return f"{label} · {event.clock()}"
        # 시각이 없다 — 아직 시작하지 않은 칸이다.
        return label

    def _fit(self, text: str, limit: int, font) -> str:
        """`limit` 픽셀에 들어가게 자른다. 자른 자리는 `…` 로 표시한다."""
        text = str(text).replace("\n", " ")
        if limit <= 0 or font.measure(text) <= limit:
            return text
        cut = text
        while cut and font.measure(cut + "…") > limit:
            cut = cut[:-1]
        return (cut + "…") if cut else ""

    # ------------------------------------------------------------- 확인용
    def drawn(self) -> list[str]:
        """지금 캔버스에 그려진 **글자들.** 확인 도구가 읽는다.

        화면을 찍어 보는 판정은 화면이 잠겨 있으면 할 수 없다. 이것은
        언제나 읽을 수 있고, "칸이 몇 개이고 무슨 글자가 들어갔나" 를
        그대로 말해 준다.
        """
        out = []
        for shape in self.canvas.find_all():
            if self.canvas.type(shape) == "text":
                out.append(self.canvas.itemcget(shape, "text"))
        return out

    def markers(self) -> int:
        """**단계 하나에 하나씩** 그린 표식(동그라미) 수. 큰 단계가 있으면 **큰 단계** 하나에 하나 (09-29).

        두 밀도 모두 단계를 동그라미로 나타낸다 — 전체는 Stepper 의
        동그라미, HUD 는 아래 점열이다. 그래서 같은 셈이 통한다.

        ★ 예전에는 둥근 사각형(다각형)을 셌다. 2026-09-16에 칸을
          Stepper 로 바꾸면서 그 셈이 0 이 됐다 — 확인 도구가 먼저 잡았다.
        """
        return sum(1 for shape in self.canvas.find_all()
                   if self.canvas.type(shape) == "oval")


class PipelinePane:
    """`진행 단계` 묶음 — `PipelineView` + 구간 실행 단추. 수집·ERPia(·통합)·실행용 창이 쓴다."""

    def __init__(self, parent, row: int, theme: dict | None = None,
                 on_run_from=None, scale: float = 1.0) -> None:
        from tkinter import ttk

        self.frame = ttk.LabelFrame(parent, text="진행 단계", padding=6)
        self.frame.grid(row=row, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self.view = PipelineView(self.frame, theme=theme or LIGHT,
                                 on_pick=self._on_pick, scale=scale)
        self.view.grid(row=0, column=0, sticky="nsew")

        self.detail = None
        self.run_from_btn = None
        if on_run_from is not None:
            bar = ttk.Frame(self.frame)
            bar.grid(row=1, column=0, sticky="w", pady=(6, 0))
            self.run_from_btn = ttk.Button(bar, text="고른 칸부터 실행",
                                           command=on_run_from)
            self.run_from_btn.grid(row=0, column=0)
            ttk.Label(bar, text="시작할 칸을 누르고 이 단추를 누릅니다. "
                               "되돌릴 수 없는 칸은 확인을 받습니다.",
                      foreground="#666666").grid(row=0, column=1, padx=(8, 0))

    # --- 예전 줄 표와 같은 이름들 -------------------------------------
    def reset(self) -> None:
        self.view.reset()
        if self.detail is not None:
            self.detail.show((), (), "칸을 누르면 내용이 여기 나옵니다.")

    def show_plan(self, events) -> None:
        self.view.show_plan(events)

    def update(self, event) -> None:
        self.view.update(event)
        # 지금 보고 있는 칸이 갱신됐으면 상세도 함께 새로 그린다.
        # (상세는 대개 끝날 때 채워진다 — 갱신하지 않으면 빈 채로 남는다.)
        if self.view.picked == event.step_id:
            self._on_pick(event)

    def set_enabled(self, enabled: bool) -> None:
        if self.run_from_btn is not None:
            self.run_from_btn.configure(state="normal" if enabled else "disabled")

    def attach_detail(self, detail) -> None:
        self.detail = detail
        self._on_pick(self.view.selected())

    def selected(self):
        return self.view.selected()

    def _on_pick(self, event) -> None:
        if self.detail is None:
            return
        if event is None:
            self.detail.show((), (), "칸을 누르면 내용이 여기 나옵니다.")
            return
        columns = tuple(getattr(event, "columns", ()) or ())
        rows = tuple(getattr(event, "rows", ()) or ())
        if not columns:
            self.detail.show((), (), f"{event.name} — 보여 줄 상세가 없습니다.")
            return
        # ★ 표의 줄 수를 '건' 으로 쓰지 않는다 (09-29 — 리포트는 09-21 에 고쳤다). 표는 '항목|값' 이거나
        #   상품별이라 줄 수가 주문 수가 아니다: 매출처리 표는 늘 3~5줄, 물류관리 표는 늘 1줄이었다.
        #   건수는 흐름이 만든 요약(`detail`)에 전표·라벨 기준으로 들어 있다.
        self.detail.show(columns, rows, f"{event.name} — {event.detail}" if event.detail else event.name)


class TableView:
    """단계의 **상세 표**를 캔버스에 그린다. 무엇을 그리는지 스스로 알지 못한다.

    `gui/common.DetailPane`(Treeview)과 같은 자료를 그리지만, 이쪽은 오버레이
    안에서 쓴다. Treeview 를 쓸 수 없는 이유가 둘이다.

    1. 오버레이는 **클릭이 통과한다.** 위젯을 놓아도 다룰 수 없다
    2. 위젯은 자기 배경을 갖는다. 오버레이는 배경색을 투명하게 비우므로
       캔버스에 직접 그려야 색을 맞출 수 있다

    ## 왜 펼침에 표가 필요한가 (사용자 요청 2026-09-16)

    칸은 "무엇을 했나" 까지만 말한다. 사람이 보고 싶은 것은 **그 단계가 실제로
    건드린 것들**이다. 그건 본 창과 리포트에만 있었는데, 대상 프로그램이 화면을
    덮고 있으면 둘 다 볼 수가 없다.
    """

    ROW_H = 22
    HEAD_H = 26
    PAD = 12

    # 값에 이 말이 있으면 색을 준다. 기능 이름이 아니라 **결과를 나타내는 말**이다.
    BAD_WORDS = ("실패", "오류", "없음", "중단")
    WARN_WORDS = ("무시", "건너뜀", "제외", "예정", "안 함")

    def __init__(self, parent, theme: dict | None = None,
                 scale: float = 1.0) -> None:
        self.theme = theme or LIGHT
        self.scale = max(scale, 1.0)
        for name in ("ROW_H", "HEAD_H", "PAD"):
            setattr(self, name, int(getattr(TableView, name) * self.scale))
        self.canvas = tk.Canvas(parent, bg=self.theme["canvas"],
                                highlightthickness=0, bd=0)
        self.canvas.bind("<Configure>", lambda _e: self._draw(), add="+")

        base = tkfont.nametofont("TkDefaultFont")
        size = abs(base.cget("size")) or 9
        # 상세 표도 8pt 를 쓰지 않는다 (위 `font_small` 과 같은 이유).
        self.font_head = base.copy()
        self.font_head.configure(size=max(size, 9), weight="bold")
        self.font_cell = base.copy()
        self.font_cell.configure(size=max(size, 9))
        self.font_title = base.copy()
        self.font_title.configure(size=size + 1, weight="bold")

        self.title = ""
        self.columns: tuple = ()
        self.rows: tuple = ()

    def grid(self, **kwargs) -> None:
        self.canvas.grid(**kwargs)

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    def show(self, title: str, columns, rows) -> None:
        self.title = title
        self.columns = tuple(columns or ())
        self.rows = tuple(tuple(row) for row in (rows or ()))
        self._draw()

    def drawn(self) -> list[str]:
        """그려진 글자들. 확인 도구가 읽는다."""
        return [self.canvas.itemcget(shape, "text")
                for shape in self.canvas.find_all()
                if self.canvas.type(shape) == "text"]

    # 자리를 잡아 줄 줄 수의 상한. 이보다 많으면 `... N줄 더 있습니다` 로
    # 알리고 리포트로 보낸다 — 표 하나가 화면을 다 먹으면 안 된다.
    RESERVE_ROWS = 8

    def needed_height(self) -> int:
        """이 표가 **쓰고 싶은** 높이. 줄 수에서 계산한다.

        ★ 그려진 것(`bbox`)으로 재면 **되먹임이 생긴다** — 자리가 좁아 세 줄만
          그렸는데 그 높이를 "필요한 만큼" 으로 읽고 더 좁히는 식이다.
          2026-09-16에 실제로 그렇게 만들어 표가 눌렸다.
          그래서 **줄 수에서 계산**한다. 이 값은 자리와 무관하다.
        """
        if not self.columns:
            return 0
        rows = min(len(self.rows), self.RESERVE_ROWS)
        title = int(22 * self.scale)
        notice = self.ROW_H if len(self.rows) > rows else 0
        return (self.PAD + 6 + title + self.HEAD_H
                + rows * self.ROW_H + notice + self.PAD)

    def visible_rows(self) -> int:
        """지금 폭·높이에서 **실제로 그린** 줄 수."""
        room = self.canvas.winfo_height() - self.PAD * 2 - self.HEAD_H \
            - int(24 * self.scale)
        return max(0, min(len(self.rows), room // self.ROW_H))

    def _tag_color(self, row) -> str:
        text = " ".join(str(value) for value in row)
        if any(word in text for word in self.BAD_WORDS):
            return self.theme["card"][FAILED][2]
        if any(word in text for word in self.WARN_WORDS):
            return self.theme["card"][CANCELLED][2]
        return self.theme["name"]

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 40 or height < 40:
            return

        _round_rect(canvas, 1, 1, width - 2, height - 2, 8,
                    fill=self.theme["panel"], outline=self.theme["edge"],
                    width=1)
        x = self.PAD + 4
        y = self.PAD + 6
        canvas.create_text(x, y, anchor="w",
                           text=self.title or "상세", fill=self.theme["name"],
                           font=self.font_title)
        y += int(22 * self.scale)

        if not self.columns:
            canvas.create_text(x, y, anchor="w",
                               text="이 단계에는 보여 줄 상세가 없습니다.",
                               fill=self.theme["detail"], font=self.font_cell)
            return

        # 컬럼 폭: 마지막 칸만 남은 자리를 다 쓴다. 대개 오류 문구라 길다.
        usable = width - self.PAD * 2 - 8
        count = len(self.columns)
        narrow = max(int(110 * self.scale), usable // (count + 2))
        widths = [narrow] * (count - 1) + [usable - narrow * (count - 1)]

        left = x
        for name, column_w in zip(self.columns, widths):
            canvas.create_text(left, y, anchor="w",
                               text=self._fit(name, column_w - 8, self.font_head),
                               fill=self.theme["detail"], font=self.font_head)
            left += column_w
        y += int(6 * self.scale)
        canvas.create_line(x, y + 6, x + usable, y + 6,
                           fill=self.theme["edge"])
        y += self.HEAD_H - int(6 * self.scale)

        shown = self.visible_rows()
        for row in self.rows[:shown]:
            color = self._tag_color(row)
            left = x
            for value, column_w in zip(row, widths):
                canvas.create_text(left, y, anchor="w",
                                   text=self._fit(str(value), column_w - 8,
                                                  self.font_cell),
                                   fill=color, font=self.font_cell)
                left += column_w
            y += self.ROW_H

        if shown < len(self.rows):
            # ★ 잘렸으면 **잘렸다고 말한다.** 안 그러면 전부 본 줄 안다.
            canvas.create_text(x, y + 2, anchor="w",
                               text=f"... {len(self.rows) - shown}줄 더 있습니다 "
                                    "(리포트에서 전부 볼 수 있습니다)",
                               fill=self.theme["detail"], font=self.font_cell)

    def _fit(self, text: str, limit: int, font) -> str:
        text = str(text).replace(chr(10), " ")
        if limit <= 0 or font.measure(text) <= limit:
            return text
        cut = text
        while cut and font.measure(cut + "…") > limit:
            cut = cut[:-1]
        return (cut + "…") if cut else ""


class CurrentView:
    """**현재 단계 하나**를 크게 보여 주는 판. Stepper 아래에 놓인다.

        매출처리                     현재 작업
        미매출 전표를 조회하고…       거래처 10321 자료를 조회…
        처리 건수      42 / 120건
        ▓▓▓▓░░░░░░░           최근 작업 로그
        성공 41  실패 1  대기 78      09:58:21 · …

    ## ★ 없는 숫자는 그리지 않는다

    건수·진행률은 흐름이 `Hooks.count()` 로 **실제로 알려 준 것만** 있다.
    모르면 `total_items` 가 0 이고, 그러면 그 줄을 아예 그리지 않는다.
    빈 막대나 `0 / 0건` 을 그리면 사람이 "아직 하나도 못 했다" 로 읽는다.
    """

    PAD = 14
    LINE = 19

    def __init__(self, parent, theme: dict | None = None,
                 scale: float = 1.0, recent=None) -> None:
        self.theme = theme or LIGHT
        self.scale = max(scale, 1.0)
        # 최근 로그를 돌려주는 함수. 없으면 그 칸을 그리지 않는다.
        self.recent = recent
        for name in ("PAD", "LINE"):
            setattr(self, name, int(getattr(CurrentView, name) * self.scale))
        self.canvas = tk.Canvas(parent, bg=self.theme["canvas"],
                                highlightthickness=0, bd=0)
        self.canvas.bind("<Configure>", lambda _e: self._draw(), add="+")

        base = tkfont.nametofont("TkDefaultFont")
        size = abs(base.cget("size")) or 9
        # ★ **현재 단계 이름이 카드 안에서 가장 커야 한다** (2026-09-16).
        #   전에는 `전체 진행률 %`(15pt)와 제목(14pt)이 현재 단계(12pt)보다
        #   컸다. 사람이 대시보드에서 가장 먼저 찾는 것은 "지금 무엇을 하고
        #   있나" 인데, 눈은 큰 글자로 먼저 간다 — 위계가 뒤집혀 있었다.
        self.font_name = base.copy()
        self.font_name.configure(size=size + 8, weight="bold")
        self.font_head = base.copy()
        self.font_head.configure(size=max(size, 9), weight="bold")
        self.font_body = base.copy()
        self.font_body.configure(size=max(size, 9))
        self.font_big = base.copy()
        self.font_big.configure(size=size + 3, weight="bold")

        self.event = None

    def grid(self, **kwargs) -> None:
        self.canvas.grid(**kwargs)

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    def show(self, event) -> None:
        self.event = event
        self._draw()

    def drawn(self) -> list[str]:
        return [self.canvas.itemcget(shape, "text")
                for shape in self.canvas.find_all()
                if self.canvas.type(shape) == "text"]

    # 오른쪽에 보여 줄 최근 로그 줄 수. 그린 뒤가 아니라 **미리** 정해 둔다.
    RECENT_LINES = 3

    def needed_height(self) -> int:
        """이 판이 **쓰고 싶은** 높이. `_draw_left` / `_draw_right` 와 같은
        수를 써서 계산한다.

        ★ 이 판은 자리를 받으면 그만큼 늘어난다. 부르는 쪽이 높이를 모르면
          **내용이 적어도 상자만 커진다** — 2026-09-16 화면에서 아래 절반이
          통째로 비어 있었다.

        ★ 그려진 것(`bbox`)으로 재면 **되먹임이 생긴다.** 자리가 좁아 적게
          그렸는데 그 높이를 "필요한 만큼" 으로 읽고 더 좁힌다.
          그래서 **그리는 쪽과 같은 셈**을 여기서 한다. 숫자가 어긋나면
          `tools/probe_overlay.py` 가 빈자리로 잡는다.
        """
        s = self.scale
        # 왼쪽: 이름 → 지금 하는 일 → 처리 건수 → 막대 → 성공·실패·대기 3줄
        left = (int(26 * s) + int(24 * s) + int(22 * s)
                + max(int(8 * s), 6) + int(12 * s) + 3 * self.LINE)
        # 오른쪽: 현재 작업(머리+값) → 최근 작업 로그(머리) → 로그 줄들
        right = int(52 * s) + int(22 * s) + self.RECENT_LINES * self.LINE
        return self.PAD * 2 + max(left, right)

    # ------------------------------------------------------------------
    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 60 or height < 40:
            return
        event = self.event
        _fill, _border, accent = self.theme["card"].get(
            getattr(event, "state", PENDING), self.theme["card"][PENDING])
        _round_rect(canvas, 1, 1, width - 2, height - 2, 10,
                    fill=self.theme["panel"], outline=self.theme["edge"],
                    width=1)
        if event is None:
            canvas.create_text(self.PAD, self.PAD + 6, anchor="w",
                               text="아직 시작하지 않았습니다.",
                               fill=self.theme["detail"], font=self.font_body)
            return

        # 왼쪽에 현재 상태 색 띠 하나. 카드를 통째로 칠하지 않는다.
        canvas.create_rectangle(1, 10, 5, height - 10, width=0, fill=accent)

        half = width // 2
        self._draw_left(event, self.PAD + 6, self.PAD + 6, half - self.PAD * 2,
                        accent)
        self._draw_right(event, half + self.PAD, self.PAD + 6,
                         width - half - self.PAD * 2, height)

    def _draw_left(self, event, x: int, y: int, room: int, accent: str) -> None:
        canvas = self.canvas
        stage = getattr(event, "stage", "")
        name = f"{stage} · {event.name}" if stage else event.name     # 큰 단계 · 세부 단계 (09-29)
        canvas.create_text(x, y + 8, anchor="w",
                           text=self._fit(name, room, self.font_name),
                           fill=self.theme["name"], font=self.font_name)
        y += int(26 * self.scale)
        # 지금 무엇을 하고 있나 — 흐름이 `activity` 를 준 경우에만.
        line = getattr(event, "activity", "") or event.detail
        if line:
            canvas.create_text(x, y + 8, anchor="w",
                               text=self._fit(line, room, self.font_body),
                               fill=self.theme["detail"], font=self.font_body)
        y += int(24 * self.scale)

        total = getattr(event, "total_items", 0) or 0
        if total <= 0:
            # ★ 셀 수 없는 단계다. **빈 막대를 그리지 않는다.**
            canvas.create_text(x, y + 8, anchor="w",
                               text="처리 건수를 셀 수 없는 단계입니다.",
                               fill=self.theme["detail"], font=self.font_body)
            return

        done = getattr(event, "done_items", 0) or 0
        ratio = event.ratio or 0.0
        canvas.create_text(x, y + 8, anchor="w", text="처리 건수",
                           fill=self.theme["detail"], font=self.font_head)
        canvas.create_text(x + room, y + 8, anchor="e",
                           text=f"{done} / {total}건 ({ratio:.0%})",
                           fill=accent, font=self.font_big)
        y += int(22 * self.scale)
        thick = max(int(8 * self.scale), 6)
        canvas.create_rectangle(x, y, x + room, y + thick, width=1,
                                outline=self.theme["edge"],
                                fill=self.theme["track"])
        if ratio > 0:
            canvas.create_rectangle(x, y, x + int(room * ratio), y + thick,
                                    width=0, fill=accent)
        y += thick + int(12 * self.scale)

        # 성공 / 실패 / 대기 — 아는 것만.
        ok = getattr(event, "ok_items", 0) or 0
        failed = getattr(event, "failed_items", 0) or 0
        waiting = event.waiting_items
        pairs = [("성공", ok, self.theme["card"][DONE][2]),
                 ("실패", failed, self.theme["card"][FAILED][2]),
                 ("대기", waiting, self.theme["detail"])]
        for offset, (label, value, color) in enumerate(pairs):
            row = y + offset * self.LINE
            canvas.create_text(x, row, anchor="w", text=label, fill=color,
                               font=self.font_body)
            canvas.create_text(x + int(90 * self.scale), row, anchor="w",
                               text=f"{value}건", fill=color,
                               font=self.font_body)

    def _draw_right(self, event, x: int, y: int, room: int, height: int) -> None:
        canvas = self.canvas
        activity = getattr(event, "activity", "")
        canvas.create_text(x, y + 8, anchor="w", text="현재 작업",
                           fill=self.theme["detail"], font=self.font_head)
        canvas.create_text(x, y + 8 + self.LINE, anchor="w",
                           text=self._fit(activity or event.detail or "-", room,
                                          self.font_body),
                           fill=self.theme["name"], font=self.font_body)
        y += int(52 * self.scale)
        if self.recent is None:
            return
        canvas.create_line(x, y - 8, x + room, y - 8, fill=self.theme["edge"])
        canvas.create_text(x, y + 8, anchor="w", text="최근 작업 로그",
                           fill=self.theme["detail"], font=self.font_head)
        y += int(22 * self.scale)
        room_lines = max(0, (height - y - self.PAD) // self.LINE)
        for offset, text in enumerate(self.recent()[:room_lines]):
            canvas.create_text(x, y + offset * self.LINE, anchor="w",
                               text=self._fit(text, room, self.font_body),
                               fill=self.theme["detail"], font=self.font_body)

    def _fit(self, text: str, limit: int, font) -> str:
        text = str(text).replace(chr(10), " ")
        if limit <= 0 or font.measure(text) <= limit:
            return text
        cut = text
        while cut and font.measure(cut + "…") > limit:
            cut = cut[:-1]
        return (cut + "…") if cut else ""
