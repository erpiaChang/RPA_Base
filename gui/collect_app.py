r"""주문 엑셀 수집 GUI.

메일에서 주문 엑셀을 내려받아 날짜·차수 폴더에 저장한다.

계정·검색 조건·저장 경로는 이 화면이 아니라 `config/settings.local.json` 에서 읽는다
(설명은 `docs/SETTINGS.md`). 화면에는 **지금 설정이 무엇인지**만 보여 주고,
실행 전에 준비 상태를 점검한다.

- [점검] : 설정 조합과 인증 경로 준비 상태만 확인한다. 로그인하지 않는다
- [미리보기] : 로그인·인증까지 하고 **대상 메일만** 확인한다. 메일을 열지 않는다
- [실행] : 메일을 열고 첨부를 내려받아 저장한다

tkinter 는 표준 라이브러리다. 추가 패키지가 필요 없다.
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from config.settings import SETTINGS
from gui.common import (DetailPane, FormToggle, LogPane,
                        apply_scaling, small_font, ui_scale)
from gui.pipeline import PipelinePane
from gui.overlay import Overlay
from gui.history_window import HistoryWindow
from orchestrator import collect_flow, history, report, steps, steps_collect, telemetry
from orchestrator.common import Hooks, Result
from utils import cancel
from utils.logger import get_logger

log = get_logger(__name__)

HELP = (
    "설정은 config\\settings.local.json 에서 읽습니다 (docs\\SETTINGS.md 참고).\n"
    "  · 대상 메일: 발신자 주소가 같고, 제목에 [사이트명] 이 있고, 읽지 않은 메일\n"
    "  · 저장 위치: 기준경로 아래 날짜 폴더 → 날짜_차수_사이트명 폴더"
)


class CollectWindow(FormToggle):
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.busy = False
        self.status_var = tk.StringVar(value="준비됨")
        # 이번 실행의 중단 토큰. 실행할 때마다 새로 만든다 — 한 번 중단한 토큰을
        # 다시 쓰면 다음 실행이 시작하자마자 중단된다.
        self.token: cancel.CancelToken | None = None
        # 브라우저가 화면을 덮으므로 진행 상황은 오버레이로도 보여 준다.
        self.overlay: Overlay | None = None
        self.report_path = None

        root.title("주문 엑셀 수집")
        scale = apply_scaling(root)
        # 단계 표 + 상세 표 + 로그 (`tools/probe_gui.py` 로 재서 정했다).
        root.geometry(f"{int(780 * scale)}x{int(760 * scale)}")
        root.minsize(int(640 * scale), int(540 * scale))

        self._build()
        self.log_pane.drain(root)
        self._show_settings()
        # 실행 전에 무엇을 할지 보여 준다. 수집 단계는 입력에 따라 달라지지 않는다.
        self.step_pane.show_plan(steps.preview(steps_collect.plan()))

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)   # 진행 단계
        root.rowconfigure(3, weight=1)   # 상세
        root.rowconfigure(4, weight=1)   # 진행 로그

        form = self.form = ttk.Frame(root, padding=(12, 12, 12, 6))
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)

        info = ttk.LabelFrame(form, text="현재 설정", padding=(10, 6))
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        self.info_labels: dict[str, tk.StringVar] = {}
        for row, name in enumerate(("메일 계정", "발신자", "사이트", "저장 경로",
                                    "인증 경로")):
            ttk.Label(info, text=name, width=10, anchor="w").grid(
                row=row, column=0, sticky="w", pady=1)
            var = tk.StringVar()
            ttk.Label(info, textvariable=var, anchor="w").grid(
                row=row, column=1, sticky="ew", pady=1)
            self.info_labels[name] = var

        ttk.Label(form, text=HELP, font=small_font(), foreground="#666666",
                  justify="left").grid(row=1, column=0, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(form)
        buttons.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.check_btn = ttk.Button(buttons, text="점검", width=10,
                                    command=lambda: self._start(mode="check"))
        self.check_btn.grid(row=0, column=0, padx=(0, 8))
        self.preview_btn = ttk.Button(buttons, text="미리보기", width=10,
                                      command=lambda: self._start(mode="preview"))
        self.preview_btn.grid(row=0, column=1, padx=(0, 8))
        self.run_btn = ttk.Button(buttons, text="실행", width=10,
                                  command=lambda: self._start(mode="run"))
        self.run_btn.grid(row=0, column=2)

        status = ttk.Frame(root, padding=(12, 0, 12, 6))
        status.grid(row=1, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, anchor="w").grid(
            row=0, column=0, sticky="ew")
        # 실행 중에만 켠다. 누르면 지금 기다리는 단계에서 빠져나온다.
        self.stop_btn = ttk.Button(status, text="중단", width=8,
                                   command=self.on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(8, 0))
        self.report_btn = ttk.Button(status, text="결과 보기", width=12,
                                     command=self.open_report, state="disabled")
        self.report_btn.grid(row=0, column=2, padx=(8, 0))
        # 실행하면 입력 폼이 사라지고 그 자리를 진행 화면이 쓴다.
        # 다시 보고 싶을 때 누른다 (사용자 요청 2026-09-16).
        self.form_btn = ttk.Button(status, text="설정", width=8,
                                   command=self.toggle_form)
        self.form_btn.grid(row=0, column=3, padx=(8, 0))
        # 오버레이는 화면 전체를 덮는다. 뒤를 봐야 할 때가 있어서
        # 본 창에서도 여닫을 수 있게 한다 (사용자 요청 2026-09-16).
        self.overlay_btn = ttk.Button(status, text="오버레이", width=10,
                                      command=self.toggle_overlay,
                                      state="disabled")
        self.overlay_btn.grid(row=0, column=4, padx=(8, 0))
        # 지난 실행 이력 (09-22, `orchestrator/history.py`)
        self.history_btn = ttk.Button(status, text="지난 실행", width=10,
                                      command=self.open_history)
        self.history_btn.grid(row=0, column=5, padx=(8, 0))

        # 단계 표. 수집은 3단계뿐이라 짧게 잡는다.
        self.step_pane = PipelinePane(root, row=2, scale=ui_scale())
        # 받은 메일을 **한 줄씩** 보여 준다. 숫자만으로는 무엇이 빠졌는지 모른다.
        self.detail_pane = DetailPane(root, row=3, height=6)
        self.step_pane.attach_detail(self.detail_pane)

        self.log_pane = LogPane(root, row=4)
        # 켤 때는 입력만 보여 준다. 진행 화면은 [실행] 뒤에 나온다.
        self.progress_frames = (self.step_pane.frame,
                                self.detail_pane.frame, self.log_pane.frame)
        self._set_progress_visible(False)

    def _show_settings(self) -> None:
        base = SETTINGS.download_base_dir or "(설정 필요)"
        self.info_labels["메일 계정"].set(SETTINGS.mail_user_id or "(설정 필요)")
        self.info_labels["발신자"].set(", ".join(SETTINGS.mail_senders) or "(제한 없음)")
        # 비우는 것이 **유효한 설정**이다 (대괄호가 있으면 대상). 바로 위
        # `mail_senders` 와 같은 의미인데 예전에는 "(설정 필요)" 로 보였다.
        self.info_labels["사이트"].set(", ".join(SETTINGS.mail_sites) or "(제한 없음)")
        self.info_labels["저장 경로"].set(str(Path(base)) if base else "(설정 필요)")
        self.info_labels["인증 경로"].set(SETTINGS.sms_source or "(설정 필요)")

    # -------------------------------------------------------------- actions
    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.check_btn, self.preview_btn, self.run_btn):
            widget.configure(state=state)
        self.stop_btn.configure(state="normal" if busy else "disabled")
        if self.overlay is not None:
            self.overlay.set_busy(busy)

    def on_stop(self) -> None:
        """[중단]. 지금 기다리는 단계에서 빠져나온다.

        수집은 **우리 데이터를 바꾸지 않는다.** 이미 받은 파일과 읽음 처리된
        메일은 그대로 남고, 매니페스트에 기록돼 다음 실행에서 다시 받지 않는다.
        되돌릴 것이 없으므로 따로 확인을 받지 않는다.
        """
        if self.token is None or self.token.cancelled:
            return
        self.token.cancel("화면에서 [중단]")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("중단 요청 — 지금 단계에서 빠져나옵니다...")

    # 실행 전에 있어야 하는 설정. 없으면 브라우저를 띄우기 전에 막는다.
    REQUIRED = ("mail_url", "mail_user_id", "mail_password", "download_base_dir",     # 기본값 없음 (10-02)
                "browser_channel", "phone_os", "sms_source", "sms_keyword")

    def _missing_settings(self) -> list[str]:
        return list(SETTINGS.missing(*self.REQUIRED))

    def _start(self, mode: str) -> None:
        if self.busy:
            return
        # 필수 설정을 먼저 본다 — 안 보면 Edge 를 띄운 뒤 `webmail.login` 의 `SETTINGS.require` 에서 멈춘다.
        missing = self._missing_settings()
        if missing:
            names = ", ".join(missing)
            log.error("설정이 비어 있다: %s — config/settings.local.json 을 "
                      "채운 뒤 다시 실행할 것.", names)
            self.status_var.set(f"설정이 비어 있다: {names}")
            return
        self.step_pane.reset()      # 지난 실행의 표가 남아 있으면 헷갈린다
        self.token = cancel.CancelToken()
        self.report_path = None
        self.report_btn.configure(state="disabled")
        self._set_form_visible(False)
        self._set_progress_visible(True)
        self._open_overlay()
        self._set_busy(True)
        self.status_var.set("실행 중...")
        threading.Thread(target=self._worker, args=(mode,), daemon=True).start()

    def _worker(self, mode: str) -> None:
        """별도 스레드. UI 위젯을 직접 건드리지 않고 root.after 로 넘긴다."""
        hooks = Hooks(
            status=lambda text: self.root.after(0, self.status_var.set, text),
            # 단계 표는 GUI 스레드에서만 그린다. 사진(StepEvent)은 얼려서 온다.
            on_step=lambda event: self.root.after(0, self._on_step, event),
            token=self.token,
        )
        outcome = "실행이 끝나지 않았다"
        # 서버 보고 (09-22). 설정에 서버 키가 없으면 None. 점검·미리보기는 보내지 않는다.
        reporter = telemetry.from_settings(SETTINGS) if mode == "run" else None
        if reporter is not None:
            reporter.start(hooks, trigger="직접 실행")
        try:
            if mode == "check":
                from collect import auth_code

                summary = auth_code.preflight().replace("\n", " / ")
            else:
                result = collect_flow.run(hooks=hooks, dry_run=(mode == "preview"))
                summary = result.summary
        except cancel.Cancelled as exc:
            # ★ 실패가 아니다. 사용자가 시킨 것이다.
            outcome = f"중단됨 — {cancel.USER_TEXT}"
            log.warning("%s", exc)
            self.root.after(0, self._on_cancelled, cancel.USER_TEXT)
        except Exception as exc:
            # traceback 을 남긴다. `log.error` 만 쓰면 어디서 났는지 알 수 없다.
            # 화면·리포트에는 사람 말만 (`Hooks.failure_text`). 원문은 파일 로그에 있다.
            message = hooks.failure_text(exc)
            outcome = f"실패 — {message}"
            log.exception("수집 실패")
            self.root.after(0, self._on_failed, message)
        else:
            outcome = summary
            self.root.after(0, self._on_finished, summary)
        finally:
            # 실패·중단에도 만든다. 그때가 오히려 더 필요하다.
            step_rows = hooks.report()
            path = report.write(Result(summary=outcome, steps=step_rows,
                                       details={"attention": list(hooks.attention)}))
            # 지난 실행 표에 한 줄 (09-22). 시작 시각은 첫 단계의 것을 쓴다.
            entry = history.record(summary=outcome, step_rows=step_rows, attention=list(hooks.attention),
                                   trigger="직접 실행", report=path)
            if reporter is not None:
                reporter.finish(entry)
            self.root.after(0, self._on_report_ready, path)

    def open_history(self) -> None:
        """[지난 실행] — 이력 표 창을 띄운다."""
        HistoryWindow(self.root)

    # ------------------------------------------------- 오버레이 / 리포트
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

    def _sync_overlay_btn(self) -> None:
        button = getattr(self, "overlay_btn", None)
        if button is None:
            return
        if self.overlay is None:
            button.configure(state="disabled", text="오버레이")
            return
        button.configure(state="normal",
                         text="오버레이 숨김" if self.overlay.shown else "오버레이 보기")

    def _open_overlay(self) -> None:
        """브라우저 위에 뜨는 진행 상황 창. 못 만들어도 실행은 계속한다."""
        self._close_overlay()
        try:
            self.overlay = Overlay(self.root, on_stop=self.on_stop,
                                   token=self.token,
                                   title=self.root.title())
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
        self.step_pane.update(event)
        if self.overlay is not None:
            try:
                self.overlay.update(event)
            except Exception as exc:
                log.debug("오버레이 갱신 실패(계속): %s", type(exc).__name__)

    def _on_report_ready(self, path) -> None:
        self.report_path = path
        self.report_btn.configure(state="normal" if path else "disabled")
        if path:
            log.info("리포트를 만들었다: %s — [결과 보기] 로 볼 수 있다.", path)

    def open_report(self) -> None:
        if not self.report_path:
            return
        import os

        try:
            os.startfile(str(self.report_path))   # noqa: S606 (Windows 전용)
        except Exception as exc:
            log.error("리포트를 열지 못했다: %s: %s", type(exc).__name__, exc)

    def _on_finished(self, summary: str) -> None:
        self._set_busy(False)
        self.status_var.set(summary)

    def _on_cancelled(self, message: str) -> None:
        """중단은 실패가 아니다. 단계 표는 그대로 남긴다."""
        self._set_busy(False)
        self.status_var.set(f"중단됨 — {message}")

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        # 여러 줄 메시지는 상태줄에서 첫 줄만 보여 준다. 전체는 로그에 있다.
        self.status_var.set("실패: " + message.splitlines()[0])


def run() -> int:
    from utils.dpi import ensure_dpi_awareness
    from utils.logger import setup_logging

    ensure_dpi_awareness()
    setup_logging()   # 로그 파일 경로는 setup_logging() 이 남긴다

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass  # 테마가 없는 환경. 기본 테마로 진행한다.
    CollectWindow(root)
    root.mainloop()
    return 0
