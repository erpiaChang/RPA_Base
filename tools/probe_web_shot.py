r"""[확인 도구 - 읽기 전용] 웹 대시보드를 **찍어서 본다** (09-28). 실서버·CDN 에 닿지 않는다.

Edge(Playwright, 창 없이)로 `web/` 을 가짜 주소에서 열고, supabase-js 자리에 가짜 클라이언트(아래 FAKE)를 넣어
관리자·업체 사용자·로그인 화면을 데스크톱·휴대폰 폭으로 찍는다. 그 밖의 요청은 모두 끊는다.
사진은 `logs/_web_shot/` (본 뒤 지운다). 자동으로 보는 것: 콘솔 오류 없음 · 탭 넷 · 사용량 막대 수 ·
업체 사용자에게 업체별 표가 없음 · 질문 패널 입력칸이 대화가 길어도 화면 안 · 휴대폰 폭 가로 스크롤 없음.

    .venv\Scripts\python.exe -m tools.probe_web_shot
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from config.settings import PROJECT_ROOT, SETTINGS  # noqa: E402
from tools import build_web  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
WEB = PROJECT_ROOT / "web"
OUT = PROJECT_ROOT / "logs" / "_web_shot"
ORIGIN = "https://rpa.test"
DESKTOP = {"width": 1366, "height": 900}
PHONE = {"width": 390, "height": 844}

# 가짜 supabase-js — createClient 가 돌려주는 것만 흉내 낸다. 역할(window.__ROLE)에 따라 RLS 처럼 업체로 거른다
FAKE = r"""
(function () {
  const A = "aaaaaaaa-0000-0000-0000-000000000001", B = "bbbbbbbb-0000-0000-0000-000000000002";
  const D1 = "d1000000-0000-0000-0000-000000000001", D2 = "d2000000-0000-0000-0000-000000000002", D3 = "d3000000-0000-0000-0000-000000000003";
  const now = Date.now(), iso = ms => new Date(ms).toISOString(), ago = s => iso(now - s * 1000);
  const kst = d => new Date(d.getTime() + 9 * 3600000).toISOString().slice(0, 10);
  const accounts = [{ id: A, name: "업체A", suspended_at: null }, { id: B, name: "업체B", suspended_at: ago(86400) }];
  const devices = [
    { id: D1, account_id: A, name: "PC-A", last_seen_at: ago(20), next_run_at: iso(now + 3600e3), auto_run_enabled: true, paused: false, app_version: "2026.09.22" },
    { id: D2, account_id: A, name: "PC-B", last_seen_at: ago(3 * 86400), auto_run_enabled: false, paused: false, app_version: "2026.09.22" },
    { id: D3, account_id: B, name: "PC-C", last_seen_at: ago(40), next_run_at: iso(now + 7200e3), auto_run_enabled: true, paused: true, app_version: "2026.09.22" },
  ];
  const usage = [];
  for (let i = 0; i < 70; i++) {
    const day = kst(new Date(now - i * 86400000));
    [[D1, A, 1], [D2, A, 0.5], [D3, B, 1.6]].forEach(([dev, acc, k], j) => {
      if ((i + j) % 7 === 5) return;
      const mail = Math.round((3 + (i * 7 + j * 3) % 9) * k), orders = 1 + (i % 3 === 0 ? 1 : 0), lw = i % 4 ? 1 : 0, lg = 1;
      usage.push({ device_id: dev, account_id: acc, day, runs: orders + 1, mail, orders, logistics_wait: lw, logistics: lg, actions: mail + orders + lw + lg });
    });
  }
  const runs = [
    { id: "r-live", account_id: A, device_id: D1, trigger: "예약 실행", modules: ["mail", "orders"], state: "running", received_at: ago(260),
      started_at: ago(260), heartbeat_at: ago(10), step_total: 7, current_index: 4, current_name: "엑셀수집", current_activity: "사이트A_20260928.xlsx 올리는 중 (2/3)" },
    { id: "r-done", account_id: A, device_id: D1, trigger: "직접 실행", modules: [], state: "done", received_at: ago(7200), finished_at: ago(6900), elapsed: 300, summary: "메일 4개 · 주문 38건 · 전표 12건", attention: 0 },
    { id: "r-fail", account_id: B, device_id: D3, trigger: "예약 실행", modules: ["logistics"], state: "failed", received_at: ago(90000), finished_at: ago(89800), elapsed: 200, summary: "[물류관리] 단계에서 멈췄습니다", attention: 1 },
    { id: "r-lost", account_id: A, device_id: D2, trigger: "원격 실행", modules: ["mail"], state: "lost", received_at: ago(200000), elapsed: null, summary: "끝나기 전에 프로그램이 종료됨", attention: 0 },
    { id: "r-unf", account_id: B, device_id: D3, trigger: "예약 실행", modules: [], state: "unfinished", received_at: ago(300000), finished_at: ago(299000), elapsed: 1000, summary: "ERPia 알림을 닫고 다음 단계로 넘어감", attention: 2 },
  ];
  const steps = [
    { run_id: "r-done", step_id: "mailbox", index: 3, name: "메일 엑셀 받기", state: "done", detail: "엑셀 4개 받음", total_items: 4, done_items: 4, ok_items: 4, failed_items: 0, elapsed: 40, finished_at: ago(7100) },
    { run_id: "r-done", step_id: "sales", index: 7, name: "매출처리", state: "done", detail: "38건 처리", total_items: 0, elapsed: 30, finished_at: ago(7000) },
    { run_id: "r-done", step_id: "logistics", index: 9, name: "물류관리", state: "done", detail: "전표 12건 저장", total_items: 0, elapsed: 60, finished_at: ago(6900) },
  ];
  const logs = [{ run_id: "r-done", seq: 1, at: ago(7200), level: "info", text: "실행을 시작했습니다" },
                { run_id: "r-done", seq: 2, at: ago(6950), level: "warning", text: "메일 1개는 엑셀이 아니었습니다" }];
  const questions = [];
  const long = "1. [자동 실행] 칸을 체크합니다.\n2. [정해진 날·시각]을 선택하고 [반복]에서 [요일 지정]을 고릅니다.\n3. 월·화·수·목·금을 체크하고 시각을 09:00 으로 둡니다.\n4. [이 예약의 기능]을 체크한 뒤 [추가]를 누릅니다.";
  const qn = window.__QUESTIONS || 0;
  for (let i = 0; i < qn; i++) questions.push({ id: i + 1, user_id: "u-1", question: i % 2 ? "웹에서 설정을 바꾸면 언제 반영되나요?" : "예약을 평일 오전 9시에 돌게 하려면?",
    answer: i % 2 ? "PC 가 30초 안에 받습니다. 실행 중이면 끝난 뒤 적용됩니다." : long, status: i === qn - 1 && window.__LAST_FAILED ? "expired" : "done" });
  const members = { admin: [{ account_id: null, role: "admin" }], owner: [{ account_id: A, role: "owner" }] };
  const tables = { accounts, devices, usage_daily: usage, runs, run_steps: steps, run_logs: logs, run_attention: [{ run_id: "r-fail", seq: 1, text: "물류관리 화면을 확인하세요" }],
                   questions, commands: [{ id: 1, device_id: D1, kind: "start", modules: ["mail"], created_at: ago(3600), created_by: "admin@example.com", result: "끝남" }] };
  // 설정은 서버 표에 없다 (10-07) — PC 의 답(take_settings)으로만 온다
  const SNAP = { locked_modules: null,
    settings: { run_modules: ["mail", "orders", "logistics_wait", "logistics"], collect_sources: ["excel", "site"], delivery_company: "택배사A", delivery_box: "박스A",
                // 빈 무선 주소는 PC 가 "" 로 보낸다 — 웹 칸(null)과 같게 봐야 저장 때 같이 가지 않는다 (10-07 검토)
                adb_wireless_address: "",
                logistics_mode: "자동", sales_mode: "전체", hold_exclude_codes: [], sms_source: "phonelink", adb_connection: "usb", auto_run_enabled: true,
                auto_run_mode: "daily", auto_run_times: ["평일 09:00 mail,orders"], auto_run_interval_minutes: 60 } };
  const role = window.__ROLE || "none";
  const visible = (name, row) => {                   // RLS 흉내 — 업체 사용자는 자기 업체만
    if (role !== "owner") return true;
    if (name === "accounts") return row.id === A;
    if ("account_id" in row) return row.account_id === A;
    if ("run_id" in row) return runs.some(r => r.id === row.run_id && r.account_id === A);
    if ("device_id" in row) return devices.some(d => d.id === row.device_id && d.account_id === A);
    return true;
  };
  const join = row => Object.assign({}, row,
    row.device_id ? { devices: { name: (devices.find(d => d.id === row.device_id) || {}).name } } : {},
    row.account_id ? { accounts: { name: (accounts.find(a => a.id === row.account_id) || {}).name } } : {});
  class Q {
    constructor(name) { this.name = name; this.f = []; this.lim = null; this.rng = null; this.one = false; this.head = false; this.ord = []; }
    select(cols, opts) { this.head = !!(opts && opts.head); return this; }
    eq(k, v) { this.f.push(r => r[k] === v); return this; }
    neq(k, v) { this.f.push(r => r[k] !== v); return this; }
    in(k, vs) { this.f.push(r => vs.includes(r[k])); return this; }
    gte(k, v) { this.f.push(r => r[k] != null && r[k] >= v); return this; }
    lte(k, v) { this.f.push(r => r[k] != null && r[k] <= v); return this; }
    lt(k, v) { this.f.push(r => r[k] != null && r[k] < v); return this; }
    order(k, o) { this.ord.push([k, !(o && o.ascending === false)]); return this; }
    limit(n) { this.lim = n; return this; }
    range(a, b) { this.rng = [a, b]; return this; }
    single() { this.one = true; return this; }
    then(ok, bad) {
      let rows = (tables[this.name] || []).filter(r => visible(this.name, r)).filter(r => this.f.every(f => f(r))).map(join);
      for (const [k, asc] of this.ord.slice().reverse()) rows.sort((x, y) => (x[k] > y[k] ? 1 : x[k] < y[k] ? -1 : 0) * (asc ? 1 : -1));
      if (this.rng) rows = rows.slice(this.rng[0], this.rng[1] + 1);
      if (this.lim != null) rows = rows.slice(0, this.lim);
      const out = this.head ? { data: null, error: null, count: rows.length }
        : this.one ? { data: rows[0] || null, error: rows[0] ? null : { message: "없음" } } : { data: rows, error: null };
      return Promise.resolve(out).then(ok, bad);
    }
  }
  const session = role === "none" ? null : { user: { id: "u-1", email: role === "admin" ? "admin@example.com" : "owner@a.example" } };
  window.supabase = { createClient: () => ({
    auth: { getSession: async () => ({ data: { session } }),
            signInWithPassword: async () => ({ error: { message: "x", status: 400 } }),
            signOut: async () => ({ error: null }),
            onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }) },
    from: name => new Q(name),
    // 부른 RPC 를 window.__RPC 에 남긴다 — 무엇을 보냈는지 시험이 본다
    rpc: async (name, args) => {
      (window.__RPC = window.__RPC || []).push([name, args]);
      if (name === "llm_online") return { data: true, error: null };
      if (name === "request_settings") return { data: 77, error: null };
      if (name === "take_settings") return { data: JSON.parse(JSON.stringify(SNAP)), error: null };
      return { data: 1, error: null };
    },
    members,
  }) };
  // account_members 는 역할에 따라
  tables.account_members = (members[role] || []).map(m => Object.assign({ user_id: "u-1" }, m));
})();
"""
XLSX_STUB = "window.XLSX = { utils: { book_new(){return {}}, json_to_sheet(){return {}}, book_append_sheet(){} }, writeFile(){} };"


def check(name: str, ok: object, detail: str = "") -> bool:
    ok = bool(ok)
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def _route(route) -> None:
    url = route.request.url
    if url.startswith(ORIGIN + "/"):
        name = url[len(ORIGIN) + 1:].split("?")[0].split("#")[0] or "index.html"
        path = WEB / name
        if path.is_file() and path.suffix in (".html", ".js", ".css"):
            # git 의 web/ 은 자리표시다 (10-02) — 배포처럼 채우되 가짜 값으로 (요청은 아래에서 다 끊긴다)
            body = build_web.fill(path.read_text(encoding="utf-8"),
                                  {"__SUPABASE_URL__": "https://fakeproject.supabase.co",
                                   "__SUPABASE_PUBLISHABLE_KEY__": "sb_publishable_fake"})
            if name == "index.html":            # 가짜 스크립트를 넣으려면 SRI 를 뗀다 (이 도구 안에서만)
                body = re.sub(r'\s+integrity="[^"]+"', "", body)
            kind = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}[path.suffix]
            route.fulfill(status=200, body=body, headers={"Content-Type": kind + "; charset=utf-8"})
            return
    if "supabase-js" in url:
        route.fulfill(status=200, body=FAKE, headers={"Content-Type": "application/javascript"})
        return
    if "sheetjs" in url:
        route.fulfill(status=200, body=XLSX_STUB, headers={"Content-Type": "application/javascript"})
        return
    route.abort()                               # 그 밖에는 어디에도 닿지 않는다


def shoot(browser, name: str, role: str, hash_: str, viewport: dict, errors: list, questions: int = 0,
          open_help: bool = False, last_failed: bool = False):
    page = browser.new_page(viewport=viewport)
    page.add_init_script(f"window.__ROLE = {json.dumps(role)}; window.__QUESTIONS = {questions};"
                         f" window.__LAST_FAILED = {json.dumps(last_failed)};")
    page.on("console", lambda m: errors.append(f"{name}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.route("**/*", _route)
    page.goto(f"{ORIGIN}/index.html{hash_}")
    page.wait_for_timeout(700)                  # 가짜 응답은 바로 온다 — 그려질 틈만
    if open_help:
        page.click("#helpBtn")
        page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=not open_help)
    # append(null) 은 "null" 글자를 붙인다 (09-28 PC 화면에서 'nullnull' 발견) — 빈 값이 글자로 새지 않았나
    leak = re.search(r"\b(null|undefined|NaN)\b", page.inner_text("body"))
    if leak:
        errors.append(f"{name}: 화면에 '{leak.group(1)}' 글자")
    return page


def main() -> int:
    setup_logging()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    out: list[bool] = []
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=SETTINGS.browser_channel or "msedge", headless=True)
        try:
            log.info("▶ 로그인")
            shoot(browser, "01_login", "none", "", DESKTOP, errors).close()

            log.info("▶ 관리자")
            page = shoot(browser, "02_admin_home", "admin", "#/home", DESKTOP, errors)
            tabs = page.locator("#tabs button").all_inner_texts()
            out.append(check("탭 넷 — 요약·사용량·PC·실행 기록", tabs == ["요약", "사용량", "PC", "실행 기록"], str(tabs)))
            out.append(check("요약 카드 넷", page.locator(".stats .stat").count() == 4))
            page.close()
            page = shoot(browser, "03_admin_usage", "admin", "#/usage", DESKTOP, errors)
            bars = page.locator(".chart .col").count()
            out.append(check("이번 달 날짜 수만큼 막대", bars >= 1, f"{bars}개"))
            out.append(check("관리자 전체 보기 — 업체별 표가 있다", page.get_by_role("heading", name="업체별").count() == 1))
            page.close()
            shoot(browser, "04_admin_devices", "admin", "#/devices", DESKTOP, errors).close()
            shoot(browser, "05_admin_history", "admin", "#/history", DESKTOP, errors).close()
            shoot(browser, "06_admin_device", "admin", "#/device/d1000000-0000-0000-0000-000000000001", DESKTOP, errors).close()
            shoot(browser, "07_admin_run", "admin", "#/run/r-done", DESKTOP, errors).close()

            log.info("▶ 업체 사용자 (자기 업체만)")
            page = shoot(browser, "08_owner_usage", "owner", "#/usage", DESKTOP, errors)
            out.append(check("업체 사용자 — 업체별 표가 없다", page.get_by_role("heading", name="업체별").count() == 0))
            out.append(check("업체 사용자 — 다른 업체 이름이 안 보인다", "업체B" not in page.inner_text("body")))
            page.close()
            shoot(browser, "09_owner_home", "owner", "#/home", DESKTOP, errors).close()

            log.info("▶ 사용법 질문 패널")
            page = shoot(browser, "10_help_empty", "owner", "#/home", DESKTOP, errors, open_help=True)
            out.append(check("대화가 없으면 예시 질문 칩", page.locator(".chips button").count() == 4))
            page.close()
            page = shoot(browser, "11_help_long", "owner", "#/usage", DESKTOP, errors, questions=10, open_help=True, last_failed=True)
            box = page.locator(".help-input textarea").bounding_box()
            logb = page.locator(".help-log").bounding_box()
            out.append(check("★ 대화가 길어도 입력칸이 화면 안, 대화 아래", box and logb and box["y"] + box["height"] <= DESKTOP["height"]
                             and box["y"] >= logb["y"] + logb["height"] - 1, f"입력칸 y={box and round(box['y'])}"))
            out.append(check("대화 영역만 스크롤된다", page.evaluate("(() => { const l = document.querySelector('.help-log');"
                                                                    " return l.scrollHeight > l.clientHeight; })()")))
            out.append(check("만료된 답에 [다시 묻기]", page.get_by_role("button", name="다시 묻기").count() >= 1))
            page.close()

            log.info("▶ 중간 폭 (1180px) + 패널 열림 — 사용량 카드가 넘치지 않나")
            page = shoot(browser, "15_mid_usage_help", "admin", "#/usage", {"width": 1180, "height": 900}, errors,
                         questions=2, open_help=True)
            over = page.evaluate("[...document.querySelectorAll('.stat .value')]"
                                 ".filter(v => v.scrollWidth > v.clientWidth + 1).length")
            out.append(check("사용량 카드 숫자가 카드 밖으로 넘치지 않는다", over == 0, f"{over}개 넘침"))
            page.close()

            log.info("▶ PC 설정 — 열 때 PC 에 묻고, 바꾼 것만 보낸다 (10-07, 서버에 설정 표 없음)")
            # PC-C — 켜져 있고 실행 중이 아니다 (PC-A 는 실행 중이라 칸이 잠긴다)
            page = shoot(browser, "16_admin_device_settings", "admin", "#/device/d3000000-0000-0000-0000-000000000003",
                         DESKTOP, errors)
            page.wait_for_selector(".form", timeout=8000)          # 첫 답은 2초 뒤에 읽는다
            calls = page.evaluate("window.__RPC.map(c => c[0])")
            out.append(check("★ 화면을 열면 PC 에 묻고 답을 읽는다 (서버 표를 읽지 않는다)",
                             calls[:2] == ["request_settings", "take_settings"] and page.is_visible("text=PC 에서 받은 값"),
                             str(calls)))
            box = page.get_by_label("박스")
            out.append(check("받은 값이 칸에 들어간다", box.input_value() == "박스A", box.input_value()))
            box.fill("박스B")
            page.get_by_role("button", name="저장").click()
            page.wait_for_function("window.__RPC.some(c => c[0] === 'set_device_settings')")
            sent = page.evaluate("window.__RPC.find(c => c[0] === 'set_device_settings')[1]")
            out.append(check("★ 바꾼 키만 보낸다 (그 사이 PC 에서 고친 다른 값을 덮지 않게)",
                             sent == {"device": "d3000000-0000-0000-0000-000000000003", "settings": {"delivery_box": "박스B"}},
                             str(sent)))
            page.screenshot(path=str(OUT / "16_admin_device_settings.png"), full_page=True)
            page.close()
            page = shoot(browser, "17_owner_device_off", "owner", "#/device/d2000000-0000-0000-0000-000000000002",
                         DESKTOP, errors)
            out.append(check("PC 가 꺼져 있으면 묻지 않고 알린다",
                             page.is_visible("text=PC 가 꺼져 있어") and not page.evaluate(
                                 "(window.__RPC || []).some(c => c[0] === 'request_settings')")))
            page.close()

            log.info("▶ 업체 단위 중지 (10-07) — 관리자만")
            page = shoot(browser, "18_admin_suspend", "admin", "#/devices", DESKTOP, errors)
            out.append(check("관리자 전체 보기에는 중지 단추가 없다 (업체를 골라야)",
                             page.get_by_role("heading", name="업체 사용").count() == 0))
            page.on("dialog", lambda d: d.accept())
            page.select_option("#accountSel", "bbbbbbbb-0000-0000-0000-000000000002")
            page.wait_for_selector("text=중지됨")
            page.get_by_role("button", name="중지 풀기").click()
            page.wait_for_selector("text=사용 중")
            called = page.evaluate("window.__RPC.filter(c => c[0].endsWith('_account'))")
            out.append(check("★ 고른 업체의 중지를 풀면 resume_account 를 부른다 (확인 창 뒤)",
                             called == [["resume_account", {"account": "bbbbbbbb-0000-0000-0000-000000000002"}]], str(called)))
            page.screenshot(path=str(OUT / "18_admin_suspend.png"), full_page=True)
            page.close()
            page = shoot(browser, "19_owner_devices", "owner", "#/devices", DESKTOP, errors)
            out.append(check("업체 사용자에게는 중지 단추가 없다",
                             page.get_by_role("heading", name="업체 사용").count() == 0
                             and page.get_by_role("button", name="이 업체 RPA 중지").count() == 0))
            page.close()

            log.info("▶ 휴대폰 폭")
            for name, hash_, help_ in (("12_phone_home", "#/home", False), ("13_phone_usage", "#/usage", False),
                                       ("14_phone_help", "#/home", True),
                                       ("20_phone_device", "#/device/d1000000-0000-0000-0000-000000000001", False)):
                page = shoot(browser, name, "admin", hash_, PHONE, errors, questions=4, open_help=help_)
                wide = page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
                out.append(check(f"{name} — 가로 스크롤 없음", wide <= 1, f"{wide}px 넘침"))
                page.close()
        finally:
            browser.close()
    out.append(check("★ 콘솔 오류 없음 · 화면에 null/undefined/NaN 글자 없음", not errors, "; ".join(errors[:5])))
    log.info("사진: %s", OUT)
    log.info("결과: 통과 %d / 실패 %d", sum(out), len(out) - sum(out))
    return 0 if all(out) else 1


if __name__ == "__main__":
    sys.exit(main())
