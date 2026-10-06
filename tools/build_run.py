r"""[개발 전용 — 빌드] 실행용 exe 를 만든다. 빌드 프로그램(`gui/build_app.py`)이 쓴다. 콘솔에서 바로 돌리면 설정 그대로·서버 등록 없이(지금 빌드 ID 그대로) 굽는다.

    .venv\Scripts\python.exe -m tools.build_run

순서: 설정 굽기(`tools/bake_settings`) → 지난 산출물 삭제 → PyInstaller `build_run.spec`
→ 구운 파일 삭제(비밀번호가 들어 있다). 산출물은 `dist/run/RPA_1.exe`.
대상 프로그램을 조작하지 않는다. 자세한 것은 `docs/BUILD.md`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PROJECT_ROOT  # noqa: E402
from tools import bake_settings  # noqa: E402

SPEC = PROJECT_ROOT / "build_run.spec"
DIST = PROJECT_ROOT / "dist" / "run"
EXE = DIST / "RPA_1.exe"            # `build_run.spec` 의 name
WORK = PROJECT_ROOT / "build" / "build_run"
BAKED_DIR = bake_settings.OUT_PATH.parent


class BuildError(RuntimeError):
    """빌드 실패. 메시지가 사람이 볼 이유다."""


def build(on_line=print) -> Path:
    """굽고 빌드한다. 줄마다 `on_line(글)` 로 알린다. 실패하면 `BuildError`."""
    python = sys.executable
    on_line("[1/3] 설정을 굽는다")
    try:
        baked = bake_settings.bake()
    except SystemExit as exc:          # bake 는 빠진 값을 SystemExit 로 알린다
        raise BuildError(str(exc)) from exc
    on_line(f"  구운 파일: {baked}")
    try:
        _ensure_pyinstaller(python, on_line)
        # 지난 산출물을 지운다. 남겨 두면 옛 코드·exe 옆 설정(계정)이 섞일 수 있다.
        for folder in (WORK, DIST):
            if folder.exists():
                shutil.rmtree(folder)
                on_line(f"  지난 산출물 삭제: {folder}")
        on_line("[2/3] PyInstaller")
        _run([python, "-m", "PyInstaller", str(SPEC), "--noconfirm",
              "--distpath", str(DIST)], on_line)
    finally:
        on_line("[3/3] 구운 설정 삭제 (비밀번호가 들어 있다)")
        shutil.rmtree(BAKED_DIR, ignore_errors=True)
    if not EXE.is_file():
        raise BuildError(f"빌드가 끝났는데 exe 가 없다: {EXE}")
    on_line(f"완료: {EXE}")
    on_line("이 exe 는 설정이 들어 있어 혼자 돕니다. 실행하면 exe 옆에 logs\\ 와 "
            "config\\settings.local.json 이 생기니, 다른 사람에게 줄 때는 exe 만 주세요.")
    return EXE


def _ensure_pyinstaller(python: str, on_line) -> None:
    try:
        subprocess.run([python, "-m", "PyInstaller", "--version"], check=True,
                       capture_output=True, creationflags=_NO_WINDOW)
    except (subprocess.CalledProcessError, OSError):
        on_line("  PyInstaller 가 없다. 설치한다")
        _run([python, "-m", "pip", "install", "pyinstaller"], on_line)


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # 빌드 프로그램에서 콘솔이 튀지 않게


def _run(cmd: list[str], on_line) -> None:
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip())
    if proc.wait() != 0:
        raise BuildError(f"명령이 실패했다 (exit {proc.returncode}): "
                         f"{' '.join(cmd[1:3])} — 위 메시지를 볼 것")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
    try:
        build(print)
    except BuildError as exc:
        print(f"[실패] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
