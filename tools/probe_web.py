r"""[확인 도구 - 읽기 전용] 웹 대시보드(`web/index.html`) — 정적 검사. 서버에 붙지 않는다.

    .venv\Scripts\python.exe -m tools.probe_web

innerHTML 금지·SRI·CSP·상태 글자가 코드와 같은지·config.js 에 비밀이 없는지 + (node 가 있으면) 스크립트 구문.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue            # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from config.settings import PROJECT_ROOT  # noqa: E402
from orchestrator import history, steps  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
WEB = PROJECT_ROOT / "web"


def check(name: str, ok: object, detail: str = "") -> bool:
    ok = bool(ok)
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def main() -> int:
    setup_logging()
    out = []
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    config = (WEB / "config.js").read_text(encoding="utf-8")
    headers = (WEB / "_headers").read_text(encoding="utf-8")

    log.info("▶ 위험한 그리기")
    out.append(check("innerHTML / document.write / eval / insertAdjacentHTML 없음 (html·js)",
                     not re.search(r"\.innerHTML|document\.write|insertAdjacentHTML|\beval\s*\(", html + app)))
    out.append(check("모든 텍스트는 textContent 로", app.count("textContent") >= 5))
    out.append(check("index.html 에 인라인 스크립트가 없다 (CSP script-src 'self' 에 막힌다) — app.js 를 읽는다",
                     not re.search(r"<script>", html) and 'src="app.js"' in html))

    log.info("▶ 스크립트·CSP")
    scripts = re.findall(r"<script[^>]*\bsrc=\"(https?://[^\"]+)\"[^>]*>", html)
    tags = re.findall(r"<script[^>]*\bsrc=\"https?://[^>]*>", html)
    out.append(check("CDN 스크립트 2개 (supabase-js · SheetJS), 버전이 URL 에 박혀 있다",
                     len(scripts) == 2 and all(re.search(r"@\d+\.\d+\.\d+|-\d+\.\d+\.\d+", s) for s in scripts), str(scripts)))
    out.append(check("jsdelivr 는 정식 파일만 (즉석 생성 .min.js 는 SRI 가 깨진다)",
                     not any("jsdelivr" in s and s.endswith(".min.js") for s in scripts)))
    out.append(check("CDN 스크립트마다 integrity(sha384) + crossorigin",
                     all("integrity=\"sha384-" in t and "crossorigin" in t for t in tags)))
    meta = re.search(r"http-equiv=\"Content-Security-Policy\" content=\"([^\"]+)\"", html)
    out.append(check("meta CSP: default-src 'none', script-src 는 self + 두 CDN 만, connect-src 는 supabase 만",
                     meta is not None and "default-src 'none'" in meta.group(1)
                     and re.search(r"script-src 'self' https://cdn\.jsdelivr\.net https://cdn\.sheetjs\.com;", meta.group(1))
                     and "connect-src __SUPABASE_URL__;" in meta.group(1)))       # build_web 이 프로젝트 주소 하나로 채운다
    out.append(check("_headers 에 같은 CSP + nosniff + no-referrer",
                     "Content-Security-Policy:" in headers and "nosniff" in headers and "no-referrer" in headers
                     and meta is not None and meta.group(1).split("; base-uri")[0] in headers))

    log.info("▶ 상태 글자 — 코드와 같은가")
    run_labels = {**history.STATE_LABELS, "running": "진행 중", "lost": "끝나기 전에 끊김", "skipped": "건너뜀"}
    missing = [f"{k}={v}" for k, v in run_labels.items() if f'{k}: "{v}"' not in app]
    out.append(check("runs 상태 글자 7개 (history.STATE_LABELS + running/lost/skipped)", not missing, str(missing)))
    missing = [f"{k}={v}" for k, v in steps.STATE_LABELS.items() if f'{k}: "{v}"' not in app]
    out.append(check("run_steps 상태 글자 6개 (steps.STATE_LABELS)", not missing, str(missing)))
    out.append(check("빈 modules 는 '전체'", '"전체"' in app))
    out.append(check("건수가 0 이면 막대를 그리지 않는다", "s.total_items > 0" in app))
    out.append(check("로그 줄 class 는 표로 거른다 (서버 문자열을 class 로 쓰지 않는다)", "LOG_CLASSES[l.level]" in app))
    out.append(check("페이지 넘김 정렬에 유일한 열을 붙인다", '.order("received_at").order("id")' in app
                     and '.order("index").order("step_id")' in app))

    log.info("▶ 원격 제어·설정 (09-28)")
    from orchestrator import remote

    sent = set(re.findall(r"\bout\.(\w+) =", app))
    out.append(check("★ 웹이 올리는 설정 키 = PC·서버의 원격 키 (비밀번호 없음)", sent == set(remote.REMOTE_KEYS),
                     f"웹만 {sorted(sent - set(remote.REMOTE_KEYS))} / 빠짐 {sorted(set(remote.REMOTE_KEYS) - sent)}"))
    out.append(check("쓰기는 RPC 셋으로만 (표에 직접 쓰지 않는다) + 읽기 RPC llm_online",
                     set(re.findall(r'sb\.rpc\("(\w+)"', app))
                     == {"send_command", "set_device_settings", "ask_question", "llm_online"}
                     and not re.search(r"\.(insert|update|upsert|delete)\(", app)))
    out.append(check("★ 실행·중단은 확인 창을 거친다", "window.confirm(ask)" in app and "start:" in app and "stop:" in app))
    out.append(check("제어 단추는 관리자·그 업체 owner 에게만", 'm.role === "owner" && m.account_id === accId' in app))
    out.append(check("★ 단추 이름 — RPA 종료·일시정지·계속하기 (사용자 확정 09-28)",
                     'stop: "RPA 종료", pause: "일시정지", resume: "계속하기"' in app))
    out.append(check("★ RPA 종료는 실행 중만, 일시정지는 아닐 때만, 계속하기는 일시정지일 때만",
                     "stop.disabled = !running" in app and "pause.disabled = paused" in app
                     and "resume.disabled = !paused" in app and "stop.disabled = pause.disabled = resume.disabled = true" in app))
    schema = (PROJECT_ROOT / "server" / "schema.sql").read_text(encoding="utf-8")
    body = schema[schema.find("function public.set_device_settings"):]
    body = body[:body.find("$$;")]
    out.append(check("★ 실행 중에는 설정을 못 바꾼다 — 서버 423 + 웹 칸 잠금 (09-29)",
                     "r.state = 'running'" in body and "PT423" in body
                     and "settings.setRunning(!!(run && run.length))" in app
                     and 'error.code === "PT423"' in app and "RUNNING_LOCK_TEXT" in app))

    log.info("▶ 매출처리 방식·실행 상세 자동 갱신 (10-01)")
    from automation.order_mapping import SALES_MODES

    sales = re.search(r"const SALES = \[\[\"([^\"]+)\", [^\]]+\], \[\"([^\"]+)\",", app)
    out.append(check("★ 웹 매출처리 값 = PC 의 SALES_MODES = 서버 clean_settings",
                     sales is not None and sales.groups() == SALES_MODES
                     and "when 'sales_mode' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('전체','선택주문')"
                     in schema))
    out.append(check("매출처리 칸은 다른 칸처럼 — 값이 없으면 빈 칸, 주문수집을 쓰면 요구한다 (10-02 검토)",
                     "salesKnown" not in app and "if (sales.value) out.sales_mode = sales.value;" in app
                     and 'else if (using.has("orders")) return "매출처리 방식을 고르세요.";' in app
                     and "courier, box, mode, sales, hold" in app))
    out.append(check("물류관리를 쓰면 택배사·박스를 요구한다 — PC 의 [실행] 필수 목록과 같게 (10-02 검토)",
                     'using.has("logistics") && !(out.delivery_company && out.delivery_box)' in app))
    out.append(check("★ 설정 칸에 기본값이 없다 (10-02) — 값이 없으면 빈 칸, 빈 선택은 보내지 않는다",
                     not re.search(r'\|\| "(자동|전체|phonelink|usb|daily)"|== null \? 60|run_modules \|\| MODULES', app)
                     and 'text: "— 고르세요 —"' in app and "delete out.collect_sources;" in app))
    detail = app[app.find("async function renderDetail"):app.find("function drawDetail")]
    out.append(check("★ 실행 상세 — 진행 중이면 5초마다 다시 읽고, 끝나면 멈추고, 겹쳐 읽지 않는다",
                     "every(POLL_LIVE_MS, async () => {" in detail and 'if (run.state !== "running") stopTimers();' in detail
                     and "if (busy) return;" in detail and "if (sign === drawn) return;" in detail))

    log.info("▶ 사용법 질문 (09-28)")
    limit = re.search(r"length\(question\) between 1 and (\d+)", schema)
    out.append(check("질문 글자 수 상한 = 서버 check",
                     limit and f"ASK_MAX = {limit.group(1)}" in app and "maxlength: String(ASK_MAX)" in app))
    out.append(check("서버 거절(503·409·429·400·403)을 사람 말로",
                     all(f"PT{c}:" in app for c in (503, 409, 429, 400, 403))))
    out.append(check("답 다시 읽기는 패널 타이머에 등록 (로그아웃 때 멈춤) + 겹쳐 읽지 않음 + 상한은 실패에도",
                     "helpTimers.push(timer)" in app and "for (const t of helpTimers) clearInterval(t);" in app
                     and "if (busy) return;" in app
                     and app.index("Date.now() - since > ASK_GIVE_UP_MS") < app.index("if (error || !q)")))

    log.info("▶ 화면 구조 (09-28 개편)")
    out.append(check("★ 사용법 질문은 어디서나 여는 옆 패널 — 입력칸이 대화 아래에 늘 보인다 (대화만 스크롤)",
                     'id="helpBtn"' in html and 'id="helpPanel"' in html and ".help-log { flex:1; overflow-y:auto;" in html
                     and "panel.append(h(\"div\", { class: \"help-head\" }" in app
                     and app.index('class: "help-input"') > app.index("log, done,")))
    out.append(check("대화 영역은 role=log, 자라는 답은 aria-busy, '답 완료'·'답을 받지 못함' 만 따로 읽힌다",
                     'role: "log"' in app and '"aria-busy"' in app
                     and 'done.textContent = text ? "답을 받지 못함" : "답 완료";' in app))
    out.append(check("로그아웃 전 요청이 늦게 와도 다음 사람의 대화에 안 섞인다 (세대) + 쓰던 글도 비운다",
                     app.count("if (mine !== epoch) return;") >= 3 and "epoch += 1;" in app
                     and 'box.value = note.textContent = done.textContent = "";' in app))
    out.append(check("답을 기다리는 중에 보내면 조용히 무시하지 않고 알린다 (쓰던 글은 덮지 않는다)",
                     app.count("if (btn.disabled) { note.textContent = ASK_ERRORS.PT409; return; }") == 2))

    log.info("▶ 검토 반영 (09-28)")
    out.append(check("불러오는 사이 화면을 옮기면 늦게 온 결과를 붙이지 않는다 (PC·실행 상세)",
                     app.count("if (!head.isConnected) return;") == 2 and "if (save.isConnected) renderDevice(" in app))
    # 화면에 'null' 글자가 실제로 찍히는지는 probe_web_shot 이 본다 (h() 안의 null 은 h() 가 거른다)
    out.append(check("PC 화면 머리는 빈 자리를 걸러서 append 한다 ('null' 글자 방지)",
                     "control ? null : h(" in app and ".filter(Boolean));" in app))
    out.append(check("요약의 이번 달 사용 횟수는 끝까지 읽는다(1000줄 상한) · 5초 갱신에 넣지 않는다",
                     "async function loadMonthUsage(card)" in app and "loadMonthUsage(usageCard);" in app
                     and 'fetchAll(() => scoped(sb.from("usage_daily")' in app
                     and app.count('.order("day").order("device_id")') >= 2))
    out.append(check("읽지 못한 숫자는 0 이 아니라 '—' (없는 숫자를 만들지 않는다)",
                     'const UNREAD = "—";' in app and "running.count == null" in app))
    out.append(check("다시 그려도 키보드 초점이 남는다 + 표 줄은 Enter 로 연다 + 정렬 머리는 단추",
                     "function keepFocus(container, draw)" in app and "rowAction(() => go(\"#/run/\" + r.id), r.id)" in app
                     and 'h("button", { class: "sort", type: "button"' in app))
    out.append(check("웹 예약 줄은 PC 와 같은 꼴만 받는다 (틀리면 저장을 막는다)",
                     "function slotError(line)" in app and "const why = slotError(t);" in app))
    out.append(check("사용자에게 내부 말(행위·답하는 PC)을 보이지 않는다",
                     not re.search(r'text: "[^"]*행위|"답하는 PC', app)))
    out.append(check("Enter 로 보내되 한글 조합 중에는 보내지 않는다 (Shift+Enter 줄바꿈)",
                     "!e.shiftKey && !e.isComposing" in app))
    out.append(check("로그아웃하면 패널을 닫고 대화를 비운다 (다음 사람에게 안 보이게)",
                     "help.close();" in app and "help.reset();" in app))
    out.append(check("탭 넷 + 주소(#/...)로 화면 기억",
                     'const TABS = [["home", "요약"], ["usage", "사용량"], ["devices", "PC"], ["history", "실행 기록"]];' in app
                     and 'addEventListener("hashchange", render)' in app))
    out.append(check("표의 숫자 열은 오른쪽 정렬, 좁은 화면에서는 표만 옆으로 민다",
                     "th.num, td.num { text-align:right;" in html and ".tscroll { overflow-x:auto; }" in html))
    out.append(check("상태는 색만이 아니라 점 + 글자 (WCAG 1.4.1)", ".state-done::before" in html and "state-" in app))

    log.info("▶ 사용량 (09-28)")
    out.append(check("★ 사용량은 usage_daily 를 읽기만 한다 (쓰기는 서버 cron 만) — 업체 범위는 scoped + RLS",
                     'scoped(sb.from("usage_daily")' in app and "usage_daily" in schema
                     and "create policy usage_daily_read" in schema
                     and "revoke execute on function public.refresh_usage(interval) from public, anon, authenticated;" in schema))
    cols = re.search(r"const USAGE_COLS = \[(.*?)\];", app)
    out.append(check("사용량 열 = 서버 usage_daily 의 기능 넷",
                     cols and re.findall(r'\["(\w+)"', cols.group(1)) == ["mail", "orders", "logistics_wait", "logistics"]
                     and all(f"    {c} " in schema or f"    {c}  " in schema for c in ("mail", "orders", "logistics"))))
    refresh = re.search(r"function public\.refresh_usage\(.*?\$\$;", schema, re.S).group(0)
    out.append(check("★ 사용량 SQL — 저장 실패한 물류관리는 안 센다 · (PC, 날) 한 줄 · 180일 경계 · 처음 적용 때 채우기",
                     "coalesce(s.failed_items, 0) = 0" in refresh and "group by p.device_id, p.day, d.account_id" in refresh
                     and "least(since, interval '179 days')" in refresh
                     and "select public.refresh_usage(interval '179 days');" in schema))
    out.append(check("날짜는 한국 시각 — 화면(kstDay)과 서버(Asia/Seoul) 가 같다",
                     "9 * 3600000" in app and "at time zone 'Asia/Seoul'" in schema))
    out.append(check("관리자 전체 보기에서만 업체별 표 (사용자는 자기 업체만)",
                     "if (allAccounts()) body.appendChild(section(\"업체별\"" in app and "return isAdmin && !accountId;" in app))
    out.append(check("자기 질문만 읽는다 (admin 은 RLS 로 전부 보이므로 user_id 로 거른다)",
                     '.eq("user_id", me.id)' in app))
    out.append(check("질문 칸 CSS (답은 줄바꿈 유지)", ".qa .a { white-space:pre-wrap;" in html))

    log.info("▶ 비밀")
    out.append(check("config.js 에 URL·anon 키 자리만, 비밀 키 문구 없음",
                     "anonKey" in config and "url" in config and not re.search(r"service_role|sb_secret_", config)))
    out.append(check("★ git 의 web/ 에는 Supabase 주소·키가 없고 자리표시만 (10-02 — 값은 .env)",
                     '"__SUPABASE_URL__"' in config and '"__SUPABASE_PUBLISHABLE_KEY__"' in config
                     and not re.search(r"https://[a-z0-9]{8,}\.supabase\.co|sb_publishable_[A-Za-z0-9_-]{8,}",
                                       config + html + headers + app)))
    out.append(check("html·js 에 비밀 키 문구 없음", not re.search(r"service_role|sb_secret_", html + app)))
    out.append(check("세션은 sessionStorage (탭 닫으면 끝) + 로그아웃 + 만료 처리",
                     "sessionStorage" in app and "signOut" in app and "SIGNED_OUT" in app))
    out.append(check("가입 없음 — signUp 을 부르지 않는다", "signUp" not in app))
    # wrangler 배포 (09-28): 이름이 주소다. README·설정 파일은 올리지 않는다
    wrangler = (WEB / "wrangler.jsonc").read_text(encoding="utf-8")
    out.append(check("wrangler.jsonc — 이름 rpa-dashboard, 올리는 폴더 build/web (자리표시를 채운 것)",
                     '"name": "rpa-dashboard"' in wrangler and '"directory": "../build/web"' in wrangler))
    bat = (PROJECT_ROOT / "deploy_web.bat").read_bytes()
    out.append(check("deploy_web.bat — CRLF + build_web 먼저 + wrangler@4 deploy (node 22)",
                     b"\r\n" in bat and b"wrangler@4 deploy" in bat
                     and 0 <= bat.find(b"tools.build_web") < bat.find(b"wrangler@4 deploy")))
    out += _check_build_web()

    log.info("▶ 스크립트 구문 (node 가 있으면)")
    node = shutil.which("node")
    if node:
        result = subprocess.run([node, "--check", str(WEB / "app.js")], capture_output=True, text=True, timeout=30)
        out.append(check("node --check app.js 통과", result.returncode == 0, result.stderr.strip()[:200]))
        result = subprocess.run([node, "--check", str(WEB / "config.js")], capture_output=True, text=True, timeout=30)
        out.append(check("node --check config.js 통과", result.returncode == 0, result.stderr.strip()[:200]))
    else:
        log.info("  건너뜀  node 가 없어 구문 검사를 못 한다")

    log.info("결과: 통과 %d / 실패 %d", sum(out), len(out) - sum(out))
    return 0 if all(out) else 1


def _check_build_web() -> list[bool]:
    """`tools/build_web` — 가짜 .env 값으로 임시 폴더에 만들어 본다 (진짜 .env·build/web 을 건드리지 않는다)."""
    import tempfile

    from tools import build_web

    log.info("▶ 배포 준비 (build_web)")
    fake = {"SUPABASE_URL": "https://abcdefghij.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_FAKE_key-1"}
    out_dir = Path(tempfile.mkdtemp()) / "web"
    build_web.build(env=fake, out=out_dir)
    made = {p.name: p.read_text(encoding="utf-8") for p in out_dir.iterdir()}
    out = [check("★ 자리표시를 .env 값으로 채운다 — 주소는 config·meta CSP·_headers 셋 다, 키는 config 에",
                 sorted(made) == sorted(build_web.ASSETS) and not any("__SUPABASE_" in t for t in made.values())
                 and all(fake["SUPABASE_URL"] in made[n] for n in ("config.js", "index.html", "_headers"))
                 and fake["SUPABASE_PUBLISHABLE_KEY"] in made["config.js"], str(sorted(made)))]
    try:
        build_web.build(env={"SUPABASE_URL": "", "SUPABASE_PUBLISHABLE_KEY": "x"}, out=out_dir)
        out.append(check("값이 비었거나 꼴이 틀리면 만들지 않는다", False))
    except build_web.BuildError:
        out.append(check("값이 비었거나 꼴이 틀리면 만들지 않는다", True))
    return out


if __name__ == "__main__":
    sys.exit(main())
