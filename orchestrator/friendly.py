r"""실패를 **사람 말로** 바꾼다 (사용자 요청 2026-09-21). 원문은 파일 로그에만 남긴다.

사용자는 긴 로그를 읽지 못한다. 예외 원문에는 auto_id·경로·pid·설정 키·개발 도구
이름이 들어 있다. 화면·리포트·자동 실행 기록에는 이 파일이 만든 문구만 나간다.

문구는 두 줄이다 — **무슨 일인지 / 무엇을 하면 되는지.** 오버레이 결과 띠는 첫 줄만 보인다.

## 어떻게 고르나

1. 예외에 `user_message` 가 있으면 그것 (던지는 쪽이 이미 사람 말로 썼다)
2. 예외와 그 원인(`__cause__`) 중 **글자 규칙**(`RULES` 의 정규식)이 맞는 것
3. 클래스 규칙 (정규식 없는 것). 더 구체적인 클래스가 먼저다
4. 없으면 `common.default_explain` — 단계 이름 + 리포트를 보내 달라

클래스는 **이름으로** 맞춘다(`모듈.클래스`) — 기능 모듈을 import 하지 않는다.
"""
from __future__ import annotations

import re

from orchestrator.common import default_explain

# 09-21 검토: "가리거나 움직이지 않았는지 확인" 은 부정이 겹쳐 안 읽혔고, 무인 실행에서는
# 아무도 만지지 않았으니 할 일이 없었다. **원인 가능성 + 할 일 + 또 멈추면** 순서로 쓴다.
RETRY = ("실행 중에 ERPia 창 위에 다른 창이 뜨거나 마우스를 쓰면 이렇게 멈출 수 있습니다. "
         "다시 실행하고, 같은 곳에서 또 멈추면 [결과 보기]로 연 리포트 파일을 "
         "담당자에게 보내 주세요.")
PHONE_OFFLINE = ("휴대폰이 PC와 연결돼 있지 않아 인증 문자를 받을 수 없습니다.\n"
                 "휴대폰 화면을 켜고 PC의 [휴대폰 연결] 앱이 '연결됨'인지 확인한 뒤 "
                 "다시 실행하세요. (휴대폰 화면을 오래 꺼 두면 연결이 끊깁니다)")
# 같은 아이디 동시 로그인은 **이 PC 안에서만** 막힌다 — 다른 PC 는 원인이 아니다 (사용자 확정 09-22)
SAME_ACCOUNT = ("같은 ERPia 아이디가 이 PC에서 이미 로그인돼 있습니다.\n"
                "켜져 있는 ERPia를 모두 닫고 다시 실행하세요.")
ELEVATED = ("ERPia가 업데이트 뒤 관리자 권한으로 다시 켜져서 RPA가 다룰 수 없습니다.\n"
            "그 ERPia 로그인 창을 닫고 다시 실행하세요.")
SCREEN = "{where} 멈췄습니다 — ERPia 화면에서 필요한 버튼이나 칸을 찾지 못했습니다.\n" + RETRY


def _missing_choice(exc, match) -> str:
    """택배사·박스 이름이 목록에 없다 — **ERPia 목록에 실제로 있는 이름**을 같이 보여 준다.

    드롭다운을 끝까지 내려 본 값이 원문에 있다 (`ui.click_dropdown_item`:
    `끝까지 내려 확인한 값 N개: [...]`). 없으면 이름만 짚는다.
    """
    what, name = match.group(1), match.group(2)
    seen = re.search(r"확인한 값 \d+개: \[(.*?)\]", str(exc), re.S)
    choices = [item.strip().strip("'\"") for item in seen.group(1).split(",")
               if item.strip()] if seen else []
    # 이름 뒤에 조사를 붙이지 않는다 — 받침에 따라 이/가가 갈린다 (09-21 검토: '을(를)' 이 어색).
    text = f"ERPia 물류관리의 {what} 목록에 없는 이름입니다: '{name}'"
    if choices:
        shown = ", ".join(choices[:8]) + (f" 외 {len(choices) - 8}개" if len(choices) > 8 else "")
        text += f"\n목록에 있는 이름: {shown}"
    return text + f"\n실행 화면 [설정]의 [{what}] 칸을 목록에 있는 이름과 글자까지 똑같이 고치세요."


def _rejected(exc, _match) -> str:
    raw = (getattr(exc, "raw_text", "") or "").strip()
    return ("ERPia가 로그인을 받지 않았습니다.\n"
            + (f"ERPia 메시지: {raw}" if raw else "ERPia 화면의 안내를 확인하세요."))


# (클래스, 정규식 또는 None, 문구). 문구의 `{0}` 은 정규식 묶음, `{where}` 는 단계.
# 문구 대신 함수 `(예외, 맞은 것) -> 문구` 를 둘 수도 있다.
RULES = (
    # --- ERPia 로그인 -------------------------------------------------------
    ("automation.login.LoginRejected", r"아이디 또는 비밀번호",
     "ERPia 아이디 또는 비밀번호가 맞지 않습니다.\n"
     "실행 화면 [설정]의 [ERPia 비밀번호] 칸을 고친 뒤에 다시 실행하세요. 아이디가 "
     "바뀌었다면 담당자에게 알려 주세요. (고치지 않고 여러 번 실행하면 계정이 잠길 수 있습니다)"),
    ("automation.login.LoginError", r"화면이 잠겨",
     "화면이 잠겨 있어 진행할 수 없습니다.\n잠금을 풀어 두고 다시 실행하세요."),
    ("automation.login.LoginError", r"닫을 다른 인스턴스를 찾지 못했다", SAME_ACCOUNT),
    ("automation.login.DuplicateLogin", None, SAME_ACCOUNT),
    ("automation.login.LoginRejected", None, _rejected),
    ("automation.login.LoginError", None,
     "ERPia 로그인 화면을 처리하지 못했습니다.\n켜져 있는 ERPia를 모두 닫고 다시 실행하세요."),
    # --- ERPia 실행 --------------------------------------------------------
    ("automation.application.AlreadyRunningError", None,
     "ERPia가 이미 켜져 있습니다.\n켜져 있는 ERPia를 모두 닫고 다시 실행하세요."),
    ("automation.application.ApplicationError", r"관리자 권한", ELEVATED),
    ("automation.application.ApplicationError",
     r"실행 파일 경로가 비어|파일이 없다|\.exe 파일이 아니다",
     "ERPia 실행 파일을 찾지 못했습니다.\n프로그램을 설치해 준 담당자에게 문의하세요."),
    ("automation.application.ApplicationError", None,
     "ERPia를 실행하지 못했습니다.\n켜져 있는 ERPia를 모두 닫고 다시 실행하세요."),
    ("utils.elevation.ElevationMismatch", None, ELEVATED),
    # --- 메일 사이트 / 인증 문자 ------------------------------------------
    ("collect.webmail.LoginError", r"2차 인증",
     "메일 사이트의 2차 인증을 통과하지 못했습니다.\n"
     "휴대폰과 PC의 연결 상태를 확인하고 다시 실행하세요."),
    ("collect.webmail.LoginError", None,
     "메일 사이트에 로그인하지 못했습니다.\n"
     "실행 화면 [설정]의 [사이트 비밀번호] 칸을 확인하고 다시 실행하세요."),
    ("collect.phonelink.PhoneLinkError", r"^휴대폰이 '", PHONE_OFFLINE),
    ("collect.phonelink.PhoneLinkError", r"인증 문자가 오지 않았다",
     "인증 문자가 제시간에 오지 않았습니다.\n"
     "휴대폰이 켜져 있고 PC의 [휴대폰 연결] 앱이 '연결됨'인지 확인한 뒤 다시 실행하세요."),
    ("collect.phonelink.PhoneLinkError", r"숫자 후보가 여러",
     "인증 문자에서 인증번호를 하나로 고르지 못해 멈췄습니다 "
     "(잘못 넣으면 계정이 잠길 수 있어서입니다).\n잠시 뒤 다시 실행하세요."),
    ("collect.phonelink.PhoneLinkError", r"앱 창을 찾지 못했다",
     "PC에서 [휴대폰 연결] 앱이 켜져 있지 않습니다.\n"
     "앱을 켜고 휴대폰이 '연결됨'인지 확인한 뒤 다시 실행하세요."),
    ("collect.phonelink.PhoneLinkError", r"대화 목록이 비어|동기화 설정",
     "[휴대폰 연결] 앱에 문자가 보이지 않습니다.\n"
     "휴대폰의 [Windows와 연결] 앱에서 문자 동기화(모바일 데이터 포함)를 켜 주세요."),
    ("collect.phonelink.PhoneLinkError", None,
     "[휴대폰 연결] 앱에서 인증 문자를 읽지 못했습니다.\n"
     "앱이 '연결됨'인지 확인하고 다시 실행하세요."),
    ("collect.sms.SmsError", r"오지 않았다",
     "인증 문자가 제시간에 오지 않았습니다.\n휴대폰이 USB로 연결돼 있는지 확인하고 다시 실행하세요."),
    ("collect.sms.SmsError", None,
     "USB로 연결한 휴대폰에서 인증 문자를 읽지 못했습니다.\n"
     "케이블과 휴대폰의 [USB 디버깅] 허용을 확인하세요."),
    ("collect.auth_code.AuthCodeError", r"adb_wireless_address",
     "[인증 문자] 가 adb (무선) 인데 [무선 주소] 가 비어 있습니다.\n"
     "실행 화면 [설정]의 [무선 주소] 칸을 채우거나 다른 방법을 고르세요."),
    ("collect.auth_code.AuthCodeError", r"settings 의 \w+ 가 (?:올바르지 않다|비어 있다)",
     "인증 문자 설정이 비어 있거나 맞지 않습니다.\n"
     "실행 화면 [설정]의 [인증 문자] 칸을 확인하세요. 칸에 없는 값이면 담당자에게 문의하세요."),
    ("collect.auth_code.AuthCodeError", None,
     "인증 문자를 받을 준비가 되지 않았습니다.\n휴대폰 연결 상태를 확인하고 다시 실행하세요."),
    ("collect.webmail.SiteMissing", None,
     "이 프로그램에는 메일 사이트 연결이 아직 없습니다.\n"
     "[실행할 기능]에서 메일 엑셀 받기를 빼고 실행하거나, 프로그램을 설치해 준 담당자에게 문의하세요."),
    ("collect.browser.BrowserError", r"browser_channel",
     "메일 사이트를 열 브라우저가 정해져 있지 않습니다.\n프로그램을 설치해 준 담당자에게 문의하세요."),
    ("collect.browser.BrowserError", None,
     "메일 사이트 화면을 읽지 못했습니다.\n다시 실행하세요. 같은 문제가 계속되면 [결과 보기]로 연 리포트 파일을 "
     "담당자에게 보내 주세요."),
    ("collect.webmail.MailError", r"mail_days_back|mail_unread_only",
     "메일을 고르는 설정이 비어 있습니다.\n프로그램을 설치해 준 담당자에게 문의하세요."),
    ("collect.webmail.MailError", r"엑셀 파일이 아니다",
     "메일 첨부를 받았지만 엑셀 파일이 아니었습니다 (메일 사이트 접속이 끊겼을 수 있습니다).\n"
     "다시 실행하세요."),
    ("collect.webmail.MailError", None,
     "메일에서 엑셀을 받는 중 문제가 생겼습니다.\n"
     "다시 실행하세요. 같은 문제가 계속되면 [결과 보기]로 연 리포트 파일을 담당자에게 "
     "보내 주세요."),
    # --- 주문매핑 / 물류 --------------------------------------------------
    ("automation.order_mapping.ScreenError", r"매출처리 중 오류 팝업: (.+)",
     "매출처리 중 ERPia가 오류를 알렸습니다.\nERPia 메시지: {0}"),
    ("automation.order_mapping.ScreenError", r"주문수집이 끝나지 않았다",
     "사이트 주문 수집이 제한 시간 안에 끝나지 않았습니다.\n"
     "ERPia 화면에서 수집 상태를 확인하세요."),
    ("automation.order_mapping.ScreenError", r"엑셀 폴더가 없다",
     "엑셀 폴더를 찾지 못했습니다.\n엑셀 폴더 설정을 확인하세요."),
    ("automation.logistics.ScreenError",
     r"(택배사|박스) '([^']*)' 을\(를\) 드롭다운에서 찾지 못했다", _missing_choice),
    ("automation.logistics.ScreenError", r"(택배사|박스)가 지정되지 않았다",
     "[{0}] 칸이 비어 있습니다.\n실행 화면 [설정]에서 [{0}]를 넣고 다시 실행하세요."),
    # 기본값 없음 (10-02) — 빈 설정으로 멈춘 것은 화면 문제가 아니다
    ("automation.order_mapping.ScreenError", r"매출처리 방식이 비어",
     "[매출처리] 방식이 정해져 있지 않습니다.\n실행 화면 [설정]의 [매출처리]에서 고르고 다시 실행하세요."),
    ("automation.logistics.ScreenError", r"자동/수동이 비어",
     "물류관리 [자동/수동] 이 정해져 있지 않습니다.\n실행 화면 [설정]에서 고르고 다시 실행하세요."),
    ("config.settings.SettingsNotConfigured", None,
     "설정 값이 비어 있어 시작하지 못했습니다.\n실행 화면 [설정]의 빈 칸을 채우세요. 칸에 없는 값이면 담당자에게 문의하세요."),
    ("automation.order_mapping.ScreenError", None, SCREEN),
    ("automation.logistics.ScreenError", None, SCREEN),
    ("automation.logistics_wait.ScreenError", None, SCREEN),
    ("utils.ui.ControlNotFound", None, SCREEN),
    ("utils.ui.ControlDisabled", None, SCREEN),
    ("utils.dialogs.DialogNotFound", None, SCREEN),
    ("utils.filedialog.FileDialogError", None, SCREEN),
    ("utils.wait.WaitTimeout", None,
     "{where} 멈췄습니다 — ERPia가 제시간에 반응하지 않았습니다.\n" + RETRY),
    ("utils.secret.SecretError", None,
     "저장된 비밀번호를 풀지 못했습니다.\n비밀번호 칸에 다시 넣고 실행하세요."),
)


def _chain(exc: BaseException) -> list[BaseException]:
    """예외와 그 원인들. `AuthCodeError(str(e)) from e` 처럼 감싼 것의 안쪽까지 본다."""
    # `__context__`(처리 중에 우연히 겹친 예외)는 보지 않는다. `from` 으로 감싼 것만 원인이다.
    out: list[BaseException] = []
    while exc is not None and exc not in out and len(out) < 5:
        out.append(exc)
        exc = exc.__cause__
    return out


def _keys(exc: BaseException) -> list[str]:
    return [f"{cls.__module__}.{cls.__name__}" for cls in type(exc).__mro__]


def _render(message, exc, match, where: str) -> str:
    if callable(message):
        return message(exc, match)
    groups = match.groups() if match else ()
    return message.format(*groups, where=where)


def explain(exc: BaseException, step=None) -> str:
    """`Hooks.explain` 에 넣는 함수. 규칙이 없으면 `default_explain`."""
    told = getattr(exc, "user_message", "")
    if told:
        return told
    # `물류대기 중에` 가 "물류가 대기하는 중에" 로 읽혔다 (09-21 2차 검토).
    where = f"[{step.name}] 단계에서" if step is not None else "실행 중에"
    chain = _chain(exc)
    for patterned in (True, False):
        for item in chain:
            text = str(item)
            for key in _keys(item):
                for name, pattern, message in RULES:
                    if name != key or (pattern is not None) != patterned:
                        continue
                    match = re.search(pattern, text) if pattern else None
                    if pattern and not match:
                        continue
                    return _render(message, item, match, where)
    return default_explain(exc, step)


# --- 엑셀 한 건의 실패 사유 --------------------------------------------------
# 업로드 실패는 흐름을 멈추지 않고 표에 사유만 남긴다(`upload_excels`). 그 사유도 사람 말로.
UPLOAD_RULES = (
    (r"버튼이 없다",
     "ERPia 사이트 목록에서 이 사이트에 [엑셀업로드] 버튼이 없습니다. "
     "ERPia에서 이 사이트의 엑셀수집 설정을 확인하세요."),
    (r"파일 선택 창",
     "[엑셀업로드]를 눌렀지만 파일 고르는 창이 뜨지 않았습니다. "
     "ERPia에서 이 사이트의 엑셀수집 설정을 확인하세요."),
    (r"비밀번호가 걸려",
     "엑셀에 비밀번호가 걸려 있는데 등록된 비밀번호가 없습니다. "
     "이 사이트의 엑셀 비밀번호를 담당자에게 알려 등록해 달라고 하세요."),
    (r"인 행을 찾지 못했다",
     "ERPia 사이트 목록에 이 사이트 이름이 없습니다. "
     "엑셀 파일 이름의 사이트명(첫 밑줄 앞)이 ERPia와 똑같은지 확인하세요."),
    (r"내용: (.+)", "ERPia가 이 엑셀을 받지 않았습니다. ERPia 메시지: {0}"),
    (r"엑셀 파일이 없다", "엑셀 파일이 없어졌습니다. 받은 폴더를 확인하세요."),
    (r"엑셀 파일이 아니다", "엑셀 파일이 아닙니다."),
)


def upload_reason(error: str) -> str:
    """엑셀 업로드 한 건의 실패 원문 → 사람 말. 빈 값이면 빈 문자열."""
    if not error:
        return ""
    for pattern, message in UPLOAD_RULES:
        match = re.search(pattern, error)
        if match:
            return message.format(*match.groups())
    return "올리는 중 문제가 생겼습니다. 엑셀 파일을 열어 내용을 확인하세요."
