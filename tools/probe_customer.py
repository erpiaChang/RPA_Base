r"""[확인 도구 — 대상 프로그램을 건드리지 않는다] 업체 전용 빌드의 **끼움 자리** (10-08, `config/customer.py`·`docs/CUSTOMERS.md`).

    .venv\Scripts\python.exe -m tools.probe_customer

업체 이름 없는 **가짜 업체**(`probefake`)를 메모리에만 만들어 본다 — git 밖 업체 패키지가 없어도 돈다.
설정은 불러오는 순간 업체를 정하므로, 업체가 있는 경우는 자식 프로세스에서 본다 (환경변수 `RPA_CUSTOMER`).
업체 한 곳의 화면 시험은 git 밖 `tools/local/probe_customer_<id>` 다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types

FAKE = "probefake"
CHILD = "--child"
passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def _fake_package() -> None:
    """`customers.probefake` 를 메모리에 — 칸 둘, 원본 기능 둘, 업체 기능 하나 (단계 둘).
    `config` 를 불러오면 설정이 곧바로 업체를 찾는다 — PROFILE 은 처음 읽힐 때 만든다 (모듈 `__getattr__`)."""

    def modules_factory():
        from orchestrator.modules import Module
        from orchestrator.steps import SAFE, WRITE, Step

        def run(target, hooks, dry_run):
            return {"dry_run": dry_run}, f"가짜 기능 ({'시험' if dry_run else '실행'})"

        return (Module("cx_fake", "가짜 기능", erpia=True,
                       steps=(Step("cx_fake_open", "열기", SAFE), Step("cx_fake_do", "처리", WRITE)), run=run),)

    def rehearse(area: str) -> bool:
        from config.settings import SETTINGS
        return getattr(SETTINGS, "cx_go", None) != "실행"

    package = types.ModuleType("customers")
    package.__path__ = []
    fake = types.ModuleType(f"customers.{FAKE}")

    def profile(name: str):
        if name != "PROFILE":
            raise AttributeError(name)
        from config.customer import Field, Profile
        fake.PROFILE = Profile(
            FAKE, "가짜업체",
            fields=(Field("cx_go", "처리 방식", "choice", "시험", ("시험", "실행")),
                    Field("cx_codes", "코드 목록", "list", [])),
            base=("orders", "logistics_wait"), modules_factory=modules_factory, rehearse=rehearse, hold=False)
        return fake.PROFILE

    fake.__getattr__ = profile
    sys.modules["customers"] = package
    sys.modules[f"customers.{FAKE}"] = fake


def child() -> int:
    os.environ["RPA_CUSTOMER"] = FAKE
    _fake_package()
    from tools import probe_guard
    probe_guard.offline()
    from config import customer, settings
    from orchestrator import erpia_flow, full_flow, modules, remote

    check("업체 읽힘", settings.CUSTOMER is not None and settings.CUSTOMER.id == FAKE)
    check("칸이 DEFAULTS 에", settings.DEFAULTS.get("cx_go") == "시험" and settings.DEFAULTS.get("cx_codes") == [])
    check("칸의 처음 값", settings.SETTINGS.cx_go == "시험")
    check("목록 기본값은 복사", settings.SETTINGS.cx_codes is not settings.DEFAULTS["cx_codes"])
    check("모르는 키 경고 없이 저장 가능 — DEFAULTS 에 있다", "cx_codes" in settings.DEFAULTS)
    check("기능 = 원본 중 고른 것 + 업체 기능", modules.IDS == ("orders", "logistics_wait", "cx_fake"), str(modules.IDS))
    plan = [s.id for s in modules.plan(modules.normalize(list(modules.IDS)), ["excel"])]
    check("업체 단계는 원본 뒤", plan[-2:] == ["cx_fake_open", "cx_fake_do"] and plan[:2] == ["launch", "login"], str(plan))
    only = [s.id for s in modules.plan(modules.normalize(["cx_fake"]), [])]
    check("업체 기능만 — 실행·로그인이 그 앞에", only == ["launch", "login", "cx_fake_open", "cx_fake_do"], str(only))
    check("모르는 기능은 막는다", _raises(lambda: modules.normalize(["logistics"]), ValueError))
    check("멈춘 곳부터 다시 — 업체 단계", modules.resume_from(
        {"state": "failed", "modules": ["가짜 기능"], "steps": [{"id": "cx_fake_do", "state": "failed"}]}) == ["cx_fake"])

    settings.SETTINGS.cx_go = "시험"
    check("시험 — 업체가 영역마다", all(full_flow.rehearsed(a, False) for a in ("collect", "sales", "cx_fake")))
    settings.SETTINGS.cx_go = "실행"
    check("실행 — 누른다", not any(full_flow.rehearsed(a, False) for a in ("collect", "sales", "cx_fake")))
    check("실행 전체가 시험 실행이면 늘 시험", full_flow.rehearsed("sales", True))
    settings.SETTINGS.cx_go = "시험"

    check("원격 키에 업체 칸", {"cx_go", "cx_codes"} <= set(remote.REMOTE_KEYS))
    fields = remote.CUSTOMER_FIELDS
    check("고르는 칸 — 아는 값", remote.acceptable("cx_go", "실행") and remote.acceptable("cx_go", None))
    check("고르는 칸 — 모르는 값", not remote.acceptable("cx_go", "아무거나"))
    check("목록 칸", remote.acceptable("cx_codes", ["a", "b"]) and not remote.acceptable("cx_codes", "a"))
    check("목록 칸 상한", not remote.acceptable("cx_codes", ["x"] * (remote.CX_ITEMS + 1)))
    check("모르는 업체 칸은 받지 않는다", not remote.acceptable("cx_nope", "x"))
    check("칸 정의", set(fields) == {"cx_go", "cx_codes"})
    told = remote.described()
    check("웹이 그릴 것", told.get("customer") == "가짜업체" and [f["key"] for f in told["fields"]] == ["cx_go", "cx_codes"]
          and ["cx_fake", "가짜 기능"] in told["modules"], str(told))
    box = remote.Remote("http://x", "k", "b", "m", locked_modules=None, post=lambda *a: (200, "{}"))
    box._take({"commands": [{"id": 1, "kind": "settings", "settings": {"cx_go": "몰라", "cx_codes": ["c1"]}}]})
    event = box.inbox.get_nowait()
    check("웹이 보낸 틀린 업체 값은 버린다", event[2] == {"cx_codes": ["c1"]}, str(event))
    box.reply_settings(7, {"cx_go": "실행", "login_password": "x"})
    sent = box._results[-1]
    check("설정 답 — 업체 칸·정의, 비밀번호 없음",
          sent["settings"].get("cx_go") == "실행" and "login_password" not in sent["settings"] and sent.get("fields"))

    from gui import run_app

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    go, codes = settings.CUSTOMER.fields
    check("화면 → 값 (고르는 칸)", run_app._customer_value(go, Var("실행")) == "실행"
          and run_app._customer_value(go, Var("엉뚱")) is None)
    check("화면 → 값 (목록 칸)", run_app._customer_value(codes, Var("a, b")) == ["a", "b"])
    var = Var("")
    run_app._set_customer_var(codes, var, ["a", "b"])
    check("값 → 화면 (목록 칸)", run_app._split_codes(var.get()) == ["a", "b"])

    # 물류대기 — 보류 없이 저장만 (`logistics_wait.run(hold=False)`)
    from automation import logistics_wait
    calls: list[str] = []
    saved = {name: getattr(logistics_wait, name) for name in
             ("menu_available", "open_screen", "hold_shortage_items", "save_general", "go_to_logistics")}
    try:
        logistics_wait.menu_available = lambda target: True
        logistics_wait.open_screen = lambda target: "screen"
        logistics_wait.hold_shortage_items = lambda *a, **k: calls.append("hold") or {}
        logistics_wait.save_general = lambda screen, dry_run=False, info=None: (info or {}).update(
            exact=True, before=3) or calls.append("save") or 3
        logistics_wait.go_to_logistics = lambda target: calls.append("logistics")
        result = logistics_wait.run(types.SimpleNamespace(pid=1, main_window=lambda: None), dry_run=True, hold=False)
        check("보류 없이 — 보류·물류처리 안 함", calls == ["save"], str(calls))
        check("보류 없음 표시", result.get("no_hold") and result["saved"] == 3)
        check("요약에 '보류 안 함'", erpia_flow.NO_HOLD in erpia_flow.hold_detail(result, False))
        calls.clear()
        logistics_wait.run(types.SimpleNamespace(pid=1, main_window=lambda: None), dry_run=True, hold=True)
        check("원본은 그대로 — 보류·저장·물류처리", calls == ["hold", "save", "logistics"], str(calls))
    finally:
        for name, value in saved.items():
            setattr(logistics_wait, name, value)

    check("잘못된 칸 정의는 막는다", _raises(lambda: _load_bad(customer), ValueError))
    check("업체 id 꼴", _raises(lambda: customer.load("../x"), ValueError))
    print(f"[child] {passed} {failed}")
    return 1 if failed else 0


def _load_bad(customer) -> None:
    bad = types.ModuleType("customers.probebad")
    bad.PROFILE = customer.Profile("probebad", "x", fields=(customer.Field("nocx", "x", "text"),))
    sys.modules["customers.probebad"] = bad
    customer.load("probebad")


def _raises(fn, kind) -> bool:
    try:
        fn()
    except kind:
        return True
    return False


def parent() -> int:
    os.environ.pop("RPA_CUSTOMER", None)
    from tools import probe_guard
    probe_guard.offline()
    from config import customer, settings
    from orchestrator import modules, remote
    from utils import dialogs
    from automation import menu

    if settings.CUSTOMER is not None:
        print(f"  (이 개발 폴더는 설정에 업체 '{settings.CUSTOMER.id}' 가 있다 — 원본 검사는 건너뛴다)")
    else:
        check("업체 없음 — 원본 기능 넷", modules.IDS == ("mail", "orders", "logistics_wait", "logistics"))
        check("업체 없음 — 원격 키에 업체 칸 없음", not any(k.startswith("cx_") for k in remote.REMOTE_KEYS))
        check("업체 없음 — 웹이 그릴 것 없음", remote.described() == {})
    check("customer 는 고정 키", "customer" in settings.LOCKED_KEYS)
    check("빌드본은 환경변수를 보지 않는다", customer.dev_override(True) == "")
    check("업체 없음 = None", customer.load("") is None and customer.load(None) is None)

    # 팝업 모두 [예] (`dialogs.confirm_all`)
    class Button:
        def __init__(self, name):
            self.name = name
            self.element_info = types.SimpleNamespace(automation_id="btn_" + name)

        def window_text(self):
            return self.name

    class Popup:
        def __init__(self, body, *names):
            self.body, self.buttons = body, [Button(n) for n in names]

        def window_text(self):
            return ""

    queue: list = []
    pressed: list[str] = []
    saved = dialogs._message_box, dialogs.text_of
    from utils import ui
    ui_saved = ui.search, ui.click
    try:
        dialogs._message_box = lambda window: queue[0] if queue else None
        dialogs.text_of = lambda popup: popup.body
        ui.search = lambda popup, **k: popup.buttons if isinstance(popup, Popup) else []
        ui.click = lambda button, what, **k: (pressed.append(button.name), queue.pop(0))
        queue += [Popup("넘기시겠습니까?", "예(Y)", "아니오(N)"), Popup("완료", "확인(O)")]
        texts = dialogs.confirm_all(object(), "시험", quiet=0.1, timeout=5)
        check("[예] 를 고르고 다음은 [확인]", pressed == ["예(Y)", "확인(O)"], str(pressed))
        check("넘긴 글", len(texts) == 2 and "넘기시겠습니까" in texts[0], str(texts))
        queue.append(Popup("물음", "아니오(N)"))
        check("[예]/[확인] 이 없으면 멈춤", _raises(lambda: dialogs.confirm_all(object(), "시험", quiet=0.1, timeout=5),
                                              dialogs.DialogStuck))
        queue.clear()
        pressed.clear()
        busy = iter([True, True, False] + [False] * 100)
        check("바쁜 동안은 끝나지 않는다", dialogs.confirm_all(object(), "시험", busy=lambda: next(busy),
                                                      quiet=0.1, timeout=5) == [])
    finally:
        dialogs._message_box, dialogs.text_of = saved
        ui.search, ui.click = ui_saved

    check("메뉴 이름 글자 그대로", menu.literal_keys("A+B (1)") == "A{+}B {(}1{)}")

    # 서버 SQL·웹 — 업체 기능·칸을 받는 자리 (실서버 적용은 승인 뒤)
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sql = (root / "server" / "schema.sql").read_text(encoding="utf-8")
    check("SQL — 기능 목록은 modules_ok", "when 'run_modules' then private.modules_ok(v_val, 1)" in sql)
    check("SQL — 업체 칸 cx_", "private.cx_value_ok(v_val)" in sql and "'^cx_[a-z0-9_]{1,30}$'" in sql)
    check("SQL — 빌드 등록에 업체", "register_build(account_name text, build_name text, customer text default null)" in sql
          and "drop function if exists public.register_build(text, text);" in sql)
    web = (root / "web" / "app.js").read_text(encoding="utf-8")
    check("웹 — 업체 기능 이름은 PC 가 보낸 것", "rememberModules(snap.modules)" in web and "snap.fields" in web)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "tools.probe_customer", CHILD], capture_output=True, text=True,
                         encoding="utf-8", env=env, cwd=str(root), timeout=300)
    for line in out.stdout.splitlines():
        if line.startswith("  FAIL"):
            print(line)
    tail = [line for line in out.stdout.splitlines() if line.startswith("[child]")]
    if not tail:
        print(out.stdout[-2000:], out.stderr[-2000:])
        check("자식 프로세스", False)
    else:
        _, ok, bad = tail[-1].split()
        global passed, failed
        passed += int(ok)
        failed += int(bad)
    print(f"probe_customer: {passed} 통과, {failed} 실패")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(child() if CHILD in sys.argv else parent())
