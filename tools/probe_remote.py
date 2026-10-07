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
    log.info("▶ 올릴 값 — 순서 맞추기·서버가 안 받을 값 거르기·적용한 판 기록 (09-28 검토)")
    server = FakeServer()
    r = _remote(server)
    r.push({"collect_sources": ["site", "excel"], "logistics_mode": "", "auto_run_interval_minutes": 5,
            "hold_exclude_codes": ["x" * 51], "delivery_box": "박스B"})
    r.poll_once()
    pushed = server.sent[0].get("pushed")
    r.applied(9)
    return [check("★ 서버가 안 받을 값은 PC 에서 거른다 (요청째 400 이 나지 않게)",
                  pushed == {"collect_sources": ["excel", "site"], "delivery_box": "박스B"}, str(pushed)),
            check("수집방식은 순서를 맞춘다 (웹·PC 순서가 달라 매번 '바뀜' 이었다)",
                  remote.normalize("collect_sources", ["site", "excel"]) == ["excel", "site"]
                  and remote.normalize("run_modules", ["logistics", "mail"]) == ["mail", "logistics"]),
            check("★ 적용한 판을 기록하고, 다시 켜면 그 판에서 이어간다",
                  _remote(FakeServer()).version == 9)]


def check_poll() -> list[bool]:
    log.info("▶ poll — 보내는 것·받는 것")
    out = []
    server = FakeServer((200, {"version": 3, "settings": {"delivery_box": "박스B", "login_password": "새어나감"},
                               "commands": [{"id": 7, "kind": "start", "modules": ["orders"]},
                                            {"id": 8, "kind": "shutdown", "modules": []}]}))
    r = _remote(server)
    r.push({"delivery_box": "박스A", "login_password": "x"})
    r.poll_once()
    sent = server.sent[0]
    out.append(check("첫 요청 — 빌드 ID·PC·판 0·고정 기능(빈 목록)",
                     sent["device_key"] == "bld_x" and sent["machine"] == "machine-guid"
                     and sent["known_version"] == 0 and sent["locked_modules"] == [], str(sent)))
    out.append(check("★ 올리는 설정에 비밀번호가 실리지 않는다", sent.get("pushed") == {"delivery_box": "박스A"},
                     str(sent.get("pushed"))))
    events = []
    while not r.inbox.empty():
        events.append(r.inbox.get_nowait())
    settings = [e for e in events if e[0] == remote.SETTINGS_EVENT]
    commands = [e for e in events if e[0] == remote.COMMAND_EVENT]
    out.append(check("★ 받은 설정은 원격 키만 (서버가 비밀을 보내도 버린다)",
                     settings == [(remote.SETTINGS_EVENT, {"delivery_box": "박스B"}, 3)], str(settings)))
    out.append(check("모르는 명령은 버리고 아는 것만 넘긴다",
                     commands == [(remote.COMMAND_EVENT, 7, "start", ["orders"])], str(commands)))
    out.append(check("판을 기억한다", r.version == 3))

    r.done(7, "시작함")
    server.replies = [(200, {"version": 3, "commands": []})]
    r.poll_once()
    out.append(check("결과를 다음 요청에 싣고, 올린 설정은 비운다",
                     server.sent[1].get("results") == [{"id": 7, "result": "시작함"}]
                     and "pushed" not in server.sent[1] and server.sent[1]["known_version"] == 3,
                     str(server.sent[1])))
    server.replies = [(200, {"version": 3, "commands": []})]
    r.poll_once()
    out.append(check("보낸 결과는 다시 보내지 않는다", "results" not in server.sent[2]))
    out.append(check("같은 판이면 설정 이벤트가 없다", r.inbox.empty()))

    r.push({"delivery_box": "박스C"})
    server.replies = [OSError("연결 끊김")]
    r.poll_once()
    server.replies = [(200, {"version": 3, "commands": []})]
    r.poll_once()
    out.append(check("네트워크가 끊겨도 올릴 설정은 남았다가 다음에 간다",
                     server.sent[-1].get("pushed") == {"delivery_box": "박스C"}))

    r.push({"auto_run_times": ("평일 09:00",)})
    server.replies = [(400, {"message": "bad request"})]
    r.poll_once()
    server.replies = [(200, {"version": 3, "commands": []})]
    r.poll_once()
    out.append(check("400 이면 그 설정을 또 보내지 않는다 (명령은 계속 받는다)",
                     "pushed" not in server.sent[-1] and not r.unauthorized))

    server.replies = [(401, {"message": "unauthorized"})]
    r.poll_once()
    out.append(check("★ 401 이면 멈춘다 (다른 PC·폐기)", r.unauthorized))

    locked = _remote(FakeServer(), locked=["logistics"])
    locked.push({"run_modules": ["orders"], "delivery_box": "박스B"})
    locked.poll_once()
    sent = locked._post.sent[0]
    out.append(check("기능 고정 빌드 — 기능은 올리지 않고 고정 기능을 알린다",
                     sent.get("pushed") == {"delivery_box": "박스B"} and sent["locked_modules"] == ["logistics"],
                     str(sent)))
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
    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.results: list[tuple[int, str]] = []
        self.pushed: list[dict] = []
        self.unauthorized = False

    def done(self, command_id, result):
        self.results.append((command_id, result))

    def push(self, values):
        self.pushed.append(dict(values))

    def applied(self, version):
        self.applied_version = version


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
            window._remote_seen = remote.snapshot(SETTINGS)     # `_start_remote` 가 하는 준비

            # 서버 판은 원격 키를 다 가진다 — 빠진 키는 PC 가 채워 올린다 (10-01, 아래 따로 본다)
            web = {**remote.snapshot(SETTINGS),
                   "delivery_box": "박스B", "delivery_company": "웹택배", "logistics_mode": "수동",
                   "hold_exclude_codes": ["111", "222"], "auto_run_enabled": False,
                   "auto_run_times": ["평일 09:00"], "run_modules": ["orders", "logistics"]}
            fake.inbox.put((remote.SETTINGS_EVENT, web, 5))
            window.busy = True
            window._drain_remote()
            out.append(check("★ 실행 중에는 웹 설정을 미룬다 (도는 회차 값을 바꾸지 않는다)",
                             window.box_var.get() != "박스B" and window._pending_remote is not None))
            window.busy = False
            window._drain_remote()
            saved = json.loads(temp.read_text(encoding="utf-8")) if temp.exists() else {}
            out.append(check("★ 끝나면 적용한다 — 화면 칸·설정 파일",
                             window.box_var.get() == "박스B" and window.courier_var.get() == "웹택배"
                             and window.mode_var.get() == "수동" and window.hold_var.get() == "111; 222"
                             and saved.get("delivery_box") == "박스B",
                             f"박스 {window.box_var.get()}, 파일 {saved.get('delivery_box')}"))
            out.append(check("기능 체크·예약 줄도 바뀐다",
                             window._checked_modules() == ["orders", "logistics"]
                             and window.autorun_pane.slots == ["평일 09:00"],
                             f"{window._checked_modules()} / {window.autorun_pane.slots}"))
            out.append(check("★ 받은 값을 서버로 되돌려 올리지 않는다", fake.pushed == [], str(fake.pushed)))
            out.append(check("적용한 뒤 그 판을 기록한다", getattr(fake, "applied_version", None) == 5))
            # 새로 생긴 설정(10-01 매출처리 방식)이 서버 판에 없으면 PC 값으로 채워 올린다
            fake.inbox.put((remote.SETTINGS_EVENT, {k: v for k, v in web.items() if k != "sales_mode"}, 6))
            window._drain_remote()
            out.append(check("★ 서버 판에 빠진 키(매출처리 방식)는 PC 값으로 채워 올린다",
                             fake.pushed == [{"sales_mode": SETTINGS.sales_mode}], str(fake.pushed)))
            fake.pushed.clear()
            fake.inbox.put((remote.SETTINGS_EVENT, {**web, "sales_mode": "선택주문"}, 7))
            window._drain_remote()
            out.append(check("★ 웹에서 바꾼 매출처리 방식이 화면 라디오에 옮겨진다 (안 옮기면 다음 저장이 되돌린다)",
                             window.sales_var.get() == "선택주문" and SETTINGS.sales_mode == "선택주문"
                             and fake.pushed == [], f"{window.sales_var.get()} / {fake.pushed}"))
            SETTINGS.sales_mode = "전체"
            window.sales_var.set("전체")
            window._remote_seen = remote.snapshot(SETTINGS)      # 되돌린 것을 'PC 에서 고침' 으로 보지 않게
            window.box_var.set("박스C")
            SETTINGS.delivery_box = "박스C"
            window._push_remote()
            out.append(check("PC 에서 고친 것만 올린다", fake.pushed == [{"delivery_box": "박스C"}], str(fake.pushed)))

            # 바로 저장 (09-29) — 사람이 친 것(키·체크·고르기)만 저장을 부른다. 코드가 칸을 바꾼 것은 아니다
            out.append(check("웹 설정을 칸에 옮겨도 저장을 예약하지 않는다", window._save_job is None))
            bound = str(window.courier_entry.bind("<KeyRelease>"))
            fake.pushed.clear()
            window.courier_var.set("손택배")
            window._schedule_save()
            scheduled = window._save_job is not None
            window._cancel_save()
            window._autosave()                                  # 0.8초 기다리는 대신 곧바로
            saved = json.loads(temp.read_text(encoding="utf-8"))
            out.append(check("★ 설정 칸에 치면 바로 저장하고 서버에도 올린다 (09-29)",
                             "_schedule_save" in bound and scheduled
                             and saved.get("delivery_company") == "손택배"
                             and fake.pushed == [{"delivery_company": "손택배"}]
                             and "저장했습니다" in window.saved_var.get(),
                             f"{saved.get('delivery_company')} / {fake.pushed} / {window.saved_var.get()}"))
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
            window._lock_if_unauthorized()
            out.append(check("원격이 401 을 받으면 [실행] 을 잠근다", window.verify_state == "blocked"))
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

    keep_state, keep_paused = remote.STATE_PATH, autorun.STATE_PATH
    temp = Path(tempfile.mkdtemp())
    remote.STATE_PATH = temp / "remote_state.json"      # 개발 PC 의 기록을 건드리지 않는다
    autorun.STATE_PATH = temp / "autorun_state.json"    # 웹 [일시정지]·[계속하기] 가 남긴다 (10-01)
    try:
        # check_values 는 판을 기록한다 — poll 확인(판 0 에서 시작)보다 뒤에 둔다
        results = (check_keys() + check_poll() + check_values() + check_window() + check_settings_file()
                   + check_autostart())
    finally:
        remote.STATE_PATH, autorun.STATE_PATH = keep_state, keep_paused
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
