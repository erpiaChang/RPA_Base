r"""[확인 도구 - 읽기 전용] git 에 올라가면 안 되는 값이 있는지 본다 (10-02 사용자 요청).

찾을 값은 **사람이 적지 않는다** — 그때그때 git 밖에서 뽑고, 값은 찍지 않는다.
  config/settings.local.json (dist/*/config 도)  모든 키의 값. SAFE_KEYS(프로그램 낱말)·참거짓·숫자만 뺀다 — 새 키는 저절로 본다.
                                                 경로는 폴더 이름까지, 주소는 호스트·도메인(공용 호스트면 프로젝트 id)까지
  .env                                           모든 값
  git 밖 사이트·업체                             collect/sites/local 의 MailSite id·name·주소, docs/customers 파일 이름
  이 PC                                          컴퓨터 이름·Windows 사용자·git 전역 이름·메일
  모양                                           메일 주소(예시 도메인 빼고)·Windows 사용자 경로·휴대폰 번호

    .venv\Scripts\python.exe -m tools.probe_leaks             추적 파일 + 아직 안 넣은 새 파일 (지금 내용)
    .venv\Scripts\python.exe -m tools.probe_leaks --staged    커밋할 내용 — .githooks/pre-commit
    .venv\Scripts\python.exe -m tools.probe_leaks --history   모든 ref 의 파일·작성자·메시지·브랜치 이름
                                                    (--pre-push 는 올리는 커밋만 — .githooks/pre-push 가 stdin 으로 준다)
    .venv\Scripts\python.exe -m tools.probe_leaks --selftest  판정 규칙 (가짜 값)

걸리면 `파일:줄 — 어디서 온 값` 만 찍고 1 로 끝난다. 짧은 값(4자 미만·8자리 미만 숫자)은 따옴표로 감싼 꼴만 본다.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import envfile, secret  # noqa: E402

SETTINGS_FILES = ("config/settings.local.json", "dist/*/config/settings.local.json")
SITE_DIR = "collect/sites/local"          # git 밖 사이트 코드 (collect/sites/__init__.py)
CUSTOMERS_DIR = "docs/customers"          # git 밖 업체 문서 — 파일 이름이 업체다

# 값이 프로그램 낱말(고르는 값·예약 줄 꼴·일반 낱말)이라 코드·문서에 그대로 나오는 키
SAFE_KEYS = frozenset({
    "run_modules", "collect_sources", "logistics_mode", "sales_mode", "browser_channel", "phone_os",
    "sms_source", "adb_connection", "auto_run_mode", "auto_run_times", "sms_keyword",
})
COMMON = frozenset({"user", "admin", "administrator", "owner", "guest", "test", "home", "desktop"})
MIN_LEN = 4
GENERIC_DIRS = frozenset({
    "program files", "program files (x86)", "programdata", "users", "public", "desktop", "documents",
    "downloads", "appdata", "local", "locallow", "roaming", "windows", "system32", "temp", "onedrive",
})
# 여러 업체가 같이 쓰는 호스트 — 앞 칸(프로젝트 id)만 업체 것이다
SHARED_SUFFIXES = (".supabase.co", ".supabase.in", ".workers.dev", ".pages.dev", ".github.io")
KR_SLD = frozenset({"co", "or", "go", "ne", "ac", "re", "pe"})
LOOPBACK = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

URL_RE = re.compile(r"https?://[^\s\"'`<>)\]]+", re.I)
# 끝 경계는 ASCII 로 — 한글 조사가 붙은 주소(…com으로)도 잡는다 (10-02 검토)
EMAIL_RE = re.compile(r"(?<![\w.+/-])[\w.+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![A-Za-z0-9_-])")
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "example", "test", "invalid", "localhost",
                   "users.noreply.github.com")
VENDOR_ADDRESSES = frozenset({"noreply@anthropic.com", "onboarding@resend.dev"})
USER_PATH_RE = re.compile(r"(?:[A-Za-z]:|(?<![\w/])/[A-Za-z])[\\/]+Users[\\/]+([^\\/\s\"'`<>%$*{}|:)\]]+)", re.I)
PLACEHOLDER_USERS = frozenset({"...", "x", "user", "username", "you", "name", "public", "default", "all users"})
# 휴대폰은 붙여 써도, 유선·070 은 - 로 나눈 꼴만 (숫자 이어 쓴 다른 값과 겹친다)
PHONE_RE = re.compile(r"(?<![0-9A-Za-z])(?:01[016789][-. ]?\d{3,4}[-. ]?\d{4}|0(?:2|[3-6][1-5]|70)-\d{3,4}-\d{4})"
                      r"(?![0-9A-Za-z])")
# 사설 IP (127.x 는 뺀다). 예시는 문서용 192.0.2.x (RFC 5737)
PRIVATE_IP_RE = re.compile(r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
                           r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\.?\d)")


# ── 찾을 값 ────────────────────────────────────────────────────────────

def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (k, *_strings(v))]
    return []


def _example_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in EXAMPLE_DOMAINS)


def host_forms(host: str) -> list[str]:
    host = host.lower().strip(".")
    if not host or host in LOOPBACK or _example_domain(host):
        return []
    if re.fullmatch(r"[\d.]+", host):
        return [host]
    for suffix in SHARED_SUFFIXES:
        if host.endswith(suffix):
            return [host, host[: -len(suffix)].rsplit(".", 1)[-1]]
    labels = host.split(".")
    size = 3 if len(labels) >= 3 and labels[-1] == "kr" and labels[-2] in KR_SLD else 2
    return [host, ".".join(labels[-size:])]


def _forms(text: str) -> list[str]:
    """한 값에서 찾을 꼴 — 그대로 + 경로면 / 꼴·JSON 꼴·폴더 이름, 주소면 호스트, IP:포트면 IP."""
    text = text.strip()
    out = [text]
    if re.match(r"[A-Za-z]:[\\/]", text):
        path = PureWindowsPath(text)
        out += [text.replace("\\", "/"), text.replace("\\", "\\\\")]
        # 프로젝트 안 경로의 폴더 이름(dist·logs …)은 이 저장소의 낱말이다
        if not os.path.normcase(text).startswith(os.path.normcase(str(ROOT)) + os.sep):
            names = path.parts[1:-1] if path.suffix else path.parts[1:]
            out += [name for name in names if name.lower() not in GENERIC_DIRS]
    elif re.match(r"[a-z][a-z0-9+.-]*://", text, re.I):
        out += host_forms(urlparse(text).hostname or "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}:\d+", text):
        out.append(text.rsplit(":", 1)[0])
    elif text.isdigit() and len(text) >= 12:
        out.append(text[:9])              # 상품코드 앞자리 = 업체 코드 — 같은 업체의 다른 상품도 잡는다
    return out


def settings_values(data: dict, source: str = "", reveal=None, notes: list | None = None) -> dict[str, str]:
    """설정 값에서 찾을 꼴. `reveal` 을 주면 감싼 비밀번호(dpapi:·baked:)를 풀어 **평문도** 찾는다 (10-02 검토)."""
    out: dict[str, str] = {}
    for key, value in data.items():
        if key in SAFE_KEYS:
            continue
        for text in _strings(value):
            for form in _forms(text):
                out.setdefault(form, f"{source}{key}")
            if reveal is None or not secret.is_wrapped(text):
                continue
            try:
                plain = str(reveal(text))
            except (secret.SecretError, ValueError, OSError) as exc:     # 다른 PC·계정에서 감싼 값, 씨앗 없음
                if notes is not None:
                    notes.append(f"{source}{key} 를 풀지 못해 평문은 못 봤다 ({type(exc).__name__})")
                continue
            for form in _forms(plain):
                out.setdefault(form, f"{source}{key}(평문)")
    return out


def site_fields(source: str) -> list[str]:
    """`MailSite(id=..., name=...)` 의 글자 값. 다른 호출의 `name=` 은 보지 않는다."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if isinstance(node, ast.Call) and getattr(func, "id", getattr(func, "attr", None)) == "MailSite":
            out += [kw.value.value for kw in node.keywords
                    if kw.arg in ("id", "name") and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)]
    return out


def local_values() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((ROOT / SITE_DIR).rglob("*.py")):
        text = _decode(path.read_bytes()) or ""
        for value in site_fields(text):
            out.setdefault(value, "git 밖 사이트 이름")
        for url in URL_RE.findall(text):
            for form in host_forms(urlparse(url).hostname or ""):
                out.setdefault(form, "git 밖 사이트 주소")
    for path in sorted((ROOT / CUSTOMERS_DIR).glob("*.md")):
        out.setdefault(path.stem, "git 밖 업체 문서 이름")
    return out


def identity() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("COMPUTERNAME", "USERNAME", "USERDOMAIN"):
        if os.environ.get(name):
            out.setdefault(os.environ[name], f"이 PC {name}")
    for key in ("user.name", "user.email"):
        value = subprocess.run(["git", "config", "--global", "--get", key], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", errors="replace").stdout.strip()
        if value:
            out.setdefault(value, f"git 전역 {key}")
    return out


def collect() -> tuple[dict[str, str], list[str], bool]:
    """({값: 어디서 온 값}, 경고, 눈먼 검사인가). 파일이 **있는데** 못 읽으면 눈먼 검사다 — 훅은 막는다 (10-02 검토)."""
    found: dict[str, str] = {}
    warnings: list[str] = []
    blind = False
    if not (ROOT / SETTINGS_FILES[0]).is_file():
        warnings.append(f"{SETTINGS_FILES[0]} 이 없다 — 설정 값은 못 봤다")
    for path in (p for pattern in SETTINGS_FILES for p in sorted(ROOT.glob(pattern))):
        rel = path.relative_to(ROOT).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            warnings.append(f"{rel} 를 읽지 못했다 (JSON·인코딩) — 그 설정 값은 못 봤다")
            blind = True
            continue
        source = "" if rel == SETTINGS_FILES[0] else f"{rel} "
        for value, name in settings_values(data if isinstance(data, dict) else {}, source,
                                           reveal=secret.unwrap, notes=warnings).items():
            found.setdefault(value, name)
    env = envfile.read()
    if envfile.ENV_PATH.is_file() and not env:
        warnings.append(".env 가 있는데 값을 하나도 읽지 못했다")
        blind = True
    for key, value in env.items():
        for form in _forms(value):
            found.setdefault(form, f".env {key}")
    for value, name in (*local_values().items(), *identity().items()):
        found.setdefault(value, name)
    return found, warnings, blind


MODES = ("--staged", "--pre-push", "--history", "--selftest")


def parse_mode(argv: list[str]) -> str | None:
    """인자 하나(또는 없음). 훅 줄 끝의 CR 은 뗀다 — 남으면 모르는 인자로 보고 멈춘다 (10-02 검토)."""
    args = [arg.strip() for arg in argv if arg.strip()]
    if not args:
        return ""
    return args[0] if len(args) == 1 and args[0] in MODES else None


# ── 판정 ──────────────────────────────────────────────────────────────

def _edge(char: str) -> bool:
    return char.isascii() and char.isalnum()


class Needles:
    def __init__(self, found: dict[str, str]):
        self.long: list[tuple[str, re.Pattern, str]] = []
        self.short: list[tuple[re.Pattern, str]] = []
        for value, name in found.items():
            value = value.strip()
            if not value or value.lower() in COMMON:
                continue
            if len(value) < MIN_LEN or (value.isdigit() and len(value) < 8):
                self.short.append((re.compile(r"([\"'`])" + re.escape(value) + r"\1"), name))
                continue
            left = r"(?<![A-Za-z0-9])" if _edge(value[0]) else ""
            # 숫자 값은 더 긴 숫자의 앞자리여도 잡는다 (상품코드 앞자리)
            right = r"(?![A-Za-z0-9])" if _edge(value[-1]) and not value.isdigit() else ""
            self.long.append((value.lower(), re.compile(left + re.escape(value) + right, re.I), name))

    def __len__(self) -> int:
        return len(self.long) + len(self.short)

    def names(self, line: str) -> set[str]:
        low = line.lower()
        out = {name for value, rx, name in self.long if value in low and rx.search(line)}
        out |= {name for rx, name in self.short if rx.search(line)}
        for match in EMAIL_RE.finditer(line):
            if match.group(0).lower() not in VENDOR_ADDRESSES and not _example_domain(match.group(1).lower()):
                out.add("메일 주소 모양")
        if any(m.group(1).lower() not in PLACEHOLDER_USERS for m in USER_PATH_RE.finditer(line)):
            out.add("Windows 사용자 경로")
        if PHONE_RE.search(line):
            out.add("전화번호 모양")
        if PRIVATE_IP_RE.search(line):
            out.add("내부 IP 모양")
        return out


def _decode(raw: bytes) -> str | None:
    """글자 파일이면 내용, 아니면 None (속을 볼 수 없다). `.bat` 은 cp949 일 수 있다."""
    if b"\0" in raw[:8000]:
        return None
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def scan(label: str, raw: bytes, needles: Needles) -> int:
    text = _decode(raw)
    if text is None:
        print(f"{label} — 속을 볼 수 없는 파일이다 (git 에 넣지 않는다)")
        return 1
    hits = 0
    for number, line in enumerate(text.splitlines(), 1):
        names = needles.names(line)
        if names:
            print(f"{label}:{number} — {', '.join(sorted(names))}")
            hits += 1
    return hits


# ── 볼 내용 ───────────────────────────────────────────────────────────

def _git(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True, check=True).stdout


def _paths(*args: str) -> list[str]:
    return [p for p in _git(*args).decode("utf-8", errors="replace").split("\0") if p]


def working_items():
    for path in _paths("ls-files", "-z", "--cached", "--others", "--exclude-standard"):
        try:
            yield path, (ROOT / path).read_bytes()
        except OSError:
            continue                      # 지웠지만 아직 커밋하지 않은 파일


def staged_items():
    for path in _paths("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"):
        yield path, _git("show", f":{path}")


def pushed_revs(stdin_text: str) -> list[str]:
    """pre-push 가 stdin 으로 주는 `<로컬 ref> <로컬 sha> <원격 ref> <원격 sha>` 줄에서 올릴 커밋. 지우기(0…)는 뺀다."""
    revs = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) == 4 and re.fullmatch(r"[0-9a-f]{40,64}", parts[1]) and set(parts[1]) != {"0"}:
            revs.append(parts[1])
    return revs


def history_items(revs: list[str] | None = None):
    """revs 에서 닿는 모든 파일·커밋 (없으면 모든 ref)."""
    revs = revs or ["--all"]
    paths: dict[str, str] = {}
    for line in _git("rev-list", "--objects", *revs).decode("utf-8", errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, path)
    if paths:
        kinds = _git("cat-file", "--batch-check", data=("\n".join(paths) + "\n").encode()).decode().splitlines()
        blobs = [line.split()[0] for line in kinds if line.split()[1:2] == ["blob"]]
        data = _git("cat-file", "--batch", data=("\n".join(blobs) + "\n").encode()) if blobs else b""
        pos = 0
        for sha in blobs:
            end = data.index(b"\n", pos)
            size = int(data[pos:end].split()[2])
            yield f"{paths[sha]} (기록 {sha[:7]})", data[end + 1:end + 1 + size]
            pos = end + 1 + size + 1
    log = _git("log", *revs, "--format=%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B%x1e").decode("utf-8", errors="replace")
    for entry in log.split("\x1e"):
        fields = entry.strip("\n").split("\x1f")
        if len(fields) == 4:
            yield f"커밋 {fields[0][:7]} 작성자·메시지", "\n".join(fields[1:]).encode()
    yield "브랜치·태그 이름", _git("for-each-ref", "--format=%(refname)")


# ── 판정 규칙 확인 (가짜 값) ──────────────────────────────────────────

# 전부 가짜 값이다 (경로·계정·주소 모두) — 판정 규칙만 본다
FAKE = {
    "login_user_id": "comp01x", "delivery_box": "박스Q", "run_modules": ["mail"], "logistics_mode": "자동",
    "excel_passwords": {"사이트Q": "4321"}, "target_exe": "D:\\Vendor9\\AppDir\\App9Main.exe",
    "mail_url": "https://login.sitezz.co.kr/form", "server_url": "https://refabc123xyz.supabase.co",
    "mail_unread_only": True, "mail_days_back": 1, "adb_wireless_address": "192.0.2.7:5555",
    "login_password": "dpapi:AAAABBBB", "build_admin_email": "boss" "@corpzz.co.kr",
    "hold_exclude_codes": ["5551234000017"],
}
LINES = (
    ("id = 'comp01x'", {"login_user_id"}),
    ("comp01xyz 는 다른 값", set()),
    ("comp01x_backup", {"login_user_id"}),
    ('box = "박스Q"', {"delivery_box"}),
    ("박스Q를 고른다", set()),
    ("mode = '자동'  modules = [\"mail\"]", set()),
    ("사이트Q 비밀번호", {"excel_passwords"}),
    ("pw = '4321'", {"excel_passwords"}),
    ("port 43210", set()),
    ("설치 폴더 Vendor9 아래", {"target_exe"}),
    ("D:/Vendor9/AppDir/App9Main.exe", {"target_exe"}),
    ("C:\\Program Files\\x", set()),
    ("sitezz.co.kr 로 로그인", {"mail_url"}),
    ("refabc123xyz 프로젝트", {"server_url"}),
    ("https://supabase.com/docs", set()),
    ("192.0.2.7 에 붙인다", {"adb_wireless_address"}),
    ("192.0.2.70 은 다른 값", set()),
    ("pw: dpapi:AAAABBBB", {"login_password"}),
    ("다른 상품 5551234009999", {"hold_exclude_codes"}),
    # 모양에 걸리는 가짜는 두 조각으로 적는다 — 이 파일이 제 검사에 걸리지 않게
    ("연락처 010" "-1234-5678 · 0101" "2345678", {"전화번호 모양"}),
    ("대표 02" "-123-4567", {"전화번호 모양"}),
    ("sha 9f01012345678ab · 0212345678", set()),
    ("adb connect 192.168" ".5.20:5555", {"내부 IP 모양"}),
    ("10.0" ".0.8 · 172.20" ".1.1", {"내부 IP 모양"}),
    ("Windows 10.0.12345 · 127.0.0.1:8080 · 172.32.0.1 · v1.10.2.3.4", set()),
    ("owner" "@company.co.kr 로 보낸다", {"메일 주소 모양"}),
    ("boss" "@corpzz.co.kr", {"build_admin_email", "메일 주소 모양"}),
    ("user01@example.com · a@b.c · supabase-js@2.116.0", set()),
    ("psql postgresql://x@db.abc.supabase.co/postgres", set()),
    ("Co-Authored-By: Claude <noreply@anthropic.com>", set()),
    ("C:\\Users" "\\kim01\\Desktop", {"Windows 사용자 경로"}),
    ("cd /c/Users" "/kim01/x", {"Windows 사용자 경로"}),
    ("C:\\\\Users\\\\...\\\\Desktop · /c/Users/x · %USERPROFILE%\\Desktop · C:\\Users\\$env:USERNAME", set()),
    ("user_id = row.user", set()),
)


def selftest() -> int:
    results = []

    def check(name: str, ok: bool) -> None:
        results.append(ok)
        print(f"  {'통과' if ok else '실패'}  {name}")

    needles = Needles({**settings_values(FAKE), "user": "이 PC USERNAME"})
    for line, expected in LINES:
        got = needles.names(line)
        check(f"{line!r} → {sorted(expected) or '없음'}" + ("" if got == expected else f" (받은 것 {sorted(got)})"),
              got == expected)
    source = 'SITE = MailSite(id="zzsite", name="지지사이트", login_once=f)\nb = page.get_by_role("button", name="로그인")'
    check("MailSite 의 id·name 만 뽑는다", site_fields(source) == ["zzsite", "지지사이트"])
    check("주소 → 호스트·도메인", host_forms("sso.mailzz.com") == ["sso.mailzz.com", "mailzz.com"])
    check("내 PC·예시 주소는 뽑지 않는다", host_forms("127.0.0.1") == [] == host_forms("a.example.com"))
    check("압축 파일은 속을 볼 수 없다", _decode(b"PK\x03\x04\x00\x00") is None)
    check("cp949 .bat 도 읽는다", _decode("한글".encode("cp949")) == "한글")
    zero, sha = "0" * 40, "a1" * 20
    check("push 할 커밋만 고른다 (지우기 0… 는 뺀다)",
          pushed_revs(f"refs/heads/main {sha} refs/heads/main {zero}\nrefs/heads/old {zero} refs/heads/old {sha}\n")
          == [sha] and pushed_revs("") == [])
    # 감싼 비밀번호는 풀어서 평문도 찾는다 — 풀기는 가짜 (10-02 검토)
    notes: list[str] = []
    opened = Needles(settings_values(FAKE, reveal=lambda text: "pw-" + text[-4:], notes=notes))
    check("★ 감싼 비밀번호의 평문이 든 줄도 걸린다", opened.names("PASSWORD = 'pw-BBBB'") == {"login_password(평문)"})

    def cannot(text):
        raise secret.SecretError("다른 PC")

    settings_values(FAKE, reveal=cannot, notes=notes)
    check("못 풀면 평문은 못 봤다고 남긴다", any("login_password" in note for note in notes))
    check("한글 조사가 붙은 메일 주소도 모양으로 걸린다",
          needles.names("boss" "@corpzz.com으로 보낸다") == {"메일 주소 모양"})
    check("인자 — 줄 끝 CR 은 떼고, 모르는 인자는 멈춘다",
          parse_mode(["--pre-push\r"]) == "--pre-push" and parse_mode([]) == ""
          and parse_mode(["--bogus"]) is None and parse_mode(["--staged", "--history"]) is None)
    print(f"결과: 통과 {sum(results)} / 실패 {len(results) - sum(results)}")
    return 0 if all(results) else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            continue                      # 재설정 불가한 스트림. 출력만 깨질 뿐 검사는 계속한다
    mode = parse_mode(sys.argv[1:])
    if mode is None:
        print(f"모르는 인자: {sys.argv[1:]} — {' / '.join(MODES)} 중 하나 (또는 없이)")
        return 2
    if mode == "--selftest":
        return selftest()
    found, warnings, blind = collect()
    for warning in warnings:
        print(f"경고: {warning}")
    needles = Needles(found)
    if mode == "--staged":
        items = staged_items()
    elif mode == "--pre-push":                # .githooks/pre-push — 올리는 커밋만 (이 PC 에만 둔 옛 기록은 안 본다)
        items = history_items(pushed_revs(sys.stdin.read()))
    elif mode == "--history":
        items = history_items()
    else:
        items = working_items()
    hits = sum(scan(label, raw, needles) for label, raw in items)
    print(f"결과: 찾을 값 {len(needles)}개 + 모양 4가지 / 걸린 줄 {hits}")
    if blind and mode in ("--staged", "--pre-push"):
        print("막는다 — 설정·.env 를 읽지 못한 채로는 커밋·push 를 통과시키지 않는다 (파일을 고친 뒤 다시)")
        return 1
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
