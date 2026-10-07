r"""[확인 도구 - 읽기 전용] 빌드 프로그램(`gui/build_app.py`)과 기능 고정 빌드(`run_modules_locked`).

    .venv\Scripts\python.exe -m tools.probe_build

창은 만들되 띄우지 않고(withdraw), 저장·빌드는 가짜로 바꾼다. 설정 파일을 쓰지 않고
PyInstaller 를 돌리지 않는다. 대상 프로그램을 건드리지 않는다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from config import settings as settings_mod  # noqa: E402
from config.settings import DEFAULTS, SETTINGS  # noqa: E402
from gui import build_app  # noqa: E402
from orchestrator import modules  # noqa: E402
from tools import build_run  # noqa: E402
from utils import schedule  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

# 가짜 값이다 — 실제 경로가 아니고 어디에도 쓰지 않는다 (칸에 들어가는지만 본다)
SAMPLE = {
    "login_company_code": "c", "login_user_id": "u", "target_exe_name": "App.exe",
    "login_password": "p", "run_modules": ["mail", "orders", "logistics_wait", "logistics"],
    "run_modules_locked": False, "collect_sources": ["excel", "site"],
    "delivery_company": "택배사A", "delivery_box": "박스A", "logistics_mode": "수동",
    "hold_exclude_codes": ["8800000000006"], "excel_passwords": {"사이트B": "0000"},
    "mail_url": "https://x", "mail_user_id": "m", "mail_password": "mp",
    "mail_days_back": 3, "sms_source": "adb",
    "auto_run_times": ["평일 09:00 mail,orders", "매월 25일 18:00"],
    "auto_run_interval_minutes": 120,
    # 7절 (09-28). `server_url` 이 비어 있으면 `on_build` 이 서버 등록을 건너뛴다 — 가짜 빌드는 그 길로 간다
    "build_account_name": "테스트업체", "build_label": "사무실 PC",
    "build_admin_email": "admin@example.com", "build_admin_password": "pw",
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def check_fields() -> list[bool]:
    log.info("▶ 화면의 칸이 설정 항목과 맞는가")
    shown = set(build_app.KINDS)
    expected = set(DEFAULTS) - set(build_app.SKIP_KEYS)
    return [check("설정 항목 전부에 칸이 있다 (개발 전용 2개 제외)", expected <= shown,
                  f"빠짐 {sorted(expected - shown)}"),
            check("설정에 없는 칸이 없다", shown <= set(DEFAULTS), f"{sorted(shown - set(DEFAULTS))}"),
            check("빌드 도구 산출물 이름이 spec 과 같다", build_run.EXE.name == "RPA_1.exe")]


def check_parse() -> list[bool]:
    log.info("▶ 칸의 글 ↔ 설정값")
    out = [check("목록: `;` 로 나눈다 (예약 줄의 쉼표는 살린다)",
                 build_app.parse_value("auto_run_times", "평일 09:00 mail,orders ; 매월 25일 18:00")
                 == ["평일 09:00 mail,orders", "매월 25일 18:00"]),
           check("사전: `이름=값;`", build_app.parse_value("excel_passwords", "a=1; b=2=3")
                 == {"a": "1", "b": "2=3"}),
           check("숫자", build_app.parse_value("mail_days_back", "-1") == -1),
           check("빈 칸은 None", build_app.parse_value("mail_user_id", "  ") is None)]
    try:
        build_app.parse_value("mail_days_back", "abc")
        out.append(check("숫자 칸에 글자면 칸 이름을 든 오류", False))
    except ValueError as exc:
        out.append(check("숫자 칸에 글자면 칸 이름을 든 오류", "mail_days_back" in str(exc), str(exc)))
    try:
        build_app.parse_value("excel_passwords", "사이트B")
        out.append(check("사전 칸에 `=` 이 없으면 오류", False))
    except ValueError as exc:
        out.append(check("사전 칸에 `=` 이 없으면 오류", "이름=값" in str(exc)))
    for key, value in (("mail_sites", ["A", "B"]), ("excel_passwords", {"x": "1"}), ("mail_days_back", 7)):
        out.append(check(f"되돌리기 — {key}", build_app.parse_value(key, build_app.format_value(key, value)) == value))
    return out


def _wait_build(window, root) -> None:
    """가짜 빌드 스레드가 끝나기를 기다린다 — 상한 100박자."""
    # 확인 도구다. tk 의 `after` 박자를 흉내 내는 짧은 폴링이고 위 range 가 상한이다 — UI 상태로
    # 조건 대기할 대상이 없다 (가짜 빌드는 스레드 한 개뿐).
    for _ in range(100):
        root.update()
        if not window.building:
            return
        time.sleep(0.02)


def check_window() -> list[bool]:
    log.info("▶ 빌드 창 — 값 채우기 · 저장 · 빌드")
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return []
    root.withdraw()
    out = []
    saved: dict = {}

    def saver(**values):
        saved.clear()
        saved.update(values)
        return Path("fake/settings.local.json")

    def builder(on_line):
        on_line("가짜 PyInstaller 한 줄")
        return Path("dist/run/RPA_1.exe")

    try:
        window = build_app.BuildWindow(root, saver=saver, builder=builder)
        window.load(SAMPLE)
        out.append(check("값이 칸에 들어간다", window.vars["mail_url"].get() == SAMPLE["mail_url"]
                         and window.vars["excel_passwords"].get() == "사이트B=0000"
                         and window.vars["logistics_mode"].get() == "수동"
                         and window.vars["auto_run_times"].get().startswith("평일 09:00 mail,orders")))
        out.append(check("기능·수집방식 체크가 값대로", all(v.get() for v in window.module_vars.values())
                         and window.site_var.get() and window.excel_var.get()))
        window.vars["run_modules_locked"].set(True)
        window.module_vars["mail"].set(False)
        window.site_var.set(False)
        out.append(check("[저장] — 기능 고정 + 고른 기능 + 파싱된 값이 저장된다",
                         window.on_save() and saved["run_modules_locked"] is True
                         and saved["run_modules"] == ["orders", "logistics_wait", "logistics"]
                         and saved["collect_sources"] == ["excel"]
                         and saved["excel_passwords"] == {"사이트B": "0000"}
                         and saved["mail_days_back"] == 3 and saved["window_title_re"] is None,
                         str({k: saved.get(k) for k in ("run_modules", "collect_sources")})))
        for var in window.module_vars.values():
            var.set(False)
        out.append(check("기능을 하나도 안 고르면 저장하지 않고 알린다",
                         not window.on_save() and "기능" in window.status_var.get()))
        window.module_vars["orders"].set(True)
        window.on_build()
        _wait_build(window, root)
        text = window.log_text.get("1.0", "end")
        out.append(check("[저장하고 빌드] — 저장 뒤 빌드가 돌고 줄이 로그에, 결과가 상태줄에",
                         not window.building and "가짜 PyInstaller 한 줄" in text
                         and "빌드 완료" in window.status_var.get(), window.status_var.get()))

        def broken(on_line):
            raise build_run.BuildError("구울 값이 비어 있다: login_user_id")

        window.builder = broken
        window.on_build()
        _wait_build(window, root)
        out.append(check("빌드 실패는 이유와 함께 상태줄에 (단추가 다시 살아난다)",
                         "빌드 실패" in window.status_var.get() and "login_user_id" in window.status_var.get()
                         and str(window.build_btn.cget("state")) == "normal", window.status_var.get()))
    finally:
        root.destroy()
    return out


def check_locked_run_window() -> list[bool]:
    log.info("▶ 실행 창 — 기능 고정 빌드")
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        log.info("  건너뜀  화면이 없어 창 확인을 못 한다: %s", exc)
        return []
    root.withdraw()
    from gui.run_app import RunWindow

    keep = {k: getattr(SETTINGS, k) for k in ("run_modules", "run_modules_locked", "auto_run_times")}
    baked_path = settings_mod.BAKED_SETTINGS_PATH
    out = []
    try:
        settings_mod.BAKED_SETTINGS_PATH = Path("fake/none.json")     # 구운 파일 없음 → 설정값
        SETTINGS.run_modules = ["orders", "logistics"]
        SETTINGS.run_modules_locked = True
        SETTINGS.auto_run_times = ["매일 09:00 mail", "평일 10:00 logistics"]
        window = RunWindow(root)
        out.append(check("고정 — 체크는 구운 기능이고 잠긴다",
                         window._checked_modules() == ["orders", "logistics"]
                         and all(str(c.cget("state")) == "disabled" for c in window.module_checks)))
        out.append(check("고정 — 실행 창 설정에 run_modules 를 쓰지 않는다",
                         "run_modules" not in window._saved_values("C:/fake/erp.exe", "c", "u", "p")))
        out.append(check("고정 — 예약의 기능 선택도 구운 기능만",
                         tuple(k for k, _ in window.autorun_pane.choices) == ("orders", "logistics")))
        # 예약 줄은 창이 `from_settings(known_modules=구운 기능)` 으로 읽는다 — 다른 기능이 붙은
        # 줄은 버리고 `problems()` 에 남는다 (예약이 켜져 있으면 상태줄이 그것을 보인다)
        initial = schedule.from_settings(SETTINGS.as_dict(), known_modules=window._known_modules())
        out.append(check("고정 — 예약 줄의 다른 기능(mail)은 버리고 문제 목록에 알린다",
                         any("mail" in p for p in initial.problems())
                         and initial.times == ("평일 10:00 logistics",),
                         f"{initial.problems()} / {initial.times}"))
        window._set_busy(True)
        window._set_busy(False)
        out.append(check("고정 — 실행이 끝나 입력을 풀어도 기능 체크는 잠긴 채",
                         all(str(c.cget("state")) == "disabled" for c in window.module_checks)))
        out.append(check("고정 — 도움말이 고정이라고 말한다",
                         any("고정" in note.cget("text") for note in window.notes)))
        root.destroy()

        root = tk.Tk()
        root.withdraw()
        SETTINGS.run_modules_locked = False
        window = RunWindow(root)
        out.append(check("선택 — 체크가 열려 있고 run_modules 를 저장한다",
                         all(str(c.cget("state")) == "normal" for c in window.module_checks)
                         and "run_modules" in window._saved_values("C:/fake/erp.exe", "c", "u", "p")
                         and len(window.autorun_pane.choices) == len(modules.ALL)))
    finally:
        for k, v in keep.items():
            setattr(SETTINGS, k, v)
        settings_mod.BAKED_SETTINGS_PATH = baked_path
        try:
            root.destroy()
        except tk.TclError:
            pass        # 위에서 이미 닫았다
    return out


def check_locked_keys() -> list[bool]:
    """고정값은 exe 옆 설정이 못 덮는다 (09-28 `LOCKED_KEYS`). 임시 파일로 구운 값·로컬 값을 흉내 낸다."""
    log.info("▶ 고정값 잠금 — 구운 값을 로컬 설정이 못 덮는다")
    import json
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="probe_locked_"))
    baked, local = tmp / "baked.json", tmp / "local.json"
    baked.write_text(json.dumps({"login_company_code": "REAL", "delivery_company": "구운택배"},
                                ensure_ascii=False), encoding="utf-8")
    # 잠긴 키 하나 + 안 잠긴 키 하나 (대조군)
    local.write_text(json.dumps({"login_company_code": "WRONG", "delivery_company": "로컬택배"},
                                ensure_ascii=False), encoding="utf-8")
    keep = settings_mod.BAKED_SETTINGS_PATH, settings_mod.LOCAL_SETTINGS_PATH
    try:
        settings_mod.BAKED_SETTINGS_PATH, settings_mod.LOCAL_SETTINGS_PATH = baked, local
        loaded = settings_mod._load()
        ignored = list(settings_mod.LOCKED_IGNORED)
    finally:
        settings_mod.BAKED_SETTINGS_PATH, settings_mod.LOCAL_SETTINGS_PATH = keep
        settings_mod._load()                     # LOCKED_IGNORED 를 원래 상태로
        for path in (baked, local):
            path.unlink(missing_ok=True)
        tmp.rmdir()
    return [check("★ 잠긴 키(업체코드)는 구운 값이 이긴다", loaded.login_company_code == "REAL",
                  str(loaded.login_company_code)),
            check("대조군 — 안 잠긴 키(택배사)는 로컬 값이 이긴다", loaded.delivery_company == "로컬택배",
                  str(loaded.delivery_company)),
            check("막은 키를 모아 둔다 (로그가 열린 뒤 남기려고)", ignored == ["login_company_code"], str(ignored)),
            check("실행 창이 로그를 연 뒤 그것을 남긴다",
                  "log_locked_ignored()" in Path(build_app.__file__).with_name("run_app.py")
                  .read_text(encoding="utf-8"))]


def check_register() -> list[bool]:
    """빌드를 서버에 등록해 빌드 ID 를 받는다 (09-28). 가짜 응답을 주입해 실서버를 부르지 않는다."""
    log.info("▶ 빌드 등록 — 관리자 로그인 → register_build")
    from tools import register_build

    calls: list[tuple[str, dict]] = []

    def fake(url: str, headers: dict, payload: dict) -> tuple[int, str]:
        calls.append((url, payload))
        if url.endswith("/token?grant_type=password"):
            return 200, '{"access_token":"tok"}'
        return 200, '"bld_' + "z" * 43 + '"'

    build_id = register_build.register("https://x.supabase.co/", "anon", "a@b.c", "pw",
                                       "업체", "사무실 PC", post=fake)
    out = [check("등록 왕복 — 로그인 뒤 업체·빌드 이름을 보내고 bld_ 를 받는다",
                 build_id.startswith("bld_") and len(calls) == 2
                 and calls[1][1] == {"account_name": "업체", "build_name": "사무실 PC"},
                 str(calls[1][1])[:60])]

    def forbidden(url: str, headers: dict, payload: dict) -> tuple[int, str]:
        return (200, '{"access_token":"tok"}') if "token" in url else (403, '{"message":"forbidden"}')

    try:
        register_build.register("https://x", "anon", "a@b.c", "pw", "업체", "PC", post=forbidden)
        out.append(check("관리자가 아니면 막힌다", False))
    except register_build.RegisterError as exc:
        out.append(check("관리자가 아니면 막힌다", "관리자" in str(exc), str(exc)[:50]))

    try:
        register_build.register("https://x", "anon", "", "pw", "업체", "PC", post=fake)
        out.append(check("빈 칸이 있으면 부르기 전에 막는다", False))
    except register_build.RegisterError as exc:
        out.append(check("빈 칸이 있으면 부르기 전에 막는다", "비어 있습니다" in str(exc), str(exc)[:50]))
    return out


def check_baked_required() -> list[bool]:
    """굽기 전 막기와 실행 창의 알림이 같은 목록 — 기능 고정이면 그 기능 것만 (10-02 검토)."""
    from orchestrator import modules
    from tools import bake_settings

    mail_left = [k for k in modules.BAKED_MAIL if k not in ("mail_unread_only", "mail_days_back")]
    return [
        check("ERPia 만 고정한 빌드는 ERPia 값만 요구한다",
              bake_settings.missing({"run_modules_locked": True, "run_modules": ["orders"]})
              == list(modules.BAKED_ERPIA)),
        check("메일만 고정한 빌드는 메일 값만 — False·0 은 값이다 (읽은 메일도 / 오늘만)",
              bake_settings.missing({"run_modules_locked": True, "run_modules": ["mail"],
                                     "mail_unread_only": False, "mail_days_back": 0}) == mail_left),
        check("고정이 아니면 둘 다", set(bake_settings.missing({}))
              == set(modules.BAKED_ERPIA + modules.BAKED_MAIL)),
    ]


def check_version_and_selfcheck() -> list[bool]:
    """판 번호·빌드 직후 자가 점검 (10-07) — git·exe 는 가짜로 (빌드하지 않는다)."""
    import json
    import shutil
    import subprocess
    from datetime import date
    from types import SimpleNamespace

    log.info("▶ 판 번호 · 빌드 직후 자가 점검 (가짜 git·가짜 exe)")
    day = date(2026, 10, 7)

    def fake_git(dirty: str):
        def run(cmd, **kwargs):
            return SimpleNamespace(stdout="abc1234\n" if "rev-parse" in cmd else dirty)
        return run

    def no_git(cmd, **kwargs):
        raise OSError("git 없음")

    out = [check("판 번호 = 날짜-커밋 해시", build_run.make_version(fake_git(""), day) == "2026.10.07-abc1234"),
           check("커밋 안 된 변경이 있으면 끝에 +", build_run.make_version(fake_git(" M x.py"), day) == "2026.10.07-abc1234+"),
           check("git 이 없으면 날짜만", build_run.make_version(no_git, day) == "2026.10.07"),
           check("개발 폴더의 판은 dev", settings_mod.APP_VERSION == "dev", settings_mod.APP_VERSION)]

    exe = settings_mod.PROJECT_ROOT / "build" / "probe_fake_exe" / "RPA_1.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ fake")
    seen: list = []

    def fake_exe(result):
        def run(cmd, **kwargs):
            seen.append(cmd)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                Path(cmd[2]).write_text(json.dumps(result), encoding="utf-8")
            return SimpleNamespace(returncode=0 if result and result.get("ok") else 1)
        return run

    def outcome(result) -> str:
        try:
            build_run.selfcheck(exe, lambda line: None, run=fake_exe(result))
            return "통과"
        except build_run.BuildError as exc:
            return str(exc)

    try:
        good = {"ok": True, "version": "v", "steps": {"modules": {"ok": True, "detail": "10개"}}}
        bad = {"ok": False, "steps": {"modules": {"ok": False, "detail": "ImportError"}}}
        passed = outcome(good)
        cmd = seen[-1] if seen else []
        out.append(check("★ 임시 사본을 --selfcheck 로 켠다 (dist 의 exe 를 직접 켜지 않는다)", passed == "통과"
                         and cmd[1:2] == ["--selfcheck"] and Path(cmd[0]).parent == build_run.SELFCHECK_DIR, str(cmd)))
        out.append(check("점검이 끝나면 임시 사본을 지운다", not build_run.SELFCHECK_DIR.exists()))
        out.append(check("점검 실패면 빌드 실패 — 쓰지 말라고 알린다", "쓰지 말 것" in outcome(bad)))
        out.append(check("결과 없이 끝나면(켜자마자 죽음) 빌드 실패", "결과가 없다" in outcome(None)))
        out.append(check("시간을 넘기면 빌드 실패",
                         "끝나지 않았다" in outcome(subprocess.TimeoutExpired(["x"], 1))))
    finally:
        shutil.rmtree(exe.parent, ignore_errors=True)
    return out


def check_bats() -> list[bool]:
    """.bat 을 진짜 cmd 로 돌려 본다 (10-07). 다른 RPA 는 UTF-8 + chcp 65001 에서 cmd 가 한글 줄 뒤 줄 위치를 잘못 세어
    줄 조각을 명령으로 실행했다. 이 PC 에서는 재현되지 않아 파일은 그대로 두고, 고친 뒤 깨지면 여기서 잡는다.
    ★ 임시 폴더 사본 + PATH 는 System32 뿐 — .venv·python·npx 를 못 찾아 '없다' 분기에서 끝난다 (빌드·배포 안 됨)."""
    import os
    import shutil
    import subprocess
    import tempfile

    log.info("▶ .bat 실제 실행 — 한글 줄·종료 코드 (빌드·배포는 일어날 수 없다)")
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags, hidden.wShowWindow = subprocess.STARTF_USESHOWWINDOW, 0      # 새 콘솔을 숨긴다
    out = []
    for name, want in (("build_tool.bat", "[오류] .venv 가 없습니다."),
                       ("deploy_web.bat", "[실패] .env 의 SUPABASE_URL")):
        with tempfile.TemporaryDirectory(prefix="probe_bat_") as tmp:
            shutil.copy2(settings_mod.PROJECT_ROOT / name, Path(tmp) / name)
            done = subprocess.run([str(system32 / "cmd.exe"), "/c", str(Path(tmp) / name)], cwd=tmp,
                                  env={"SystemRoot": os.environ["SystemRoot"], "PATH": str(system32)},
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                                  creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=hidden)
        text = done.stdout.decode("utf-8", "replace")
        error = done.stderr.decode("utf-8", "replace")
        out.append(check(f"{name} — 한글 줄이 그대로 찍히고 줄 조각이 명령으로 새지 않는다",
                         want in text and "is not recognized" not in error, (text + error)[-160:]))
        out.append(check(f"{name} — 한글 줄 뒤 exit /b 1 까지 간다", done.returncode == 1, f"exit {done.returncode}"))
    return out


def main() -> int:
    setup_logging()
    from tools import probe_guard

    # 실행 창을 만들면 그것만으로 서버 확인이 나가고, 아직 안 묶인 빌드가 이 PC 에 묶인다 (09-28)
    with probe_guard.offline():
        results = [*check_fields(), *check_parse(), *check_window(), *check_locked_run_window(),
                   *check_register(), *check_locked_keys(), *check_baked_required(),
                   *check_version_and_selfcheck(), *check_bats()]
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
