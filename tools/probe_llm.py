r"""[조사 도구 - 읽기 전용] 사용법 질문 워커(`llm/worker.py`) 확인 (09-28).

실서버·실제 LM Studio 에 닿지 않는다 — 가짜 LM Studio(이 PC 안 임의 포트, 스트리밍)와 가짜 서버(`_http_post` 자리).
설정 파일은 임시 파일로 돌린다. 작업 스케줄러·레지스트리 등록은 부르지 않고 XML 만 본다.

    .venv\Scripts\python.exe -m tools.probe_llm
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import logging
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from config import settings as config_settings  # noqa: E402
from config.settings import DEFAULTS, SETTINGS  # noqa: E402
from llm import worker  # noqa: E402
from utils import autostart  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent


def check(name: str, ok: object, detail: str = "") -> bool:
    ok = bool(ok)
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


# --- 가짜 LM Studio ------------------------------------------------------------------------------

class FakeLms:
    """정해 둔 조각을 SSE 로 흘린다. 받은 요청은 적어 둔다. `status` 가 200 이 아니면 그 코드로 실패."""

    def __init__(self) -> None:
        self.pieces: list = []
        self.gap = 0.0
        self.status = 200
        self.send_done = True
        self.requests: list[dict] = []
        self.served = 0                      # 실제로 보낸 조각 수
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):     # 콘솔을 어지럽히지 않는다
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for piece in fake.pieces:     # 글자면 답 조각, dict 면 그 이벤트를 그대로
                        chunk = piece if isinstance(piece, dict) else {"choices": [{"delta": {"content": piece}}]}
                        self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                        self.wfile.flush()
                        fake.served += 1
                        if fake.gap:
                            threading.Event().wait(fake.gap)
                    if fake.send_done:
                        self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return                    # 워커가 먼저 끊었다 — 기대한 일일 수 있다

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self, pieces: list, gap: float = 0.0, status: int = 200, send_done: bool = True) -> None:
        self.pieces, self.gap, self.status, self.send_done = pieces, gap, status, send_done
        self.requests, self.served = [], 0


class FakeServer:
    """`_http_post` 자리. llm_write 는 적어 두고 `write_reply` 를 준다. llm_take 는 `takes` 를 차례로."""

    def __init__(self) -> None:
        self.writes: list[dict] = []
        self.takes: list = []                  # (상태, 본문) 또는 예외
        self.write_reply = "true"
        self.final_fails = 0                   # 닫는 쓰기를 이만큼 503 으로 거절한다
        self.lock = threading.Lock()

    def __call__(self, url, anon, payload, limit=1000):
        body = json.loads(payload)
        fn = url.rsplit("/", 1)[-1]
        if fn == "llm_write":
            with self.lock:
                self.writes.append(body)
                if body["final"] and self.final_fails > 0:
                    self.final_fails -= 1
                    return 503, "busy"
            return 200, self.write_reply
        if fn == "llm_take":
            reply = self.takes.pop(0) if self.takes else (401, "unauthorized")
            if isinstance(reply, Exception):
                raise reply
            status, item = reply
            return status, json.dumps(item, ensure_ascii=False)
        return 404, "{}"


def _server(fake: FakeServer) -> worker.Server:
    worker._http_post = fake
    return worker.Server("https://fake", "anon", "llmw_fake")


# --- 검사 ----------------------------------------------------------------------------------------

def check_text() -> list[bool]:
    log.info("▶ 생각 블록 빼기")
    cases = [("<think>\n\n</think>\n\n답입니다", "답입니다"), ("<thi", ""), ("<think>생각 중", ""),
             ("  바로 답", "바로 답"), ("답 <think>는 글자", "답 <think>는 글자"), ("", "")]
    bad = [(raw, worker.visible(raw), want) for raw, want in cases if worker.visible(raw) != want]
    return [check("생각 블록은 빼고, 닫히기 전에는 아무것도 안 보인다", not bad, str(bad))]


def check_prompt(tmp: Path) -> list[bool]:
    log.info("▶ 프롬프트")
    out = []
    guide = tmp / "guide.md"
    guide.write_text("# 설명서\n[실행] 을 누른다.\n", encoding="utf-8")
    prompt = worker.Prompt(guide)
    first, second = prompt.system(), prompt.system()
    out.append(check("시스템 프롬프트 = 규칙 + 설명서, 매번 같은 글 (LM Studio 가 앞부분을 다시 계산하지 않는다)",
                     first == second and first.startswith(worker.RULES) and "[실행] 을 누른다." in first))
    alone = prompt.messages({"question": "예약은?"})
    follow = prompt.messages({"question": "그럼 그건요?", "prev_question": "예약은?", "prev_answer": "[자동 실행] 에서."})
    out.append(check("질문만: [시스템, 질문 + /no_think]",
                     [m["role"] for m in alone] == ["system", "user"] and alone[1]["content"].endswith("/no_think")))
    out.append(check("이어 묻기: 앞 질문·답을 시스템 뒤에 (시스템 글은 그대로)",
                     [m["role"] for m in follow] == ["system", "user", "assistant", "user"]
                     and follow[0]["content"] == first and follow[2]["content"] == "[자동 실행] 에서."))
    long_prev = prompt.messages({"question": "또?", "prev_question": "예약은?", "prev_answer": "가" * 4000})
    out.append(check("앞 답은 앞부분만 붙인다 (문맥 8192 를 넘지 않게)",
                     len(long_prev[2]["content"]) == worker.PREV_ANSWER_CHARS))
    guide.write_text("# 설명서\n[중단] 을 누른다.\n", encoding="utf-8")
    later = time.time() + 5
    import os
    os.utime(guide, (later, later))
    out.append(check("설명서를 고치면 다음 질문부터 바뀐다 (워커를 다시 켜지 않아도)",
                     "[중단] 을 누른다." in prompt.system()))
    return out


def check_guide() -> list[bool]:
    log.info("▶ 설명서 (llm/guide.md) — 내부 값이 없는지")
    text = worker.GUIDE_PATH.read_text(encoding="utf-8")
    out = []
    # 이 PC 의 실제 값 — 설정에서 읽어 비교만 한다 (찍지 않는다)
    private = [str(getattr(SETTINGS, k, "") or "") for k in
               ("login_company_code", "login_user_id", "mail_user_id", "server_anon_key", "download_base_dir",
                "target_exe", "excel_dir")]
    host = (getattr(SETTINGS, "server_url", "") or "").split("//")[-1]
    leaked = [i for i, v in enumerate(private + [host]) if len(v) >= 4 and v in text]
    out.append(check("★ 계정·키·경로·서버 주소 (이 PC 의 설정값) 가 없다", not leaked, f"{len(leaked)}개"))
    patterns = {
        "드라이브 경로": r"[A-Za-z]:\\", "auto_id": r"auto_?id", "exe 파일": r"\.exe\b", "서버 이름": r"(?i)supabase",
        "키 머리": r"\b(sb_|rpa_|llmw_|bld_|enr_|eyJ)", "주소": r"https?://", "이메일": r"\S+@\S+\.\S+",
        "코드 이름(snake_case)": r"\b[a-z]+_[a-z_]+\b", "파일 이름": r"\b\w+\.(py|json|js|md|log)\b",
    }
    found = {name: re.findall(p, text)[:3] for name, p in patterns.items() if re.search(p, text)}
    out.append(check("★ 내부 값 꼴 (경로·auto_id·exe·키·주소·이메일·코드 이름·파일 이름) 이 없다", not found, str(found)))
    # 09-28 --bench: 8443자 = 약 5850 토큰. 9000자를 넘기면 이어 묻기 + 답 상한이 문맥 8192 를 넘을 수 있다
    out.append(check("길이 — 문맥 8192 토큰 안에 설명서 + 질문 + 답 (9000자 이하, 늘리면 --bench 로 토큰을 본다)",
                     len(text) <= 9000, f"{len(text)}자"))
    return out


def check_answer(lms: FakeLms) -> list[bool]:
    log.info("▶ 답 만들기 — 가짜 LM Studio 스트리밍")
    out = []
    worker.LMS_URL = lms.url
    prompt = worker.Prompt()
    item = {"id": 7, "question": "예약은 어떻게 거나요?"}

    fake = FakeServer()
    lms.reset(["<think>\n\n</think>\n\n", "[자동 실행] ", "칸에서 ", "켭니다."], gap=0.05)
    ok = worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    body = lms.requests[0] if lms.requests else {}
    out.append(check("요청 — 불러 둔 모델 이름·스트리밍·상한·/no_think",
                     body.get("model") == worker.IDENT and body.get("stream") is True
                     and body.get("max_tokens") == worker.MAX_TOKENS
                     and body["messages"][-1]["content"].endswith("/no_think")))
    out.append(check("★ 마지막에 한 번 닫는다 — 생각 블록 없이 전체 답", ok and len(finals) == 1
                     and finals[0]["answer"] == "[자동 실행] 칸에서 켭니다." and finals[0]["failed"] is False,
                     str(finals)))
    partial = [w for w in fake.writes if not w["final"]]
    out.append(check("★ 만드는 동안 지금까지의 글을 흘려 쓴다 (생각 블록은 안 보낸다)",
                     partial and all("<think>" not in w["answer"] for w in partial)
                     and all(w["question_id"] == 7 and w["worker_key"] == "llmw_fake" for w in fake.writes),
                     f"{len(partial)}번"))

    fake = FakeServer()
    lms.reset([], status=500)
    ok = worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("LM Studio 가 실패하면 — 실패로 닫고 사람 말 + 모델 다시 확인(False)",
                     ok is False and len(finals) == 1 and finals[0]["failed"] is True
                     and finals[0]["answer"] == worker.FAIL_TEXT))

    fake = FakeServer()
    lms.reset([], status=200)
    ok = worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("빈 답이면 실패로 닫는다", finals and finals[0]["failed"] is True
                     and finals[0]["answer"] == worker.FAIL_TEXT))

    fake = FakeServer()
    fake.write_reply = "false"                  # 서버에서 이미 닫혔다 (만료)
    lms.reset([f"조각{i} " for i in range(200)], gap=0.01)
    worker.answer(item, prompt, _server(fake))
    out.append(check("★ 서버가 닫은 질문이면 만들기를 멈추고 닫지 않는다",
                     lms.served < 200 and not [w for w in fake.writes if w["final"]], f"{lms.served}/200 조각"))

    fake = FakeServer()
    lms.reset(["가" * 1000] * 5)
    worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("긴 답은 서버 상한(4000자) 안에서 끊고 알린다",
                     finals and len(finals[0]["answer"]) <= worker.ANSWER_LIMIT
                     and finals[0]["answer"].endswith(worker.CUT_TEXT.strip())))

    fake = FakeServer()
    lms.reset(["반쯤 만든 ", "답"], send_done=False)   # LM Studio 가 도중에 꺼졌다
    ok = worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("★ 끝 표시 없이 끊긴 스트림은 다 된 답으로 닫지 않는다 (실패 + 모델 다시 확인)",
                     ok is False and finals and finals[0]["failed"] is True and worker.FAIL_TEXT in finals[0]["answer"]))

    fake = FakeServer()
    lms.reset(["앞부분 ", {"error": "model unloaded"}])
    ok = worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("오류 이벤트도 실패로 닫는다", ok is False and finals and finals[0]["failed"] is True))

    fake = FakeServer()
    lms.reset(["1. 첫 단계", {"choices": [{"delta": {}, "finish_reason": "length"}]}])
    worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("토큰 상한에서 잘린 답은 잘렸다고 알린다",
                     finals and finals[0]["answer"].endswith(worker.CUT_TEXT.strip()) and finals[0]["failed"] is False))

    fake = FakeServer()
    fake.final_fails = 2                         # 서버가 잠깐 503
    lms.reset(["닫는 ", "쓰기"])
    worker.answer(item, prompt, _server(fake))
    finals = [w for w in fake.writes if w["final"]]
    out.append(check("★ 닫는 쓰기가 잠깐 실패하면 다시 해서 닫는다 ('만드는 중' 에 머물지 않게)",
                     len(finals) == 3 and fake.final_fails == 0, f"{len(finals)}번"))

    fake = FakeServer()
    lms.reset(["빠른 ", "답"], gap=0.3)         # 서버 쓰기가 만들기를 막지 않는다
    started = time.monotonic()

    def slow(url, anon, payload, limit=1000):
        threading.Event().wait(0.4)
        return fake(url, anon, payload, limit)
    worker._http_post = slow
    worker.answer(item, prompt, worker.Server("https://fake", "anon", "llmw_fake"))
    out.append(check("느린 서버에도 끝까지 온다 (쓰기는 따로 도는 스레드)",
                     [w for w in fake.writes if w["final"]] and time.monotonic() - started < 3.0,
                     f"{time.monotonic() - started:.1f}초"))
    return out


def check_loop(lms: FakeLms) -> list[bool]:
    log.info("▶ 돌기 — 가져오기·401·설정 없음")
    out = []
    keep = (worker._server, worker.worker_key, worker.ensure_model, worker.warm_up)
    try:
        worker._server = lambda s: ("https://fake", "anon", "")
        worker.worker_key = lambda: "llmw_fake"
        worker.ensure_model = lambda stop: True
        worker.warm_up = lambda prompt: None
        fake = FakeServer()
        fake.takes = [(200, None), http.client.IncompleteRead(b"x"),
                      (200, {"id": 9, "question": "중단은?"}), (401, "unauthorized")]
        _server(fake)
        lms.reset(["[중단] ", "을 누릅니다."])
        code = worker.run(threading.Event())
        finals = [w for w in fake.writes if w["final"]]
        out.append(check("빈 응답은 넘기고, 끊긴 응답(IncompleteRead)에도 죽지 않고, 질문은 답하고, 401 이면 멈춘다 (3)",
                         code == 3 and len(finals) == 1 and finals[0]["question_id"] == 9, f"코드 {code}"))
        worker.worker_key = lambda: ""
        out.append(check("워커 키가 없으면 돌지 않는다 (2)", worker.run(threading.Event()) == 2))

        def locked() -> str:
            raise RuntimeError("dpapi 를 풀지 못했다")
        worker.worker_key = locked
        out.append(check("다른 Windows 계정의 키(못 푼다)면 로그를 남기고 끝낸다 (2)", worker.run(threading.Event()) == 2))
    finally:
        worker._server, worker.worker_key, worker.ensure_model, worker.warm_up = keep

    keep_run, keep_acquire = worker.run, worker.instance.acquire
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger(worker.__name__).addHandler(handler)
    try:
        def broken(stop):
            raise ValueError("예상 못 한 예외")
        worker.run, worker.instance.acquire = broken, (lambda path: True)
        code = worker.main([])
    finally:
        worker.run, worker.instance.acquire = keep_run, keep_acquire
        logging.getLogger(worker.__name__).removeHandler(handler)
    out.append(check("★ 예상 못 한 예외도 파일 로그에 남기고 끝낸다 (pythonw 는 stderr 가 없다)",
                     code == 1 and any(r.exc_info for r in records)))
    code = (ROOT / "llm" / "worker.py").read_text(encoding="utf-8")
    out.append(check("끊긴 채 오래 가도 대기 계산이 넘치지 않는다 (지수 상한)", "2.0 ** min(failures, 5)" in code))
    return out


def check_settings(tmp: Path) -> list[bool]:
    log.info("▶ 설정 — 워커 키 (임시 설정 파일)")
    out = []
    out.append(check("워커 키는 RPA 설정 항목이 아니다 (굽기·빌드 칸에 안 들어간다)",
                     not any(k.startswith(config_settings.WORKER_PREFIX) for k in DEFAULTS)))
    path = tmp / "settings.local.json"
    path.write_text(json.dumps({"delivery_box": "박스A"}), encoding="utf-8")
    keep = (config_settings.LOCAL_SETTINGS_PATH, worker.LOCAL_SETTINGS_PATH)
    config_settings.LOCAL_SETTINGS_PATH = worker.LOCAL_SETTINGS_PATH = path
    try:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            worker.init_key()
        raw = json.loads(path.read_text(encoding="utf-8"))
        key = worker.worker_key()
        out.append(check("★ --init — 파일에는 감싼 값, 다른 값은 그대로",
                         str(raw.get(worker.KEY_NAME, "")).startswith("dpapi:") and raw.get("delivery_box") == "박스A"))
        out.append(check("★ --init — 평문 키는 안 찍고 해시만 찍는다",
                         key.startswith(worker.KEY_PREFIX) and key not in printed.getvalue()
                         and hashlib.sha256(key.encode()).hexdigest() in printed.getvalue()))
        with contextlib.redirect_stdout(io.StringIO()):
            worker.init_key()
        out.append(check("--init 을 또 해도 키를 바꾸지 않는다 (서버 등록이 깨지지 않게)", worker.worker_key() == key))

        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append
        logging.getLogger("config.settings").addHandler(handler)
        try:
            config_settings._apply_file(config_settings.Settings(), path, "임시")
        finally:
            logging.getLogger("config.settings").removeHandler(handler)
        out.append(check("RPA 는 워커 키를 '모르는 키' 로 경고하지 않는다",
                         not [r for r in records if worker.KEY_NAME in r.getMessage()]))
    finally:
        config_settings.LOCAL_SETTINGS_PATH, worker.LOCAL_SETTINGS_PATH = keep
    return out


def check_logs(tmp: Path) -> list[bool]:
    log.info("▶ 워커 로그 — 따로 두고 오래된 것은 지운다")
    folder = tmp / "llmlogs"
    folder.mkdir()
    old, new = folder / "llm_20260101.log", folder / "llm_20260928.log"
    old.write_text("x", encoding="utf-8")
    new.write_text("x", encoding="utf-8")
    ago = time.time() - (worker.LOG_KEEP_DAYS + 5) * 86400
    import os
    os.utime(old, (ago, ago))
    keep = worker.WORKER_LOG_DIR
    worker.WORKER_LOG_DIR = folder
    try:
        worker._drop_old_logs()
    finally:
        worker.WORKER_LOG_DIR = keep
    return [
        check("워커 로그는 logs/llm/ — RPA 로그와 섞이지 않고 임시 로그 정리(logs/*.log)에 안 걸린다",
              keep == config_settings.LOG_DIR / "llm"),
        check(f"켤 때 {worker.LOG_KEEP_DAYS}일 지난 로그만 지운다", not old.exists() and new.exists()),
    ]


def check_install() -> list[bool]:
    log.info("▶ 자동 켜기 XML (등록하지 않는다)")
    script = ROOT / "llm" / "worker.py"
    exe = Path(tempfile.gettempdir()) / "venv" / "pythonw.exe"
    xml = autostart.task_xml(exe, f'"{script}"', ROOT)
    rpa = autostart.task_xml(Path(tempfile.gettempdir()) / "RPA_1.exe")
    return [
        check("워커 — 스크립트 경로를 인자로, 작업 폴더는 프로젝트", f'<Arguments>"{script}"</Arguments>' in xml
              and f"<WorkingDirectory>{ROOT}</WorkingDirectory>" in xml),
        check("RPA 는 --background --task, exe 폴더 (09-29)", "<Arguments>--background --task</Arguments>" in rpa
              and f"<WorkingDirectory>{tempfile.gettempdir()}</WorkingDirectory>" in rpa),
    ]


def check_contract() -> list[bool]:
    log.info("▶ 서버·빌드와 맞는지")
    schema = (ROOT / "server" / "schema.sql").read_text(encoding="utf-8")
    code = (ROOT / "llm" / "worker.py").read_text(encoding="utf-8")
    out = []
    grants = {"ask_question(text)": "authenticated", "llm_online()": "authenticated",
              "llm_take(text)": "anon", "llm_write(text, bigint, text, boolean, boolean)": "anon"}
    out.append(check("★ RPC 넷의 권한 — 웹은 로그인 사용자, 워커는 키로",
                     all(f"grant execute on function public.{f} to {who};" in schema for f, who in grants.items())))
    write_args = re.search(r"function public\.llm_write\(([^)]*)\)", schema).group(1)
    names = set(re.findall(r"(\w+) (?:text|bigint|boolean)", write_args))
    call = re.search(r'self\.call\("llm_write",(.*?)\)\n', code, re.S).group(1)
    sent = set(re.findall(r"(\w+)=", call)) | {"worker_key"}          # worker_key 는 Server.call 이 붙인다
    out.append(check("llm_write 인자 이름 = 워커가 보내는 이름", names == sent, f"{sorted(names)} / {sorted(sent)}"))
    purge = re.search(r"function public\.purge_old\(\).*?\$\$;", schema, re.S).group(0)
    out.append(check("★ 질문 보관 = 30일 + 사람당 최근 20개 (사용자 확정 09-28 — DB 용량을 사용자 수에 묶는다)",
                     "questions where created_at < now() - interval '30 days'" in purge
                     and "partition by user_id order by id desc" in purge and "r.rn > 20" in purge))
    limit = re.search(r"length\(answer\) <= (\d+)", schema)
    out.append(check("답 상한 = 서버 check", limit and int(limit.group(1)) == worker.ANSWER_LIMIT))
    out.append(check("워커 코드 — while True·time.sleep 없음 (프로젝트 규칙)",
                     "while True" not in code and "time.sleep" not in code))
    specs = [p for p in ROOT.glob("build_*.spec")]
    out.append(check("★ 빌드(build_run.spec 하나)가 llm 을 금지 목록·검사 대상에 둔다",
                     len(specs) == 1 and all('"llm",' in p.read_text(encoding="utf-8")
                                             and '"tools", "llm")' in p.read_text(encoding="utf-8") for p in specs)))
    product = [p for d in ("gui", "automation", "collect", "orchestrator", "config", "utils")
               for p in (ROOT / d).rglob("*.py")] + list(ROOT.glob("main_*.py"))
    users = [p.name for p in product if re.search(r"^\s*(from|import) llm\b", p.read_text(encoding="utf-8"), re.M)]
    out.append(check("제품 코드는 llm 을 부르지 않는다", not users, str(users)))
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []
    keep_post, keep_url = worker._http_post, worker.LMS_URL
    lms = FakeLms()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            results += check_text()
            results += check_prompt(Path(tmp))
            results += check_guide()
            results += check_answer(lms)
            results += check_loop(lms)
            results += check_settings(Path(tmp))
            results += check_logs(Path(tmp))
            results += check_install()
            results += check_contract()
        finally:
            worker._http_post, worker.LMS_URL = keep_post, keep_url
            lms.httpd.shutdown()
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
