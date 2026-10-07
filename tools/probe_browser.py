r"""[조사 도구 - 읽기 전용] 사이트 화면 도우미 확인 (10-07, `collect/browser.py`).

대상 프로그램(ERPia)·실사이트를 건드리지 않는다. 127.0.0.1 무작위 포트에 가짜 사이트를 띄우고 진짜 Edge 를 창 없이 띄워
가로막는 공지·마우스 올림 메뉴·팝업이 주는 파일·알림창·새로 생긴 줄·업로드 폼을 본다.

    .venv\Scripts\python.exe -m tools.probe_browser
"""
from __future__ import annotations

import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue

from collect import browser  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
HOSTS: list[str] = []

_HEAD = '<!doctype html><meta charset="utf-8"><style>body{font:14px sans-serif}</style>'
PAGES = {
    "/menu": _HEAD + r"""
<p>눌린 횟수 <span id="count">0</span> / 고른 항목 <span id="picked">-</span></p>
<button id="btn" onclick="count.textContent = +count.textContent + 1">버튼</button>
<div id="menu" style="display:inline-block;padding:4px">메뉴
  <div id="sub"><button id="item" onclick="picked.textContent='항목'">항목</button></div>
</div>
<style>#sub{display:none} #menu:hover #sub{display:block}</style>
<div id="notice" style="display:none;position:fixed;left:0;top:0;right:0;bottom:0;background:#eee;z-index:9">
  공지 <button id="notice-close" onclick="notice.style.display='none'">오늘 하루 보지 않기</button>
  <button id="decoy" onclick="window.decoyClicked = 1">닫기</button>
</div>
<script>if (location.search.includes('notice')) notice.style.display = 'block'</script>""",
    "/dl": _HEAD + r"""
<a id="same" href="/f/ok.xlsx">같은 창</a>
<button id="popup" onclick="window.open('/pop', 'pop', 'width=360,height=220')">팝업</button>
<a id="nothing" href="#">없음</a> <a id="bad" href="/f/bad.xlsx">HTML</a>""",
    # 실제 사이트 꼴 — 스크립트로 연 팝업이 파일 주소로 옮겨 가 내려 주고, 조금 뒤 스스로 닫힌다
    "/pop": _HEAD + r"""
<p>파일을 만드는 중</p>
<script>setTimeout(() => location.href = '/f/ok.xlsx', 300); setTimeout(() => window.close(), 1500);</script>""",
    "/dlg": _HEAD + r"""
<p id="out">-</p>
<button id="confirm" onclick="out.textContent = confirm('자료를 보낼까요?') ? '확인됨' : '취소됨'">보내기</button>
<button id="alert" onclick="alert('처리 3건 완료'); out.textContent = '알림 뒤'">알림</button>
<button id="quiet" onclick="out.textContent = '조용'">조용</button>""",
    "/rows": _HEAD + r"""
<table id="list"><tbody id="lines"><tr><td>A-1</td></tr><tr><td>A-2</td></tr></tbody></table>
<p id="empty" style="display:none">조회된 자료가 없습니다</p>
<button id="add" onclick="later(['A-1', 'A-2', 'B-1', 'B-2'])">새 줄</button>
<button id="redraw" onclick="later(['A-1', 'A-2', 'B-3'])">다시 그리기</button>
<button id="none" onclick="setTimeout(() => document.getElementById('empty').style.display = 'block', 300)">없음</button>
<button id="still" onclick="0">아무 일 없음</button>
<script>
// 조회처럼 조금 뒤 목록을 통째로 다시 그린다 — 옛 줄도 새로 만들어진다
function later(texts) {
  setTimeout(() => document.getElementById('lines').replaceChildren(...texts.map(t => {
    const r = document.createElement('tr'); r.insertCell().textContent = t; return r; })), 500);
}
</script>""",
    "/up": _HEAD + r"""
<input type="file" id="file"> <span id="size">-</span>
<script>file.onchange = () => size.textContent = file.files[0].size</script>""",
}
FILES = {"/f/ok.xlsx": b"PK\x03\x04" + b"\0" * 60, "/f/bad.xlsx": b"<html>login</html>"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        HOSTS.append(self.headers.get("Host", ""))
        path = self.path.split("?")[0]
        if path in FILES:
            body = FILES[path]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{path.rsplit("/", 1)[1]}"')
        elif path in PAGES:
            body = PAGES[path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b""
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):     # 콘솔에 요청 줄을 찍지 않는다
        return


@contextmanager
def fake_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


def _raises(func, kind) -> str:
    try:
        func()
    except kind as exc:
        return str(exc) or kind.__name__
    return ""


def check_notice_menu(page, base) -> list[bool]:
    log.info("▶ 가로막는 공지 · 마우스 올림 메뉴")
    out = []
    page.goto(base + "/menu")
    browser.click_through(page, page.locator("#btn"), label="버튼")
    out.append(check("공지가 없으면 바로 누른다", page.inner_text("#count") == "1"))
    page.goto(base + "/menu?notice=1")
    browser.click_through(page, page.locator("#btn"), notice_close=("#notice-close",), label="버튼")
    out.append(check("★ 공지가 가리면 사이트가 준 닫기 selector 로 닫고 누른다", page.inner_text("#count") == "1"))
    page.goto(base + "/menu?notice=1")
    error = _raises(lambda: browser.click_through(page, page.locator("#btn"), label="버튼"), browser.SiteScreenError)
    count, decoy = page.inner_text("#count"), page.evaluate("window.decoyClicked || 0")
    out.append(check("★ 닫기 selector 를 안 주면 멈춘다 — 글자('닫기')로 짐작하거나 강제로 누르지 않는다",
                     "버튼" in error and count == "0" and not decoy, f"{error[:40]} / 횟수 {count!r} / 닫기 {decoy}"))
    page.goto(base + "/menu")
    error = _raises(lambda: browser.hover_click(page, "#menu", "#item", label="메뉴"), browser.SiteScreenError)
    picked = page.inner_text("#picked")
    out.append(check("마우스를 올려 메뉴를 열고 항목을 누른다", not error and picked == "항목",
                     f"{error[:60]} / 고른 것 {picked!r}"))
    error = _raises(lambda: browser.hover_click(page, "#menu", "#no-such", label="메뉴"), browser.SiteScreenError)
    out.append(check("항목 selector 가 틀리면 라벨을 든 오류로 멈춘다", "메뉴" in error, error[:60]))
    return out


def check_download(page, base, folder: Path) -> list[bool]:
    log.info("▶ 파일 받기 — 같은 창 · 팝업이 주고 닫힘 · 안 옴 · HTML 을 엑셀 이름으로")
    out = []
    page.goto(base + "/dl")
    got = browser.receive_download(page, lambda: page.click("#same"), timeout_s=15)
    saved = browser.save_download(got, folder / "same.xlsx")
    out.append(check("같은 창에서 받은 파일을 저장하고 엑셀로 확인한다", saved.read_bytes().startswith(b"PK")))
    got = browser.receive_download(page, lambda: page.click("#popup"), timeout_s=15)
    browser.save_download(got, folder / "popup.xlsx")
    out.append(check("★ 팝업이 파일을 주고 스스로 닫혀도 받는다", (folder / "popup.xlsx").is_file()))
    pages = browser.site_pages(page.context)
    out.append(check("받은 뒤 원래 창이 남고, 브라우저 자체 페이지(edge://)는 사이트 창에 없다",
                     page in pages and not any(p.url.startswith("edge://") for p in pages),
                     str([p.url for p in page.context.pages])))
    error = _raises(lambda: browser.receive_download(page, lambda: page.click("#nothing"), timeout_s=1.5),
                    browser.DownloadTimeout)
    out.append(check("파일이 안 오면 상한에서 DownloadTimeout", bool(error), error[:60]))
    got = browser.receive_download(page, lambda: page.click("#bad"), timeout_s=15)
    error = _raises(lambda: browser.save_download(got, folder / "bad.xlsx"), browser.SiteFileError)
    out.append(check("★ HTML 을 .xlsx 이름으로 주면(로그인 풀림) 받지 않는다", bool(error), error[:60]))
    return out


def check_dialogs(page, base) -> list[bool]:
    log.info("▶ 알림창 — 기대 문구만 수락, 나머지는 닫고 오류")
    out = []
    page.goto(base + "/dlg")
    guard = browser.DialogGuard(page.context)
    with guard.expecting("자료를 보낼까요?"):
        page.click("#confirm")
    out.append(check("기대한 confirm 은 수락한다", page.inner_text("#out") == "확인됨"))
    try:
        with guard.expecting("다른 문구"):
            page.click("#confirm")
        error = ""
    except browser.UnexpectedDialog as exc:
        error = str(exc)
    out.append(check("★ 기대 밖 confirm 은 취소하고 UnexpectedDialog", page.inner_text("#out") == "취소됨"
                     and "자료를 보낼까요" in error, error[:60]))
    page.click("#confirm")
    error = _raises(guard.raise_if_unexpected, browser.UnexpectedDialog)
    out.append(check("expecting 밖에서 뜬 알림창은 닫고 남겨 둔다 (다음 확인에서 오류)",
                     page.inner_text("#out") == "취소됨" and bool(error)))
    with guard.expecting(re.compile(r"처리 \d+건 완료")):
        page.click("#alert")
    out.append(check("정규식은 전체일치로 수락한다", page.inner_text("#out") == "알림 뒤"))
    try:
        with guard.expecting("자료를 보낼까요?", required=True):
            page.click("#quiet")
        error = ""
    except browser.SiteScreenError as exc:
        error = str(exc)
    out.append(check("꼭 떠야 하는 알림창이 안 뜨면 멈춘다", "뜨지 않았다" in error, error[:60]))
    return out


def check_rows(page, base) -> list[bool]:
    log.info("▶ 새로 생긴 줄만")
    out = []
    for button, want, name in (("#add", ["B-1", "B-2"], "새 줄 둘만 고른다"),
                               ("#redraw", ["B-3"], "★ 목록을 통째로 다시 그려도 새 줄만")):
        page.goto(base + "/rows")
        watch = browser.RowWatch(page, "#list tr", root="#list")
        watch.mark()
        page.click(button)
        got = [text for _, text in watch.new_rows()]
        out.append(check(name, got == want, str(got)))
    page.goto(base + "/rows")
    watch = browser.RowWatch(page, "#list tr", root="#list")
    watch.mark()
    page.click("#none")
    out.append(check("0건 표시가 보이면 빈 목록", watch.new_rows(empty="#empty") == []))
    page.goto(base + "/rows")
    watch = browser.RowWatch(page, "#list tr", root="#list")
    watch.mark()
    page.click("#still")
    error = _raises(lambda: watch.new_rows(timeout_s=1.5), browser.SiteScreenError)
    out.append(check("아무것도 안 생기면 상한에서 멈춘다", bool(error), error[:60]))
    out.append(check("같은 글자 줄은 개수로 센다", browser.fresh_indexes(["a", "a"], ["a", "a", "a"]) == [2]))
    return out


def check_upload(page, base, folder: Path) -> list[bool]:
    log.info("▶ 업로드 폼 (도우미 없이 Playwright 그대로 — 나중을 위해)")
    sample = folder / "up.xlsx"
    sample.write_bytes(b"PK\x03\x04" + b"\0" * 20)
    page.goto(base + "/up")
    page.set_input_files("#file", str(sample))
    return [check("파일 칸에 넣으면 페이지가 받는다", page.inner_text("#size") == "24")]


def check_static() -> list[bool]:
    log.info("▶ 규칙 — 새 코드에 고정 대기·강제 클릭·innerHTML 이 없다")
    source = (Path(browser.__file__)).read_text(encoding="utf-8")
    helpers = source.split("사이트 화면 도우미 (10-07)", 2)[-1]
    banned = [word for word in ("wait_for_timeout", "time.sleep", "force=True", "innerHTML", ".click()")
              if word in helpers]
    return [check("도우미 코드에 금지 꼴이 없다", not banned, str(banned)),
            check("가짜 사이트 밖으로 나간 요청이 없다", HOSTS and all(h.startswith("127.0.0.1:") for h in HOSTS),
                  str(sorted(set(HOSTS)))[:80])]


def main() -> int:
    setup_logging()
    keep = (SETTINGS.browser_channel, SETTINGS.browser_headless, browser.CLICK_MS, browser.MENU_OPEN_MS)
    SETTINGS.browser_channel, SETTINGS.browser_headless = "msedge", True
    browser.CLICK_MS = browser.MENU_OPEN_MS = 800           # 실패 경로를 빨리 보려고
    results: list[bool] = []
    folder = Path(tempfile.mkdtemp(prefix="probe_browser_"))
    try:
        with fake_site() as base, browser.browser_page() as page:
            results += check_notice_menu(page, base)
            results += check_download(page, base, folder)
            results += check_dialogs(page, base)
            results += check_rows(page, base)
            results += check_upload(page, base, folder)
        results += check_static()
    finally:
        SETTINGS.browser_channel, SETTINGS.browser_headless, browser.CLICK_MS, browser.MENU_OPEN_MS = keep
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
