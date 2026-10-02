r"""[조사 전용 / 읽기 전용] 현재 떠 있는 최상위 Window를 전부 나열한다.

대상 프로그램의 실제 Window title, class_name, EXE 경로를 추측하지 않고
여기서 확인한다. 클릭/입력/포커스 이동을 하지 않는다.

    .venv\Scripts\python.exe -m tools.survey_windows
    .venv\Scripts\python.exe -m tools.survey_windows --all
    .venv\Scripts\python.exe -m tools.survey_windows --backend win32
    .venv\Scripts\python.exe -m tools.survey_windows --filter ERP
"""
from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes
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

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_image_path(pid: int) -> str:
    """PID의 실행 파일 전체 경로. 권한이 없으면 빈 문자열."""
    if not pid:
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def describe(win, backend: str) -> dict:
    """Window 하나의 식별 정보를 읽는다. 상태 변경 없이 읽기만 한다."""
    info: dict = {
        "title": "",
        "class_name": "",
        "control_type": "",
        "pid": 0,
        "handle": 0,
        "exe": "",
        "visible": None,
        "enabled": None,
        "rect": "",
        "error": "",
    }
    try:
        info["title"] = win.window_text()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    for key, getter in (
        ("class_name", lambda: win.class_name()),
        ("pid", lambda: win.process_id()),
        ("handle", lambda: win.handle),
        ("visible", lambda: win.is_visible()),
        ("enabled", lambda: win.is_enabled()),
        ("rect", lambda: str(win.rectangle())),
    ):
        try:
            info[key] = getter()
        except Exception:
            continue  # 접근 불가 항목은 비워둔다. 조사 자체는 계속한다.
    if backend == "uia":
        try:
            info["control_type"] = win.element_info.control_type
        except Exception:
            info["control_type"] = ""  # UIA 속성 미지원 창. 조사는 계속한다.
    info["exe"] = process_image_path(info["pid"] or 0)
    return info


def collect(backend: str, show_all: bool, keyword: str | None) -> list[dict]:
    windows = Desktop(backend=backend).windows()
    rows = [describe(w, backend) for w in windows]

    if not show_all:
        rows = [r for r in rows if r["title"].strip() and r["visible"] is not False]
    if keyword:
        low = keyword.lower()
        rows = [
            r for r in rows
            if low in r["title"].lower()
            or low in r["class_name"].lower()
            or low in Path(r["exe"]).name.lower()
        ]
    rows.sort(key=lambda r: (Path(r["exe"]).name.lower(), r["title"].lower()))
    return rows


def render(rows: list[dict], backend: str, show_all: bool) -> str:
    lines = [
        "# 최상위 Window 목록 (읽기 전용 조사)",
        f"조사 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"backend  : {backend}",
        f"필터     : {'없음 (숨김 창 포함)' if show_all else 'title 있고 보이는 창만'}",
        f"창 개수  : {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("조건에 맞는 Window가 없다. --all 로 다시 확인할 것.")
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        lines.append(f"[{i}] {r['title'] or '(제목 없음)'}")
        lines.append(f"    class_name  : {r['class_name']}")
        if r["control_type"]:
            lines.append(f"    control_type: {r['control_type']}")
        lines.append(f"    pid / handle: {r['pid']} / {r['handle']}")
        lines.append(f"    exe         : {r['exe'] or '(권한 없음 또는 확인 불가)'}")
        lines.append(f"    visible/enabled: {r['visible']} / {r['enabled']}")
        lines.append(f"    rect        : {r['rect']}")
        if r["error"]:
            lines.append(f"    error       : {r['error']}")
        lines.append("")

    lines.append("다음 단계: 대상 창을 고른 뒤 컨트롤 트리를 덤프한다.")
    lines.append(r'    .venv\Scripts\python.exe -m tools.dump_controls --pid <PID> --name <화면명>')
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="최상위 Window 조사 (읽기 전용)")
    parser.add_argument("--backend", default="uia", choices=["uia", "win32"])
    parser.add_argument("--all", action="store_true", help="제목 없는/숨겨진 창도 포함")
    parser.add_argument("--filter", default=None, help="title/class_name/exe 부분 일치")
    parser.add_argument("--save", action="store_true", help="docs/_windows_<시각>.txt 로 저장")
    args = parser.parse_args()

    rows = collect(args.backend, args.all, args.filter)
    report = render(rows, args.backend, args.all)
    print(report)

    if args.save:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        out = DOCS_DIR / f"_windows_{datetime.now():%Y%m%d_%H%M%S}.txt"
        out.write_text(report, encoding="utf-8")
        print(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
