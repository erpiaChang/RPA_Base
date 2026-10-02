r"""UserPromptSubmit 훅: 이전 작업에서 남은 임시/디버그 산출물을 알린다.

로그와 디버그 파일은 버그 수정 중에만 만든다. 만들었으면 그 작업이 끝날 때
지운다. 다음 구현 작업을 시작하는 시점에 남아 있으면 여기서 알려준다.

stdout은 Claude의 컨텍스트에 추가된다. 차단하지 않는다(exit 0).
"""
import os
import sys
import time
from pathlib import Path

# (glob 패턴, 설명)
TEMP_PATTERNS = [
    ("docs/_dump_*.txt", "UI 조사 덤프 — UI_SURVEY.md 기록 후 삭제"),
    ("docs/_windows_*.txt", "창 목록 덤프 — UI_SURVEY.md 기록 후 삭제"),
    ("docs/_probe_*.txt", "화면 조사 덤프(probe_screen) — CONTROLS.md 기록 후 삭제"),
    ("logs/*.log", "실행 로그 — 원인 파악이 끝났으면 삭제"),
    ("logs/*.png", "실패 스크린샷 — 원인 파악이 끝났으면 삭제"),
    ("logs/*.html", "실행 리포트 — 본 뒤 삭제"),
    ("logs/outbox.jsonl", "서버로 못 보낸 이벤트(확인 도구가 만든 것) — 삭제"),
    ("logs/run_inflight.json", "끝나지 않은 실행 표식(확인 도구가 만든 것) — 삭제"),
    ("logs/_probe_*.jsonl", "확인 도구 임시 이력 — 삭제"),
    ("CLAUDE-SECURITY-*/CLAUDE-SECURITY-RESULTS.md", "보안 스캔 리포트 — 반영 뒤 폴더째 삭제"),
    ("debug_*.py", "디버그 스크립트 — 작업 종료 시 삭제"),
    ("tmp_*.py", "임시 스크립트 — 작업 종료 시 삭제"),
    ("temp_*.py", "임시 스크립트 — 작업 종료 시 삭제"),
    ("scratch*.py", "임시 스크립트 — 작업 종료 시 삭제"),
    ("**/*.orig", "편집 잔여물 — 삭제"),
    ("**/*.bak", "편집 잔여물 — 삭제"),
]

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass  # 재설정 불가한 스트림. 검사 자체는 계속한다.

SKIP_PARTS = {".venv", "__pycache__", "node_modules", ".git"}


def main() -> int:
    root_env = os.environ.get("CLAUDE_PROJECT_DIR")
    if not root_env:
        return 0
    root = Path(root_env)

    now = time.time()
    found: list[tuple[Path, str, float]] = []
    for pattern, note in TEMP_PATTERNS:
        for path in root.glob(pattern):
            if not path.is_file() or SKIP_PARTS & set(path.parts):
                continue
            try:
                age_h = (now - path.stat().st_mtime) / 3600
            except OSError:
                continue
            found.append((path, note, age_h))

    if not found:
        return 0

    print("[임시 산출물 정리 필요] 이전 작업에서 남은 파일이 있다.")
    for path, note, age_h in sorted(found):
        print(f"  {path.relative_to(root).as_posix()}  ({age_h:.1f}시간 전)  — {note}")
    print(
        "이번 요청이 이 파일들과 직접 관련된 버그 수정이 아니라면 먼저 삭제할 것.\n"
        "덤프 파일은 내용을 docs/UI_SURVEY.md 에 기록한 뒤에만 삭제한다."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
