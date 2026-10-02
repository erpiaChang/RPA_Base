# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 빌드 정의 — ERPia 전용.

    build_erpia.bat            (또는)
    .venv\Scripts\python.exe -m PyInstaller build_erpia.spec --noconfirm

산출물: dist\ERPia_RPA.exe

## 배포본을 기능별로 나누는 이유 (사용자 확정 2026-09-08)

배포본 안에 **다른 기능의 코드도, 흔적도 남기지 않는다.** 실행 파일은 뜯어볼 수
있으므로, 넣지 않은 것만이 안전하다. 그래서

1. 진입점을 기능별로 나눴다 (`main_*.py`). PyInstaller 는 진입점에서 import 로
   도달하는 것만 넣는다
2. 그래도 기대지 않고, 다른 기능의 모듈 이름을 `FORBIDDEN` 에 못 박는다
3. 빌드 끝에 번들 목록을 검사해서 **하나라도 섞이면 빌드를 중단**한다

화면 문구도 같은 이유로 나눠 뒀다. 공용 파일(`gui/common.py`, `orchestrator/common.py`)에는
어떤 기능의 이름도 적지 않는다.
"""
from PyInstaller.utils.hooks import collect_submodules

HIDDEN = collect_submodules("pywinauto") + collect_submodules("comtypes")
# 설정 항목 파일은 config/settings.py 가 **동적으로** import 한다.
# 정적 분석으로는 잡히지 않아 이름으로 넣어 준다. 빠지면 설정이 통째로 빈다.
HIDDEN += ["config.fields_erpia"]

# 개발·조사 전용. 어느 배포본에도 들어가면 안 된다.
DEV_ONLY = [
    "tools",
    "gui.launcher",              # 개발용 모드 선택 창
    "gui.build_app",             # 빌드 프로그램
    "llm",                       # LLM 전용 PC 의 워커 (09-28) — RPA 에 들어가면 안 된다
]

# **이 배포본에 들어가면 안 되는 다른 기능.**
FORBIDDEN = [
    "orchestrator.steps_collect",        # 수집 단계 표
    "orchestrator.modules",              # 기능 선택 (수집+ERPia 단계 표를 둘 다 쓴다)
    "collect",
    "playwright",
    "orchestrator.collect_flow",
    "orchestrator.full_flow",
    "gui.collect_app",
    "gui.full_app",
]

# 설치돼 있으면 딸려 들어갈 수 있는 무거운 패키지. 이 프로그램은 쓰지 않는다.
UNUSED_LIBS = [
    "psutil",
    "numpy", "scipy", "pandas", "matplotlib",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest", "unittest", "pydoc",
    "setuptools", "pip", "PyInstaller",
]

a = Analysis(
    ["main_erpia.py"],
    pathex=[],
    binaries=[],
    # 설정 파일은 번들에 넣지 않는다. exe 옆에서 읽고 쓴다.
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # ★ **`excludes` 에 DEV_ONLY / FORBIDDEN 을 넣지 않는다.**
    #   넣으면 PyInstaller 가 그 이름을 `ExcludedModule` 로 만들어 `a.pure` 에
    #   아예 나타나지 않는다. 그러면 아래 누출 검사가 **항상 통과**해서
    #   "이중 안전장치" 가 이름만 남는다 (2026-09-10 감사).
    #   지금은 실제로 번들에 들어갔는지 보고, 들어갔으면 **빌드를 중단한다.**
    #   (`UNUSED_LIBS` 는 우리 코드가 아니라 딸려 오는 패키지라 계속 제외한다)
    excludes=UNUSED_LIBS,
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------- 확인
_OURS = ("main", "main_collect", "main_erpia", "main_full",
         "gui", "automation", "collect", "orchestrator", "config", "utils", "tools", "llm")
_bundled = sorted(
    name for name, _path, _kind in a.pure
    if name.split(".")[0] in _OURS
)
print("=" * 60)
print(f" [ERPia 전용] 번들에 포함된 프로젝트 모듈 {len(_bundled)}개")
for _name in _bundled:
    print(f"   {_name}")

# ★ 이 검사가 유일한 안전장치다. `excludes` 로 숨기지 않았으므로,
#   누군가 금지 모듈을 import 하면 여기서 **빌드가 멈춘다.**
_blocked = DEV_ONLY + FORBIDDEN
_leaked = [n for n in _bundled
           if any(n == b or n.startswith(b + ".") for b in _blocked)]
if _leaked:
    raise SystemExit(
        f"[빌드 중단] 이 배포본에 들어가면 안 되는 모듈이 섞였다: {_leaked} "
        "— 누군가 이 모듈을 import 하고 있다. 의존을 끊을 것."
    )
print(f" 금지 모듈 {len(_blocked)}종 누출 없음 (검사 대상: {', '.join(_blocked)})")
print("=" * 60)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ERPia_RPA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
