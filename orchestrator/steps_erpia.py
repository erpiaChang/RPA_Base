r"""ERPia 업무 흐름의 단계 표. **재진입 정책이 여기 값으로 박혀 있다.**

처음 설계는 `docs/archive/HANDOFF_20260917.md` 6-2 의 표다. 그 문서는 이력으로
옮겼으므로 **지금은 이 파일이 기준이다.** 정책을 바꿀 때는 여기만 고친다.

## 이 표가 왜 필요한가

"아무 구간에서나 다시 시작" 을 그냥 열어 주면 안 된다. 매출처리는 **실제 전표를
만들고 되돌릴 수 없다.** 어느 단계가 그런 단계인지를 흐름 코드가 아니라
**값으로** 들고 있어야, 화면이 확인을 받을지 스스로 판단할 수 있다.
"""
from __future__ import annotations

from orchestrator.steps import (
    CONDITIONAL,
    IRREVERSIBLE,
    SAFE,
    WRITE,
    Step,
)

LAUNCH = Step(
    "launch", "ERPia 실행", SAFE,
    "인스턴스를 새로 띄운다. 이미 떠 있는 것은 그대로 둔다.",
)
LOGIN = Step(
    "login", "ERPia 로그인", SAFE,
    "몇 번이든 무해하다. 단, 같은 아이디로 이미 로그인돼 있으면 서버가 막는다.",
)
SCREEN = Step(
    "screen", "주문매핑 화면 열기", SAFE,
    "몇 번이든 무해하다. 이미 열려 있으면 그대로 쓴다.",
)
EXCEL = Step(
    "excel", "엑셀수집", CONDITIONAL,
    "통합 흐름은 매니페스트의 consumed_at 으로 같은 파일을 두 번 올리지 않는다. "
    "ERPia 단독 흐름에는 그 보호가 없어 **폴더의 엑셀을 그대로 다시 올린다.**",
)
SITE = Step(
    "site", "자동수집", CONDITIONAL,
    "다시 눌러도 되지만 실데이터가 많으면 2~3시간까지 간다.",
)
SALES = Step(
    "sales", "매출처리", IRREVERSIBLE,
    "★ **실제 전표를 만든다. 되돌릴 수 없다.** 이미 처리된 주문을 다시 "
    "처리하지는 않지만, 남아 있는 미매출 주문은 전표가 된다.",
)
LOGI_WAIT = Step(
    "logistics_wait", "물류대기", WRITE,
    "배송보류와 저장 상태가 바뀐다.",
)
LOGI = Step(
    "logistics", "물류관리", WRITE,
    "배송등록이 저장되고 운송장출력 창이 뜬다.",
)

# 화면(주문매핑) 안에서 도는 단계. 붙어서 재개할 때 이 앞은 다시 하지 않아도 된다.
ALL = (LAUNCH, LOGIN, SCREEN, EXCEL, SITE, SALES, LOGI_WAIT, LOGI)

# 수집방식 id → 단계. 키는 `erpia_flow.SOURCE_*` 값과 같아야 한다.
# 갈라지면 `plan()` 이 즉시 막으므로 조용히 어긋나지는 않는다.
SOURCE_STEPS = {EXCEL.id: EXCEL, SITE.id: SITE}


def plan(ordered_sources: list[str]) -> list[Step]:
    """이번 실행이 **실제로 지나갈** 단계 목록.

    `ordered_sources` 는 `Options.normalized_sources()` 가 준 **실행 순서대로의**
    수집방식이다. 순서의 authority 는 `erpia_flow.SOURCES` 이고, 여기서는
    받은 순서를 그대로 쓴다. 두 곳에서 순서를 정하면 갈라진다.

    고르지 않은 수집방식은 목록에 넣지 않는다. 대시보드의 `N/M` 이
    **실제로 할 일의 개수**여야 하기 때문이다.
    """
    unknown = [s for s in ordered_sources if s not in SOURCE_STEPS]
    if unknown:
        raise ValueError(f"알 수 없는 수집 방식: {unknown}")
    collect = [SOURCE_STEPS[s] for s in ordered_sources]
    return [LAUNCH, LOGIN, SCREEN, *collect, SALES, LOGI_WAIT, LOGI]
