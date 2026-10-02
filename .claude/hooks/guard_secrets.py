"""PreToolUse 훅 (Write / Edit): **비밀 키가 코드·문서 파일에 들어가는 것**을 막는다 (2026-09-22).

서버 연동(`docs/SERVER_PLAN.md`)으로 생기는 비밀 — Supabase service_role 키, `sb_secret_` 키,
메일 API 키, 기기 키 — 는 `config/settings.local.json` 에만 둔다. 그 밖의 파일에 쓰려 하면 exit 2 로 막는다.
anon 키(`sb_publishable_`, role 이 anon 인 JWT)는 공개 키라 막지 않는다 — JWT 는 가운데 조각을 풀어 role 을 본다.
"""
import base64
import json
import re
import sys

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass  # 재설정 불가한 스트림. 검사 자체는 계속한다.

# 비밀을 둬도 되는 유일한 파일
ALLOWED = re.compile(r"(?:^|[\/])config[\/]settings\.local\.json$")

# (정규식, 이름) — 맞으면 무조건 막는다
PATTERNS = [
    (re.compile(r"\bsb_secret_[A-Za-z0-9_-]{10,}"), "Supabase secret 키"),
    (re.compile(r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9]{20,}\b"), "Resend API 키"),
    (re.compile(r"\brpa_[A-Za-z0-9+/=_-]{40,}"), "기기 키"),
    (re.compile(r"\benr_[A-Za-z0-9+/=_-]{40,}"), "업체 등록 토큰"),
    (re.compile(r"\bllmw_[A-Za-z0-9_-]{40,}"), "LLM 워커 키"),
]
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.([A-Za-z0-9_-]{10,})\.[A-Za-z0-9_-]{10,}")


def jwt_role(payload_b64: str) -> str:
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return ""
    return str(payload.get("role", "")) if isinstance(payload, dict) else ""


def findings(text: str) -> list[str]:
    found = [name for pattern, name in PATTERNS if pattern.search(text)]
    for match in JWT.finditer(text):
        if jwt_role(match.group(1)) == "service_role":
            found.append("service_role JWT")
            break
    return found


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path", ""))
    # Windows 도구 입력은 역슬래시 경로다 — / 로 맞춘 뒤 본다 (10-02 검토. 역슬래시는 chr 로 — 파일에 쓰면 한 겹 먹힌다)
    if ALLOWED.search(path.replace(chr(0x5C), "/")):
        return 0
    text = str(tool_input.get("content", "")) + "\n" + str(tool_input.get("new_string", ""))
    found = findings(text)
    if not found:
        return 0
    print(
        f"[비밀 키 차단] {', '.join(found)} 가 파일에 들어가려 한다: {path}\n"
        "비밀은 config/settings.local.json 에만 둔다. 코드·문서·웹 페이지에는 anon(공개) 키만 쓴다.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[비밀 키 훅 오류] {type(exc).__name__}: {exc} — 안전을 위해 막는다.", file=sys.stderr)
        sys.exit(2)
