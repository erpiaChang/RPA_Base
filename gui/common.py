r"""GUI 공통 조각. **모든 배포본에 들어간다.**

여기에는 특정 기능의 이름을 적지 않는다. 이 파일은 어느 배포본에나 포함되므로,
여기 남긴 문구는 전부 흔적이 된다.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import font as tkfont, ttk

from utils.logger import is_user_record

HELP_FONT_SIZE = 8      # 보조 설명 폰트. 기본 UI 폰트보다 한 단계 작다


def ui_scale() -> float:
    """화면 배율. 읽지 못하면 1.0.

    `utils/dpi.ensure_dpi_awareness()` 로 per-monitor 인식을 켜면 **Windows 가
    창을 대신 늘려주지 않는다.** tkinter 는 96dpi 기준이라 150%/200% 화면에서
    창과 글자가 그만큼 작게 나온다. 그래서 직접 맞춘다.
    """
    from utils.dpi import scale_info

    try:
        return max(float(scale_info().get("scale") or 1.0), 1.0)
    except Exception:
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    """작업표시줄을 뺀 **주 모니터**의 영역 `(left, top, right, bottom)`.

    모니터가 여럿이어도 주 모니터를 본다. 대상 프로그램이 어느 쪽에 뜰지 알 수
    없고, 사용자가 늘 보는 쪽이 주 모니터이기 때문이다. (오버레이도 이것을 쓴다.)
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


# 창 제목줄·테두리 몫. `winfo_reqheight()` 는 이것을 빼고 잰다.
WINDOW_FRAME_MARGIN = 40


def apply_scaling(root: tk.Tk) -> float:
    """tk 스케일과 기본 폰트를 배율에 맞춘다."""
    scale = ui_scale()
    root.tk.call("tk", "scaling", scale * 1.333)
    if scale > 1.0:
        for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont"):
            try:
                font = tkfont.nametofont(name)
            except tk.TclError:
                continue
            font.configure(size=int(abs(font.cget("size")) * scale))
    return scale


def small_font() -> tkfont.Font:
    font = tkfont.nametofont("TkDefaultFont").copy()
    font.configure(size=HELP_FONT_SIZE)
    return font


class QueueLogHandler(logging.Handler):
    """로그를 큐에 넣는다. GUI 스레드가 주기적으로 꺼내 표시한다."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait((record.levelno, self.format(record)))
        except queue.Full:
            pass  # 표시용이다. 파일 로그에는 이미 남아 있다.


class LogPane:
    """진행 로그 창. 큐에 쌓인 로그를 주기적으로 옮겨 담는다."""

    POLL_MS = 100
    MAX_DRAIN = 200

    def __init__(self, parent, row: int) -> None:
        self.queue: queue.Queue = queue.Queue(maxsize=2000)
        frame = self.frame = ttk.LabelFrame(parent, text="진행 로그", padding=6)
        frame.grid(row=row, column=0, sticky="nsew", padx=12, pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.text = tk.Text(frame, wrap="none", height=10, state="disabled")
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure("error", foreground="#b00020")
        self.text.tag_configure("warning", foreground="#a06000")

        self.handler = QueueLogHandler(self.queue)
        self.handler.setFormatter(
            logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        # 사용자용 로그만 보인다. 나머지는 파일 로그에 있다 (`utils/logger.USER_LOGGER`).
        self.handler.addFilter(is_user_record)
        logging.getLogger().addHandler(self.handler)

        # 창이 닫힐 때 되돌릴 것. 들고 있지 않으면 정리할 수 없다.
        #   · 반복 예약(after) — 취소하지 않으면 창이 사라진 뒤 콜백이 돌아
        #     `invalid command name` 이 남는다 (2026-09-10 실측)
        #   · 루트 로거 핸들러 — 떼지 않으면 창이 없어도 큐에 계속 쌓인다
        self.job: str | None = None
        # 창을 **생성 시점에** 붙잡아 둔다. 파괴 중에 `winfo_toplevel()` 로
        # 되짚으면 이미 사라진 위젯을 거쳐야 해서 창을 못 알아본다 (2026-09-10).
        self.root = frame.winfo_toplevel()
        self.root.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event) -> None:
        """창 자신이 사라질 때만 정리한다. `<Destroy>` 는 위젯마다 올라온다."""
        if event.widget is not self.root:
            return
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
            self.handler = None

    def drain(self, root: tk.Tk) -> None:
        """큐를 비워 화면에 옮긴다. root.after 로 반복 호출한다."""
        drained = 0
        self.text.configure(state="normal")
        while drained < self.MAX_DRAIN:
            try:
                level, line = self.queue.get_nowait()
            except queue.Empty:
                break
            tag = "error" if level >= logging.ERROR else (
                "warning" if level >= logging.WARNING else "")
            self.text.insert("end", line + "\n", tag)
            drained += 1
        if drained:
            self.text.see("end")
        self.text.configure(state="disabled")
        if self.handler is not None:      # 창이 살아 있을 때만 다시 예약한다
            self.job = root.after(self.POLL_MS, self.drain, root)


class RecentLog(logging.Handler):
    """마지막 로그 몇 줄만 들고 있는 고리 버퍼.

    오버레이의 "최근 작업 로그" 가 읽는다. `LogPane` 과 달리 화면 위젯을
    갖지 않고 **줄만 보관**한다 — 오버레이는 캔버스에 직접 그리기 때문이다.

    ★ 여기서 만드는 값은 없다. 실제로 남은 로그를 그대로 돌려준다.
    """

    def __init__(self, keep: int = 12) -> None:
        super().__init__()
        self.keep = keep
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                            "%H:%M:%S"))
        self.addFilter(is_user_record)      # 사용자용 로그만 (`LogPane` 과 같다)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
            if len(self.lines) > self.keep:
                del self.lines[:-self.keep]
        except Exception:                      # noqa: BLE001
            # 표시용이다. 파일 로그에는 이미 남아 있다.
            self.handleError(record)

    def latest(self, count: int = 3) -> list[str]:
        """최근 줄, **새 것이 위로**. 없으면 빈 목록."""
        return list(reversed(self.lines[-count:]))

    def attach(self) -> None:
        logging.getLogger().addHandler(self)

    def detach(self) -> None:
        logging.getLogger().removeHandler(self)


class FormToggle:
    """입력 폼과 진행 화면을 접었다 폈다 하는 조각. 창 클래스가 섞어 쓴다.

    붙이는 쪽이 이것들을 만들어 두면 된다. 없는 것이 있어도 그냥 아무 일도
    하지 않는다 — 진행 화면이 이것 때문에 멈추면 안 된다.

    | 이름 | 무엇 |
    | --- | --- |
    | `self.form` | 입력 폼 프레임 |
    | `self.form_btn` | 폼을 여닫는 단추 |
    | `self.progress_frames` | 진행 단계 / 상세 / 로그 프레임들 |

    처음에는 입력만 보이고 [실행] 을 누르면 진행 화면이 나온다 — 빈 진행 표·로그가 자리만 먹지 않게 (사용자 요청 09-16).
    """

    def toggle_form(self) -> None:
        """입력 폼을 접었다 폈다 한다."""
        self._set_form_visible(not self._form_visible())

    def _form_visible(self) -> bool:
        form = getattr(self, "form", None)
        return bool(form is not None and form.winfo_manager())

    def _set_form_visible(self, visible: bool) -> None:
        """`grid_remove()` 를 쓴다 — `grid_forget()` 과 달리 **놓였던 자리를
        기억해서** 다시 붙일 때 옵션을 되풀이하지 않아도 된다.
        """
        form = getattr(self, "form", None)
        if form is not None:
            form.grid() if visible else form.grid_remove()
        button = getattr(self, "form_btn", None)
        if button is not None:
            button.configure(text="설정 숨기기" if visible else "설정",
                             width=12 if visible else 8)

    def _set_progress_visible(self, visible: bool) -> None:
        """진행 단계 / 상세 / 로그를 한꺼번에 여닫는다."""
        for frame in getattr(self, "progress_frames", ()):
            if frame is None:
                continue
            frame.grid() if visible else frame.grid_remove()
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        """창 높이를 안에 든 것에 맞춘다.

        진행 화면을 감추면 창 아래쪽이 **빈 채로 남는다.** 사용자가 보기에는
        고장난 것처럼 보인다. 폭은 그대로 두고 높이만 줄인다.

        ★ **화면보다 크게 만들지 않는다** (09-21). 세로 768px 노트북에서 창이 화면
          밖으로 나가 아래 단추 줄([중단]·[설정])을 누를 수 없었다. 넘치는 만큼은
          늘어나는 칸(진행 표·로그)이 줄어든다.
        """
        root = getattr(self, "root", None)
        if root is None:
            return
        try:
            root.update_idletasks()
            _left, top, _right, bottom = work_area()
            room = bottom - top - int(WINDOW_FRAME_MARGIN * ui_scale())
            need = min(root.winfo_reqheight(), max(room, 200))
            width = root.winfo_width() or root.winfo_reqwidth()
            # ★ minsize 도 같이 내린다. 그러지 않으면 창이 그 아래로
            #   줄지 않아 아래쪽이 빈 채로 남는다.
            min_w = root.minsize()[0] if root.minsize() else width
            root.minsize(min_w, max(need, 200))
            root.geometry(f"{width}x{need}")
        except Exception as exc:
            # 창 크기 맞추기는 보조 동작이다. 실패해도 화면은 그대로 쓴다.
            logging.getLogger(__name__).debug(
                "창 높이를 맞추지 못했다: %s", type(exc).__name__)


class DetailPane:
    """단계의 **내용물**을 그리는 표. 무엇을 그리는지 스스로 알지 못한다.

    컬럼 이름과 줄(`StepEvent.columns`·`rows`)만 받아 그대로 그린다 — 모든 배포본에 들어가므로 기능 이름을 모른다.
    단계가 실제로 건드린 것(사이트별 건수·보류 상품 등)을 로그를 뒤지지 않고 보게 한다.
    """

    def __init__(self, parent, row: int, height: int = 6) -> None:
        self.frame = ttk.LabelFrame(parent, text="상세", padding=6)
        self.frame.grid(row=row, column=0, sticky="nsew", padx=12, pady=(0, 6))
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self.title = ttk.Label(self.frame, text="단계를 고르면 내용이 여기 나옵니다.",
                               anchor="w", foreground="#555555")
        self.title.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self.tree = ttk.Treeview(self.frame, show="headings", height=height)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.frame, orient="vertical",
                               command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        # 결과가 좋지 않은 줄은 눈에 띄게 한다. 값으로 판단한다 — 어느 컬럼이
        # '결과' 인지 모르기 때문이다.
        self.tree.tag_configure("bad", foreground="#b00020")
        self.tree.tag_configure("warn", foreground="#a06000")
        self._rows: dict[str, tuple] = {}          # iid → 원래 칸 값 (`Link` 를 잃지 않게)
        self.tree.bind("<Double-1>", self._open)

    # 줄 안에 이 말이 있으면 색을 준다. 기능 이름이 아니라 **결과를 나타내는 말**이다.
    BAD_WORDS = ("실패", "오류", "없음", "중단", "못 올림", "못 받음", "안 됨")
    WARN_WORDS = ("무시", "건너뜀", "제외", "예정", "안 함", "확인 못 함", "dry-run",
                  "보류하지 않는")
    OPEN_HINT = "  (파일·폴더 이름을 두 번 누르면 열립니다)"

    def _tag(self, row) -> tuple:
        text = " ".join(str(value) for value in row)
        if any(word in text for word in self.BAD_WORDS):
            return ("bad",)
        if any(word in text for word in self.WARN_WORDS):
            return ("warn",)
        return ()

    def show(self, columns, rows, title: str = "") -> None:
        """표를 갈아 끼운다. `columns` 가 비면 안내만 남긴다.

        칸 값에 `path` 가 있으면(리포트의 `Link`) **두 번 누르면 연다** — 엑셀·폴더를 바로
        열어 보고 싶다는 요청 (09-21). 이 파일은 기능을 모르므로 `path` 가 있는지만 본다.
        """
        self._rows = {}
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree.configure(columns=tuple(columns))
        openable = any(getattr(value, "path", "") for row in rows for value in row)
        self.title.configure(text=(title or "") + (self.OPEN_HINT if openable else ""))
        if not columns:
            return
        scale = ui_scale()
        # 마지막 컬럼만 늘린다. 대개 오류 문구나 제목이라 길다.
        for index, name in enumerate(columns):
            last = index == len(columns) - 1
            self.tree.heading(name, text=name)
            self.tree.column(name, width=int((260 if last else 120) * scale),
                             stretch=last, anchor="w")
        for row in rows:
            iid = self.tree.insert("", "end", values=tuple(str(v) for v in row),
                                   tags=self._tag(row))
            self._rows[iid] = tuple(row)

    def _open(self, event) -> None:
        """두 번 누른 칸(없으면 그 줄의 첫 파일)을 연다. 못 열면 로그만 남긴다."""
        row = getattr(self, "_rows", {}).get(self.tree.identify_row(event.y))
        if not row:
            return
        column = self.tree.identify_column(event.x)      # '#1' 부터
        index = int(column[1:]) - 1 if column[1:].isdigit() else -1
        values = ([row[index]] if 0 <= index < len(row) else []) + list(row)
        target = next((getattr(v, "path", "") for v in values if getattr(v, "path", "")), "")
        if not target:
            return
        try:
            import os
            os.startfile(target)          # noqa: S606 (Windows 전용)
        except OSError as exc:
            logging.getLogger(__name__).warning("열지 못했다: %s (%s)", target, exc)

