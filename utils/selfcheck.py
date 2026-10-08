r"""빌드 직후 자가 점검 — `RPA_1.exe --selfcheck <결과 파일>` (10-07, 부르는 쪽 `tools/build_run.py`).

"빌드는 됐는데 켜자마자 죽는 exe" 를 사용자보다 먼저 잡는다 (모듈 누락·구운 값 못 풂·Tcl·UIA).
★ 한 벌 뮤텍스·자동 켜기 질문·서버 확인(빌드를 그 PC 에 묶는다)·창보다 **먼저** 끝낸다 — `main_run.py` 맨 앞에서 갈라진다.
  모듈은 불러오기만 한다 (서버 확인은 `RunWindow.__init__`). 로그도 열지 않는다.
결과는 JSON 한 파일 — 콘솔 없는 exe 라 stdout 이 없다. 값(비밀번호·주소)은 담지 않는다.
"""
from __future__ import annotations

import importlib
import json
import pkgutil
import sys
import traceback
from pathlib import Path

PACKAGES = ("config", "utils", "automation", "collect", "orchestrator", "gui")   # 번들에 드는 우리 패키지
SKIP = ("gui.build_app",)           # 개발 전용 (build_run.spec DEV_ONLY). tools·llm 은 위 묶음에 없다


def _settings() -> str:
    from config import settings
    from orchestrator import modules
    from utils import secret

    if getattr(sys, "frozen", False) and not settings.BAKED_SETTINGS_PATH.is_file():
        raise RuntimeError("번들에 구운 설정이 없다")
    values = settings.SETTINGS
    ids = set(values.run_modules or []) if values.run_modules_locked else set(modules.IDS)
    required = modules.baked_required(ids)
    blank = [key for key in required if getattr(values, key, None) in (None, "", [], {})]
    if blank:
        raise RuntimeError("구운 값이 비었다(빠졌거나 풀지 못함): " + ", ".join(blank))
    secret.seed()
    # 구운 비밀번호(`baked:`)를 실제로 풀어 본다 — 씨앗이 안 맞으면 켤 때 그 값이 조용히 빈 값이 된다
    wrapped = 0
    if settings.BAKED_SETTINGS_PATH.is_file():
        for key, value in settings.read_json_file(settings.BAKED_SETTINGS_PATH).items():
            for item in (value.values() if isinstance(value, dict) else [value]):
                if isinstance(item, str) and item.startswith(secret.BAKED_PREFIX):
                    try:
                        secret.unwrap(item)
                    except Exception as exc:        # noqa: BLE001 — 어느 값인지 남긴다 (값은 남기지 않는다)
                        raise RuntimeError(f"구운 비밀번호를 풀지 못했다: {key} ({type(exc).__name__})") from exc
                    wrapped += 1
    return f"필수 {len(required)}개 채워짐, 구운 비밀번호 {wrapped}개 풀림"


def _modules() -> str:
    from config.settings import CUSTOMER

    names: list[str] = []
    # 업체 빌드면 그 업체 패키지도 (10-08) — 번들에 빠졌으면 여기서 잡힌다
    for package in PACKAGES + ((f"customers.{CUSTOMER.id}",) if CUSTOMER else ()):
        root = importlib.import_module(package)
        names += [info.name for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}.")
                  if info.name not in SKIP]
    bad = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:        # noqa: BLE001 — 어느 모듈이 왜 안 불러지는지 남긴다
            bad.append(f"{name}: {type(exc).__name__}: {exc}"[:160])
    if bad:
        raise RuntimeError("; ".join(bad))
    return f"{len(names)}개"


def _tcl() -> str:
    import tkinter

    return "tcl " + tkinter.Tcl().eval("info patchlevel")       # 창 없이 Tcl 만


def _uia() -> str:
    from pywinauto.uia_defines import IUIA

    IUIA()                              # UIAutomation 클라이언트를 만든다. 창은 건드리지 않는다
    return "UIA 클라이언트 생성"


def collect() -> dict:
    result: dict = {"frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0], "steps": {}}
    for name, step in (("settings", _settings), ("modules", _modules), ("tcl", _tcl), ("uia", _uia)):
        try:
            result["steps"][name] = {"ok": True, "detail": step()}
        except (Exception, SystemExit) as exc:      # noqa: BLE001 — 실패도 결과로 남긴다
            tail = traceback.format_exc().strip().splitlines()[-1]
            result["steps"][name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc} ({tail})"[:400]}
    try:
        from config.settings import APP_VERSION
        result["version"] = APP_VERSION
    except Exception as exc:            # noqa: BLE001 — 설정이 안 불러지면 위 settings 단계가 이미 실패로 남겼다
        result["version"] = f"(읽지 못함: {type(exc).__name__})"
    result["ok"] = all(step["ok"] for step in result["steps"].values())
    return result


def run(argv: list[str]) -> int:
    """`argv` = `--selfcheck` 뒤의 인자. 결과 파일을 쓰고 끝낸다. 0 통과 / 1 실패 / 2 파일 인자 없음 / 3 못 씀."""
    if not argv:
        return 2
    result = collect()
    try:
        Path(argv[0]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return 3
    return 0 if result["ok"] else 1
