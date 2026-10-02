r"""메일 사이트에서 주문 엑셀 받기 — **공용 틀** (10-02). 사이트마다 다른 화면 조작은 사이트 파일이 한다.

**원본(git)에는 사이트가 없다** — 사이트 파일은 git 밖 `collect/sites/local/` 에 둔다 (사용자 결정 10-02:
실제 업체가 어떤 사이트를 쓸지 모르고, 지금까지의 사이트는 로그인·2차 인증 시험용이었다). 계약은 `MailSite`, 절차는 `docs/SITES.md`.

선별 기준 (사용자 확정 2026-09-08 · 09-09)

1. **읽지 않은 메일**만 — 설정 `mail_unread_only` (비우면 멈춘다, 10-02)
2. **발신자 주소 완전일치** — `mail_senders` 에 있음. **비어 있으면 발신자를 보지 않는다**
3. **제목에 `[...]` 가 있을 것.** `mail_sites` 에 적으면 그 목록과 **완전일치**만, 비우면 대괄호만 있으면 대상
3-1. **첨부가 있을 것.** 목록에서는 '첨부가 있다' 까지만 안다. 엑셀인지는 열어 봐야 안다
4. **기간** — `mail_days_back` 이 0 이상이면 `오늘 - N일` 이후 수신분만 (`0`=오늘). `-1` 이면 기간 조건 없음 (비우면 멈춘다)

★ 메일을 **열면 읽음 처리된다.** 첨부는 보기 화면에서만 받을 수 있으므로 열 수밖에 없다. 그래서 실패도 매니페스트에 남긴다.
★ **첨부가 엑셀이 아니면 실패가 아니라 `skipped`** 다 (09-09). 읽음으로 둔다 — 다시 열어도 결과가 같다.
★ **메일을 읽지 않음으로 되돌리지 않는다** (09-30). 받기 실패한 메일은 매니페스트의 실패 기록으로 다음 실행에서
  **읽음이어도 다시 연다** (`retry_keys` → `select_targets(retry=)`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from config.settings import LOG_DIR, SETTINGS
from collect import auth_code
from collect import manifest as manifest_mod
from collect import storage
from utils.logger import get_logger, user_log
from utils.wait import pause

log = get_logger(__name__)

# 2차 인증에 실패하면 **1분 뒤에 한 번 더** 시도한다 (사용자 확정 2026-09-09).
# 인증 문자의 도착 시각을 [휴대폰 연결] 에서 **분 단위**로만 읽을 수 있어, 같은 분 안에 다시 요청하면
# 직전 인증번호를 새 것으로 착각해 또 실패한다 (09-09 실측, 2회 재현). 분이 바뀌기를 기다린다.
AUTH_ATTEMPTS = 2          # 무제한 재시도 금지
AUTH_RETRY_DELAY = 60.0    # 초. 분이 바뀌도록 기다린다
# 인증번호가 **아예 안 오면** 1분 뒤 새로 요청한다. 최대 2번 더, 그 뒤 중단 (사용자 확정 09-28). 위와 따로 센다.
CODE_RETRIES = 2
OPEN_ATTEMPTS = 2          # 메일 열기. 무제한 재시도 금지
MAX_PAGES = 20             # 목록 페이지 상한 — 기간 조건이 없어도(-1) 무한히 넘기지 않는다 (사이트 파일이 쓴다)

# 엑셀 파일의 첫 바이트. 내용이 정말 엑셀인지 본다. .xlsx/.xlsm = zip(PK) / .xls = OLE 복합문서
EXCEL_MAGIC = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
# 업로드 쪽(`automation/order_mapping.EXCEL_SUFFIXES`)과 **같아야 한다.** 예전에 `.xlsm` 이 빠져 조용히 누락됐다 (09-10)
EXCEL_SUFFIXES = (".xlsx", ".xls", ".xlsm")
SITE_RE = re.compile(r"\[([^\]]+)\]")


class LoginError(RuntimeError):
    """메일 사이트 로그인 또는 2차 인증에 실패했다."""


class MailError(RuntimeError):
    """메일함 처리에 실패했다."""


class NoExcelAttachment(MailError):
    """첨부는 있었지만 **엑셀이 아니었다.** 실패가 아니라 무시할 대상이다."""


class SiteMissing(MailError):
    """메일 사이트 연결(사이트 파일)이 없다 — 원본(git)은 비어 있다 (10-02)."""


@dataclass(frozen=True)
class MailRow:
    key: str
    fromaddr: str
    unread: bool
    subject: str
    has_attach: bool
    date_text: str                  # 목록의 수신일시 글자 그대로 (로그용)
    received: date | None = None    # 사이트 파일이 읽은 수신 **날짜**. 못 읽으면 None (추측하지 않는다)
    page: int = 1                   # 이 메일이 보인 목록 페이지. 열 때 그 페이지로 간다

    def site(self) -> str | None:
        """제목의 첫 `[...]` 안 문자열. 비었으면(`[ ]`) None — 사이트명이 없는 메일이다."""
        matched = SITE_RE.search(self.subject)
        return (matched.group(1).strip() if matched else "") or None


@dataclass(frozen=True)
class MailSite:
    """메일 사이트 하나 — 사이트 파일이 `SITE = MailSite(...)` 로 내놓는다 (`collect/sites/__init__.py`)."""

    id: str
    name: str                                   # 사람이 보는 이름 (로그·화면)
    login_once: Callable                        # (page) -> None. 로그인 + 2차 인증 한 번. 거부는 LoginError, 문자 안 옴은 auth_code.CodeNotArrived
    list_rows_until: Callable                   # (page, cutoff: date | None) -> list[MailRow]. 최신순, 기간 안쪽이 끝날 때까지 (MAX_PAGES 상한)
    open_mail: Callable                         # (page, row) -> None. 한 번 연다 (첨부 단추가 보일 때까지). 못 열면 예외
    attachment_buttons: Callable                # (page) -> list. 보기 화면의 첨부 받기 단추들
    back_to_list: Callable                      # (page) -> None. 받은메일함 목록으로 (떠 있는 팝업은 닫는다)


def login(page, site: MailSite) -> None:
    """로그인 → 2차 인증까지. 거부되면 1분 뒤 한 번 더(`AUTH_ATTEMPTS`), 인증번호가 안 오면 1분 뒤 최대 2번 더
    새로 요청한다(`CODE_RETRIES`). 인증번호를 새로 받아야 하므로 로그인 화면부터 다시 간다."""
    SETTINGS.require("mail_url", "mail_user_id", "mail_password")

    rejected = missing = 0
    # 두 종류가 각자 상한에서 raise 하므로 이 범위를 다 쓰기 전에 끝난다
    for _ in range(AUTH_ATTEMPTS + CODE_RETRIES + 1):
        try:
            site.login_once(page)
            return
        except LoginError as exc:
            rejected += 1
            log.error("로그인/2차 인증 실패 (%d/%d): %s", rejected, AUTH_ATTEMPTS, exc)
            if rejected >= AUTH_ATTEMPTS:
                raise
            user_log().warning("메일 인증을 통과하지 못해 1분 뒤 다시 시도합니다.")
        except auth_code.CodeNotArrived as exc:
            missing += 1
            if missing > CODE_RETRIES:
                log.error("인증번호를 %d번 요청했는데 오지 않았다. 중단한다: %s", missing, exc)
                raise
            log.error("인증번호가 오지 않았다. 1분 뒤 다시 요청한다 (%d/%d): %s",
                      missing, CODE_RETRIES, exc)
            user_log().warning("인증번호 문자가 오지 않아 1분 뒤 다시 요청합니다 (%d/%d).",
                               missing, CODE_RETRIES)
        pause(AUTH_RETRY_DELAY, "인증번호를 새로 받기 위해 분이 바뀌기를 기다린다")
    raise LoginError("로그인에 실패했다 (재시도 상한).")


def mail_cutoff() -> date | None:
    """`mail_days_back` 의 기준 날짜. 이 날 **이후** 수신분만 본다. -1 이면 None(조건 없음). 비면 멈춘다 (10-02).

    0 이면 오늘만, 1 이면 어제부터 오늘까지 (사용자 확정 2026-09-08).
    """
    days_back = SETTINGS.mail_days_back
    if days_back is None or isinstance(days_back, bool):
        raise MailError("메일을 며칠 전까지 볼지(mail_days_back)가 비었다 — 기본값이 없다")
    if days_back < 0:
        return None
    return date.today() - timedelta(days=int(days_back))


def retry_keys(cutoff: date | None) -> set[str]:
    """다시 열 메일 key — 메일 기간(`cutoff`~오늘) 안의 날짜 매니페스트에서 **받기 실패만 있는** 메일.

    한 번이라도 받았거나(`downloaded`) 엑셀이 아니었던(`skipped`) 메일은 빼서 같은 메일을 또 받지 않는다.
    기간 조건이 없으면(-1) 오늘 것만 본다.
    """
    today = date.today()
    failed: set[str] = set()
    done: set[str] = set()
    day = cutoff or today
    while day <= today:
        if storage.date_dir(day, create=False).is_dir():     # 지난 날짜 폴더를 새로 만들지 않는다
            for item in manifest_mod.load(day).items:
                (failed if item.status == manifest_mod.STATUS_FAILED else done).add(item.mail_key)
        day += timedelta(days=1)
    return failed - done


def select_targets(rows: list[MailRow], retry: set[str] | frozenset[str] = frozenset()) -> list[MailRow]:
    """설정 기준으로 대상 메일을 고른다. 이유를 로그에 남긴다. `retry` 의 메일은 읽음이어도 대상이다."""
    unread_only = SETTINGS.mail_unread_only
    if not isinstance(unread_only, bool):
        raise MailError("읽지 않은 메일만 볼지(mail_unread_only)가 비었다 — 기본값이 없다")
    cutoff = mail_cutoff()
    if cutoff is not None:
        log.info("기간 조건: %s 이후 수신분만 (mail_days_back=%s)", cutoff, SETTINGS.mail_days_back)

    senders = {s.strip().lower() for s in (SETTINGS.mail_senders or [])}
    sites = set(SETTINGS.mail_sites or [])
    if not sites:
        # 사용자 확정 2026-09-09: 목록을 비우면 **대괄호가 있기만 하면** 대상이다.
        # 사이트명이 ERPia 사이트 그리드에 없으면 업로드 단계에서 걸러진다.
        log.info("mail_sites 가 비어 있다. 제목에 대괄호가 있는 메일을 전부 본다.")
    if not senders:
        log.info("mail_senders 가 비어 있다. 발신자 조건을 걸지 않는다.")

    targets = []
    # ★ 왜 빠졌는지를 **세어 둔다** — 대상이 0건일 때 이유를 사람이 알 수 있게 늘 INFO 로 남긴다 (09-28)
    dropped: dict[str, int] = {}

    def skip(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for row in rows:
        if unread_only and not row.unread:
            if row.key not in retry:
                skip("이미 읽은 메일")
                continue
            log.info("지난 실행에서 받지 못한 메일이라 읽음이어도 다시 연다: %s", row.subject)
        if senders and row.fromaddr.lower() not in senders:
            log.debug("제외(발신자): %s / %s", row.fromaddr, row.subject)
            skip("발신자가 목록에 없다")
            continue
        site = row.site()
        if site is None:
            log.debug("제외(제목에 [사이트명] 없음): %s", row.subject)
            skip("제목에 [사이트명] 이 없다")
            continue
        if sites and site not in sites:
            log.debug("제외(사이트명 불일치): %s", site)
            skip("사이트명이 목록과 다르다")
            continue
        if not row.has_attach:
            log.warning("첨부가 없어 건너뛴다: %s", row.subject)
            skip("첨부가 없다")
            continue
        if cutoff is not None:
            if row.received is None:
                # 형태를 모르는 날짜다. 넣지도 빼지도 않고 **빼는 쪽**을 택한다.
                log.warning("수신일시를 읽지 못해 건너뛴다(%r): %s", row.date_text, row.subject)
                skip("수신일시를 읽지 못했다")
                continue
            if row.received < cutoff:
                log.debug("제외(기간 밖 %s): %s", row.received, row.subject)
                skip("기간 밖이다")
                continue
        targets.append(row)

    log.info("대상 메일 %d건 (목록 %d건 중)", len(targets), len(rows))
    if dropped:
        log.info("%s: %s", "대상이 없는 이유" if not targets else "빠진 메일",
                 " / ".join(f"{why} {n}건" for why, n in sorted(dropped.items(), key=lambda kv: -kv[1])))
    for row in targets:
        log.info("  - [%s] %s (%s)", row.site(), row.subject, row.fromaddr)
    return targets


def verify_excel(path) -> None:
    """내려받은 파일이 **정말 쓸 수 있는 엑셀인지** 본다 — 파일이 생겼나 / 0바이트가 아닌가 / 첫 바이트가 엑셀인가.

    로그인 페이지 HTML 이 `.xlsx` 이름으로 저장되는 일이 있다. 내용(암호 포함)은 업로드할 때 프로그램이 판정한다.
    """
    if not path.is_file():
        raise MailError(f"내려받은 파일이 없다: {path}")
    size = path.stat().st_size
    if size == 0:
        raise MailError(f"내려받은 파일이 비어 있다(0바이트): {path.name}")
    with path.open("rb") as handle:
        head = handle.read(8)
    if not any(head.startswith(magic) for magic in EXCEL_MAGIC):
        raise MailError(
            f"엑셀 파일이 아니다(첫 바이트 {head[:4]!r}): {path.name} — "
            "세션 만료 페이지가 저장됐을 수 있다.")
    log.info("파일 확인: %s (%d바이트)", path.name, size)


def _download_attachments(page, site: MailSite, item_dir) -> list[str]:
    """보기 화면의 첨부를 내려받아 저장한 파일명을 돌려준다."""
    buttons = site.attachment_buttons(page)
    if not buttons:
        raise MailError("보기 화면에서 첨부 다운로드 버튼을 찾지 못했다.")

    saved = []
    for index, button in enumerate(buttons):
        with page.expect_download(timeout=int(SETTINGS.timeouts.download * 1000)) as info:
            button.click()
        download = info.value
        name = download.suggested_filename
        if not name.lower().endswith(EXCEL_SUFFIXES):
            log.info("엑셀이 아니라 건너뛴다: %s", name)
            continue
        failure = download.failure()
        if failure:
            raise MailError(f"다운로드가 끝나지 않았다: {name} ({failure})")
        target = item_dir / name
        download.save_as(str(target))
        verify_excel(target)            # 저장했다고 끝이 아니다. 쓸 수 있는 파일인지 여기서 판정한다
        log.info("첨부 저장 (%d/%d): %s", index + 1, len(buttons), target.name)
        saved.append(name)

    if not saved:
        raise NoExcelAttachment("첨부에 엑셀 파일이 없다.")
    return saved


def _open(page, site: MailSite, row: MailRow) -> None:
    """메일을 연다 (**읽음 처리된다**). 실패하면 화면을 남기고 목록으로 돌아가 한 번 더 (`OPEN_ATTEMPTS`)."""
    last_error: Exception | None = None
    for attempt in range(1, OPEN_ATTEMPTS + 1):
        try:
            site.open_mail(page, row)
            return
        except Exception as exc:
            last_error = exc
            # 무엇이 떠 있는지 남긴다. "안 열렸다"와 "열렸는데 첨부가 없다"는 다르다.
            pages = len(page.context.pages)
            log.warning("메일 열기 %d/%d 실패. url=%s / 탭 %d개 / %s", attempt, OPEN_ATTEMPTS, page.url, pages, exc)
            if pages > 1:
                log.warning("다른 탭이 열려 있다: %s", [other.url for other in page.context.pages])
            shot = LOG_DIR / f"mail_open_fail_{row.key[:8]}_{attempt}.png"
            try:
                page.screenshot(path=str(shot))
                log.warning("실패 화면: %s", shot)
            except Exception as shot_exc:
                log.warning("스크린샷 실패: %s", shot_exc)
            if attempt < OPEN_ATTEMPTS:
                site.back_to_list(page)
    raise MailError(f"메일을 열지 못했다: {row.subject}") from last_error


def collect(page, site: MailSite, dry_run: bool = False) -> manifest_mod.Manifest:
    """받은메일함에서 대상 메일의 첨부를 내려받고 매니페스트를 남긴다.

    `dry_run=True` 면 **아무것도 열지 않고** 대상만 골라 보고한다 (메일을 열면 읽음 처리된다).
    """
    cutoff = mail_cutoff()
    rows = site.list_rows_until(page, cutoff)
    retry = retry_keys(cutoff)
    targets = select_targets(rows, retry)

    book = manifest_mod.load()
    if dry_run:
        log.info("[dry-run] 여기서 멈춘다. 메일을 열지 않았으므로 읽음 처리되지 않았다.")
        return book

    for row in targets:
        if book.has_mail(row.key):
            # ★ 받기 실패만 있는 메일은 다시 받는다 (`retry_keys`). 받았거나 엑셀이 아니었으면 건너뛴다
            if row.key in retry:
                log.info("지난 실행에서 실패한 메일이다. 다시 받는다: %s", row.subject)
            else:
                log.info("이미 매니페스트에 있다. 건너뛴다: %s", row.subject)
                continue

        name = row.site()
        entry = manifest_mod.Item(seq=0, site=name, path=None,
                                  mail_subject=row.subject, mail_key=row.key)
        try:
            # 폴더를 못 만들어도(이름·경로 길이) 그 메일만 실패다 — 뒤 메일까지 매번 멈추지 않게 (10-02 검토)
            item_dir, entry.seq = storage.make_item_dir(name)
            _open(page, site, row)
            saved = _download_attachments(page, site, item_dir)
            entry.path = f"{item_dir.name}/{saved[0]}"
            entry.downloaded_at = datetime.now().isoformat(timespec="seconds")
            entry.status = manifest_mod.STATUS_DOWNLOADED
        except NoExcelAttachment as exc:
            # 실패가 아니다. 열어 보니 대상이 아니었을 뿐이다. 되돌리지 않는다 — 다시 열어도 결과가 같다.
            log.info("엑셀 첨부가 아니라 무시한다: %s (%s)", row.subject, exc)
            entry.status = manifest_mod.STATUS_SKIPPED
            entry.error = str(exc)
        except Exception as exc:
            # 메일은 이미 열려 읽음이 됐다. 실패도 반드시 남긴다 — 다음 실행이 이 기록으로 다시 연다 (`retry_keys`)
            log.error("첨부 저장 실패: %s / %s", row.subject, exc)
            entry.status = manifest_mod.STATUS_FAILED
            entry.error = f"{type(exc).__name__}: {exc}"
        book.add(entry)
        manifest_mod.save(book)

        site.back_to_list(page)          # 목록으로 돌아가 다음 메일을 본다

    skipped = [i for i in book.items if i.status == manifest_mod.STATUS_SKIPPED]
    if skipped:
        log.info("엑셀이 아니어서 무시한 메일 %d건: %s", len(skipped), [i.mail_subject for i in skipped])
    return book
