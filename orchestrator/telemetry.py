r"""서버 보고 — 실행 진행·이력을 Supabase 로 보낸다 (`docs/SERVER_PLAN.md` D-2·D-10, 2026-09-22).

어느 배포본에나 들어간다. 기능 이름을 적지 않는다.

## 지키는 것

- **RPA 스레드는 기다리지 않는다.** `on_step`·로그 핸들러는 큐에 넣기만 한다. 보내는 것은 스레드 하나가
  2초마다(또는 20개가 차면) `POST /rest/v1/rpc/ingest` 로 한다. 표준 라이브러리 `urllib` 만 쓴다.
- 서버가 죽어도 실행은 그대로 돈다. 못 보낸 묶음은 `logs/outbox.jsonl` 에 두고 다음 `start()`/`finish()`/5분마다
  다시 보낸다. **401/400/429 는 다시 보내지 않는다** (키 폐기·형식 오류 — 같은 것을 또 보내도 같다).
- 보내는 글은 사용자 로그·단계 사진의 **사람 말**뿐이다. 예외 원문·경로·리포트 경로는 싣지 않는다
  (`run_end` 는 `history.record` 의 dict 에서 `report`·`steps` 를 뺀다).
- 시험 실행(dry-run)은 보내지 않는다 (`start(dry_run=True)` 는 아무 일도 안 한다, 사용자 확정 09-22).
- 사람 손이 지금 필요한 일(`alert` — UAC 동의 대기·예약 전 휴대폰 점검 실패, 10-01)은 **따로·한 번만** 보낸다.
- `logs/run_inflight.json` 에 도는 실행을 적어 두고, 끝나기 전에 프로그램이 죽으면 다음 `start()` 가 그 실행에
  `run_end(state="lost")` 를 먼저 보낸다.

## 조절

`Hooks._notify` 는 건수 갱신마다 `on_step` 을 부른다. **상태가 바뀐 사진은 즉시**, 같은 상태의 갱신은 단계마다
마지막 것만 다음 묶음(2초)에 싣는다.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib import error, parse, request

from config.settings import APP_VERSION, LOG_DIR
from orchestrator import steps
from utils.logger import USER_LOGGER, get_logger, user_log

log = get_logger(__name__)

OUTBOX_PATH = LOG_DIR / "outbox.jsonl"
INFLIGHT_PATH = LOG_DIR / "run_inflight.json"
INGEST = "/rest/v1/rpc/ingest"

# `verify()` 판정. blocked 는 다시 해도 같다 (다른 PC 에 묶인 빌드), later 는 잠시 뒤 다시 해 본다.
VERIFY_OK, VERIFY_BLOCKED, VERIFY_LATER = "ok", "blocked", "later"

BATCH_SIZE = 20             # 이만큼 차면 바로 보낸다
BATCH_SECONDS = 2.0         # 아니면 이 주기로
HEARTBEAT_SECONDS = 30.0    # 도는 동안
OUTBOX_RETRY_SECONDS = 300.0
FINISH_WAIT_SECONDS = 5.0   # finish() 가 큐 비우기를 기다리는 상한
REQUEST_TIMEOUT = 5.0
WARM_TIMEOUT = 15           # 루트 인증서 채우기(PowerShell) 한 번의 상한(초)
CERT_FAIL_TEXT = ("서버와 안전하게 연결하지 못했습니다 (보안 인증서를 확인하지 못함). "
                  "Windows 업데이트를 하고 PC 의 날짜·시간이 맞는지 확인하세요. 잠시 뒤 다시 시도합니다.")
OUTBOX_KEEP_LINES = 1000
OUTBOX_KEEP_SECONDS = 24 * 3600
TEXT_LIMIT = 300            # 서버 컬럼 check 와 같다

# 보낸 결과. 401/400/429 는 버린다 — 재시도해도 같다.
OK, DROP, RETRY = "ok", "drop", "retry"
LOST_SUMMARY = "끝나기 전에 프로그램이 종료됨"
# 사람 손이 **지금** 필요한 일 (10-01) — 서버가 업체 담당자에게 메일. server/schema.sql 의 alerts 종류·ingest alert 검사와 같다
# (끊김·무인 실행 결과는 서버가 스스로 만든다)
ALERT_KINDS = ("uac", "phone")


def _server(settings) -> tuple[str, str, str]:
    """(url, anon, build_id) — 비어 있으면 빈 문자열."""
    return ((getattr(settings, "server_url", None) or "").strip().rstrip("/"),
            (getattr(settings, "server_anon_key", None) or "").strip(),
            (getattr(settings, "server_build_id", None) or "").strip())


def from_settings(settings) -> Reporter | None:
    """세 값이 다 있을 때만 보고기를 만든다. 아니면 None = 지금처럼 돈다."""
    url, anon, build = _server(settings)
    if not (url and anon and build):
        # 값 없이 조용히 꺼지면 "왜 대시보드에 안 뜨나" 를 로그로 가릴 수 없다 (09-23, 다른 PC 에서 실제로 그랬다)
        missing = [name for name, value in (("server_url", url), ("server_anon_key", anon),
                                            ("server_build_id", build)) if not value]
        log.info("서버 보고 꺼짐 — 비어 있음: %s", ", ".join(missing))
        return None
    log.info("서버 보고 켜짐 — %s", url)
    from utils.machine import machine_id

    return Reporter(url, anon, build, machine=machine_id())


def verify(settings, *, post=None) -> tuple[str, str]:
    """이 PC 에서 이 빌드를 쓸 수 있는지 서버에 확인한다 (09-28). 돌려주는 값: (판정, 사용자에게 보일 말).

    `ingest` 를 **빈 이벤트 배열**로 한 번 부른다 — 서버는 처음 보는 빌드면 이 PC 로 묶고, 이미 다른 PC 에
    묶였으면 401 을 준다. 그래서 등록과 확인이 같은 호출이고, 따로 `enroll` 을 두지 않는다.

    서버 값이 없는 빌드(보고를 안 쓰는 빌드)는 `VERIFY_OK` — 실행을 막지 않는다.
    """
    from utils.machine import machine_id

    url, anon, build = _server(settings)
    if not (url and anon and build):
        return VERIFY_OK, ""
    machine = machine_id()
    if not machine:
        return VERIFY_BLOCKED, ("이 PC 의 식별값을 읽지 못해 서버에 확인할 수 없습니다. "
                                "관리자에게 문의하세요.")
    payload = json.dumps({"device_key": build, "machine": machine, "events": []},
                         ensure_ascii=False).encode("utf-8")
    try:
        status, _ = (post or _http_post)(url + INGEST, anon, payload)
    except Exception as exc:                            # noqa: BLE001 — 네트워크·타임아웃
        if is_cert_error(exc):
            return VERIFY_LATER, CERT_FAIL_TEXT
        return VERIFY_LATER, (f"서버에 연결하지 못했습니다 ({type(exc).__name__}). "
                              "인터넷 연결을 확인하세요. 잠시 뒤 다시 시도합니다.")
    if status == 401:
        # 다른 PC 에 묶인 빌드와 폐기된 빌드를 서버가 **같은 401** 로 준다 (ID 존재 여부를 안 새려고).
        # 그래서 문구가 둘 다 말한다 — 폐기인데 "다른 PC" 라고만 하면 사용자가 엉뚱한 PC 를 찾는다 (09-28 실기)
        return VERIFY_BLOCKED, ("이 PC 에서는 이 프로그램을 쓸 수 없습니다 — 다른 PC 에 등록되어 있거나 "
                                "사용이 중지된 프로그램입니다. 관리자에게 문의하세요.")
    if status == 400:
        return VERIFY_BLOCKED, "이 PC 의 식별값을 서버가 받지 않았습니다. 관리자에게 문의하세요."
    if not 200 <= status < 300:
        return VERIFY_LATER, f"서버가 확인을 받지 않았습니다 ({status}). 잠시 뒤 다시 시도합니다."
    log.info("서버 확인 통과 — 이 PC 에서 쓸 수 있다")
    return VERIFY_OK, ""


# ★ 갓 설치한 Windows 는 루트 인증서가 저장소에 다 없다 — Windows 는 자기(CryptoAPI) 검증 때만 받아 오고 파이썬 ssl 은
#   그것을 일으키지 않는다 (10-07, 다른 RPA 가 샌드박스에서 겪음). 그래서 인증서 오류면 PowerShell 로 한 번 접속해
#   Windows 가 받게 하고, 새 문맥(저장소를 다시 읽음)으로 한 번 더 보낸다. 검증은 끄지 않는다 (SERVER_PLAN D-10).
_opener = None
_warmed = False
_net_lock = threading.Lock()


def is_cert_error(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError)


def _get_opener():
    global _opener
    with _net_lock:
        if _opener is None:
            _opener = request.build_opener(request.HTTPSHandler(context=ssl.create_default_context()))
        return _opener


def _warm_roots(url: str, *, run=subprocess.run) -> bool:
    """Windows 가 이 서버의 루트 인증서를 받게 창 없이 한 번 접속한다 (프로세스당 1회, 응답은 안 본다). 했으면 True."""
    global _warmed
    with _net_lock:
        if _warmed:
            return False
        _warmed = True
    parts = parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        return False
    # 3072 = TLS 1.2 (옛 Windows 10 의 PowerShell 5.1 기본은 TLS 1.0). 주소는 글에 넣지 않고 환경변수로 넘긴다
    script = ("[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; "
              f"try {{ Invoke-WebRequest -Uri $env:RPA_WARM_URL -UseBasicParsing -TimeoutSec {WARM_TIMEOUT} "
              "| Out-Null } catch { }")
    log.info("서버 인증서 오류 — Windows 가 루트 인증서를 받게 한 번 접속한다")
    try:
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=WARM_TIMEOUT + 10, creationflags=subprocess.CREATE_NO_WINDOW,
            env=dict(os.environ, RPA_WARM_URL=f"{parts.scheme}://{parts.netloc}/"))
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("루트 인증서 채우기를 하지 못했다(그대로 진행): %s", exc)
    return True


def _http_post(url: str, anon_key: str, payload: bytes, limit: int = 1000) -> tuple[int, str]:
    global _opener
    req = request.Request(url, data=payload, method="POST",
                          headers={"apikey": anon_key, "Content-Type": "application/json"})
    for attempt in (0, 1):
        try:
            with _get_opener().open(req, timeout=REQUEST_TIMEOUT) as resp:     # https 고정, 검증 켬
                return resp.status, resp.read(limit).decode("utf-8", "replace")
        except error.HTTPError as exc:
            return exc.code, exc.read(400).decode("utf-8", "replace")
        except error.URLError as exc:
            if not is_cert_error(exc):
                raise
            with _net_lock:
                _opener = None                  # 다음 요청은 지금의 Windows 저장소로 새 문맥
            if attempt or not _warm_roots(url):
                raise
    raise AssertionError("unreachable")


def _cut(text: object) -> str:
    return str(text or "")[:TEXT_LIMIT]


class _UserLogHandler(logging.Handler):
    """`user` 로거 한 줄 → `log` 이벤트. 다른 로거는 받지 않는다."""

    def __init__(self, reporter: Reporter) -> None:
        super().__init__()
        self.reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != USER_LOGGER:
            return
        try:
            self.reporter._log(record.levelname.lower(), record.getMessage())
        except Exception:                                   # noqa: BLE001 — 보고 때문에 실행이 죽으면 안 된다
            pass


class Reporter:
    def __init__(self, url: str, anon_key: str, device_key: str, *,
                 outbox: Path = OUTBOX_PATH, inflight: Path = INFLIGHT_PATH,
                 post=None, app_version: str = APP_VERSION, machine: str = "") -> None:
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.device_key = device_key              # exe 에 구운 빌드 ID. 서버 파라미터 이름이라 device_key 로 둔다
        self.machine = machine                    # 서버가 처음 보고 때 이 PC 로 묶고, 그 뒤로는 대조한다 (09-28)
        self.outbox = outbox
        self.inflight = inflight
        self.app_version = app_version
        # (본문 bytes) -> (HTTP 상태, 응답 글). 확인 도구가 가짜를 준다
        self._post = post or self._http_post
        self._queue: list[dict] = []
        self._alerts: list[dict] = []           # 따로 보낸다 (`alert`)
        self._latest: dict[str, dict] = {}      # 같은 상태의 단계 사진 — 마지막 것만
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._drained = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hooks = None
        self._handler: _UserLogHandler | None = None
        self._orig_on_step = None
        self._orig_attend = None
        self._orig_alert = None
        self._run_id = ""
        self._seq = 0
        self._last_state: dict[str, str] = {}
        self._last_heartbeat = 0.0
        self._last_outbox = 0.0
        self._outbox_due = True
        self.failed = 0                           # 버린 묶음 수 (상태줄이 보여 준다)
        self.sent = 0
        self.unauthorized = False                 # 401 을 받았다 — 이 PC 에서 쓸 수 없는 빌드 (실행 창이 잠근다)

    # ---------------------------------------------------------------- 공개
    def start(self, hooks, *, trigger: str, modules: list[str] = (), dry_run: bool = False) -> str:
        """실행 시작. `on_step`·`attend` 를 감싸고 `user` 로거에 핸들러를 단다. run_id 를 돌려준다."""
        if dry_run:
            return ""
        try:
            self._recover_inflight()
            self._hooks = hooks
            self._run_id = str(uuid.uuid4())
            self._seq = 0
            self._last_state = {}
            self._write_inflight()
            self._orig_on_step = hooks.on_step
            hooks.on_step = self._on_step
            self._orig_attend = hooks.attend
            hooks.attend = self._attend
            self._orig_alert = hooks.alert
            hooks.alert = self._hook_alert
            self._handler = _UserLogHandler(self)
            user_log().addHandler(self._handler)
            self._put({"type": "run_start", "trigger": _cut(trigger), "modules": list(modules)[:10],
                       "plan": [{"id": s.id, "name": s.name} for s in getattr(hooks, "plan", [])],
                       "app_version": self.app_version})
            self._last_heartbeat = time.monotonic()
            self._outbox_due = True
            self._ensure_thread()
        except Exception as exc:                            # noqa: BLE001
            log.warning("서버 보고를 시작하지 못했다(실행은 계속): %s", exc)
        return self._run_id

    def finish(self, entry: dict | None) -> None:
        """실행 끝. `history.record` 의 dict 를 받아 `run_end` 를 보내고 최대 5초 큐를 비운다."""
        if not self._run_id:
            return
        try:
            self._flush_latest()
            body = {"type": "run_end", "state": (entry or {}).get("state") or "failed",
                    "summary": _cut((entry or {}).get("summary")),
                    "elapsed": (entry or {}).get("elapsed"),
                    "attention": (entry or {}).get("attention") or 0,
                    "modules": list((entry or {}).get("modules") or [])[:10],
                    "trigger": _cut((entry or {}).get("trigger"))}
            self._put(body)
            self._detach()
            self._outbox_due = True
            self._wait_drained(FINISH_WAIT_SECONDS)
            self._clear_inflight()
        except Exception as exc:                            # noqa: BLE001
            log.warning("서버 보고를 끝맺지 못했다: %s", exc)
        finally:
            self._run_id = ""

    def idle(self, *, next_run_at: float | None, auto_run_enabled: bool, paused: bool) -> None:
        """앱이 떠 있는 동안 15분마다 — 살아 있음·다음 예약. run_id 가 없다."""
        body = {"type": "idle", "auto_run_enabled": bool(auto_run_enabled), "paused": bool(paused),
                "app_version": self.app_version}
        if next_run_at:
            body["next_run_at"] = float(next_run_at)
        self._put(body, with_run=False)
        self._ensure_thread()

    def alert(self, kind: str, text: str) -> None:
        """사람 손이 **지금** 필요한 일 — 서버가 1분 안에 업체 담당자에게 메일 (10-01). 기다리지 않는다.

        도는 실행 안이면 그 실행에 붙는다(UAC 동의 대기), 밖이면 실행 없이 간다(예약 전 휴대폰 점검).
        ★ **따로 보낸다** — 이 종류를 모르는 옛 서버는 묶음째 400 을 준다. 같이 보내면 단계·로그까지 버려진다.
        ★ outbox 에 남기지 않는다 — 몇 시간 뒤의 '지금 [예] 를 누르세요' 는 쓸모가 없다.
        """
        if kind not in ALERT_KINDS:
            log.warning("모르는 알림 종류라 보내지 않는다: %s", kind)
            return
        body = {"type": "alert", "kind": kind, "text": _cut(text), "at": time.time()}
        if self._run_id:
            body["run_id"] = self._run_id
        with self._lock:
            self._alerts.append(body)
        self._drained.clear()
        self._ensure_thread()

    def skipped(self, planned_at: float, reason: str, modules: list[str] = ()) -> None:
        """예약 회차를 건너뛰었다 — 실행 하나로 남긴다 (`run_start` + `run_end(skipped)`, 사용자 확정 09-22)."""
        run_id = str(uuid.uuid4())
        at = float(planned_at or time.time())
        self._put({"type": "run_start", "run_id": run_id, "seq": 1, "at": at, "trigger": "예약 실행",
                   "modules": list(modules)[:10], "plan": [], "app_version": self.app_version}, with_run=False)
        self._put({"type": "run_end", "run_id": run_id, "seq": 2, "at": at, "state": "skipped",
                   "summary": _cut(f"건너뜀 — {reason}"), "attention": 0, "trigger": "예약 실행"}, with_run=False)
        self._ensure_thread()

    def close(self) -> None:
        """스레드를 멈춘다 (창을 닫을 때·확인 도구). 남은 것은 outbox 로 간다."""
        self._detach()
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(FINISH_WAIT_SECONDS)

    # --------------------------------------------------------- 이벤트 넣기
    def _on_step(self, event: steps.StepEvent) -> None:
        try:
            if self._orig_on_step is not None:
                self._orig_on_step(event)
        finally:
            try:
                body = {"type": "step", "step_id": event.step_id, "index": event.index, "total": event.total,
                        "name": _cut(event.name), "state": event.state, "detail": _cut(event.detail),
                        "activity": _cut(event.activity), "total_items": event.total_items,
                        "done_items": event.done_items, "ok_items": event.ok_items,
                        "failed_items": event.failed_items, "started_at": event.started_at,
                        "finished_at": event.finished_at, "elapsed": round(event.elapsed, 1),
                        "error": _cut(event.error)}
                if self._last_state.get(event.step_id) != event.state:
                    self._last_state[event.step_id] = event.state
                    with self._lock:
                        pending = self._latest.pop(event.step_id, None)
                    if pending is not None:
                        self._put(pending)          # 같은 상태의 마지막 사진(건수)을 잃지 않는다
                    self._put(body)
                else:
                    with self._lock:
                        self._latest[event.step_id] = body       # 같은 상태 — 마지막 사진만
            except Exception:                               # noqa: BLE001
                pass

    def _attend(self, text: str) -> None:
        try:
            if self._orig_attend is not None:
                self._orig_attend(text)
        finally:
            try:
                self._put({"type": "attend", "text": _cut(text)})
            except Exception:                               # noqa: BLE001
                pass

    def _hook_alert(self, kind: str, text: str) -> None:
        try:
            if self._orig_alert is not None:
                self._orig_alert(kind, text)
        finally:
            try:
                self.alert(kind, text)
            except Exception:                               # noqa: BLE001
                pass

    def _log(self, level: str, text: str) -> None:
        if not self._run_id:
            return
        step_id = ""
        hooks = self._hooks
        if hooks is not None:
            run = next((r for r in getattr(hooks, "runs", []) if r.state == steps.RUNNING), None)
            step_id = run.step.id if run is not None else ""
        self._put({"type": "log", "level": level[:10], "step_id": step_id, "text": _cut(text)})

    def _heartbeat(self) -> None:
        locked = paused = None
        try:
            from utils import process

            locked = bool(process.screen_locked())
        except Exception:                                   # noqa: BLE001
            locked = None
        token = getattr(self._hooks, "token", None)
        if token is not None:
            paused = bool(getattr(token, "paused", False))
        self._put({"type": "heartbeat", "screen_locked": locked, "paused": paused})

    def _put(self, body: dict, with_run: bool = True) -> None:
        if with_run:
            if not self._run_id:
                return
            body.setdefault("run_id", self._run_id)
            self._seq += 1
            body.setdefault("seq", self._seq)
        body.setdefault("at", time.time())
        with self._lock:
            self._queue.append(body)
            full = len(self._queue) >= BATCH_SIZE
        self._drained.clear()
        if full:
            self._wake.set()

    def _flush_latest(self) -> None:
        with self._lock:
            pending = list(self._latest.values())
            self._latest.clear()
        for body in pending:
            self._put(body)

    # ------------------------------------------------------------ 스레드
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._wake.set()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="telemetry", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(BATCH_SECONDS)
            self._wake.clear()
            try:
                self._cycle()
            except Exception as exc:                        # noqa: BLE001
                log.warning("서버 보고 스레드 오류(계속): %s", exc)
        try:
            self._cycle()
        except Exception:                                   # noqa: BLE001
            pass

    def _cycle(self) -> None:
        now = time.monotonic()
        if self._run_id and now - self._last_heartbeat >= HEARTBEAT_SECONDS:
            self._last_heartbeat = now
            self._heartbeat()
        self._flush_latest()
        with self._lock:
            batch, self._queue = self._queue[:BATCH_SIZE * 5], self._queue[BATCH_SIZE * 5:]
        for chunk in (batch[i:i + BATCH_SIZE] for i in range(0, len(batch), BATCH_SIZE)):
            if self._send(chunk) == RETRY:
                self._to_outbox(chunk)
        with self._lock:
            alerts, self._alerts = self._alerts, []
        for body in alerts:                         # 하나씩 따로 (`alert`). 못 보내면 버린다 — outbox 에 넣지 않는다
            if self._send([body]) == RETRY:
                log.warning("알림을 서버에 보내지 못했다(버림): %s", body.get("kind"))
        # outbox 는 5분마다, 그리고 start()/finish() 가 요청했을 때만 — 죽은 서버에 2초마다 5초씩 매달리지 않는다
        if self._outbox_due or now - self._last_outbox >= OUTBOX_RETRY_SECONDS:
            self._outbox_due = False
            self._last_outbox = now
            self._resend_outbox()
        with self._lock:
            empty = not self._queue and not self._latest and not self._alerts
        if empty:
            self._drained.set()

    def _wait_drained(self, seconds: float) -> None:
        self._wake.set()
        self._drained.wait(seconds)

    # ---------------------------------------------------------------- 전송
    def _send(self, events: list[dict]) -> str:
        payload = json.dumps({"device_key": self.device_key, "machine": self.machine, "events": events},
                             ensure_ascii=False).encode("utf-8")
        try:
            status, text = self._post(payload)
        except Exception as exc:                            # noqa: BLE001 — 네트워크·타임아웃
            log.warning("서버로 보내지 못했다(나중에 다시): %s", type(exc).__name__)
            return RETRY
        if 200 <= status < 300:
            self.sent += 1
            return OK
        if status in (400, 401, 429):
            self.failed += 1
            log.warning("서버가 받지 않았다(%s, 버림): %s", status, text[:120])
            if status == 401:
                # 빌드가 폐기됐거나 다른 PC 에 묶인 것. 실행 창이 이 표식을 보고 실행을 잠근다 (09-28)
                self.unauthorized = True
                user_log().warning("이 PC 에서 쓸 수 없는 프로그램이라 진행 상황을 서버에 보내지 못했습니다.")
            return DROP
        log.warning("서버 오류 %s(나중에 다시): %s", status, text[:120])
        return RETRY

    def _http_post(self, payload: bytes) -> tuple[int, str]:
        return _http_post(self.url + INGEST, self.anon_key, payload)

    # --------------------------------------------------------------- outbox
    def _to_outbox(self, events: list[dict]) -> None:
        try:
            self.outbox.parent.mkdir(parents=True, exist_ok=True)
            with self.outbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.time(), "events": events}, ensure_ascii=False) + "\n")
            lines = self.outbox.read_text(encoding="utf-8").splitlines()
            if len(lines) > OUTBOX_KEEP_LINES:
                self.outbox.write_text("\n".join(lines[-OUTBOX_KEEP_LINES:]) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("outbox 에 쓰지 못했다: %s", exc)

    def _resend_outbox(self) -> None:
        if not self.outbox.is_file():
            return
        try:
            lines = [l for l in self.outbox.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return
        keep: list[str] = []
        cutoff = time.time() - OUTBOX_KEEP_SECONDS
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if float(item.get("at", 0)) < cutoff:
                continue
            if self._send(item.get("events") or []) == RETRY:
                keep.append(line)
        try:
            if keep:
                self.outbox.write_text("\n".join(keep) + "\n", encoding="utf-8")
            else:
                self.outbox.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("outbox 를 정리하지 못했다: %s", exc)

    # ------------------------------------------------------------ inflight
    def _write_inflight(self) -> None:
        try:
            self.inflight.parent.mkdir(parents=True, exist_ok=True)
            self.inflight.write_text(json.dumps({"run_id": self._run_id, "started_at": time.time()}),
                                     encoding="utf-8")
        except OSError as exc:
            log.warning("실행 표식을 쓰지 못했다: %s", exc)

    def _clear_inflight(self) -> None:
        try:
            self.inflight.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("실행 표식을 지우지 못했다: %s", exc)

    def _recover_inflight(self) -> None:
        """지난 실행이 끝을 못 알렸다 — 그 실행에 `lost` 를 먼저 보낸다."""
        if not self.inflight.is_file():
            return
        try:
            item = json.loads(self.inflight.read_text(encoding="utf-8"))
            old = str(item.get("run_id") or "")
        except (OSError, ValueError):
            old = ""
        self._clear_inflight()
        if old:
            self._put({"type": "run_end", "run_id": old, "seq": 999999, "state": "lost",
                       "summary": LOST_SUMMARY, "attention": 0}, with_run=False)
            log.warning("지난 실행이 끝을 알리지 못했다 — 서버에 '끊김' 으로 남긴다 (%s)", old[:8])

    # ---------------------------------------------------------------- 정리
    def _detach(self) -> None:
        if self._handler is not None:
            user_log().removeHandler(self._handler)
            self._handler = None
        hooks, self._hooks = self._hooks, None
        if hooks is not None:
            if self._orig_on_step is not None or hooks.on_step == self._on_step:
                hooks.on_step = self._orig_on_step
            if self._orig_attend is not None:
                hooks.attend = self._orig_attend
            if self._orig_alert is not None:
                hooks.alert = self._orig_alert
        self._orig_on_step = self._orig_attend = self._orig_alert = None
