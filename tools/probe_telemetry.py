r"""[확인 도구 - 읽기 전용] 서버 보고(`orchestrator/telemetry.py`) — 가짜 서버로 이벤트 형식·조절·outbox·비밀 값을 본다.

    .venv\Scripts\python.exe -m tools.probe_telemetry

실서버에 붙지 않는다. 표준 라이브러리 `http.server` 를 127.0.0.1 에 띄워 받은 본문을 검사한다.
outbox·실행 표식은 임시 파일(`logs/_probe_telemetry_*.json*`)에 쓰고 끝에 지운다. 대상 프로그램을 건드리지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from config.settings import LOG_DIR, SECRET_KEYS  # noqa: E402
from orchestrator import steps, telemetry  # noqa: E402
from orchestrator.common import Hooks  # noqa: E402
from orchestrator.steps import Step  # noqa: E402
from utils.logger import get_logger, setup_logging, user_log  # noqa: E402

log = get_logger(__name__)
OUTBOX = LOG_DIR / "_probe_telemetry_outbox.jsonl"
INFLIGHT = LOG_DIR / "_probe_telemetry_inflight.json"
PLAN = [Step("a", "메일", steps.SAFE), Step("b", "매출처리", steps.IRREVERSIBLE)]
# 서버로 가면 안 되는 것. 본문 전체에서 찾는다
FORBIDDEN = (re.compile(r"[A-Za-z]:\\"), re.compile(r"dpapi:"), re.compile(r"auto_id"), re.compile(r"\bpid\b"),
             re.compile(r"01[016789]-?\d{3,4}-?\d{4}"), re.compile(r"[\w.]+@[\w.]+\.\w+"), re.compile(r"\"report\""))


def check(name: str, ok: object, detail: str = "") -> bool:
    ok = bool(ok)
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


class FakeServer:
    """모드: ok / fail(500) / auth(401) / slow(응답 전 3초)."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.received: list[dict] = []
        self.bodies: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:          # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                server.bodies.append(raw.decode("utf-8"))
                body = json.loads(raw)
                if server.mode == "slow":
                    threading.Event().wait(3)   # 가짜 지연 — 보고기가 이 사이에도 흐름을 세우지 않는지 본다
                if server.mode == "fail":
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"boom")
                    return
                if server.mode == "auth":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"message":"unauthorized"}')
                    return
                server.received.append(body)
                out = json.dumps({"accepted": len(body["events"])}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *_args) -> None:
                return

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def events(self) -> list[dict]:
        return [e for body in self.received for e in body["events"]]

    def wait(self, n: int, seconds: float = 6.0) -> bool:
        deadline = time.monotonic() + seconds
        gate = threading.Event()
        for _ in range(int(seconds / 0.05) + 1):
            if len(self.events()) >= n or time.monotonic() > deadline:
                break
            gate.wait(0.05)
        return len(self.events()) >= n

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def new_reporter(server: FakeServer) -> telemetry.Reporter:
    return telemetry.Reporter(server.url, "anon-key", "bld_" + "k" * 43, outbox=OUTBOX, inflight=INFLIGHT)


def check_flow(server: FakeServer) -> list[bool]:
    log.info("▶ 실행 하나 — 형식·조절·로그·확인할 것·끝")
    out = []
    drawn: list = []
    hooks = Hooks(on_step=drawn.append)
    reporter = new_reporter(server)
    telemetry.BATCH_SECONDS = 0.3
    run_id = reporter.start(hooks, trigger="직접 실행", modules=["메일 엑셀 받기"])
    out.append(check("run_id 가 나오고 실행 표식 파일이 생긴다", bool(run_id) and INFLIGHT.is_file()))
    hooks.begin(PLAN)
    with hooks.stage(PLAN[0]) as run:
        for i in range(6):
            hooks.count(PLAN[0], total=6, done=i)       # 같은 상태 6번 — 마지막 것만 가야 한다
        user_log().info("메일 2건을 받았습니다")
        logging.getLogger("automation.x").info("auto_id=btnX pid=123 C:\\secret")    # 내부 로그 — 안 간다
        hooks.attend("확인할 것 하나")
        run.detail = "엑셀 2개"
    try:
        with hooks.stage(PLAN[1]):
            raise RuntimeError("C:\\Users\\x\\y.xlsx 못 찾음 pid=9")
    except RuntimeError:
        pass  # 실패 단계를 만들려고 일부러 낸 예외
    out.append(check("원래 on_step 도 그대로 불린다", len(drawn) >= 4, str(len(drawn))))
    out.append(check("attend 가 원래 목록에도 남는다", hooks.attention == ["확인할 것 하나"]))
    entry = {"state": "failed", "summary": "실패 — [매출처리] 단계에서 문제", "elapsed": 3.2, "attention": 1,
             "modules": ["메일 엑셀 받기"], "trigger": "직접 실행", "report": "C:\\x\\report.html", "steps": []}
    started = time.monotonic()
    reporter.finish(entry)
    out.append(check("finish 가 5초 안에 돌아온다", time.monotonic() - started < 5.5))
    out.append(check("실행 표식이 지워진다", not INFLIGHT.is_file()))
    server.wait(8)
    events = server.events()
    types = [e["type"] for e in events]
    out.append(check("run_start 가 첫 이벤트, run_end 가 마지막", types[0] == "run_start" and types[-1] == "run_end", str(types)))
    a_steps = [e for e in events if e["type"] == "step" and e["step_id"] == "a"]
    a_running = [e for e in a_steps if e["state"] == "running"]
    out.append(check("같은 상태의 건수 갱신은 묶어서 — running 사진 2장 이하(시작 + 마지막)",
                     1 <= len(a_running) <= 2 and a_running[-1]["done_items"] == 5, str([e["done_items"] for e in a_running])))
    out.append(check("상태가 바뀐 사진(pending→running→done)은 다 간다",
                     [e["state"] for e in a_steps][:1] == ["pending"] and "done" in [e["state"] for e in a_steps]))
    b_fail = next((e for e in events if e["type"] == "step" and e["step_id"] == "b" and e["state"] == "failed"), None)
    out.append(check("실패 단계에 사람 말 error 가 실린다", b_fail is not None and "매출처리" in b_fail["error"]
                     and "pid" not in b_fail["error"], str(b_fail and b_fail["error"])))
    logs = [e for e in events if e["type"] == "log"]
    mine = [e for e in logs if e["text"] == "메일 2건을 받았습니다"]
    out.append(check("user 로그가 log 이벤트로 도는 단계 id 와 함께 — 내부 로거 줄은 없다",
                     len(mine) == 1 and mine[0]["step_id"] == "a" and mine[0]["level"] == "info"
                     and not any("auto_id" in e["text"] for e in logs), str([e["text"][:20] for e in logs])))
    out.append(check("attend 이벤트", any(e["type"] == "attend" and e["text"] == "확인할 것 하나" for e in events)))
    end = events[-1]
    out.append(check("run_end 에 state·summary·elapsed 가 있고 report·steps 는 없다",
                     end["state"] == "failed" and end["elapsed"] == 3.2 and "report" not in end and "steps" not in end))
    out.append(check("seq 가 1부터 빠짐없이 오른다", [e["seq"] for e in events] == list(range(1, len(events) + 1))))
    out.append(check("모든 이벤트에 같은 run_id·at", all(e["run_id"] == run_id and e["at"] > 0 for e in events)))
    out.append(check("본문 device_key 자리에 빌드 ID 가 들어가고 헤더용 anon 키와 다르다",
                     all(json.loads(b)["device_key"].startswith("bld_") for b in server.bodies)))
    hit = [(p.pattern, b) for b in server.bodies for p in FORBIDDEN if p.search(b)]
    out.append(check("본문에 경로·pid·auto_id·dpapi·전화·이메일·report 없음", not hit, str(hit[:1])[:120]))
    out.append(check("finish 뒤 on_step·attend 가 원래대로", hooks.on_step == drawn.append
                     and not any(isinstance(h, telemetry._UserLogHandler) for h in user_log().handlers)))
    user_log().info("뒤늦은 줄")
    threading.Event().wait(0.5)
    out.append(check("finish 뒤 user 로그는 더 안 간다", not any(e.get("text") == "뒤늦은 줄" for e in server.events())))
    return out


def check_dry_and_none(server: FakeServer) -> list[bool]:
    log.info("▶ dry-run·설정 없음")
    out = []

    class S:
        server_url = server.url
        server_anon_key = "anon"
        server_build_id = ""

    out.append(check("빌드 ID 가 비면 보고기를 만들지 않는다", telemetry.from_settings(S()) is None))
    S.server_build_id = "bld_x"
    S.server_url = server.url + "/"
    made = telemetry.from_settings(S())
    out.append(check("셋 다 있으면 만든다 (끝 / 는 뗀다)", made is not None and made.url == server.url))
    before = len(server.bodies)
    hooks = Hooks()
    reporter = new_reporter(server)
    out.append(check("dry-run 은 아무것도 안 한다", reporter.start(hooks, trigger="콘솔 실행", dry_run=True) == ""
                     and hooks.on_step is None))
    reporter.finish({"state": "done"})
    server.wait(1, 0.5)
    out.append(check("dry-run 은 보내지 않는다", len(server.bodies) == before))
    return out


def check_outbox(server: FakeServer) -> list[bool]:
    log.info("▶ 서버 장애 — outbox / 401 은 버림 / 다음 start 에서 재전송 / 끊김 표식")
    out = []
    OUTBOX.unlink(missing_ok=True)
    server.mode = "fail"
    hooks = Hooks()
    reporter = new_reporter(server)
    reporter.start(hooks, trigger="직접 실행")
    hooks.begin(PLAN)
    started = time.monotonic()
    with hooks.stage(PLAN[0]):
        pass                                            # 단계 하나
    out.append(check("서버가 죽어도 단계 통지가 바로 돌아온다", time.monotonic() - started < 0.5))
    reporter.finish({"state": "done", "summary": "완료"})
    out.append(check("500 이면 outbox 에 남는다", OUTBOX.is_file() and OUTBOX.read_text(encoding="utf-8").count("\n") >= 1))
    reporter.close()

    server.mode = "auth"
    hooks2 = Hooks()
    reporter2 = new_reporter(server)
    reporter2.start(hooks2, trigger="직접 실행")
    reporter2.finish({"state": "done", "summary": "완료"})
    server.wait(1, 0.5)
    lines_after_401 = OUTBOX.read_text(encoding="utf-8").splitlines() if OUTBOX.is_file() else []
    out.append(check("401 은 다시 보내지 않는다 — outbox 가 비거나 줄지 않는다", len(lines_after_401) <= 1
                     and reporter2.failed >= 1, str(len(lines_after_401))))
    reporter2.close()

    # 끝을 못 알린 실행 (표식만 남긴다)
    INFLIGHT.write_text(json.dumps({"run_id": "22222222-2222-2222-2222-222222222222", "started_at": 1}), encoding="utf-8")
    OUTBOX.write_text(json.dumps({"at": time.time(), "events": [{"type": "log", "run_id": "2222", "seq": 1, "at": 1,
                                                                  "level": "info", "step_id": "", "text": "옛날 줄"}]},
                                 ensure_ascii=False) + "\n"
                      + json.dumps({"at": time.time() - 90000, "events": [{"type": "log", "run_id": "3333", "seq": 1,
                                                                            "at": 1, "text": "하루 지난 줄"}]}) + "\n",
                      encoding="utf-8")
    server.mode = "ok"
    server.received.clear()
    hooks3 = Hooks()
    reporter3 = new_reporter(server)
    reporter3.start(hooks3, trigger="직접 실행")
    reporter3.finish({"state": "done", "summary": "완료"})
    server.wait(3)
    events = server.events()
    lost = [e for e in events if e["type"] == "run_end" and e.get("state") == "lost"]
    out.append(check("지난 실행에 run_end(lost) 를 먼저 보낸다", len(lost) == 1 and lost[0]["run_id"].startswith("2222")
                     and events.index(lost[0]) < next(i for i, e in enumerate(events) if e["type"] == "run_start")))
    out.append(check("outbox 의 줄을 다시 보내고 하루 지난 줄은 버린다",
                     any(e.get("text") == "옛날 줄" for e in events) and not any(e.get("text") == "하루 지난 줄" for e in events)))
    out.append(check("다 보내면 outbox 가 지워진다", not OUTBOX.is_file()))
    reporter3.close()
    return out


def check_idle_skip_heartbeat(server: FakeServer) -> list[bool]:
    log.info("▶ idle · 건너뜀 · 심박")
    out = []
    server.received.clear()
    reporter = new_reporter(server)
    reporter.idle(next_run_at=1_800_000_000.0, auto_run_enabled=True, paused=False)
    reporter.skipped(1_700_000_000.0, "화면이 잠겨 있다 (잠긴 화면에서는 자동화가 동작하지 않는다)")
    server.wait(3)
    events = server.events()
    idle = next((e for e in events if e["type"] == "idle"), None)
    out.append(check("idle — run_id 없이 다음 예약·켜짐·앱 버전", idle is not None and "run_id" not in idle
                     and idle["next_run_at"] == 1_800_000_000.0 and idle["auto_run_enabled"] and idle["app_version"]))
    skip = [e for e in events if e.get("run_id") and e["type"] in ("run_start", "run_end")]
    out.append(check("건너뜀 — run_start + run_end(skipped) 한 쌍, 같은 run_id, 예약 시각",
                     len(skip) == 2 and skip[0]["run_id"] == skip[1]["run_id"] and skip[1]["state"] == "skipped"
                     and skip[1]["summary"].startswith("건너뜀 — 화면이 잠겨") and skip[0]["at"] == 1_700_000_000.0))

    server.received.clear()
    keep = telemetry.HEARTBEAT_SECONDS
    telemetry.HEARTBEAT_SECONDS = 0.2
    try:
        hooks = Hooks()
        reporter.start(hooks, trigger="예약 실행")
        server.wait(2, 2.0)
        beat = next((e for e in server.events() if e["type"] == "heartbeat"), None)
        out.append(check("도는 동안 심박 — screen_locked·paused 가 든다",
                         beat is not None and "screen_locked" in beat and "paused" in beat, str(beat)))
        reporter.finish({"state": "cancelled", "summary": "중단됨"})
    finally:
        telemetry.HEARTBEAT_SECONDS = keep
    reporter.close()
    return out


def check_alert(server: FakeServer) -> list[bool]:
    """사람 손이 지금 필요한 일 (10-01) — 따로 한 건씩, outbox 에 안 남긴다, 도는 실행이면 그 run_id."""
    log.info("▶ 알림 (UAC 대기·휴대폰 점검)")
    out = []
    gate = threading.Event()

    def sent_alert(since: int) -> bool:
        for _ in range(80):
            if any('"alert"' in body for body in server.bodies[since:]):
                return True
            gate.wait(0.05)
        return False

    server.mode = "ok"
    server.received.clear()
    reporter = new_reporter(server)
    reporter.alert("모름", "x")
    reporter.alert("phone", "휴대폰이 PC 와 연결돼 있지 않습니다")
    sent_alert(0)
    alone = [body["events"] for body in server.received if any(e["type"] == "alert" for e in body["events"])]
    first = alone[0][0] if alone else {}
    out.append(check("실행 밖 알림 — 따로 한 건, run_id 없음, 모르는 종류는 안 보낸다",
                     len(alone) == 1 and len(alone[0]) == 1 and first.get("kind") == "phone"
                     and "run_id" not in first, str(alone)))

    server.received.clear()
    calls: list[str] = []
    original = lambda kind, text: calls.append(kind)       # noqa: E731
    hooks = Hooks(alert=original)
    run_id = reporter.start(hooks, trigger="예약 실행")
    since = len(server.bodies)
    hooks.alert("uac", "Windows [사용자 계정 컨트롤] 창에서 [예] 를 눌러 주세요.")
    sent_alert(since)
    with_alert = [body["events"] for body in server.received if any(e["type"] == "alert" for e in body["events"])]
    uac = with_alert[0][0] if with_alert else {}
    out.append(check("★ 실행 안 알림 — 원래 alert 도 불리고, 그 실행 run_id 를 달고 따로 간다 (옛 서버 400 이 단계까지 버리지 않게)",
                     calls == ["uac"] and uac.get("kind") == "uac" and uac.get("run_id") == run_id
                     and all(len(events) == 1 for events in with_alert), str(with_alert)))
    reporter.finish({"state": "done", "summary": "완료"})
    out.append(check("finish 뒤 hooks.alert 가 원래대로", hooks.alert is original))

    OUTBOX.unlink(missing_ok=True)
    server.mode = "fail"
    since = len(server.bodies)
    reporter.alert("phone", "x")
    sent_alert(since)
    gate.wait(0.3)
    server.mode = "ok"
    out.append(check("★ 서버가 죽어 있으면 알림은 버린다 — outbox 에 남기지 않는다 (늦은 '지금 눌러 주세요' 는 쓸모없다)",
                     not OUTBOX.is_file() or '"alert"' not in OUTBOX.read_text(encoding="utf-8")))
    reporter.close()

    schema = (Path(__file__).resolve().parent.parent / "server" / "schema.sql").read_text(encoding="utf-8")
    out.append(check("알림 종류·무인 실행 글이 서버(ingest·alerts)와 같다 — 다르면 서버가 400 이거나 메일이 조용히 안 간다",
                     "in ('uac', 'phone')" in schema and "('lost', 'result', 'uac', 'phone')" in schema
                     and set(telemetry.ALERT_KINDS) == {"uac", "phone"}
                     and "('예약 실행', '원격 실행')" in schema))
    return out


def check_slow(server: FakeServer) -> list[bool]:
    log.info("▶ 느린 서버 — 흐름을 세우지 않는다")
    out = []
    server.mode = "slow"
    hooks = Hooks()
    reporter = new_reporter(server)
    reporter.start(hooks, trigger="직접 실행")
    hooks.begin(PLAN)
    worst = 0.0
    for _ in range(5):
        started = time.monotonic()
        with hooks.stage(PLAN[0]):
            hooks.count(PLAN[0], total=3, done=1)
            user_log().info("느린 서버 중 로그")
        worst = max(worst, time.monotonic() - started)
    out.append(check("단계 통지·로그가 서버 지연과 무관하게 50ms 안", worst < 0.05, f"{worst * 1000:.1f}ms"))
    started = time.monotonic()
    reporter.finish({"state": "done"})
    out.append(check("finish 는 5초 상한", time.monotonic() - started < 5.6))
    reporter.close()
    server.mode = "ok"
    return out


def check_settings_and_window() -> list[bool]:
    log.info("▶ 설정 키·굽기 제외·실행 창 칸·StepEvent.error")
    out = []
    from config.fields_erpia import DEFAULTS as ERPIA_DEFAULTS
    from gui import build_app
    from tools import bake_settings

    out.append(check("설정 키 셋", all(k in ERPIA_DEFAULTS for k in ("server_url", "server_anon_key", "server_build_id"))))
    out.append(check("빌드 ID 는 비밀(dpapi)", "server_build_id" in SECRET_KEYS))
    out.append(check("빌드 ID 는 굽는다 (09-28 — exe 안에 들어가야 보고할 수 있다)",
                     "server_build_id" not in bake_settings.SKIP_KEYS))
    out.append(check("빌드 프로그램 화면에는 빌드 ID 칸이 없다 (서버가 발급한다)",
                     "server_build_id" in build_app.SKIP_KEYS and "server_build_id" not in build_app.KINDS))
    out.append(check("빌드 프로그램 칸에 server_url·anon 키·업체 이름·관리자 계정이 있다",
                     all(k in build_app.KINDS for k in ("server_url", "server_anon_key", "build_account_name",
                                                        "build_label", "build_admin_email", "build_admin_password"))))
    out.append(check("관리자 계정은 굽지 않는다 (사용자 exe 에 들어가면 안 된다)",
                     all(k in bake_settings.SKIP_KEYS for k in ("build_account_name", "build_label",
                                                                "build_admin_email", "build_admin_password"))))
    out.append(check("관리자 비밀번호는 비밀(dpapi)", "build_admin_password" in SECRET_KEYS))
    out.append(check("StepEvent 에 error 필드", "error" in steps.StepEvent.__dataclass_fields__))

    # 확인 도구가 실서버를 건드리지 않게 막는 장치 (09-28). 09-23 에 가짜 실행이 대시보드에 올라갔고,
    # 09-28 에 키 이름이 바뀌자 막던 코드가 **조용히 아무것도 안 막는 상태**가 됐다. 그래서 검사로 고정한다.
    from pathlib import Path

    from tools import probe_guard

    out.append(check("probe_guard 가 비우는 키가 실제 설정 키와 같다",
                     all(k in ERPIA_DEFAULTS for k in probe_guard.SERVER_KEYS)
                     and "server_build_id" in probe_guard.SERVER_KEYS))
    tools_dir = Path(__file__).resolve().parent
    windowed = ("probe_modules", "probe_build", "probe_history", "probe_gui")
    missing = [name for name in windowed
               if "probe_guard" not in (tools_dir / f"{name}.py").read_text(encoding="utf-8")]
    out.append(check("실행 창을 만드는 확인 도구는 probe_guard 로 막는다 (창이 뜨면 서버 확인이 나간다)",
                     not missing, f"빠짐 {missing}"))
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return out
    root.withdraw()
    try:
        from gui.run_app import RunWindow

        window = RunWindow(root)
        saved = window._saved_values("x.exe", "c", "u", "p")
        out.append(check("실행 창에 [서버 키] 칸이 없다 (09-28 — 사용자가 넣을 것이 없다)",
                         not hasattr(window, "server_key_entry") and "server_device_key" not in saved))
        out.append(check("빌드 ID 는 창이 저장하지 않는다 (구운 값을 쓴다)",
                         "server_build_id" not in saved))
    finally:
        root.destroy()
    return out


def check_verify(server: FakeServer) -> list[bool]:
    """서버 확인·PC 바인딩 (09-28) — `verify` 왕복 + 실행 창의 잠금.

    `enroll` 은 없앴다. `ingest` 를 **빈 이벤트**로 부르는 것이 곧 등록·확인이다.
    """
    log.info("▶ 서버 확인 — verify 왕복·거부·네트워크·실행 창 잠금")
    from types import SimpleNamespace

    from config.settings import SETTINGS

    build = "bld_" + "b" * 43
    fake = SimpleNamespace(server_url=server.url, server_anon_key="anon", server_build_id=build)
    n_before = len(server.received)
    state, text = telemetry.verify(fake)
    sent = server.received[-1]
    out = [check("verify 왕복 — 빌드 ID·PC 식별값을 빈 이벤트로 보낸다",
                 state == telemetry.VERIFY_OK and not text and sent["device_key"] == build
                 and len(sent["machine"]) >= 8 and sent["events"] == []
                 and len(server.received) == n_before + 1, str(sent)[:80])]
    out.append(check("보낸 본문에 dpapi·경로 없음", not any(p.search(server.bodies[-1]) for p in FORBIDDEN)))

    server.mode = "auth"
    state, text = telemetry.verify(fake)
    out.append(check("401 이면 거부 — 다시 해도 같다",
                     state == telemetry.VERIFY_BLOCKED and "다른 PC" in text, text[:60]))
    server.mode = "ok"
    state, text = telemetry.verify(SimpleNamespace(**{**vars(fake), "server_url": "http://127.0.0.1:9"}))
    out.append(check("네트워크 실패는 거부가 아니다 (나중에 다시)",
                     state == telemetry.VERIFY_LATER, text[:60]))
    state, _ = telemetry.verify(SimpleNamespace(**{**vars(fake), "server_build_id": ""}))
    out.append(check("서버를 쓰지 않는 빌드는 막지 않는다", state == telemetry.VERIFY_OK))

    reporter = telemetry.Reporter(server.url, "anon", build, outbox=OUTBOX, inflight=INFLIGHT, machine="m-1")
    reporter.idle(next_run_at=None, auto_run_enabled=False, paused=False)
    reporter.close()
    out.append(check("ingest 본문에 machine 이 들어간다", server.received and server.received[-1].get("machine") == "m-1"))

    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return out
    root.withdraw()
    from gui import run_app

    keep = {k: getattr(SETTINGS, k, None) for k in ("server_url", "server_anon_key", "server_build_id")}
    try:
        SETTINGS.server_url, SETTINGS.server_anon_key = server.url, "anon"
        SETTINGS.server_build_id = build

        def wait_state(window, seconds: float = 5.0) -> None:
            for _ in range(int(seconds / 0.05)):
                root.update()
                if window.verify_state != "pending":
                    return
                threading.Event().wait(0.05)

        window = run_app.RunWindow(root)
        out.append(check("확인하는 동안 [실행] 이 잠긴다", run_app.VERIFY_KEY in window._missing()))
        wait_state(window)
        out.append(check("확인을 통과하면 [실행] 잠금이 풀린다",
                         window.verify_state == "" and run_app.VERIFY_KEY not in window._missing(),
                         window.status_var.get()[:60]))
        root.update()

        server.mode = "auth"
        window2 = run_app.RunWindow(root)
        wait_state(window2)
        out.append(check("다른 PC 에 묶인 빌드면 거부 — [실행] 잠김 + 이유",
                         window2.verify_state == "blocked" and run_app.VERIFY_KEY in window2._missing()
                         and "다른 PC" in window2.status_var.get(), window2.status_var.get()[:60]))

        # 돌다가 401 을 받으면 실행을 잠근다 (09-28 — 재등록하지 않는다)
        server.mode = "ok"
        window3 = run_app.RunWindow(root)
        wait_state(window3)
        rep = window3._reporter()
        server.mode = "auth"
        rep.idle(next_run_at=None, auto_run_enabled=False, paused=False)
        rep.close()
        out.append(check("401 을 받으면 보고기에 표식", rep.unauthorized))
        window3.on_autorun_tick()
        out.append(check("★ 401 뒤 [실행] 이 잠긴다 (키를 새로 받지 않는다)",
                         window3.verify_state == "blocked" and run_app.VERIFY_KEY in window3._missing(),
                         window3.status_var.get()[:60]))
    finally:
        server.mode = "ok"
        for k, v in keep.items():
            setattr(SETTINGS, k, v)
        root.destroy()
    return out


def main() -> int:
    setup_logging()
    server = FakeServer()
    try:
        results = [*check_flow(server), *check_dry_and_none(server), *check_outbox(server),
                   *check_idle_skip_heartbeat(server), *check_alert(server), *check_slow(server),
                   *check_settings_and_window(),
                   *check_verify(server)]
    finally:
        server.stop()
        OUTBOX.unlink(missing_ok=True)
        INFLIGHT.unlink(missing_ok=True)
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
