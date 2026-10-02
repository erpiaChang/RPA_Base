r"""브라우저 — 사이트 연결이 같이 쓰는 공용 부품 (10-02, 시험 사이트 파일에서 뗐다).

Edge 를 **시크릿(InPrivate)** 으로 띄운다 (사용자 지시 2026-09-08) — 세션이 남지 않아 실행마다 로그인한다.
브라우저 종류는 설정 `browser_channel` 이다. **기본값이 없다** (10-02) — 비면 띄우지 않고 멈춘다.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from config.settings import SETTINGS
from utils.logger import get_logger

log = get_logger(__name__)

CHANNELS = ("msedge", "chrome")       # 설정 browser_channel 의 선택지 (gui/build_app.py 칸도 이것)


class BrowserError(RuntimeError):
    """브라우저 설정이 비었거나 틀렸다, 또는 화면을 읽지 못했다."""


@contextmanager
def browser_page():
    """시크릿 브라우저를 띄우고 페이지를 넘긴다. 끝나면 반드시 닫는다."""
    channel = SETTINGS.browser_channel
    if channel not in CHANNELS:
        raise BrowserError(f"브라우저 종류(browser_channel)가 비었거나 틀렸다: {channel!r} — {' / '.join(CHANNELS)}")
    from playwright.sync_api import sync_playwright

    from collect import pw_driver

    pw_driver.prepare()            # 빌드본: exe 리소스의 node.exe 를 꺼내 쓴다 (09-29)
    with sync_playwright() as pw:
        args = ["--inprivate"] if channel == "msedge" else []
        browser = pw.chromium.launch(
            channel=channel,
            headless=bool(SETTINGS.browser_headless),     # 비면 창을 띄운다 — 2차 인증이 막히면 사람이 봐야 한다
            args=args,
        )
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
