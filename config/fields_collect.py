r"""메일 수집 기능의 설정 항목.

기능별로 파일을 나눈다. 배포본에는 그 기능의 항목만 들어간다
(`build_*.spec` 의 `FORBIDDEN`). 설정 이름 자체가 흔적이 되기 때문이다.

값의 뜻은 `docs/SETTINGS.md` 에 있다. ★ **기본값이 없다** (10-02 사용자 결정) — 고를 값은 비어 있고, 비면 그 기능을
시작하지 않는다. 남긴 값은 안전용 꺼짐(창을 띄운다)과 '비우면 조건 없음' 목록뿐이다.
"""
from __future__ import annotations

DEFAULTS: dict[str, object] = {
    # --- 메일 사이트 (사이트 파일은 git 밖 — collect/sites/) ---
    "mail_url": None,
    "mail_user_id": None,
    "mail_password": None,          # SECRET_KEYS — 저장 때 dpapi:/baked: 로 감싼다 (utils/secret.py)
    "mail_senders": [],             # 발신자 주소 완전일치 목록. 비우면 발신자를 보지 않는다
    "mail_sites": [],               # 제목 대괄호 안 이름 완전일치 목록. 비우면 대괄호만 있으면 대상
    "mail_unread_only": None,       # True = 읽지 않은 메일만
    "mail_days_back": None,         # 며칠 전까지. -1 = 날짜 조건 없음
    "download_base_dir": None,

    # --- 브라우저 ---
    "browser_channel": None,        # collect/browser.CHANNELS
    "browser_headless": False,      # 안전용 꺼짐 — 창을 띄운다 (2차 인증이 막히면 사람이 봐야 한다)

    # --- 인증 문자 ---
    "phone_os": None,               # android / ios
    "sms_source": None,             # phonelink / adb / auto
    "adb_connection": None,         # usb / wireless (sms_source 가 adb·auto 일 때만 본다)
    "sms_keyword": None,            # 인증 문자를 고르는 말 (사이트마다 다르다)
    # 인증 문자 발신번호 **목록**. 완전일치로 비교한다(하이픈·공백은 무시).
    # **비우면 발신자 조건을 걸지 않는다.** 여러 사이트의 인증을 쓰게 되면 번호를 다 모으기 어려우므로
    # 비워 두고 시각+키워드로 거를 수 있다.
    "sms_senders": [],
    "adb_path": None,
    "adb_wireless_address": None,
}
