r"""ERPia 자동화 창 — 입력 폼 + 진행 파이프라인 + 자동 실행 칸. `run_app` 이 상속한다.

- 입력: 프로그램 경로([찾기]) / 업체코드 / 아이디 / 비밀번호 / 수집방식 / 택배사 / 박스 / 자동·수동.
  필수값이 다 채워져야 [실행] 이 살아난다. 비밀번호는 `*` 로 보인다.
- [실행] 은 `orchestrator/erpia_flow.py` 를 작업 스레드에서 돌리고, 끝나면 리포트를 쓴다.
- 입력값은 `config/settings.local.json` 에 저장돼 다음에 다시 뜬다. 비밀번호는 `dpapi:` 로
  감싸 저장한다 (`utils/secret.py`, 09-17).

tkinter는 표준 라이브러리다. 추가 패키지가 필요 없다.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

# 업무 흐름은 orchestrator/erpia_flow.py 에 있다. 여기서는 오류 종류만 쓴다.
from automation.application import AlreadyRunningError, ApplicationError, Target
from automation.login import LoginError
from automation import logistics, logistics_wait
from automation.order_mapping import SALES_MODES, ScreenError
from config.settings import SETTINGS, save_local
from gui.common import HELP_FONT_SIZE, DetailPane, FormToggle
from gui.history_window import HistoryWindow
from gui.overlay import Overlay
from gui.pipeline import PipelinePane
from utils import autorun, cancel, process, ui
from orchestrator import erpia_flow, friendly, history, report, steps, steps_erpia, telemetry
from orchestrator.common import Hooks, Result
from utils.logger import get_logger, is_user_record, user_log

log = get_logger(__name__)

PASSWORD_MASK = "*"

# 수집 방식 이름은 흐름 쪽 정의를 그대로 쓴다. 두 벌로 두면 어긋난다.
SOURCE_SITE = erpia_flow.SOURCE_SITE      # 자동수집
SOURCE_EXCEL = erpia_flow.SOURCE_EXCEL    # 엑셀수집

# 엑셀수집이 폴더를 받는 이유와 파일명 규칙. 화면에 적어 둔다.
EXCEL_HELP = "\n".join((
    "폴더 안의 엑셀을 모두 올립니다. 어느 사이트에 올릴지는 파일 이름으로 정합니다.",
    "  · 파일명은  사이트명_아무거나.xlsx  형식입니다.  예) 사이트A_20260908.xlsx",
    "  · 첫 밑줄(_) 앞부분이 사이트명이며, 사이트 목록과 완전히 같아야 합니다",
    "  · 밑줄이 없는 파일은 건너뜁니다. 하위 폴더는 보지 않습니다",
))


COURIER_HELP = "\n".join((
    "물류관리에서 배송업체 목록에서 이름이 완전히 같은 항목을 고릅니다.",
    "  · 부분 일치는 하지 않습니다.  '택배' 라고 적으면 '택배사A'는 선택하지 않습니다.",
    "  · 같은 이름이 여러 개면 목록에서 맨 위 것을 고릅니다",
    "  · 공백과 괄호까지 화면 목록과 똑같이 입력하세요",
))

# 물류관리 자동/수동 (사용자 확정 2026-09-17). 저장 뒤 누르는 버튼이 달라진다.
MODE_HELP = "\n".join((
    "물류관리 위쪽의 [자동/수동] 을 이 값으로 맞추고 시작합니다.",
    "  · 자동: 저장 뒤 [운송장출력] 을 누릅니다",
    "  · 수동: 저장 뒤 [엑셀파일생성] 을 누릅니다",
))

# 박스도 택배사와 같은 규칙으로 고른다 (사용자 확정 2026-09-07).
BOX_HELP = "\n".join((
    "물류관리에서 박스 목록에서 이름이 완전히 같은 항목을 고릅니다.",
    "  · 택배사와 같은 규칙입니다. 부분 일치는 하지 않습니다",
))


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


class ErpiaWindow(FormToggle):
    POLL_MS = 100
    MAX_DRAIN = 200

    # 구간 실행(리모컨)을 쓸 수 있는 화면인가.
    # ★ 통합 흐름은 지원하지 않는다 — 수집(메일)부터 이어지는 흐름이라
    #   중간부터 시작하는 것이 의미가 없고, `full_flow` 가 막는다.
    #   하위 창에서 False 로 덮으면 단추 자체가 생기지 않는다.
    SUPPORTS_SEGMENT = True

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.target: Target | None = None
        self.log_queue: queue.Queue = queue.Queue(maxsize=2000)
        self.busy = False
        # [실행] 을 받아 시작하기 전(검사·알림 창). 그 창이 떠 있는 동안 예약·웹 [실행] 이 끼어들면
        # 손 실행의 '시작 못 함' 이 예약 회차를 닫는다 (10-02)
        self._starting = False
        # ★ **무인 실행 중인가.** 자동 실행(예약)으로 시작한 회차에서는 True 다.
        #   이때는 **사람을 기다리는 창을 절대 띄우지 않는다** — 아무도 누르지
        #   않아 창이 영구히 남고, 그 뒤 예약이 **전부** 죽는다. 무인 운전에서
        #   가장 위험한 고장 방식이라 자리마다 막는 것이 아니라 여기 한 곳에서
        #   가른다 (`_tell_problem`).
        self.unattended = False
        # 예약 시계. 자동 실행을 쓰는 창에서만 만든다 (`attach_autorun`).
        self.autorunner: autorun.AutoRunner | None = None
        self._autorun_job: str | None = None
        # 이번 실행의 중단 토큰. 실행할 때마다 새로 만든다 — 한 번 중단한 토큰을
        # 다시 쓰면 다음 실행이 시작하자마자 중단된다.
        self.token: cancel.CancelToken | None = None
        # ERPia 위에 뜨는 진행 상황 오버레이. 실행할 때 만든다.
        self.overlay: Overlay | None = None
        # 마지막 실행의 HTML 리포트. [결과 보기] 가 이것을 연다.
        self.report_path = None

        root.title("ERPia RPA")
        # 창 크기도 배율을 따른다. 고정 px 로 두면 150%/200% 화면에서
        # 위젯만 커지고 창은 그대로라 아래쪽이 잘린다.
        scale = _ui_scale()
        # 단계 표 + 상세 표 + 로그가 들어간 뒤의 크기다.
        # `tools/probe_gui.py` 로 재서 정했다 — 고정 462px + 늘어나는 영역 532px.
        # 세로 1080 화면을 넘지 않게 900 으로 잡았다. 세 영역 모두 스크롤이
        # 있어 더 작은 화면에서도 줄어들 뿐 잘리지는 않는다.
        root.geometry(f"{int(800 * scale)}x{int(900 * scale)}")
        root.minsize(int(680 * scale), int(600 * scale))

        self.exe_var = tk.StringVar(value=SETTINGS.target_exe or "")
        self.company_var = tk.StringVar(value=SETTINGS.login_company_code or "")
        self.id_var = tk.StringVar(value=SETTINGS.login_user_id or "")
        self.pw_var = tk.StringVar(value=SETTINGS.login_password or "")
        # 수집 방식. **최소 1개, 최대 2개.** 기본값 없음 (10-02) — 비면 [실행] 이 '입력 필요' 로 잠긴다
        saved_sources = SETTINGS.collect_sources or []
        self.site_source_var = tk.BooleanVar(value=SOURCE_SITE in saved_sources)
        self.excel_source_var = tk.BooleanVar(value=SOURCE_EXCEL in saved_sources)
        # 엑셀수집이 훑을 **폴더**. 파일 하나가 아니다.
        self.excel_var = tk.StringVar(value=SETTINGS.excel_dir or "")
        # 물류관리 배송정보설정 [업체] 에서 완전 일치로 고를 택배사 이름
        self.courier_var = tk.StringVar(value=SETTINGS.delivery_company or "")
        # 물류관리 배송정보설정 [박스] 에서 완전 일치로 고를 박스 규격
        self.box_var = tk.StringVar(value=SETTINGS.delivery_box or "")
        # 물류관리 자동/수동. 기본값 없음 (10-02) — 비면 둘 다 안 골린 채로 뜨고 [실행] 이 잠긴다
        self.mode_var = tk.StringVar(value=(SETTINGS.logistics_mode or "").strip())
        self.status_var = tk.StringVar()

        self._build()
        self._attach_log_handler()

        # 네 값 중 하나라도 바뀌면 [실행] 활성 여부를 다시 판단한다.
        for var in (
            self.exe_var, self.company_var, self.id_var, self.pw_var,
            self.site_source_var, self.excel_source_var, self.excel_var,
            self.courier_var, self.box_var, self.mode_var,
        ):
            var.trace_add("write", self._on_field_changed)
        self._update_source_fields()
        # 실행 전에 단계 목록을 그려 둔다. 구간 실행에서 골라야 하기 때문이다.
        self._show_plan()
        self._update_run_state()

        # 창을 닫을 때 되돌릴 것 두 가지. 들고 있지 않으면 정리할 수 없다.
        #   · 반복 예약(after) — 취소하지 않으면 창이 사라진 뒤 콜백이 돌아
        #     `invalid command name ..._drain_log` 가 남는다 (2026-09-10 실측)
        #   · 루트 로거에 붙인 핸들러 — 떼지 않으면 창이 없어도 큐에 계속 쌓인다
        self._drain_job: str | None = self.root.after(self.POLL_MS, self._drain_log)
        self.root.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        # 단계 표와 로그가 함께 늘어난다. 단계 표는 줄 수가 정해져 있어
        # (수집방식에 따라 7~8단계) 로그에 더 많이 준다.
        root.rowconfigure(2, weight=1)   # 진행 단계
        root.rowconfigure(3, weight=1)   # 상세 (고른 단계의 내용물)
        root.rowconfigure(4, weight=1)   # 진행 로그

        form = self.form = ttk.Frame(root, padding=(12, 12, 12, 6))
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        # --- 1행: 경로 칸이 무엇을 받는지 밝힌다 ---
        # [실행] 옆에 빈 입력칸만 있으면 무엇을 넣는 자리인지 알 수 없다.
        ttk.Label(
            form,
            text="ERPia 프로그램 실행파일",
            foreground="#444444",
        ).grid(row=0, column=1, columnspan=2, sticky="w")

        # --- 2행: [실행] [프로그램 경로] [찾기] ---
        self.run_btn = ttk.Button(form, text="실행", width=8, command=self.on_run)
        self.run_btn.grid(row=1, column=0, padx=(0, 8), pady=(2, 0), sticky="w")

        self.exe_entry = ttk.Entry(form, textvariable=self.exe_var)
        self.exe_entry.grid(row=1, column=1, pady=(2, 0), sticky="ew")

        self.browse_btn = ttk.Button(form, text="찾기", width=8, command=self.on_browse)
        self.browse_btn.grid(row=1, column=2, padx=(8, 0), pady=(2, 0))

        # --- 3~5행: 업체코드 / 아이디 / 비밀번호 ---
        self.company_entry = self._add_field(form, 2, "업체코드", self.company_var)
        self.id_entry = self._add_field(form, 3, "아이디", self.id_var)
        self.pw_entry = self._add_field(form, 4, "비밀번호", self.pw_var, mask=True)

        # --- 5행: 수집 방식 (기본 자동수집) ---
        ttk.Label(form, text="수집방식", width=8, anchor="w").grid(
            row=5, column=0, padx=(0, 8), pady=(10, 0), sticky="w"
        )
        source_box = ttk.Frame(form)
        source_box.grid(row=5, column=1, columnspan=2, pady=(10, 0), sticky="w")
        # 둘 다 고를 수 있다. 둘 다 고르면 자동수집 → 엑셀수집 순으로 이어서 한다.
        self.auto_check = ttk.Checkbutton(
            source_box, text="자동수집", variable=self.site_source_var
        )
        self.auto_check.grid(row=0, column=0, padx=(0, 16))
        self.excel_check = ttk.Checkbutton(
            source_box, text="엑셀수집", variable=self.excel_source_var
        )
        self.excel_check.grid(row=0, column=1)
        ttk.Label(
            source_box,
            text="둘 다 고를 수 있습니다 (최소 하나). 자동수집 → 엑셀수집 순으로 진행합니다.",
            foreground="#666666",
        ).grid(row=0, column=2, padx=(16, 0))

        # --- 6~7행: 엑셀수집 전용 입력 ---
        self.excel_label = ttk.Label(form, text="엑셀폴더", width=8, anchor="w")
        self.excel_label.grid(row=7, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        self.excel_entry = ttk.Entry(form, textvariable=self.excel_var)
        self.excel_entry.grid(row=7, column=1, pady=(8, 0), sticky="ew")
        self.excel_browse_btn = ttk.Button(
            form, text="찾기", width=8, command=self.on_browse_excel
        )
        self.excel_browse_btn.grid(row=7, column=2, padx=(8, 0), pady=(8, 0))

        self.excel_help = ttk.Label(
            form,
            text=EXCEL_HELP,
            font=self._small_font(),
            foreground="#666666",
            justify="left",
        )
        self.excel_help.grid(row=6, column=1, columnspan=2, pady=(8, 0), sticky="w")

        # --- 9행: 택배사 (물류관리에서 [업체] 드롭다운을 완전 일치로 고른다) ---
        self.courier_entry = self._add_field(form, 8, "택배사", self.courier_var)

        # 선택 규칙을 화면에 적어 둔다. 코드는 노출되지 않으므로
        # 사용자가 "왜 다른 택배사가 골라졌지?" 를 여기서 알 수 있어야 한다.
        ttk.Label(
            form,
            text=COURIER_HELP,
            font=self._small_font(),
            foreground="#666666",
            justify="left",
        ).grid(row=9, column=1, columnspan=2, pady=(4, 0), sticky="w")

        # --- 11행: 박스 (택배사와 같은 규칙으로 [박스] 드롭다운에서 고른다) ---
        self.box_entry = self._add_field(form, 10, "박스", self.box_var)
        ttk.Label(
            form,
            text=BOX_HELP,
            font=self._small_font(),
            foreground="#666666",
            justify="left",
        ).grid(row=11, column=1, columnspan=2, pady=(4, 0), sticky="w")

        # --- 13행: 물류관리 자동/수동 ---
        self.mode_radios = self._mode_selector(form, 12, label_width=8)
        ttk.Label(
            form,
            text=MODE_HELP,
            font=self._small_font(),
            foreground="#666666",
            justify="left",
        ).grid(row=13, column=1, columnspan=2, pady=(4, 0), sticky="w")

        # Enter로 다음 칸 이동, 마지막 칸에서는 실행
        self.company_entry.bind("<Return>", lambda _e: self.id_entry.focus_set())
        self.id_entry.bind("<Return>", lambda _e: self.pw_entry.focus_set())
        self.pw_entry.bind("<Return>", lambda _e: self.on_run())

        # --- 상태 줄 + [중단] ---
        status = ttk.Frame(root, padding=(12, 0, 12, 6))
        status.grid(row=1, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        # 상태줄 오른쪽에 둔다. 지금 무엇을 하고 있는지 바로 옆에서 끊는 것이
        # 자연스럽다. 실행 중에만 켜진다.
        self.stop_btn = ttk.Button(status, text="중단", width=8,
                                   command=self.on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(8, 0))
        # 실행이 끝나면 켜진다. 화면이 좁아 못 본 것을 여기서 크게 본다.
        self.report_btn = ttk.Button(status, text="결과 보기", width=12,
                                     command=self.open_report, state="disabled")
        self.report_btn.grid(row=0, column=2, padx=(8, 0))
        # 실행하면 입력 폼이 사라지고 그 자리를 진행 화면이 쓴다.
        # 다시 보고 싶을 때 누른다 (사용자 요청 2026-09-16).
        self.form_btn = ttk.Button(status, text="설정", width=8,
                                   command=self.toggle_form)
        self.form_btn.grid(row=0, column=3, padx=(8, 0))
        # 지난 실행 이력 (사용자 확정 2026-09-22). 실행마다 한 줄이 쌓인다 (`orchestrator/history.py`).
        self.history_btn = ttk.Button(status, text="지난 실행", width=10,
                                      command=self.open_history)
        self.history_btn.grid(row=0, column=5, padx=(8, 0))
        # 오버레이는 화면 전체를 덮는다. 뒤를 봐야 할 때가 있어서
        # 본 창에서도 여닫을 수 있게 한다 (사용자 요청 2026-09-16).
        self.overlay_btn = ttk.Button(status, text="오버레이", width=10,
                                      command=self.toggle_overlay,
                                      state="disabled")
        self.overlay_btn.grid(row=0, column=4, padx=(8, 0))

        # --- 진행 단계 (대시보드 + 구간 실행) ---
        self.step_pane = PipelinePane(
            root, row=2, scale=_ui_scale(),
            on_run_from=self.on_run_from if self.SUPPORTS_SEGMENT else None)
        # --- 상세: 고른 단계의 내용물 (올린 엑셀 / 보류 상품 …) ---
        self.detail_pane = DetailPane(root, row=3, height=6)
        self.step_pane.attach_detail(self.detail_pane)

        # --- 로그 ---
        log_frame = ttk.LabelFrame(root, text="진행 로그", padding=6)
        log_frame.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="none", height=7, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        # 켤 때는 입력만 보여 준다. 진행 화면은 [실행] 뒤에 나온다.
        self.progress_frames = (self.step_pane.frame,
                                self.detail_pane.frame, log_frame)
        self._set_progress_visible(False)

        self.log_text.tag_configure("error", foreground="#b00020")
        self.log_text.tag_configure("warning", foreground="#a06000")

        # 실행 중에 잠글 입력 위젯. **`_build()` 가 목록을 정한다.**
        # 화면 구성이 다른 하위 창(`gui/run_app.py`)이 없는 위젯을 잠그려다
        # AttributeError 로 죽지 않게 하려는 것이다.
        self.input_widgets = (
            self.exe_entry, self.browse_btn,
            self.company_entry, self.id_entry, self.pw_entry,
            self.auto_check, self.excel_check,
            self.excel_entry, self.excel_browse_btn,
            self.courier_entry, self.box_entry,
            *self.mode_radios,
        )

    def _mode_selector(self, parent: ttk.Frame, row: int,
                       label_width: int) -> tuple:
        """물류관리 자동/수동 라디오 한 줄. 라디오 위젯들을 돌려준다.

        실행용 창(`gui/run_app.py`)도 같은 줄을 쓴다. 두 창의 칸 너비만 다르다.
        """
        ttk.Label(parent, text="자동/수동", width=label_width, anchor="w").grid(
            row=row, column=0, padx=(0, 8), pady=(10, 0), sticky="w")
        box = ttk.Frame(parent)
        box.grid(row=row, column=1, columnspan=2, pady=(10, 0), sticky="w")
        radios = []
        for index, value in enumerate(logistics.MODES):
            radio = ttk.Radiobutton(box, text=value, value=value,
                                    variable=self.mode_var)
            radio.grid(row=0, column=index, padx=(0, 16))
            radios.append(radio)
        return tuple(radios)

    @staticmethod
    def _small_font() -> tkfont.Font:
        """보조 설명용 폰트. 8pt (기본 UI 폰트는 9pt)."""
        font = tkfont.nametofont("TkDefaultFont").copy()
        font.configure(size=HELP_FONT_SIZE)
        return font

    def _add_field(
        self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, mask: bool = False
    ) -> ttk.Entry:
        ttk.Label(parent, text=label, width=8, anchor="w").grid(
            row=row, column=0, padx=(0, 8), pady=(8, 0), sticky="w"
        )
        entry = ttk.Entry(parent, textvariable=var, show=PASSWORD_MASK if mask else "")
        entry.grid(row=row, column=1, columnspan=2, pady=(8, 0), sticky="ew")
        return entry

    def _attach_log_handler(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        # 사용자용 로그만 보인다. 나머지는 파일 로그에 있다 (`utils/logger.USER_LOGGER`).
        handler.addFilter(is_user_record)
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    def _on_destroy(self, event) -> None:
        """창이 사라질 때 예약과 핸들러를 되돌린다.

        `<Destroy>` 는 위젯마다 올라오므로 **창 자신일 때만** 처리한다.
        """
        if event.widget is not self.root:
            return
        if self._drain_job is not None:
            self.root.after_cancel(self._drain_job)
            self._drain_job = None
        handler = getattr(self, "_log_handler", None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            self._log_handler = None
        # 오버레이는 별도 창이라 본 창이 닫혀도 남는다. 같이 정리한다.
        # (`ui.set_click_guard(None)` 도 여기서 풀린다)
        self._close_overlay()

    def _drain_log(self) -> None:
        # 한 번에 꺼내는 양을 제한한다. 로그가 몰려도 UI가 멈추지 않는다.
        lines: list[tuple[int, str]] = []
        for _ in range(self.MAX_DRAIN):
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            for levelno, text in lines:
                tag = "error" if levelno >= logging.ERROR else (
                    "warning" if levelno >= logging.WARNING else ""
                )
                self.log_text.insert("end", text + "\n", tag)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        if self._drain_job is not None:
            self._drain_job = self.root.after(self.POLL_MS, self._drain_log)

    # -------------------------------------------------------------- 입력 상태
    def _values(self) -> dict[str, str]:
        values = {
            "실행 파일": self.exe_var.get().strip().strip('"'),
            "업체코드": self.company_var.get().strip(),
            "아이디": self.id_var.get().strip(),
            "비밀번호": self.pw_var.get(),
            # 물류관리는 어느 경로로 시작하든 마지막에 반드시 거친다.
            "택배사": self.courier_var.get().strip(),
            "박스": self.box_var.get().strip(),
            # 설정 파일에 틀린 값이 있으면 둘 다 안 골린 채로 뜬다. 여기서 막는다.
            "자동/수동": (self.mode_var.get()
                      if self.mode_var.get() in logistics.MODES else ""),
            # 이 창에는 매출처리 칸이 없다 — 설정 파일 값이 비거나 틀리면 여기서 막는다 (기본값 없음, 10-02 검토).
            # 실행용 창(run_app)은 제 칸으로 다시 정한다
            "매출처리": SETTINGS.sales_mode if SETTINGS.sales_mode in SALES_MODES else "",
        }
        # 수집 방식은 최소 하나. 아무것도 고르지 않으면 실행할 일이 없다.
        values["수집방식"] = " ".join(self._sources())
        # 엑셀수집일 때만 폴더를 필수로 본다.
        if self._needs_excel_dir():
            values["엑셀폴더"] = self.excel_var.get().strip().strip('"')
        return values

    def _needs_exe(self) -> bool:
        """이번 실행에 ERPia 실행 파일이 필요한가. **하위 창이 바꾼다** (메일만 고른 경우)."""
        return True

    def _needs_excel_dir(self) -> bool:
        """엑셀을 올릴 폴더를 사람이 지정해야 하는가.

        엑셀수집을 골랐으면 그렇다. 올릴 목록을 다른 데서 받는 화면은
        이 값을 False 로 바꿔, 폴더 칸을 비워 두어도 실행할 수 있게 한다.
        """
        return bool(self.excel_source_var.get())

    def _sources(self) -> list[str]:
        """고른 수집 방식. 실행 순서대로 돌려준다."""
        chosen = []
        if self.site_source_var.get():
            chosen.append(SOURCE_SITE)
        if self.excel_source_var.get():
            chosen.append(SOURCE_EXCEL)
        return chosen

    def _update_source_fields(self) -> None:
        """엑셀수집을 골랐을 때만 폴더를 입력받는다.

        폴더 칸 자체가 없는 화면(`gui/run_app.py`)에서는 할 일이 없다.
        """
        if getattr(self, "excel_entry", None) is None:
            return
        excel = self._needs_excel_dir()
        state = "normal" if excel else "disabled"
        for widget in (self.excel_entry, self.excel_browse_btn):
            widget.configure(state=state)
        color = "" if excel else "#999999"
        for label in (self.excel_label, self.excel_help):
            label.configure(foreground=color)

    def _missing(self) -> list[str]:
        return [name for name, value in self._values().items() if not value]

    def _on_field_changed(self, *_args: object) -> None:
        if not self.busy:
            self._update_source_fields()
            # 수집방식을 바꾸면 **단계 목록 자체가 바뀐다.** 미리 그려 둔 계획도
            # 따라가야 구간 실행에서 엉뚱한 단계를 고르지 않는다.
            self._show_plan()
        self._update_run_state()

    def _show_plan(self) -> None:
        """실행 **전에** 이번에 지나갈 단계를 표에 그려 둔다.

        구간 실행은 사용자가 단계를 골라야 하는데, 한 번 돌려 본 뒤에야 목록이
        보이면 고를 수가 없다.
        """
        if getattr(self, "step_pane", None) is None:
            return
        try:
            plan = self._plan()
        except ValueError:
            # 수집방식을 둘 다 끈 상태. 계획을 세울 수 없다 — 표를 비워 둔다.
            self.step_pane.reset()
            return
        self.step_pane.show_plan(steps.preview(plan))

    def _plan(self) -> list:
        """이번 입력으로 지나갈 단계 목록. **하위 창이 바꾼다.**"""
        return steps_erpia.plan(
            erpia_flow.Options(exe="", company="", user_id="", password="",
                               sources=self._sources()).normalized_sources())

    def _update_run_state(self) -> None:
        """네 값이 모두 채워졌을 때만 [실행]을 누를 수 있게 한다."""
        if self.busy:
            return
        missing = self._missing()
        self.run_btn.configure(state="disabled" if missing else "normal")
        if missing:
            self.status_var.set("입력 필요: " + ", ".join(missing))
        else:
            self.status_var.set("입력 완료. [실행]을 누르세요.")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in self.input_widgets:
            widget.configure(state=state)
        if not busy:
            self._update_source_fields()
        if busy:
            self.run_btn.configure(state="disabled")
        else:
            self._update_run_state()
        # [중단] 은 실행 중에만, 구간 실행은 실행 중이 아닐 때만.
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.step_pane.set_enabled(not busy)
        if self.overlay is not None:
            self.overlay.set_busy(busy)

    # -------------------------------------------------------------- actions
    def on_browse(self) -> None:
        current = self.exe_var.get().strip()
        initial = Path(current).parent if current else Path.home()
        if not initial.exists():
            initial = Path.home()

        chosen = filedialog.askopenfilename(
            title="실행할 프로그램 선택",
            initialdir=str(initial),
            filetypes=[("실행 파일", "*.exe"), ("모든 파일", "*.*")],
        )
        if not chosen:
            return  # 취소
        self.exe_var.set(str(Path(chosen)))
        log.info("실행 파일 선택: %s", self.exe_var.get())

    def on_browse_excel(self) -> None:
        current = self.excel_var.get().strip()
        initial = Path(current) if current and Path(current).is_dir() else Path.home()
        chosen = filedialog.askdirectory(
            title="엑셀이 들어 있는 폴더 선택",
            initialdir=str(initial),
        )
        if not chosen:
            return
        self.excel_var.set(str(Path(chosen)))
        log.info("엑셀 폴더 선택: %s", self.excel_var.get())

    def on_run_from(self) -> None:
        """[고른 단계부터 실행]. 표에서 고른 단계를 시작점으로 삼는다.

        위험한 단계인지 판단해서 확인을 받는 것은 **흐름 쪽**이다
        (`Hooks.approve_start`). 화면을 확보한 뒤라야 "지금 무엇이 남아
        있는지" 를 함께 보여 줄 수 있기 때문이다.
        """
        if self.busy:
            return
        event = self.step_pane.selected()
        if event is None:
            self.status_var.set("표에서 시작할 단계를 먼저 고르세요.")
            return
        log.info("구간 실행 요청: %s (%s) 부터", event.name, event.reentry_label)
        self.on_run(start_step=event.step_id)

    def on_run(self, start_step: str = "") -> None:
        if self.busy or self._starting:
            return
        self._starting = True
        try:
            self._begin_run(start_step)
        finally:
            self._starting = False

    def _begin_run(self, start_step: str) -> None:
        """[실행] 의 몸통 — 검사하고 흐름 스레드를 띄운다. 하위 창은 이것을 덮는다."""
        missing = self._missing()
        if missing:
            # 버튼이 비활성이라 정상 경로로는 오지 않는다. Enter 키 등을 대비한 방어.
            self._tell_problem("입력 필요",
                               "다음 값을 입력하세요:\n" + "\n".join(missing),
                               kind="warning")
            self._give_up_run("입력값이 비어 있다: " + ", ".join(missing))
            return

        exe = self.exe_var.get().strip().strip('"')
        if self._needs_exe() and not Path(exe).is_file():
            self._tell_problem("경로 오류", f"파일을 찾을 수 없습니다:\n{exe}")
            self._give_up_run(f"실행 파일이 없다: {exe}")
            return

        if self._needs_excel_dir():
            folder = self.excel_var.get().strip().strip('"')
            if not Path(folder).is_dir():
                self._tell_problem("경로 오류",
                                   f"폴더를 찾을 수 없습니다:\n{folder}")
                self._give_up_run(f"엑셀 폴더가 없다: {folder}")
                return

        company = self.company_var.get().strip()
        user_id = self.id_var.get().strip()
        password = self.pw_var.get()

        values = self._saved_values(exe, company, user_id, password)
        try:
            # 테스트 환경 편의를 위해 비밀번호도 저장한다(평문).
            # settings.local.json 은 git에 포함되지 않는다.
            save_local(**values)
        except (OSError, ValueError, RuntimeError) as exc:     # RuntimeError: 비밀 감싸기 실패(SecretError)
            # ★ 저장에 실패해도 **이번 실행에는 화면 값을 쓴다.** 예전에는
            #   `save_local` 안에서만 SETTINGS 를 갱신해서, 저장이 막히면
            #   화면에 새로 넣은 비밀번호가 아니라 예전 값으로 돌았다.
            log.warning("설정 저장 실패(화면 값으로 실행은 계속): %s", exc)
            for key, value in values.items():
                if hasattr(SETTINGS, key):
                    setattr(SETTINGS, key, value)

        self.step_pane.reset()      # 지난 실행의 표가 남아 있으면 헷갈린다
        self.token = cancel.CancelToken()
        self.report_path = None
        self.report_btn.configure(state="disabled")
        self._set_form_visible(False)   # 이제부터 화면은 진행 상황을 보여 준다
        self._set_progress_visible(True)
        self._open_overlay()
        self._set_busy(True)
        self.status_var.set("실행 중...")
        log.info(
            "실행 요청: %s / 업체코드=%s / 아이디=%s / 비밀번호=%s",
            Path(exe).name, company, user_id,
            PASSWORD_MASK * len(password),  # 로그에 평문을 남기지 않는다
        )
        threading.Thread(
            target=self._run_worker,
            args=(exe, company, user_id, password, start_step),
            daemon=True,
        ).start()

    def on_stop(self) -> None:
        """[중단]. **지금 기다리는 중에** 빠져나온다.

        되돌리기가 아니다. 이미 저장된 것은 그대로 남는다. 그래서 무엇이
        일어나는지 먼저 확인받는다.
        """
        if self.token is None or self.token.cancelled:
            return
        if not messagebox.askyesno(
                "중단",
                "진행 중인 작업을 중단할까요?\n\n"
                "· 지금 기다리는 단계에서 빠져나옵니다.\n"
                "· **이미 저장된 것은 되돌리지 않습니다** "
                "(매출처리·물류 저장은 그대로 남습니다).\n"
                "· ERPia 창은 열린 채로 둡니다."):
            return
        self.token.cancel("화면에서 [중단]")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("중단 요청 — 지금 단계에서 빠져나옵니다...")

    def _saved_values(self, exe: str, company: str,
                      user_id: str, password: str) -> dict[str, object]:
        """실행할 때 설정 파일에 남길 값. **하위 창이 항목을 더할 수 있다.**"""
        return dict(
            target_exe=exe,
            target_work_dir=str(Path(exe).parent),
            login_company_code=company,
            login_user_id=user_id,
            login_password=password,
            collect_sources=self._sources(),
            excel_dir=self.excel_var.get().strip().strip('"') or None,
            delivery_company=self.courier_var.get().strip() or None,
            delivery_box=self.box_var.get().strip() or None,
            logistics_mode=self.mode_var.get(),
        )

    def _run_worker(self, exe: str, company: str,
                    user_id: str, password: str, start_step: str = "") -> None:
        """별도 스레드. UI 위젯을 직접 건드리지 않고 root.after 로 넘긴다.

        업무 흐름은 `orchestrator/erpia_flow.py` 에 있다. 여기서는 입력값을
        넘기고 결과·오류만 화면에 옮긴다.
        """
        # ★ **지난 회차의 인스턴스를 잊는다.** 남겨 두면, 이번 회차가 ERPia 를 띄우기
        #   **전에** 실패할 때(메일 인증 실패 등) 아래 예외 처리가 지난 회차의 로그인된
        #   ERPia 를 닫는다 (09-21 검토). 이번 회차가 띄우면 `_remember_target` 이 채운다.
        self.target = None
        hooks = Hooks(
            status=lambda text: self.root.after(0, self.status_var.set, text),
            on_target=self._remember_target,
            on_login=lambda title: self.root.after(0, self._on_logged_in, title),
            # 단계 표는 **GUI 스레드에서만** 그린다. 사진(StepEvent)은 얼려서
            # 오므로 나중에 그려도 값이 바뀌지 않는다.
            on_step=lambda event: self.root.after(0, self._on_step, event),
            token=self.token,
            confirm=self._ask,
            # 실패를 사람 말로 (`orchestrator/friendly.py`). 원문은 파일 로그에만.
            explain=friendly.explain,
        )
        # 서버 보고 (09-22). 설정에 서버 키가 없으면 None — 지금처럼 돈다. 큐에 넣기만 해 흐름을 세우지 않는다.
        reporter = self._reporter()
        options = erpia_flow.Options(
            exe=exe,
            company=company,
            user_id=user_id,
            password=password,
            sources=self._sources(),
            excel_dir=self.excel_var.get().strip().strip('"'),
            courier=self.courier_var.get().strip(),
            box=self.box_var.get().strip(),
            mode=self.mode_var.get(),
            start_step=start_step,
        )

        # 리포트에 적을 한 줄. 어느 갈래로 끝나든 채워 둔다.
        # ★ 사용자에게 보이는 것(창·오버레이·리포트·자동 실행 기록)은 전부 **사람 말**이다
        #   (`hooks.failure_text`). 예외 원문은 아래 `log.*` 로 파일 로그에만 남는다.
        outcome = "실행이 끝나지 않았다"
        # 리포트 머리줄. 예약으로 돈 것인지 사람이 누른 것인지 (09-21 검토).
        trigger = self._trigger()
        # 기능 이름도 **시작할 때** 잡는다 — 끝에서 읽으면 GUI 스레드가 먼저 예약 회차 표시를 거둬
        # 창 체크 기능이 이력·서버에 남았다 (10-01 검토. [멈춘 곳부터 다시] 가 이 이름으로 계산한다)
        names = self._module_names()
        started_at = time.time()
        if reporter is not None:
            reporter.start(hooks, trigger=trigger, modules=names)
        try:
            result = self._call_flow(options, hooks)
        except cancel.Cancelled as exc:
            # ★ 실패가 아니다. 사용자가 시킨 것이다. 오류 창을 띄우지 않는다.
            #   ERPia 창도 닫지 않는다 — 어디까지 됐는지 봐야 하고,
            #   이어서 재개할 때 그 인스턴스에 붙는다.
            outcome = f"중단됨 — {cancel.USER_TEXT}"
            log.warning("%s", exc)
            self.root.after(0, self._on_cancelled, cancel.USER_TEXT)
        except NotImplementedError as exc:
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.error("%s", exc)
            self.root.after(0, self._on_failed, message)
        except AlreadyRunningError as exc:
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.error("%s", exc)
            self.root.after(0, self._on_already_running, message)
        except LoginError as exc:
            # 로그인에 실패하면 방금 띄운 인스턴스를 닫는다.
            # 로그인 창만 남은 프로세스가 계속 쌓이면 다음 실행을 방해한다.
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.error("%s", exc)
            self._close_target(self.target)
            self.root.after(0, self._on_failed, message)
        except ApplicationError as exc:
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.error("%s", exc)
            self.root.after(0, self._on_failed, message)
        except (ScreenError, logistics_wait.ScreenError, logistics.ScreenError,
                ui.ControlNotFound, ui.ControlDisabled) as exc:
            # 화면 상태 문제. 원문(파일 로그)에 어느 컨트롤인지가 있다.
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.error("%s", exc)
            self.root.after(0, self._on_failed, message)
        except Exception as exc:
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.exception("예상치 못한 오류")
            self._close_target(self.target)
            self.root.after(0, self._on_failed, message)
        else:
            outcome = result.summary
            self.root.after(0, self._on_finished, result.summary, list(hooks.attention))
        finally:
            # ★ **실패하거나 중단돼도 리포트를 만든다.** 오히려 그때가 가장
            #   필요하다 — 어디까지 했고 무엇이 남았는지가 단계 기록에 있다.
            #   `hooks.report()` 는 흐름이 Result 를 못 돌려줬어도 채워져 있다.
            #   **확인할 것**(`hooks.attention`)은 실패한 실행에도 싣는다.
            step_rows = hooks.report()
            path = report.write(Result(
                summary=outcome, steps=step_rows,
                details={"attention": list(hooks.attention), "trigger": trigger}))
            # 지난 실행 표에 한 줄 (09-22). 손에 있는 것만 적는다 — 로그를 다시 읽지 않는다.
            entry = history.record(summary=outcome, step_rows=step_rows, attention=list(hooks.attention),
                                   trigger=trigger, report=path, modules=names,
                                   started_at=started_at)
            if reporter is not None:
                reporter.finish(entry)
            self.root.after(0, self._on_report_ready, path)

    def _module_names(self) -> list[str]:
        """이번 실행의 기능 이름 (이력 표의 '기능' 칸). 기능 선택이 없는 창은 빈 목록 = 전체."""
        return []

    def _trigger(self) -> str:
        """이번 실행이 어떻게 시작됐나 (리포트·이력·서버). 실행용 창은 '원격 실행' 을 더한다."""
        return "예약 실행" if self.unattended else "직접 실행"

    # --- 서버 보고 (09-22) --------------------------------------------------
    IDLE_REPORT_MS = 15 * 60 * 1000     # 앱이 떠 있는 동안 "살아 있음·다음 예약" 을 보내는 주기
    IDLE_FIRST_MS = 5 * 1000            # 켜고 첫 보고까지 (09-28)

    def _reporter(self):
        """지금 설정으로 보고기를 만든다. 설정이 바뀌면 새로 만든다. 빌드 ID 가 없으면 None."""
        keys = tuple((getattr(SETTINGS, k, None) or "") for k in
                     ("server_url", "server_anon_key", "server_build_id"))
        current = getattr(self, "_reporter_obj", None)
        if current is not None and getattr(self, "_reporter_keys", None) == keys:
            return current
        if current is not None:
            current.close()
        self._reporter_keys = keys
        self._reporter_obj = telemetry.from_settings(SETTINGS)
        return self._reporter_obj

    def _report_idle(self) -> None:
        """15분마다. 실행 중이면 심박이 대신하므로 건너뛴다."""
        self.send_idle()
        self.root.after(self.IDLE_REPORT_MS, self._report_idle)

    def send_idle(self, force: bool = False) -> None:
        """"살아 있음·다음 예약·멈춤" 을 지금 보낸다. 예약을 멈추거나 풀었을 때도 부른다 (09-28).

        `force` 면 실행 중에도 보낸다 — 심박은 예약 멈춤을 싣지 않아 웹의 [일시정지]·[계속하기] 가 틀린다.
        """
        try:
            reporter = self._reporter()
            runner = self.autorunner
            if reporter is not None and (force or not self.busy):
                reporter.idle(next_run_at=runner.next_run if runner else None,
                              auto_run_enabled=bool(runner and runner.plan.enabled),
                              paused=bool(runner and runner.paused))
        except Exception as exc:                            # noqa: BLE001 — 보고 때문에 창이 죽으면 안 된다
            log.warning("서버에 대기 상태를 보내지 못했다: %s", exc)

    def _on_autorun_skipped(self, planned_at: float, reason: str) -> None:
        reporter = self._reporter()
        if reporter is not None:
            reporter.skipped(planned_at, reason)

    def open_history(self) -> None:
        """[지난 실행] — 이력 표 창을 띄운다."""
        HistoryWindow(self.root)

    def toggle_overlay(self) -> None:
        """오버레이만 여닫는다. 본 창과 진행 화면은 그대로 둔다.

        오버레이는 화면 전체를 덮으므로 **뒤에 있는 대상 프로그램을 봐야 할
        때**가 있다. 오버레이 자신에게도 [숨기기] 가 있지만, 그것까지 놓쳤을
        때 되돌릴 곳이 본 창에 있어야 한다.
        """
        if self.overlay is None:
            return
        self.overlay.toggle()
        self._sync_overlay_btn()

    def _sync_pause(self) -> None:
        """오버레이에서 [일시정지]·[계속하기] 를 눌렀다. 창에 같은 단추가 있으면 맞춘다 (실행용 창, 09-29)."""

    def _sync_overlay_btn(self) -> None:
        button = getattr(self, "overlay_btn", None)
        if button is None:
            return
        if self.overlay is None:
            button.configure(state="disabled", text="오버레이")
            return
        button.configure(state="normal",
                         text="오버레이 숨김" if self.overlay.shown else "오버레이 보기")

    # ------------------------------------------------- 오버레이 / 리포트
    def _open_overlay(self) -> None:
        """ERPia 위에 뜨는 진행 상황 창을 연다. 지난 것이 있으면 닫고 새로 만든다.

        오버레이가 없어도 실행은 된다. 만들지 못하면 로그만 남기고 넘어간다 —
        **보조 장치가 본 동작을 막으면 안 된다.**
        """
        self._close_overlay()
        try:
            self.overlay = Overlay(self.root, on_stop=self.on_stop,
                                   token=self.token,
                                   on_report=self.open_report,
                                   on_pause=self._sync_pause,
                                   title=self.root.title())
            self.overlay.show_plan(steps.preview(self._plan()))
        except Exception as exc:
            log.warning("진행 오버레이를 만들지 못했다(계속): %s: %s",
                        type(exc).__name__, exc)
            self.overlay = None
        self._sync_overlay_btn()

    def _close_overlay(self) -> None:
        if self.overlay is None:
            return
        try:
            self.overlay.close()
        except Exception as exc:
            log.debug("오버레이를 닫지 못했다: %s", type(exc).__name__)
        self.overlay = None
        self._sync_overlay_btn()

    def _on_step(self, event) -> None:
        """단계 사진 하나를 **화면 셋**에 그린다. GUI 스레드에서만 불린다."""
        self.step_pane.update(event)
        if self.overlay is not None:
            try:
                self.overlay.update(event)
            except Exception as exc:
                log.debug("오버레이 갱신 실패(계속): %s", type(exc).__name__)

    def _on_report_ready(self, path) -> None:
        """리포트가 만들어졌다. 열 수 있게 한다."""
        self.report_path = path
        self.report_btn.configure(state="normal" if path else "disabled")
        if path:
            log.info("리포트를 만들었다: %s — [결과 보기] 로 볼 수 있다.", path)

    def open_report(self) -> None:
        """리포트를 기본 브라우저로 연다."""
        if not self.report_path:
            return
        import os

        try:
            os.startfile(str(self.report_path))   # noqa: S606 (Windows 전용)
        except Exception as exc:
            log.error("리포트를 열지 못했다: %s: %s", type(exc).__name__, exc)
            messagebox.showerror("리포트", f"열지 못했습니다:\n{self.report_path}")

    def _ask(self, question: str) -> bool:
        """작업 스레드가 사람에게 묻는다. **답이 올 때까지 기다린다.**

        tkinter 창은 GUI 스레드에서만 건드릴 수 있어서, 작업 스레드가 직접
        `messagebox` 를 띄우면 안 된다. 그래서 GUI 스레드에 예약해 두고
        `Event` 로 답을 기다린다.

        막히지 않는 이유: 기다리는 것은 **작업 스레드**이고, 창을 띄우는 것은
        GUI 스레드다. GUI 스레드는 이 답을 기다리지 않는다.
        """
        answer: dict[str, bool] = {}
        answered = threading.Event()

        def ask() -> None:
            try:
                answer["ok"] = bool(messagebox.askyesno("구간 실행 확인", question))
            finally:
                # 창을 못 띄웠더라도 **반드시** 풀어 준다. 그러지 않으면
                # 작업 스레드가 영원히 기다린다.
                answered.set()

        self.root.after(0, ask)
        answered.wait()
        return answer.get("ok", False)

    def _remember_target(self, target) -> None:
        """흐름이 인스턴스를 띄우면 **워커 스레드에서 바로** 붙잡는다.

        ★ 예전에는 `root.after(0, self._on_started, target)` 로만 넘겨서,
          `self.target` 대입이 GUI 스레드로 예약됐다. 실행~로그인 사이에
          실패하면 `self.target` 이 아직 None 이라 `_close_target(None)` 이
          조용히 반환하고, 막겠다던 프로세스가 남았다 (2026-09-10 감사).
        """
        self.target = target
        self.root.after(0, self._on_started, target)

    def _call_flow(self, options, hooks) -> "Result":
        """실제 흐름을 부른다. **여기만** 하위 창에서 바꾼다.

        예전에는 통합 창이 `_run_worker` 를 통째로 재정의해서, 부모의 예외
        분기(로그인 실패 시 인스턴스 닫기, traceback 남기기, 중복 실행 안내)를
        전부 잃었다 (2026-09-10 감사).
        """
        return erpia_flow.run(options, hooks=hooks)

    def _on_finished(self, summary: str, attention=()) -> None:
        """끝났다. **확인할 것**이 있으면 그 수를 맨 앞에 쓴다 — '완료' 만 보고 지나치지 않게."""
        self._set_busy(False)
        if attention:
            summary = f"확인할 것 {len(attention)}건 (리포트 맨 위) / {summary}"
        user_log().info("실행이 끝났습니다 — %s", summary.replace(" / ", " · "))
        self.status_var.set(summary.split(" / ")[0] if attention else summary)
        self._tell_overlay("done", summary)
        self._end_autorun_cycle(autorun.DONE, summary)

    def _tell_overlay(self, state: str, summary: str = "",
                      error: str = "") -> None:
        """오버레이에 **끝났다**고 알린다. 결과 띠가 거기서 나온다.

        오버레이가 없어도 실행은 된다. 실패해도 로그만 남긴다 —
        **보조 장치가 본 동작을 막으면 안 된다.**
        """
        if self.overlay is None:
            return
        try:
            self.overlay.finish(state, summary, error)
        except Exception as exc:
            log.debug("오버레이 결과 표시 실패(계속): %s", type(exc).__name__)

    def _close_target(self, target: Target | None) -> None:
        if target is None or not target.started:
            return  # 우리가 띄우지 않은 인스턴스는 건드리지 않는다
        try:
            target.close()
        except Exception as exc:
            log.warning("인스턴스 정리 실패: %s", exc)

    def _on_started(self, target: Target) -> None:
        self.target = target
        how = "실행함" if target.started else "이미 실행 중 → 연결함"
        titles = target.window_titles()
        log.info("연결 완료: pid=%s, %s", target.pid, how)
        if titles:
            log.info(
                "현재 창 %d개: %s",
                len(titles),
                ", ".join(t or "(제목 없음)" for t in titles[:5]),
            )
        else:
            log.warning("창 목록이 비어 있다. 스플래시 중이거나 별도 프로세스일 수 있다.")
        # 사용자에게는 pid·실행 파일 이름을 보이지 않는다 (위 로그에 있다).
        self.status_var.set("ERPia를 실행했습니다" if target.started
                            else "켜져 있는 ERPia에 연결했습니다")

    def _on_logged_in(self, main_title: str) -> None:
        # 중간 단계다. 이어서 화면 진입/수집이 남았으므로 busy 를 풀지 않는다.
        # 창 제목에는 업체코드·아이디가 들어 있다. 화면에는 쓰지 않는다.
        log.info("로그인 완료 — %s", main_title)
        self.status_var.set("ERPia 로그인 완료")

    def _on_already_running(self, message: str) -> None:
        """중복 실행 불가. 안내만 하고 [실행]을 다시 누를 수 있게 둔다."""
        self._set_busy(False)
        self.status_var.set(message.splitlines()[0])
        self._tell_problem("이미 실행 중", message, kind="info")
        self._end_autorun_cycle(autorun.FAILED, message)

    def _on_cancelled(self, message: str) -> None:
        """중단은 **실패가 아니다.** 오류 창을 띄우지 않는다.

        단계 표는 그대로 남긴다. 어느 단계에서 멈췄는지가 다음 실행의 근거다.
        """
        self._set_busy(False)
        self.status_var.set(f"중단됨 — {message}")
        self._end_autorun_cycle(autorun.CANCELLED, message)

    def _on_failed(self, message: str) -> None:
        """`message` 는 **사람 말**이다 (`Hooks.failure_text`). 두 줄: 무슨 일 / 할 일."""
        self._tell_overlay("failed", "", message)
        self._set_busy(False)
        user_log().error("실행을 멈췄습니다 — %s", message.replace("\n", " / "))
        self.status_var.set("실패 — " + message.splitlines()[0])
        self._tell_problem("실행 실패", message, kind="error")
        # ★ 예약 회차를 **실패로 닫는다** (09-21 발견). 빠져 있어서 무인 실행이 실패하면
        #   회차 기록이 '진행 중' 으로 남고 상태줄이 '실행 중...' 에 멈췄으며,
        #   `unattended` 가 그대로라 다음 **손 실행**의 오류 창까지 막혔다.
        #   `_tell_problem` 뒤에 둔다 — 무인 표시를 보고 창을 띄우지 않아야 한다.
        self._end_autorun_cycle(autorun.FAILED, message.replace("\n", " / "))

    # --- 자동 실행(예약) 과의 연결 ---------------------------------------
    AUTORUN_TICK_MS = 1000       # 1초에 한 번 시각을 본다. 분 단위 예약에 충분하다

    def attach_autorun(self, plan, paused: tuple[bool, float] = (False, 0.0)) -> autorun.AutoRunner:
        """예약 시계를 달고 돌린다. 창을 닫으면 `detach_autorun()` 으로 뗀다.
        `paused` 는 첫 박자 전에 넣는다 — 뒤에 넣으면 첫 박자가 멈춘 예약의 경고·점검을 띄운다."""
        self.autorunner = autorun.AutoRunner(
            plan,
            start=self._start_scheduled_run,
            busy=lambda: self.busy or self._starting,
            locked=process.screen_locked,
            on_skip=self._on_autorun_skipped,
        )
        self.autorunner.paused, self.autorunner.paused_since = paused
        self.autorunner.reschedule()
        self._tick_autorun()
        # 서버에 "살아 있음·다음 예약" (09-22). 예약 시계가 있는 창 = 무인으로 떠 있는 창이다.
        # 첫 번은 곧바로 — 15분 뒤에야 보내면 웹이 지난번 프로그램의 '일시정지' 를 계속 보여 준다 (09-28 실기)
        self.root.after(self.IDLE_FIRST_MS, self._report_idle)
        return self.autorunner

    def detach_autorun(self) -> None:
        if self._autorun_job is not None:
            try:
                self.root.after_cancel(self._autorun_job)
            except tk.TclError:
                # 창이 이미 닫혔다. 취소할 일정도 함께 사라진 상태다.
                pass
            self._autorun_job = None

    def _tick_autorun(self) -> None:
        try:
            if self.autorunner is not None:
                self.autorunner.tick()
                self.on_autorun_tick()
        except Exception:
            # ★ 여기서 예외가 새면 다음 박자를 못 잡아 **예약·원격 명령이 영구히 멈춘다** (09-28 검토)
            log.exception("자동 실행 시계 한 박자에서 오류 — 다음 박자는 그대로 돈다")
        finally:
            self._autorun_job = self.root.after(self.AUTORUN_TICK_MS,
                                                self._tick_autorun)

    def on_autorun_tick(self) -> None:
        """화면을 갱신할 자리. 기본은 아무 것도 안 한다 (하위 창이 덮는다)."""

    def _start_scheduled_run(self) -> None:
        """예약이 흐름을 시작한다. **여기서만 무인 표시를 켠다.**"""
        self.unattended = True
        log.info("예약 실행 — 사람을 기다리는 창을 띄우지 않는 모드로 시작한다")
        self.on_run()

    def _give_up_run(self, reason: str) -> None:
        """[실행] 이 **시작도 못 하고** 돌아섰다 (경로가 없다, 값이 비었다).

        ★ 무인 실행에서 이것을 알리지 않으면 예약 시계가 "아직 도는 중" 으로
          알고 영구히 기다린다. **다음 회차가 영원히 오지 않는다.**
          그래서 되돌아서는 모든 갈래에서 이것을 부른다.
        """
        self._end_autorun_cycle(autorun.FAILED, reason)

    def _end_autorun_cycle(self, state: str, summary: str) -> None:
        """이번 회차가 끝났다고 예약 시계에 알린다. 손으로 돌린 것이면 아무 일도 안 한다."""
        if not self.unattended:
            return
        self.unattended = False
        if self.autorunner is not None:
            self.autorunner.finished(state, summary)

    # --- 사람에게 알리는 단일 창구 ---------------------------------------
    def _tell_problem(self, title: str, message: str,
                      kind: str = "error") -> None:
        """문제를 알린다. **무인 실행 중에는 창을 띄우지 않는다.**

        창을 띄우는 자리를 코드 곳곳에 두면 무인 모드를 추가할 때마다 하나를
        빠뜨린다. 그 하나가 예약을 전부 죽인다. 그래서 창구를 하나로 둔다.

        무인일 때도 **정보를 버리지 않는다** — 로그와 상태줄에 그대로 남고,
        자동 실행 회차 기록에도 요약이 들어간다. 사람은 나중에 그것을 본다.
        """
        if self.unattended:
            log.warning("[무인] %s — %s (창을 띄우지 않는다)", title, message)
            self.status_var.set(f"{title} — {message}")
            return
        if kind == "error":
            messagebox.showerror(title, message)
        elif kind == "warning":
            messagebox.showwarning(title, message)
        else:
            messagebox.showinfo(title, message)


def _ui_scale() -> float:
    """화면 배율. 읽지 못하면 1.0.

    `utils/dpi.ensure_dpi_awareness()` 로 per-monitor 인식을 켜면 **Windows 가
    창을 대신 늘려주지 않는다.** tkinter 는 96dpi 기준이라 150%/200% 화면에서
    창과 글자가 그만큼 작게 나온다. 그래서 직접 맞춘다.
    """
    from utils.dpi import scale_info

    try:
        return max(float(scale_info().get("scale") or 1.0), 1.0)
    except Exception as exc:
        log.debug("화면 배율을 읽지 못했다(1.0으로 본다): %s", type(exc).__name__)
        return 1.0

