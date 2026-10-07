r"""주문 자동화 — 실행용 진입점.

    .venv\Scripts\python.exe main_run.py

배포본은 이 파일에서 시작한다. 사용자는 exe 만 실행하면 되고, 설정은
**빌드에 구워 넣은 값**(`config/settings.baked.json`)에서 온다.

**다른 기능은 import 하지 않는다.** PyInstaller 는 여기서 도달하는 것만 넣는다.

★ **한 벌만 뜬다** (09-28, `utils/instance.py`). 자동 켜기가 5분마다 exe 를 띄우므로, 이미 떠 있으면
  무거운 것을 불러오기 전에 곧바로 끝낸다. 사람이 두 번 눌렀으면 이미 켜져 있다고 알린다.
★ **로그온 때(`--background`) 켜진 빌드본은 작업 스케줄러에 넘긴다** (09-29, `autostart.hand_over`). 작업이 띄운
  것이 떠 있는 동안에는 5분 트리거가 exe 를 띄우지 않는다 — 켤 때마다 푸는 onefile 이 5분마다 풀던 것이 없어진다.
  **사람이 두 번 누른 것은 넘기지 않는다** — exe 를 두 번 풀어 창이 1.8초 → 3.6초로 늦었다 (09-29 실측).
  그렇게 켠 동안만 5분마다 푼다(46MB). 창을 닫으면 작업이 다시 켜서 작업 소유로 돌아온다.
  자동 켜기를 안 켰거나 넘기지 못하면 그냥 뜬다.
"""
from __future__ import annotations

import sys

from utils import crashlog, instance

if __name__ == "__main__":
    crashlog.install()                      # 켜는 중 import 실패(설정 깨짐 등)·창의 예외도 logs 에 남긴다 (10-07)
    args = sys.argv[1:]
    if not instance.acquire():
        if "--background" not in args:
            instance.tell_already_running()
        sys.exit(0)
    if getattr(sys, "frozen", False) and "--background" in args and "--task" not in args:
        from utils import autostart

        instance.release()                  # 넘겨받는 쪽이 잡는다
        if autostart.hand_over(instance.exe_path()):
            sys.exit(0)
        if not instance.acquire():          # 넘기지 못한 사이 다른 벌이 떴다
            sys.exit(0)
    from gui.run_app import run

    sys.exit(run())
