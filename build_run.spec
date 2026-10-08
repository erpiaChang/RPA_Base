# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 빌드 정의 — 실행용(설정을 구워 넣는다).

    build_tool.bat            빌드 프로그램 — 설정을 화면에서 고쳐서 빌드 (gui/build_app.py)
    .venv\Scripts\python.exe -m tools.build_run   콘솔 — 설정 그대로, 서버 등록 없이
    둘 다 tools/build_run.py 가 아래를 순서대로 한다:
    .venv\Scripts\python.exe -m tools.bake_settings
    .venv\Scripts\python.exe -m PyInstaller build_run.spec --noconfirm --distpath "dist\run"

산출물: dist\run\RPA_1.exe

## 유일한 빌드다 (10-06 — 기능별 빌드 collect/erpia/full 을 지웠다)

흐름은 `orchestrator/full_flow.py`, 창은 `gui/run_app.py`. **설정을 번들 안에 구워 넣는다** — 사용자는 exe 만 받아 실행하면 된다.
기능만 따로 쓰려면 기능 고정(`run_modules_locked`)으로 굽는다. 메일이 없으면 Playwright 만 빠지고 다른 기능의 코드는 들어간다.

구운 파일은 `build/baked/settings.baked.json` 이고 `tools/bake_settings.py` 가 만든다.
번들 안에서는 `config/settings.baked.json` 이 되며, `config/settings.py` 가
**`DEFAULTS` 다음, exe 옆 `settings.local.json` 보다 먼저** 깔아 준다.

★ 구운 비밀번호는 `baked:` 로 감싸지만 키가 프로그램 안에 있어 **뜯으면 풀린다**
  (`utils/secret.py`). 이 exe 는 **믿을 수 있는 사용자에게만** 준다.
★ `run_modules_locked` 가 True 로 구워지면 실행 창의 [실행할 기능] 이 잠긴다 (09-22).
★ 개발 전용 모듈(`DEV_ONLY`)이 번들에 섞이면 빌드 끝 검사가 **빌드를 중단**한다.
"""
import json
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, SPECPATH)
from collect import pw_driver  # noqa: E402 — node.exe 리소스 형식은 한 곳(collect/pw_driver.py)에만 둔다

HIDDEN = collect_submodules("pywinauto") + collect_submodules("comtypes")
# 설정 항목 파일은 config/settings.py 가 **동적으로** import 한다.
# 정적 분석으로는 잡히지 않아 이름으로 넣어 준다. 빠지면 설정이 통째로 빈다.
HIDDEN += ["config.fields_collect", "config.fields_erpia"]

# 구워 넣을 설정. **없으면 빌드를 중단한다.** 값이 빠진 exe 를 만들면
# 사용자는 화면에 없는 항목 때문에 실행이 막히고, 스스로 고칠 수도 없다.
BAKED_SOURCE = os.path.join("build", "baked", "settings.baked.json")
SEED_SOURCE = os.path.join("build", "baked", "baked.key")      # utils/secret.SEED_FILE — 원본은 .env (10-02)
VERSION_SOURCE = os.path.join("build", "baked", "version.txt")  # config/settings.VERSION_FILE
for _need in (BAKED_SOURCE, SEED_SOURCE):
    if not os.path.isfile(_need):
        raise SystemExit(
            f"[빌드 중단] 구워 넣을 파일이 없다: {_need}\n"
            "  먼저 만들 것:  .venv\\Scripts\\python.exe -m tools.bake_settings"
        )

# 개발·조사 전용. 어느 배포본에도 들어가면 안 된다.
DEV_ONLY = [
    "tools",
    "gui.build_app",             # 빌드 프로그램
    "llm",                       # LLM 전용 PC 의 워커 (09-28) — RPA 에 들어가면 안 된다
]

# ★ 업체 전용 (10-08, `docs/CUSTOMERS.md` 4절) — 구운 `customer` 의 업체 패키지 **하나만** 넣는다.
#   `config/settings.py` 가 이름으로(`importlib`) 불러 정적 분석에 안 잡히므로 이름으로 넣고,
#   다른 업체 패키지는 금지 목록에 더한다 — 섞이면 아래 누출 검사가 빌드를 멈춘다. 업체가 없으면 업체 패키지 전부 금지.
from config import customer as _customer  # noqa: E402
CUSTOMER_ID = (json.loads(open(BAKED_SOURCE, encoding="utf-8").read()).get("customer") or "").strip()
if CUSTOMER_ID:
    if CUSTOMER_ID not in _customer.available():
        raise SystemExit(f"[빌드 중단] 구운 업체 '{CUSTOMER_ID}' 의 패키지가 없다: customers/{CUSTOMER_ID}/ (git 밖)")
    HIDDEN += ["customers"] + collect_submodules(f"customers.{CUSTOMER_ID}")
    CUSTOMER_BLOCKED = [f"customers.{other}" for other in _customer.available() if other != CUSTOMER_ID]
else:
    CUSTOMER_BLOCKED = ["customers"]

# 설치돼 있으면 딸려 들어갈 수 있는 무거운 패키지. 이 프로그램은 쓰지 않는다.
UNUSED_LIBS = [
    "psutil",
    "numpy", "scipy", "pandas", "matplotlib",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "pytest", "unittest", "pydoc",
    "setuptools", "pip", "PyInstaller",
    "PIL._avif",        # 7.9MB. 화면 찍기(PNG)만 쓴다 — 없으면 Pillow 가 AVIF 만 못 쓴다 (09-29)
]
# ★ 메일 기능이 없는 기능 고정 빌드는 Playwright 를 통째로 뺀다 (09-29, 106MB)
NEED_MAIL = pw_driver.needed(json.loads(open(BAKED_SOURCE, encoding="utf-8").read()))
if not NEED_MAIL:
    UNUSED_LIBS.append("playwright")

a = Analysis(
    ["main_run.py"],
    pathex=[],
    binaries=[],
    # ★ 이 빌드만 설정을 번들에 넣는다. 다른 빌드는 exe 옆에서 읽고 쓴다.
    datas=[(BAKED_SOURCE, "config"), (SEED_SOURCE, "config")]
          # 판 번호 (10-07, `tools/build_run.py` 가 만든다). 직접 PyInstaller 를 돌려 없으면 판이 '?' 로 보인다
          + ([(VERSION_SOURCE, "config")] if os.path.isfile(VERSION_SOURCE) else []),
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # ★ **`excludes` 에 DEV_ONLY 를 넣지 않는다.**
    #   넣으면 PyInstaller 가 그 이름을 `ExcludedModule` 로 만들어 `a.pure` 에
    #   아예 나타나지 않는다. 그러면 아래 누출 검사가 **항상 통과**한다.
    excludes=UNUSED_LIBS,
    noarchive=False,
    optimize=0,
)

# ---------------------------------------------------------------- 확인
_OURS = ("main_run", "gui", "automation", "collect", "orchestrator", "config", "utils", "tools", "llm", "customers")
_bundled = sorted(
    name for name, _path, _kind in a.pure
    if name.split(".")[0] in _OURS
)
print("=" * 60)
print(f" [실행용] 번들에 포함된 프로젝트 모듈 {len(_bundled)}개")
for _name in _bundled:
    print(f"   {_name}")

# ★ 이 검사가 유일한 안전장치다. `excludes` 로 숨기지 않았으므로,
#   누군가 금지 모듈을 import 하면 여기서 **빌드가 멈춘다.**
_blocked = DEV_ONLY + CUSTOMER_BLOCKED
_leaked = [n for n in _bundled
           if any(n == b or n.startswith(b + ".") for b in _blocked)]
if _leaked:
    raise SystemExit(
        f"[빌드 중단] 이 배포본에 들어가면 안 되는 모듈이 섞였다: {_leaked} "
        "— 누군가 이 모듈을 import 하고 있다. 의존을 끊을 것."
    )
print(f" 금지 모듈 {len(_blocked)}종 누출 없음 (검사 대상: {', '.join(_blocked)})")
if CUSTOMER_ID:
    if f"customers.{CUSTOMER_ID}" not in _bundled:
        raise SystemExit(f"[빌드 중단] 업체 패키지 customers.{CUSTOMER_ID} 가 번들에 들어가지 않았다.")
    print(f" 업체 전용: {CUSTOMER_ID}")

# 구운 설정이 실제로 들어갔는지 확인한다. 빠지면 값이 통째로 빈 exe 가 된다.
for _name in ("config/settings.baked.json", "config/baked.key"):
    _baked = [dest for dest, _src, _kind in a.datas if dest.replace("\\", "/").endswith(_name)]
    if not _baked:
        raise SystemExit(f"[빌드 중단] {_name} 이 번들에 들어가지 않았다.")
    print(f" 구워 넣음: {_baked[0]}")

# ★ node.exe(92MB)는 켤 때마다 풀리는 칸에서 빼 **exe 리소스**로 넣는다 (09-29, `collect/pw_driver.py`).
#   onefile 은 켤 때마다 번들을 전부 푸는데 153MB 중 92MB 가 이것이었다. 메일 기능을 처음 쓸 때 한 번 꺼낸다.
RESOURCES = []
if NEED_MAIL:
    # PyInstaller 6 은 PE 파일을 datas 에 넣어도 binaries 로 다시 나눈다 — 둘 다 본다
    _node = [entry for entry in a.binaries + a.datas
             if entry[0].replace("\\", "/").lower() == "playwright/driver/node.exe"]
    if len(_node) != 1:
        raise SystemExit(f"[빌드 중단] 번들에서 playwright/driver/node.exe 를 찾지 못했다: {_node}")
    a.binaries = [entry for entry in a.binaries if entry not in _node]
    a.datas = [entry for entry in a.datas if entry not in _node]
    _blob = os.path.join("build", "baked", "pwnode.bin")    # 빌드가 끝나면 build/baked 째 지운다
    with open(_blob, "wb") as _out:
        _out.write(pw_driver.pack(_node[0][1]))
    RESOURCES.append(f"{_blob},{pw_driver.RES_TYPE},{pw_driver.RES_NAME}")
    print(f" node.exe → exe 리소스 ({os.path.getsize(_blob) / 1e6:.1f}MB 압축)")
else:
    print(" 메일 기능이 없는 빌드 — Playwright 를 뺐다")
print("=" * 60)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RPA_1",
    resources=RESOURCES,
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
