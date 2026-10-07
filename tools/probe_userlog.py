r"""[확인 도구 - 읽기 전용] **사용자가 보는 글**이 사람 말인지 — 코드·id·경로가 새지 않는지.

ERPia·메일·휴대폰·브라우저를 건드리지 않는다. 통합 흐름(`full_flow`)이 부르는 바깥 함수를
가짜로 바꿔 여러 장면(정상 / 휴대폰 오프라인 / 택배사 틀림 / 로그인 실패 / 화면 요소 없음 /
dry-run)을 돌리고, 사용자가 보는 것 — 사용자 로그·상태줄·단계 한 줄·상세 표·실패 문구·
리포트 — 을 모아 **내부 값이 새는지** 본다. 가짜 자리는 전부 되돌린다.

    .venv\Scripts\python.exe -m tools.probe_userlog            확인만
    .venv\Scripts\python.exe -m tools.probe_userlog --write    logs/_sample_* 로도 남긴다 (검토용 — 보고 지운다)

사용자 요청 2026-09-21: "사용자는 긴 로그를 이해하지 못한다. 코드·id 는 빼고 상품명·엑셀명·건수로."
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from automation import logistics, logistics_wait  # noqa: E402
from automation import login as login_mod  # noqa: E402
from collect import auth_code, browser, manifest, phonelink, sites, storage, webmail  # noqa: E402
from gui.common import RecentLog  # noqa: E402
from orchestrator import erpia_flow, friendly, full_flow, report  # noqa: E402
from orchestrator.common import Hooks, Result  # noqa: E402
from utils import cancel, logger as logger_mod, ui  # noqa: E402
from utils.logger import USER_LOGGER, get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
SAMPLE_DIR = Path("logs")
EXCEL_BASE = (Path("logs") / "_sample_excel" / "2026-09-21").resolve()

# 사용자에게 보이면 안 되는 것. 파일·폴더 **링크 칸**(위치)은 사용자가 요청한 것이라 뺀다.
LEAKS = (
    (r"[A-Za-z]+_[A-Za-z]+", "auto_id·설정 키 꼴 (gridCtrl_, excel_passwords …)"),
    (r"\bpid\b|pid=", "pid"),
    (r"auto_id", "auto_id"),
    (r"config[/\\]|settings\.local", "설정 파일 경로"),
    (r"tools[./\\]|\.py\b|docs/", "개발 도구·문서"),
    (r"Traceback|[A-Z][a-zA-Z]+(?:Error|NotFound|Rejected|Disabled|Timeout)\b",
     "예외 이름"),
    (r"화면 기준|\d+행\b|(?<![가-힣])행 \d+", "그리드 행 수"),     # `3행` / `행 3` 둘 다
    (r"\b\d{13}\b", "상품코드(바코드)"),
    (r"Invoke|click_input|UIA", "조작 방법"),
    (r"dry-run", "영어 개발 용어 (시험 실행으로)"),
    (r"\*\*", "마크다운 강조 표시"),
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


# --- 가짜 --------------------------------------------------------------------
def _item(key, site, status, name, subject):
    # seq — 실제 매니페스트 항목은 차수를 가진다 (collect_flow 가 (메일 키, 차수) 로 새 항목을 가린다, 09-28)
    return SimpleNamespace(mail_key=key, seq=int(key), site=site, status=status,
                           path=f"2026-09-21_00{key}_{site}/{name}" if name else None,
                           mail_subject=subject)


MAILS = [
    _item("1", "사이트A", manifest.STATUS_DOWNLOADED, "사이트A_20260921.xlsx",
          "[사이트A] 9월 21일 주문 내역"),
    _item("2", "사이트C", manifest.STATUS_DOWNLOADED, "사이트C_20260921.xlsx",
          "[사이트C] 주문서 발송"),
    _item("3", "사이트B", manifest.STATUS_DOWNLOADED, "사이트B_20260921.xlsx",
          "[사이트B] 금일 주문"),
]
UPLOADS = [(m.site, EXCEL_BASE / m.path) for m in MAILS]
HOLD_ITEMS = [
    {"code": "8800000000006", "name": "상품A", "shortage": "3",
     "checked": 4, "already": 0, "state": "보류함"},
    {"code": "8801234567890", "name": "상품B", "shortage": "1",
     "checked": 1, "already": 2, "state": "보류함"},
    {"code": "8809876543210", "name": "상품C", "shortage": "2",
     "checked": 0, "already": 0, "state": "제외"},
]


class FakeTarget:
    pid = 4242
    started = True

    def main_window(self):
        return SimpleNamespace(window_text=lambda: "user01 - user01")


def _chained(outer, inner):
    try:
        raise inner
    except Exception as exc:     # noqa: BLE001 — 원인 사슬을 만들려는 것이다
        outer.__cause__ = exc
    return outer


def _uploads(dry_run: bool, scene: str = ""):
    """`upload_excels` 가짜. 실물처럼 파일마다 `on_item` 을 부른다.

    `popup_excel` 장면은 첫 파일을 올린 **뒤** 알림에 막힌다 — 올린 것이 결과에 남아야 한다."""
    def fake(screen, items, pid=None, dry_run=dry_run, on_item=None, on_uploaded=None):
        if scene == "popup_excel" and on_item is not None:
            site, path = items[0]
            on_item(0, len(items), site, None)
            first = {"site": site, "path": str(path), "ok": True, "dry_run": False, "attempts": 1, "added": 12,
                     "kind": None, "warning": "", "error": None}
            if on_uploaded is not None:
                on_uploaded(first)          # 실물처럼 올라간 순간 (10-02)
            on_item(1, len(items), site, first)
            raise logistics.ScreenError("두 번째 엑셀 [엑셀업로드] 를 누를 수 없다")
        added = {"사이트A": 12, "사이트C": 5}
        out = []
        for index, (site, path) in enumerate(items, start=1):
            if on_item:
                on_item(index - 1, len(items), site, None)
            ok = site != "사이트B"
            entry = {"site": site, "path": str(path), "ok": ok or dry_run,
                     "dry_run": dry_run, "attempts": 1 if ok else 2,
                     "added": None if dry_run or not ok else added[site],
                     "kind": None if ok else "rejected",
                     # 실물은 시험 실행에서 파일 머리(`needs_password`)로 이것을 채운다
                     "warning": "실제로 올릴 때 비밀번호가 필요한데 등록된 비밀번호가 없습니다"
                     if dry_run and not ok else "",
                     "error": None if ok or dry_run else (
                         "ScreenError: 엑셀에 비밀번호가 걸려 있는데 설정에 값이 없다 "
                         "(사이트 사이트B). config/settings.local.json 의 "
                         'excel_passwords 에 {"사이트B": "..."} 를 넣을 것.')}
            out.append(entry)
            if on_uploaded is not None and entry["ok"] and not dry_run:
                on_uploaded(entry)
            if on_item:
                on_item(index, len(items), site, entry)
        return out
    return fake


@contextmanager
def fakes(scene: str, dry_run: bool):
    """장면대로 가짜를 끼운다. **이름이 없으면 AttributeError** — 조용히 실물이 돌지 않는다."""
    saved: list[tuple[object, str, object]] = []

    def put(owner, name, value):
        saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def preflight():
        if scene == "phone_offline":
            raise _chained(auth_code.AuthCodeError("휴대폰이 '오프라인' 이다."),
                           phonelink.PhoneLinkError(
                               "휴대폰이 '오프라인' 이다. 대화 목록은 지난 동기화 내용이라 "
                               "**새 인증 문자를 받을 수 없다.** ConnectivityStatusTextBlock"))
        return "인증 경로: phonelink\n휴대폰 연결 앱: 휴대폰 연결 — 준비됨"

    @contextmanager
    def browser_page():
        yield object()

    empty_book = SimpleNamespace(items=[], succeeded=lambda: [], pending=lambda: [])
    mail_book = SimpleNamespace(items=list(MAILS), pending=lambda: [],
                                succeeded=lambda: [m for m in MAILS if m.status ==
                                                   manifest.STATUS_DOWNLOADED])

    def launch(exe, work_dir=None, status=None, alert=None):
        return FakeTarget()

    def do_login(company, user_id, password, pid=None):
        if scene == "login_rejected":
            raise login_mod.LoginRejected("아이디 또는 비밀번호가 올바르지 않습니다.",
                                          "잘못된 아이디 또는 비밀번호입니다.")
        return "user01 - user01"

    unsold = iter([17, 47])

    def sales(screen, main_window=None, dry_run=dry_run):
        if dry_run:
            return {"before": "47건", "after": "47건", "clicked": "[dry-run]",
                    "dialogs": [], "skipped": False}
        return {"before": "47건", "after": "3건", "clicked": "Invoke",
                "dialogs": ["정상매출처리 성공"], "skipped": False}

    def wait_run(target, dry_run=dry_run):
        if scene == "popup_continue":
            # 저장 실패 알림이 떠서 다음 조작이 막힌 꼴 — 흐름은 알림을 닫고 물류관리로 가야 한다
            raise logistics_wait.ScreenError("[물류처리] 를 누를 수 없다")
        if scene == "screen_missing":
            raise _chained(logistics_wait.ScreenError(
                "일반 탭의 그리드(gridCtrl_NormalDataLeft)를 찾지 못했다. "
                "tools/probe_screen.py 를 돌릴 것."),
                ui.ControlNotFound("auto_id='gridCtrl_NormalDataLeft'"))
        items = [dict(item) for item in HOLD_ITEMS]
        if dry_run:
            for item in items:
                item["state"] = "보류 예정" if item["state"] == "보류함" else item["state"]
        return {"shortage": len(items), "held": 2, "checked": 5, "skipped": 0,
                "excluded": ["8809876543210"], "verify": {}, "items": items,
                "saved": 0 if dry_run else 39, "saved_exact": not dry_run}   # 44 - 보류 5

    def logi_run(target, courier="", box="", mode="", dry_run=dry_run):
        if scene == "courier_missing":
            # 실물(`logistics._select_from_dropdown` ← `ui.click_dropdown_item`)과 같은 꼴
            seen = ("드롭다운에서 '택배' 을(를) 찾지 못했다.\n"
                    "  끝까지 내려 확인한 값 3개: ['택배사A', '택배사B', '택배사C']")
            raise _chained(logistics.ScreenError(
                f"택배사 '택배' 을(를) 드롭다운에서 찾지 못했다.\n  현재 값: ''\n  {seen}"),
                ui.ControlNotFound(seen))
        return {"orders": 34, "registered": 0 if dry_run else 34, "courier": "택배사A",
                "box": "박스A", "mode": "자동", "finish": "운송장출력",
                "save_clicked": not dry_run, "saved": not dry_run,
                "finish_clicked": not dry_run, "skipped": "",
                "slips": 36, "slips_exact": True,        # 주문 39줄 = 전표 36건 (여러 줄 전표 3)
                "slips_saved": None if dry_run else 36,
                "boxes": None if dry_run else 40, "boxes_exact": not dry_run,   # 개별배송이 나눈 박스(송장)
                "slips_left": None if dry_run else 0,
                **({"mode_not_restored": "수동"} if scene == "dry_mode_stuck" else {})}

    def counts(screen, failed_names=None):
        """`collect_counts_all` 가짜 — `(건수, 끝까지 봤나)`. 정상 장면은 끝까지 봤다."""
        if failed_names is not None:
            failed_names.append("몰A")
        return {"성공": 4, "실패": 1}, True

    # 일부러 낸 실패에 `step()` 이 **실제 화면**을 찍어 logs/ 에 남긴다 (09-21 첫 실행). 막는다.
    put(logger_mod, "save_screenshot", lambda tag: None)
    put(phonelink, "reset_retry_state", lambda: None)
    put(auth_code, "preflight", preflight)
    put(sites, "mail_site", lambda: object())        # 원본(git)에는 사이트가 없다 — 가짜 사이트 (10-02)
    put(browser, "browser_page", browser_page)
    put(webmail, "login", lambda page, site: None)
    # 실물처럼 시험 실행은 메일을 열지 않아 새 항목이 없다 (`webmail.collect` 의 dry_run)
    put(webmail, "collect", lambda page, site, dry_run=False: empty_book if dry_run else mail_book)
    put(manifest, "load", lambda: empty_book)
    put(storage, "date_dir", lambda create=False: EXCEL_BASE)
    put(full_flow, "_pending_uploads", lambda book: list(UPLOADS))
    def enter(target):
        if scene == "popup_screen":
            raise ui.ControlNotFound("auto_id='OrderMapping'")
        return object()

    put(full_flow, "enter_order_mapping", enter)
    # 실패 뒤 ERPia 알림을 닫는 창구 (`Hooks.recover`). `popup_*` 장면만 알림이 떠 있다.
    popup = "저장실패: 연락처를 입력하세요" if scene.startswith("popup_") else None
    put(erpia_flow, "popup_closer", lambda target: (lambda: popup))
    put(erpia_flow, "start_new_instance", launch)
    put(erpia_flow, "login", do_login)
    put(erpia_flow, "updater", SimpleNamespace(settle=lambda *a, **k: ""))
    put(erpia_flow, "upload_excels", _uploads(dry_run, scene))
    put(erpia_flow, "collect_orders", lambda *a, **k: 20)
    put(erpia_flow, "unsold_number", lambda screen: next(unsold))
    put(erpia_flow, "collect_counts_all", counts)
    put(erpia_flow, "process_sales", sales)
    put(erpia_flow, "logistics_wait", SimpleNamespace(run=wait_run))
    put(erpia_flow, "logistics", SimpleNamespace(run=logi_run))
    try:
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


# --- 한 장면 ----------------------------------------------------------------
def run_scene(scene: str, dry_run: bool = False) -> dict:
    lines: list[str] = []
    handler = logging.Handler()
    # 화면과 같은 꼴(`시각  글`). 화면은 경고를 주황, 오류를 빨강으로 칠한다 — 글로 적어 둔다.
    color = {logging.WARNING: "   ← 주황 글씨", logging.ERROR: "   ← 빨간 글씨"}
    handler.emit = lambda record: lines.append(
        f"{logging.Formatter('%(asctime)s', '%H:%M:%S').format(record)}  "
        f"{record.getMessage()}{color.get(record.levelno, '')}")
    user = logging.getLogger(USER_LOGGER)
    user.addHandler(handler)
    statuses: list[str] = []
    hooks = Hooks(status=statuses.append, explain=friendly.explain)
    options = erpia_flow.Options(exe="C:/fake/App.exe", company="c", user_id="u",
                                 password="p", sources=["excel", "site"],
                                 courier="택배사A", box="박스A", mode="자동",
                                 dry_run=dry_run, modules=None)
    failure = summary = ""
    try:
        with fakes(scene, dry_run):
            result = full_flow.run(options, hooks=hooks)
        summary = result.summary
        outcome = summary
    except Exception as exc:        # noqa: BLE001 — 장면이 일부러 실패시킨다
        failure = hooks.failure_text(exc)
        outcome = f"실패 — {failure}"
    finally:
        user.removeHandler(handler)
    steps = hooks.report()
    # 창(`gui/erpia_app._run_worker`)이 리포트에 싣는 것과 같게 싣는다
    details = {"attention": list(hooks.attention), "trigger": "직접 실행"}
    return {"scene": scene, "lines": lines, "statuses": statuses, "summary": summary,
            "failure": failure, "steps": steps, "attention": list(hooks.attention),
            "html": report.build(Result(summary=outcome, steps=steps, details=details),
                                 title="실행 리포트 (예시)")}


def visible_texts(got: dict) -> list[tuple[str, str]]:
    """사용자에게 보이는 글 전부 `(어디, 글)`. 링크 칸(파일·폴더)은 뺀다."""
    out = [("사용자 로그", line) for line in got["lines"]]
    out += [("상태줄", line) for line in got["statuses"]]
    out += [("요약", got["summary"]), ("실패 문구", got["failure"])]
    out += [("확인할 것", item) for item in got.get("attention") or []]
    for entry in got["steps"]:
        name = entry["name"]
        out += [(f"{name} 한 줄", entry.get("detail") or ""),
                (f"{name} 실패", entry.get("error") or "")]
        for row in entry.get("rows") or ():
            for column, value in zip(entry.get("columns") or (), row):
                if isinstance(value, report.Link) or column == "위치":
                    continue
                out.append((f"{name} 표 [{column}]", str(value)))
    return [(where, text) for where, text in out if text]


def leaks(got: dict) -> list[str]:
    found = []
    for where, text in visible_texts(got):
        for pattern, what in LEAKS:
            match = re.search(pattern, text)
            if match:
                found.append(f"{where}: {what} '{match.group(0)}' ← {text[:80]}")
    return found


def step_of(got: dict, step_id: str) -> dict:
    return next((entry for entry in got["steps"] if entry["id"] == step_id), {})


def sample_text(got: dict) -> str:
    """검토자에게 줄 글. 화면에 보이는 순서대로."""
    out = [f"# 장면: {got['scene']}", "", "## 진행 로그 (프로그램 창 아래 칸에 보이는 글)"]
    out += got["lines"] or ["(없음)"]
    out += ["", "## 상태줄 (창 위쪽 한 줄 — 바뀐 차례대로. 한 번에 한 줄만 보인다)"]
    shown = [line for index, line in enumerate(got["statuses"])
             if index == 0 or line != got["statuses"][index - 1]]
    # 끝나면 창이 상태줄을 바꾼다 (`erpia_app._on_finished` / `_on_failed`) — 그것도 적는다
    count = len(got.get("attention") or [])
    if got["failure"]:
        shown.append("(멈추면) 실패 — " + got["failure"].split("\n")[0])
    elif got["summary"]:
        shown.append("(끝나면) " + (f"확인할 것 {count}건 (리포트 맨 위)" if count
                                   else got["summary"]))
    out += shown or ["(없음)"]
    out += ["", "## 단계 표 (진행 중 화면 · 오버레이에 보이는 것)"]
    for entry in got["steps"]:
        state = {"done": "완료", "skipped": "건너뜀", "failed": "실패", "cancelled": "중단",
                 "pending": "실행 전", "running": "진행 중"}.get(entry["state"], entry["state"])
        out.append(f"- {entry['name']} [{state}] {entry.get('detail') or ''}")
        if entry.get("error"):
            out.append(f"    멈춘 이유: {entry['error']}".replace("\n", " / "))
    for entry in got["steps"]:
        if not entry.get("columns"):
            continue
        out += ["", f"## 상세 표 — {entry['name']}", " | ".join(entry["columns"])]
        for row in entry["rows"]:
            out.append(" | ".join(f"{v} (누르면 열림)" if isinstance(v, report.Link)
                                  else str(v) for v in row))
    out += ["", "## 확인할 것 (리포트 맨 위에 모이는 목록)"]
    out += [f"{index}. {item}" for index, item in enumerate(got.get("attention") or [], 1)] \
        or ["(없음)"]
    attention = got.get("attention") or []
    end = got["failure"] or got["summary"] or "(없음)"
    if attention and not got["failure"]:
        end = f"확인할 것 {len(attention)}건 (리포트 맨 위) / {end}"   # `_on_finished` 와 같다
    out += ["", "## 끝났을 때 보이는 결과 (결과 띠 · 알림 창 · 자동 실행 기록. / 는 줄바꿈)", end]
    return "\n".join(out) + "\n"


# --- 확인 -------------------------------------------------------------------
def check_friendly() -> list[bool]:
    log.info("▶ 실패 문구 규칙")
    step = SimpleNamespace(name="물류관리")
    out = []
    got = friendly.explain(logistics.ScreenError(
        "택배사 '택배' 을(를) 드롭다운에서 찾지 못했다.\n  현재 값: ''"), step)
    out.append(check("택배사 이름을 짚어 준다", "'택배'" in got and "[택배사]" in got, got))
    got = friendly.explain(ui.ControlNotFound("auto_id=x"), step)
    out.append(check("화면 요소 없음 → 단계 이름 + 할 일", got.startswith("[물류관리] 단계에서"), got))
    wrapped = _chained(auth_code.AuthCodeError("x"),
                       phonelink.PhoneLinkError("휴대폰이 '오프라인' 이다."))
    out.append(check("감싼 예외는 원인(휴대폰 오프라인)으로 설명", friendly.explain(wrapped)
                     == friendly.PHONE_OFFLINE))
    out.append(check("모르는 예외는 기본 문구(담당자에게 리포트)",
                     "담당자" in friendly.explain(ValueError("모르는 것"))))
    out.append(check("user_message 가 있으면 그것", friendly.explain(
        type("E", (RuntimeError,), {"user_message": "그대로"})()) == "그대로"))
    out.append(check("업로드 실패 — ERPia 팝업 본문을 옮긴다",
                     "ERPia 메시지: 헤더 불일치" in friendly.upload_reason(
                         "제목: x\n  내용: 헤더 불일치")))
    out.append(check("업로드 실패 — 비밀번호 없음은 담당자에게",
                     "담당자" in friendly.upload_reason("엑셀에 비밀번호가 걸려 있는데")))
    got = friendly.explain(logistics.ScreenError(
        "박스 '박스X' 을(를) 드롭다운에서 찾지 못했다.\n  드롭다운에서 '박스X' 을(를) 찾지 못했다.\n"
        "  끝까지 내려 확인한 값 3개: ['박스A', '박스B', '박스C']"), step)
    out.append(check("목록에 있는 이름을 같이 보여 준다", "목록에 있는 이름: 박스A, 박스B, 박스C" in got, got))
    from utils import crashlog

    from orchestrator import telemetry

    found = leaks({"lines": [crashlog.CRASH_TEXT, crashlog.START_FAIL_TEXT, telemetry.CERT_FAIL_TEXT],
                   "statuses": [], "summary": "", "failure": "", "steps": []})
    out.append(check("처리 안 된 예외·서버 인증서 알림 글에 내부 값이 없다 (10-07)", not found, str(found)))
    out += _check_no_browser()

    log.info("▶ 암호 걸린 엑셀 알아보기 (파일 머리 8바이트)")
    from automation.order_mapping import OLE_SIGNATURE, password_state
    out.append(check("xlsx + OLE 머리 = 암호", password_state(OLE_SIGNATURE, ".xlsx") is True))
    out.append(check("xlsx + ZIP 머리 = 암호 없음",
                     password_state(b"PK\x03\x04\x14\x00\x06\x00", ".xlsx") is False))
    out.append(check("xls 는 모른다 (원래 OLE)", password_state(OLE_SIGNATURE, ".xls") is None))
    out.append(check("알 수 없는 머리는 모른다", password_state(b"<html>..", ".xlsx") is None))
    return out


def _check_no_browser() -> list[bool]:
    """브라우저(Edge/Chrome)가 없을 때 (10-07) — Playwright 문구 대신 이름과 할 일. 진짜 브라우저는 안 띄운다."""
    log.info("▶ 브라우저 없음 (10-07)")
    import playwright.sync_api as pw_api

    from config.settings import SETTINGS

    got = friendly.explain(browser.BrowserError("브라우저 실행 파일이 없다: msedge"))
    out = [check("브라우저가 없으면 이름과 할 일 — 내부 이름 없이",
                 "Microsoft Edge" in got and "설치" in got and "msedge" not in got, got)]
    got = friendly.explain(browser.BrowserError("브라우저 종류(browser_channel)가 비었거나 틀렸다: ''"))
    out.append(check("브라우저가 안 정해진 것은 그대로 담당자 문의", "정해져 있지 않습니다" in got, got))

    class Chromium:
        def __init__(self, message):
            self.message = message

        def launch(self, **kwargs):
            raise pw_api.Error(self.message)

    def fake_playwright(message):
        @contextmanager
        def sync_playwright():
            yield SimpleNamespace(chromium=Chromium(message))
        return sync_playwright

    def launch_error(message):
        pw_api.sync_playwright = fake_playwright(message)
        try:
            with browser.browser_page():
                return None
        except Exception as exc:        # noqa: BLE001 — 무엇이 올라오는지 본다
            return exc

    keep = (pw_api.sync_playwright, SETTINGS.browser_channel)
    try:
        SETTINGS.browser_channel = "msedge"
        missing = launch_error("Chromium distribution 'msedge' is not found at C:\\가짜\\msedge.exe")
        other = launch_error("가짜 — 다른 실행 오류")
    finally:
        pw_api.sync_playwright, SETTINGS.browser_channel = keep
    out.append(check("실행 파일이 없다는 Playwright 오류만 BrowserError 로 바꾼다 (원인은 남긴다)",
                     isinstance(missing, browser.BrowserError) and missing.__cause__ is not None
                     and isinstance(other, pw_api.Error) and not isinstance(other, browser.BrowserError),
                     f"{missing!r} / {other!r}"))
    return out


def check_detector() -> list[bool]:
    """★ 계측기부터 — 새는 글을 **넣어 봤을 때** 잡는가. 못 잡으면 아래 '안 샌다' 는 뜻이 없다."""
    log.info("▶ 새는 글 감지기 자체")
    planted = {"lines": ["[3/8] 엑셀수집 실패 — ScreenError: gridCtrl_SiteList 행 3",
                         "pid=4242 로 연결"],
               "statuses": [], "summary": "config/settings.local.json 의 excel_passwords",
               "failure": "",
               "steps": [{"name": "물류대기", "detail": "상품 8800000000006 보류",
                          "error": "", "columns": ("상품명", "위치"),
                          # 위치 칸의 밑줄(a_b)은 새는 글 꼴과 같지만 **봐서는 안 된다**
                          "rows": [("상품A", "받은폴더/a_b")]}]}
    found = leaks(planted)
    kinds = {line.split(": ")[1].split(" '")[0] for line in found}
    return [check("넣어 둔 내부 값 5종을 잡는다",
                  {"예외 이름", "pid", "설정 파일 경로", "상품코드(바코드)",
                   "그리드 행 수"} <= kinds, str(sorted(kinds))),
            check("위치(링크) 칸은 보지 않는다", not any("위치" in line for line in found))]


def check_screen_filter() -> list[bool]:
    log.info("▶ 화면 로그는 사용자용만")
    recent = RecentLog()
    recent.attach()
    try:
        logging.getLogger("automation.x").info("gridCtrl_SiteList 행 3 클릭")
        logging.getLogger(USER_LOGGER).info("사람 말")
    finally:
        recent.detach()
    lines = recent.latest(5)
    return [check("오버레이 최근 로그에 내부 로그가 안 들어간다",
                  len(lines) == 1 and lines[0].endswith("사람 말"), str(lines))]


def check_scene(scene: str, dry_run: bool, expect) -> tuple[list[bool], dict]:
    log.info("▶ 장면 %s%s", scene, " (dry-run)" if dry_run else "")
    got = run_scene(scene, dry_run)
    found = leaks(got)
    out = [check("내부 값이 새지 않는다", not found, "; ".join(found[:5]))]
    for name, ok in expect(got):
        out.append(check(name, ok))
    return out, got


def expect_ok(got):
    excel = step_of(got, "excel")
    html = got["html"]
    yield "요약이 있고 실패가 없다", bool(got["summary"]) and not got["failure"]
    yield ("엑셀 — 파일마다 새 주문 수",
           any("사이트A_20260921.xlsx — 올림, 새 주문 12건" in l for l in got["lines"]))
    yield "엑셀 — 합계와 못 올린 사이트 (괄호 겹침 없이)", excel.get("detail") == (
        "엑셀 3개 중 2개 올림 · 새 주문 17건 · 못 올림: 사이트B")
    yield "엑셀 — 파일명이 링크다", any(isinstance(row[1], report.Link)
                                   for row in excel.get("rows") or [])
    yield "리포트에 file:/// 링크", 'href="file:///' in html
    yield "자동수집 — 새 주문 30건", "새 주문 30건" in (step_of(got, "site").get("detail") or "")
    yield "매출처리 — 44건 처리 · 남은 3건", step_of(got, "sales").get("detail") == (
        "주문 44건 매출처리 · 남은 미매출 주문 3건")
    hold = step_of(got, "logistics_wait")
    yield "물류대기 — 상품명으로 보인다", [row[0] for row in hold.get("rows") or []][:2] == [
        "상품A", "상품B"]
    # 09-22 사용자 확정: 보류를 건 상품만 적는다 (제외 상품 '상품C' 는 확인할 것에만)
    yield "물류대기 — 부족수량에 단위, 보류 건 상품만", [
        row[1] for row in hold.get("rows") or []] == ["3개", "1개"]
    yield "물류대기 한 줄 — 보류한 상품 이름", (hold.get("detail") or "").startswith(
        "재고 부족으로 배송보류 상품 2종(상품A, 상품B)")
    # 09-22 사용자 확정: 물류관리는 만든 전표 수만
    yield "물류관리 — 생성 전표(송장) 40건 + 올라간 주문 36건", (
        step_of(got, "logistics").get("detail") == "개별배송 전표(송장) 40건 생성 · 주문 36건"
        and step_of(got, "logistics").get("rows") == [("생성한 개별배송 전표(송장)", "40건"),
                                                     ("올라간 주문", "36건")])
    yield "메일 — 받은 엑셀 3개", step_of(got, "mailbox").get("detail") == "메일 엑셀 3개 받음"
    attention = " ".join(got["attention"])
    yield "확인할 것 — 못 올린 엑셀·실패 사이트(이름)·보류 제외 상품", (
        "사이트B" in attention and "몰A" in attention and "상품C" in attention)
    yield "확인할 것 — 실패 사이트는 로그인 정보 안내 없이 (09-22)", any(
        item == "자동수집 시 실패한 사이트입니다 (몰A) — 주문이 없었거나 사이트 로그인이 "
                "안 된 것입니다." for item in got["attention"])
    yield "사이트 수는 '곳' 이 아니라 '건' (09-22)", "사이트 실패 1건: 몰A" in got["summary"] and not any(
        "곳" in text for _, text in visible_texts(got))
    yield "리포트 맨 위에 확인할 것 상자와 주황 배지", (
        f"확인할 것 ({len(got['attention'])})" in html and f"확인할 것 {len(got['attention'])}" in html
        and html.index("확인할 것 (") < html.index("단계별 상세"))
    yield "★ 리포트 제목에 표 줄 수를 쓰지 않는다", not re.search(r"<h3>[^<]* — \d+건</h3>", html)
    yield "시작 줄 없이 끝 줄만", not any(line.endswith(" 시작") for line in got["lines"])


def expect_failure(*words):
    def expect(got):
        yield "실패 문구가 있다", bool(got["failure"])
        for word in words:
            yield f"실패 문구에 '{word}'", word in got["failure"]
        yield "리포트에 담당자용 기술 정보(접힘)", "담당자용 기술 정보" in got["html"]
        yield "사용자 로그에 실패 줄", any("실패 —" in line for line in got["lines"])
    return expect


def expect_mail_continue(*words):
    """메일 단계가 멈춰도 뒤 기능은 돈다 (사용자 확정 09-30, `full_flow._full`)."""
    def expect(got):
        yield "★ 멈추지 않고 끝까지 간다", bool(got["summary"]) and not got["failure"]
        yield "★ 물류관리는 돈다", step_of(got, "logistics").get("state") == "done"
        yield "멈춘 메일 단계는 실패로 남는다", step_of(got, "preflight").get("state") == "failed"
        yield "남은 메일 단계는 건너뜀", all(
            step_of(got, sid).get("state") == "skipped" for sid in ("mail_login", "mailbox"))
        yield "요약 맨 앞에 끝나지 않은 단계를 적는다", got["summary"].startswith("끝나지 않은 단계:")
        for word in words:
            yield f"확인할 것에 '{word}'", any(word in item for item in got["attention"])
        yield "사용자 로그에 실패 줄", any("실패 —" in line for line in got["lines"])
    return expect


def expect_late_failure(*words):
    """뒤쪽에서 멈춘 장면 — **이미 끝난 것 / 다시 실행하면** 이 붙어야 한다 (09-21 검토)."""
    base = expect_failure(*words)

    def expect(got):
        yield from base(got)
        yield "이미 끝난 것을 알려 준다", "이미 끝난 것:" in got["failure"] and "매출처리" in got["failure"]
        yield "다시 실행해도 매출처리가 되풀이되지 않는다고 알려 준다", (
            "이미 매출처리한 주문은 다시 처리하지 않습니다" in got["failure"])
        yield "실패 리포트에도 확인할 것이 실린다", "확인할 것 (" in got["html"]
    return expect


def expect_dry(got):
    yield "완료된다", bool(got["summary"]) and not got["failure"]
    yield "숫자를 지어내지 않는다 (새 주문 0건 따위 없음)", not any(
        "새 주문 0건" in text for _, text in visible_texts(got))
    yield "시험 실행이 드러난다 (영어 dry-run 없이)", "시험 실행" in got["summary"] and not any(
        "dry-run" in text for _, text in visible_texts(got))
    yield "첫 줄에 시험 실행이라고 알린다", any("시험 실행입니다" in l for l in got["lines"][:2])
    yield "★ 실제로 올리면 막힐 엑셀을 미리 알린다", any(
        "사이트B" in item and "비밀번호" in item for item in got["attention"])
    stuck = any("되돌리지 못했습니다" in item for item in got["attention"])
    yield ("자동/수동을 못 되돌리면 확인할 것에 원래 값과 함께" if got["scene"] == "dry_mode_stuck"
           else "자동/수동을 되돌렸으면 확인할 것에 없다"), (
        stuck and any("'수동'" in item for item in got["attention"])
        if got["scene"] == "dry_mode_stuck" else not stuck)


def expect_popup(got):
    """ERPia 알림을 닫고 다음 단계로 (09-22 사용자 확정)."""
    yield "★ 알림을 닫고 끝까지 간다 (멈추지 않는다)", bool(got["summary"]) and not got["failure"]
    yield "★ 물류관리는 돈다", step_of(got, "logistics").get("state") == "done"
    yield "알림 글이 확인할 것에 남는다", any("연락처를 입력하세요" in item
                                         for item in got["attention"])
    yield "요약 맨 앞에 끝나지 않은 단계를 적는다 (완료로 읽히지 않게)", got["summary"].startswith(
        "끝나지 않은 단계:")
    if got["scene"] == "popup_screen":
        yield "주문매핑 화면을 못 열면 엑셀·자동수집·매출처리는 건너뜀", all(
            step_of(got, sid).get("state") == "skipped" for sid in ("excel", "site", "sales"))
    elif got["scene"] == "popup_excel":
        yield "★ 알림 전에 올린 엑셀은 결과에 남는다 (다음에 또 올리지 않게)", (
            step_of(got, "excel").get("state") == "failed" and "새 주문 12건" in got["summary"])
        yield "자동수집·매출처리는 이어서 돈다", all(
            step_of(got, sid).get("state") == "done" for sid in ("site", "sales"))
    else:
        yield "막힌 단계는 실패로 남는다 (완료로 속이지 않는다)", step_of(
            got, "logistics_wait").get("state") == "failed"


def check_collect_log_all() -> list[bool]:
    """자동수집 수집로그를 **끝까지 내려** 세는가 (09-22 사용자 요청 "모든 행을").

    가짜 그리드: 행 번호 1..N, 한 화면에 `page` 행. 스크롤바는 행이 한 화면을 넘을 때만 있다.
    """
    from automation import order_mapping as om

    log.info("▶ 자동수집 수집로그 — 끝까지 내려 세기")
    grid = SimpleNamespace()
    world: dict = {}

    def setup(statuses, page, scroll_works=True, jump=1, bar_named=True):
        world.update(statuses=statuses, page=page, top=1, works=scroll_works, pressed=0,
                     jump=jump, bar_named=bar_named)

    def visible():
        top, n = world["top"], len(world["statuses"])
        return set(range(top, min(top + world["page"], n + 1)))

    def can_down():
        return max(visible()) < len(world["statuses"])

    def has_bar():
        return len(world["statuses"]) > world["page"]

    def scroll_down(g, methods=(), rounds=None):
        if not (has_bar() and world["bar_named"]) or not can_down():
            return False            # (이름으로 찾은) 스크롤바 없음 / 맨 아래 — 누르지 않는다
        world["pressed"] += 1
        if not world["works"]:
            return False
        # 보통은 2행 겹치게 한 페이지. jump=2 면 **한 페이지를 건너뛴다** (느린 UI 에서 두 번 먹은 꼴)
        step = (world["page"] - 2) * world["jump"]
        world["top"] = min(world["top"] + step, len(world["statuses"]))
        return True

    names = {n: f"몰{n}" for n in range(1, 60)}
    stubs = {
        "find": lambda *a, **k: grid,
        "scroll_to_top": lambda g: world.update(top=1) or True,
        "visible_row_numbers": lambda g: visible(),
        "columns_values": lambda g, cols: {
            om.STATUS_COLUMN: {n: world["statuses"][n - 1] for n in visible()},
            om.MARKET_COLUMN: {n: names[n] for n in visible()}},
        "scroll_down_verified": scroll_down,
        # 'Vertical' 이름으로 찾는 것. bar_named=False 면 이름이 달라('세로') 못 찾는 꼴
        "vertical_scrollbar": lambda g: object() if has_bar() and world["bar_named"] else None,
        "has_hidden_rows": lambda g: has_bar(),
        "scrollbar_button": lambda g, names_, bar=None: object() if can_down() else None,
    }
    saved = {name: getattr(ui, name) for name in stubs}
    out = []
    try:
        for name, value in stubs.items():
            setattr(ui, name, value)
        setup(["성공", "수집중", "수집중", "실패", "수집중", "수집중"], page=10)
        failed: list[str] = []
        counts, exact = om.collect_counts_all(object(), failed_names=failed)
        out.append(check("한 화면(6행)이면 누르지 않고 정확히 센다",
                         exact and world["pressed"] == 0 and counts == {"성공": 1, "수집중": 4,
                                                                      "실패": 1}
                         and failed == ["몰4"], f"{counts} {failed} 누름 {world['pressed']}"))
        many = ["성공"] * 30
        many[4] = many[27] = "실패"
        setup(many, page=12)
        failed = []
        counts, exact = om.collect_counts_all(object(), failed_names=failed)
        out.append(check("★ 30행(3화면) — 끝까지 내려 **화면 밖 실패도** 세고, 겹친 행은 한 번만",
                         exact and counts == {"성공": 28, "실패": 2} and failed == ["몰5", "몰28"],
                         f"{counts} {failed}"))
        setup(many, page=12, scroll_works=False)
        counts, exact = om.collect_counts_all(object(), failed_names=[])
        out.append(check("★ 내려가지 않으면 '끝까지 못 봄' (숫자를 지어내지 않는다)",
                         not exact and sum(counts.values()) == 12, f"{counts}"))
        setup(many, page=12, jump=2)
        counts, exact = om.collect_counts_all(object(), failed_names=[])
        out.append(check("★ 스크롤이 한 페이지를 건너뛰어 가운데가 비면 '끝까지 못 봄'",
                         not exact, f"{counts} exact={exact}"))
        setup(many, page=12, bar_named=False)
        counts, exact = om.collect_counts_all(object(), failed_names=[])
        out.append(check("스크롤바 이름이 달라 못 찾아도 세로 스크롤바가 보이면 '끝까지 못 봄'",
                         not exact, f"{counts} exact={exact}"))
    finally:
        for name, value in saved.items():
            setattr(ui, name, value)

    result = {"added": 0, "counts": {"성공": 11, "실패": 1}, "failures": {"실패": 1},
              "failed_names": ["몰4"], "counts_exact": False}
    detail = erpia_flow.site_detail(result, dry_run=False)
    table = dict(erpia_flow.site_table(result))
    out.append(check("끝까지 못 봤으면 한 줄에 '일부만 읽음'·표에 '이상'", "일부만 읽음" in detail
                     and table.get("사이트 — 실패") == "1건 이상", f"{detail} / {table}"))
    return out


def check_mode_restore() -> list[bool]:
    """시험 실행이 바꾼 물류관리 [자동/수동] 을 끝에 되돌리는가 (사용자 확정 09-21).

    `logistics.run` 의 화면 조작을 가짜로 바꾸고 콤보 값만 흉내 낸다.
    """
    log.info("▶ 시험 실행 뒤 물류관리 자동/수동 되돌리기")
    state = {"mode": "수동", "calls": 0, "fail_restore": False, "fail_prepare": False}

    def dropdown(combo, name, pid, what, dry_run=False):
        state["calls"] += 1
        if state["fail_restore"] and "되돌리기" in what:
            raise logistics.ScreenError("드롭다운에서 못 찾았다")
        state["mode"] = name

    def prepare(screen):
        if state["fail_prepare"]:
            raise logistics.ScreenError("등록모드 실패")
        return 3

    noop = lambda *a, **k: None   # noqa: E731
    stubs = {"open_screen": lambda target: object(), "_mode_combo": lambda screen: object(),
             "current_mode": lambda screen: state["mode"], "_select_from_dropdown": dropdown,
             "select_mode": lambda screen, mode, pid, dry_run=False: dropdown(None, mode, pid, "자동/수동"),
             "prepare": prepare, "bottom_grid": lambda screen: object(),
             "count_slips": lambda grid: (3, True),
             "individual_delivery": lambda screen, pid, dry_run=False: 0,
             "select_courier": noop, "apply_courier": noop, "select_box": noop, "apply_box": noop,
             "save": lambda *a, **k: False, "ui": SimpleNamespace(visible_row_count=lambda grid: 0)}
    saved = {name: getattr(logistics, name) for name in stubs}   # 없으면 AttributeError

    def run(start="수동", dry_run=True, **flags):
        state.update({"mode": start, "calls": 0, "fail_restore": False, "fail_prepare": False,
                      **flags})
        return logistics.run(FakeTarget(), courier="택배사A", box="박스A", mode="자동",
                             dry_run=dry_run)

    out = []
    try:
        for name, value in stubs.items():
            setattr(logistics, name, value)
        result = run()
        out.append(check("시험 실행 끝에 원래 값(수동)으로 되돌린다",
                         state["mode"] == "수동" and "mode_not_restored" not in result, str(state)))
        run(start="자동")
        out.append(check("원래 값과 같으면 되돌리지 않는다 (드롭다운 1번)", state["calls"] == 1,
                         str(state)))
        result = run(dry_run=False)
        out.append(check("실제 실행은 되돌리지 않는다", state["mode"] == "자동", str(state)))
        result = run(fail_restore=True)
        out.append(check("되돌리기 실패는 결과에 원래 값을 남기고 흐름은 끝난다",
                         result.get("mode_not_restored") == "수동", str(result)))
        try:
            run(fail_prepare=True)
            raised = False
        except logistics.ScreenError:
            raised = True
        out.append(check("중간에 실패해도 되돌리고, 원래 실패는 그대로 올린다",
                         raised and state["mode"] == "수동", str(state)))
        token = cancel.CancelToken()
        token.cancel("probe")
        with cancel.use(token):
            run()
        out.append(check("중단됐으면 건드리지 않는다", state["mode"] == "자동", str(state)))
    finally:
        for name, value in saved.items():
            setattr(logistics, name, value)
    return out


SCENES = (
    ("ok", False, expect_ok),
    ("phone_offline", False, expect_mail_continue("휴대폰", "연결됨")),
    ("login_rejected", False, expect_failure("비밀번호", "[설정]")),
    ("courier_missing", False, expect_late_failure("'택배'", "[택배사]", "택배사A")),
    ("screen_missing", False, expect_late_failure("[물류대기] 단계에서", "담당자", "하지 못한 것")),
    ("dry_run", True, expect_dry),
    ("dry_mode_stuck", True, expect_dry),
    ("popup_continue", False, expect_popup),
    ("popup_screen", False, expect_popup),
    ("popup_excel", False, expect_popup),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="logs/_sample_<장면>.txt/.html 로 남긴다 (검토용, 보고 지운다)")
    args = parser.parse_args()
    setup_logging()
    results = (check_detector() + check_friendly() + check_screen_filter()
               + check_mode_restore() + check_collect_log_all())
    for scene, dry_run, expect in SCENES:
        out, got = check_scene(scene, dry_run, expect)
        results += out
        if args.write:
            SAMPLE_DIR.mkdir(exist_ok=True)
            (SAMPLE_DIR / f"_sample_{scene}.txt").write_text(sample_text(got), encoding="utf-8")
            (SAMPLE_DIR / f"_sample_{scene}.html").write_text(got["html"], encoding="utf-8")
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
