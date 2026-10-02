"""로깅과 실패 시 스크린샷 저장.

로그만 보고 어느 단계에서 실패했는지 알 수 있어야 한다.
모든 업무 단계는 `step()`으로 감싸서 진입/종료를 남긴다.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config.settings import LOG_DIR

_CONFIGURED = False
_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO, prefix: str = "rpa", folder: Path | None = None) -> Path:
    """콘솔 + 파일 로깅을 초기화하고 로그 파일 경로를 돌려준다.
    `prefix`·`folder` — LLM 워커는 `logs/llm/llm_날짜.log` (RPA 로그와 안 섞고, 임시 로그 정리 대상도 아니다)."""
    global _CONFIGURED

    folder = folder or LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / f"{prefix}_{datetime.now():%Y%m%d}.log"
    if _CONFIGURED:
        return log_path

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(file_handler)

    # 빌드된 exe 를 콘솔 없이(windowed) 띄우면 sys.stdout 이 None 이다.
    # 그대로 StreamHandler 에 넘기면 첫 로그에서 죽는다. 파일 로그만 남긴다.
    stream = sys.stdout
    if stream is None:
        logging.getLogger(__name__).debug("표준 출력이 없다. 파일 로그만 남긴다.")
    else:
        try:
            stream.reconfigure(encoding="utf-8")  # 콘솔 코드페이지가 cp949여도 한글 유지
        except (AttributeError, OSError) as exc:
            # 재설정 불가한 스트림. 로깅 자체는 계속한다.
            logging.getLogger(__name__).debug("출력 인코딩 설정 실패: %s", exc)
        console = logging.StreamHandler(stream)
        console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(console)

    _CONFIGURED = True
    logging.getLogger(__name__).info("로그 파일: %s", log_path)
    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ★ **사용자에게 보이는 로그는 이 이름 하나로만 쓴다** (사용자 요청 2026-09-21).
#   화면(진행 로그 / 오버레이 최근 로그)은 이 이름만 받는다 — 나머지 로그에는
#   auto_id·경로·pid 같은 내부 값이 있어 사용자가 읽을 수 없다. 파일 로그에는 둘 다 남는다.
#   여기에는 **사람 말로만** 쓴다: 코드·id 대신 상품명·엑셀명·건수.
USER_LOGGER = "user"


def user_log() -> logging.Logger:
    return logging.getLogger(USER_LOGGER)


def is_user_record(record: logging.LogRecord) -> bool:
    """화면 로그 핸들러의 필터. 사용자용 로그만 통과시킨다."""
    return record.name == USER_LOGGER


@contextmanager
def step(logger: logging.Logger, name: str, screenshot_on_error: bool = True):
    """업무 단계 하나를 감싼다. 진입/성공/실패를 로그로 남긴다.

    실패하면 스크린샷을 남기고 예외를 그대로 올린다. 예외를 삼키지 않는다.
    """
    logger.info("▶ %s 시작", name)
    try:
        yield
    except Exception as exc:
        logger.error("✕ %s 실패: %s: %s", name, type(exc).__name__, exc)
        if screenshot_on_error:
            shot = save_screenshot(name)
            if shot:
                logger.error("  스크린샷: %s", shot)
        raise
    else:
        logger.info("✔ %s 완료", name)


def save_screenshot(tag: str) -> Path | None:
    """전체 화면을 logs/ 에 저장한다. 실패해도 원래 예외를 가리지 않는다."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:60]
    path = LOG_DIR / f"error_{datetime.now():%Y%m%d_%H%M%S}_{safe}.png"
    try:
        from PIL import ImageGrab

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(all_screens=True).save(path)
        return path
    except Exception as exc:  # 스크린샷 실패가 본래 실패를 덮으면 안 된다
        logging.getLogger(__name__).warning("스크린샷 저장 실패: %s", exc)
        return None
