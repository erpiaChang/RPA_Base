r"""브라우저 — 사이트 연결이 같이 쓰는 공용 부품 (10-02, 시험 사이트 파일에서 뗐다).

Edge 를 **시크릿(InPrivate)** 으로 띄운다 (사용자 지시 2026-09-08) — 세션이 남지 않아 실행마다 로그인한다.
브라우저 종류는 설정 `browser_channel` 이다. **기본값이 없다** (10-02) — 비면 띄우지 않고 멈춘다.

사이트 화면 도우미 (10-07, `docs/SITES.md` 4절) — 가로막는 공지·마우스 올림 메뉴·팝업이 주는 파일·알림창·새로 생긴 줄.
selector·기대 문구는 **인자로만** 받는다 (사이트 파일 상수). 글자로 짐작해 닫거나 JS 로 강제 클릭하지 않는다.
★ Playwright 는 함수 안에서만 부른다 — 메일 기능을 뺀 빌드에는 없다 (`build_run.spec`).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import contextmanager

from config.settings import SETTINGS
from utils.logger import get_logger
from utils.wait import WaitTimeout, wait_for

log = get_logger(__name__)

CHANNELS = ("msedge", "chrome")       # 설정 browser_channel 의 선택지 (gui/build_app.py 칸도 이것)
BROWSER_NAMES = {"msedge": "Microsoft Edge", "chrome": "Google Chrome"}   # 사용자에게 보일 이름


class BrowserError(RuntimeError):
    """브라우저 설정이 비었거나 틀렸다, 또는 화면을 읽지 못했다."""


@contextmanager
def browser_page():
    """시크릿 브라우저를 띄우고 페이지를 넘긴다. 끝나면 반드시 닫는다."""
    channel = SETTINGS.browser_channel
    if channel not in CHANNELS:
        raise BrowserError(f"브라우저 종류(browser_channel)가 비었거나 틀렸다: {channel!r} — {' / '.join(CHANNELS)}")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from collect import pw_driver

    pw_driver.prepare()            # 빌드본: exe 리소스의 node.exe 를 꺼내 쓴다 (09-29)
    with sync_playwright() as pw:
        args = ["--inprivate"] if channel == "msedge" else []
        try:
            browser = pw.chromium.launch(
                channel=channel,
                headless=bool(SETTINGS.browser_headless),     # 비면 창을 띄운다 — 2차 인증이 막히면 사람이 봐야 한다
                args=args,
            )
        except PlaywrightError as exc:
            # Playwright 문구 "Chromium distribution 'msedge' is not found at ..." — 이때만 사람 말로 (10-07)
            if f"'{channel}' is not found" in str(exc):
                raise BrowserError(f"브라우저 실행 파일이 없다: {channel}") from exc
            raise
        context = browser.new_context(accept_downloads=True)
        context.set_default_timeout(int(SETTINGS.timeouts.page_load * 1000))
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
            log.info("브라우저를 닫았다")


def page_text(page, limit: int = 300) -> str:
    """실패 원인을 로그에 남기기 위한 화면 텍스트."""
    try:
        return (page.inner_text("body") or "").replace("\n", " ").strip()[:limit]
    except Exception as exc:
        return f"(화면 텍스트를 읽지 못했다: {exc})"


def eval_json(page, script: str, arg=None):
    """JS 결과를 JSON 문자열로 받아 파싱한다. 페이지 안에서 `JSON.stringify` 로 만들어 가져온다 —
    객체를 그대로 돌려주면 None 이 오는 사이트가 있다."""
    raw = page.evaluate(script) if arg is None else page.evaluate(script, arg)
    if not isinstance(raw, str):
        raise BrowserError(f"화면을 읽지 못했다 (evaluate 결과가 {type(raw).__name__}).")
    return json.loads(raw)


# ---------------------------------------------------------------- 사이트 화면 도우미 (10-07)
CLOSE_ATTEMPTS = 3              # 가로막는 공지를 닫고 다시 누르는 상한
CLICK_MS = 4000                 # 누르기 한 번의 상한 (Playwright 가 그 안에서 다시 해 본다)
MENU_OPEN_MS = 3000             # 마우스를 올린 뒤 항목이 보이기까지
QUIET_MS = 400                  # 목록이 이만큼 안 바뀌면 다 그려진 것으로 본다
QUIET_MAX_MS = 5000             # 그래도 계속 바뀌면 여기서 멈춘다
ROWS_TIMEOUT_S = 15.0           # 새 줄이 생기기까지
POLL_S = 0.2
DIALOG_CUT = 80                 # 알림창 글은 이만큼만 남긴다 (주문자 정보가 들 수 있다)
_HELP = "다시 실행하세요. 같은 문제가 계속되면 [결과 보기]로 연 리포트 파일을 담당자에게 보내 주세요."


class SiteScreenError(BrowserError):
    """사이트 화면이 기대와 다르다. 메시지는 '화면이 바뀜: <라벨> ...'."""
    user_message = "사이트 화면이 예상과 달라 멈췄습니다.\n" + _HELP


class UnexpectedDialog(SiteScreenError):
    user_message = ("사이트가 예상하지 못한 안내창을 띄워 멈췄습니다. 안내창은 닫았습니다.\n"
                    "사이트에서 상태를 확인한 뒤 다시 실행하세요.")


class DownloadTimeout(SiteScreenError):
    user_message = "사이트에서 파일이 내려오지 않아 멈췄습니다.\n" + _HELP


class SiteFileError(SiteScreenError):
    user_message = "사이트에서 받은 파일이 엑셀이 아닙니다 (로그인이 풀렸을 수 있습니다).\n" + _HELP


class DialogGuard:
    """알림창(alert·confirm·prompt). `with guard.expecting(문구…)` 안에서 사이트가 준 문구와 **완전일치**(공백 정리)·
    정규식 **전체일치**만 수락하고, 나머지는 닫는다(confirm 은 취소) — 닫은 것은 `UnexpectedDialog` 로 올린다.
    context 하나에 하나. 새 창(팝업)에도 건다."""

    def __init__(self, context):
        self._allowed: list = []
        self._pages: list = []
        self.accepted: list[tuple[str, str]] = []
        self.unexpected: list[tuple[str, str]] = []
        for page in context.pages:
            self._watch(page)
        context.on("page", self._watch)

    def _watch(self, page) -> None:
        if page not in self._pages:
            self._pages.append(page)
            page.on("dialog", self._on_dialog)

    def _ok(self, text: str) -> bool:
        return any(want.fullmatch(text) if isinstance(want, re.Pattern) else " ".join(want.split()) == text
                   for want in self._allowed)

    def _on_dialog(self, dialog) -> None:
        from playwright.sync_api import Error as PlaywrightError

        text = " ".join(dialog.message.split())
        ok = self._ok(text)
        (self.accepted if ok else self.unexpected).append((dialog.type, text[:DIALOG_CUT]))
        log.info("알림창(%s) %s: %s", dialog.type, "수락" if ok else "닫음", text[:DIALOG_CUT])
        try:
            if ok:
                dialog.accept()
            else:
                dialog.dismiss()
        except PlaywrightError as exc:
            log.warning("알림창을 처리하지 못했다(사이트가 먼저 닫음): %s", exc)

    def raise_if_unexpected(self) -> None:
        if self.unexpected:
            kind, text = self.unexpected[0]
            self.unexpected.clear()
            raise UnexpectedDialog(f"예상 밖 알림창({kind}): {text}")

    @contextmanager
    def expecting(self, *phrases, required: bool = False):
        start = len(self.accepted)
        self._allowed = list(phrases)
        try:
            yield self
        finally:
            self._allowed = []
        self.raise_if_unexpected()
        if required and len(self.accepted) == start:
            raise SiteScreenError("화면이 바뀜: 기대한 알림창이 뜨지 않았다")


def _visible(page, selector: str):
    found = page.locator(selector)
    for index in range(min(found.count(), 5)):
        if found.nth(index).is_visible():
            return found.nth(index)
    return None


def _close_notices(page, notice_close) -> int:
    """사이트가 준 닫기 selector 중 보이는 것만 누른다. 글자로 짐작하지 않는다."""
    from playwright.sync_api import Error as PlaywrightError

    closed = 0
    for selector in notice_close:
        target = _visible(page, selector)
        if target is None:
            continue
        try:
            target.click(timeout=CLICK_MS)
        except PlaywrightError as exc:
            log.warning("공지를 닫지 못했다: %s", str(exc).splitlines()[0])
            continue
        closed += 1
        log.info("가린 공지를 닫았다: %s", selector)
    return closed


def click_through(page, locator, *, notice_close=(), label: str) -> None:
    """누른다. 시간 안에 못 누르면(공지가 가림 등) `notice_close` 로 닫고 다시 — 상한 `CLOSE_ATTEMPTS`. 아니면 `SiteScreenError`."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for attempt in range(CLOSE_ATTEMPTS + 1):
        try:
            locator.click(timeout=CLICK_MS)
            return
        except PlaywrightTimeout as exc:
            reason = str(exc).splitlines()[0]
        if attempt == CLOSE_ATTEMPTS or not _close_notices(page, notice_close):
            raise SiteScreenError(f"화면이 바뀜: {label} 을(를) 누르지 못했다 ({reason})")


def hover_click(page, menu: str, item: str, *, notice_close=(), label: str) -> None:
    """`menu` 에 마우스를 올려 `item` 이 보이면 누른다. 둘 다 사이트 파일 상수 — 둘 이상 맞으면 오류(strict)."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for attempt in range(CLOSE_ATTEMPTS + 1):
        try:
            page.locator(menu).hover(timeout=CLICK_MS)
            page.locator(item).wait_for(state="visible", timeout=MENU_OPEN_MS)
            break
        except PlaywrightTimeout as exc:
            if attempt == CLOSE_ATTEMPTS or not _close_notices(page, notice_close):
                raise SiteScreenError(
                    f"화면이 바뀜: {label} 메뉴가 열리지 않았다 ({str(exc).splitlines()[0]})") from exc
        except PlaywrightError as exc:
            raise SiteScreenError(f"화면이 바뀜: {label} 메뉴를 찾지 못했다 ({str(exc).splitlines()[0]})") from exc
    click_through(page, page.locator(item), notice_close=notice_close, label=label)


def site_pages(context) -> list:
    """사이트 창만 — 브라우저 자체 페이지(Edge 가 받은 뒤 여는 edge://downloads-hub 등)는 뺀다."""
    return [page for page in context.pages
            if not page.is_closed() and not page.url.startswith(("edge://", "chrome://"))]


def _pump(context) -> None:
    """Playwright 동기 API 는 호출 중에만 이벤트(새 창·다운로드·알림창)를 받는다 — 대기 중에 싼 호출로 받게 한다.
    ★ 페이지가 아니라 브라우저에 묻는다 (`context.cookies()`) — 다운로드로 바뀐 이동 중인 페이지에 `evaluate` 를 하면
      상한 없이 멈췄다 (10-07 가짜 사이트 시험)."""
    context.cookies()


def receive_download(page, act, *, guard: DialogGuard | None = None, timeout_s: float | None = None,
                     label: str = "파일 받기"):
    """`act()` 를 하고 첫 다운로드를 돌려준다 — 누른 창이든 그것이 연 팝업이든. 안 오면 `DownloadTimeout`.
    `guard` 를 주면 예상 밖 알림창이 파일보다 먼저 오류가 된다."""
    context = page.context
    got: list = []
    watched: list = []

    def take(download) -> None:         # 함수로 둔다 — Playwright 는 처리 함수에 속성을 붙여 list.append 를 못 받는다
        got.append(download)

    def watch(pg) -> None:
        if pg not in watched:
            watched.append(pg)
            pg.on("download", take)

    for pg in context.pages:
        watch(pg)
    context.on("page", watch)
    timeout = float(SETTINGS.timeouts.download) if timeout_s is None else timeout_s
    try:
        act()

        def ready() -> bool:
            _pump(context)
            return bool(got) or bool(guard and guard.unexpected)

        try:
            wait_for(ready, label, timeout=timeout, interval=POLL_S)
        except WaitTimeout as exc:
            raise DownloadTimeout(f"{label}: {timeout:.0f}초 안에 파일이 오지 않았다") from exc
        if guard is not None:
            guard.raise_if_unexpected()
        return got[0]
    finally:
        # ★ Edge 가 여는 edge://downloads-hub 는 닫지 않는다 — Playwright 로 닫으면 끝나지 않았다 (10-07). `site_pages` 가 뺀다
        context.remove_listener("page", watch)
        for pg in watched:
            if not pg.is_closed():
                pg.remove_listener("download", take)


def save_download(download, target):
    """받은 파일을 `target`(Path) 에 저장하고 정말 엑셀인지 본다 (`webmail.verify_excel`). 아니면 `SiteFileError`."""
    from collect import webmail

    failure = download.failure()
    if failure:
        raise DownloadTimeout(f"다운로드가 끝나지 않았다: {download.suggested_filename} ({failure})")
    download.save_as(str(target))
    try:
        webmail.verify_excel(target)
    except webmail.MailError as exc:
        raise SiteFileError(str(exc)) from exc
    return target


_ROW_SIGNS_JS = """(a) => JSON.stringify([...document.querySelectorAll(a.rows)].map(r =>
  (a.cell ? [...r.querySelectorAll(a.cell)] : [r]).map(c => c.textContent).join('|')))"""
_QUIET_JS = """(a) => new Promise(done => {
  const root = document.querySelector(a.root) || document.body;
  let calm, hard, observer;
  const end = ok => { observer.disconnect(); clearTimeout(calm); clearTimeout(hard); done(ok); };
  observer = new MutationObserver(() => { clearTimeout(calm); calm = setTimeout(() => end(true), a.quiet); });
  observer.observe(root, {subtree: true, childList: true, characterData: true});
  calm = setTimeout(() => end(true), a.quiet); hard = setTimeout(() => end(false), a.max);
})"""


def fresh_indexes(before: list[str], after: list[str]) -> list[int]:
    """`after` 에서 `before` 에 없던 줄의 번호. 같은 글자 줄이 여럿이면 개수로 센다."""
    left, out = Counter(before), []
    for index, sign in enumerate(after):
        if left[sign] > 0:
            left[sign] -= 1
        else:
            out.append(index)
    return out


class RowWatch:
    """새로 생긴 줄만 — `watch.mark()` → 누르기 → `watch.new_rows(empty=…)`. 글자는 textContent 로 읽는다
    (바쁜 PC 에서 innerText 는 칸 사이 공백이 빠져 옛 줄이 새 줄처럼 보였다 — 다른 RPA 실측). `cell` 은 주문번호 칸처럼 좁힌다."""

    def __init__(self, page, rows: str, *, cell: str | None = None, root: str = "body",
                 quiet_max_ms: int = QUIET_MAX_MS) -> None:
        self.page, self.rows, self.cell, self.root = page, rows, cell, root
        self.quiet_max_ms = quiet_max_ms
        self.before: list[str] = []

    def _quiet(self) -> bool:
        return bool(self.page.evaluate(_QUIET_JS, {"root": self.root, "quiet": QUIET_MS, "max": self.quiet_max_ms}))

    def _read(self) -> list[str]:
        signs = eval_json(self.page, _ROW_SIGNS_JS, {"rows": self.rows, "cell": self.cell})
        return [" ".join(sign.split()) for sign in signs]

    def mark(self) -> int:
        """누르기 전 — 조용해진 뒤의 줄을 기준으로 (늦게 그려지는 옛 줄이 새 줄로 읽히지 않게)."""
        if not self._quiet():
            raise SiteScreenError("화면이 바뀜: 목록이 계속 바뀌어 기준을 잡지 못했다")
        self.before = self._read()
        return len(self.before)

    def new_rows(self, *, empty: str | None = None, timeout_s: float = ROWS_TIMEOUT_S) -> list[tuple[int, str]]:
        """[(번호, 줄 글자)] — 번호는 `rows` selector 의 nth. 0건은 사이트가 준 `empty` 가 보일 때만 []."""
        def poll():
            if empty and _visible(self.page, empty):
                return "empty"
            return "rows" if fresh_indexes(self.before, self._read()) else None

        try:
            kind = wait_for(poll, "새 줄", timeout=timeout_s, interval=POLL_S)
        except WaitTimeout as exc:
            raise SiteScreenError("화면이 바뀜: 새 줄이 생기지 않았다") from exc
        if kind == "empty":
            return []
        if not self._quiet():
            raise SiteScreenError("화면이 바뀜: 목록이 계속 바뀌어 새 줄을 읽지 못했다")
        after = self._read()
        return [(index, after[index]) for index in fresh_indexes(self.before, after)]
