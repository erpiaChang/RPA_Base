r"""모듈 — 사람이 고르는 **기능 단위**이자 **과금 행위 단위** (2026-09-21 사용자 확정).

단계(`steps_*`)는 대시보드·재진입 판단의 단위이고, 모듈은 그보다 크다. 사람은
모듈을 고르고, **고른 모듈의 단계만** 계획에 들어간다. 순서는 `ALL` 로 고정이다.
설계와 행위 수 규칙은 `docs/BILLING.md` 2절.

| 모듈 | 단계 |
| --- | --- |
| 메일 엑셀 받기 | 인증 경로 점검 / 웹메일 로그인 / 받은메일함 |
| 주문수집·매출처리 | 화면 진입 / 엑셀수집·자동수집 / 매출처리 |
| 물류대기 | 물류대기 |
| 물류관리 | 물류관리 |

프로그램 실행·로그인은 ERPia 모듈을 하나라도 고르면 **자동으로** 들어간다.

이 파일은 수집·ERPia 단계 표를 둘 다 쓴다 → 통합·실행용 배포본에만 들어간다
(`build_collect.spec` / `build_erpia.spec` 의 FORBIDDEN).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from orchestrator import steps, steps_collect, steps_erpia
from orchestrator.steps import Step


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    erpia: bool         # ERPia 를 띄워야 하는가


MAIL = Module("mail", "메일 엑셀 받기", erpia=False)
ORDERS = Module("orders", "주문수집·매출처리", erpia=True)
LOGI_WAIT = Module("logistics_wait", "물류대기", erpia=True)
LOGI = Module("logistics", "물류관리", erpia=True)

# ★ 실행 순서. 고른 순서와 상관없이 이 순서로 돈다.
ALL = (MAIL, ORDERS, LOGI_WAIT, LOGI)
IDS = tuple(module.id for module in ALL)


def normalize(selected: list[str] | None) -> list[Module]:
    """고른 모듈을 **실행 순서대로.** `None` 이면 전부(예전 동작).

    빈 목록은 `None` 과 다르다 — 사람이 전부 끈 것이라 **막는다.**
    """
    if selected is None:
        return list(ALL)
    unknown = [value for value in selected if value not in IDS]
    if unknown:
        raise ValueError(f"알 수 없는 기능: {unknown}")
    chosen = [module for module in ALL if module.id in selected]
    if not chosen:
        raise ValueError("실행할 기능을 최소 하나 골라야 한다.")
    return chosen


def needs_erpia(chosen: list[Module]) -> bool:
    return any(module.erpia for module in chosen)


# 빌드에 꼭 구울 값 — 실행 창에 칸이 없다 (기본값 없음, 10-02). 화면에서 고치는 값(비밀번호·택배사 …)은 넣지 않는다.
# `tools/bake_settings`(굽기 전 막기)와 `gui/run_app`(구운 값이 비면 알림)이 같이 쓴다 — tools 는 빌드에 없다
BAKED_ERPIA = ("login_company_code", "login_user_id", "target_exe_name")
BAKED_MAIL = ("mail_url", "mail_user_id", "mail_unread_only", "mail_days_back",
              "browser_channel", "phone_os", "sms_keyword")


def baked_required(ids) -> tuple[str, ...]:
    """그 기능들에 꼭 구울 키."""
    ids = set(ids)
    return (BAKED_ERPIA if ids - {MAIL.id} else ()) + (BAKED_MAIL if MAIL.id in ids else ())


def plan(chosen: list[Module], sources: list[str]) -> list[Step]:
    """고른 모듈이 **실제로 지나갈** 단계. 고르지 않은 모듈은 넣지 않는다.

    `sources` 는 `Options.normalized_sources()` 가 준 실행 순서대로의 수집방식이다.
    주문수집·매출처리를 고르지 않았으면 보지 않는다.

    단계마다 **큰 단계 = 모듈 이름**을 붙인다 (09-29 사용자 요청 — 사람은 11단계가 아니라 4단계로 느낀다).
    ERPia 실행·로그인은 처음 고른 ERPia 모듈에 넣는다.
    """
    ids = {module.id for module in chosen}
    out: list[Step] = []
    if MAIL.id in ids:
        out += _staged(MAIL, steps_collect.plan())
    launch = [steps_erpia.LAUNCH, steps_erpia.LOGIN] if needs_erpia(chosen) else []
    if ORDERS.id in ids:
        # 수집방식 → 단계. 순서·검증은 `steps_erpia.plan` 과 같은 표를 쓴다.
        unknown = [s for s in sources if s not in steps_erpia.SOURCE_STEPS]
        if unknown:
            raise ValueError(f"알 수 없는 수집 방식: {unknown}")
        out += _staged(ORDERS, [*launch, steps_erpia.SCREEN,
                                *(steps_erpia.SOURCE_STEPS[s] for s in sources),
                                steps_erpia.SALES])
        launch = []
    if LOGI_WAIT.id in ids:
        out += _staged(LOGI_WAIT, [*launch, steps_erpia.LOGI_WAIT])
        launch = []
    if LOGI.id in ids:
        out += _staged(LOGI, [*launch, steps_erpia.LOGI])
    return out


def _staged(module: Module, items: list[Step]) -> list[Step]:
    return [replace(item, stage=module.name) for item in items]


# 단계 → 그 단계를 가진 기능. 실행·로그인은 뺀다 — 막히면 첫 ERPia 기능의 단계도 '실행 전' 으로 남아 잡힌다
_OWNER = {
    **{item.id: MAIL.id for item in steps_collect.plan()},
    steps_erpia.SCREEN.id: ORDERS.id, steps_erpia.SALES.id: ORDERS.id,
    **{item.id: ORDERS.id for item in steps_erpia.SOURCE_STEPS.values()},
    steps_erpia.LOGI_WAIT.id: LOGI_WAIT.id,
    steps_erpia.LOGI.id: LOGI.id,
}


def resume_from(entry: dict, known: tuple[str, ...] = IDS) -> list[str]:
    """지난 실행(이력 한 줄)에서 **멈춘 기능부터 끝까지** — [멈춘 곳부터 다시] 가 돌릴 기능 id (10-01).

    끝나지 않은 단계(완료·건너뜀이 아닌 것)가 있는 첫 기능부터 그 실행이 고른 마지막 기능까지다. 뒤 기능은 앞 기능의
    결과를 받아 돌므로 같이 다시 돈다 (메일만 다시 받으면 받은 엑셀이 다음 실행까지 안 올라간다). 이미 한 것은 다시
    하지 않는다 — 받은 메일·올린 엑셀·매출처리한 주문·건 보류. 다시 할 것이 없으면 빈 목록.
    """
    if entry.get("state") not in ("failed", "cancelled", "unfinished"):
        return []
    names = {module.name: module.id for module in ALL}
    chosen = [names[name] for name in entry.get("modules") or () if name in names]
    unfinished = {_OWNER[row.get("id")] for row in entry.get("steps") or ()
                  if row.get("id") in _OWNER and row.get("state") not in steps.FINISHED}
    first = next((key for key in IDS if key in unfinished and key in chosen), None)
    if first is None:
        return []
    return [key for key in IDS[IDS.index(first):] if key in chosen and key in known]
