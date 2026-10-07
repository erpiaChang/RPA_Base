r"""[조사 도구 - 읽기 전용] 켜기 비용 줄이기 확인 (09-29).

1. node.exe 리소스 — 꺼내기·다시 쓰기·깨지면 다시 꺼내기·옛 판 지우기·메일 없는 빌드 (`collect/pw_driver.py`)
2. 작업 스케줄러 넘기기 — 가짜 schtasks 로 넘길지·못 넘길지 (`utils/autostart.py`)
3. 진입점 순서 — 가짜 잠금·넘기기로 `main_run.py` 를 돌린다 (로그온만 넘기는지, 넘기기 전에 잠금을 놓는지)
4. `--exe` : 빌드한 `dist/run/RPA_1.exe` 를 열어 본다 (실행하지 않는다) — 풀리는 칸에 node.exe·AVIF 가 없는지,
   리소스에서 꺼낸 node.exe 가 패키지의 것과 같은지. `--browser` 를 더하면 꺼낸 node.exe 로 Edge 를
   창 없이 띄워 빈 페이지를 연다 (계정·메일에 닿지 않는다).

시스템을 바꾸지 않는다 — 작업 스케줄러·레지스트리는 부르지 않는다. 파일은 임시 폴더에만 쓴다.

    .venv\Scripts\python.exe -m tools.probe_startup [--exe [exe 경로] [--browser]]
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from collect import pw_driver  # noqa: E402
from utils import autostart, crashlog, instance, process, selfcheck  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "run" / "RPA_1.exe"
FAKE_EXE = Path(tempfile.gettempdir()) / "probe_startup" / "RPA_1.exe"


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def check_driver() -> list[bool]:
    log.info("▶ node.exe 리소스 꺼내기 (가짜 node.exe)")
    work = Path(tempfile.mkdtemp(prefix="probe_driver_"))
    try:
        fake = work / "node.exe"
        fake.write_bytes(os.urandom(1 << 16) * 8)
        blob = pw_driver.pack(fake)
        old = work / "driver" / "0123456789abcdef"
        old.mkdir(parents=True)
        (old / "node.exe").write_bytes(b"old")
        first = pw_driver.ensure(work, blob)
        same = first.read_bytes() == fake.read_bytes()
        stamp = first.stat().st_mtime_ns          # 다시 쓰면 100ns 단위 시각이 바뀐다
        again = pw_driver.ensure(work, blob)
        kept = again == first and again.stat().st_mtime_ns == stamp
        first.write_bytes(b"broken")
        fixed = pw_driver.ensure(work, blob).read_bytes() == fake.read_bytes()
        bad_head = _raises(lambda: pw_driver.ensure(work, b"XXXXXXXX" + blob[8:]))
        tampered = bytearray(blob)
        tampered[pw_driver.HEAD] ^= 0xFF                      # 압축 몸통 한 바이트
        bad_body = _raises(lambda: pw_driver.ensure(work / "t", bytes(tampered)))
        env_before = os.environ.get(pw_driver.ENV)
        pw_driver.prepare()                                   # 개발 중 — 아무것도 안 한다
        return [
            check("꺼내면 원본과 같다, exe 옆 driver\\<해시>\\node.exe", same
                  and first.parent.parent == work / "driver" and len(first.parent.name) == 16),
            check("두 번째는 다시 쓰지 않는다", kept),
            check("★ 깨진 캐시는 다시 꺼낸다", fixed),
            check("옛 판 폴더를 지운다", not old.exists()),
            check("머리가 다른 리소스는 거절", bad_head),
            check("★ 몸통이 바뀐 리소스는 거절 (해시)", bad_body),
            check("개발 중에는 환경 변수를 건드리지 않는다", os.environ.get(pw_driver.ENV) == env_before),
            check("메일 없는 기능 고정 빌드만 Playwright 를 뺀다",
                  pw_driver.needed({}) and pw_driver.needed({"run_modules_locked": True, "run_modules": ["mail"]})
                  and not pw_driver.needed({"run_modules_locked": True, "run_modules": ["orders", "logistics"]})
                  and pw_driver.needed({"run_modules_locked": False, "run_modules": ["orders"]})),
        ]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _raises(func) -> bool:
    try:
        func()
    except (ValueError, Exception):          # noqa: BLE001 — zlib.error 도 거절로 친다
        return True
    return False


class FakeSchtasks:
    """`autostart._schtasks` 자리. 부른 것을 적고 정해 둔 답을 준다."""

    def __init__(self, xml: str | None, run_ok: bool = True):
        self.xml, self.run_ok, self.calls = xml, run_ok, []

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(args[0])
        if args[0] == "/query":
            ok = self.xml is not None
            return subprocess.CompletedProcess(args, 0 if ok else 1, self.xml or "", "")
        return subprocess.CompletedProcess(args, 0 if self.run_ok else 1, "", "")


def check_hand_over() -> list[bool]:
    log.info("▶ 작업 스케줄러 넘기기 (가짜 schtasks — 등록하지 않는다)")
    keep = autostart._schtasks
    now_xml = autostart.task_xml(FAKE_EXE)
    old_xml = now_xml.replace(autostart.TASK_ARGS, autostart.BACKGROUND)
    out = []
    try:
        out.append(check("작업 XML — 인자 --background --task, 우선순위 보통(5)",
                         f"<Arguments>{autostart.TASK_ARGS}</Arguments>" in now_xml
                         and "<Priority>5</Priority>" in now_xml and "<MultipleInstancesPolicy>IgnoreNew" in now_xml))
        cases = [("작업이 없으면 넘기지 않는다", FakeSchtasks(None), False, ["/query"]),
                 ("★ 옛 판 작업(--task 없음)이면 넘기지 않는다 — 되풀이 방지", FakeSchtasks(old_xml), False, ["/query"]),
                 ("꺼진 작업이면 넘기지 않는다",
                  FakeSchtasks(now_xml.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>")), False,
                  ["/query"]),
                 ("실행이 거부되면 넘기지 않는다", FakeSchtasks(now_xml, run_ok=False), False, ["/query", "/run"]),
                 ("지금 판 작업이면 /run 으로 넘긴다", FakeSchtasks(now_xml), True, ["/query", "/run"])]
        for name, fake, want, calls in cases:
            autostart._schtasks = fake
            got = autostart.hand_over(FAKE_EXE)
            out.append(check(name, got is want and fake.calls == calls, f"부른 것 {fake.calls}"))
    finally:
        autostart._schtasks = keep
    return out


def check_entry() -> list[bool]:
    log.info("▶ main_run.py 순서 (가짜 잠금·넘기기·창)")
    out = []
    for name, frozen, args, acquire, handed, want in (
            ("개발 중에는 넘기지 않고 뜬다", False, ["--background"], [True], None, ["install", "acquire", "run"]),
            ("★ 로그온(--background)은 잠금을 놓고 넘긴다, 넘겼으면 창을 띄우지 않는다", True, ["--background"],
             [True], True, ["install", "acquire", "release", "hand_over"]),
            ("★ 사람이 누른 것은 넘기지 않고 바로 뜬다 — 두 번 풀지 않게", True, [], [True], None,
             ["install", "acquire", "run"]),
            ("못 넘기면 다시 잡고 뜬다", True, ["--background"], [True, True], False,
             ["install", "acquire", "release", "hand_over", "acquire", "run"]),
            ("못 넘긴 사이 다른 벌이 잡았으면 끝낸다", True, ["--background"], [True, False], False,
             ["install", "acquire", "release", "hand_over", "acquire"]),
            ("★ 작업이 띄운 것(--task)은 다시 넘기지 않는다", True, ["--background", "--task"], [True], None,
             ["install", "acquire", "run"]),
            ("이미 떠 있고 사람이 눌렀으면 알린다", True, [], [False], None, ["install", "acquire", "tell"]),
            ("이미 떠 있고 자동 켜기면 조용히 끝난다", True, ["--background"], [False], None, ["install", "acquire"]),
            ("★ 빌드 직후 점검(--selfcheck)은 훅·한 벌 확인·창 없이 점검만 (10-07)", True,
             ["--selfcheck", "x.json"], [True], None, ["selfcheck"])):
        calls = _run_entry(frozen, args, list(acquire), handed)
        out.append(check(name, calls == want, f"{calls}"))
    return out


def _run_entry(frozen: bool, args: list[str], acquire: list[bool], handed: bool | None) -> list[str]:
    calls: list[str] = []
    keep = (instance.acquire, instance.release, instance.tell_already_running,
            autostart.hand_over, sys.argv, sys.modules.get("gui.run_app"), crashlog.install, selfcheck.run)
    fake_gui = types.ModuleType("gui.run_app")
    fake_gui.run = lambda: calls.append("run") or 0
    crashlog.install = lambda *a: calls.append("install")     # 진짜는 전역 훅을 바꾼다
    selfcheck.run = lambda argv: calls.append("selfcheck") or 0
    instance.acquire = lambda *a: calls.append("acquire") or acquire.pop(0)
    instance.release = lambda: calls.append("release")
    instance.tell_already_running = lambda: calls.append("tell")
    autostart.hand_over = lambda exe: calls.append("hand_over") or handed
    sys.argv = ["main_run.py", *args]
    sys.modules["gui.run_app"] = fake_gui
    if frozen:
        sys.frozen = True
    try:
        runpy.run_path(str(ROOT / "main_run.py"), run_name="__main__")
    except SystemExit as done:                   # main_run 은 sys.exit 로 끝난다 — 정상
        log.debug("main_run 끝 (%s)", done.code)
    finally:
        (instance.acquire, instance.release, instance.tell_already_running,
         autostart.hand_over, sys.argv, gui, crashlog.install, selfcheck.run) = keep
        if gui is None:
            sys.modules.pop("gui.run_app", None)
        else:
            sys.modules["gui.run_app"] = gui
        if frozen:
            del sys.frozen
    return calls


def check_instance() -> list[bool]:
    log.info("▶ 한 벌만 — 뮤텍스 판정 (10-07, 권한이 다른 벌)")
    second = instance.is_second
    return [check("★ 만들지 못했는데 접근 거부면 다른 권한의 벌이 떠 있다", second(None, instance.ERROR_ACCESS_DENIED)),
            check("그 밖의 이유로 못 만들면 뜬다", not second(None, 0)),
            check("만들었는데 이미 있으면 둘째다", second(1, instance.ERROR_ALREADY_EXISTS)),
            check("처음 만들었으면 뜬다", not second(1, 0))]


def _caught(exc: BaseException):
    try:
        raise exc
    except BaseException as got:        # noqa: BLE001 — 훅에 넘길 (종류, 값, 트레이스백)을 만든다
        return type(got), got, got.__traceback__


def _thread_boom() -> None:
    raise RuntimeError("가짜 스레드 오류")


def check_crashlog() -> list[bool]:
    log.info("▶ 처리 안 된 예외 — logs 에 남기고 알림 (10-07)")
    import threading

    from utils.cancel import Cancelled

    folder = Path(tempfile.mkdtemp())
    told: list[str] = []
    boxes: list[str] = []
    keep = (sys.excepthook, threading.excepthook, crashlog._installed, crashlog._log_dir, crashlog._notifier,
            dict(crashlog._seen), crashlog._notified, crashlog._message_box, sys.argv)
    out = []
    try:
        crashlog._installed, crashlog._notified = False, 0
        crashlog._seen.clear()
        crashlog.install(folder)
        hook = sys.excepthook
        crashlog.install(folder)
        out.append(check("훅을 건다 — 두 번 불러도 한 번만", hook is sys.excepthook is not keep[0]
                         and threading.excepthook is not keep[1]))
        crashlog.set_notifier(told.append)
        for exc in (SystemExit(0), KeyboardInterrupt(), Cancelled()):
            crashlog.report(*_caught(exc), "main")
        out.append(check("끝내기·[중단](Cancelled)은 남기지도 알리지도 않는다",
                         not told and not list(folder.glob("crash_*.log"))))
        for _ in range(2):
            crashlog.report(*_caught(ValueError("가짜 1")), "tk")
        body = "".join(p.read_text(encoding="utf-8") for p in folder.glob("crash_*.log"))
        out.append(check("crash 파일에 트레이스백, 같은 오류 두 번째는 한 줄·알림 없음",
                         "Traceback" in body and "같은 오류 2번째" in body and len(told) == 1, str(len(told))))
        for n in range(2, 6):
            crashlog.report(*_caught(ValueError(f"가짜 {n}")), "tk")
        out.append(check("알림은 프로세스당 3번까지", len(told) == crashlog.MAX_NOTICES, str(len(told))))
        crashlog._seen.clear()
        crashlog._notified = 0
        told.clear()
        worker = threading.Thread(target=_thread_boom, name="probe")
        worker.start()
        worker.join()
        out.append(check("스레드에서 터진 예외도 알린다", told == [crashlog.CRASH_TEXT]))
        crashlog.set_notifier(None)
        crashlog._message_box = boxes.append
        sys.argv = ["main_run.py", "--background"]
        crashlog.report(*_caught(RuntimeError("켜기 실패 A")), "main")
        sys.argv = ["main_run.py"]
        crashlog.report(*_caught(RuntimeError("켜기 실패 B")), "main")
        out.append(check("★ 창이 뜨기 전 실패 — 자동 켜기면 창 없이, 사람이 켰으면 알림 창 한 번",
                         boxes == [crashlog.START_FAIL_TEXT], str(boxes)))
        source = (ROOT / "utils" / "crashlog.py").read_text(encoding="utf-8")
        out.append(check("설정·utils 를 부르지 않는다 (설정이 깨져도 남긴다)",
                         "import config" not in source and "from config" not in source
                         and "from utils" not in source))
        entry = (ROOT / "main_run.py").read_text(encoding="utf-8")
        out.append(check("main_run 은 한 벌 확인보다 먼저 훅을 건다",
                         0 < entry.find("crashlog.install()") < entry.find("instance.acquire()")))
    finally:
        (sys.excepthook, threading.excepthook, crashlog._installed, crashlog._log_dir, crashlog._notifier,
         seen, crashlog._notified, crashlog._message_box, sys.argv) = keep
        crashlog._seen.clear()
        crashlog._seen.update(seen)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def check_selfcheck() -> list[bool]:
    """빌드 직후 자가 점검 본체를 개발 폴더에서 돌린다 (10-07) — 한 벌 확인·서버 확인·로그 열기를 부르지 않는가."""
    log.info("▶ 자가 점검 본체 (개발 폴더 — 모듈 전부 불러오기·Tcl·UIA)")
    from orchestrator import telemetry
    from utils import logger

    touched: list[str] = []
    keep = (instance.acquire, telemetry.verify, logger.setup_logging)
    instance.acquire = lambda *a: touched.append("acquire")
    telemetry.verify = lambda *a, **k: touched.append("verify")
    logger.setup_logging = lambda *a, **k: touched.append("setup_logging")
    try:
        result = selfcheck.collect()
    finally:
        instance.acquire, telemetry.verify, logger.setup_logging = keep
    steps = result["steps"]
    return [check("모듈 전부 불러오기·Tcl·UIA 가 통과한다",
                  all(steps[name]["ok"] for name in ("modules", "tcl", "uia")),
                  str({k: v["detail"][:60] for k, v in steps.items()})),
            check("★ 점검은 한 벌 확인·서버 확인·로그 열기를 부르지 않는다", not touched, str(touched)),
            check("개발 전용(빌드 프로그램)은 불러오지 않는다", "gui.build_app" in selfcheck.SKIP)]


def check_clock() -> list[bool]:
    log.info("▶ 켜는 데 걸린 시간 — 프로세스 생성 시각")
    started = process.created_epoch(os.getpid())
    return [check("생성 시각을 time.time() 과 같은 초로 읽는다",
                  started is not None and time.time() - 3600 < started <= time.time(),
                  f"{time.time() - started:.1f}초 전" if started else "읽지 못함")]


def check_exe(browser: bool) -> list[bool]:
    log.info("▶ 빌드본 열어 보기 (실행하지 않는다): %s", EXE)
    if not EXE.is_file():
        return [check("빌드본이 있다", False, str(EXE))]
    from PyInstaller.archive.readers import CArchiveReader

    toc = CArchiveReader(str(EXE)).toc
    names = [name.replace("\\", "/").lower() for name in toc]
    unpacked = sum(entry[2] for entry in toc.values() if entry[4] in ("b", "x"))
    mail = any(name.startswith("playwright/") for name in names)
    out = [check("★ 켤 때 풀리는 칸에 node.exe 가 없다", "playwright/driver/node.exe" not in names),
           check("AVIF 모듈이 없다", not any(name.startswith("pil/_avif") for name in names)),
           check("켤 때 푸는 양", unpacked < 70e6, f"{unpacked / 1e6:.1f}MB (09-28 빌드 153MB)")]
    if not mail:
        # 메일 없는 기능 고정 빌드 (`pw_driver.needed`) — Playwright 도 리소스도 없어야 한다
        with _datafile(EXE) as module:
            out.append(check("메일 없는 빌드 — Playwright·node.exe 리소스가 없다", pw_driver.resource(module) is None))
        return out
    out.append(check("Playwright 패키지(cli.js)는 남아 있다", "playwright/driver/package/cli.js" in names))
    work = Path(tempfile.mkdtemp(prefix="probe_exe_"))
    try:
        with _datafile(EXE) as module:
            blob = pw_driver.resource(module)
            out.append(check("★ exe 리소스에 node.exe 가 있다", blob is not None,
                             f"{len(blob) / 1e6:.1f}MB" if blob is not None else ""))
            if blob is None:
                return out
            node = pw_driver.ensure(work, blob)
            del blob                                # 라이브러리를 닫기 전에 놓는다
        package = Path(pw_driver.__file__).resolve().parents[1] / ".venv/Lib/site-packages/playwright/driver/node.exe"
        out.append(check("꺼낸 node.exe = 패키지의 node.exe", _sha(node) == _sha(package)))
        if browser:
            out.append(_check_browser(node))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


@contextmanager
def _datafile(exe: Path):
    """exe 를 실행하지 않고 데이터·리소스로만 연다."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    module = kernel32.LoadLibraryExW(str(exe), None, 0x2 | 0x20)   # AS_DATAFILE | AS_IMAGE_RESOURCE
    try:
        yield module
    finally:
        kernel32.FreeLibrary(module)


def _check_browser(node: Path) -> bool:
    from playwright.sync_api import sync_playwright

    os.environ[pw_driver.ENV] = str(node)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content("<title>probe</title>")
            title = page.title()
            browser.close()
        return check("★ 꺼낸 node.exe 로 Edge 를 띄운다 (창 없이, 빈 페이지)", title == "probe")
    finally:
        os.environ.pop(pw_driver.ENV, None)


def _sha(path: Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest()


def main() -> int:
    setup_logging()
    results = (check_driver() + check_hand_over() + check_entry() + check_instance() + check_crashlog()
               + check_selfcheck() + check_clock())
    if "--exe" in sys.argv:
        global EXE
        after = sys.argv[sys.argv.index("--exe") + 1:]
        if after and not after[0].startswith("--"):
            EXE = Path(after[0]).resolve()          # 다른 폴더에 빌드한 것
        results += check_exe("--browser" in sys.argv)
    failed = results.count(False)
    log.info("결과: %d건 중 실패 %d건", len(results), failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
