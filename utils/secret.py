r"""설정 파일에 **비밀번호를 평문으로 두지 않는다.**

## 무엇이 문제였나

`config/settings.local.json` 에 비밀번호가 평문으로 들어간다. 그리고 배포본은
사람이 화면에서 고친 값을 **exe 옆에 그대로 저장**하므로, 한 번 돌리면 그
폴더에 실계정 비밀번호가 생긴다. 그 폴더를 복사하면 비밀번호가 함께 간다.

## ★ 두 가지를 **다른 방법**으로 감싼다

같은 방법으로 덮으면 하나가 나빠진다. 요구가 다르기 때문이다.

| 무엇 | 어디서 풀려야 하나 | 방법 |
| --- | --- | --- |
| 사람이 화면에서 넣어 저장한 값 | **그 PC, 그 Windows 계정에서만** | **DPAPI** |
| 빌드에 구워 넣는 값 | 받은 사람의 **다른 PC에서도** | 프로그램 안의 키 |

### DPAPI (`dpapi:`) — 로컬에 저장되는 값

Windows 가 **로그인한 계정에 묶어** 암호화한다. 키가 우리 프로그램 안에 없다.
그래서 `settings.local.json` 을 통째로 복사해 가도 **다른 PC·다른 계정에서는
풀리지 않는다.** 이것이 실제로 막고 싶었던 일이다.

`ctypes` 로 `crypt32.dll` 을 부른다 — 새 패키지가 필요 없다.

### 구운 값 (`baked:`) — 배포본에 들어가는 값

받는 사람의 PC 에서 풀려야 하므로 **키가 프로그램 안에 있어야 한다.**
그러면 프로그램을 뜯는 사람은 결국 읽을 수 있다.

★ **그래서 이것은 "못 읽게" 가 아니라 "쉽게는 못 읽게" 다.** 사용자도 그렇게
  알고 있다(`docs/archive/PROGRESS_20260917.md` 7단계). 막는 것은 **설정 파일을 열어 보는 것**
  이지 프로그램을 분석하는 것이 아니다.

표준 라이브러리만 쓴다.

- 키 유도: `PBKDF2-HMAC-SHA256` (`hashlib`)
- 암호화: HMAC-SHA256 키스트림 XOR (CTR 방식)
- 무결성: 암호문에 HMAC 을 붙인다 (Encrypt-then-MAC)

새 암호를 만든 것이 아니라 **표준 조각을 표준 순서로** 엮은 것이다.
`cryptography` 를 넣지 않은 이유: 키가 프로그램 안에 있는 이상 강도가 같고,
네이티브 확장이라 배포본이 커진다.

## ★ 실패하면 **조용히 넘어가지 않는다**

풀지 못한 값을 빈 값으로 두면 프로그램이 "설정이 비었다" 며 엉뚱한 곳에서
멈춘다. 그래서 `SecretError` 를 올리고, 부르는 쪽이 **무엇을 못 풀었는지**
사람에게 보여 준다.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import logging
import os
import sys
from ctypes import wintypes
from pathlib import Path

from utils import envfile

# ★ **프로젝트 로거를 쓰지 않는다.** `utils.logger` 는 `config.settings` 를
#   읽고, 그 설정은 다시 이 파일을 불러 비밀을 푼다 — 순환이 된다.
#   표준 로거면 충분하다(설정이 붙기 전에도 동작한다).
log = logging.getLogger(__name__)

# 감싼 값 앞에 붙는 표. 이 표가 없으면 평문으로 본다(옛 설정과 섞여도 된다).
DPAPI_PREFIX = "dpapi:"
BAKED_PREFIX = "baked:"
PREFIXES = (DPAPI_PREFIX, BAKED_PREFIX)

# 구운 값을 감쌀 때 쓰는 씨앗 — **git 에 두지 않는다** (10-02 사용자 결정). 개발 PC 는 `.env` 의 BAKED_SEED,
# 빌드본은 번들 안 `config/baked.key` (`tools/bake_settings` 가 굽는다). 번들 안에 있다는 것이 한계다 — "쉽게는 못 읽게".
SEED_ENV = "BAKED_SEED"
SEED_FILE = "baked.key"
_KDF_ROUNDS = 200_000
_SALT_LEN = 16
_MAC_LEN = 32


class SecretError(RuntimeError):
    """감싸거나 푸는 데 실패했다. **조용히 넘어가지 않는다.**"""


# --- DPAPI -----------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _free(blob: _Blob) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def dpapi_available() -> bool:
    """이 환경에서 DPAPI 를 쓸 수 있는가. 못 쓰면 평문으로 둘 수밖에 없다."""
    try:
        return bool(ctypes.windll.crypt32)
    except (AttributeError, OSError):
        return False


def _dpapi_wrap(text: str) -> str:
    out = _Blob()
    source = _blob(text.encode("utf-8"))
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1 — 창을 띄우지 않는다(무인 실행이 있다)
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise SecretError(
            f"DPAPI 로 감싸지 못했다 (오류 {ctypes.GetLastError()})")
    try:
        return DPAPI_PREFIX + base64.b64encode(_blob_bytes(out)).decode("ascii")
    finally:
        _free(out)


def _dpapi_unwrap(packed: str) -> str:
    raw = base64.b64decode(packed[len(DPAPI_PREFIX):])
    out = _Blob()
    source = _blob(raw)
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise SecretError(
            "DPAPI 로 풀지 못했다. **다른 PC 나 다른 Windows 계정에서 만든 "
            "값**일 수 있다 — 그 경우 이 값을 화면에서 다시 입력해야 한다 "
            f"(오류 {ctypes.GetLastError()})")
    try:
        return _blob_bytes(out).decode("utf-8")
    finally:
        _free(out)


# --- 구운 값 ---------------------------------------------------------------
def seed() -> bytes:
    """구운 값 씨앗. 빌드본은 번들 안 파일, 개발 PC 는 `.env`. 없으면 **조용히 넘어가지 않는다**."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        try:
            value = (Path(bundle) / "config" / SEED_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value.encode("utf-8")
    value = envfile.read().get(SEED_ENV, "")
    if not value:
        raise SecretError(f"구운 값을 감쌀 씨앗이 없다 — 프로젝트의 .env 에 {SEED_ENV} 를 넣는다 (꼴은 .env.example)")
    return value.encode("utf-8")


def _keys(salt: bytes) -> tuple[bytes, bytes]:
    """암호화 키와 무결성 키를 따로 뽑는다. 하나를 두 곳에 쓰지 않는다."""
    material = hashlib.pbkdf2_hmac("sha256", seed(), salt, _KDF_ROUNDS, 64)
    return material[:32], material[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 을 카운터 모드로 돌려 만든 키스트림."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"),
                         hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _baked_wrap(text: str) -> str:
    salt = os.urandom(_SALT_LEN)
    enc_key, mac_key = _keys(salt)
    body = text.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(body, _keystream(enc_key, salt,
                                                          len(body))))
    # Encrypt-then-MAC. 소금까지 함께 서명해야 바꿔치기를 막는다.
    tag = hmac.new(mac_key, salt + cipher, hashlib.sha256).digest()
    return BAKED_PREFIX + base64.b64encode(salt + cipher + tag).decode("ascii")


def _baked_unwrap(packed: str) -> str:
    raw = base64.b64decode(packed[len(BAKED_PREFIX):])
    if len(raw) < _SALT_LEN + _MAC_LEN:
        raise SecretError("구운 값이 너무 짧다. 파일이 깨졌을 수 있다.")
    salt, cipher, tag = (raw[:_SALT_LEN], raw[_SALT_LEN:-_MAC_LEN],
                         raw[-_MAC_LEN:])
    enc_key, mac_key = _keys(salt)
    if not hmac.compare_digest(
            tag, hmac.new(mac_key, salt + cipher, hashlib.sha256).digest()):
        raise SecretError("구운 값의 서명이 맞지 않다. 파일이 바뀌었거나 "
                          "다른 버전에서 만든 값이다.")
    body = bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, salt,
                                                          len(cipher))))
    return body.decode("utf-8")


# --- 바깥에서 쓰는 것 -------------------------------------------------------
def is_wrapped(value: object) -> bool:
    """이미 감싼 값인가. 평문과 섞여 있어도 구분된다."""
    return isinstance(value, str) and value.startswith(PREFIXES)


def wrap_local(text: str) -> str:
    """이 PC 에 저장할 값을 감싼다. **그 PC, 그 계정에서만 풀린다.**

    DPAPI 를 쓸 수 없는 환경이면 **평문 그대로 돌려주고 경고한다** — 여기서
    막으면 프로그램이 아예 못 돈다. 감싸지 못한 것은 사람이 알아야 한다.
    """
    if not text:
        return text
    if is_wrapped(text):
        return text
    if not dpapi_available():
        log.warning("DPAPI 를 쓸 수 없어 **비밀번호를 평문으로 저장한다.** "
                    "이 폴더를 복사하면 비밀번호가 함께 간다.")
        return text
    return _dpapi_wrap(text)


def wrap_baked(text: str) -> str:
    """빌드에 구워 넣을 값을 감싼다. **다른 PC 에서도 풀린다** (그래서 약하다)."""
    if not text or is_wrapped(text):
        return text
    return _baked_wrap(text)


def unwrap(value: object) -> object:
    """감싼 값을 푼다. 평문이면 그대로 돌려준다.

    ★ 풀지 못하면 **예외를 올린다.** 빈 값으로 바꾸면 프로그램이 "설정이
      비었다" 며 엉뚱한 곳에서 멈추고, 사람은 원인을 못 찾는다.
    """
    if not isinstance(value, str):
        return value
    if value.startswith(DPAPI_PREFIX):
        return _dpapi_unwrap(value)
    if value.startswith(BAKED_PREFIX):
        return _baked_unwrap(value)
    return value


def mask(text: object) -> str:
    """로그에 남길 꼴. **값을 남기지 않는다.**"""
    if not isinstance(text, str) or not text:
        return "(비어 있다)"
    if is_wrapped(text):
        return "(감싼 값)"
    return "*" * len(text) + " (평문)"
