r"""[조사 도구 — 읽기 전용] 우리 GUI 창이 실제로 만들어지는지 확인한다.

대상 프로그램(ERPia)은 건드리지 않는다. **우리 tkinter 창만** 만들어 보고
바로 닫는다. 업무 동작은 실행하지 않는다.

    .venv\Scripts\python.exe -m tools.probe_gui              모든 창
    .venv\Scripts\python.exe -m tools.probe_gui --only erpia 하나만

왜 필요한가: 런처를 띄웠는데 **창이 하나도 나타나지 않은** 일이 있었다
(2026-09-10). 프로세스는 살아 있어 "실행됐다"고 보이지만 화면에는 없다.
어느 단계에서 막히는지 로그로 가르는 도구가 없어 만들었다.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

# 창을 실제로 띄우는 모듈들. 런처는 함수 하나라 따로 다룬다.
APPS = {
    "collect": "gui.collect_app",
    "erpia": "gui.erpia_app",
    "full": "gui.full_app",
    "run": "gui.run_app",
}


def _probe_launcher() -> bool:
    """런처와 **같은 순서로** 창을 만들어 어디까지 가는지 본다."""
    import tkinter as tk

    print("[launcher] 1) DPI 인식")
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()

    print("[launcher] 2) tk.Tk()")
    root = tk.Tk()
    root.title("[조사] 런처")
    root.geometry("360x200")

    print("[launcher] 3) update()")
    root.update()
    print(f"[launcher] hwnd={root.winfo_id()} geometry={root.winfo_geometry()} "
          f"state={root.state()} viewable={bool(root.winfo_viewable())}")
    root.destroy()
    return True


def _probe_app(key: str, module_name: str) -> bool:
    """기능 창을 실제로 만들어 본다.

    `run()` 안에 창 조립과 `mainloop()` 가 같이 있어 따로 부를 수 없다.
    그래서 **`mainloop` 만 아무것도 하지 않게 바꿔** 조립까지만 시킨다.
    바꾼 것은 끝에서 되돌린다.
    """
    import importlib
    import tkinter as tk

    from gui.common import FormToggle

    print(f"[{key}] import {module_name}")
    module = importlib.import_module(module_name)

    made: list[tk.Misc] = []
    real_loop = tk.Misc.mainloop

    def _no_loop(self, n=0):
        made.append(self)

    # ★ 창 **객체**도 잡아 둔다. `run()` 안에서만 살아 있어서 밖에서 닿을 수
    #   없는데, 예약 시계처럼 위젯이 아닌 것을 확인하려면 객체가 필요하다.
    #   `FormToggle` 자체에는 `__init__` 이 없어서 부모를 패치해도 안 걸린다.
    #   그래서 **이 모듈이 정의한 창 클래스**를 찾아 그것을 감싼다.
    windows: list[object] = []
    owned = [obj for obj in vars(module).values()
             if isinstance(obj, type) and issubclass(obj, FormToggle)
             and obj.__module__ == module_name]
    patched: list[tuple[type, object]] = []
    for cls in owned:
        original = cls.__init__

        def _watch(self, *args, _real=original, **kwargs):
            windows.append(self)
            return _real(self, *args, **kwargs)

        cls.__init__ = _watch
        patched.append((cls, original))

    tk.Misc.mainloop = _no_loop
    try:
        print(f"[{key}] run() — mainloop 는 건너뛴다")
        module.run()
    finally:
        tk.Misc.mainloop = real_loop
        for cls, original in patched:
            cls.__init__ = original

    if not made:
        print(f"[{key}] ★ mainloop 까지 오지 못했다. 창 조립 중에 끝났다.")
        return False

    root = made[0].winfo_toplevel()
    root.update()
    print(f"[{key}] hwnd={root.winfo_id()} title={root.title()!r} "
          f"geometry={root.winfo_geometry()} state={root.state()} "
          f"viewable={bool(root.winfo_viewable())}")

    _report_size(key, root)
    _report_steps(key, root)
    _report_autorun(key, root, windows[-1] if windows else None)
    root.destroy()
    return True


def _report_autorun(key: str, root, window) -> None:
    """자동 실행 칸이 있으면 **예약 시계가 실제로 돌고 있는지** 본다.

    칸만 그려 놓고 시계를 안 돌리면 화면은 멀쩡한데 한 번도 돌지 않는다.
    무인 기능이라 사람이 며칠 뒤에 안다. 그래서 여기서 막는다.
    """
    frames = [w for w in _walk(root)
              if w.winfo_class() == "TLabelframe"
              and "자동 실행" in str(w.cget("text"))]
    if not frames:
        print(f"[{key}] 자동 실행 칸이 없다 (이 화면은 쓰지 않는다)")
        return

    runner = getattr(window, "autorunner", None)
    if runner is None:
        raise RuntimeError("자동 실행 칸은 있는데 예약 시계가 없다 — "
                           "start_autorun() 을 부르지 않았다")
    job = getattr(window, "_autorun_job", None)
    if job is None:
        raise RuntimeError("예약 시계가 반복 호출에 걸려 있지 않다 — "
                           "한 번 보고 멈춘다")

    # ★ 무인 경로가 살아 있는지. 이것이 없으면 첫 실패에서 모달이 떠 예약이 죽는다.
    if not hasattr(window, "_tell_problem"):
        raise RuntimeError("무인 경로(_tell_problem)가 없다")
    if window.unattended:
        raise RuntimeError("띄운 직후인데 무인 표시가 켜져 있다")

    print(f"[{key}] 자동 실행 칸 있음 — 시계 동작 / 상태줄: "
          f"{runner.status_line()!r} / 예약 {runner.plan.describe()!r}")


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _report_steps(key: str, root) -> None:
    """진행 파이프라인이 **실행 전에** 채워져 있는지 본다.

    구간 실행은 사용자가 시작 칸을 골라야 한다. 비어 있으면 고를 것이 없어서
    기능이 통째로 쓸 수 없다. 그런데 창은 멀쩡히 떠서 눈에 안 띈다.

    2026-09-16에 줄 표가 **가로 파이프라인**(`gui/pipeline.py`)으로 바뀌어서,
    Treeview 가 아니라 Canvas 에 무엇을 그렸는지 읽는다.
    """
    canvases = [c for c in _walk(root) if c.winfo_class() == "Canvas"]
    if not canvases:
        print(f"[{key}] 진행 파이프라인이 없다 (이 화면은 쓰지 않는다)")
        return

    canvas = canvases[0]
    # 단계 표식은 동그라미다 (2026-09-16에 칸 -> Stepper 로 바뀌었다).
    cards = sum(1 for s in canvas.find_all() if canvas.type(s) == "oval")
    texts = [canvas.itemcget(s, "text") for s in canvas.find_all()
             if canvas.type(s) == "text"]
    print(f"[{key}] 진행 칸 {cards}개 — {', '.join(texts[:6])} ...")
    if not cards:
        # ★ 그냥 찍고 넘어가면 안 된다. 이건 기능이 죽은 상태다.
        raise RuntimeError("진행 파이프라인이 비어 있다 — 실행 전에 계획이 그려져야 한다")

    # ★ 상세 표가 함께 있어야 한다 (2026-09-15). 칸 이름만 보여 주면
    #   "어떤 엑셀을 올렸나 / 어떤 상품이 보류됐나" 를 볼 곳이 없다.
    trees = [c for c in _walk(root) if c.winfo_class() == "Treeview"]
    if not trees:
        raise RuntimeError("상세 표가 없다")
    _feed_detail(key, trees[0])


def _feed_detail(key: str, detail) -> None:
    """상세 표에 **실제로 줄이 그려지는지** 본다.

    비어 있으면 가짜 표를 직접 그려 위젯 자체를 확인한다.
    (실행 전에는 칸에 담긴 상세가 없는 것이 정상이다.)
    """
    drawn = len(detail.get_children())
    if drawn:
        print(f"[{key}] 상세 표: 고른 단계에 {drawn}줄")
        return

    # 실행 전이라 담긴 상세가 없다. 위젯이 그릴 수 있는지만 확인한다.
    detail.configure(columns=("가", "나"))
    for name in ("가", "나"):
        detail.heading(name, text=name)
    detail.insert("", "end", values=("시험", "줄"))
    detail.update()
    ok = len(detail.get_children()) == 1
    print(f"[{key}] 상세 표: 실행 전이라 비어 있다. 그리기 시험 "
          + ("성공" if ok else "★ 실패"))


def _report_size(key: str, root) -> None:
    """창 크기가 **정말로** 모자란지 본다 (2026-09-15 추가).

    창 크기를 코드에 적어 두는 구조라, 안에 위젯을 더하면 아래쪽이 조용히
    잘린다. 그런데 `winfo_reqheight()` 를 그대로 쓰면 **줄어들어도 되는 영역**
    (로그처럼 `weight` 를 준 행)까지 세어서 멀쩡한 창도 "잘린다" 가 된다.

    그래서 행을 둘로 나눠 본다.

    | 구분 | 뜻 |
    | --- | --- |
    | 고정 영역 (`weight=0`) | 입력 칸·상태줄. **줄어들지 않는다.** 창보다 크면 진짜로 잘린다 |
    | 늘어나는 영역 (`weight>0`) | 로그·표. 창이 작으면 줄어들 뿐이다 |

    늘어나는 영역에도 **최소한 이만큼은 줘야** 쓸 수 있다고 보는 값을 함께 찍는다.
    """
    fixed = flexible = 0
    for child in root.winfo_children():
        info = child.grid_info()
        if not info:
            continue                      # grid 로 붙이지 않은 위젯
        row = int(info["row"])
        weight = int(root.grid_rowconfigure(row).get("weight", 0) or 0)
        need = child.winfo_reqheight()
        if weight:
            flexible += need
        else:
            fixed += need

    have_h = root.winfo_height()
    print(f"[{key}] 고정 {fixed}px + 늘어나는 영역 요구 {flexible}px "
          f"/ 창 {root.winfo_width()}x{have_h}", end="")
    if fixed > have_h:
        print(f"  ★ 잘린다 — 고정 영역이 창보다 {fixed - have_h}px 크다")
    elif fixed + MIN_FLEXIBLE_PX > have_h:
        print(f"  △ 빠듯하다 — 늘어나는 영역에 {have_h - fixed}px 밖에 안 남는다")
    else:
        print(f"  여유 {have_h - fixed}px")


# 로그·표 같은 영역에 최소한 이만큼은 남아야 쓸 수 있다고 본다.
# 근거: 표 머리글 + 세 줄, 또는 로그 여섯 줄 정도.
MIN_FLEXIBLE_PX = 220


def main() -> int:
    parser = argparse.ArgumentParser(description="GUI 창 생성 조사 (읽기 전용)")
    parser.add_argument("--only", choices=("launcher", *APPS),
                        help="이것만 확인한다")
    args = parser.parse_args()

    targets: list[tuple[str, str | None]] = [("launcher", None)]
    targets += [(key, module) for key, module in APPS.items()]
    if args.only:
        targets = [t for t in targets if t[0] == args.only]

    # 실행 창을 만들면 그것만으로 서버 확인이 나가고, 아직 안 묶인 빌드가 이 PC 에 묶인다 (09-28)
    from tools import probe_guard
    with probe_guard.offline():
        return _run(targets)


def _run(targets: list[tuple[str, str | None]]) -> int:
    failed = 0
    for key, module_name in targets:
        print(f"\n=== {key} ===")
        try:
            if module_name is None:
                _probe_launcher()
            else:
                _probe_app(key, module_name)
        except Exception:
            failed += 1
            print(f"[{key}] ★ 실패")
            traceback.print_exc()
    print(f"\n실패 {failed}건")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
