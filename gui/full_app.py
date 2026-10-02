r"""통합 실행 GUI.

메일에서 주문 엑셀을 받아 저장하고, 이어서 업무 프로그램에 업로드·처리까지 한다.

흐름은 `orchestrator/full_flow.py` 에 있다. 이 창은 입력만 받는다.
입력 화면은 ERPia 창을 그대로 쓰고, [실행] 이 부르는 흐름만 통합으로 바꾼다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.common import apply_scaling
from gui.erpia_app import ErpiaWindow
from orchestrator import full_flow, steps_collect
from orchestrator.common import Result


class FullWindow(ErpiaWindow):
    """ERPia 입력 화면을 그대로 쓰되, 흐름만 통합으로 바꾼다."""

    # ★ 통합은 구간 실행을 지원하지 않는다. 메일 수집부터 이어지는 흐름이고,
    #   수집은 매니페스트가 이미 중복을 막고 있어 중간부터 시작할 일이 없다.
    #   `full_flow.run()` 도 시작 단계가 들어오면 막는다 — 화면과 코드가
    #   어긋나지 않게 **두 곳에서** 막는다.
    SUPPORTS_SEGMENT = False

    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root)
        root.title("통합 자동화")
        # 올릴 엑셀은 방금 받은 것들이다. 폴더를 사람이 지정하지 않는다.
        self.excel_label.configure(text="엑셀")
        self.excel_help.configure(
            text="엑셀수집을 고르면, 방금 받은 엑셀을 파일명의 사이트명으로 각각 올립니다.\n"
                 "  · 올릴 목록은 자동으로 정해집니다. 폴더를 고를 필요가 없습니다")
        self._update_source_fields()

    def _needs_excel_dir(self) -> bool:
        """올릴 목록을 방금 받은 것에서 얻는다. 폴더 입력이 필요 없다."""
        return False

    def _plan(self) -> list:
        """통합은 **수집 + 그 뒤 처리**가 한 계획이다 (`full_flow.run` 과 같다).

        여기서 ERPia 단계만 그리면, 실행을 시작하는 순간 표가 8단계에서
        11단계로 늘어난다. 그러면 시작 전에 본 `N/M` 이 거짓말이 된다.
        """
        return steps_collect.plan() + super()._plan()

    def _call_flow(self, options, hooks) -> "Result":
        """흐름만 통합으로 바꾼다.

        ★ `_run_worker` 를 통째로 재정의하지 않는다. 그렇게 하면 부모의 예외
          분기(로그인 실패 시 인스턴스 닫기 / traceback / 중복 실행 안내)를
          전부 잃는다 (2026-09-10 감사에서 실제로 그런 상태였다).
        """
        return full_flow.run(options, hooks=hooks)


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

    # ★ 세 창이 **같은 방식으로** 배율을 맞춘다. 예전에는 통합 창만 이것이
    #   빠져서, 창 크기는 배율만큼 커지는데 글자는 96dpi 기준으로 남아
    #   고배율 PC 에서 큰 창에 절반 크기 글자가 나왔다 (2026-09-10 감사).
    apply_scaling(root)

    FullWindow(root)
    root.mainloop()
    return 0
