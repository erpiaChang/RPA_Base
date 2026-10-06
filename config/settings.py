r"""전역 설정.

환경마다 다른 값(경로, 계정 등)은 코드에 쓰지 않고 `config/settings.local.json`
하나에 모은다. 그 파일은 계정이 들어 있어 배포본에 복사하지 않는다 (`docs/SETTINGS.md`).

**설정 항목은 기능별 파일에 나눠 정의한다.**

    config/fields_collect.py
    config/fields_erpia.py

아래 import 는 **실패를 정상으로 취급한다** — 기능별 빌드가 한 파일만 넣던 때(09-08~10-06)의 처리다.
지금 빌드(`build_run.spec`)는 둘 다 넣는다.

값이 확정되기 전에는 None 으로 둔다. 추측해서 채우지 않는다.
각 항목의 뜻은 `docs/SETTINGS.md` 참고.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    """설정 / 로그가 놓이는 기준 폴더.

    **빌드된 exe 에서는 실행 파일이 있는 폴더다.** PyInstaller onefile 은
    소스를 임시 폴더(`sys._MEIPASS`)에 풀었다가 종료 시 지운다. 거기에
    `settings.local.json` 이나 `logs/` 를 두면 실행할 때마다 사라지고,
    사용자가 설정을 열어 고칠 수도 없다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()

LOG_DIR = PROJECT_ROOT / "logs"
DOCS_DIR = PROJECT_ROOT / "docs"

LOCAL_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.local.json"

# 빌드에 **구워 넣은** 기본값. 있으면 `DEFAULTS` 다음, 로컬 설정보다 먼저 깔린다.
#   · 배포본  : 번들 안(`sys._MEIPASS/config/settings.baked.json`)
#   · 개발    : 보통 없다. 있으면 그것도 읽는다
# 사용자가 exe 만 받아 실행해도 값이 채워져 있게 하려는 것이다.
# 만드는 방법은 `tools/bake_settings.py`, 굽는 배포본은 `build_run.spec` 뿐이다.
BAKED_SETTINGS_NAME = "settings.baked.json"


def _baked_settings_path() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parent.parent
    return root / "config" / BAKED_SETTINGS_NAME


BAKED_SETTINGS_PATH = _baked_settings_path()

# 기능별 설정 파일. 이 배포본에 없는 것은 그냥 빠진다.
FIELD_MODULES = ("config.fields_collect", "config.fields_erpia")


def _collect_defaults() -> dict[str, object]:
    """이 배포본에 들어 있는 기능들의 설정 항목을 모은다.

    ImportError 는 오류가 아니라 **정상**이다. 그 기능이 이 배포본에 없다는 뜻이다.
    """
    merged: dict[str, object] = {}
    for module_name in FIELD_MODULES:
        try:
            module = __import__(module_name, fromlist=["DEFAULTS"])
        except ImportError:
            continue
        merged.update(module.DEFAULTS)
    return merged


DEFAULTS = _collect_defaults()

# --- 비밀로 다루는 항목 -----------------------------------------------------
#
# ★ 이 키들은 파일에 **평문으로 두지 않는다.** 읽을 때 풀고 쓸 때 감싼다
#   (`utils/secret.py`). 새 비밀 항목이 생기면 **여기 추가한다** —
#   빠뜨리면 조용히 평문으로 저장된다.
SECRET_KEYS = ("login_password", "mail_password", "excel_password", "server_build_id",
               "build_admin_password")

# LLM 워커(`llm/worker.py`) 전용 키의 머리 (09-28). RPA 는 읽지도 굽지도 않고 "모르는 키" 경고도 하지 않는다.
# 워커가 파일에서 직접 읽는다 — 워커 키는 워커가 감싸서 넣는다 (`dpapi:`, 이 PC 에서만 풀린다)
WORKER_PREFIX = "llm_"

# --- 관리자가 빌드에 구워 고정하는 항목 (09-28, 사용자 확정) ------------------
#
# ★ 여기 있는 키는 **구운 값이 최종**이다. exe 옆 `settings.local.json` 에 같은 키가 있어도 무시한다
#   (그 파일은 사용자가 열어 고칠 수 있다). 사용자가 고치는 값은 여기 넣지 않는다.
LOCKED_KEYS = ("login_company_code", "login_user_id", "mail_url", "mail_user_id",
               "run_modules_locked", "server_url", "server_anon_key", "server_build_id")

# 프로그램 버전. 서버 보고(`orchestrator/telemetry.py`)가 함께 보낸다. 빌드할 때 올린다.
APP_VERSION = "2026.09.22"
# 값이 딕셔너리이고 그 **안쪽 값**이 비밀인 항목.
SECRET_MAP_KEYS = ("excel_passwords",)


@dataclass
class Timeouts:
    """모든 대기의 상한. 초 단위."""

    app_start: float = 30.0      # 프로그램 실행 후 첫 창 등장
    login: float = 30.0          # 로그인 후 다음 화면 등장 (서버 인증 포함)
    window: float = 15.0         # 창 등장/소멸
    handover: float = 5.0        # 시작한 프로세스가 죽은 뒤 다른 프로세스로 넘기기까지 (런처·업데이터)
    control: float = 10.0        # 컨트롤 등장/활성화
    dialog: float = 10.0         # 팝업 등장/닫힘
    long_task: float = 120.0     # 오래 걸리는 처리
    # 대상 프로그램 업데이트. 사람이 UAC 창을 누르기를 기다리는 시간이
    # 들어 있어 길게 잡는다 (`automation/updater.py`).
    update: float = 600.0
    collect: float = 4 * 3600.0  # 전체 수집. 두세 시간까지 간다
    collect_start: float = 15.0  # 시작 신호 대기
    collect_settle: float = 5.0  # 완료 판정 전 안정화 대기
    page_load: float = 30.0      # 웹 페이지 이동/로딩
    sms_code: float = 120.0      # 인증 문자 도착 대기
    # 휴대폰 연결이 끊겨 [다시 시도]/새로 고침을 누른 뒤 **붙기를 기다리는 상한.**
    # 사람이 폰에서 [Windows와 연결] 을 켜러 가는 시간이 들어 있다.
    # 붙으면 그 즉시 넘어간다 — 5분을 다 쓰는 것이 아니다 (사용자 확정 2026-09-17).
    phone_connect: float = 300.0
    download: float = 60.0       # 파일 다운로드 완료
    poll_interval: float = 0.3   # 조건 대기 폴링 간격


class SettingsNotConfigured(RuntimeError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(
            "설정값이 비어 있다: "
            + ", ".join(names)
            + f"\n{LOCAL_SETTINGS_PATH} 에 값을 채운 뒤 다시 실행할 것."
            + "\n각 항목의 뜻은 docs/SETTINGS.md 를 볼 것."
        )


class Settings:
    """`DEFAULTS` 에 있는 항목만 속성으로 갖는다."""

    def __init__(self) -> None:
        for name, value in DEFAULTS.items():
            # 리스트 기본값은 복사해서 넣는다. 공유하면 한 곳의 변경이 번진다.
            setattr(self, name, list(value) if isinstance(value, list) else value)
        self.backend = "uia"
        self.timeouts = Timeouts()
        # 재시도 상한은 여기 없다 — 자리마다 상수 (docs/ERROR_HANDLING.md 3절)

    def as_dict(self) -> dict[str, object]:
        """`DEFAULTS` 에 있는 항목만 딕셔너리로.

        `backend` / `timeouts` 는 **넣지 않는다** — 그것들은 환경마다
        바뀌는 값이 아니라 코드에 두는 값이라 설정 파일에 나가면 안 된다.
        """
        return {name: getattr(self, name, None) for name in DEFAULTS}

    def missing(self, *names: str) -> list[str]:
        """지정한 설정 중 아직 비어 있는 항목을 돌려준다."""
        return [n for n in names if not getattr(self, n, None)]

    def require(self, *names: str) -> None:
        """설정이 비어 있으면 즉시 중단한다. 추측값으로 진행하지 않는다."""
        empty = self.missing(*names)
        if empty:
            raise SettingsNotConfigured(empty)


def save_local(**values: object) -> Path:
    """확정된 설정값을 `config/settings.local.json` 에 병합 저장한다.

    코드에 경로를 하드코딩하지 않기 위해, 화면에서 고른 값도 여기로 들어온다.
    """
    current: dict = {}
    if LOCAL_SETTINGS_PATH.exists():
        try:
            current = json.loads(LOCAL_SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # ★ 손상된 파일을 **조용히 덮어쓰지 않는다.** 다른 값이 전부 사라진다.
            #   백업을 남기고 경고한다 (2026-09-10 감사).
            backup = LOCAL_SETTINGS_PATH.with_suffix(".json.bak")
            try:
                backup.write_bytes(LOCAL_SETTINGS_PATH.read_bytes())
            except OSError as copy_exc:
                logging.getLogger(__name__).warning(
                    "손상된 설정 백업 실패: %s", copy_exc)
            logging.getLogger(__name__).warning(
                "설정 파일이 손상돼 새로 쓴다 (%s). 이전 내용은 %s 에 남겼다.",
                exc, backup.name)
            current = {}

    unknown = {k for k in values if k not in DEFAULTS and not k.startswith(WORKER_PREFIX)}
    if unknown:
        raise ValueError(f"알 수 없는 설정 항목: {sorted(unknown)}")

    # ★ 비밀은 **감싸서** 파일에 넣는다. 메모리의 `SETTINGS` 에는 푼 값을
    #   그대로 둔다 — 쓰는 쪽(로그인 등)은 감싼 것을 모른다.
    current.update({key: _wrap_value(key, value) for key, value in values.items()})
    LOCAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_SETTINGS_PATH.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for key, value in values.items():
        setattr(SETTINGS, key, value)
    return LOCAL_SETTINGS_PATH


def _wrap_value(key: str, value: object) -> object:
    """비밀이면 감싼다. 아니면 그대로.

    ★ 여기서 감싸는 값은 **이 PC 에 남는 값**이라 DPAPI 를 쓴다 —
      폴더를 통째로 복사해 가도 다른 PC·다른 계정에서는 풀리지 않는다.
      빌드에 굽는 값은 다른 PC 에서 풀려야 해서 방법이 다르다
      (`tools/bake_settings.py`, `utils/secret.wrap_baked`).
    """
    from utils import secret

    if key in SECRET_KEYS and isinstance(value, str):
        return secret.wrap_local(value)
    if key in SECRET_MAP_KEYS and isinstance(value, dict):
        return {k: secret.wrap_local(v) if isinstance(v, str) else v
                for k, v in value.items()}
    return value


def _apply_file(settings: Settings, path: Path, what: str) -> bool:
    """설정 파일 하나를 덮어씌운다. 파일이 없으면 아무 일도 하지 않는다.

    **모르는 항목은 무시한다.** 설정 파일 하나를 여러 배포본이 함께 쓸 수 있고,
    이 배포본에 없는 기능의 항목이 들어 있는 것은 오류가 아니다.

    ★ 그래도 **무엇을 버렸는지 남긴다.** 예전에는 조용히 버려서,
      `hold_exclude_codes` 를 `hold_exclude_code` 로 오타 내면 배송보류 제외가
      전혀 동작하지 않는데 로그에도 흔적이 없었다 (2026-09-10 감사).
    """
    if not path.exists():
        return False

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path} 를 읽을 수 없다: {exc}") from exc

    unknown = sorted(k for k in raw if k not in DEFAULTS and not k.startswith(WORKER_PREFIX))
    if unknown:
        logging.getLogger(__name__).warning(
            "%s 에서 이 배포본이 모르는 설정 키 %d개는 무시한다: %s",
            what, len(unknown), ", ".join(unknown))
    for key, value in raw.items():
        if key in DEFAULTS:
            setattr(settings, key, _unwrap_value(key, value, what))
    return True


def _unwrap_value(key: str, value: object, what: str) -> object:
    """감싼 비밀을 푼다. **읽는 지점이 여기 하나뿐**이라 여기서만 하면 된다.

    ★ 풀지 못하면 **크게 알리고 값을 비운다.** 조용히 평문으로 두면 감싼 적이
      없는 것과 구별되지 않고, 그대로 두면 감싼 문자열이 비밀번호로 입력된다.
      비우면 `Settings.require()` 가 "설정이 비었다" 로 잡아 준다.
    """
    from utils import secret

    def one(name: str, item: object) -> object:
        if not secret.is_wrapped(item):
            return item
        try:
            return secret.unwrap(item)
        except secret.SecretError as exc:
            logging.getLogger(__name__).error(
                "%s 의 %s 를 풀지 못했다 — %s. 화면에서 다시 입력해야 한다.",
                what, name, exc)
            return ""

    if key in SECRET_KEYS:
        return one(key, value)
    if key in SECRET_MAP_KEYS and isinstance(value, dict):
        return {k: one(f"{key}[{k}]", v) for k, v in value.items()}
    return value


def _load() -> Settings:
    """`DEFAULTS` → 구워 넣은 값 → 로컬 설정 순으로 덮어씌운다.

    나중에 읽은 것이 이긴다. 그래서 배포본은 **exe 만 받아도 값이 채워져 있고**,
    사용자가 exe 옆 `config/settings.local.json` 을 고치면 그쪽이 우선한다.

    예외는 `LOCKED_KEYS` 다 (09-28). 관리자가 구운 값은 사용자가 그 파일을 열어 고쳐도 되돌린다 —
    업체코드·아이디를 바꿔 남의 계정으로 돌리는 것을 막는다. `run_modules_locked` 를 구운 빌드는
    `run_modules` 까지 잠근다 (기능 고정 빌드).
    """
    settings = Settings()
    _apply_file(settings, BAKED_SETTINGS_PATH, "빌드에 구워 넣은 설정")
    baked = {key: getattr(settings, key, None) for key in LOCKED_KEYS}
    if getattr(settings, "run_modules_locked", False):
        baked["run_modules"] = getattr(settings, "run_modules", None)
    _apply_file(settings, LOCAL_SETTINGS_PATH, str(LOCAL_SETTINGS_PATH))
    LOCKED_IGNORED.clear()
    for key, value in baked.items():
        if value in (None, "", [], {}):
            continue                                # 안 구운 빌드(개발)는 로컬 값을 그대로 쓴다
        if getattr(settings, key, None) != value:
            LOCKED_IGNORED.append(key)
            setattr(settings, key, value)
    return settings


# 로컬 설정이 덮으려다 막힌 고정 키 (09-28). `_load()` 는 **모듈을 불러오는 순간** 돌아 로그 파일이
# 아직 없다 — 콘솔 없는 exe 에서는 그때 낸 경고가 그대로 사라졌다 (실기에서 확인). 그래서 모아 두고,
# 로그를 연 뒤 실행 창이 `log_locked_ignored()` 로 남긴다.
LOCKED_IGNORED: list[str] = []


def log_locked_ignored() -> None:
    """고정값을 덮으려던 로컬 설정이 있었으면 로그에 남긴다. 로그를 연 뒤에 부른다."""
    for key in LOCKED_IGNORED:
        logging.getLogger(__name__).warning("%s 는 빌드에 고정된 값이라 로컬 설정을 무시한다", key)


SETTINGS = _load()
