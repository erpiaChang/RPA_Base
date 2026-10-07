r"""[개발 전용] 지금 설정을 **빌드에 구워 넣을 파일**로 굽는다. 읽기 전용 도구다.

    .venv\Scripts\python.exe -m tools.bake_settings

`config/settings.local.json` 을 읽어 `build/baked/settings.baked.json` 을 만든다.
`build_run.spec` 이 그 파일을 번들 안 `config/settings.baked.json` 으로 넣고,
`config/settings.py` 가 **`DEFAULTS` 다음, 로컬 설정보다 먼저** 깔아 준다.

그래서 사용자는 exe 만 받아 실행하면 값이 이미 채워져 있다.

## 왜 소스 폴더에 쓰지 않는가

구운 파일에는 비밀번호가 **감싸여**(`baked:`) 들어간다. 다만 키가 프로그램
안에 있어 뜯으면 읽을 수 있다. 소스 폴더(`config/`)에 두면
다른 빌드에도 딸려 들어가고 실수로 남기 쉽다. 그래서 빌드 산출물 폴더인
`build/baked/` 에만 만들고, `tools/build_run.py` 가 빌드 끝에 지운다.

## 굽지 않는 것

| 키 | 이유 |
| --- | --- |
| `collect_site_code` / `excel_path` | 개발 도구 전용이다. 화면에서 쓰지 않는다 |
| `target_exe` / `target_work_dir` / `download_base_dir` | 개발 PC 의 경로다. 실행 창이 그 PC 에서 찾아 채운다 (09-23) |

`server_build_id` 는 **굽는다** (09-28). 빌드할 때 서버가 발급한 값이고, 그 빌드가 처음 보고할 때
서버가 그 PC 에 묶는다. 복사해 가도 다른 PC 에서는 401 이라 실행이 잠긴다.

대상 프로그램을 조작하지 않는다. 파일만 읽고 쓴다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config.settings import (DEFAULTS, LOCAL_SETTINGS_PATH,  # noqa: E402
                             PROJECT_ROOT, SECRET_KEYS, SECRET_MAP_KEYS, WORKER_PREFIX, read_json_file)

OUT_PATH = PROJECT_ROOT / "build" / "baked" / "settings.baked.json"

# 개발 도구 전용이라 배포본에 넣지 않는다.
# ERPia 경로·엑셀 저장 폴더는 굽지 않는다 — 실행 창이 그 PC 에서 찾아 채운다. 개발 PC 경로를 구우면 남의 PC 에 그 폴더를 만든다 (09-23).
# 빌드 프로그램 전용 값(관리자 계정 포함)은 절대 굽지 않는다 (09-28).
SKIP_KEYS = ("collect_site_code", "excel_path",
             "target_exe", "target_work_dir", "download_base_dir",
             "build_account_name", "build_label", "build_admin_email", "build_admin_password",
             "auto_start")      # 그 PC 에서 사람이 고른다 (09-28) — 관리자 PC 의 선택을 굽지 않는다

def required(values: dict) -> tuple[str, ...]:
    """이 빌드에 꼭 구울 키 (`orchestrator/modules.baked_required` — 실행 창의 알림과 같은 목록).

    기능 고정이면 고정한 기능만, 아니면 실행 창에서 무엇이든 고를 수 있어 전부. 기본값이 없다 (10-02).
    """
    from orchestrator import modules

    ids = set(values.get("run_modules") or []) if values.get("run_modules_locked") else set(modules.IDS)
    return modules.baked_required(ids)


def missing(values: dict) -> list[str]:
    """굽기 전에 비어 있으면 안 되는 키 중 빈 것. 빌드 프로그램이 서버에 등록하기 **전에** 부른다."""
    return [k for k in required(values) if _blank(values.get(k))]


def _blank(value: object) -> bool:
    """빈 값인가. False·0 은 값이다 (읽은 메일도 / 오늘만)."""
    return value is None or value == "" or value == [] or value == {}

# 값을 그대로 찍으면 안 되는 항목. 요약에는 길이만 보여 준다.
# ★ 비밀 목록은 **`config/settings.py` 하나**를 쓴다. 여기 따로 적어 두었더니
#   설정 쪽에 항목이 늘어도 이 도구는 모르는 상태가 됐다 (2026-09-17 정리).


def bake(source: Path = LOCAL_SETTINGS_PATH, out: Path = OUT_PATH) -> Path:
    if not source.exists():
        raise SystemExit(f"[중단] 설정 파일이 없다: {source}")

    raw = read_json_file(source)
    values = {k: v for k, v in raw.items()
              if k in DEFAULTS and k not in SKIP_KEYS}

    unknown = sorted(k for k in raw if k not in DEFAULTS and not k.startswith(WORKER_PREFIX))
    if unknown:
        print(f"  무시한 키 {len(unknown)}개: {', '.join(unknown)}")

    empty = missing(values)
    if empty:
        raise SystemExit(
            "[중단] 구울 값이 비어 있다: " + ", ".join(empty)
            + f"\n  {source} 를 채운 뒤 다시 굽는다. 설명은 docs/SETTINGS.md."
        )
    from collect import sites

    if "mail_url" in required(values) and not sites.available():
        print("  [알림] 메일 사이트 연결(collect/sites/local/)이 없다 — 이 빌드에서 메일 엑셀 받기는 '입력 필요' 로 잠긴다")

    values = _rewrap_secrets(values)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    # 씨앗도 같이 굽는다 — 받은 PC 에서 baked: 를 풀 열쇠다. 원본은 .env (git 밖, 10-02). 빌드 끝에 build/baked 째 지운다
    from utils import secret

    try:
        seed = secret.seed()
    except secret.SecretError as exc:
        raise SystemExit(f"[중단] {exc}") from exc
    (out.parent / secret.SEED_FILE).write_bytes(seed)
    return out


def _rewrap_secrets(values: dict) -> dict:
    """비밀을 **굽는 용 포장으로 바꿔 끼운다.**

    ★ 개발 폴더의 설정에 든 비밀은 `dpapi:` 로 감싸여 있다 — **이 PC, 이 계정
      에서만** 풀린다. 그대로 구우면 받는 사람의 PC 에서 못 푼다.
      그래서 여기서 **풀고 다시 `baked:` 로 감싼다.**

    ★ 구운 값은 키가 프로그램 안에 있어 **"쉽게는 못 읽게"** 수준이다.
      그래도 평문으로 굽는 것보다는 낫다 — 번들을 열어 봐도 바로 읽히지 않는다.
    """
    from utils import secret

    out = dict(values)
    for key in SECRET_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = secret.wrap_baked(str(secret.unwrap(value)))
    for key in SECRET_MAP_KEYS:
        value = out.get(key)
        if isinstance(value, dict):
            out[key] = {k: (secret.wrap_baked(str(secret.unwrap(v)))
                            if isinstance(v, str) and v else v)
                        for k, v in value.items()}
    return out


def summarize(path: Path) -> None:
    values = json.loads(path.read_text(encoding="utf-8"))
    print(f"  구운 항목 {len(values)}개 → {path}")
    for key in sorted(values):
        value = values[key]
        if key in SECRET_KEYS:
            shown = f"(설정됨, {len(str(value))}자)" if value else "(비어 있음)"
        else:
            shown = repr(value)
        print(f"    {key:22s} {shown}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass  # 재설정 불가한 스트림. 출력만 깨질 뿐 굽는 것은 계속한다.

    print(f"설정을 굽는다: {LOCAL_SETTINGS_PATH}")
    path = bake()
    summarize(path)
    # ★ 문구를 고쳤다 (2026-09-17). 예전에는 "평문으로 들어 있다" 고 적혀
    #   있었는데, 이제 비밀번호는 `baked:` 로 감싸서 굽는다. 틀린 경고를 그대로
    #   두면 사람이 실제 위험이 어디에 있는지 헷갈린다.
    print("\n★ 비밀번호는 `baked:` 로 감싸 뒀다. 다만 **키가 프로그램 안에 있어서**"
          "\n  마음먹으면 풀 수 있다. 빌드가 끝나면 이 파일은 지운다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
