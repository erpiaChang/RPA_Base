r"""[개발 전용] 빌드를 서버에 등록하고 **빌드 ID** 를 받는다 (2026-09-28).

빌드 프로그램(`gui/build_app.py`)이 [저장하고 빌드] 때 부른다. 관리자가 대시보드에서 토큰을
발급해 복사해 붙이던 단계를 없앤 것이다 (사용자 확정 09-28).

    관리자 계정으로 로그인 → `register_build(업체명, 빌드명)` → `bld_...` → 설정에 넣고 굽는다

받은 빌드 ID 는 그 빌드가 **처음 보고할 때** 서버가 그 PC 에 묶는다. 다른 PC 로 복사하면 401 이라
실행 창이 잠긴다 (`server/schema.sql` 의 `ingest`).

배포본에는 들어가지 않는다 (`build_*.spec` 의 `DEV_ONLY` 가 `tools` 를 통째로 뺀다) — 사용자 exe 에
관리자 계정이 흘러들면 안 된다. 서버에 쓰기를 하므로 **대상 프로그램은 건드리지 않는다.**
"""
from __future__ import annotations

import difflib
import json
import re
from urllib import error, request

TIMEOUT = 15.0
TOKEN_PATH = "/auth/v1/token?grant_type=password"
RPC_PATH = "/rest/v1/rpc/register_build"
ACCOUNTS_LIMIT = 1000
ACCOUNTS_PATH = f"/rest/v1/accounts?select=name&order=name&limit={ACCOUNTS_LIMIT}"   # 관리자는 RLS 로 전부 읽는다
SIMILAR_CUTOFF = 0.6        # 이 이상 겹치면 '비슷한 이름' 으로 보여 준다
SIMILAR_MAX = 5


class RegisterError(Exception):
    """빌드 등록 실패. 메시지는 빌드 프로그램 화면에 그대로 보인다."""


def _post(url: str, headers: dict[str, str], payload: dict) -> tuple[int, str]:
    req = request.Request(url, method="POST",
                          data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", **headers})
    try:
        with request.urlopen(req, timeout=TIMEOUT) as resp:     # noqa: S310 — https 고정
            return resp.status, resp.read(4000).decode("utf-8", "replace")
    except error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:                                    # noqa: BLE001 — 네트워크·타임아웃
        raise RegisterError(f"서버에 연결하지 못했습니다 ({type(exc).__name__}). "
                            "인터넷 연결과 서버 URL 을 확인하세요.") from exc


def _get(url: str, headers: dict[str, str]) -> tuple[int, str]:
    req = request.Request(url, method="GET", headers=headers)
    try:
        with request.urlopen(req, timeout=TIMEOUT) as resp:     # noqa: S310 — https 고정
            return resp.status, resp.read(200000).decode("utf-8", "replace")
    except error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:                                    # noqa: BLE001 — 네트워크·타임아웃
        raise RegisterError(f"서버에 연결하지 못했습니다 ({type(exc).__name__}). "
                            "인터넷 연결과 서버 URL 을 확인하세요.") from exc


def list_accounts(url: str, anon: str, token: str) -> list[str]:
    """서버의 업체 이름 전부 (관리자 토큰). 못 읽으면 `RegisterError` — 확인 없이 새 업체를 만들지 않는다."""
    status, text = _get(url + ACCOUNTS_PATH, {"apikey": anon, "Authorization": f"Bearer {token}"})
    if not 200 <= status < 300:
        raise RegisterError(f"업체 목록을 읽지 못해 등록하지 않았습니다 ({status}).")
    try:
        names = [str(row["name"]) for row in json.loads(text)]
    except (ValueError, KeyError, TypeError) as exc:
        raise RegisterError("업체 목록 응답을 읽지 못해 등록하지 않았습니다.") from exc
    if len(names) >= ACCOUNTS_LIMIT:
        raise RegisterError("업체가 너무 많아 이름을 확인할 수 없어 등록하지 않았습니다.")
    return names


def _norm(name: str) -> str:
    return re.sub(r"\(주\)|㈜|주식회사|\s+", "", name).casefold()


def similar_names(name: str, existing: list[str]) -> list[str]:
    """공백·대소문자·(주) 만 다르거나 글자가 많이 겹치는 이름 (가까운 순, 최대 `SIMILAR_MAX`)."""
    key = _norm(name)
    scored = sorted(((difflib.SequenceMatcher(None, key, _norm(other)).ratio(), other)
                     for other in existing if other != name), reverse=True)
    return [other for ratio, other in scored if ratio >= SIMILAR_CUTOFF][:SIMILAR_MAX]


def _login(url: str, anon: str, email: str, password: str) -> str:
    status, text = _post(url + TOKEN_PATH, {"apikey": anon},
                         {"email": email, "password": password})
    if status == 400:
        raise RegisterError("관리자 이메일 또는 비밀번호가 맞지 않습니다.")
    if not 200 <= status < 300:
        raise RegisterError(f"로그인하지 못했습니다 ({status}).")
    try:
        token = str(json.loads(text)["access_token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RegisterError("로그인 응답을 읽지 못했습니다.") from exc
    return token


def register(url: str, anon: str, email: str, password: str,
             account_name: str, build_name: str, *, post=None, get=None, confirm_new=None) -> str:
    """빌드를 등록하고 `bld_...` 를 돌려준다. 실패하면 `RegisterError`.

    ★ 서버에 없는 업체 이름이면 `confirm_new(이름, 비슷한 이름들)` 이 True 일 때만 새 업체가 된다 (10-07) —
      서버 `register_build` 는 이름이 한 글자라도 다르면 조용히 새 업체를 만든다. 콜백이 없으면 거절한다.
    `post`·`get` 은 확인 도구가 가짜를 준다 — 실제 서버를 부르지 않고 시험할 수 있다.
    """
    global _post, _get                              # noqa: PLW0603 — 확인 도구가 갈아 끼운다
    if post is not None or get is not None:
        original = _post, _get
        _post, _get = post or _post, get or _get
        try:
            return register(url, anon, email, password, account_name, build_name, confirm_new=confirm_new)
        finally:
            _post, _get = original

    url = url.strip().rstrip("/")
    for name, value in (("서버 URL", url), ("공개(anon) 키", anon.strip()),
                        ("관리자 이메일", email.strip()), ("관리자 비밀번호", password),
                        ("업체 이름", account_name.strip()), ("빌드 이름", build_name.strip())):
        if not value:
            raise RegisterError(f"{name} 이(가) 비어 있습니다. 7절을 채우세요.")

    account = account_name.strip()
    token = _login(url, anon.strip(), email.strip(), password)
    names = list_accounts(url, anon.strip(), token)
    if account not in names and (confirm_new is None or not confirm_new(account, similar_names(account, names))):
        raise RegisterError(f"'{account}' 은(는) 서버에 없는 업체 이름이라 등록하지 않았습니다. "
                            "기존 업체 이름과 똑같이 쓰거나, 새 업체면 확인 창에서 [예] 를 누르세요.")
    status, text = _post(url + RPC_PATH,
                         {"apikey": anon.strip(), "Authorization": f"Bearer {token}"},
                         {"account_name": account, "build_name": build_name.strip()})
    if status in (401, 403):
        raise RegisterError("이 계정은 빌드를 등록할 수 없습니다 (관리자 권한이 필요합니다).")
    if not 200 <= status < 300:
        raise RegisterError(f"서버가 빌드 등록을 받지 않았습니다 ({status}).")
    build_id = text.strip().strip('"')
    if not build_id.startswith("bld_"):
        raise RegisterError("서버 응답을 읽지 못했습니다.")
    return build_id
