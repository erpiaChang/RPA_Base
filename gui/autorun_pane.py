r"""**자동 실행** 설정과 회차 기록을 보여 주는 칸. 기능 이름을 적지 않는다.

`gui/common.py` / `gui/pipeline.py` 와 같은 이유다 — 이 파일은 모든 배포본에
들어갈 수 있으므로 여기 남긴 문구는 전부 흔적이 된다.

## 무엇을 보여 주나

| 줄 | 왜 |
| --- | --- |
| [자동 실행] 체크 + 상태줄 | 사람이 **이것만 보고** 돌고 있는지 알 수 있어야 한다 |
| 예약 목록 (반복 / 시각 / 다음 실행 / 기능) + [추가]·[삭제] | 날·시각을 **여러 개** 둔다 (09-21). 글자로 치게 하지 않고 골라 넣는다 |
| 기능 체크 | 예약마다 실행할 기능 (09-21). 이름은 부르는 쪽이 `choices` 로 준다 |
| 간격 | 고칠 수 있어야 한다 |
| [적용] / [일시정지]·[계속하기] | 껐다 켜지 않고 바꿀 수 있어야 한다. 이름은 웹과 같다 (09-29) |
| 회차 기록 표 | 무인 실행이라 **사람은 나중에 본다.** 이것이 유일한 창구다 |

★ **회차 기록을 화면에 두는 것이 이 칸의 핵심이다.** 사람이 없을 때 도는
  기능이라, 지난 회차가 완료였는지 건너뛰었는지 실패였는지 볼 곳이 없으면
  "돌고 있는 줄 알았는데 한 번도 안 돌았다" 를 며칠 뒤에 안다.
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from gui.common import small_font
from utils import autorun, schedule

# 표 컬럼. 회차 기록은 네 칸이다 (`AutoRunner.rows()` 와 맞춘다).
COLUMNS = ("시각", "상태", "소요", "내용")
COLUMN_WIDTHS = (110, 70, 80, 380)

SLOT_COLUMNS = ("반복", "시각", "다음 실행", "기능")   # 기능은 `choices` 가 있을 때만
SLOT_WIDTHS = (150, 60, 150, 220)
DEFAULT_MODULES = "[실행할 기능] 선택대로"     # 기능을 붙이지 않은 예전 줄
SLOT_ROWS = 3

# 반복 고르기. 고른 것에 따라 옆 입력칸이 바뀐다 (`_show_repeat_fields`).
REPEAT_DAILY, REPEAT_WEEK, REPEAT_MONTH, REPEAT_DATE = (
    "매일", "요일 지정", "매월", "날짜 지정")
REPEATS = (REPEAT_DAILY, REPEAT_WEEK, REPEAT_MONTH, REPEAT_DATE)

# 상태별 글자색. **색만으로 구분하지 않는다** — 글자로도 상태가 적혀 있다.
STATE_COLORS = {
    "완료": "#1b7f3a",
    "실패": "#b03434",
    "건너뜀": "#8a6d1f",
    "중단": "#555f6a",
    "진행 중": "#1f5f9e",
}

HISTORY_ROWS = 8
PAUSE_TEXT, RESUME_TEXT = "일시정지", "계속하기"     # 웹 대시보드와 같은 이름 (09-29)


def days_label(slot: schedule.Slot) -> str:
    """`반복` 칸 글자. `월수금` → `월·수·금`, `평일` → `평일 (월~금)`."""
    text = slot.days_text()
    if slot.date is not None:
        return f"{text} (한 번)"
    if text == "평일":
        return "평일 (월~금)"
    if text == "주말":
        return "주말 (토·일)"
    if not slot.month_day and text != "매일":
        return "·".join(text)
    return text


class AutoRunPane:
    """`자동 실행` LabelFrame 하나. 값 읽기/쓰기와 표 갱신만 한다."""

    def __init__(self, parent: tk.Widget, plan: schedule.Plan, *,
                 on_apply, on_pause_toggle, scale: float = 1.0,
                 choices: tuple[tuple[str, str], ...] = (),
                 chosen: tuple[str, ...] = (), history: bool = True) -> None:
        """`choices` 예약마다 고를 기능 `(id, 이름)` — 비면 기능 칸이 없다. `chosen` 처음 체크.

        `history=False` 면 회차 표를 만들지 않는다 — 실행 창은 [실행 기록] 탭에 합쳐 보인다 (09-29).
        """
        self._on_apply = on_apply
        self._on_pause_toggle = on_pause_toggle
        self.scale = scale
        self.with_history = history
        self.tree: ttk.Treeview | None = None
        self.choices = tuple(choices)
        self.module_vars = {key: tk.BooleanVar(value=key in chosen)
                            for key, _name in self.choices}
        self.slot_columns = SLOT_COLUMNS if self.choices else SLOT_COLUMNS[:3]

        self.frame = ttk.LabelFrame(parent, text="자동 실행", padding=10)
        # 도움말 줄. 화면이 작으면 창이 숨긴다 (`run_app._fit_form_to_screen`).
        self.notes: list[ttk.Label] = []

        self.enabled_var = tk.BooleanVar(value=plan.enabled)
        self.mode_var = tk.StringVar(value=plan.mode)
        self.slots: list[str] = list(plan.times)
        self.interval_var = tk.StringVar(value="" if plan.interval_minutes is None else str(plan.interval_minutes))
        self.status_var = tk.StringVar(value="자동 실행 꺼짐")

        self.repeat_var = tk.StringVar(value=REPEAT_DAILY)
        self.weekday_vars = [tk.BooleanVar(value=False) for _ in schedule.WEEKDAYS]
        self.month_day_var = tk.StringVar(value="1")
        self.date_var = tk.StringVar(value=time.strftime("%Y-%m-%d"))
        self.hour_var = tk.StringVar(value="09")
        self.minute_var = tk.StringVar(value="00")
        self.add_error_var = tk.StringVar(value="")

        self._build()
        self._fill_slots()
        self._update_mode_fields()

    # --- 만들기 -------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self.frame)
        top.pack(fill="x")
        ttk.Checkbutton(top, text="자동 실행", variable=self.enabled_var,
                        command=self._changed).pack(side="left")
        # 웹과 같은 이름 (사용자 확정 09-29) — 예약만 멈춘다
        self.pause_button = ttk.Button(top, text=PAUSE_TEXT, width=10,
                                       command=self._on_pause_toggle)
        self.pause_button.pack(side="right")
        ttk.Button(top, text="적용", width=8,
                   command=self._changed).pack(side="right", padx=(0, 6))

        # ★ 상태줄을 위쪽에 둔다. 사람이 가장 먼저 찾는 것이 "지금 돌고 있나" 다.
        ttk.Label(self.frame, textvariable=self.status_var,
                  foreground="#1f5f9e").pack(anchor="w", pady=(6, 0))

        form = ttk.Frame(self.frame)
        form.pack(fill="x", pady=(8, 0))
        form.columnconfigure(1, weight=1)

        ttk.Radiobutton(form, text="정해진 날·시각", value=schedule.DAILY,
                        variable=self.mode_var,
                        command=self._mode_changed).grid(row=0, column=0,
                                                         sticky="w")
        self._build_add_row(form)
        self._build_module_row(form)
        self._build_slot_list(form)

        ttk.Radiobutton(form, text="정해진 간격", value=schedule.INTERVAL,
                        variable=self.mode_var,
                        command=self._mode_changed).grid(row=4, column=0,
                                                         sticky="w",
                                                         pady=(8, 0))
        gap = ttk.Frame(form)
        gap.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        self.interval_entry = ttk.Entry(gap, textvariable=self.interval_var,
                                        width=8)
        self.interval_entry.pack(side="left")
        ttk.Label(gap, text=f"분 ({schedule.MIN_INTERVAL_MINUTES}~"
                            f"{schedule.MAX_INTERVAL_MINUTES})",
                  ).pack(side="left", padx=(4, 0))
        gap_note = ttk.Label(gap, text="앞 회차가 끝난 뒤부터 셉니다. "
                             + ("기능은 [실행할 기능] 선택대로. " if self.choices else "")
                             + "고친 뒤 [적용]",
                             font=small_font(), foreground="#666666")
        gap_note.pack(side="left", padx=(8, 0))
        self.notes.append(gap_note)

        skip_note = ttk.Label(self.frame, font=small_font(), foreground="#8a6d1f",
                              text="화면이 잠겨 있거나 앞 회차가 아직 돌고 있으면 그 회차는 "
                                   "건너뜁니다. 건너뛴 회차는 몰아서 돌리지 않습니다.")
        skip_note.pack(anchor="w", pady=(8, 0))
        self.notes.append(skip_note)

        if self.with_history:
            self._build_history()

    def _build_add_row(self, form: ttk.Frame) -> None:
        """반복 / (요일·날·날짜) / 시각 / [추가] 한 줄."""
        row = ttk.Frame(form)
        row.grid(row=1, column=0, columnspan=2, sticky="w", padx=(20, 0),
                 pady=(4, 0))
        self.repeat_box = ttk.Combobox(row, textvariable=self.repeat_var,
                                       values=REPEATS, state="readonly", width=9)
        self.repeat_box.pack(side="left")
        self.repeat_box.bind("<<ComboboxSelected>>",
                             lambda _e: self._show_repeat_fields())

        # 반복칸들이 들어갈 자리. ★ **반복칸보다 먼저 만든다** — `pack(in_=)` 로 넣는 칸은
        #   겹침 순서가 자리보다 위여야 보인다. 나중에 만들면 자리 틀이 덮어 요일 칸이
        #   안 보였다 (09-21 찍어서 발견).
        self.repeat_anchor = ttk.Frame(row)
        # 반복에 따라 셋 중 하나만 보인다.
        self.week_box = ttk.Frame(row)
        self.weekday_checks = [
            ttk.Checkbutton(self.week_box, text=name, variable=var)
            for name, var in zip(schedule.WEEKDAYS, self.weekday_vars)]
        for check in self.weekday_checks:
            check.pack(side="left")
        self.month_box = ttk.Frame(row)
        self.month_spin = ttk.Spinbox(self.month_box, from_=1, to=31, width=3,
                                      textvariable=self.month_day_var, wrap=True)
        self.month_spin.pack(side="left")
        ttk.Label(self.month_box, text="일").pack(side="left", padx=(2, 0))
        self.date_box = ttk.Frame(row)
        self.date_entry = ttk.Entry(self.date_box, textvariable=self.date_var,
                                    width=11)
        self.date_entry.pack(side="left")
        self.repeat_boxes = {REPEAT_WEEK: self.week_box,
                             REPEAT_MONTH: self.month_box,
                             REPEAT_DATE: self.date_box}
        self.repeat_anchor.pack(side="left", padx=(6, 0))

        clock = ttk.Frame(row)
        clock.pack(side="left", padx=(10, 0))
        self.hour_spin = ttk.Spinbox(clock, from_=0, to=23, width=3, wrap=True,
                                     format="%02.0f", textvariable=self.hour_var)
        self.hour_spin.pack(side="left")
        ttk.Label(clock, text=":").pack(side="left")
        self.minute_spin = ttk.Spinbox(clock, from_=0, to=59, increment=5,
                                       width=3, wrap=True, format="%02.0f",
                                       textvariable=self.minute_var)
        self.minute_spin.pack(side="left")
        self.add_button = ttk.Button(row, text="추가", width=6,
                                     command=self._add_slot)
        self.add_button.pack(side="left", padx=(10, 0))
        self._show_repeat_fields()

        ttk.Label(form, textvariable=self.add_error_var, font=small_font(),
                  foreground="#b00020").grid(row=1, column=0, columnspan=2,
                                            sticky="e")

    def _build_module_row(self, form: ttk.Frame) -> None:
        """이 예약에 돌릴 기능. `choices` 가 없으면 만들지 않는다."""
        self.module_checks: list[ttk.Checkbutton] = []
        if not self.choices:
            return
        row = ttk.Frame(form)
        row.grid(row=2, column=0, columnspan=2, sticky="w", padx=(20, 0),
                 pady=(2, 0))
        ttk.Label(row, text="이 예약의 기능").pack(side="left")
        for key, name in self.choices:
            check = ttk.Checkbutton(row, text=name, variable=self.module_vars[key])
            check.pack(side="left", padx=(8, 0))
            self.module_checks.append(check)

    def _build_slot_list(self, form: ttk.Frame) -> None:
        box = ttk.Frame(form)
        box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(20, 0),
                 pady=(4, 0))
        box.columnconfigure(0, weight=1)
        columns = self.slot_columns
        self.slot_tree = ttk.Treeview(box, columns=columns, show="headings",
                                      height=SLOT_ROWS, selectmode="extended")
        for name, width in zip(columns, SLOT_WIDTHS):
            self.slot_tree.heading(name, text=name)
            self.slot_tree.column(name, width=int(width * self.scale),
                                  stretch=name == columns[-1], anchor="w")
        self.slot_tree.tag_configure("past", foreground="#8a6d1f")
        self.slot_tree.grid(row=0, column=0, sticky="ew")
        self.delete_button = ttk.Button(box, text="삭제", width=6,
                                        command=self._delete_slots)
        self.delete_button.grid(row=0, column=1, sticky="n", padx=(6, 0))

    def _build_history(self) -> None:
        box = ttk.Frame(self.frame)
        box.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(box, text="실행 기록", font=small_font()).pack(anchor="w")

        self.tree = ttk.Treeview(box, columns=COLUMNS, show="headings",
                                 height=HISTORY_ROWS)
        for name, width in zip(COLUMNS, COLUMN_WIDTHS):
            last = name == COLUMNS[-1]
            self.tree.heading(name, text=name)
            self.tree.column(name, width=int(width * self.scale),
                             stretch=last, anchor="w")
        for label, color in STATE_COLORS.items():
            self.tree.tag_configure(label, foreground=color)
        self.tree.pack(fill="both", expand=True)

    # --- 값 ------------------------------------------------------------
    def plan(self) -> schedule.Plan:
        """화면에 적힌 값을 `Plan` 으로. **틀린 값은 버리고 알린다.**"""
        return schedule.from_settings({
            "auto_run_enabled": self.enabled_var.get(),
            "auto_run_mode": self.mode_var.get(),
            "auto_run_times": list(self.slots),
            "auto_run_interval_minutes": self._interval(),
        }, known_modules=tuple(key for key, _ in self.choices) or None)

    def _interval(self) -> object:
        text = self.interval_var.get().strip()
        try:
            return int(text)
        except ValueError:
            # 숫자가 아니면 그대로 넘긴다. `from_settings` 가 버리고 알린다.
            return text

    def set_plan(self, plan: schedule.Plan) -> None:
        self.enabled_var.set(plan.enabled)
        self.mode_var.set(plan.mode)
        self.slots = list(plan.times)
        self.interval_var.set("" if plan.interval_minutes is None else str(plan.interval_minutes))
        self._fill_slots()
        self._update_mode_fields()

    # --- 예약 줄 -------------------------------------------------------
    def _slot_text(self) -> tuple[str, str]:
        """입력칸을 예약 한 줄로. `(줄, 오류)` — 오류가 있으면 줄은 빈 문자열."""
        text, error = self._when_text()
        if error or not self.choices:
            return text, error
        ids = [key for key, _ in self.choices if self.module_vars[key].get()]
        if not ids:
            return "", "이 예약의 기능을 하나 이상 고르세요."
        return f"{text} {','.join(ids)}", ""

    def _when_text(self) -> tuple[str, str]:
        """날·시각 부분만. `(줄, 오류)`."""
        try:
            hour = int(self.hour_var.get())
            minute = int(self.minute_var.get())
        except ValueError:
            return "", "시각은 숫자로 넣으세요."
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "", "시각은 00:00 ~ 23:59 입니다."
        clock = schedule.format_time(hour, minute)
        repeat = self.repeat_var.get()
        if repeat == REPEAT_WEEK:
            days = "".join(name for name, var in
                           zip(schedule.WEEKDAYS, self.weekday_vars) if var.get())
            if not days:
                return "", "요일을 하나 이상 고르세요."
            return f"{days} {clock}", ""
        if repeat == REPEAT_MONTH:
            return f"매월 {self.month_day_var.get().strip()}일 {clock}", ""
        if repeat == REPEAT_DATE:
            return f"{self.date_var.get().strip()} {clock}", ""
        return f"매일 {clock}", ""

    def _add_slot(self) -> None:
        text, error = self._slot_text()
        slot = schedule.parse_slot(text) if text else None
        if not error and slot is None:
            error = ("날짜는 2026-09-25 처럼 넣으세요." if
                     self.repeat_var.get() == REPEAT_DATE else "날을 확인하세요.")
        if not error and slot.next_after(time.time()) is None:
            error = "이미 지난 날짜입니다."
        if not error and slot.text() in self.slots:
            error = "이미 있는 예약입니다."
        if not error and len(self.slots) >= schedule.MAX_SLOTS:
            error = f"예약은 {schedule.MAX_SLOTS}개까지입니다."
        self.add_error_var.set(error)
        if error:
            return
        self.slots.append(slot.text())
        self.slots = list(self.plan().times)      # 정렬·중복 제거를 한곳에서
        self._fill_slots()
        self._changed()

    def _delete_slots(self) -> None:
        chosen = set(self.slot_tree.selection())
        if not chosen:
            self.add_error_var.set("지울 예약을 목록에서 고르세요.")
            return
        self.add_error_var.set("")
        self.slots = [text for index, text in enumerate(self.slots)
                      if str(index) not in chosen]
        self._fill_slots()
        self._changed()

    def _slot_rows(self) -> list[tuple[str, ...]]:
        now = time.time()
        rows = []
        for text in self.slots:
            slot = schedule.parse_slot(text)
            if slot is None:                # 설정 파일에 손으로 쓴 틀린 줄
                rows.append((text, "", "읽을 수 없음 — 지우세요", "")[:len(self.slot_columns)])
                continue
            when = slot.next_after(now)
            row = (days_label(slot), schedule.format_time(slot.hour, slot.minute),
                   schedule.when_text(when, now) if when else "지났음 (돌지 않음)")
            rows.append(row + ((self._modules_label(slot),) if self.choices else ()))
        return rows

    def _modules_label(self, slot: schedule.Slot) -> str:
        if not slot.modules:
            return DEFAULT_MODULES
        if len(slot.modules) == len(self.choices):
            return "전체"
        names = dict(self.choices)
        return ", ".join(names.get(key, key) for key in slot.modules)

    def _fill_slots(self) -> None:
        rows = self._slot_rows()
        current = [tuple(self.slot_tree.item(item, "values"))
                   for item in self.slot_tree.get_children()]
        if current == rows:
            return
        selected = set(self.slot_tree.selection())
        self.slot_tree.delete(*self.slot_tree.get_children())
        for index, row in enumerate(rows):
            tags = ("past",) if row[2].startswith(("지났음", "읽을 수")) else ()
            self.slot_tree.insert("", "end", iid=str(index), values=row,
                                  tags=tags)
        keep = [iid for iid in selected if self.slot_tree.exists(iid)]
        if keep:
            self.slot_tree.selection_set(keep)

    # --- 갱신 ----------------------------------------------------------
    def refresh(self, runner: autorun.AutoRunner) -> None:
        """상태줄과 회차 표를 다시 그린다. 1초에 한 번 불린다."""
        self.status_var.set(runner.status_line())
        self.pause_button.configure(text=RESUME_TEXT if runner.paused else PAUSE_TEXT)
        self._fill_slots()                 # 다음 실행 칸이 시간에 따라 바뀐다
        if self.tree is not None:
            self._fill(runner.rows(limit=HISTORY_ROWS * 3))

    def _fill(self, rows: list[tuple[str, str, str, str]]) -> None:
        # ★ 매번 지우고 다시 넣는다. 회차는 하루에 몇 건이라 비용이 없고,
        #   부분 갱신을 하면 "지난 회차가 사라지지 않는" 버그가 생긴다.
        current = [self.tree.item(item, "values") for item in
                   self.tree.get_children()]
        if [tuple(row) for row in current] == [tuple(row) for row in rows]:
            return                       # 바뀐 것이 없으면 건드리지 않는다
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=row, tags=(row[1],))

    def problems(self) -> list[str]:
        return self.plan().problems()

    # --- 짜임 ----------------------------------------------------------
    def _show_repeat_fields(self) -> None:
        for name, box in self.repeat_boxes.items():
            if name == self.repeat_var.get():
                box.pack(in_=self.repeat_anchor, side="left")
            else:
                box.pack_forget()

    def _mode_changed(self) -> None:
        self._update_mode_fields()
        self._changed()

    def _update_mode_fields(self) -> None:
        daily = self.mode_var.get() == schedule.DAILY
        slot_state = "normal" if daily else "disabled"
        for widget in (self.month_spin, self.date_entry, self.hour_spin,
                       self.minute_spin, self.add_button, self.delete_button,
                       *self.weekday_checks, *self.module_checks):
            widget.configure(state=slot_state)
        self.repeat_box.configure(state="readonly" if daily else "disabled")
        # 방식을 안 골랐으면(기본값 없음, 10-02) 둘 다 닫는다 — 고르면 열린다
        self.interval_entry.configure(state="normal" if self.mode_var.get() == schedule.INTERVAL else "disabled")

    def _changed(self) -> None:
        self._on_apply(self.plan())

    def pack(self, **kwargs) -> None:
        self.frame.pack(**kwargs)

    def grid(self, **kwargs) -> None:
        self.frame.grid(**kwargs)
