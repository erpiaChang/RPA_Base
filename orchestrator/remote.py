r"""원격 설정·명령 (09-28, 사용자 요청) — 웹에서 바꾼 설정과 [실행]·[중단]·예약 멈춤을 가져온다.

서버는 PC 를 부르지 않는다. 스레드 하나가 **30초마다** `rpc/poll` 을 부르고, 받은 것은 큐(`inbox`)에 넣기만
한다. 창(GUI 스레드)이 1초 시계에서 큐를 비워 적용한다 — 이 모듈은 화면도 흐름도 모른다.

| 주고받는 것 | 방향 | |
| --- | --- | --- |
| 설정 (`REMOTE_KEYS` 만) | 서버 → PC | 서버 판(`version`)이 PC 가 아는 것보다 새것일 때만 온다 |
| 설정 | PC → 서버 | 창에서 저장하면 `push()`. **PC 가 서버 최신판을 받은 뒤에만** 서버가 받는다 (웹 쪽이 이긴다) |
| 명령 `start`·`stop`·`pause`·`resume` | 서버 → PC | 한 번만 온다. 결과는 `done()` → 다음 poll 에 실린다 |

비밀번호는 오가지 않는다 — `REMOTE_KEYS` 에 없고, 서버도 모르는 키를 거부한다 (`server/schema.sql` 7절).
"""
from __future__ import annotations

import json
import queue
import threading

from config.settings import LOG_DIR
from orchestrator.modules import IDS as MODULE_IDS
from orchestrator.telemetry import _http_post, _server
from utils.logger import get_logger

log = get_logger(__name__)

POLL = "/rest/v1/rpc/poll"
POLL_SECONDS = 30.0
RESPONSE_LIMIT = 65536          # 설정이 실린 응답 (서버 check 가 8KB)
# 마지막으로 **적용한** 서버 판. 다시 켰을 때 이 판에서 이어가야 PC 에서 고친 값이 서버에 받아들여진다
STATE_PATH = LOG_DIR / "remote_state.json"

# 웹에서 바꿀 수 있는 설정 (사용자 확정 09-28 — 비밀번호 빼고, PC 마다 다른 경로도 뺀다).
# `server/schema.sql` 의 `private.clean_settings` 와 **같은 목록**이어야 한다 — 모르는 키는 서버가 요청째 거부한다.
REMOTE_KEYS = (
    "run_modules", "auto_run_enabled", "auto_run_mode", "auto_run_times", "auto_run_interval_minutes",
    "collect_sources", "delivery_company", "delivery_box", "logistics_mode", "sales_mode", "hold_exclude_codes",
    "sms_source", "adb_connection", "adb_wireless_address",
)
START, STOP, PAUSE, RESUME = "start", "stop", "pause", "resume"
SETTINGS_EVENT, COMMAND_EVENT = "settings", "command"


def from_settings(settings, *, locked_modules: list[str] | None, post=None) -> Remote | None:
    """서버 값이 셋 다 있을 때만. 아니면 None — 원격 없이 지금처럼 돈다."""
    url, anon, build = _server(settings)
    if not (url and anon and build):
        return None
    from utils.machine import machine_id

    return Remote(url, anon, build, machine_id(), locked_modules=locked_modules, post=post)


def snapshot(settings) -> dict:
    """지금 설정에서 원격 키만. 목록은 순서를 맞춘다 (`normalize`)."""
    return {key: normalize(key, getattr(settings, key, None)) for key in REMOTE_KEYS}


def normalize(key: str, value: object) -> object:
    """순서가 뜻이 없는 목록을 한 가지 순서로 — 웹(엑셀·자동)과 PC(자동·엑셀)가 달라 매번 '바뀜' 이었다."""
    if isinstance(value, tuple):
        value = list(value)
    if key == "collect_sources" and isinstance(value, list):
        return sorted(value)
    if key == "run_modules" and isinstance(value, list):
        return [m for m in MODULE_IDS if m in value]
    return value


# sales_mode 는 automation/order_mapping.SALES_MODES 와 같다 (10-01) — 이 파일은 모든 배포본에 들어가 그쪽을 부르지 않는다
_CHOICES = {"auto_run_mode": ("daily", "interval"), "logistics_mode": ("자동", "수동"),
            "sales_mode": ("전체", "선택주문"),
            "sms_source": ("phonelink", "adb", "auto"), "adb_connection": ("usb", "wireless")}
_TEXT_LISTS = {"run_modules": (1, 4, 20, MODULE_IDS), "collect_sources": (1, 2, 10, ("excel", "site")),
               "auto_run_times": (0, 12, 60, None), "hold_exclude_codes": (0, 200, 50, None)}


def acceptable(key: str, value: object) -> bool:
    """서버 `private.clean_settings` 가 받는 값인가. 안 받을 값을 올리면 **요청째 400** 이라 PC 에서 먼저 거른다."""
    if key in _CHOICES:
        return value in _CHOICES[key]
    if key in _TEXT_LISTS:
        least, most, width, allowed = _TEXT_LISTS[key]
        return (isinstance(value, list) and least <= len(value) <= most
                and all(isinstance(v, str) and len(v) <= width and (allowed is None or v in allowed)
                        for v in value))
    if key == "auto_run_enabled":
        return isinstance(value, bool)
    if key == "auto_run_interval_minutes":
        return isinstance(value, int) and not isinstance(value, bool) and 10 <= value <= 1440
    return value is None or (isinstance(value, str) and len(value) <= 50)   # 택배사·박스·무선 주소


def _load_version() -> int:
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("version") or 0)
    except (OSError, ValueError, AttributeError):
        return 0                    # 처음이거나 깨졌다 — 서버 판을 그대로 받는다


class Remote:
    def __init__(self, url: str, anon: str, build: str, machine: str, *,
                 locked_modules: list[str] | None, post=None) -> None:
        self._url, self._anon, self._build, self._machine = url, anon, build, machine
        self._post = post or _http_post
        # 기능 고정 빌드면 그 기능, 아니면 빈 목록 — 서버가 웹에서 기능 칸을 잠근다
        self._locked = list(locked_modules or [])
        self.inbox: queue.Queue = queue.Queue()
        self.version = _load_version()      # 마지막으로 받은 서버 판 (시작은 지난번에 적용한 판)
        self.unauthorized = False           # 401 — 다른 PC 에 묶였거나 폐기됐다. 더 묻지 않는다
        self._lock = threading.Lock()
        self._pushed: dict | None = None
        self._results: list[dict] = []
        self._stop = threading.Event()
        # 결과·올릴 설정이 생기면 30초를 기다리지 않고 바로 묻는다 — 기다리는 사이 창이 닫히면
        # 명령 결과가 사라져 웹에 "처리 중" 으로 남았다 (09-28 실기, [중단] 직후 창을 닫음)
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 창이 부른다
    def start(self, initial: dict | None = None) -> None:
        """스레드를 띄운다. 첫 poll 은 곧바로 — 켜자마자 웹 설정을 받는다.

        `initial` 은 지금 PC 의 설정이다. 서버에 아직 판이 없으면(새 빌드) 그것이 첫 판이 된다.
        """
        if initial is not None:
            self.push(initial)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="remote")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()

    def push(self, values: dict) -> None:
        """창에서 저장한 값을 다음 poll 에 싣는다. 원격 키만, 서버가 받을 값만."""
        picked = {key: normalize(key, values[key]) for key in REMOTE_KEYS if key in values}
        if self._locked:
            picked.pop("run_modules", None)
        # 아직 안 고른 값(기본값 없음, 10-02)은 조용히 뺀다 — 고를 값에 빈 값을 올리면 서버가 요청째 400 이다
        for key in [k for k, v in picked.items() if (v is None or v == []) and not acceptable(k, v)]:
            picked.pop(key)
        refused = [key for key, value in picked.items() if not acceptable(key, value)]
        for key in refused:
            log.warning("서버가 받지 않는 값이라 올리지 않는다 — %s=%r", key, picked.pop(key))
        if not picked:
            return
        with self._lock:
            self._pushed = {**(self._pushed or {}), **picked}
        self._wake.set()

    def done(self, command_id: int, result: str) -> None:
        with self._lock:
            self._results.append({"id": command_id, "result": str(result)[:200]})
        self._wake.set()

    def applied(self, version: int) -> None:
        """창이 그 판의 설정을 **적용한 뒤** 부른다. 다시 켰을 때 이 판에서 이어간다."""
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps({"version": int(version)}), encoding="utf-8")
        except OSError as exc:
            log.warning("원격 판을 기록하지 못했다 (다시 켜면 서버 판을 새로 받는다): %s", exc)

    # ---------------------------------------------------------------- 스레드
    def _loop(self) -> None:
        while not self._stop.is_set() and not self.unauthorized:
            try:
                self.poll_once()
            except Exception as exc:                        # noqa: BLE001 — 원격 때문에 창이 죽으면 안 된다
                log.warning("원격 확인 실패 (다음에 다시): %s", exc)
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def poll_once(self) -> None:
        with self._lock:
            pushed, results = self._pushed, list(self._results)
        payload = {"device_key": self._build, "machine": self._machine,
                   "known_version": self.version, "locked_modules": self._locked}
        if pushed:
            payload["pushed"] = _jsonable(pushed)
        if results:
            payload["results"] = results
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            status, text = self._post(self._url + POLL, self._anon, body, RESPONSE_LIMIT)
        except Exception as exc:                            # noqa: BLE001 — 네트워크. 다음 주기에 다시
            log.info("원격 확인 — 서버에 닿지 못했다 (%s)", type(exc).__name__)
            return
        if status == 401:
            log.warning("원격 확인 — 서버가 이 PC 를 거부했다 (401). 더 묻지 않는다")
            self.unauthorized = True
            return
        if not 200 <= status < 300:
            # 400 이면 올린 설정이 틀렸다 — 같은 것을 또 보내지 않는다 (명령은 계속 받아야 한다)
            log.warning("원격 확인 — 서버 응답 %s: %s", status, text[:200])
            if status == 400:
                with self._lock:
                    self._pushed = None
            return
        data = json.loads(text or "{}")          # 못 읽으면 예외 — 보낸 것을 지우지 않고 다음에 다시 보낸다
        with self._lock:
            # 보낸 것만 지운다 — 보내는 사이 새로 들어온 결과·설정은 남긴다
            self._results = self._results[len(results):]
            if self._pushed is pushed:
                self._pushed = None
        self._take(data)

    def _take(self, data: dict) -> None:
        version = int(data.get("version") or 0)
        settings = data.get("settings")
        if isinstance(settings, dict) and version > self.version:
            picked = {key: normalize(key, settings[key]) for key in REMOTE_KEYS if key in settings}
            # 판은 설정이 비어 있어도 넘긴다 — 창이 적용하고 `applied()` 로 기록한다
            self.inbox.put((SETTINGS_EVENT, picked, version))
        self.version = max(self.version, version)
        for command in data.get("commands") or []:
            kind = command.get("kind")
            if kind not in (START, STOP, PAUSE, RESUME):
                continue
            log.info("원격 명령 받음 — %s (id %s)", kind, command.get("id"))
            self.inbox.put((COMMAND_EVENT, int(command["id"]), kind, list(command.get("modules") or [])))


def _jsonable(values: dict) -> dict:
    """튜플 등을 JSON 목록으로."""
    return {key: list(value) if isinstance(value, tuple) else value for key, value in values.items()}
