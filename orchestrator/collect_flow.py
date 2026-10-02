r"""메일에서 주문 엑셀을 내려받아 날짜·차수 폴더에 저장한다.

이 모듈은 `collect/` 만 쓴다. 다른 업무 모듈을 import 하지 않는다 —
수집 전용 배포본에 다른 기능의 흔적이 남지 않게 하기 위해서다.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

from orchestrator import report, steps_collect
from orchestrator.common import Hooks, Result
from utils.logger import get_logger, step

log = get_logger(__name__)

# 화면에 보여 줄 상세 표. **여기서 만든다** — 공용 GUI 는 기능을 모른다
# (`orchestrator/steps.py` 의 `StepEvent.columns` 주석).
MAILBOX_COLUMNS = ("사이트", "결과", "받은 엑셀", "메일 제목", "위치")

# 매니페스트 상태 → 사람이 읽을 말. 숫자만 보면 '무시' 를 실패로 읽는다.
MAIL_STATE_LABELS = {"downloaded": "받음", "skipped": "건너뜀 (엑셀 첨부 아님)",
                     "failed": "못 받음 (다음 실행에서 다시 받습니다)"}
# 인증 준비 점검의 화면 한 줄. 원문 마지막 줄에는 기기 번호가 들어갈 수 있다 (adb).
# 단계 이름('휴대폰 인증 준비')이 이미 말하므로 덧붙이지 않는다 — "준비 완료 — 준비됨" 이 된다.
PREFLIGHT_READY = ""


def mailbox_table(items, base: Path | None = None) -> list[tuple]:
    """받은메일함 — **이번 실행에 새로 본 메일만** 한 줄씩.

    매니페스트는 그날 누적이라 전부 보여 주면 지난 실행 것이 섞인다.
    `base`(그날 폴더)를 주면 받은 엑셀과 폴더를 리포트에서 눌러 열 수 있다.
    """
    rows = []
    for item in items:
        path = (base / item.path) if base is not None and item.path else None
        rows.append((
            item.site or "-",
            MAIL_STATE_LABELS.get(item.status, item.status),
            report.path_link(path) if path else (Path(item.path).name if item.path else "-"),
            (item.mail_subject or "").strip() or "-",
            report.path_link(path.parent, str(path.parent)) if path else "-",
        ))
    return rows


def mailbox_detail(ok: int, failed: int, skipped: int) -> str:
    text = f"메일 엑셀 {ok}개 받음" if ok else "새로 받은 엑셀 없음"
    if failed:
        text += f" · 못 받음 {failed}개 (다음 실행에서 다시 받습니다)"
    if skipped:
        text += f" · 엑셀 첨부가 아닌 메일 {skipped}개"
    return text


def run(hooks: Hooks | None = None, dry_run: bool = False) -> Result:
    """수집 흐름. 계획을 세우고 중단 토큰을 걸어 준다. 본체는 `_collect` 다."""
    hooks = hooks or Hooks()
    # 통합 흐름이 이미 계획을 세웠으면 덮지 않는다. 그쪽은 수집과 그 뒤 처리를
    # **한 계획**으로 들고 있어서, 여기서 다시 세우면 앞부분만 남는다.
    if not hooks.plan:
        hooks.begin(steps_collect.plan())
    # 이 블록 안에서 도는 **모든 대기가 중단 토큰을 본다** (`utils/cancel.py`).
    # 통합 흐름에서 불려도 같은 토큰이라 겹쳐 걸려도 무해하다.
    with hooks.running():
        return _collect(hooks, dry_run)


def _collect(hooks: Hooks, dry_run: bool) -> Result:
    from collect import auth_code, browser, sites, webmail

    from collect import phonelink

    # 실행마다 [다시 시도] 누른 횟수를 되돌린다. GUI 는 같은 프로세스에
    # 머물기 때문에, 되돌리지 않으면 두 번째 실행부터 재연결을 시도하지 않는다.
    phonelink.reset_retry_state()
    with hooks.stage(steps_collect.PREFLIGHT) as checked:
        ready = auth_code.preflight()    # 설정 조합이 틀렸으면 여기서 멈춘다
        checked.result = ready
        # 여러 줄이다(경로·기기 번호·준비 상태). 파일 로그에만 남기고 화면은 한 마디.
        log.info("인증 준비 점검 — %s", ready.replace("\n", " / "))
        checked.detail = PREFLIGHT_READY

    from collect import manifest as manifest_mod
    from collect import storage

    # 매니페스트는 **그날 누적**이다 — 이번 실행 건수 = 시작 전후 항목 차이 (안 그러면 지난 실행분까지 센다).
    # 키는 (메일 키, 차수): 지난 회차에 받기 실패한 메일을 다시 받으면 같은 메일 키로 새 항목이 붙는다.
    before_keys = {(item.mail_key, item.seq) for item in manifest_mod.load().items}

    # step() 으로 감싸면 실패한 단계 이름이 로그에 남고 **화면 스크린샷이 저장된다.**
    # 브라우저 쪽 실패는 화면을 봐야 원인을 알 수 있는 경우가 많다.
    with ExitStack() as opened:
        with hooks.stage(steps_collect.MAIL_LOGIN):
            # 사이트 찾기·브라우저 켜기도 이 단계 안 — 실패가 이 단계로 적혀야 통합 흐름이 뒤 기능으로 넘어간다 (`full_flow._full`)
            site = sites.mail_site()             # 원본(git)에는 사이트가 없다 → SiteMissing (10-02)
            page = opened.enter_context(browser.browser_page())
            with step(log, "웹메일 로그인"):
                webmail.login(page, site)
        with hooks.stage(steps_collect.MAILBOX) as collected:
            with step(log, "첨부 수집"):
                book = webmail.collect(page, site, dry_run=dry_run)

            # **이번 실행에** 새로 받은 것만 센다 (위 `before_keys` 주석 참고).
            fresh = [i for i in book.items if (i.mail_key, i.seq) not in before_keys]
            ok = len([i for i in fresh if i.status == manifest_mod.STATUS_DOWNLOADED])
            failed = len([i for i in fresh
                          if i.status == manifest_mod.STATUS_FAILED])
            skipped = len([i for i in fresh
                           if i.status == manifest_mod.STATUS_SKIPPED])
            today_ok = len(book.succeeded())
            collected.result = {"ok": ok, "failed": failed, "skipped": skipped,
                                "today_ok": today_ok}
            # 서버가 이 건수로 사용량(트래픽)을 센다: 처리한 메일(전체 - 받기 실패)이 1통이라도 있으면 1회 (09-30)
            # (엑셀이 아니었던 것은 포함 — docs/BILLING.md 1절, server/schema.sql 9절)
            # '엑셀 아님' 도 처리한 것이라 ok 에 넣는다 — 안 넣으면 화면 합계가 '3건 중 2건 성공' 으로 빠진다
            hooks.count(steps_collect.MAILBOX, total=ok + failed + skipped, done=ok + failed + skipped,
                        ok=ok + skipped, failed=failed)
            collected.detail = mailbox_detail(ok, failed, skipped)
            if dry_run:
                # 시험 실행은 메일을 열지 않는다 — 받은 것이 없다 (`webmail.collect` 의 dry_run).
                collected.detail = "확인만 함 (시험 실행) · 메일을 열지 않아 받은 엑셀 없음"
            if failed:
                sites = ", ".join(i.site or "?" for i in fresh
                                  if i.status == manifest_mod.STATUS_FAILED)
                hooks.attend(f"메일 엑셀 {failed}개를 받지 못했습니다 ({sites}) — "
                             "다음 실행 때 다시 받습니다.")
            # **어떤 메일을 받았는지**를 그대로 보여 준다. 숫자만으로는
            # 무엇이 빠졌는지 알 수 없어 로그를 뒤져야 했다.
            collected.columns = MAILBOX_COLUMNS
            collected.rows = mailbox_table(fresh, storage.date_dir(create=False))

    # '건너뜀'(엑셀 첨부 아님)은 실패가 아니다. 숫자를 섞으면 원인을 잘못 찾는다.
    summary = mailbox_detail(ok, failed, skipped)
    if today_ok != ok:
        # 지난 실행에서 받은 것이 있다. 숫자가 다른 이유를 함께 적는다.
        summary += f" (오늘 모두 {today_ok}개)"
    if dry_run:
        summary = "메일 시험 실행 — 받을 대상만 확인했습니다 (메일을 열지 않았습니다)"
    log.info("%s", summary)
    return Result(summary=summary,
                  details={"ok": ok, "failed": failed, "today_ok": today_ok,
                           "manifest": book},
                  steps=hooks.report())
