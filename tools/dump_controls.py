r"""[조사 전용 / 읽기 전용] 지정한 Window의 Control 트리를 파일로 덤프한다.

auto_id / control_type / title 을 추측하지 않고 여기서 확인한다.
클릭/입력/포커스 이동을 하지 않는다.

    .venv\Scripts\python.exe -m tools.dump_controls --pid 1234 --name 로그인
    .venv\Scripts\python.exe -m tools.dump_controls --title-re "ERP.*" --name 메인
    .venv\Scripts\python.exe -m tools.dump_controls --pid 1234 --name 로그인 --backend win32

덤프 파일은 크다. 전체를 읽지 말고 필요한 요소만 grep 해서 확인한다.
"""
from __future__ import annotations

import argparse
import locale
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 콘솔 코드페이지가 cp949여도 한글이 깨지지 않게 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass  # 리다이렉트된 스트림 등 재설정 불가. 조사 자체는 계속한다.

from pywinauto import Desktop  # noqa: E402

from config.settings import DOCS_DIR  # noqa: E402


def find_window(backend: str, pid: int | None, title_re: str | None, class_name: str | None):
    criteria: dict = {}
    if pid:
        criteria["process"] = pid
    if title_re:
        criteria["title_re"] = title_re
    if class_name:
        criteria["class_name"] = class_name
    if not criteria:
        raise SystemExit("--pid / --title-re / --class-name 중 하나는 지정해야 한다.")

    desktop = Desktop(backend=backend)
    matches = desktop.windows(**criteria)
    if not matches:
        raise SystemExit(
            f"조건에 맞는 Window가 없다: {criteria} (backend={backend})\n"
            "tools.survey_windows 로 실제 title/pid 를 먼저 확인할 것."
        )
    if len(matches) > 1:
        print(f"[경고] 조건에 {len(matches)}개가 맞는다. 첫 번째를 덤프한다:", file=sys.stderr)
        for w in matches:
            print(f"  - {w.window_text()!r} class={w.class_name()!r} handle={w.handle}", file=sys.stderr)
    # windows()는 Wrapper를 돌려주고 print_control_identifiers가 없다.
    # handle로 WindowSpecification을 다시 잡는다 (handle은 고유하다).
    wrapper = matches[0]
    return wrapper, desktop.window(handle=wrapper.handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Control 트리 덤프 (읽기 전용)")
    parser.add_argument("--name", required=True, help="화면 이름 (파일명에 사용). 예: 로그인")
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--title-re", default=None)
    parser.add_argument("--class-name", default=None)
    parser.add_argument("--backend", default="uia", choices=["uia", "win32"])
    parser.add_argument(
        "--depth", type=int, default=None,
        help="트리 깊이 제한. 기본은 제한 없음(DataGrid 내부까지). 너무 크면 지정한다.",
    )
    args = parser.parse_args()

    win, spec = find_window(args.backend, args.pid, args.title_re, args.class_name)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_DIR / f"_dump_{args.name}_{args.backend}_{datetime.now():%Y%m%d_%H%M%S}.txt"

    print(f"대상 Window : {win.window_text()!r}")
    print(f"class_name  : {win.class_name()!r}")
    print(f"handle/pid  : {win.handle} / {win.process_id()}")
    print(f"backend     : {args.backend}")
    print("덤프 중...")

    spec.print_control_identifiers(depth=args.depth, filename=str(out))

    # pywinauto는 locale 인코딩(cp949 등)으로 쓴다. 이후 grep/읽기를 위해 UTF-8로 맞춘다.
    raw = out.read_bytes()
    out.write_text(raw.decode(locale.getpreferredencoding(), errors="replace"), encoding="utf-8")

    size = out.stat().st_size
    lines = out.read_text(encoding="utf-8", errors="replace").count("\n")
    print(f"저장: {out}  ({size:,} bytes / {lines:,} lines)")
    if args.backend == "uia" and lines < 20:
        print(
            "[주의] UIA 트리가 매우 얕다. 구형 프레임워크일 수 있다.\n"
            "       --backend win32 로도 덤프해서 비교할 것.",
            file=sys.stderr,
        )
    print("\n다음: 덤프에서 조작 대상 요소만 grep 하고, 결과를 docs/UI_SURVEY.md 에 기록한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
