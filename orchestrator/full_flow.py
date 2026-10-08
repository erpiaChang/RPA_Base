r"""수집 → 업무 처리를 이어서 실행한다. **고른 기능(모듈)만** 돈다.

```
메일에서 엑셀 받기 → 날짜·차수 폴더 저장 → manifest.json
   ↓ (파일로 넘긴다. 코드로 직접 부르지 않는다)
프로그램 실행 → 로그인 → 화면 진입
   → 주문수집(엑셀수집 / 자동수집, 최소 1개 최대 2개. **엑셀수집이 먼저다**)
   → 엑셀수집: 매니페스트의 각 엑셀을 **사이트명 완전일치**로 각각 업로드
   → 매출처리 → 물류대기 → 물류관리
```

★ **기능 선택** (2026-09-21, `orchestrator/modules.py`). 메일 엑셀 받기 /
주문수집·매출처리 / 물류대기 / 물류관리 중 고른 것만 **위 순서대로** 돈다.
프로그램 실행·로그인은 ERPia 기능을 하나라도 고르면 자동으로 들어간다.
`Options.modules` 가 None 이면 전부(예전 동작).

두 단계를 잇는 것은 매니페스트다. 업로드에 성공한 항목은 `consumed_at` 을 채워
**같은 파일을 두 번 올리지 않는다.** 실패한 항목은 그대로 남겨 다음 실행에서
다시 시도한다. 메일을 고르지 않으면 **오늘 매니페스트에 남은 것**을 올린다.

수집 이후의 절차는 `erpia_flow` 와 **같은 코드**를 쓴다. 다른 것은 올릴 목록을
폴더에서 얻느냐(단독 실행) 매니페스트에서 얻느냐(통합)뿐이다.
"""
from __future__ import annotations

from pathlib import Path

# ★ 실행·로그인·매출처리·물류는 `erpia_flow` 의 단계 묶음을 **부른다** (같은 코드).
#   그래서 여기서 `start_new_instance` 등을 import 하지 않는다 — 남겨 두면 확인 도구가
#   여기만 가짜로 바꾸고 실제 ERPia 를 띄우는 일이 생긴다 (09-21).
from automation.order_mapping import (
    enter_order_mapping,
    log_upload_report,
    normalized_sales_mode,
)
from collect import manifest as manifest_mod
from config.settings import CUSTOMER, SETTINGS
from collect import storage
from orchestrator import collect_flow, erpia_flow, modules, steps, steps_collect, steps_erpia
from orchestrator.common import Hooks, Result
from utils.logger import get_logger, step, user_log

log = get_logger(__name__)
user = user_log()

# 메일이 멈췄을 때 남은 메일 단계의 한 줄 (`_full` 의 메일 except)
MAIL_STOPPED = "앞 단계가 실패해 하지 않음"


def _pending_uploads(book) -> list[tuple[str, Path]]:
    """아직 올리지 않은 항목만 `(사이트명, 파일경로)` 로 만든다."""
    base = storage.date_dir(create=False)
    items: list[tuple[str, Path]] = []
    for entry in book.pending():
        if not entry.path:
            continue
        path = base / entry.path
        if not path.is_file():
            log.warning("파일이 없어 건너뛴다: %s", path)
            continue
        items.append((entry.site, path))
    return items


def _mark_uploaded(book, results: list[dict]) -> int:
    """업로드에 성공한 항목만 `consumed_at` 을 채운다.

    결과와 매니페스트를 **순서로 맞추지 않는다.** 사이트명과 파일 경로를 함께
    보고 찾는다. 순서로 맞추면 나중에 결과 목록이 한 건이라도 어긋났을 때
    **엉뚱한 항목에 소비 표시**가 찍히고, 그 항목은 영영 올라가지 않는다.
    """
    base = storage.date_dir(create=False)
    marked = 0
    for result in results:
        if not result["ok"]:
            continue          # 실패한 것은 남겨 둔다. 다음 실행에서 다시 올린다
        for entry in book.pending():
            if entry.site != result["site"] or not entry.path:
                continue
            if str(base / entry.path) != str(result["path"]):
                continue
            book.mark_consumed(entry)
            marked += 1
            break
        else:
            log.warning("업로드는 됐는데 매니페스트에서 찾지 못했다: %s / %s",
                        result["site"], result["path"])
    return marked


def run(options: erpia_flow.Options, hooks: Hooks | None = None,
        dry_run: bool = False) -> Result:
    hooks = hooks or Hooks()
    hooks.recover = None        # ERPia 로그인 **뒤에** 넣는다. 실행·로그인 실패는 원래대로 멈춘다 (메일 실패는 `_full` 이 잇는다)
    # dry-run 스위치(인자·`options.dry_run`)를 하나로 합친다 — 따로 보면 메일은 열고 물류만 건너뛰는 반쪽 dry-run 이 된다.
    dry_run = bool(dry_run or options.dry_run)
    options.dry_run = dry_run
    # ★ 고른 기능만 돈다. 틀렸으면 **아무것도 하기 전에** 막는다.
    chosen = modules.normalize(options.modules)
    ids = {module.id for module in chosen}
    # 수집방식은 주문수집·매출처리를 할 때만 본다. 안 하면 둘 다 꺼 둬도 된다.
    sources = options.normalized_sources() if modules.ORDERS.id in ids else []
    if modules.ORDERS.id in ids:            # 매출처리 방식도 — 틀렸으면 몇 시간 수집한 뒤가 아니라 지금 (10-02)
        normalized_sales_mode(getattr(SETTINGS, "sales_mode", None))
    # ★ 통합 흐름은 **구간 실행을 지원하지 않는다.** 여기서 막지 않으면
    #   계획에는 "건너뜀" 으로 표시되는데 코드는 그대로 실행해서, 화면과 실제가
    #   어긋난다. 조용히 어긋나는 것이 가장 나쁘다.
    #   구간 실행은 ERPia 창(`erpia_flow`)에서 한다. 여기서는 **기능 선택**을 쓴다.
    if options.start_step or hooks.start_step:
        raise ValueError(
            "통합 흐름은 구간 실행을 지원하지 않는다. "
            "ERPia 창에서 구간 실행할 것.")
    log.info("고른 기능: %s", " → ".join(module.name for module in chosen))
    # 통합은 매니페스트로 올린 엑셀을 다시 올리지 않는다 (멈춘 뒤 안내 — `Hooks.failure_text`).
    hooks.rerun_notes = hooks.rerun_notes or dict(erpia_flow.RERUN_FULL)
    # 고른 기능의 단계를 **한 계획**으로 알린다. 화면의 `N/M` 이 실제로 할 일의
    # 개수여야 한다. 수집 흐름은 이 계획을 덮지 않는다.
    hooks.begin(modules.plan(chosen, sources))
    if dry_run:
        user.info("%s 메일은 열지 않고 받을 대상만 확인합니다.", erpia_flow.TRIAL_NOTICE)
    # 이 블록 안에서 도는 **모든 대기가 중단 토큰을 본다** (`utils/cancel.py`).
    with hooks.running():
        return _full(options, hooks, dry_run, chosen)


def rehearsed(area: str, dry_run: bool, customer=None) -> bool:
    """그 영역을 누르지 않고 확인만 할까 — 실행 전체가 시험 실행이거나, 업체 빌드가 그 영역을 시험 실행으로 두었다
    (10-08, `config/customer.Profile.rehearse`). 업체가 없으면 실행 전체의 값 그대로."""
    customer = CUSTOMER if customer is None else customer
    return dry_run or bool(customer is not None and customer.rehearse is not None and customer.rehearse(area))


def _full(options: erpia_flow.Options, hooks: Hooks, dry_run: bool,
          chosen: list[modules.Module]) -> Result:
    ids = {module.id for module in chosen}
    # 요약은 **조각으로 모은다.** 고르지 않은 기능이 요약에 남으면 안 된다.
    # 전부 고르면 예전과 같은 순서의 문장이 된다.
    parts: list[str] = []

    # --- 1) 메일 엑셀 받기 ------------------------------------------------
    collected = None
    if modules.MAIL.id in ids:
        try:
            with step(log, "수집"):
                collected = collect_flow.run(hooks=hooks, dry_run=dry_run)
        except Exception:
            # 메일이 멈춰도 뒤 기능은 돈다 (사용자 확정 09-30 — 엑셀 받는 곳 하나가 막혀 나머지까지 멈추면 안 된다).
            # 메일만 골랐거나, 실패로 적힌 메일 단계가 없으면(단계 밖에서 멈춤) 원래대로 멈춘다. `Cancelled` 는 여기 안 온다
            mail_ids = {item.id for item in steps_collect.plan()}
            mail = [run for run in hooks.runs if run.step.id in mail_ids]
            reason = next((run.error for run in mail if run.state == steps.FAILED and run.error), "")
            if not reason or len(chosen) == 1:
                raise
            for run in mail:
                if run.state == steps.PENDING:
                    hooks.skip(run.step, MAIL_STOPPED)
            hooks.attend("메일 엑셀 받기를 끝내지 못해 다음 기능으로 넘어갔습니다 — "
                         + reason.replace("\n", " "))
            parts.append("메일: 엑셀을 받지 못함")
        else:
            parts.append("메일: 확인만 함 (시험 실행)" if dry_run
                         else f"메일 엑셀 {collected.details.get('ok', 0)}개 받음")
        if modules.ORDERS.id not in ids:
            left = len(_pending_uploads(manifest_mod.load()))
            if left:
                # 받기만 하고 올리지 않는다. 다음에 주문수집을 고르면 올라간다.
                log.warning("주문수집·매출처리를 고르지 않았다. 받은 엑셀 %d건은 "
                            "올리지 않고 매니페스트에 남긴다.", left)
    collect_details = collected.details if collected is not None else {}

    if not modules.needs_erpia(chosen):
        # 메일만 골랐다. ERPia 를 띄우지 않는다.
        return Result(summary=" / ".join(parts) or "실행한 기능이 없습니다",
                      details={"collect": collect_details},
                      steps=hooks.report())

    # 올릴 엑셀은 **주문수집(엑셀수집)을 할 때만** 모은다.
    book = None
    uploads: list[tuple[str, Path]] = []
    if modules.ORDERS.id in ids and erpia_flow.SOURCE_EXCEL in (options.sources or []):
        if SETTINGS.download_base_dir:
            book = manifest_mod.load()
            uploads = _pending_uploads(book)
            log.info("업로드 대상 %d건: %s", len(uploads), [site for site, _ in uploads])
        else:
            # 기본값 없음 (10-02) — 저장 폴더가 없으면 받은 엑셀도 없다. 메일을 고르면 [실행] 이 폴더를 요구한다
            log.warning("엑셀 저장 폴더가 비어 있어 올릴 엑셀(매니페스트)을 찾지 않는다")

    # --- 2) 프로그램 실행 / 로그인 ---------------------------------------
    # `erpia_flow` 와 **같은 코드**다. 업데이트 안내 창이 로그인 창 대신 뜰 수 있다 (09-18).
    target = erpia_flow.launch_stage(options, hooks)
    hooks.on_target(target)
    main_title = erpia_flow.login_stage(options, target, hooks)
    hooks.on_login(main_title)
    # 여기부터 단계가 실패해도 ERPia 알림([확인] 하나)이 떠 있으면 닫고 다음 단계로 (09-22)
    hooks.recover = erpia_flow.popup_closer(target)

    # --- 3) 주문수집·매출처리 ---------------------------------------------
    rows = 0
    upload_results: list[dict] = []
    site_failures: dict[str, int] = {}
    site_info: dict = {}
    sales: dict = {}
    screen = None
    if modules.ORDERS.id in ids:
        with hooks.stage(steps_erpia.SCREEN):
            screen = enter_order_mapping(target)
        if screen is None:
            # 화면 진입이 ERPia 알림으로 끝나지 않았다 (`Hooks.recover`). 이 화면을 쓰는 단계는 못 한다.
            erpia_flow.skip_order_steps(hooks)
            parts.append(f"주문수집·매출처리: {erpia_flow.NO_SCREEN}")

    if screen is not None:
        # 고른 방식대로. 엑셀은 매니페스트에서 온다.
        def consumed(entry: dict) -> None:
            # ★ 올라간 **그 순간** 한 건씩 표시하고 저장한다 (10-02) — 끝에 몰아 찍으면 그 사이(안정 대기·
            #   자동수집은 몇 시간)에 중단·창 닫기·재부팅이 나면 다음 실행이 같은 엑셀을 또 올렸다
            try:
                if _mark_uploaded(book, [entry]):
                    manifest_mod.save(book)
            except OSError as exc:
                log.warning("올린 엑셀을 매니페스트에 표시하지 못했다: %s", exc)
                hooks.attend(f"올린 엑셀({entry.get('site')})을 기록하지 못했습니다 — "
                             "다음 실행에서 같은 엑셀이 다시 올라갈 수 있습니다")

        rows, upload_results = erpia_flow.collect_all(
            screen, options, target.pid, hooks, dry_run=rehearsed("collect", dry_run), uploads=uploads,
            site_failures=site_failures, site_info=site_info, on_uploaded=consumed)
        log.info("주문수집 후 조회된 주문 %d건", rows)

        if uploads and not upload_results and not dry_run:
            # 방금 받은 엑셀이 있는데 올리지 않았다. 요약의 `업로드 0/0건` 은
            # 옆의 `수집 N건` 과 함께 있으면 성공처럼 읽힌다 (2026-09-10 감사).
            log.warning("방금 받은 엑셀 %d건을 **올리지 않았다.** 수집방식에서 "
                        "엑셀수집을 켜지 않았다. 매니페스트에 그대로 남는다.",
                        len(uploads))

        # ★ dry_run 을 반드시 넘긴다. 매출처리는 되돌릴 수 없다 (2026-09-10 감사).
        sales, sales_text = erpia_flow.sales_stage(screen, target, hooks, rehearsed("sales", dry_run))

        parts.append(erpia_flow.upload_summary(upload_results))
        if site_info:
            parts.append(f"자동수집: {erpia_flow.site_detail(site_info, rehearsed('collect', dry_run))}")
        parts.append(sales_text)

    # --- 4) 물류대기 -----------------------------------------------------
    wait_result: dict = {}
    if modules.LOGI_WAIT.id in ids:
        wait_result, text = erpia_flow.logistics_wait_stage(
            target, hooks, rehearsed("logistics_wait", dry_run),
            hold=CUSTOMER is None or CUSTOMER.hold)
        parts.append(text)

    # --- 5) 물류관리 -----------------------------------------------------
    logi: dict = {}
    if modules.LOGI.id in ids:
        logi, text = erpia_flow.logistics_stage(target, options, hooks, rehearsed("logistics", dry_run))
        parts.append(text)

    # --- 6) 업체 기능 (10-08, `config/customer.py`) — 원본 뒤에 그 순서로 ----------------
    extra: dict = {}
    for module in chosen:
        if module.run is not None:
            extra[module.id], text = module.run(target, hooks, rehearsed(module.id, dry_run))
            parts.append(text)

    # 저장까지 끝난 뒤에 **맨 마지막으로** 업로드 실패를 다시 적는다.
    if upload_results:
        log_upload_report(upload_results)
    if site_failures:
        # ★ `실패` 는 **결함이라는 뜻이 아니다.** 주문이 없어도 실패로 나온다
        #   (`order_mapping.collect_failures` 의 "수집 상태의 실제 의미").
        log.warning("오늘 자동수집 상태 — %s. 주문이 없었거나 로그인에 "
                    "실패한 것이다(결함이 아닐 수 있다).",
                    erpia_flow.site_failure_summary(site_failures))

    # 알림을 닫고 넘어간 단계가 있으면 맨 앞에 — 없으면 "완료" 로 읽힌다 (09-22 검토)
    if hooks.unfinished_note():
        parts.insert(0, hooks.unfinished_note())
    if dry_run:
        parts.insert(0, erpia_flow.TRIAL_SUMMARY)
    return Result(summary=" / ".join(parts) or "실행한 기능이 없습니다", target=target,
                  details={"collect": collect_details, "uploads": upload_results,
                           "rows": rows, "sales": sales,
                           "site_failures": site_failures,
                           "logistics_wait": wait_result, "logistics": logi, "customer": extra},
                  steps=hooks.report())
