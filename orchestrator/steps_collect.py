r"""수집 흐름의 단계 표.

수집은 **우리 데이터를 바꾸지 않는다.** 메일을 읽음으로 만들고 첨부를
내려받는 것이 전부다. 그래서 재진입 정책이 ERPia 쪽보다 느슨하다.
"""
from __future__ import annotations

from orchestrator.steps import CONDITIONAL, SAFE, Step

PREFLIGHT = Step(
    "preflight", "휴대폰 인증 준비", SAFE,
    "설정 조합만 본다. 아무것도 바꾸지 않는다.",
)
MAIL_LOGIN = Step(
    "mail_login", "메일 로그인·인증", SAFE,
    "시크릿 창이라 세션이 남지 않는다. 다시 하면 인증 문자를 다시 받는다.",
)
MAILBOX = Step(
    "mailbox", "메일 엑셀 받기", CONDITIONAL,
    "받은 메일은 읽음으로 남고 매니페스트에 기록되므로 다시 열지 않는다. "
    "받지 못한 메일은 기록을 보고 다음 실행에서 다시 받는다 (읽음 상태는 바꾸지 않는다).",
)

ALL = (PREFLIGHT, MAIL_LOGIN, MAILBOX)


def plan() -> list[Step]:
    """수집 흐름이 지나갈 단계. 입력에 따라 달라지지 않는다."""
    return list(ALL)
