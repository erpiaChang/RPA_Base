r"""메일 브라우저(Playwright)의 node.exe 를 **켤 때마다 풀지 않는다** (09-29).

실행용 exe 는 한 파일(onefile)이라 켤 때마다 안의 것을 전부 임시 폴더에 푼다. 153MB 중 92MB 가
메일 기능만 쓰는 node.exe 였다. 그래서 빌드(`build_run.spec`)가 node.exe 를 풀리는 칸에서 빼
**exe 의 Windows 리소스**로 넣고, 메일 기능을 처음 쓸 때 여기서 exe 옆 `driver\<해시>\node.exe` 로
한 번 꺼낸다. Playwright 는 `PLAYWRIGHT_NODEJS_PATH` 로 그것을 쓴다 (`playwright/_impl/_driver.py`).

리소스 = `MAGIC`(8) + sha256(node.exe)(32) + zlib(node.exe). 쓰기 전마다 해시를 본다 — 깨졌거나
바뀌었으면 다시 꺼낸다. 판이 바뀌면 옛 폴더를 지운다.
개발 중이거나 리소스가 없는 빌드면 아무것도 하지 않는다 — Playwright 가 패키지 안의 node.exe 를 쓴다.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import sys
import zlib
from ctypes import wintypes
from pathlib import Path

from utils.logger import get_logger

log = get_logger(__name__)

RES_TYPE = "RPADATA"
RES_NAME = "PWNODE"
MAGIC = b"RPANODE1"
HEAD = len(MAGIC) + 32
ENV = "PLAYWRIGHT_NODEJS_PATH"


def pack(node: Path | str) -> bytes:
    """빌드가 부른다 — node.exe 를 리소스 내용으로 만든다. PyInstaller 목록의 경로는 문자열이다."""
    data = Path(node).read_bytes()
    return MAGIC + hashlib.sha256(data).digest() + zlib.compress(data, 6)


def needed(baked: dict) -> bool:
    """이 빌드에 메일 기능이 있나. 없으면 빌드가 Playwright 를 통째로 뺀다."""
    return not baked.get("run_modules_locked") or "mail" in (baked.get("run_modules") or [])


def prepare() -> None:
    """메일 브라우저를 띄우기 전에 부른다. 빌드본에 리소스가 있으면 꺼낸 node.exe 를 쓰게 한다."""
    if not getattr(sys, "frozen", False):
        return
    blob = resource()
    if blob is None:
        log.info("node.exe 리소스가 없다 — 번들 안의 것을 쓴다")
        return
    from config.settings import PROJECT_ROOT

    os.environ[ENV] = str(ensure(PROJECT_ROOT, blob))


def ensure(root: Path, blob) -> Path:
    """`root\\driver\\<해시>\\node.exe` 를 돌려준다. 없거나 깨졌으면 `blob` 에서 꺼낸다."""
    blob = memoryview(blob)
    if bytes(blob[:len(MAGIC)]) != MAGIC:
        raise ValueError("node.exe 리소스의 머리가 맞지 않는다")
    digest = bytes(blob[len(MAGIC):HEAD])
    folder = root / "driver"
    target = folder / digest.hex()[:16] / "node.exe"
    if not (target.is_file() and _sha256(target) == digest):
        data = zlib.decompress(blob[HEAD:])
        if hashlib.sha256(data).digest() != digest:
            raise ValueError("node.exe 리소스가 깨졌다 (해시가 다르다)")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        os.replace(part, target)          # 쓰다 끊겨도 반쪽 node.exe 가 남지 않는다
        log.info("메일 브라우저 드라이버를 꺼냈다: %s (%.0fMB)", target, len(data) / 1e6)
    for old in folder.iterdir():
        if old.is_dir() and old != target.parent:
            try:
                shutil.rmtree(old)
                log.info("옛 드라이버를 지웠다: %s", old)
            except OSError as exc:
                log.warning("옛 드라이버를 지우지 못했다 (다음에 다시): %s — %s", old, exc)
    return target


def resource(module: int | None = None) -> memoryview | None:
    """exe 에 박힌 리소스를 복사 없이 본다. 없으면 None. `module` 은 확인 도구가 빌드한 exe 를 열 때 쓴다."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.FindResourceW.restype = ctypes.c_void_p
    kernel32.FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    kernel32.LoadResource.restype = ctypes.c_void_p
    kernel32.LoadResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.LockResource.argtypes = [ctypes.c_void_p]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.SizeofResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    if module is None:
        module = kernel32.GetModuleHandleW(None)
    found = kernel32.FindResourceW(module, RES_NAME, RES_TYPE)
    if not found:
        return None
    size = kernel32.SizeofResource(module, found)
    address = kernel32.LockResource(kernel32.LoadResource(module, found))
    if not address or not size:
        return None
    return memoryview((ctypes.c_char * size).from_address(address)).cast("B")


def _sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()
