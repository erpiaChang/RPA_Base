r"""[개발 전용] 어떤 창을 띄울지 고르는 화면.

배포본에는 **들어가지 않는다.** 각 기능은 자기 진입점(`main_collect.py` /
`main_erpia.py` / `main_full.py`)으로 따로 빌드되고, 그 배포본에는 다른 기능의
코드도 문구도 들어가지 않는다 (`build_*.spec` 의 `FORBIDDEN`).

이 파일은 개발 중에 셋을 오가며 확인하려고 둔 것이다.
`build_*.spec` 의 `DEV_ONLY` 에 들어 있어 빌드에서 제외된다.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


CHOICES = (
    ("주문 엑셀 수집", "gui.collect_app"),
    ("ERPia 자동화", "gui.erpia_app"),
    ("통합", "gui.full_app"),
)


def _open(module_name: str, chooser: tk.Tk) -> None:
    import importlib

    chooser.destroy()
    module = importlib.import_module(module_name)
    module.run()


def run() -> int:
    from utils.dpi import ensure_dpi_awareness
    from utils.logger import setup_logging

    ensure_dpi_awareness()
    setup_logging()   # 로그 파일 경로는 setup_logging() 이 남긴다

    root = tk.Tk()
    root.title("[개발] 실행할 기능 선택")
    root.geometry("360x200")
    ttk.Label(root, padding=12, justify="left",
              text="개발용 선택 화면입니다. 배포본에는 들어가지 않습니다."
              ).pack(anchor="w")
    for label, module_name in CHOICES:
        ttk.Button(root, text=label, width=28,
                   command=lambda m=module_name: _open(m, root)).pack(pady=4)
    root.mainloop()
    return 0
