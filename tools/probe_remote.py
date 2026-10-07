r"""[조사 도구 - 읽기 전용] 원격 설정·명령·자동 켜기 확인 (09-28).

실서버에 닿지 않는다 (가짜 응답, `probe_guard.offline`). 시스템을 바꾸지 않는다 — 작업 스케줄러·레지스트리
등록은 부르지 않고 등록할 XML 만 본다. 설정 파일은 임시 파일로 돌린다.

    .venv\Scripts\python.exe -m tools.probe_remote
"""
from __future__ import annotations

import json
import queue
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from config import settings as config_settings  # noqa: E402
from config.settings import DEFAULTS, SECRET_KEYS, SETTINGS  # noqa: E402
from orchestrator import remote  # noqa: E402
from utils import autostart, instance  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


class FakeServer:
    """`_http_post` 자리. 받은 요청을 적어 두고 정해 둔 응답을 차례로 준다."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent: list[dict] = []

    def __call__(self, url, anon, payload, limit=1000):
        self.sent.append(json.loads(payload))
        reply = self.replies.pop(0) if self.replies else (200, {"version": 0, "commands": []})
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return status, json.dumps(body, ensure_ascii=False)


def _remote(server, locked=None) -> remote.Remote:
    return remote.Remote("https://x", "anon", "bld_x", "machine-guid", locked_modules=locked, post=server)


def check_keys() -> list[bool]:
    log.info("▶ 원격 키 목록 — 서버와 같고, 비밀이 없다")
    sql = (ROOT / "server" / "schema.sql").read_text(encoding="utf-8")
    body = sql.split("function private.clean_settings", 1)[1].split("$$;", 1)[0]
    server_keys = set(re.findall(r"when '(\w+)' then", body))
    return [check("★ `REMOTE_KEYS` 가 서버 `clean_settings` 와 같다", server_keys == set(remote.REMOTE_KEYS),
                  f"서버만 {sorted(server_keys - set(remote.REMOTE_KEYS))} / PC 만 "
                  f"{sorted(set(remote.REMOTE_KEYS) - server_keys)}"),
            check("★ 비밀번호 키가 없다", not set(remote.REMOTE_KEYS) & set(SECRET_KEYS)
                  and not any("password" in key for key in remote.REMOTE_KEYS)),
            check("PC 마다 다른 경로가 없다",
                  not {"target_exe", "target_work_dir", "download_base_dir", "excel_dir"} & set(remote.REMOTE_KEYS)),
            check("전부 설정 항목이다", set(remote.REMOTE_KEYS) <= set(DEFAULTS))]


def check_values() -> list[bool]:
    log.info("▶ 실을 값 — 순서 맞추기·서버가 안 받을 값 거르기 (09-28 검토)")
    picked = remote.sendable({"collect_sources": ["site", "excel"], "logistics_mode": "", "auto_run_interval_minutes": 5,
                              "hold_exclude_codes": ["x" * 51], "delivery_box": "박스B", "login_password": "x"})
    return [check("★ 서버가 안 받을 값·원격 키가 아닌 값(비밀번호)은 PC 에서 거른다 (답째 버려지지 않게)",
                  picked == {"collect_sources": ["excel", "site"], "delivery_box": "박스B"}, str(picked)),
            check("수집방식은 순서를 맞춘다 (웹·PC 순서가 달라 매번 '바뀜' 이었다)",
                  remote.normalize("collect_sources", ["site", "excel"]) == ["excel", "site"]
                  and remote.normalize("run_modules", ["logistics", "mail"]) == ["mail", "logistics"]),
            check("★ 서버 설정 판·판 기록 파일·올리기가 없다 — 설정은 PC 에만 (10-07)",
                  not hasattr(remote, "STATE_PATH") and not hasattr(remote.Remote, "push")
                  and not hasattr(remote.Remote, "applied"))]


def _drain(r) -> list:
    events = []
    while not r.inbox.empty():
        events.append(r.inbox.get_nowait())
    return events


def check_poll() -> list[bool]:
    log.info("▶ poll — 보내는 것·받는 것 (설정은 서버에 두지 않는다, 10-07)")
    out = []
    server = FakeServer((200, {"commands": [
        {"id": 7, "kind": "start", "modules": ["orders"]},
        {"id": 8, "kind": "shutdown", "modules": []},
        {"id": 9, "kind": "read_settings", "modules": []},
        {"id": 10, "kind": "settings", "modules": [], "settings": {"delivery_box": "박스B", "login_password": "새어나감"}}]}))
    r = _remote(server)
    r.poll_once()
    sent = server.sent[0]
    out.append(check("★ 요청에는 빌드 ID·PC 뿐 — 설정을 싣지 않는다",
                     sent == {"device_key": "bld_x", "machine": "machine-guid"}, str(sent)))
    events = _drain(r)
    out.append(check("★ 웹이 바꾼 값은 원격 키만 넘긴다 (서버가 비밀을 보내도 버린다)",
                     (remote.SETTINGS_EVENT, 10, {"delivery_box": "박스B"}) in events, str(events)))
    out.append(check("설정 보기 요청을 넘긴다", (remote.READ_EVENT, 9) in events, str(events)))
    out.append(check("모르는 명령은 버리고 아는 것만 넘긴다",
                     [e for e in events if e[0] == remote.COMMAND_EVENT] == [(remote.COMMAND_EVENT, 7, "start", ["orders"])],
                     str(events)))

    r.done(7, "시작함")
    r.reply_settings(9, {"delivery_box": "박스A", "login_password": "x", "target_exe": "C:/x.exe", "logistics_mode": ""})
    r.poll_once()
    out.append(check("★ 설정 답에는 원격 키만 — 비밀번호·경로·빈 값이 실리지 않는다",
                     server.sent[1].get("results") == [
                         {"id": 7, "result": "시작함"},
                         {"id": 9, "result": "보냄", "settings": {"delivery_box": "박스A"}, "locked_modules": None}],
                     str(server.sent[1])))
    r.poll_once()
    out.append(check("보낸 결과는 다시 보내지 않는다", "results" not in server.sent[2]))

    r.done(12, "끝")
    server.replies = [OSError("연결 끊김")]
    r.poll_once()
    r.poll_once()
    out.append(check("네트워크가 끊겨도 결과는 남았다가 다음에 간다",
                     server.sent[-1].get("results") == [{"id": 12, "result": "끝"}]))

    r.done(13, "x")
    server.replies = [(400, {"message": "bad request"})]
    r.poll_once()
    r.poll_once()
    out.append(check("400 이면 그 결과를 또 보내지 않는다 (명령은 계속 받는다)",
                     "results" not in server.sent[-1] and not r.unauthorized))

    server.replies = [(401, {"message": "unauthorized"})]
    r.poll_once()
    out.append(check("★ 401 이면 멈춘다 (다른 PC·폐기·업체 중지)", r.unauthorized))

    locked = _remote(FakeServer(), locked=["logistics"])
    locked.reply_settings(1, {"run_modules": ["logistics"], "delivery_box": "박스B"})
    locked.poll_once()
    reply = locked._post.sent[0]["results"][0]
    out.append(check("기능 고정 빌드 — 설정 답에 고정 기능을 싣는다 (웹이 기능 칸을 잠근다)",
                     reply["locked_modules"] == ["logistics"], str(reply)))
    from utils.wait import WaitTimeout, wait_for

    live = FakeServer()
    running = _remote(live)
    running.start()
    try:
        wait_for(lambda: len(live.sent) >= 1, "첫 poll", timeout=3)
        running.done(11, "끝")
        wait_for(lambda: any(s.get("results") for s in live.sent), "결과 전송", timeout=3)
        sent_fast = True
    except WaitTimeout:
        sent_fast = False
    finally:
        running.close()
    out.append(check("★ 명령 결과는 30초를 기다리지 않고 바로 보낸다 (그 사이 창을 닫아도 남는다)", sent_fast,
                     f"요청 {len(live.sent)}번"))
    out.append(check("설정이 없는 빌드는 원격을 만들지 않는다",
                     remote.from_settings(type("S", (), {"server_url": "", "server_anon_key": "a",
                                                        "server_build_id": "b"})(),
                                          locked_modules=None) is None))
    return out


class FakeRemote:
    """창이 부르는 것만 — 올리기(push)가 없으니 창이 부르면 AttributeError 로 드러난다."""

    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.results: list[tuple[int, str]] = []
        self.replies: list[tuple[int, dict]] = []
        self.unauthorized = False
        self.closed = False

    def done(self, command_id, result):
        self.results.append((command_id, result))

    def reply_settings(self, command_id, values):
        self.replies.append((command_id, dict(values)))

    def close(self):
        self.closed = True


def check_window() -> list[bool]:
    """실행 창 — 웹 설정 적용·실행 중 미루기·명령 넷. 설정 파일은 임시 파일, 예약은 꺼 둔다."""
    import tkinter as tk

    from gui import run_app
    from tools import probe_guard
    from utils import schedule

    log.info("▶ 실행 창 — 웹 설정·명령")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return []
    root.withdraw()
    out: list[bool] = []
    keep_settings = SETTINGS.as_dict()
    keep_path = config_settings.LOCAL_SETTINGS_PATH
    keep_locked = run_app.process.screen_locked
    temp = Path(tempfile.mkdtemp()) / "settings.local.json"
    try:
        config_settings.LOCAL_SETTINGS_PATH = temp
        run_app.process.screen_locked = lambda: False
        with probe_guard.offline():
            window = run_app.RunWindow(root)
            window.locked_modules = None
            off = schedule.from_settings({"auto_run_enabled": False, "auto_run_mode": "daily",
                                          "auto_run_times": [], "auto_run_interval_minutes": 60})
            window.attach_autorun(off)
            fake = window.remote = FakeRemote()

            # 웹은 바꾼 키만 보낸다 (10-07 — 서버에 판이 없다)
            web = {"delivery_box": "박스B", "delivery_company": "웹택배", "logistics_mode": "수동",
                   "hold_exclude_codes": ["111", "222"], "auto_run_enabled": False,
                   "auto_run_times": ["평일 09:00"], "run_modules": ["orders", "logistics"]}
            fake.inbox.put((remote.SETTINGS_EVENT, 5, web))
            window.busy = True
            window._drain_remote()
            out.append(check("★ 실행 중에는 웹 설정을 미루고, 적용 전에는 결과를 알리지 않는다 (웹엔 '처리 중')",
                             window.box_var.get() != "박스B" and window._pending_remote is not None
                             and not fake.results, str(fake.results[-1:])))
            fake.inbox.put((remote.READ_EVENT, 6))
            window._drain_remote()
            out.append(check("실행 중 설정 보기는 끝난 뒤 적용할 웹 값까지 답한다",
                             fake.replies and fake.replies[-1][1].get("delivery_box") == "박스B", str(fake.replies[-1:])))
            window.busy = False
            window._drain_remote()
            saved = json.loads(temp.read_text(encoding="utf-8")) if temp.exists() else {}
            out.append(check("끝나 적용한 뒤에 그 명령의 결과를 알린다",
                             fake.results and fake.results[-1][0] == 5 and fake.results[-1][1].startswith("적용함"),
                             str(fake.results[-1:])))
            out.append(check("★ 끝나면 적용한다 — 화면 칸·설정 파일",
                             window.box_var.get() == "박스B" and window.courier_var.get() == "웹택배"
                             and window.mode_var.get() == "수동" and window.hold_var.get() == "111; 222"
                             and saved.get("delivery_box") == "박스B",
                             f"박스 {window.box_var.get()}, 파일 {saved.get('delivery_box')}"))
            out.append(check("기능 체크·예약 줄도 바뀐다",
                             window._checked_modules() == ["orders", "logistics"]
                             and window.autorun_pane.slots == ["평일 09:00"],
                             f"{window._checked_modules()} / {window.autorun_pane.slots}"))
            fake.inbox.put((remote.SETTINGS_EVENT, 7, {"sales_mode": "선택주문"}))
            window._drain_remote()
            out.append(check("★ 웹에서 바꾼 매출처리 방식이 화면 라디오에 옮겨진다 (안 옮기면 다음 저장이 되돌린다)",
                             window.sales_var.get() == "선택주문" and SETTINGS.sales_mode == "선택주문"
                             and fake.results[-1] == (7, "적용함 — 1개 바뀜"),
                             f"{window.sales_var.get()} / {fake.results[-1:]}"))
            fake.inbox.put((remote.SETTINGS_EVENT, 8, {"sales_mode": "선택주문"}))
            window._drain_remote()
            out.append(check("같은 값이면 바뀐 것이 없다고 답한다", fake.results[-1] == (8, "바뀐 것이 없다"),
                             str(fake.results[-1:])))
            window.locked_modules = ["orders"]
            fake.inbox.put((remote.SETTINGS_EVENT, 9, {"run_modules": ["mail"]}))
            window._drain_remote()
            out.append(check("기능 고정 빌드는 웹의 기능 바꾸기를 버린다 (서버는 빌드 구성을 모른다)",
                             "mail" not in (SETTINGS.run_modules or []) and fake.results[-1] == (9, "바뀐 것이 없다"),
                             str(fake.results[-1:])))
            window.locked_modules = None
            fake.inbox.put((remote.READ_EVENT, 10))
            window._drain_remote()
            reply = fake.replies[-1]
            out.append(check("★ 설정 보기 — 지금 값을 원격 키만 답한다 (비밀번호·경로 없음)",
                             reply[0] == 10 and reply[1].get("sales_mode") == "선택주문"
                             and set(reply[1]) == set(remote.REMOTE_KEYS), str(sorted(reply[1]))))
            SETTINGS.sales_mode = "전체"
            window.sales_var.set("전체")

            # 바로 저장 (09-29) — 사람이 친 것(키·체크·고르기)만 저장을 부른다. 코드가 칸을 바꾼 것은 아니다
            out.append(check("웹 설정을 칸에 옮겨도 저장을 예약하지 않는다", window._save_job is None))
            bound = str(window.courier_entry.bind("<KeyRelease>"))
            window.courier_var.set("손택배")
            window._schedule_save()
            scheduled = window._save_job is not None
            window._cancel_save()
            window._autosave()                                  # 0.8초 기다리는 대신 곧바로
            saved = json.loads(temp.read_text(encoding="utf-8"))
            out.append(check("★ 설정 칸에 치면 바로 저장한다 — PC 설정 파일에만, 서버에는 올리지 않는다 (09-29 / 10-07)",
                             "_schedule_save" in bound and scheduled
                             and saved.get("delivery_company") == "손택배"
                             and "저장했습니다" in window.saved_var.get(),
                             f"{saved.get('delivery_company')} / {window.saved_var.get()}"))
            window.busy = True
            window._schedule_save()
            out.append(check("실행 중에는 저장하지 않는다 (칸이 잠겨 있고 [실행] 이 이미 저장했다)",
                             window._save_job is None))
            window.busy = False
            keep_pw = SETTINGS.login_password
            window.pw_var.set("")                               # 지우고 다시 치는 중
            window.courier_var.set("")
            window._autosave()
            saved = json.loads(temp.read_text(encoding="utf-8"))
            out.append(check("★ 빈 비밀번호·택배사는 저장하지 않는다 — 구운 값을 덮지 않는다 (09-29 검토)",
                             saved.get("login_password", "x") != "" and saved.get("delivery_company") == "손택배"
                             and SETTINGS.login_password == keep_pw and "빈 칸" in window.saved_var.get(),
                             window.saved_var.get()))
            window.pw_var.set(keep_pw or "")
            window.courier_var.set("손택배")
            calls, real_save = [], run_app.save_local
            run_app.save_local = lambda **kw: (calls.append(kw), real_save(**kw))[1]
            try:
                SETTINGS.collect_sources = ["excel", "site"]         # 구운 값 순서. 화면은 자동·엑셀 순
                window.site_source_var.set(True)
                window.excel_source_var.set(True)
                window._autosave()
            finally:
                run_app.save_local = real_save
            out.append(check("수집방식 순서만 다르면 저장하지 않는다 (09-29 실기 — 다른 칸을 쳤는데 같이 쓰였다)",
                             not any("collect_sources" in kw for kw in calls), str(calls)))
            SETTINGS.sms_source, SETTINGS.adb_connection = "auto", "wireless"
            window.sms_var.set(run_app._sms_label("auto", "wireless"))
            values = window._saved_values("", "", "", "")
            out.append(check("웹의 자동+무선을 저장하며 usb 로 바꾸지 않는다",
                             (values["sms_source"], values["adb_connection"]) == ("auto", "wireless"),
                             f"{values['sms_source']}/{values['adb_connection']}"))

            def command(kind, modules=()):
                fake.inbox.put((remote.COMMAND_EVENT, len(fake.results) + 1, kind, list(modules)))
                window._drain_remote()
                return fake.results[-1][1]

            out.append(check("중단 — 돌고 있지 않으면 할 일이 없다", "실행 중이 아니" in command(remote.STOP)))
            out.append(check("★ 일시정지·계속하기 (예약)",
                             command(remote.PAUSE).startswith("일시정지") and window.autorunner.paused
                             and command(remote.RESUME).startswith("계속하기") and not window.autorunner.paused))

            started: list[dict] = []

            def fake_on_run(start_step=""):
                started.append({"modules": window._modules(), "trigger": window._trigger(),
                                "unattended": window.unattended})
                window.busy = True

            window.on_run = fake_on_run
            finished: list = []
            window.autorunner.finished = lambda state, summary: finished.append(state)
            result = command(remote.START, ["logistics", "mail"])
            out.append(check("★ 실행 — 받은 기능으로, 무인으로, '원격 실행' 으로 시작한다",
                             result.startswith("시작함") and started
                             and started[0] == {"modules": ["mail", "logistics"], "trigger": "원격 실행",
                                                "unattended": True}, f"{result} / {started}"))
            out.append(check("실행 중에 또 [실행] 이 오면 시작하지 않는다", "이미 실행 중" in command(remote.START)))
            window.token = type("T", (), {"cancelled": False, "reason": ""})()
            window.token.cancel = lambda why: setattr(window.token, "cancelled", True)
            out.append(check("★ 실행 중 [RPA 종료] 는 중단 요청을 넣는다",
                             command(remote.STOP).startswith("RPA 종료 요청") and window.token.cancelled))
            window.busy = False
            window._end_autorun_cycle("failed", "끝")
            out.append(check("★ 원격 회차가 끝나도 예약 시계에 알리지 않는다 (없는 회차를 닫지 않는다)",
                             finished == [] and not window.remote_run and not window.unattended
                             and window.scheduled_modules is None))
            window.locked_modules = ["orders"]
            out.append(check("★ 요청한 기능이 이 빌드에 없으면 거절한다 (창의 체크로 대신 돌지 않는다)",
                             "빌드에 없어" in command(remote.START, ["mail"]) and not started[1:]))
            window.locked_modules = None

            def boom(kind, wanted):
                raise RuntimeError("시험 오류")

            keep_run = window._run_command
            window._run_command = boom
            out.append(check("명령 처리가 터져도 결과를 웹에 남긴다", "오류" in command(remote.STOP)))
            window._run_command = keep_run
            keep_tick = window.on_autorun_tick
            window.on_autorun_tick = lambda: boom("", [])
            window.detach_autorun()
            window._tick_autorun()
            out.append(check("★ 1초 시계는 예외가 나도 다음 박자를 잡는다 (예약·원격이 멈추지 않는다)",
                             window._autorun_job is not None))
            window.on_autorun_tick = keep_tick
            window.detach_autorun()
            window.verify_state = "blocked"
            out.append(check("이 PC 에서 쓸 수 없으면 시작하지 않는다", "쓸 수 없어" in command(remote.START)))
            window.verify_state = ""
            run_app.process.screen_locked = lambda: True
            out.append(check("화면이 잠겼으면 시작하지 않는다", "잠겨" in command(remote.START)))
            fake.unauthorized = True
            scheduled: list = []
            keep_after = window.root.after
            window.root.after = lambda ms, fn=None, *args: scheduled.append((ms, fn)) or "job"
            try:
                window._lock_if_unauthorized()
                out.append(check("원격이 401 을 받으면 [실행] 을 잠근다", window.verify_state == "blocked"))
                out.append(check("★ 잠근 뒤에도 10분마다 서버 확인을 다시 한다 (업체 중지를 풀면 껐다 켜지 않아도 돈다)",
                                 (run_app.VERIFY_BLOCKED_MS, window._verify_build) in scheduled, str(scheduled)))
                reporter = type("R", (), {"unauthorized": True, "close": lambda self: None})()
                window._reporter_obj = reporter
                window._verify_result = (run_app.telemetry.VERIFY_OK, "")
                window._poll_verify()
                out.append(check("★ 다시 확인을 통과하면 잠금을 풀고 원격을 새로 만든다",
                                 window.verify_state == "" and not reporter.unauthorized and fake.closed
                                 and window.remote is not fake, f"{window.verify_state!r} / 닫힘 {fake.closed}"))
            finally:
                window.root.after = keep_after
    finally:
        run_app.process.screen_locked = keep_locked
        config_settings.LOCAL_SETTINGS_PATH = keep_path
        for key, value in keep_settings.items():
            setattr(SETTINGS, key, value)
        root.destroy()
    return out


def check_settings_file() -> list[bool]:
    log.info("▶ 설정 파일 — BOM 읽기·임시 파일 교체 저장 (10-07)")
    folder = Path(tempfile.mkdtemp())
    path = folder / "settings.local.json"
    path.write_text(json.dumps({"delivery_box": "박스A"}, ensure_ascii=False), encoding="utf-8-sig")
    keep_path = config_settings.LOCAL_SETTINGS_PATH
    keep_values = {k: getattr(SETTINGS, k, None) for k in ("delivery_company", "delivery_box")}
    keep_replace = config_settings.os.replace
    out = []
    try:
        config_settings.LOCAL_SETTINGS_PATH = path
        out.append(check("BOM 붙은 파일을 읽는다", config_settings.read_json_file(path) == {"delivery_box": "박스A"}))
        config_settings.save_local(delivery_company="택배사A")
        saved = config_settings.read_json_file(path)
        out.append(check("BOM 파일에 저장해도 다른 값이 남고 백업을 만들지 않는다",
                         saved == {"delivery_box": "박스A", "delivery_company": "택배사A"}
                         and not list(folder.glob("*.bak")), str(saved)))
        out.append(check("저장 뒤 임시 파일이 없고 BOM 없이 쓴다",
                         not list(folder.glob("*.tmp")) and not path.read_bytes().startswith(b"\xef\xbb\xbf")))

        def _blocked(src, dst):
            raise PermissionError("가짜 — 다른 프로그램이 쥐고 있다")

        config_settings.os.replace = _blocked
        before = path.read_bytes()
        try:
            config_settings.save_local(delivery_company="택배사B")
            raised = False
        except OSError:
            raised = True
        out.append(check("교체가 막히면 오류를 올리고 원본·임시 파일을 남기지 않는다",
                         raised and path.read_bytes() == before and not list(folder.glob("*.tmp"))))
    finally:
        config_settings.os.replace = keep_replace
        config_settings.LOCAL_SETTINGS_PATH = keep_path
        for key, value in keep_values.items():
            setattr(SETTINGS, key, value)
    return out


def check_autostart() -> list[bool]:
    log.info("▶ 자동 켜기 — 등록할 내용만 본다 (등록하지 않는다)")
    # 가짜 경로 — 등록하지 않고 XML 만 본다. `&` 가 든 폴더를 감싸는지 보려고 넣었다
    fake_dir = Path(tempfile.gettempdir())
    exe = fake_dir / "A&B" / "RPA_1.exe"
    xml = autostart.task_xml(exe)
    other = autostart.name_for(fake_dir / "다른폴더" / "RPA_1.exe")
    probe_mutex = Path(tempfile.gettempdir()) / "probe_remote_mutex.exe"
    first = instance.acquire(probe_mutex)
    second = instance.acquire(probe_mutex)
    return [check("★ 72시간 강제 종료·배터리 조건을 끈다",
                  "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
                  and "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
                  and "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml),
            check("5분마다, 떠 있으면 새로 띄우지 않는다",
                  "<Interval>PT5M</Interval>" in xml and "<MultipleInstancesPolicy>IgnoreNew" in xml),
            check("최소화·작업 표시 인자, 경로의 & 를 감싼다 (09-29 --task)",
                  "<Arguments>--background --task</Arguments>" in xml and "A&amp;B" in xml),
            check("이름은 exe 경로마다 다르고 늘 같다",
                  autostart.name_for(exe) == autostart.name_for(exe) and autostart.name_for(exe) != other),
            check("★ 한 벌만 — 같은 경로로 두 번째는 막힌다", first is True and second is False,
                  f"{first}/{second}")]


def main() -> int:
    setup_logging()
    from utils import autorun

    keep_paused = autorun.STATE_PATH
    autorun.STATE_PATH = Path(tempfile.mkdtemp()) / "autorun_state.json"    # 웹 [일시정지]·[계속하기] 가 남긴다 (10-01)
    try:
        results = (check_keys() + check_poll() + check_values() + check_window() + check_settings_file()
                   + check_autostart())
    finally:
        autorun.STATE_PATH = keep_paused
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
