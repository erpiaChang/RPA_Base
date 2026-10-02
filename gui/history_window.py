r"""지난 실행 표 — `logs/history.jsonl` 을 보여 준다. 줄을 두 번 누르면 그 리포트를 연다.

`HistoryPane` 은 표 하나다. 실행 창은 [실행 기록] 탭에 넣고(09-29. 건너뛴 예약도 기록 파일에 있다, 10-01),
다른 창은 `HistoryWindow`(따로 뜨는 창)로 쓴다.

**모든 배포본에 들어간다.** 기능 이름을 적지 않는다 (`gui/common.py` 와 같은 규칙).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from gui.common import ui_scale
from orchestrator import history
from utils.logger import get_logger

log = get_logger(__name__)

COLUMNS = ("시각", "어떻게", "기능", "결과", "걸린 시간", "확인할 것")
WIDTHS = (100, 70, 150, 420, 70, 60)
LIMIT = 100


class HistoryPane:
    """머리줄 + 표. 값 읽기와 표 그리기만 한다."""

    def __init__(self, parent: tk.Misc, *, loader=history.load) -> None:
        self.loader = loader
        self.entries: list[dict] = []
        scale = ui_scale()
        frame = self.frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        head = ttk.Frame(frame, padding=(0, 0, 0, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.note_var = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.note_var, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Button(head, text="새로 고침", width=10, command=self.refresh).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(head, text="결과 보기", width=10, command=self.open_selected).grid(row=0, column=2, padx=(8, 0))

        body = ttk.Frame(frame)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, columns=COLUMNS, show="headings", selectmode="browse")
        for name, width in zip(COLUMNS, WIDTHS):
            self.tree.heading(name, text=name)
            self.tree.column(name, width=int(width * scale), anchor="w",
                             stretch=(name == "결과"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.tag_configure(history.FAILED, foreground="#b00020")
        self.tree.tag_configure(history.UNFINISHED, foreground="#a06000")
        self.tree.tag_configure(history.CANCELLED, foreground="#666666")
        self.tree.tag_configure(history.SKIPPED, foreground="#8a6d1f")
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.refresh()

    def refresh(self) -> None:
        self.entries = self.loader(LIMIT)
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(index), values=history.row_of(entry),
                             tags=(entry.get("state") or "",))
        self.note_var.set(f"최근 {len(self.entries)}건 (새 것부터). 줄을 두 번 누르면 결과가 열립니다."
                          if self.entries else "아직 실행 기록이 없습니다.")

    def selected(self) -> dict | None:
        picked = self.tree.selection()
        if not picked:
            return None
        return self.entries[int(picked[0])]

    def open_selected(self) -> None:
        entry = self.selected()
        if entry is None:
            return
        owner = self.frame.winfo_toplevel()
        path = entry.get("report") or ""
        if not path or not os.path.isfile(path):
            messagebox.showinfo("결과", "이 줄은 결과 파일이 없습니다 (지워졌거나, 건너뛴 예약이거나, 만들지 못한 실행).",
                                parent=owner)
            return
        try:
            os.startfile(path)   # noqa: S606 (Windows 전용)
        except OSError as exc:
            log.error("리포트를 열지 못했다: %s: %s", type(exc).__name__, exc)
            messagebox.showerror("결과", f"열지 못했습니다:\n{path}", parent=owner)


class HistoryWindow(HistoryPane):
    """[지난 실행] — 따로 뜨는 창 (실행용이 아닌 창들이 쓴다)."""

    def __init__(self, parent: tk.Misc, *, loader=history.load) -> None:
        scale = ui_scale()
        top = self.top = tk.Toplevel(parent)
        top.title("지난 실행")
        top.geometry(f"{int(960 * scale)}x{int(480 * scale)}")
        top.minsize(int(600 * scale), int(240 * scale))
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        super().__init__(top, loader=loader)
        self.frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(10, 12))
