r"""[확인 도구 - 읽기 전용] 메일 사이트 **공용 틀** (`collect/webmail.py`·`collect/sites`) — 가짜 사이트로 (10-02).

실제 사이트에 접속하지 않는다. 원본(git)은 사이트가 비어 있으니 등록표가 비었을 때의 동작과, 사이트 파일이 무엇이든
같은 틀(대상 고르기·다시 받기·열기 재시도·받기·매니페스트)을 본다. 시험 사이트 자체의 화면 조작은 git 밖 `tools/local/`.

    .venv\Scripts\python.exe -m tools.probe_webmail
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        continue  # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.

from collect import manifest as manifest_mod  # noqa: E402
from collect import sites, webmail  # noqa: E402
from config.settings import SETTINGS  # noqa: E402
from orchestrator import friendly  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)
KEYS = ("download_base_dir", "mail_unread_only", "mail_senders", "mail_sites", "mail_days_back")


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name, f" - {detail}" if detail else "")
    return ok


@contextmanager
def settings(base: Path, **values):
    saved = {key: getattr(SETTINGS, key) for key in KEYS}
    try:
        SETTINGS.download_base_dir = str(base)
        SETTINGS.mail_unread_only, SETTINGS.mail_senders, SETTINGS.mail_sites = True, [], []
        SETTINGS.mail_days_back = -1
        for key, value in values.items():
            setattr(SETTINGS, key, value)
        yield
    finally:
        for key, value in saved.items():
            setattr(SETTINGS, key, value)


def row(key: str, unread: bool = True, subject: str = "[사이트A] 주문", received: date | None = None):
    return webmail.MailRow(key=key, fromaddr="a@example.com", unread=unread, subject=subject,
                           has_attach=True, date_text="", received=received or date.today())


def check_registry() -> list[bool]:
    """원본(git)에는 사이트가 없다 — 등록표가 비면 SiteMissing, 사람 말은 '연결이 없다'.

    sys.modules 에 None 을 넣는 흉내는 진짜 부재와 다른 예외 길을 탄다 (10-02 검토 — 진짜로 없으면 ImportError 였다).
    collect.sites 의 찾는 자리를 빈 임시 폴더로 돌려 **진짜로 없게** 만든다.
    """
    import collect.sites as pkg

    keep_path, keep_mod = list(pkg.__path__), sys.modules.pop("collect.sites.local", None)
    keep_attr = pkg.__dict__.pop("local", None)
    with tempfile.TemporaryDirectory() as nothing:
        pkg.__path__[:] = [nothing]
        try:
            try:
                empty = sites.available()
            except ImportError as exc:      # 진짜 부재에서 죽으면 실패로 센다
                empty = exc
            try:
                sites.mail_site()
                missing = None
            except (webmail.SiteMissing, ImportError) as exc:
                missing = exc
        finally:
            pkg.__path__[:] = keep_path
            if keep_mod is not None:
                sys.modules["collect.sites.local"] = keep_mod
            if keep_attr is not None:
                pkg.local = keep_attr
    missing = missing if isinstance(missing, webmail.SiteMissing) else None
    told = friendly.explain(missing) if missing else ""
    return [check("★ 사이트 파일이 없으면 등록표가 비고 mail_site() 는 SiteMissing", empty == () and missing is not None),
            check("SiteMissing 은 사람 말로 — '메일 사이트 연결이 아직 없습니다'", "메일 사이트 연결" in told, told[:60])]


def check_required(base: Path) -> list[bool]:
    """기본값이 없다 (10-02) — 비면 조용히 '전체 기간'·'읽은 메일도' 로 돌지 않고 멈춘다."""
    out = []
    with settings(base, mail_days_back=None):
        try:
            webmail.mail_cutoff()
            out.append(check("★ 메일 기간이 비면 MailError (조건 없음으로 돌지 않는다)", False))
        except webmail.MailError as exc:
            out.append(check("★ 메일 기간이 비면 MailError (조건 없음으로 돌지 않는다)", "mail_days_back" in str(exc)))
    with settings(base, mail_unread_only=None):
        try:
            webmail.select_targets([row("k1")])
            out.append(check("★ '읽지 않은 메일만' 이 비면 MailError (읽은 메일까지 열지 않는다)", False))
        except webmail.MailError:
            out.append(check("★ '읽지 않은 메일만' 이 비면 MailError (읽은 메일까지 열지 않는다)", True))
    with settings(base, mail_days_back=0):
        today = date.today()
        picked = [r.key for r in webmail.select_targets(
            [row("t", received=today), row("y", received=today - timedelta(days=1)),
             webmail.MailRow(key="?", fromaddr="", unread=True, subject="[사이트A] x", has_attach=True,
                             date_text="모름")])]
        out.append(check("기간 0 = 오늘만, 날짜를 못 읽은 메일은 뺀다", picked == ["t"], str(picked)))
    return out


def check_retry(base: Path) -> list[bool]:
    """받기 실패한 메일은 **읽음이어도** 다음 실행에서 다시 연다 — 읽음 상태를 바꾸지 않는다 (09-30)."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    def item(key: str, status: str) -> manifest_mod.Item:
        return manifest_mod.Item(seq=1, site="사이트A", path=None, mail_subject="[사이트A] 주문",
                                 mail_key=key, status=status)

    out = []
    with settings(base):
        manifest_mod.save(manifest_mod.Manifest(date=f"{yesterday}", items=[
            item("k1", "failed"), item("k2", "failed"), item("k3", "downloaded")]), yesterday)
        manifest_mod.save(manifest_mod.Manifest(date=f"{today}", items=[
            item("k2", "downloaded"), item("k4", "skipped")]), today)
        retry = webmail.retry_keys(yesterday)
        out.append(check("★ 기간 안의 받기 실패만 다시 연다 (그 뒤 받은 것·받은 것·엑셀 아님은 뺀다)",
                         retry == {"k1"}, str(sorted(retry))))
        out.append(check("기간 조건이 없으면 오늘 기록만 본다", webmail.retry_keys(None) == set()))
        webmail.retry_keys(today - timedelta(days=3))
        out.append(check("기록이 없는 지난 날짜 폴더를 만들지 않는다",
                         not (base / f"{today - timedelta(days=3):%Y-%m-%d}").exists()))
        picked = [r.key for r in webmail.select_targets(
            [row("k1", unread=False), row("k3", unread=False), row("u1")], retry)]
        out.append(check("★ 읽은 메일은 다시 열 목록에 있을 때만 대상", picked == ["k1", "u1"], str(picked)))
        blank = [r.key for r in webmail.select_targets([row("b1", subject="[ ] 주문")], set())]
        out.append(check("제목이 `[ ]` 뿐이면 사이트명이 없는 메일 — 대상에서 뺀다 (폴더 이름 오류로 멈추지 않게)",
                         blank == [], str(blank)))
    out.append(check("★ 메일을 읽지 않음으로 되돌리는 코드가 없다",
                     not any(hasattr(webmail, name) for name in ("mark_unread", "JS_MARK_UNREAD"))))
    # 같은 날 받기 실패 → 다시 받기: 같은 mail_key 가 둘. 올린 쪽(받은 항목)에만 소비 표시 (10-02 검토)
    book = manifest_mod.Manifest(date=f"{today}", items=[item("k9", "failed"), item("k9", "downloaded")])
    book.mark_consumed(book.pending()[0])
    out.append(check("★ 다시 받은 엑셀을 올리면 그 항목이 소비된다 — 실패 항목에 찍혀 또 올라가지 않는다",
                     book.pending() == [] and book.items[0].consumed_at is None))
    return out


class FakeDownload:
    def __init__(self, name: str, body: bytes) -> None:
        self.suggested_filename, self.body = name, body

    def failure(self):
        return None

    def save_as(self, path: str) -> None:
        Path(path).write_bytes(self.body)


class FakePage:
    """다운로드·스크린샷만 흉내 낸다. 화면 조작은 가짜 사이트가 한다."""

    def __init__(self) -> None:
        self.url, self.context = "https://site-a.example/mail", SimpleNamespace(pages=[self])
        self.queue: list[FakeDownload] = []
        self.shots = 0

    @contextmanager
    def expect_download(self, timeout=None):
        holder = SimpleNamespace()
        yield holder
        holder.value = self.queue.pop(0)

    def screenshot(self, path=None):
        self.shots += 1


def check_collect(base: Path) -> list[bool]:
    """틀 전체 — 가짜 사이트로: 열기 한 번 실패 뒤 성공 / 엑셀 아님 / 엑셀이 아닌 내용 / 시험 실행."""
    page = FakePage()
    rows = [row("ok"), row("txt"), row("bad"), row("old", unread=False)]
    opened: list[str] = []
    backs: list[int] = []
    fail_once = {"ok"}

    def open_mail(_page, target):
        opened.append(target.key)
        if target.key in fail_once:
            fail_once.discard(target.key)
            raise TimeoutError("첨부 단추가 안 보인다")
        page.queue.append({"ok": FakeDownload("사이트A_주문.xlsx", b"PK\x03\x04data"),
                           "txt": FakeDownload("안내.pdf", b"%PDF"),
                           "bad": FakeDownload("사이트A_주문.xlsx", b"<html>")}[target.key])

    site = webmail.MailSite(id="fake", name="가짜", login_once=lambda p: None,
                            list_rows_until=lambda p, cutoff: list(rows), open_mail=open_mail,
                            attachment_buttons=lambda p: [SimpleNamespace(click=lambda: None)],
                            back_to_list=lambda p: backs.append(1))
    out = []
    with settings(base):
        book = webmail.collect(page, site, dry_run=True)
        out.append(check("시험 실행은 메일을 열지 않는다",
                         opened == [] and not {"ok", "txt", "bad"} & {i.mail_key for i in book.items}))
        book = webmail.collect(page, site)
    status = {i.mail_key: i.status for i in book.items}
    out.append(check("★ 열기를 한 번 실패하면 화면을 남기고 목록으로 돌아가 한 번 더 — 그다음 받는다",
                     opened.count("ok") == 2 and page.shots == 1 and status.get("ok") == manifest_mod.STATUS_DOWNLOADED,
                     f"{opened} / 화면 {page.shots}"))
    out.append(check("★ 첨부가 엑셀이 아니면 실패가 아니라 skipped", status.get("txt") == manifest_mod.STATUS_SKIPPED))
    out.append(check("★ 엑셀 이름인데 내용이 엑셀이 아니면 failed (다음 실행에서 다시 연다)",
                     status.get("bad") == manifest_mod.STATUS_FAILED, str(status)))
    out.append(check("읽은 메일은 열지 않는다 · 메일마다 목록으로 돌아간다",
                     "old" not in opened and len(backs) >= 3, f"돌아감 {len(backs)}"))
    return out


def main() -> int:
    setup_logging()
    base = Path(tempfile.mkdtemp(prefix="probe_webmail_"))
    results: list[bool] = []
    try:
        log.info("▶ 등록표 — 원본은 사이트가 없다")
        results += check_registry()
        log.info("▶ 설정 기본값 없음")
        results += check_required(base)
        log.info("▶ 받기 실패한 메일 — 읽음 그대로 다음 실행에서 다시 연다")
        results += check_retry(base)
        log.info("▶ 틀 전체 — 가짜 사이트")
        results += check_collect(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
