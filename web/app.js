"use strict";
// RPA 서버 대시보드 화면 코드 (docs/SERVER_PLAN.md D-4·D-10). index.html 이 config.js 다음에 읽는다.
// 규칙: 서버에서 온 문자열은 전부 textContent(h() 의 text / 문자열 자식) 로만 그린다. 없는 숫자를 만들지 않는다.
// 화면 (09-28 개편): 위쪽 탭 넷 — 요약 / 사용량 / PC / 실행 기록. 주소(#/...)로 기억해 새로 고쳐도 그 화면이다.
//   [사용법 질문] 은 어느 화면에서나 오른쪽 아래 단추로 여는 옆 패널이다 (입력칸은 패널 아래, 대화만 스크롤).
// 보이는 범위: 사용자는 자기 업체만, 관리자는 전부 — 서버 RLS 가 막는다 (화면에서 숨기는 것이 아니다).

// 상태 글자 — orchestrator/history.py 와 steps.py 의 STATE_LABELS 를 그대로 옮긴다. 서버 전용 running/lost/skipped 추가.
const RUN_STATES = { running: "진행 중", done: "완료", unfinished: "완료 (끝나지 않은 단계 있음)", failed: "실패",
                     cancelled: "중단", lost: "끝나기 전에 끊김", skipped: "건너뜀" };
const STEP_STATES = { pending: "실행 전", running: "진행 중", done: "완료", skipped: "건너뜀", failed: "실패", cancelled: "중단" };
const LOG_CLASSES = { warning: "warning", error: "error" };   // 서버 문자열을 class 로 쓰지 않는다 — 표로 거른다
const HEARTBEAT_STALE_SEC = 90;
const POLL_LIVE_MS = 5000, POLL_HISTORY_MS = 30000, PAGE = 1000, EXPORT_MAX_DAYS = 90;

// 원격 제어·설정 (09-28) — 목록은 orchestrator/modules.py · gui/run_app.SMS_CHOICES 와 같다.
// 바꿀 수 있는 키는 server/schema.sql 의 private.clean_settings 가 거른다 (비밀번호는 없다)
const MODULES = [["mail", "메일 엑셀 받기"], ["orders", "주문수집·매출처리"], ["logistics_wait", "물류대기"], ["logistics", "물류관리"]];
const SOURCES = [["excel", "엑셀수집"], ["site", "자동수집"]];
const SMS = [["휴대폰 연결", "phonelink", "usb"], ["adb (USB 케이블)", "adb", "usb"], ["adb (무선)", "adb", "wireless"], ["자동으로 고름", "auto", "usb"]];
// 매출처리 방식 (10-01) — 값 = automation/order_mapping.SALES_MODES, 글 = gui/run_app.SALES_LABELS
const SALES = [["전체", "전체 매출처리 — 조회 안 된 미매출 주문까지"], ["선택주문", "선택주문 매출처리 — 조회된 주문만"]];
// 단추 이름 (사용자 확정 09-28). 일시정지·계속하기는 **예약**을 멈추고 다시 돌린다 — 도는 실행은 [RPA 종료] 로
const COMMAND_LABELS = { start: "실행", stop: "RPA 종료", pause: "일시정지", resume: "계속하기", settings: "설정 바꾸기" };
const SUSPENDED_TEXT = "이 업체는 관리자가 사용을 중지했습니다. RPA 가 돌지 않습니다 — 관리자에게 문의하세요.";
// 설정은 PC 에만 있다 (10-07) — 화면을 열 때 PC 에 묻는다. PC 는 30초마다 확인하고, 서버는 2분 안에 안 오면 버린다
const SETTINGS_WAIT_MS = 2000, SETTINGS_WAIT_TRIES = 70;
// 실행 중에는 웹에서도 설정을 바꾸지 않는다 (09-29 사용자 요청). 서버 set_device_settings 도 423 으로 거절한다
const RUNNING_LOCK_TEXT = "PC 가 실행 중이라 설정을 바꿀 수 없습니다. 실행이 끝나면 다시 바꿀 수 있습니다.";
const ONLINE_SEC = 90;
const ONLINE_TEXT = "켜져 있음", OFFLINE_TEXT = "꺼짐 (연결 안 됨)";   // PC 는 30초마다 서버에 묻는다 — 90초 넘게 조용하면 꺼졌거나 끊긴 것

// 사용량(트래픽) — server/schema.sql 9절 usage_daily. 행위 수가 요금 기준이다 (docs/BILLING.md)
const USAGE_COLS = [["mail", "메일 엑셀 받기"], ["orders", "주문수집·매출처리"], ["logistics_wait", "물류대기"], ["logistics", "물류관리"]];
const PERIODS = [["this", "이번 달"], ["last", "지난달"], ["30", "최근 30일"]];
const TABS = [["home", "요약"], ["usage", "사용량"], ["devices", "PC"], ["history", "실행 기록"]];

const cfg = window.RPA_CONFIG || {};
const main = document.getElementById("main");
let sb = null, me = null, isAdmin = false, accountId = "", timers = [], onVisible = null, myMembers = [], accountNames = {};
let accountSuspended = {};   // 업체 id → 중지 시각 (10-07). 관리자가 PC 탭에서 중지·해제한다
let usagePeriod = "this";

// ---------------------------------------------------------------- DOM (textContent 만)
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v == null ? "" : String(v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
function pad(n) { return String(n).padStart(2, "0"); }
function localDay(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function fmtTime(iso, withDate = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return withDate ? `${d.getMonth() + 1}/${d.getDate()} ${t}` : t;
}
function fmtDur(sec) {
  if (sec == null || isNaN(sec) || sec < 0) return "";
  sec = Math.floor(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? `${m}분 ${s}초` : `${s}초`;
}
function fmtNum(n) { return Number(n || 0).toLocaleString("ko-KR"); }
function ageSec(iso) { return iso ? (Date.now() - new Date(iso).getTime()) / 1000 : Infinity; }
function agoText(iso) {
  const a = ageSec(iso);
  if (!isFinite(a)) return "기록 없음";
  if (a < 60) return "방금";
  if (a < 3600) return `${Math.floor(a / 60)}분 전`;
  if (a < 86400) return `${Math.floor(a / 3600)}시간 전`;
  return `${Math.floor(a / 86400)}일 전`;
}
// 업체 전용 빌드의 기능 이름 (10-08) — 이 파일은 모든 업체가 받는 공개 파일이라 업체 기능을 적지 않는다.
// PC 가 설정 답에 실어 보낸 [id, 이름] 을 기억해 쓴다 (docs/CUSTOMERS.md 4절)
const customModules = new Map();
function rememberModules(list) {
  for (const m of list || []) if (Array.isArray(m) && /^cx_/.test(m[0])) customModules.set(m[0], m[1]);
}
function moduleName(id) {
  const base = MODULES.find(m => m[0] === id);
  return base ? base[1] : (customModules.get(id) || id);
}
function modulesText(mods) {
  if (!mods || !mods.length) return "전체";
  return mods.map(moduleName).join(", ");
}
function stateCell(state, labels) {
  const known = Object.prototype.hasOwnProperty.call(labels, state);
  return h("span", { class: known ? "state-" + state : "", text: known ? labels[state] : String(state || "") });
}
function say(el, text, cls) { clear(el); el.appendChild(h("div", { class: cls || "empty", text })); }
// 내용이 들어올 자리 — 빈 상태 모양은 안쪽 줄에만 준다 (자리 자체에 주면 들어온 표·카드까지 가운데 정렬된다)
function loading() { return h("div", {}, h("div", { class: "empty", text: "불러오는 중..." })); }
function stopTimers() {
  for (const t of timers) clearInterval(t);
  timers = [];
  if (onVisible) { document.removeEventListener("visibilitychange", onVisible); onVisible = null; }
}
function scoped(q) { return accountId ? q.eq("account_id", accountId) : q; }
function allAccounts() { return isAdmin && !accountId; }
function section(title, ...children) { return h("section", {}, title ? h("h2", { text: title }) : null, ...children); }
function table(head, rows, foot) {
  return h("div", { class: "tscroll" }, h("table", {}, h("thead", {}, h("tr", {}, ...head)), h("tbody", {}, ...rows), foot || null));
}
function th(text, cls) { return h("th", { class: cls || "", text }); }
// 눌러서 가는 카드 — 마우스와 키보드(Enter·Space) 둘 다
function pressable(onclick) {
  return { onclick, role: "button", tabindex: "0",
           onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onclick(); } } };
}
// 표의 줄 — 역할은 줄 그대로 두고(표 읽기가 안 깨지게) 초점·Enter 만 준다
function rowAction(onclick, key) {
  return { class: "clickable", "data-key": key, tabindex: "0", onclick,
           onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); onclick(); } } };
}
function stat(label, value, sub, onclick, bad) {
  return h("div", Object.assign({ class: "stat" + (onclick ? " clickable" : "") + (bad ? " bad" : "") },
                                onclick ? pressable(onclick) : {}),
    h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), h("div", { class: "sub", text: sub || "" }));
}
// 주기적으로 다시 그리는 표·카드 — 키보드 초점이 있던 줄(data-key)로 초점을 돌려준다
function keepFocus(container, draw) {
  const active = document.activeElement;
  const key = active && container.contains(active) ? active.getAttribute("data-key") : null;
  draw();
  if (!key) return;
  const again = [...container.querySelectorAll("[data-key]")].find(el => el.getAttribute("data-key") === key);
  if (again) again.focus();
}

// 한국 시각 날짜 — 서버의 usage_daily.day 와 같은 기준 (PC 가 어느 시간대든)
function kstDay(d) { return new Date(d.getTime() + 9 * 3600000).toISOString().slice(0, 10); }
function addDays(day, n) { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function periodRange(p) {
  const today = kstDay(new Date());
  const [y, m] = today.split("-").map(Number);
  const first = `${y}-${pad(m)}-01`;
  if (p === "this") return [first, today];
  if (p === "last") return [`${m === 1 ? y - 1 : y}-${pad(m === 1 ? 12 : m - 1)}-01`, addDays(first, -1)];
  return [addDays(today, -29), today];
}

// ---------------------------------------------------------------- 로그인
function renderLogin(msg) {
  stopTimers();
  clear(main);
  const email = h("input", { type: "email", autocomplete: "username", required: "" });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: "" });
  const err = h("div", { class: "err", role: "alert", text: msg || "" });
  const go_ = h("button", { class: "primary", type: "submit", text: "들어가기" });
  const form = h("form", { class: "login", onsubmit: async e => {
    e.preventDefault();
    if (go_.disabled) return;                       // 두 번 눌러도 로그인·boot 가 겹치지 않게
    go_.disabled = true;
    err.textContent = "";
    const { error } = await sb.auth.signInWithPassword({ email: email.value.trim(), password: pw.value });
    if (error) { err.textContent = "로그인하지 못했습니다. 이메일·비밀번호를 확인하세요."; go_.disabled = false; return; }
    await boot();
  } }, h("h2", { text: "로그인" }), h("label", {}, "이메일", email), h("label", {}, "비밀번호", pw),
     go_, err,
     h("div", { class: "note", text: "계정은 관리자가 만듭니다. 비밀번호를 잊었으면 관리자에게 재설정을 요청하세요." }));
  main.appendChild(form);
}

function showHeader(on) {
  document.getElementById("logoutBtn").classList.toggle("hidden", !on);
  document.getElementById("tabs").classList.toggle("hidden", !on);
  document.getElementById("helpBtn").classList.toggle("hidden", !on);
  if (!on) {
    document.getElementById("accountSel").classList.add("hidden");
    document.getElementById("who").textContent = "";
    help.close();
    help.reset();
  }
}

async function boot() {
  const { data: { session } } = await sb.auth.getSession();
  if (!session) { renderLogin(); return; }
  me = session.user;
  const { data: members, error } = await sb.from("account_members").select("account_id, role").eq("user_id", me.id);
  if (error) { renderLogin("계정 정보를 읽지 못했습니다: " + error.message); return; }
  if (!members || !members.length) {
    document.getElementById("logoutBtn").classList.remove("hidden");
    document.getElementById("who").textContent = me.email || "";
    clear(main);
    main.appendChild(section("", h("div", { class: "err", text: "이 로그인은 아직 업체 계정에 연결되지 않았습니다. 관리자에게 요청하세요." })));
    return;
  }
  myMembers = members;
  isAdmin = members.some(m => m.role === "admin");
  document.getElementById("who").textContent = (me.email || "") + (isAdmin ? " (관리자)" : "");
  showHeader(true);
  buildTabs();
  await setupAccountSelect();
  render();
}

async function setupAccountSelect() {
  const sel = document.getElementById("accountSel");
  const { data: accounts } = await sb.from("accounts").select("id, name, suspended_at").order("name");
  clear(sel);
  const list = accounts || [];
  accountNames = Object.fromEntries(list.map(a => [a.id, a.name]));
  accountSuspended = Object.fromEntries(list.map(a => [a.id, a.suspended_at || null]));
  if (isAdmin) sel.appendChild(h("option", { value: "", text: "전체 업체" }));
  for (const a of list) sel.appendChild(h("option", { value: a.id, text: a.name }));
  accountId = isAdmin ? "" : (list[0] ? list[0].id : "");
  sel.value = accountId;
  sel.classList.toggle("hidden", list.length < 2 && !isAdmin);
  sel.onchange = () => { accountId = sel.value; render(); };
}
function pickAccount(id) {
  accountId = id;
  document.getElementById("accountSel").value = id;
  render();
}

// ---------------------------------------------------------------- 화면 이동 (주소 #/탭 · #/device/id · #/run/id)
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
function buildTabs() {
  const nav = document.getElementById("tabs");
  clear(nav);
  for (const [id, label] of TABS) nav.appendChild(h("button", { "data-tab": id, text: label, onclick: () => go("#/" + id) }));
}
function markTab(active) {
  for (const b of document.querySelectorAll("#tabs button")) {
    if (b.getAttribute("data-tab") === active) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
}
function render() {
  if (!me || !myMembers.length) return;
  const [, page, id] = (location.hash || "#/home").split("/");
  stopTimers();
  clear(main);
  if (page === "device" && id) { markTab("devices"); renderDevice(id); return; }
  if (page === "run" && id) { markTab("history"); renderDetail(id); return; }
  const known = TABS.some(t => t[0] === page) ? page : "home";
  markTab(known);
  ({ home: renderHome, usage: renderUsage, devices: renderDevices, history: renderHistory })[known]();
}
function every(ms, fn) {
  const tick = () => { if (!document.hidden) fn(); };
  tick();
  timers.push(setInterval(tick, ms));
  // 탭이 다시 보이면 바로 갱신한다 (숨겨진 동안은 건너뛰어 낡은 표가 남는다)
  const prev = onVisible;
  onVisible = () => { if (prev) prev(); if (!document.hidden) fn(); };
  if (prev) document.removeEventListener("visibilitychange", prev);
  document.addEventListener("visibilitychange", onVisible);
}

// ---------------------------------------------------------------- 요약 (첫 화면)
function renderHome() {
  // 카드는 한 번 만들고 값만 바꾼다 — 주기마다 다시 만들면 키보드 초점이 사라진다
  const usageCard = stat("이번 달 사용 횟수 (요금 기준)", "…", "", () => { usagePeriod = "this"; go("#/usage"); });
  const runningCard = stat("지금 실행 중", "…", "5초마다 갱신");
  const pcCard = stat("켜져 있는 PC", "…", "90초 안에 신호가 온 PC", () => go("#/devices"));
  const needCard = stat("최근 7일 확인이 필요한 실행", "…", "실패·끊김·확인할 것이 남은 실행", () => go("#/history"));
  const live = loading(), recent = loading();
  main.append(h("div", { class: "page-title" }, h("h2", { text: allAccounts() ? "요약 — 전체 업체" : "요약" })),
    h("div", { class: "stats" }, usageCard, runningCard, pcCard, needCard),
    section("지금 실행 중", live), section("최근 7일 — 확인이 필요한 실행", recent));
  loadMonthUsage(usageCard);                        // 서버가 10분마다 세는 숫자 — 화면을 열 때 한 번, 끝까지 읽는다
  every(POLL_LIVE_MS, () => { loadLiveStats(runningCard, pcCard); loadLive(live); });
  every(POLL_HISTORY_MS, () => loadRecentProblems(recent, needCard));
}

const UNREAD = "—";   // 읽지 못한 숫자 자리 — 0 으로 그리지 않는다 (없는 숫자를 만들지 않는다)
function setStat(card, value, sub, bad) {
  card.querySelector(".value").textContent = value;
  if (sub != null) card.querySelector(".sub").textContent = sub;
  card.classList.toggle("bad", !!bad);
}

async function loadMonthUsage(card) {
  const [from, to] = periodRange("this");
  try {
    const rows = await fetchAll(() => scoped(sb.from("usage_daily").select("actions, runs, day, device_id")
      .gte("day", from).lte("day", to).order("day").order("device_id")));
    const sum = sumUsage(rows);
    setStat(card, `${fmtNum(sum.actions)}회`, `실행 ${fmtNum(sum.runs)}번 (참고)`);
  } catch (e) {
    setStat(card, UNREAD, "읽지 못했습니다 — 잠시 뒤 새로 고침하세요");
  }
}

async function loadLiveStats(runningCard, pcCard) {
  const [devs, running] = await Promise.all([
    scoped(sb.from("devices").select("last_seen_at, revoked_at")),
    scoped(sb.from("runs").select("id", { count: "exact", head: true }).eq("state", "running")),
  ]);
  if (running.error || running.count == null) setStat(runningCard, UNREAD, "읽지 못했습니다");
  else setStat(runningCard, `${fmtNum(running.count)}건`, "5초마다 갱신");
  if (devs.error) { setStat(pcCard, UNREAD, "읽지 못했습니다"); return; }
  const live = (devs.data || []).filter(d => !d.revoked_at);
  const online = live.filter(d => ageSec(d.last_seen_at) < ONLINE_SEC).length;
  setStat(pcCard, `${online} / ${live.length}대`, "90초 안에 신호가 온 PC");
}

async function loadLive(body) {
  const { data, error } = await scoped(sb.from("runs").select("*, devices(name), accounts(name)").eq("state", "running").order("received_at", { ascending: false }));
  if (error) { say(body, "읽지 못했습니다 — 잠시 뒤 다시 읽습니다.", "err"); return; }
  if (!data || !data.length) { say(body, "지금 도는 실행이 없습니다. 예약 시각이 되거나 [PC] 에서 [실행] 을 누르면 여기에 나타납니다."); return; }
  keepFocus(body, () => {
    const cards = h("div", { class: "cards" });
    for (const r of data) cards.appendChild(runCard(r));
    clear(body); body.appendChild(cards);
  });
}

function runCard(r) {
  const stale = ageSec(r.heartbeat_at || r.received_at) > HEARTBEAT_STALE_SEC;
  const where = [allAccounts() && r.accounts ? r.accounts.name : null, r.devices ? r.devices.name : null].filter(Boolean).join(" · ");
  const title = h("div", {}, h("span", { class: "big", text: r.current_name || "시작 중" }),
    r.step_total ? h("span", { class: "tag", text: `${r.current_index || 0}/${r.step_total}` }) : null,
    stale ? h("span", { class: "tag bad", text: "연결 끊김" }) : null,
    r.screen_locked ? h("span", { class: "tag warn", text: "화면 잠김 — 진행되지 않음" }) : null,
    r.paused ? h("span", { class: "tag warn", text: "일시정지" }) : null);
  // 경과는 서버가 받은 시각(received_at) 기준 — PC 시계가 어긋나도 틀리지 않는다
  return h("div", Object.assign({ class: "card clickable", "data-key": r.id }, pressable(() => go("#/run/" + r.id))), title,
    where ? h("div", { class: "note", text: where }) : null,
    h("div", { class: "row" }, h("span", { text: r.trigger || "" }), h("span", { text: modulesText(r.modules) })),
    r.current_activity ? h("div", { text: r.current_activity }) : null,
    h("div", { class: "row" }, h("span", { text: "시작 " + fmtTime(r.started_at || r.received_at) }),
      h("span", { text: "경과 " + fmtDur(ageSec(r.received_at)) })),
    h("div", { class: "row" }, h("span", { text: "마지막 신호 " + agoText(r.heartbeat_at || r.received_at) })));
}

// 사람이 봐야 하는 실행 — 실패·끊김, 알림을 닫고 넘어간 것, 확인할 것이 남은 것
function needsCheck(r) { return ["failed", "lost", "unfinished"].includes(r.state) || (r.attention || 0) > 0; }
async function loadRecentProblems(body, card) {
  const since = new Date(Date.now() - 7 * 86400000).toISOString();
  // ponytail: 최근 500건 안에서 거른다 — 7일에 500건을 넘는 규모가 되면 서버 쪽 필터(or)로 바꾼다
  const { data, error } = await scoped(sb.from("runs").select("*, devices(name), accounts(name)").neq("state", "running")
    .gte("received_at", since).order("received_at", { ascending: false }).limit(500));
  if (error) { say(body, "읽지 못했습니다 — 잠시 뒤 다시 읽습니다.", "err"); setStat(card, UNREAD, "읽지 못했습니다"); return; }
  const need = (data || []).filter(needsCheck);
  setStat(card, `${fmtNum(need.length)}건`, "실패·끊김·확인할 것이 남은 실행", need.length > 0);
  if (!need.length) { say(body, "최근 7일 동안 확인이 필요한 실행이 없습니다."); return; }
  keepFocus(body, () => { clear(body); body.appendChild(runTable(need.slice(0, 10))); });
}

// ---------------------------------------------------------------- 사용량 (트래픽)
async function renderUsage() {
  const seg = h("div", { class: "seg", role: "group", "aria-label": "기간" });
  for (const [id, label] of PERIODS) {
    seg.appendChild(h("button", { "aria-pressed": String(id === usagePeriod), text: label, onclick: () => { usagePeriod = id; render(); } }));
  }
  const dlNote = h("span", { class: "note", role: "status" });
  const dl = h("button", { text: "엑셀로 내려받기" });
  dl.disabled = true;                                 // 다 읽은 뒤에 켠다 — 그 전에 누르면 아무 일도 없다
  const [from, to] = periodRange(usagePeriod);
  const who = allAccounts() ? "전체 업체" : (accountNames[accountId] || "");
  main.append(h("div", { class: "page-title" }, h("h2", { text: "사용량" + (who ? ` — ${who}` : "") }), seg, dl, dlNote),
    h("div", { class: "note period", text: `${from} ~ ${to} · 한국 시각 날짜` }));
  const body = loading();
  main.appendChild(body);
  let rows;
  try {
    rows = await fetchAll(() => scoped(sb.from("usage_daily").select("*, devices(name), accounts(name)")
      .gte("day", from).lte("day", to).order("day").order("device_id")));
  } catch (e) { say(body, "사용량을 읽지 못했습니다 — 잠시 뒤 새로 고침하세요.", "err"); return; }
  dl.addEventListener("click", () => exportUsage(rows, from, to, dlNote));
  dl.disabled = false;
  clear(body);

  const total = sumUsage(rows);
  const totalCard = stat("사용 횟수 합계 (요금 기준)", `${fmtNum(total.actions)}회`, `실행 ${fmtNum(total.runs)}번 — 실패 포함, 참고`);
  totalCard.classList.add("primary");
  body.append(h("div", { class: "stats usage" }, totalCard,
    ...USAGE_COLS.map(([k, label]) => stat(label, `${fmtNum(total[k])}회`, k === "mail" ? "처리한 메일이 있던 실행" : "끝까지 마친 실행"))));

  body.appendChild(section("날짜별 사용 횟수", usageChart(rows, from, to)));
  if (allAccounts()) body.appendChild(section("업체별", byAccountTable(rows)));
  body.appendChild(section("PC별", byDeviceTable(rows)));
  body.appendChild(h("div", { class: "note", text:
    "세는 법: 기능마다 끝까지 마친 실행 한 번을 1회로 셉니다. 메일 엑셀 받기는 메일 사이트에서 처리한 메일이 있으면 " +
    "몇 통이든 1회입니다(받기 실패는 빼고, 열어 보니 엑셀이 아니었던 메일은 넣습니다). " +
    "실패하거나 할 일이 없어 건너뛴 기능은 세지 않습니다. " +
    "10분마다 모아 계산하고, 날짜는 한국 시각입니다." }));
  if (isAdmin) body.appendChild(h("div", { class: "note", text:
    "관리자 참고: 메일 기능의 횟수는 09-28 이후 빌드부터 셉니다 (그 전 빌드는 메일 건수를 서버로 보내지 않습니다)." }));
}

function sumUsage(rows) {
  const out = { actions: 0, runs: 0, mail: 0, orders: 0, logistics_wait: 0, logistics: 0 };
  for (const r of rows) for (const k of Object.keys(out)) out[k] += r[k] || 0;
  return out;
}

function usageChart(rows, from, to) {
  const perDay = {};
  for (const r of rows) perDay[r.day] = (perDay[r.day] || 0) + (r.actions || 0);
  const days = [];
  for (let d = from; d <= to; d = addDays(d, 1)) days.push(d);
  const max = Math.max(1, ...days.map(d => perDay[d] || 0));
  const total = days.reduce((a, d) => a + (perDay[d] || 0), 0);
  if (!total) return h("div", { class: "empty", text: "이 기간에 센 사용 횟수가 없습니다. 실행이 끝나면 10분 안에 여기에 나타납니다." });
  const step = days.length > 16 ? 5 : 1;
  const chart = h("div", { class: "chart", role: "img", "aria-label": `${from} ~ ${to} 날짜별 사용 횟수, 가장 많은 날 ${fmtNum(max)}회` },
    h("span", { class: "ymax", "aria-hidden": "true", text: `${fmtNum(max)}회` }));
  const axis = h("div", { class: "axis", "aria-hidden": "true" });
  days.forEach((d, i) => {
    const n = perDay[d] || 0;
    const [, m, day] = d.split("-");
    const bar = h("div");
    bar.style.height = `${Math.round(100 * n / max)}%`;              // 숫자만 style 로 간다
    chart.appendChild(h("div", { class: "col" + (n ? "" : " zero"), title: `${+m}/${+day} — ${fmtNum(n)}회` }, bar));
    // 눈금은 step 마다 + 마지막 날 (앞 눈금과 붙으면 마지막은 뺀다 — 글자가 겹친다)
    const last = i === days.length - 1 && (i % step) >= Math.ceil(step / 2);
    axis.appendChild(h("span", { text: (i % step === 0 || last) ? `${+m}/${+day}` : "" }));
  });
  // 막대의 뜻을 화면낭독기와 정확한 값이 필요한 사람에게 — 같은 숫자를 표로
  const rowsEl = days.filter(d => perDay[d]).map(d => h("tr", {}, h("td", { text: d }), h("td", { class: "num", text: fmtNum(perDay[d]) })));
  return h("div", {}, chart, axis, h("details", {}, h("summary", { text: "날짜별 숫자 보기" }),
    table([th("날짜"), th("사용 횟수", "num")], rowsEl)));
}

// 머리를 누르면 그 열로 정렬한다 (숫자는 큰 것부터)
function sortableTable(cols, items, onRow) {
  let key = cols.find(c => c.sortFirst) ? cols.find(c => c.sortFirst).key : cols[0].key, desc = true;
  const wrap = h("div");
  const draw = () => keepFocus(wrap, () => {
    const sorted = [...items].sort((a, b) => {
      const x = a[key], y = b[key];
      const cmp = typeof x === "number" ? x - y : String(x || "").localeCompare(String(y || ""), "ko");
      return desc ? -cmp : cmp;
    });
    const head = cols.map(c => h("th", { class: `${c.num ? "num" : ""} ${c.opt ? "opt" : ""}`, scope: "col",
      "aria-sort": c.key === key ? (desc ? "descending" : "ascending") : "none" },
      h("button", { class: "sort", type: "button", "data-key": "sort:" + c.key, text: c.label + (c.key === key ? (desc ? " ▼" : " ▲") : ""),
        onclick: () => { if (key === c.key) desc = !desc; else { key = c.key; desc = !!c.num; } draw(); } })));
    const body = sorted.map(it => h("tr", onRow ? rowAction(() => onRow(it), it.id) : {},
      ...cols.map(c => h("td", { class: `${c.num ? "num" : ""} ${c.opt ? "opt" : ""}`, text: c.num ? fmtNum(it[c.key]) : (it[c.key] || "") }))));
    const sum = sumUsage(items);
    const foot = h("tfoot", {}, h("tr", {}, ...cols.map((c, i) => h("td", { class: `${c.num ? "num" : ""} ${c.opt ? "opt" : ""}`,
      text: i === 0 ? "합계" : (c.num && c.key in sum ? fmtNum(sum[c.key]) : "") }))));
    clear(wrap);
    wrap.appendChild(items.length ? table(head, body, foot) : h("div", { class: "empty", text: "이 기간에 기록이 없습니다." }));
  });
  draw();
  return wrap;
}
function usageColumns(first) {
  return [first, { key: "actions", label: "사용 횟수", num: true, sortFirst: true },
    ...USAGE_COLS.map(([k, label]) => ({ key: k, label, num: true, opt: true })),
    { key: "runs", label: "실행 (참고)", num: true, opt: true }];
}
function groupUsage(rows, keyOf, init) {
  const map = new Map();
  for (const r of rows) {
    const k = keyOf(r);
    if (!map.has(k)) map.set(k, Object.assign({ actions: 0, runs: 0, mail: 0, orders: 0, logistics_wait: 0, logistics: 0, last: "" }, init(r)));
    const g = map.get(k);
    for (const c of ["actions", "runs", "mail", "orders", "logistics_wait", "logistics"]) g[c] += r[c] || 0;
    if (r.actions && r.day > g.last) g.last = r.day;
    if (g.devices) g.devices.add(r.device_id);
  }
  return [...map.values()];
}
function byAccountTable(rows) {
  const items = groupUsage(rows, r => r.account_id,
    r => ({ id: r.account_id, name: r.accounts ? r.accounts.name : (accountNames[r.account_id] || ""), devices: new Set() }))
    .map(g => Object.assign(g, { pcs: g.devices.size }));
  const cols = usageColumns({ key: "name", label: "업체" });
  cols.splice(2, 0, { key: "pcs", label: "PC 수", num: true, opt: true });
  cols.push({ key: "last", label: "마지막 사용일", opt: true });
  return h("div", {}, h("div", { class: "note", text: "업체를 누르면 그 업체만 보입니다 (위쪽 업체 고르기와 같습니다)." }),
    sortableTable(cols, items, it => pickAccount(it.id)));
}
function byDeviceTable(rows) {
  const items = groupUsage(rows, r => r.device_id, r => ({ id: r.device_id, name: r.devices ? r.devices.name : "",
    account: r.accounts ? r.accounts.name : "" }));
  const cols = usageColumns({ key: "name", label: "PC" });
  if (allAccounts()) cols.splice(1, 0, { key: "account", label: "업체" });
  cols.push({ key: "last", label: "마지막 사용일", opt: true });
  return sortableTable(cols, items, it => go("#/device/" + it.id));
}
function exportUsage(rows, from, to, note) {
  if (!window.XLSX) { note.textContent = "엑셀 도구를 읽지 못했습니다. 새로 고친 뒤 다시 누르세요."; return; }
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows.map(r => ({
    "날짜(한국)": r.day, "업체": r.accounts ? r.accounts.name : "", "PC": r.devices ? r.devices.name : "",
    "메일 엑셀 받기": r.mail || 0, "주문수집·매출처리": r.orders || 0, "물류대기": r.logistics_wait || 0, "물류관리": r.logistics || 0,
    "사용 횟수 합계": r.actions || 0, "실행 (참고)": r.runs || 0 }))), "사용량");
  XLSX.writeFile(wb, `RPA_사용량_${from}_${to}.xlsx`);
  note.textContent = `${rows.length}줄을 받았습니다.`;
}

// ---------------------------------------------------------------- PC 목록
function renderDevices() {
  const body = loading();
  main.append(h("div", { class: "page-title" }, h("h2", { text: "PC" })));
  // 업체 단위 중지 (10-07) — 관리자는 업체를 고르면 중지·해제, 그 업체 사람은 중지됐다는 안내만
  if (isAdmin && accountId) main.append(suspendSection(accountId));
  else if (accountId && accountSuspended[accountId]) main.append(section("", h("div", { class: "err", text: SUSPENDED_TEXT })));
  main.append(section("", body, h("div", { class: "note", text: "PC 를 누르면 켜짐 상태·원격 제어·설정·명령 기록이 열립니다." })));
  every(POLL_LIVE_MS, () => loadDevices(body));
}

function suspendSection(accId) {
  const box = h("section", {});
  const draw = () => {
    clear(box);
    const since = accountSuspended[accId], name = accountNames[accId] || "";
    const note = h("div", { class: "note", role: "status" });
    const btn = h("button", { class: since ? "primary" : "", text: since ? "중지 풀기" : "이 업체 RPA 중지", onclick: async () => {
      const ask = since ? `'${name}' 업체의 중지를 풉니다. 그 업체의 PC 는 10분 안에 다시 돕니다. 계속할까요?`
        : `'${name}' 업체의 모든 PC 에서 RPA 를 멈춥니다. 실행·예약이 막히고 업체 담당자는 웹에서 제어할 수 없습니다. 계속할까요?`;
      if (!window.confirm(ask)) return;
      btn.disabled = true; note.textContent = "보내는 중...";
      const { error } = await sb.rpc(since ? "resume_account" : "suspend_account", { account: accId });
      if (!box.isConnected) return;
      btn.disabled = false;
      if (error) { note.textContent = "하지 못했습니다: " + error.message; return; }
      accountSuspended[accId] = since ? null : new Date().toISOString();
      draw();
    } });
    box.append(h("h2", { text: "업체 사용" }),
      h("div", {}, h("span", { class: since ? "state-failed" : "state-done", text: since ? `중지됨 — ${fmtTime(since)}부터` : "사용 중" })),
      h("div", { class: "toolbar" }, btn),
      h("div", { class: "note", text: "중지하면 그 업체의 모든 PC 가 30초 안에 실행·예약을 멈춥니다 (도는 실행은 끝까지 갑니다). " +
        "자료와 빌드는 그대로이고, 중지를 풀면 PC 가 10분 안에 다시 돕니다." }),
      note);
  };
  draw();
  return box;
}

// 09-28: 업체 등록 토큰 발급 폼을 없앴다. 빌드 ID 는 **빌드 프로그램이 빌드할 때** 서버에서 받아
// 굽는다 (`tools/register_build.py` → `register_build` RPC). 관리자가 여기서 복사해 붙일 것이 없다.
async function loadDevices(body) {
  const { data, error } = await scoped(sb.from("devices").select("*, accounts(name)").order("name"));
  if (error) { say(body, "읽지 못했습니다 — 잠시 뒤 다시 읽습니다.", "err"); return; }
  if (!data || !data.length) { say(body, "등록된 PC 가 없습니다. 관리자가 빌드한 프로그램을 PC 에서 처음 켜면 여기에 나타납니다."); return; }
  const head = [allAccounts() ? th("업체") : null, th("PC"), th("상태"), th("마지막 접속"), th("다음 예약", "opt"),
    th("예약", "opt"), th("버전", "opt"), th("알림")].filter(Boolean);
  const rows = data.map(d => {
    const silentDays = Math.floor(ageSec(d.last_seen_at) / 86400);
    const flags = [];
    if (d.revoked_at) flags.push("폐기됨");
    if (d.flag) flags.push(d.flag);
    if (isFinite(silentDays) && silentDays >= 1) flags.push(`${silentDays}일째 보고 없음`);
    if (d.paused) flags.push("일시정지");
    const online = ageSec(d.last_seen_at) < ONLINE_SEC;
    return h("tr", rowAction(() => go("#/device/" + d.id), d.id),
      allAccounts() ? h("td", { text: d.accounts ? d.accounts.name : "" }) : null,
      // 업체 전용 빌드면 그 업체 id 를 붙인다 (10-08, docs/CUSTOMERS.md 확정 3)
      h("td", { text: d.customer ? `${d.name} · 전용 ${d.customer}` : d.name }),
      h("td", {}, h("span", { class: online ? "state-done" : "state-pending", text: online ? ONLINE_TEXT : OFFLINE_TEXT })),
      h("td", { text: d.last_seen_at ? `${fmtTime(d.last_seen_at)} (${agoText(d.last_seen_at)})` : "기록 없음" }),
      h("td", { class: "opt", text: d.next_run_at ? fmtTime(d.next_run_at) : "" }),
      h("td", { class: "opt", text: d.auto_run_enabled == null ? "" : (d.auto_run_enabled ? "켬" : "끔") }),
      h("td", { class: "opt", text: d.app_version || "" }),
      h("td", { class: flags.length ? "err" : "", text: flags.join(" · ") }));
  });
  keepFocus(body, () => { clear(body); body.appendChild(table(head, rows)); });
}

// ---------------------------------------------------------------- PC 한 대 — 제어·설정 (09-28)
// 서버는 PC 를 부르지 않는다. 여기서 넣은 명령·설정은 PC 가 30초마다 가져간다 (server/schema.sql 7절).
function canControl(accId) {
  // 중지된 업체의 owner 는 제어 못 한다 (서버 can_control 과 같게, 10-07) — 단추를 눌러 'forbidden' 을 보지 않게
  return myMembers.some(m => m.role === "admin" || (m.role === "owner" && m.account_id === accId && !accountSuspended[accId]));
}
function checkbox(label, on, disabled) {
  const box = h("input", { type: "checkbox" });
  box.checked = !!on; box.disabled = !!disabled;
  return { box, el: h("label", { class: "check" }, box, " " + label) };
}
function select(options, value, disabled) {
  const sel = h("select");
  // 값이 없으면 빈 칸 — 첫 선택지가 기본값처럼 보이지 않게 (기본값 없음, 10-02)
  const known = options.some(([v]) => v === value);
  if (!known) sel.appendChild(h("option", { value: "", text: "— 고르세요 —" }));
  for (const [v, label] of options) sel.appendChild(h("option", { value: v, text: label }));
  sel.value = known ? value : ""; sel.disabled = !!disabled;
  return sel;
}
function textInput(value, disabled, props) {
  const el = h("input", Object.assign({ type: "text" }, props || {}));
  el.value = value == null ? "" : String(value); el.disabled = !!disabled;
  return el;
}
// 예약 한 줄이 PC 가 받는 꼴인가. 틀리면 사람 말 사유, 맞으면 "" — utils/schedule.py parse_slot 과 같은 규칙
const WEEKDAYS = "월화수목금토일";
function slotError(line) {
  let tokens = line.split(/\s+/).filter(Boolean);
  if (tokens.length && /^[a-z_]+(,[a-z_]+)*$/.test(tokens[tokens.length - 1])) {
    const mods = tokens.pop().split(",");
    if (new Set(mods).size !== mods.length || mods.some(m => !MODULES.some(x => x[0] === m) && !customModules.has(m))) return "끝에 붙인 기능 이름이 틀렸습니다";
  }
  if (tokens.length === 1) tokens = ["매일", tokens[0]];
  if (tokens.length === 3 && tokens[0] === "매월") tokens = [`매월 ${tokens[1]}`, tokens[2]];
  if (tokens.length !== 2) return "꼴이 맞지 않습니다";
  const [days, clock] = tokens;
  const t = clock.split(":");
  if (t.length !== 2 || !/^\d{1,2}$/.test(t[0]) || !/^\d{1,2}$/.test(t[1]) || +t[0] > 23 || +t[1] > 59) return "시각은 09:00 처럼 적습니다";
  if (["매일", "평일", "주말"].includes(days)) return "";
  if (/^매월 \d+일$/.test(days)) { const n = +days.slice(3, -1); return n >= 1 && n <= 31 ? "" : "매월은 1~31일입니다"; }
  if (/^\d{4}-\d{2}-\d{2}$/.test(days)) {
    const d = new Date(days + "T00:00:00Z");
    return !isNaN(d) && d.toISOString().slice(0, 10) === days ? "" : "없는 날짜입니다";
  }
  if (days && [...days].every(c => WEEKDAYS.includes(c)) && new Set(days).size === days.length) return "";
  return "요일은 월수금·평일·주말·매일, 날짜는 2026-10-01 처럼 적습니다";
}
function splitList(text) { return text.split(/[;,\n]/).map(s => s.trim()).filter(Boolean); }

async function renderDevice(deviceId, message) {
  stopTimers();
  clear(main);
  const back = h("button", { text: "← PC 목록", onclick: () => go("#/devices") });
  const head = h("section", {}, h("div", { class: "toolbar" }, back), h("div", { class: "note", text: "불러오는 중..." }));
  main.appendChild(head);
  const { data: dev, error } = await sb.from("devices").select("*, accounts(name)").eq("id", deviceId).single();
  // 기다리는 사이 다른 화면으로 옮겼으면 그리지 않는다 — 제어 단추·타이머가 다른 화면에 붙는다
  if (!head.isConnected) return;
  if (error || !dev) { say(head.children[1], "PC 를 읽지 못했습니다.", "err"); return; }
  if (accountSuspended[dev.account_id]) {
    message = message || (isAdmin ? "이 업체는 사용 중지 상태입니다 — PC 탭에서 업체를 골라 풀 수 있습니다." : SUSPENDED_TEXT);
  }
  const control = canControl(dev.account_id) && !dev.revoked_at;
  let lastSeen = dev.last_seen_at;           // 5초 갱신이 바꾼다 — 설정을 물을 때 켜져 있나 본다
  const status = h("div");
  clear(head.children[1]);
  // ★ append() 에 null 을 넘기면 "null" 글자가 붙는다 — 빈 자리는 걸러서 넘긴다 (09-28 사진으로 발견)
  head.children[1].append(...[h("div", { class: "big", text: `${dev.accounts ? dev.accounts.name : ""} · ${dev.name}` }), status,
    message ? h("div", { class: "err", text: message }) : null,
    control ? null : h("div", { class: "note", text: dev.revoked_at ? "폐기된 PC 입니다." : "보기만 할 수 있습니다 (제어는 관리자·업체 담당자)." })]
    .filter(Boolean));

  // --- 제어 ---
  const cmdBody = loading();
  let showState = null;     // 제어 단추를 지금 상태에 맞춰 켜고 끈다 — refresh 가 부른다
  let showPicks = null;     // PC 설정을 받으면 기능 체크를 PC 값으로 (기능 고정 빌드는 그 기능만)
  if (control) {
    // 업체 전용 빌드면 PC 가 보낸 기능 목록으로 다시 그린다 (10-08) — 처음에는 원본 네 기능
    let picks = [];
    const pickBox = h("span", { class: "toolbar" });
    const drawPicks = list => {
      picks = list.map(([id, name]) => Object.assign(checkbox(name, false), { id }));
      clear(pickBox); pickBox.append(...picks.map(p => p.el));
    };
    drawPicks(MODULES);
    showPicks = snap => {
      if (snap.modules) { rememberModules(snap.modules); drawPicks(snap.modules); }
      const locked = snap.locked_modules, wanted = new Set(snap.settings.run_modules || []);
      for (const p of picks) {
        const allowed = !locked || locked.includes(p.id);
        p.el.classList.toggle("hidden", !allowed);
        p.box.checked = locked ? allowed : wanted.has(p.id);
      }
    };
    const note = h("div", { class: "note", role: "status" });
    const send = async (kind, modules) => {
      const ask = { start: "이 PC 에서 지금 실행합니다. 실계정 저장이 돌 수 있습니다. 계속할까요?",
                    stop: "진행 중인 RPA 를 종료합니다. 이미 저장된 것은 되돌리지 않습니다. 계속할까요?" }[kind];
      if (ask && !window.confirm(ask)) return;
      note.textContent = "보내는 중...";
      const { error: e } = await sb.rpc("send_command", { device: deviceId, kind, modules: modules || [] });
      note.textContent = e ? "보내지 못했습니다: " + e.message
        : "보냈습니다. PC 가 켜져 있으면 30초 안에 가져갑니다 (꺼져 있으면 켜진 뒤, 10분이 지나면 버립니다).";
      loadCommands(cmdBody, deviceId);
    };
    const start = h("button", { class: "primary", text: "실행", onclick: () => {
      const chosen = picks.filter(p => p.box.checked).map(p => p.id);
      if (!chosen.length) { note.textContent = "실행할 기능을 하나 이상 고르세요."; return; }
      send("start", chosen);
    } });
    const stop = h("button", { text: COMMAND_LABELS.stop, onclick: () => send("stop") });
    const pause = h("button", { text: COMMAND_LABELS.pause, onclick: () => send("pause") });
    const resume = h("button", { text: COMMAND_LABELS.resume, onclick: () => send("resume") });
    stop.disabled = pause.disabled = resume.disabled = true;     // 상태를 읽기 전에는 누르지 못한다
    // RPA 종료는 실행 중일 때만, 일시정지는 일시정지가 아닐 때만, 계속하기는 일시정지일 때만 (사용자 확정 09-28)
    showState = (running, paused) => {
      stop.disabled = !running;
      pause.disabled = paused;
      resume.disabled = !paused;
    };
    main.appendChild(h("section", {}, h("h2", { text: "제어" }),
      h("div", { class: "toolbar" }, pickBox, start),
      h("div", { class: "toolbar" }, stop, pause, resume),
      h("div", { class: "note", text: "일시정지는 예약을 멈춥니다 — 도는 실행은 [RPA 종료] 로 멈춥니다. " +
        "PC 의 화면이 잠겨 있거나 이미 실행 중이면 시작하지 않고 그 사유를 아래 기록에 남깁니다." }),
      note));
  }

  // --- 설정 ---
  const settings = settingsSection(deviceId, control, () => ageSec(lastSeen) < ONLINE_SEC, snap => showPicks && showPicks(snap));
  main.appendChild(settings);

  // --- 명령 기록 ---
  main.appendChild(h("section", {}, h("h2", { text: "명령 기록 (최근 20건)" }), cmdBody));

  const refresh = async () => {
    const [{ data: d }, { data: run }] = await Promise.all([
      sb.from("devices").select("last_seen_at, paused, auto_run_enabled, next_run_at").eq("id", deviceId).single(),
      sb.from("runs").select("current_name, trigger").eq("device_id", deviceId).eq("state", "running").limit(1),
    ]);
    clear(status);
    if (d) {
      lastSeen = d.last_seen_at;
      const online = ageSec(d.last_seen_at) < ONLINE_SEC;
      status.append(h("span", { class: online ? "state-done" : "state-pending", text: online ? ONLINE_TEXT : OFFLINE_TEXT }),
        h("span", { class: "note", text: ` · 마지막 접속 ${agoText(d.last_seen_at)}` +
          (d.auto_run_enabled == null ? "" : ` · 예약 ${d.auto_run_enabled ? "켬" : "끔"}${d.paused ? " (일시정지)" : ""}`) +
          (d.next_run_at ? ` · 다음 예약 ${fmtTime(d.next_run_at)}` : "") }));
    }
    if (run && run.length) status.append(h("div", {}, h("span", { class: "state-running", text: `실행 중 — ${run[0].current_name || "시작 중"} (${run[0].trigger || ""})` })));
    // 일시정지(예약)는 devices.paused — PC 가 일시정지·계속하기 때 곧바로 보낸다 (실행 중이어도)
    if (showState && d) showState(!!(run && run.length), !!d.paused);
    settings.setRunning(!!(run && run.length));     // 실행 중이면 설정 칸을 잠근다 (09-29)
    loadCommands(cmdBody, deviceId);
  };
  every(POLL_LIVE_MS, refresh);
}

// 설정 (10-07 개편) — 설정은 PC 에만 있다. 화면을 열 때 PC 에 지금 값을 묻고(request_settings), 답을 한 번 읽는다
// (take_settings — 서버는 읽히면 지운다). 바꾼 값은 명령으로 보내고 PC 가 가져가면 서버에서 지워진다
function settingsSection(deviceId, editable, online, onSnapshot) {
  const status = h("div", { class: "note", role: "status" });
  const body = h("div");
  const reload = h("button", { text: "다시 받아 오기", onclick: () => load() });
  let form = null, running = false;
  const section = h("section", {}, h("h2", { text: "설정" }),
    h("div", { class: "note", text: "설정은 PC 에만 저장됩니다. 이 화면을 열 때 PC 에서 지금 값을 받아 옵니다 (서버에 남지 않습니다)." }),
    h("div", { class: "note", text: "비밀번호와 PC 의 폴더 경로는 여기서 보거나 바꾸지 않습니다 — PC 의 실행 창에서만 넣습니다." }),
    h("div", { class: "toolbar" }, status, reload), body);
  section.setRunning = r => { running = r; if (form) form.setRunning(r); };
  const load = async () => {
    reload.disabled = true; clear(body); form = null;
    if (!online()) { status.textContent = "PC 가 꺼져 있어 설정을 볼 수 없습니다. PC 가 켜지면 [다시 받아 오기] 를 누르세요."; reload.disabled = false; return; }
    status.textContent = "PC 에서 지금 설정을 받아 오는 중입니다... (PC 는 30초마다 확인합니다)";
    const { data: id, error } = await sb.rpc("request_settings", { device: deviceId });
    let got = error ? { error: error.message } : null;
    for (let i = 0; !got && i < SETTINGS_WAIT_TRIES; i++) {
      await new Promise(r => setTimeout(r, SETTINGS_WAIT_MS));
      if (!section.isConnected) return;               // 다른 화면으로 갔다 — 답은 서버가 2분 뒤 지운다
      const { data, error: e } = await sb.rpc("take_settings", { command: id });
      got = e ? { error: e.message } : data;
    }
    if (!section.isConnected) return;
    reload.disabled = false;
    if (!got || !got.settings) {
      status.textContent = !got || got.error === "expired" ? "PC 가 2분 안에 답하지 않았습니다 — 꺼졌거나 서버에 닿지 않습니다."
        : "PC 에서 설정을 받지 못했습니다: " + got.error;
      return;
    }
    status.textContent = `PC 에서 받은 값입니다 (${fmtTime(new Date().toISOString())}).`;
    form = settingsForm(deviceId, got, editable);
    form.setRunning(running);
    body.appendChild(form.el);
    if (onSnapshot) onSnapshot(got);
  };
  load();
  return section;
}

function settingsForm(deviceId, snap, editable) {
  const s = Object.assign({}, snap.settings || {});
  const locked = snap.locked_modules;
  const off = !editable;
  // 업체 전용 빌드면 PC 가 보낸 기능 목록·업체 칸 정의로 그린다 (10-08). 원본 빌드는 둘 다 없다
  const MODS = snap.modules || MODULES;
  rememberModules(snap.modules);
  const mods = MODS.map(([id, name]) => Object.assign(checkbox(name, (s.run_modules || []).includes(id), off || !!locked), { id }));
  const cx = (snap.fields || []).map(f => {
    const v = s[f.key];
    const el = f.kind === "bool" ? checkbox(f.label, v, off)
      : f.kind === "choice" ? select((f.choices || []).map(c => [c, c]), v, off)
      : textInput(f.kind === "list" ? (v || []).join("; ") : v, off);
    return { f, el };
  });
  const cxValue = ({ f, el }) => f.kind === "bool" ? el.box.checked
    : f.kind === "list" ? splitList(el.value)
    : (el.value.trim() || null);
  const srcs = SOURCES.map(([id, name]) => Object.assign(checkbox(name, (s.collect_sources || []).includes(id), off), { id }));
  const courier = textInput(s.delivery_company, off), box = textInput(s.delivery_box, off);
  const mode = select([["자동", "자동 — 저장 뒤 [운송장출력]"], ["수동", "수동 — 저장 뒤 [엑셀파일생성]"]], s.logistics_mode, off);
  // 값이 없으면 빈 칸 — PC 가 빈 값을 올리지 못해 '모르는 PC' 와 가를 수 없다 (10-02 검토, 10-01 전 빌드는 시험판뿐)
  const sales = select(SALES, s.sales_mode, off);
  const hold = textInput((s.hold_exclude_codes || []).join("; "), off, { placeholder: "상품코드; 상품코드" });
  const smsNow = SMS.find(([, src, conn]) => src === s.sms_source && (src !== "adb" || conn === s.adb_connection));
  const sms = select(SMS.map(([label]) => [label, label]), smsNow ? smsNow[0] : "", off);
  const adb = textInput(s.adb_wireless_address, off, { placeholder: "IP:포트" });
  const autoOn = checkbox("예약 실행 켜기", s.auto_run_enabled, off);
  const autoMode = select([["daily", "정해진 날·시각"], ["interval", "정해진 간격"]], s.auto_run_mode, off);
  const times = h("textarea", { rows: "4", text: (s.auto_run_times || []).join("\n") });
  times.disabled = off;
  const interval = textInput(s.auto_run_interval_minutes, off, { type: "number", min: "10", max: "1440" });
  const note = h("div", { class: "note", role: "status" });

  const read = () => {
    const out = {};
    if (!locked) {
      out.run_modules = mods.filter(m => m.box.checked).map(m => m.id);
      if (!out.run_modules.length) return "실행할 기능을 하나 이상 고르세요.";
    }
    // 기본값이 없다 (10-02) — 빈 선택은 보내지 않는다(서버가 빈 값을 받지 않는다). 고른 기능에 필요한 것만 요구한다
    const using = new Set(locked || out.run_modules);
    out.collect_sources = srcs.filter(m => m.box.checked).map(m => m.id);
    if (!out.collect_sources.length) {
      if (using.has("orders")) return "수집방식을 하나 이상 고르세요.";
      delete out.collect_sources;
    }
    out.delivery_company = courier.value.trim() || null;
    out.delivery_box = box.value.trim() || null;
    if (using.has("logistics") && !(out.delivery_company && out.delivery_box)) return "물류관리 택배사·박스를 넣으세요.";
    if (mode.value) out.logistics_mode = mode.value;
    else if (using.has("logistics")) return "물류관리 자동/수동을 고르세요.";
    if (sales.value) out.sales_mode = sales.value;
    else if (using.has("orders")) return "매출처리 방식을 고르세요.";
    out.hold_exclude_codes = splitList(hold.value);
    const picked = SMS.find(([label]) => label === sms.value);
    if (picked) { out.sms_source = picked[1]; out.adb_connection = picked[2]; }
    else if (using.has("mail")) return "인증 문자 방법을 고르세요.";
    out.adb_wireless_address = adb.value.trim() || null;
    out.auto_run_enabled = autoOn.box.checked;
    if (autoMode.value) out.auto_run_mode = autoMode.value;
    else if (out.auto_run_enabled) return "예약 방식(정해진 날·시각 / 정해진 간격)을 고르세요.";
    out.auto_run_times = times.value.split("\n").map(t => t.trim()).filter(Boolean);
    if (out.auto_run_times.length > 12) return "예약 줄은 12개까지입니다.";
    // PC 는 꼴이 틀린 줄을 버리고 PC 화면에만 알린다 — 여기서 먼저 막는다 (웹은 '저장했습니다' 인데 안 도는 일이 없게)
    for (const [i, t] of out.auto_run_times.entries()) {
      const why = slotError(t);
      if (why) return `예약 줄 ${i + 1}번째 '${t}' — ${why}. 예) 평일 09:00 · 월수금 13:00 mail,orders · 매월 25일 18:00`;
    }
    const minutesText = interval.value.trim();
    if (minutesText || out.auto_run_mode === "interval") {
      const minutes = Number(minutesText);
      if (!minutesText || !Number.isInteger(minutes) || minutes < 10 || minutes > 1440) return "간격은 10 ~ 1440 분입니다.";
      out.auto_run_interval_minutes = minutes;
    }
    for (const c of cx) out[c.f.key] = cxValue(c);
    return out;
  };
  const save = h("button", { class: "primary", text: "저장", onclick: async () => {
    const values = read();
    if (typeof values === "string") { note.textContent = values; return; }
    // 바꾼 것만 보낸다 — 그 사이 PC 에서 고친 다른 값을 덮지 않게. 빈 값은 꼴이 달라도 같다
    // (PC 는 "" 를 보내고 서버가 안 받을 목록은 통째로 뺀다 — 웹 칸은 null·[] 이다. 다르게 보면 PC 목록을 [] 로 지운다)
    const blank = v => v == null || v === "" || (Array.isArray(v) && !v.length);
    const same = (a, b) => (blank(a) && blank(b)) || JSON.stringify(a) === JSON.stringify(b);
    const changed = Object.fromEntries(Object.entries(values).filter(([k, v]) => !same(v, s[k])));
    if (!Object.keys(changed).length) { note.textContent = "바뀐 것이 없습니다."; return; }
    save.disabled = true; note.textContent = "보내는 중...";
    const { error } = await sb.rpc("set_device_settings", { device: deviceId, settings: changed });
    save.disabled = false;
    if (error && (error.code === "PT423" || /running/.test(error.message || ""))) {
      note.textContent = RUNNING_LOCK_TEXT; setRunning(true); return;
    }
    if (error && (error.code === "PT429" || /too many/.test(error.message || ""))) {
      note.textContent = "PC 가 앞의 요청을 아직 가져가지 않았습니다. PC 가 켜져 있는지 보고 잠시 뒤 다시 하세요."; return;
    }
    if (error) { note.textContent = "보내지 못했습니다: " + error.message; return; }
    Object.assign(s, changed);
    note.textContent = "보냈습니다. PC 가 30초 안에 제 설정에 저장합니다 — 결과는 아래 명령 기록에 남습니다.";
  } });
  save.disabled = off;
  // PC 가 실행 중이면 칸을 잠근다 (09-29 사용자 요청 — 서버도 423 으로 거절한다). 기기 화면의 5초 갱신이 부른다
  const fields = [...srcs.map(m => m.box), courier, box, mode, sales, hold, sms, adb, autoOn.box, autoMode, times, interval,
    ...cx.map(c => c.el.box || c.el), save];
  const busyNote = h("div", { class: "note", role: "status" });
  const setRunning = running => {
    for (const f of fields) f.disabled = off || running;
    for (const m of mods) m.box.disabled = off || !!locked || running;
    busyNote.textContent = running ? RUNNING_LOCK_TEXT : "";
  };

  // 칸마다 이름을 붙인다 — 화면낭독기가 '편집 텍스트' 로만 읽지 않게 (체크는 label 로 이미 감쌌다)
  const line = (label, ...fields) => {
    for (const f of fields) {
      if (f && f.tagName && /^(INPUT|SELECT|TEXTAREA)$/.test(f.tagName) && !f.getAttribute("aria-label")) f.setAttribute("aria-label", label);
    }
    return [h("div", { class: "note", text: label }), h("div", {}, ...fields)];
  };
  adb.setAttribute("aria-label", "무선 주소");
  const form = h("div", { class: "form" },
    ...line("실행할 기능", ...mods.map(m => m.el), locked ? h("div", { class: "note", text: "이 PC 는 기능이 고정된 빌드입니다." }) : null),
    ...line("수집방식", ...srcs.map(m => m.el)),
    ...line("매출처리", sales),
    ...line("택배사", courier), ...line("박스", box), ...line("자동/수동", mode),
    ...line("보류제외 상품코드", hold),
    ...line("인증 문자", sms, " 무선 주소 ", adb),
    ...line("예약", autoOn.el, " ", autoMode),
    ...line("예약 줄 (한 줄에 하나)", times, h("div", { class: "note", text: "예) 평일 09:00 · 월수금 13:00 mail,orders · 매월 25일 18:00 · 2026-10-01 14:00 — " +
      "끝에 기능을 붙이면 그 기능만: " + MODS.map(([id, name]) => `${id}(${name})`).join(" · ") })),
    ...line("간격 (분)", interval),
    ...(cx.length ? [h("div", { class: "note", text: `${snap.customer || "업체"} 전용` }), h("div")] : []),
    ...cx.flatMap(c => line(c.f.label, c.el.el || c.el)));
  return { el: h("div", {}, busyNote, form, h("div", { class: "toolbar" }, save), note), setRunning };
}

async function loadCommands(body, deviceId) {
  // 설정 값(payload)은 열 권한이 없어 고르지 않는다. 설정 보기 요청(read_settings)은 화면을 열 때마다 생겨 뺀다
  const { data, error } = await sb.from("commands").select("id, kind, modules, created_by, created_at, taken_at, result")
    .eq("device_id", deviceId).neq("kind", "read_settings")
    .order("created_at", { ascending: false }).limit(20);
  if (error) { say(body, "읽지 못했습니다: " + error.message, "err"); return; }
  if (!data || !data.length) { say(body, "보낸 명령이 없습니다."); return; }
  const rows = data.map(c => {
    const names = (c.modules || []).map(moduleName);
    const state = c.result || (c.taken_at ? "PC 가 받음 — 처리 중" : "PC 가 가져가기를 기다리는 중");
    return h("tr", {}, h("td", { text: fmtTime(c.created_at) }),
      h("td", { text: COMMAND_LABELS[c.kind] || String(c.kind || "") }),
      h("td", { class: "opt", text: c.kind === "start" ? (names.length ? names.join(", ") : "PC 의 선택") : "" }),
      h("td", { class: "opt", text: c.created_by || "" }), h("td", { text: state }));
  });
  clear(body);
  body.appendChild(table([th("보낸 시각"), th("명령"), th("기능", "opt"), th("보낸 사람", "opt"), th("결과")], rows));
}

// ---------------------------------------------------------------- 실행 기록
function renderHistory() {
  const body = loading();
  const from = h("input", { type: "date", "aria-label": "시작 날짜" }), to = h("input", { type: "date", "aria-label": "끝 날짜" });
  to.value = localDay(new Date());
  from.value = localDay(new Date(Date.now() - (EXPORT_MAX_DAYS - 1) * 86400000));
  const dlNote = h("span", { class: "note", role: "status" });
  const dlBtn = h("button", { text: "엑셀로 내려받기", onclick: () => exportExcel(from.value, to.value, dlNote, dlBtn) });
  const retention = h("div", { class: "note" });
  main.append(h("div", { class: "page-title" }, h("h2", { text: "실행 기록" })),
    section("", h("div", { class: "toolbar" }, h("span", { class: "note", text: "엑셀로 받을 기간" }), from, h("span", { text: "~" }), to, dlBtn, dlNote),
      retention, body));
  every(POLL_HISTORY_MS, () => loadHistory(body, retention));
}

function runTable(rows) {
  const head = [th("시각"), allAccounts() ? th("업체", "opt") : null, th("PC", "opt"), th("어떻게", "opt"), th("기능", "opt"),
    th("결과"), th("걸린 시간", "opt"), th("확인할 것", "num")].filter(Boolean);
  return table(head, rows.map(r => {
    // 좁은 화면에서는 업체·PC 칸을 숨기므로 결과 칸 안에 한 줄로 보인다 (어느 업체의 실패인지 알게)
    const where = [allAccounts() && r.accounts ? r.accounts.name : null, r.devices ? r.devices.name : null].filter(Boolean).join(" · ");
    return h("tr", rowAction(() => go("#/run/" + r.id), r.id),
      h("td", { class: "nowrap", text: fmtTime(r.finished_at || r.received_at) }),
      allAccounts() ? h("td", { class: "opt", text: r.accounts ? r.accounts.name : "" }) : null,
      h("td", { class: "opt", text: r.devices ? r.devices.name : "" }),
      h("td", { class: "opt nowrap", text: r.trigger || "" }),
      h("td", { class: "opt", text: modulesText(r.modules) }),
      h("td", {}, stateCell(r.state, RUN_STATES), r.summary ? h("span", { text: " — " + r.summary }) : null,
        where ? h("div", { class: "note narrow-only", text: where }) : null),
      h("td", { class: "opt nowrap", text: fmtDur(r.elapsed) }), h("td", { class: "num", text: r.attention ? `${r.attention}건` : "" }));
  }));
}

async function loadHistory(body, retention) {
  const { data, error } = await scoped(sb.from("runs").select("*, devices(name), accounts(name)").neq("state", "running")
    .order("received_at", { ascending: false }).order("id").limit(100));
  if (error) { say(body, "읽지 못했습니다 — 잠시 뒤 다시 읽습니다.", "err"); return; }
  const rows = data || [];
  const { data: oldest } = await scoped(sb.from("runs").select("received_at").order("received_at", { ascending: true }).limit(1));
  if (oldest && oldest.length) {
    const days = Math.max(0, Math.ceil((cfg.retentionDays || 180) - ageSec(oldest[0].received_at) / 86400));
    retention.textContent = `최근 100건을 보입니다. 가장 오래된 기록은 ${days}일 뒤 자동 삭제됩니다 (사용량 숫자는 지워지지 않습니다). 필요하면 지워지기 전에 엑셀로 받아 두세요.`;
  } else retention.textContent = "";
  if (!rows.length) { say(body, "아직 실행 기록이 없습니다. PC 에서 실행이 끝나면 여기에 쌓입니다."); return; }
  keepFocus(body, () => { clear(body); body.appendChild(runTable(rows)); });
}

// ---------------------------------------------------------------- 실행 상세
// 진행 중인 실행은 5초마다 다시 읽는다 (요약의 '지금 실행 중' 과 같은 주기, 10-01). 끝나면(끊김 포함) 멈추고,
// 화면을 옮기면 render() 의 stopTimers() 가 멈춘다. 숨긴 탭은 every() 가 건너뛴다.
async function renderDetail(runId) {
  stopTimers();
  clear(main);
  const back = h("button", { text: "← 실행 기록", onclick: () => go("#/history") });
  const head = h("section", {}, h("div", { class: "toolbar" }, back), h("div", { class: "note", text: "불러오는 중..." }));
  const body = h("div");                            // 확인할 것·단계·로그 — 다시 읽으면 통째로 바꾼다
  main.append(head, body);
  let busy = false, drawn = "";
  every(POLL_LIVE_MS, async () => {
    if (busy) return;                               // 앞 읽기가 안 끝났으면 겹쳐 읽지 않는다 (늦은 옛 결과가 새것을 덮지 않게)
    busy = true;
    try {
      const [{ data: run, error }, { data: steps }, { data: attention }, { data: logs }] = await Promise.all([
        sb.from("runs").select("*, devices(name), accounts(name)").eq("id", runId).single(),
        sb.from("run_steps").select("*").eq("run_id", runId).order("index").order("step_id"),
        sb.from("run_attention").select("*").eq("run_id", runId).order("seq"),
        sb.from("run_logs").select("*").eq("run_id", runId).order("seq"),
      ]);
      if (!head.isConnected) return;                // 기다리는 사이 다른 화면으로 옮겼다
      if (error || !run) { if (!drawn) say(head.children[1], "실행을 읽지 못했습니다 — 잠시 뒤 다시 읽습니다.", "err"); return; }
      if (run.state !== "running") stopTimers();    // 끝난 실행은 더 바뀌지 않는다
      // 바뀐 것이 없으면 다시 그리지 않는다 — 로그를 고르던 중이면 선택이 풀린다 (log·attend 는 last_seq 를 안 올려 수도 본다)
      // 신호 시각은 원값이 아니라 '늦음 + 분' 으로 — 원값이면 30초 심박마다 다시 그려 선택이 풀리고, 끊기면 안 바뀌어 '마지막 신호' 를 못 보인다
      const age = ageSec(run.heartbeat_at || run.received_at);
      const sign = JSON.stringify([run.state, run.last_seq, run.current_activity,
                                   age > HEARTBEAT_STALE_SEC ? Math.floor(age / 60) : -1,
                                   (steps || []).length, (attention || []).length, (logs || []).length]);
      if (sign === drawn) return;
      drawn = sign;
      drawDetail(head.children[1], body, run, steps || [], attention || [], logs || []);
    } finally {
      busy = false;
    }
  });
}

function drawDetail(info, body, run, steps, attention, logs) {
  clear(info);
  clear(body);
  const live = run.state === "running", seen = run.heartbeat_at || run.received_at;
  // ★ append(null) 은 'null' 글자를 붙인다 — 빈 자리는 걸러서 넘긴다
  info.append(...[
    h("div", { class: "big" }, stateCell(run.state, RUN_STATES), run.summary ? h("span", { text: " — " + run.summary }) : null),
    h("div", { class: "note", text: `${run.accounts ? run.accounts.name : ""} · ${run.devices ? run.devices.name : ""} · ${run.trigger || ""} · ${modulesText(run.modules)}` }),
    h("div", { class: "note", text: `시작 ${fmtTime(run.started_at || run.received_at)} · 끝 ${fmtTime(run.finished_at)} · 걸린 시간 ${fmtDur(run.elapsed)}` }),
    live ? h("div", { class: "note", text: "진행 중 — 5초마다 새로 고칩니다" + (run.current_activity ? " · " + run.current_activity : "")
      + (ageSec(seen) > HEARTBEAT_STALE_SEC ? " · 마지막 신호 " + agoText(seen) : "") }) : null,
  ].filter(Boolean));

  if (attention.length) {
    const ul = h("ul");
    for (const a of attention) ul.appendChild(h("li", { text: a.text }));
    body.appendChild(h("section", {}, h("h2", { text: `확인할 것 ${attention.length}건` }), ul));
  }

  const rows = steps.map(s => {
    const counts = s.total_items > 0 ? `${s.done_items || 0} / ${s.total_items}건` +
      ((s.ok_items || s.failed_items) ? ` (성공 ${s.ok_items || 0}, 실패 ${s.failed_items || 0})` : "") : "";
    const content = h("td", {}, h("div", { text: s.detail || s.activity || "" }), s.error ? h("div", { class: "err", text: s.error }) : null);
    if (s.total_items > 0) {
      const pct = Math.min(100, Math.round(100 * (s.done_items || 0) / s.total_items));   // 숫자만 style 로 간다
      content.appendChild(h("div", { class: "bar" }, h("div", { style: `width:${pct}%` })));
    }
    return h("tr", {}, h("td", { class: "num", text: s.index }), h("td", { text: s.name }), h("td", {}, stateCell(s.state, STEP_STATES)),
      content, h("td", { class: "opt", text: counts }),
      h("td", { class: "opt", text: fmtTime(s.finished_at || s.started_at, false) }), h("td", { class: "opt", text: fmtDur(s.elapsed) }));
  });
  body.appendChild(h("section", {}, h("h2", { text: "단계" }), rows.length
    ? table([th("#", "num"), th("단계"), th("상태"), th("내용"), th("건수", "opt"), th("시각", "opt"), th("걸린 시간", "opt")], rows)
    : h("div", { class: "empty", text: "단계 기록이 없습니다." })));

  const logBox = h("div", { class: "log" });
  for (const l of logs) logBox.appendChild(h("div", { class: LOG_CLASSES[l.level] || "", text: `${fmtTime(l.at, false)}  ${l.text}` }));
  body.appendChild(h("section", {}, h("h2", { text: "로그" }), logs.length ? logBox : h("div", { class: "empty", text: "로그가 없습니다." })));
}

// ---------------------------------------------------------------- 엑셀 내려받기
async function fetchAll(build) {
  const out = [];
  for (let from = 0; from < 100000; from += PAGE) {
    const { data, error } = await build().range(from, from + PAGE - 1);
    if (error) throw new Error(error.message);
    out.push(...(data || []));
    if (!data || data.length < PAGE) break;
  }
  return out;
}
async function exportExcel(fromDay, toDay, note, btn) {
  if (!fromDay || !toDay) { note.textContent = "기간을 고르세요."; return; }
  const fromDate = new Date(fromDay + "T00:00:00"), toDate = new Date(toDay + "T00:00:00");   // 로컬 자정
  if (isNaN(fromDate) || isNaN(toDate) || toDate < fromDate) { note.textContent = "기간이 맞지 않습니다."; return; }
  if ((toDate - fromDate) / 86400000 + 1 > EXPORT_MAX_DAYS) { note.textContent = `한 번에 최대 ${EXPORT_MAX_DAYS}일까지 받을 수 있습니다.`; return; }
  const fromIso = fromDate.toISOString(), toIso = new Date(toDate.getTime() + 86400000).toISOString();
  btn.disabled = true; note.textContent = "모으는 중...";
  try {
    // 페이지를 넘길 때 순서가 흔들리지 않게 유일한 열(id / step_id)을 뒤에 붙인다
    const runs = await fetchAll(() => scoped(sb.from("runs").select("*, devices(name), accounts(name)")
      .gte("received_at", fromIso).lt("received_at", toIso).order("received_at").order("id")));
    const ids = runs.map(r => r.id);
    const steps = [], logs = [], att = [];
    for (let i = 0; i < ids.length; i += 100) {
      const chunk = ids.slice(i, i + 100);
      steps.push(...await fetchAll(() => sb.from("run_steps").select("*").in("run_id", chunk).order("run_id").order("index").order("step_id")));
      logs.push(...await fetchAll(() => sb.from("run_logs").select("*").in("run_id", chunk).order("run_id").order("seq")));
      att.push(...await fetchAll(() => sb.from("run_attention").select("*").in("run_id", chunk).order("run_id").order("seq")));
    }
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(runs.map(r => ({
      "실행 id": r.id, "업체": r.accounts ? r.accounts.name : "", "PC": r.devices ? r.devices.name : "",
      "시작": fmtTime(r.started_at || r.received_at), "끝": fmtTime(r.finished_at), "어떻게": r.trigger || "",
      "기능": modulesText(r.modules), "결과": RUN_STATES[r.state] || r.state, "요약": r.summary || "",
      "걸린 시간(초)": r.elapsed || "", "확인할 것": r.attention || 0 }))), "실행");
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(steps.map(s => ({
      "실행 id": s.run_id, "#": s.index, "단계": s.name, "상태": STEP_STATES[s.state] || s.state, "내용": s.detail || "",
      "전체": s.total_items || "", "끝낸": s.done_items || "", "성공": s.ok_items || "", "실패": s.failed_items || "",
      "시작": fmtTime(s.started_at), "끝": fmtTime(s.finished_at), "걸린 시간(초)": s.elapsed || "", "실패 문구": s.error || "" }))), "단계");
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(att.map(a => ({ "실행 id": a.run_id, "내용": a.text }))), "확인할 것");
    XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(logs.map(l => ({
      "실행 id": l.run_id, "시각": fmtTime(l.at), "수준": l.level || "", "단계": l.step_id || "", "내용": l.text }))), "로그");
    XLSX.writeFile(wb, `RPA_실행기록_${fromDay}_${toDay}.xlsx`);
    note.textContent = `실행 ${runs.length}건을 받았습니다.`;
  } catch (e) {
    note.textContent = "내려받지 못했습니다: " + (e && e.message ? e.message : String(e));
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- 사용법 질문 — 옆 패널 (09-28 개편)
// 질문은 서버에 쌓이고 LLM 전용 PC 가 가져가 답을 흘려 쓴다 (server/schema.sql 8절, llm/worker.py).
// 채팅 관례: 입력칸은 패널 아래에 늘 보이고, 대화만 스크롤한다(오래된 것이 위, 새 것이 아래).
// 보내면 새 질문이 대화창 위쪽에 오게 두고 답이 그 아래로 자란다 — 끝까지 따라 내리지 않는다 (NN/g).
// 패널은 화면을 옮겨도 남는다 — 그래서 타이머는 화면 타이머(timers)와 따로 둔다 (helpTimers, 로그아웃 때 멈춘다).
const ASK_POLL_MS = 300, ASK_ONLINE_MS = 15000, ASK_WAIT_WARN_MS = 20000, ASK_GIVE_UP_MS = 150000, ASK_MAX = 500, ASK_SHOW = 10;
const ASK_ERRORS = { PT503: "지금은 답변 도우미가 쉬고 있습니다. 잠시 뒤 다시 물어 주세요.",
                     PT409: "앞 질문의 답을 만드는 중입니다. 끝난 뒤 물어 주세요.",
                     PT429: "최근 24시간 동안 물을 수 있는 수(50개)를 다 썼습니다. 먼저 물은 질문부터 24시간이 지나면 다시 물을 수 있습니다.",
                     PT400: "질문은 1~500자로 적어 주세요.", PT403: "질문할 수 있는 계정이 아닙니다." };
const QA_STATES = { queued: "기다리는 중...", answering: "답을 만드는 중...", done: "",
                    failed: "답을 만들지 못했습니다.",
                    expired: "답을 받지 못했습니다." };
const ASK_EXAMPLES = ["예약을 평일 오전 9시에 걸려면?", "인증 문자가 안 와서 멈췄어요", "택배사 목록에 없는 이름이라고 나와요",
                      "웹에서 도는 RPA 를 멈추려면?"];
let helpTimers = [];

const help = (() => {
  const panel = document.getElementById("helpPanel");
  const fab = document.getElementById("helpBtn");
  const status = h("span", { class: "tag", text: "확인 중" });
  const log = h("div", { class: "help-log", role: "log", "aria-label": "사용법 질문 기록" });
  const done = h("div", { class: "sr", role: "status" });       // 화면낭독기에는 '답 완료'만 읽힌다 (자라는 글자는 읽지 않는다)
  const box = h("textarea", { class: "ask", rows: "2", maxlength: String(ASK_MAX), "aria-label": "사용법 질문",
                              placeholder: "RPA 사용법을 물어보세요" });
  const note = h("span", { class: "note", role: "status" });
  const btn = h("button", { class: "primary", text: "보내기" });
  let following = null, onlineTimer = null, loaded = false, isOpen = false, epoch = 0;
  // 답을 기다리는 중에는 새로 보내지 않는다 — 조용히 무시하지 말고 알린다. 쓰던 글은 덮지 않는다
  const ask = text => {
    if (btn.disabled) { note.textContent = ASK_ERRORS.PT409; return; }
    box.value = text;
    submit();
  };

  const showOnline = async () => {
    const { data, error } = await sb.rpc("llm_online");
    status.textContent = error ? "확인 못 함" : data ? "답변 가능" : "답변 도우미 쉬는 중";
    status.className = error || !data ? "tag bad" : "tag ok";
  };
  const empty = () => h("div", { class: "help-empty" },
    h("div", { text: "RPA 사용법을 물어보세요. 사용 설명서를 바탕으로 곧바로 답합니다." }),
    h("div", { class: "note", text: "틀릴 수 있으니 저장·삭제처럼 중요한 것은 담당자에게 확인하세요. 계정·비밀번호는 답하지 않습니다. " +
      "한 번에 하나씩, 24시간에 50개까지 물을 수 있습니다." }),
    h("div", { class: "chips" }, ...ASK_EXAMPLES.map(t => h("button", { text: t, onclick: () => ask(t) }))));
  const paint = (parts, q, since) => {
    parts.a.textContent = q.answer || "";
    let s = Object.prototype.hasOwnProperty.call(QA_STATES, q.status) ? QA_STATES[q.status] : "";
    if (q.status === "queued" && since && Date.now() - since > ASK_WAIT_WARN_MS) {
      s = "아직 차례를 기다리는 중입니다 — 1~2분 안에 시작하지 않으면 닫힙니다.";
    }
    const bad = q.status === "failed" || q.status === "expired";
    clear(parts.s);
    parts.s.appendChild(h("span", { class: bad ? "err" : "note", text: s }));
    if (bad) parts.s.append(" ", h("button", { class: "link", text: "다시 묻기", onclick: () => ask(parts.question) }));
    parts.a.setAttribute("aria-busy", String(q.status === "queued" || q.status === "answering"));
  };
  const entry = q => {
    const parts = { question: q.question, a: h("div", { class: "a" }), s: h("div", { class: "s" }) };
    parts.el = h("div", { class: "qa" }, h("div", { class: "q", text: q.question }), parts.a, parts.s);
    paint(parts, q);
    return parts;
  };
  const follow = (id, parts) => {
    if (following) clearInterval(following);
    const since = Date.now();
    let busy = false, misses = 0;
    btn.disabled = true;
    const finish = text => {
      clearInterval(timer);
      following = null;
      btn.disabled = false;
      if (text) { clear(parts.s); parts.s.appendChild(h("span", { class: "note", text })); }
      done.textContent = text ? "답을 받지 못함" : "답 완료";
    };
    const timer = setInterval(async () => {
      // 상한은 읽기 실패에도 건다 — 끊긴 채 끝없이 읽지 않게
      if (Date.now() - since > ASK_GIVE_UP_MS) {
        loaded = false;                               // 안내대로 닫았다 열면 다시 읽는다
        finish("더 기다리지 않습니다 — 패널을 닫았다 열면 다시 봅니다.");
        return;
      }
      if (busy) return;
      busy = true;
      const mine = epoch;
      const { data: q, error } = await sb.from("questions").select("answer, status").eq("id", id).single();
      busy = false;
      if (mine !== epoch) return;                     // 그 사이 로그아웃했다
      if (error || !q) {
        misses += 1;
        if (misses >= 10) { clear(parts.s); parts.s.appendChild(h("span", { class: "err", text: "연결이 끊겨 답을 읽지 못하고 있습니다..." })); }
        return;                                       // 다음 차례에 다시 읽는다
      }
      misses = 0;
      paint(parts, q, since);
      if (q.status !== "queued" && q.status !== "answering") finish();
    }, ASK_POLL_MS);
    following = timer;
    helpTimers.push(timer);                           // 로그아웃하면 reset 이 멈춘다 (패널은 화면을 옮겨도 남는다)
  };
  const loadMine = async () => {
    const mine = epoch;
    const { data, error } = await sb.from("questions").select("id, question, answer, status")
      .eq("user_id", me.id).order("id", { ascending: false }).limit(ASK_SHOW);
    if (mine !== epoch) return;                       // 그 사이 로그아웃했다
    clear(log);
    if (error) { log.appendChild(h("div", { class: "err", text: "지난 질문을 읽지 못했습니다 — 패널을 닫았다 열면 다시 읽습니다." })); return; }
    loaded = true;
    if (!data || !data.length) { log.appendChild(empty()); return; }
    for (const q of data.reverse()) {
      const parts = entry(q);
      log.appendChild(parts.el);
      if (q.status === "queued" || q.status === "answering") follow(q.id, parts);
    }
    log.scrollTop = log.scrollHeight;                 // 열면 가장 최근 대화
  };
  const submit = async () => {
    const text = box.value.trim();
    if (!text) { note.textContent = "질문을 적어 주세요."; return; }
    if (btn.disabled) { note.textContent = ASK_ERRORS.PT409; return; }
    btn.disabled = true;
    note.textContent = "보내는 중...";
    const mine = epoch;
    const { data: id, error } = await sb.rpc("ask_question", { question: text });
    if (mine !== epoch) return;                       // 그 사이 로그아웃했다
    if (error) {
      note.textContent = ASK_ERRORS[error.code] || "보내지 못했습니다: " + error.message;
      btn.disabled = false;
      showOnline();
      return;
    }
    note.textContent = "";
    box.value = "";
    const first = log.querySelector(".help-empty");
    if (first) log.removeChild(first);
    const parts = entry({ question: text, answer: "", status: "queued" });
    log.appendChild(parts.el);
    while (log.children.length > ASK_SHOW) log.removeChild(log.firstChild);
    log.scrollTop = parts.el.offsetTop - 8;           // 새 질문을 위쪽에 두고 답이 아래로 자란다
    follow(id, parts);
    box.focus();
  };
  btn.addEventListener("click", submit);
  // Enter 로 보내고 Shift+Enter 는 줄바꿈. 한글 조합 중(isComposing)에는 보내지 않는다 — 글자가 둘로 나뉘어 간다
  box.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });

  const close = () => {
    if (!isOpen) return;
    isOpen = false;
    panel.classList.add("hidden");
    document.body.classList.remove("help-open");
    fab.classList.toggle("hidden", !me);
    fab.setAttribute("aria-expanded", "false");
    if (onlineTimer) { clearInterval(onlineTimer); onlineTimer = null; }
    fab.focus();
  };
  const open = () => {
    if (isOpen) return;
    isOpen = true;
    panel.classList.remove("hidden");
    document.body.classList.add("help-open");           // 넓은 화면에서는 본문이 옆으로 비켜 준다 (CSS)
    fab.classList.add("hidden");
    fab.setAttribute("aria-expanded", "true");
    showOnline();
    onlineTimer = setInterval(showOnline, ASK_ONLINE_MS);
    helpTimers.push(onlineTimer);
    if (!loaded) loadMine(); else log.scrollTop = log.scrollHeight;
    box.focus();
  };
  const closeBtn = h("button", { "aria-label": "닫기", text: "×", onclick: close });
  panel.append(h("div", { class: "help-head" }, h("h2", { text: "사용법 질문" }), status, closeBtn), log, done,
    h("div", { class: "help-input" }, box,
      h("div", { class: "row" }, h("span", { class: "note", text: "Enter 보내기 · Shift+Enter 줄바꿈" }), btn), note));
  fab.addEventListener("click", open);
  panel.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  return {
    open, close,
    reset() {                                         // 로그아웃 — 남의 대화가 다음 로그인에 비치지 않게 비운다
      for (const t of helpTimers) clearInterval(t);
      helpTimers = [];
      following = onlineTimer = null;
      loaded = false;
      epoch += 1;
      clear(log);
      box.value = note.textContent = done.textContent = "";
      status.textContent = "확인 중"; status.className = "tag";
      btn.disabled = false;
    },
  };
})();

// ---------------------------------------------------------------- 시작
(function start() {
  if (!cfg.url || !cfg.anonKey || !window.supabase) {
    say(main, "config.js 에 서버 주소와 공개 키를 넣어야 합니다.", "err");
    return;
  }
  // 세션은 sessionStorage — 탭을 닫으면 끝난다 (무료 플랜엔 세션 시간 제한이 없다, SERVER_PLAN D-10)
  sb = window.supabase.createClient(cfg.url, cfg.anonKey, { auth: { storage: window.sessionStorage, persistSession: true, autoRefreshToken: true } });
  sb.auth.onAuthStateChange(event => {
    if (event === "SIGNED_OUT") {
      me = null; isAdmin = false; accountId = ""; myMembers = []; accountNames = {}; accountSuspended = {};
      history.replaceState(null, "", "#/home");
      showHeader(false); renderLogin("로그인이 끝났습니다. 다시 로그인하세요.");
    }
  });
  document.getElementById("logoutBtn").addEventListener("click", async () => {
    stopTimers(); await sb.auth.signOut();
  });
  window.addEventListener("hashchange", render);
  boot();
})();
