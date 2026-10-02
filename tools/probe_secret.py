r"""[조사 도구 - 읽기 전용] 비밀번호 감싸기/풀기를 확인한다.

**실계정 값을 건드리지 않는다.** 가짜 문자열만 감쌌다 푼다.
설정 파일도 읽지 않는다 — 감싼 모양만 본다.

    .venv\Scripts\python.exe -m tools.probe_secret

## 왜 이 도구가 있나 (2026-09-17)

`config/settings.local.json` 에 비밀번호가 평문으로 들어갔다. 배포본은 사람이
화면에서 고친 값을 **exe 옆에 그대로 저장**하므로, 한 번 돌리면 그 폴더에
실계정 비밀번호가 생긴다. 그 폴더를 복사하면 비밀번호가 함께 간다.

여기서 확인하는 것은 셋이다.

1. 감싼 값이 **원래 글자로 정확히 되돌아오는가** (되돌아오지 않으면 로그인 불가)
2. 파일에 **평문이 남지 않는가**
3. **풀지 못할 때 조용히 넘어가지 않는가**

★ DPAPI 로 감싼 값이 **다른 PC 에서 풀리지 않는 것**은 여기서 확인할 수 없다.
  이 PC 한 대뿐이기 때문이다. 그건 설계상 그렇다는 것까지만 말할 수 있다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        # 콘솔이 없는 환경. 출력 인코딩에만 영향이 있다.
        continue

from utils import secret  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

log = get_logger(__name__)

# 가짜 값들. **실계정 비밀번호를 쓰지 않는다.**
SAMPLES = (
    "simple123",
    "한글비밀번호",
    "!@#$%^&*()_+-=[]{}|;:,.<>?",
    "a" * 200,
    "공백 있는 값",
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  %s  %s%s", "통과" if ok else "실패", name,
             f" - {detail}" if detail else "")
    return ok


def _check_seed() -> list[bool]:
    """구운 값 씨앗은 git 밖(.env)에 있다 (10-02). 코드에 박힌 씨앗이 없고, 없으면 조용히 넘어가지 않는다."""
    import inspect
    import tempfile

    from utils import envfile

    log.info("▶ 구운 값 씨앗은 코드에 없다")
    source = inspect.getsource(secret)
    out = [check("코드에 씨앗 상수가 없다 (_BAKED_SEED·b\"...\" 씨앗)",
                 "_BAKED_SEED" not in source and "RPA-baked-settings" not in source)]
    keep = envfile.ENV_PATH
    try:
        envfile.ENV_PATH = Path(tempfile.mkdtemp()) / ".env"          # 없는 .env
        try:
            secret.wrap_baked("x")
            out.append(check("씨앗이 없으면 SecretError (빈 씨앗으로 감싸지 않는다)", False))
        except secret.SecretError as exc:
            out.append(check("씨앗이 없으면 SecretError (빈 씨앗으로 감싸지 않는다)", ".env" in str(exc)))
        envfile.ENV_PATH.write_text("# 주석\nBAKED_SEED='probe-seed'\n", encoding="utf-8")
        packed = secret.wrap_baked("값")
        out.append(check(".env 의 씨앗으로 감싸고 푼다 (따옴표·주석 줄)",
                         secret.seed() == b"probe-seed" and secret.unwrap(packed) == "값"))
        envfile.ENV_PATH.write_text("BAKED_SEED=other-seed\n", encoding="utf-8")
        try:
            secret.unwrap(packed)
            out.append(check("씨앗이 다르면 풀리지 않는다 (서명 불일치)", False))
        except secret.SecretError:
            out.append(check("씨앗이 다르면 풀리지 않는다 (서명 불일치)", True))
    finally:
        envfile.ENV_PATH = keep
    return out


def main() -> int:
    setup_logging()
    results: list[bool] = []
    results += _check_seed()
    # 아래는 가짜 씨앗으로 — 이 PC 의 .env 가 없어도 돈다
    secret.seed = lambda: b"probe-seed"

    log.info("▶ 이 환경에서 DPAPI 를 쓸 수 있나")
    usable = secret.dpapi_available()
    results.append(check("DPAPI 를 부를 수 있다", usable))
    if not usable:
        log.warning("DPAPI 를 쓸 수 없다. 이 PC 에서는 **평문으로 저장된다.**")

    log.info("▶ 감싼 값이 **원래 글자로 정확히** 돌아온다")
    for text in SAMPLES:
        shown = text if len(text) <= 20 else f"{text[:17]}... ({len(text)}자)"
        for label, wrap in (("이 PC 전용(dpapi)", secret.wrap_local),
                            ("구운 값(baked)", secret.wrap_baked)):
            packed = wrap(text)
            back = secret.unwrap(packed)
            results.append(check(f"{label} — {shown}", back == text,
                                 "" if back == text else f"{back!r} 로 돌아왔다"))

    log.info("▶ 감싼 값에 **원래 글자가 남지 않는다**")
    for text in ("simple123", "한글비밀번호"):
        for label, wrap in (("dpapi", secret.wrap_local),
                            ("baked", secret.wrap_baked)):
            packed = wrap(text)
            results.append(check(f"{label} 안에 원문이 없다",
                                 text not in packed, packed[:40] + "..."))

    log.info("▶ 빈 값과 이미 감싼 값은 건드리지 않는다")
    results.append(check("빈 문자열은 그대로", secret.wrap_local("") == ""))
    once = secret.wrap_baked("값")
    results.append(check("두 번 감싸지 않는다", secret.wrap_baked(once) == once))
    results.append(check("평문은 그대로 돌려준다", secret.unwrap("평문") == "평문"))
    results.append(check("문자열이 아니면 그대로",
                         secret.unwrap(["목록"]) == ["목록"]))

    log.info("▶ ★ 망가진 값은 **조용히 넘어가지 않는다**")
    broken = secret.wrap_baked("원래값")
    # 가운데 한 글자를 바꾼다. 서명이 깨져야 한다.
    tampered = broken[:-5] + ("A" if broken[-5] != "A" else "B") + broken[-4:]
    try:
        secret.unwrap(tampered)
        results.append(check("바꿔치기를 잡는다", False, "예외 없이 통과했다"))
    except secret.SecretError as exc:
        results.append(check("★ 바꿔치기를 잡는다", True, str(exc)[:50]))
    try:
        secret.unwrap(secret.BAKED_PREFIX + "QQ==")
        results.append(check("너무 짧은 값을 잡는다", False, "예외 없이 통과했다"))
    except secret.SecretError as exc:
        results.append(check("너무 짧은 값을 잡는다", True, str(exc)[:40]))

    log.info("▶ 감싼 것과 안 감싼 것을 구분한다")
    results.append(check("감싼 값을 알아본다",
                         secret.is_wrapped(secret.wrap_baked("x"))))
    results.append(check("평문을 감싼 값으로 보지 않는다",
                         not secret.is_wrapped("그냥 글자")))

    log.info("▶ 로그에 값을 남기지 않는다")
    results.append(check("평문은 길이만 알린다",
                         "1234" not in secret.mask("1234"), secret.mask("1234")))
    results.append(check("감싼 값은 감쌌다고만 말한다",
                         secret.mask(secret.wrap_baked("x")) == "(감싼 값)"))
    results.append(check("빈 값을 알린다", secret.mask("") == "(비어 있다)"))

    results += _check_settings_wiring()

    log.info("결과: 통과 %d / 실패 %d", sum(results), len(results) - sum(results))
    log.info("★ 미검증: DPAPI 값이 **다른 PC 에서 풀리지 않는 것**은 이 PC "
             "한 대로는 확인할 수 없다. 설계상 그렇다는 것까지다.")
    return 0 if all(results) else 1


def _check_settings_wiring() -> list[bool]:
    """설정 쪽 배선 — **비밀 목록에 빠진 키가 없는가.**

    새 비밀 항목을 만들고 `SECRET_KEYS` 에 넣는 것을 잊으면 **조용히 평문으로
    저장된다.** 이름에 `password` 가 든 항목을 전부 훑어 대조한다.
    """
    from config import settings as S

    out: list[bool] = []
    log.info("▶ 설정 배선")
    looks_secret = {name for name in S.DEFAULTS
                    if "password" in name or "secret" in name}
    listed = set(S.SECRET_KEYS) | set(S.SECRET_MAP_KEYS)
    missed = sorted(looks_secret - listed)
    out.append(check("★ 비밀로 보이는 키가 **목록에 다 있다**", not missed,
                     f"빠진 것: {missed}" if missed else
                     " / ".join(sorted(listed))))

    # 실제로 감싸지는지 — 파일을 건드리지 않고 함수만 부른다.
    packed = S._wrap_value("login_password", "가짜비밀번호")
    out.append(check("비밀 키는 감싸서 저장한다", secret.is_wrapped(packed),
                     secret.mask(packed)))
    plain = S._wrap_value("delivery_company", "택배사A")
    out.append(check("비밀이 아닌 키는 그대로 저장한다", plain == "택배사A"))
    mapped = S._wrap_value("excel_passwords", {"사이트": "9876"})
    out.append(check("딕셔너리 안쪽 값도 감싼다",
                     secret.is_wrapped(mapped["사이트"])))

    # 읽는 쪽에서 되돌아오는가
    back = S._unwrap_value("login_password", packed, "시험")
    out.append(check("읽을 때 원래 값으로 돌아온다", back == "가짜비밀번호"))
    back_map = S._unwrap_value("excel_passwords", mapped, "시험")
    out.append(check("딕셔너리도 되돌아온다", back_map == {"사이트": "9876"}))

    # ★ 못 푸는 값이 와도 프로그램이 죽지 않고, **빈 값**이 되어 잡힌다
    bad = S._unwrap_value("login_password",
                          secret.BAKED_PREFIX + "QQ==", "시험")
    out.append(check("★ 못 푼 값은 **비운다** (감싼 문자열을 입력하지 않는다)",
                     bad == "", repr(bad)))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
