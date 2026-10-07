r"""[개발 전용 — 빌드] 실행용 exe 를 만든다. 빌드 프로그램(`gui/build_app.py`)이 쓴다. 콘솔에서 바로 돌리면 설정 그대로·서버 등록 없이(지금 빌드 ID 그대로) 굽는다.

    .venv\Scripts\python.exe -m tools.build_run

순서: 설정 굽기(`tools/bake_settings`) + 판 번호 → 지난 산출물 삭제 → PyInstaller `build_run.spec`
→ 구운 파일 삭제(비밀번호가 들어 있다) → 자가 점검(임시 사본을 `--selfcheck` 로 켜 본다, 10-07). 산출물은 `dist/run/RPA_1.exe`.
대상 프로그램을 조작하지 않는다. 자세한 것은 `docs/BUILD.md`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PROJECT_ROOT, VERSION_FILE  # noqa: E402
from tools import bake_settings  # noqa: E402

SPEC = PROJECT_ROOT / "build_run.spec"
DIST = PROJECT_ROOT / "dist" / "run"
EXE = DIST / "RPA_1.exe"            # `build_run.spec` 의 name
WORK = PROJECT_ROOT / "build" / "build_run"
BAKED_DIR = bake_settings.OUT_PATH.parent
SELFCHECK_DIR = PROJECT_ROOT / "build" / "selfcheck"
SELFCHECK_TIMEOUT = 180             # onefile 을 풀고 모듈을 다 불러오는 상한(초)


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
    version = make_version()
    (BAKED_DIR / VERSION_FILE).write_text(version, encoding="utf-8")
    on_line(f"  판: {version}")
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
    selfcheck(EXE, on_line)
    on_line(f"완료: {EXE}")
    on_line("이 exe 는 설정이 들어 있어 혼자 돕니다. 실행하면 exe 옆에 logs\\ 와 "
            "config\\settings.local.json 이 생기니, 다른 사람에게 줄 때는 exe 만 주세요.")
    return EXE


def make_version(run=subprocess.run, today: date | None = None) -> str:
    """판 번호 '날짜-커밋 해시' — 커밋 안 된 변경(추적 파일)이 있으면 끝에 + (10-07). git 이 없으면 날짜만."""
    stamp = (today or date.today()).strftime("%Y.%m.%d")
    try:
        head = run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True,
                   check=True, creationflags=_NO_WINDOW).stdout.strip()
        dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=PROJECT_ROOT, capture_output=True,
                    text=True, check=True, creationflags=_NO_WINDOW).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return stamp
    return f"{stamp}-{head}{'+' if dirty else ''}"


def selfcheck(exe: Path, on_line, run=subprocess.run) -> dict:
    """만든 exe 의 **임시 사본**을 `--selfcheck` 로 켜 본다 (10-07, `utils/selfcheck.py`) — exe 옆에 logs·config 가 생기지 않게.
    창·한 벌 뮤텍스·서버 확인 앞에서 끝나므로 빌드를 이 PC 에 묶지 않는다. 실패하면 `BuildError`."""
    on_line("[점검] 만든 exe 를 임시 사본으로 켜 본다 (창·서버 확인 없이)")
    shutil.rmtree(SELFCHECK_DIR, ignore_errors=True)
    SELFCHECK_DIR.mkdir(parents=True)
    copy, result_file = SELFCHECK_DIR / exe.name, SELFCHECK_DIR / "selfcheck.json"
    shutil.copy2(exe, copy)
    try:
        try:
            done = run([str(copy), "--selfcheck", str(result_file)], cwd=SELFCHECK_DIR,
                       timeout=SELFCHECK_TIMEOUT, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise BuildError(f"자가 점검이 {SELFCHECK_TIMEOUT}초 안에 끝나지 않았다 — 이 exe 를 쓰지 말 것") from exc
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BuildError(f"자가 점검 결과가 없다 — exe 가 켜지자마자 끝났다 (종료 코드 {done.returncode}). "
                             "이 exe 를 쓰지 말 것") from exc
        for name, step in result.get("steps", {}).items():
            on_line(f"  {'통과' if step.get('ok') else '실패'} {name}: {step.get('detail')}")
        if not result.get("ok"):
            raise BuildError("자가 점검 실패 — 이 exe 를 쓰지 말 것. 위 '실패' 줄을 볼 것")
        on_line(f"  자가 점검 통과 — 판 {result.get('version')}")
        return result
    finally:
        shutil.rmtree(SELFCHECK_DIR, ignore_errors=True)


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
