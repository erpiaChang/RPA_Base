r"""대상 프로그램 실행 / 연결 (Phase 2).

이미 실행 중이면 그 프로세스에 연결하고, 아니면 EXE를 직접 실행한다.
바로가기를 더블클릭하지 않는다. 경로는 호출자가 넘기며(설정에서 읽는다),
이 모듈에 하드코딩하지 않는다.

실행 직후에는 창이 "하나라도 뜰 때까지"만 기다린다. 로그인 뒤 메인 창은
`<업체코드> - <아이디>` 제목으로 판정한다 (`docs/CONTROLS.md`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pywinauto import Application, Desktop
from pywinauto.application import ProcessNotFoundError

from config.settings import SETTINGS
from utils.dpi import scale_info
from utils.elevation import check_target, is_elevated
from utils.logger import get_logger
from utils import winprobe
from utils.process import newest_pid, pids_by_name, pids_for_exe
from utils.wait import WaitTimeout, wait_for


def _area(window) -> int:
    try:
        rect = window.rectangle()
        return max(0, rect.width()) * max(0, rect.height())
    except Exception:
        return 0

log = get_logger(__name__)


class ApplicationError(RuntimeError):
    """대상 프로그램 실행/연결 실패."""


class AlreadyRunningError(ApplicationError):
    """이미 실행 중이고, 중복 실행이 허용되지 않는다.

    사용자가 기존 창을 닫고 다시 시도할 수 있도록 GUI가 따로 안내한다.
    """

    def __init__(self, exe: str, pids: list[int]) -> None:
        self.exe = exe
        self.pids = pids
        super().__init__(
            f"{Path(exe).name} 이(가) 이미 실행 중이며 중복 실행을 허용하지 않는다.\n"
            f"실행 중인 프로세스: {', '.join(str(p) for p in pids)}\n"
            "기존 창을 닫은 뒤 [실행]을 다시 누르세요."
        )


@dataclass
class Target:
    """연결된 대상 프로그램."""

    app: Application
    pid: int
    exe: str
    started: bool  # True면 이번에 실행함, False면 기존 프로세스에 연결함

    def close(self) -> bool:
        """이 인스턴스를 닫는다. 로그인 실패로 남는 빈 창을 정리할 때 쓴다.

        먼저 창 닫기를 시도하고, 그래도 살아 있으면 프로세스를 종료한다.
        로그인 전이라 저장할 데이터가 없다.
        """
        if not self.app.is_process_running():
            return True
        try:
            self.app.kill(soft=True)  # WM_CLOSE
            wait_for(
                lambda: not self.app.is_process_running(),
                f"인스턴스 종료(pid={self.pid})",
                timeout=SETTINGS.timeouts.window,
            )
            log.info("인스턴스 종료 (pid=%s)", self.pid)
            return True
        except Exception as exc:
            log.warning("정상 종료 실패(pid=%s): %s. 프로세스를 종료한다.", self.pid, exc)

        try:
            self.app.kill(soft=False)  # TerminateProcess
            log.info("인스턴스 강제 종료 (pid=%s)", self.pid)
            return True
        except Exception as exc:
            log.error("인스턴스 종료 실패 (pid=%s): %s", self.pid, exc)
            return False

    def main_window(self, timeout: float | None = None):
        """이 프로세스의 최상위 창 중 가장 큰 것 = 메인 창.

        `app.windows()`는 Wrapper를 돌려주는데 Wrapper에는 `child_window`가 없다.
        handle로 WindowSpecification을 다시 잡아서 돌려준다.
        """
        timeout = SETTINGS.timeouts.window if timeout is None else timeout
        windows = wait_for(lambda: self.app.windows(), "메인 창", timeout=timeout)
        largest = max(windows, key=_area)
        return Desktop(backend=SETTINGS.backend).window(handle=largest.handle)

    def normalize(self, timeout: float | None = None):
        """작업 시작 전 창 상태를 맞춘다. 최대화하고 화면 크기를 로그에 남긴다.

        해상도/배율이 PC마다 달라서, 실패했을 때 환경을 알 수 있어야 한다.
        """
        window = self.main_window(timeout=timeout)
        info = scale_info()
        log.info(
            "화면: %dx%d, DPI %d (배율 %.2fx)",
            info["width"], info["height"], info["dpi"], info["scale"],
        )
        try:
            window.set_focus()
            window.maximize()
        except Exception as exc:
            log.warning("창 최대화 실패(계속 진행): %s", exc)
        log.info("메인 창: %r rect=%s", window.window_text(), window.rectangle())
        return window

    def window_titles(self) -> list[str]:
        titles = []
        for win in self.app.windows():
            try:
                titles.append(win.window_text())
            except Exception as exc:
                log.debug("창 제목 읽기 실패: %s", exc)
        return titles


_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def exe_name() -> str:
    """ERPia 실행 파일 이름 — 설정 `target_exe_name` (빌드에 굽는다). **코드·git 에 두지 않는다** (10-02 사용자 결정).

    경로가 섞여 있으면 파일 이름만 쓴다. 비면 '' — 그때는 어떤 exe 도 ERPia 로 보지 않는다.
    """
    return Path(str(getattr(SETTINGS, "target_exe_name", None) or "").strip().strip('"')).name


def is_erpia_exe(path: str | None) -> bool:
    """이 PC 에 있는 ERPia 실행 파일인가. 다른 exe 를 골라 두었으면 아니다 (09-29)."""
    path = (path or "").strip().strip('"')
    name = exe_name()
    return bool(path and name) and Path(path).is_file() and Path(path).name.lower() == name.lower()


def find_exe(hint: str | None = None) -> str | None:
    """이 PC 의 ERPia 실행 파일을 찾는다. 못 찾으면 None (사람이 [찾기] 로 고른다).

    순서: 설정값 → Program Files 아래 두 단계(제조사\\제품 폴더)에서 이름으로 → 프로그램 등록 정보(제어판 목록) →
    바탕화면·시작 메뉴 바로가기. 설치 폴더 이름은 코드에 두지 않는다 (10-02). 읽기만 한다 (사용자 요청 09-23, 09-29).
    """
    if is_erpia_exe(hint):
        return hint.strip().strip('"')
    if not exe_name():
        log.warning("ERPia 실행 파일 이름(target_exe_name)이 비어 있어 찾지 않는다")
        return None
    for finder in (_exe_from_program_files, _exe_from_registry, _exe_from_shortcuts):
        try:
            found = finder()
        except Exception as exc:     # 등록 정보·바로가기 읽기는 보조 수단이다. 실패해도 다음을 본다
            log.debug("ERPia 위치 조사 실패(%s): %s", finder.__name__, exc)
            continue
        if found:
            return found
    return None


def _exe_from_program_files() -> str | None:
    """Program Files 두 곳의 `<제조사>\\<제품>\\<이름>` · `<제품>\\<이름>` — 폴더 이름 없이 파일 이름으로만 (10-02)."""
    import os

    name = exe_name()
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not root or not Path(root).is_dir():
            continue
        for pattern in (f"*/{name}", f"*/*/{name}"):
            for found in sorted(Path(root).glob(pattern)):
                if found.is_file():
                    return str(found)
    return None


def _exe_from_registry() -> str | None:
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for flag in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                base = winreg.OpenKey(hive, _UNINSTALL_KEY, 0, winreg.KEY_READ | flag)
            except OSError:
                continue
            with base:
                for index in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, index)) as key:
                            name = str(_reg_value(key, "DisplayName") or "")
                            if "erpia" not in name.lower():
                                continue
                            for candidate in (_reg_value(key, "DisplayIcon"),
                                              _reg_value(key, "InstallLocation")):
                                found = _exe_near(candidate)
                                if found:
                                    return found
                    except OSError:
                        continue
    return None


def _reg_value(key, name: str):
    import winreg

    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _exe_near(value) -> str | None:
    """등록 정보 값(아이콘 경로·설치 폴더)·바로가기 대상 옆의 ERPia 실행 파일.

    파일이면 그 폴더에서 찾는다 — 아이콘·바로가기가 제거 프로그램 같은 다른 exe 일 수 있다 (09-29).
    """
    name = exe_name()
    if not value or not name:
        return None
    path = Path(str(value).split(",")[0].strip().strip('"'))
    candidate = (path.parent if path.is_file() else path) / name
    return str(candidate) if candidate.is_file() else None


def _exe_from_shortcuts() -> str | None:
    import os

    import win32com.client

    folders = [Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "")) / "Desktop",
               Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu",
               Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows" / "Start Menu"]
    shell = win32com.client.Dispatch("WScript.Shell")
    for folder in folders:
        if not folder.is_dir():
            continue
        for link in folder.rglob("*.lnk"):
            if "erpia" not in link.stem.lower():
                continue
            target = shell.CreateShortcut(str(link)).TargetPath
            found = _exe_near(target)
            if found:
                return found
    return None


def _validate(exe: str) -> Path:
    if not exe or not exe.strip():
        raise ApplicationError("실행 파일 경로가 비어 있다.")
    path = Path(exe.strip().strip('"'))
    if not path.exists():
        raise ApplicationError(f"파일이 없다: {path}")
    if not path.is_file():
        raise ApplicationError(f"파일이 아니다: {path}")
    if path.suffix.lower() != ".exe":
        raise ApplicationError(f".exe 파일이 아니다: {path.name}")
    return path


def connect(exe: str, timeout: float | None = None, pid: int | None = None) -> Target | None:
    """실행 중인 프로세스에 연결한다. 없으면 None.

    **여러 개가 떠 있으면 가장 최근에 실행된 것에 붙는다.**
    pywinauto의 `connect(path=...)` 는 WMI 목록을 뒤집어 처음 걸리는 하나를
    돌려줄 뿐이라 어느 인스턴스인지 보장하지 않는다. 그래서 여기서 직접 고른다.
    순서 판단은 PID 크기가 아니라 프로세스 생성 시각으로 한다 (PID는 재사용된다).

    `pid` 를 주면 그 인스턴스로 고정한다.
    """
    path = _validate(exe)
    timeout = SETTINGS.timeouts.window if timeout is None else timeout

    candidates = pids_for_exe(str(path))
    chosen = pid
    if chosen is not None:
        if candidates and chosen not in candidates:
            raise ApplicationError(
                f"pid={chosen} 는 {path.name} 프로세스가 아니다. 실행 중: {candidates}"
            )
    elif len(candidates) == 1:
        chosen = candidates[0]
    elif len(candidates) > 1:
        chosen = newest_pid(candidates)
        if chosen is None:
            raise ApplicationError(
                f"{path.name} 가 {len(candidates)}개 실행 중인데 실행 시각을 읽지 못했다.\n"
                f"  후보 pid: {candidates}\n"
                "  관리자 권한으로 실행하거나 --pid 로 대상을 지정할 것."
            )
        log.warning(
            "%s 가 %d개 실행 중이다. 가장 최근에 실행된 pid=%s 에 연결한다. (후보: %s)",
            path.name, len(candidates), chosen, candidates,
        )

    try:
        if chosen is not None:
            app = Application(backend=SETTINGS.backend).connect(
                process=chosen, timeout=timeout
            )
        else:
            # 권한 부족 등으로 PID 목록을 못 얻은 경우의 폴백.
            app = Application(backend=SETTINGS.backend).connect(
                path=str(path), timeout=timeout
            )
    except (ProcessNotFoundError, RuntimeError) as exc:
        log.info("실행 중인 프로세스 없음 (%s): %s", path.name, type(exc).__name__)
        return None

    pid_connected = app.process
    # 대상이 관리자 권한인데 우리가 아니면 창이 하나도 안 보인다.
    # 여기서 막지 않으면 "컨트롤을 못 찾는다"로 한참 헤매게 된다.
    check_target(pid_connected)
    target = Target(app=app, pid=pid_connected, exe=str(path), started=False)
    log.info("기존 프로세스에 연결: %s (pid=%s)", path.name, pid_connected)
    _log_window_title(target)
    return target


def _log_window_title(target: Target) -> None:
    """어느 인스턴스에 붙었는지 창 제목으로 남긴다.

    메인 창 제목은 `<업체코드> - <아이디>` 라서 로그인 여부까지 알 수 있다.
    """
    try:
        titles = [t for t in target.window_titles() if t.strip()]
    except Exception as exc:
        log.debug("창 제목을 읽지 못했다 (pid=%s): %s", target.pid, exc)
        return
    log.info("연결된 인스턴스의 창: %s", titles or "(없음)")


def start(
    exe: str,
    args: list[str] | None = None,
    work_dir: str | None = None,
    timeout: float | None = None,
) -> Target:
    """EXE를 직접 실행하고 최상위 창이 뜰 때까지 기다린다."""
    path = _validate(exe)
    timeout = SETTINGS.timeouts.app_start if timeout is None else timeout
    work_dir = work_dir or str(path.parent)

    cmd = " ".join([f'"{path}"', *(args or [])])
    log.info("실행: %s", cmd)
    log.info("작업 폴더: %s", work_dir)

    try:
        app = Application(backend=SETTINGS.backend).start(
            cmd, work_dir=work_dir, timeout=timeout
        )
    except Exception as exc:
        raise ApplicationError(f"실행 실패: {path.name}: {type(exc).__name__}: {exc}") from exc

    pid = app.process
    log.info("프로세스 시작 (pid=%s). 창 등장 대기 중...", pid)

    try:
        wait_for(lambda: app.windows(), "최상위 창 등장", timeout=timeout)
    except WaitTimeout as exc:
        # 런처형 EXE는 본체를 다른 프로세스로 띄우고 자신은 종료한다.
        # 같은 EXE 경로로 다시 연결되는지 한 번만 확인한다.
        log.warning("시작한 프로세스(pid=%s)에 창이 없다. 같은 EXE로 재연결을 시도한다.", pid)
        # ★ 업데이트가 필요하면 창 대신 업데이터가 먼저 돈다. 그것부터 본다
        #   — 안 보면 "창이 안 뜬다" 로 끝나 원인을 알 수 없다.
        from automation import updater

        if updater.settle(str(path), pid):
            try:
                wait_for(lambda: app.windows(), "업데이트 뒤 창 등장",
                         timeout=timeout)
                return Target(app=app, pid=pid, exe=str(path), started=True)
            except WaitTimeout:
                log.warning("업데이트 뒤에도 이 프로세스에 창이 없다. 재연결한다.")
        fallback = connect(str(path))
        if fallback is not None and fallback.app.windows():
            log.info("재연결 성공: pid=%s", fallback.pid)
            return Target(app=fallback.app, pid=fallback.pid, exe=str(path), started=True)

        raise ApplicationError(
            f"{path.name} 실행 후 {timeout:.0f}초 안에 창이 나타나지 않았다.\n"
            "스플래시 화면이 길거나, 창이 다른 EXE의 프로세스로 뜰 수 있다.\n"
            "업데이트가 필요한 상태라면 [사용자 계정 컨트롤] 창에서 [예] 를 "
            "눌러야 진행된다 (automation/updater.py).\n"
            "tools.survey_windows 로 실제 창과 EXE 경로를 확인할 것."
        ) from exc

    return Target(app=app, pid=pid, exe=str(path), started=True)


def start_new_instance(
    exe: str,
    args: list[str] | None = None,
    work_dir: str | None = None,
    timeout: float | None = None,
    status=None,
    _relaunched: bool = False,
    alert=None,
) -> Target:
    """항상 새 인스턴스를 실행한다.

    이미 실행 중이어도 하나 더 띄운다. 프로그램이 단일 인스턴스만 허용해서
    새 프로세스가 곧바로 종료되면 `AlreadyRunningError`를 낸다.
    `status` 는 업데이트·UAC 안내를 사람에게 보여 줄 통로다 (`updater.settle`). `alert` 는 서버 알림 (10-01).
    """
    path = _validate(exe)
    timeout = SETTINGS.timeouts.app_start if timeout is None else timeout
    work_dir = work_dir or str(path.parent)

    before = pids_for_exe(str(path))
    if before:
        log.info("이미 실행 중인 프로세스: %s. 새 인스턴스를 시도한다.", before)

    cmd = " ".join([f'"{path}"', *(args or [])])
    log.info("실행: %s", cmd)

    try:
        app = Application(backend=SETTINGS.backend).start(
            cmd, work_dir=work_dir, timeout=timeout
        )
    except Exception as exc:
        raise ApplicationError(f"실행 실패: {path.name}: {type(exc).__name__}: {exc}") from exc

    pid = app.process
    log.info("프로세스 시작 (pid=%s). 창 등장 대기 중...", pid)

    def ready():
        windows = app.windows()
        if windows:
            return ("window", windows)
        if not app.is_process_running():
            return ("dead", None)  # 곧바로 죽었다 → 단일 인스턴스일 가능성
        return None

    try:
        kind, _ = wait_for(ready, "새 인스턴스의 창 등장", timeout=timeout)
    except WaitTimeout as exc:
        # ★ 업데이트가 필요하면 창 대신 업데이터의 UAC 동의 창이 먼저 뜬다. `start()` 처럼
        #   여기서 처리한다 — 로그인 단계의 settle 까지 가지 못하고 30초에 실패했다 (09-18 실측).
        from automation import updater

        if not updater.settle(str(path), pid, status=status, alert=alert):
            raise ApplicationError(
                f"{path.name} 실행 후 {timeout:.0f}초 안에 창이 나타나지 않았다.\n"
                "tools.survey_windows 로 실제 창을 확인할 것."
            ) from exc
        try:
            kind, _ = wait_for(ready, "업데이트 뒤 창 등장", timeout=timeout)
        except WaitTimeout as exc2:
            raise ApplicationError(
                f"{path.name} 업데이트 뒤에도 {timeout:.0f}초 안에 창이 나타나지 않았다.\n"
                "tools.survey_windows 로 실제 창을 확인할 것."
            ) from exc2

    if kind == "window":
        log.info("새 인스턴스 실행 완료 (pid=%s)", pid)
        return Target(app=app, pid=pid, exe=str(path), started=True)

    # 시작한 프로세스가 죽었다. 다른 pid로 넘겼는지(런처형) 확인한다.
    log.warning("시작한 프로세스(pid=%s)가 종료됐다. 다른 프로세스로 넘겼는지 확인한다.", pid)

    def handed_over():
        after = [p for p in pids_for_exe(str(path)) if p not in before]
        return after[0] if after else None

    try:
        # ★ 업데이터가 다시 띄운 인스턴스는 원래 프로세스가 죽고 1초쯤 **뒤에** 생긴다 (09-23 실측).
        #   한 번만 보면 놓쳐 "곧바로 종료됐다" 로 끝난다.
        new_pid = wait_for(handed_over, "인계된 프로세스 등장", timeout=SETTINGS.timeouts.handover)
    except WaitTimeout:
        new_pid = None
    if new_pid:
        if is_elevated(new_pid) and not is_elevated():
            # ★ 업데이터(관리자 권한)가 다시 띄운 인스턴스는 **관리자 권한**이라 일반 권한 RPA 가
            #   조작할 수 없다 (UIPI, 09-18 실측). 업데이트는 끝났으니 **한 번만** 새로 띄운다.
            text = (f"업데이트 뒤 다시 뜬 {path.name}(pid={new_pid})는 관리자 권한이라 "
                    "RPA 가 쓸 수 없다. 그 로그인 창은 사람이 닫아 주세요.")
            log.warning(text)
            if status:
                # 화면에는 pid·파일 이름 없이 (사용자 요청 09-21)
                status("업데이트 뒤 다시 켜진 ERPia는 관리자 권한이라 RPA가 쓸 수 없습니다. "
                       "RPA가 한 번 더 켭니다 — 먼저 켜진 ERPia 로그인 창은 닫아 주세요.")
            if _relaunched:
                raise ApplicationError(f"{text}\n다시 띄워도 관리자 권한 인스턴스만 생겼다.")
            return start_new_instance(exe, args, work_dir, timeout, status, _relaunched=True, alert=alert)
        log.info("새 프로세스 발견: pid=%s. 연결한다.", new_pid)
        handed = Application(backend=SETTINGS.backend).connect(process=new_pid, timeout=timeout)
        wait_for(lambda: handed.windows(), "인계된 프로세스의 창 등장", timeout=timeout)
        return Target(app=handed, pid=new_pid, exe=str(path), started=True)

    if before:
        raise AlreadyRunningError(str(path), before)

    raise ApplicationError(
        f"{path.name} 을(를) 실행했으나 프로세스가 곧바로 종료됐다.\n"
        "실행 인자나 작업 폴더가 필요한 프로그램일 수 있다."
    )


def logged_in_pids(company: str, user_id: str) -> list[int]:
    """이 PC 에서 그 업체코드·아이디로 **로그인된** ERPia 의 pid. 읽기만 한다 (예약 전 경고, 10-01).

    판정은 `close_others` 와 같은 메인 창 제목(`업체코드.*아이디`)이다. 프로세스 목록 스냅샷이라
    프로세스를 열지 않는다 — 관리자 권한 인스턴스도 보인다 (`pids_for_exe` 는 놓친다).
    """
    if not company or not user_id or not exe_name():
        return []
    title_re = f"{re.escape(company)}.*{re.escape(user_id)}"
    return [pid for pid in pids_by_name(exe_name())
            if any(re.search(title_re, w.title) for w in winprobe.top_windows(pid, visible_only=False))]


def close_others(exe: str, keep_pid: int | None, title_re: str) -> list[int]:
    """`exe` 로 떠 있는 다른 인스턴스 중 **같은 계정으로 로그인된 것**만 닫는다. 닫은 PID 목록.

    같은 아이디로 동시 로그인을 서버가 막기 때문에, 중복 로그인 팝업이 뜨면
    **기존 인스턴스를 닫고 다시 로그인한다** (사용자 확정 2026-09-04).

    `keep_pid` (지금 우리가 띄운 인스턴스)는 건드리지 않는다. 메인 창 제목이 `title_re`
    (`업체코드.*아이디`)에 안 맞는 인스턴스도 건드리지 않는다 — 사람이 **다른 계정으로 쓰던**
    ERPia 까지 닫던 결함 (09-28 코드 검토).
    """
    closed: list[int] = []
    for pid in pids_for_exe(exe):
        if keep_pid is not None and pid == keep_pid:
            continue
        # 제목은 Win32 로 읽는다 — 관리자 권한 인스턴스도 여기까지는 읽힌다
        titles = [w.title for w in winprobe.top_windows(pid, visible_only=False)]
        if not any(re.search(title_re, t) for t in titles):
            log.info("다른 계정이거나 로그인 전이라 닫지 않는다 (pid=%s, 창 %s)", pid, titles)
            continue
        try:
            app = Application(backend=SETTINGS.backend).connect(process=pid)
        except Exception as exc:
            # **가장 흔한 원인은 권한이다.** 관리자 권한으로 뜬 인스턴스는 UIPI 때문에
            # 일반 권한 프로세스에서 창이 보이지도, 닫히지도 않는다. 그런데 서버에는
            # 로그인돼 있어서 **이후 모든 실행이 중복 로그인으로 막힌다.**
            # 원인을 안 남기면 "닫지 못했다"만 보고 계정 문제로 오해하게 된다
            # (2026-09-08에 실제로 그랬다).
            if is_elevated(pid) and not is_elevated():
                log.error(
                    "기존 인스턴스(pid=%s)가 **관리자 권한**이라 닫을 수 없다.\n"
                    "  RPA 는 일반 권한이라 UIPI 로 막힌다. 그 인스턴스가 로그인돼 있으면\n"
                    "  이후 실행이 계속 중복 로그인으로 실패한다.\n"
                    "  해당 창을 직접 닫거나, RPA exe 를 관리자 권한으로 실행할 것.",
                    pid,
                )
            else:
                log.warning("기존 인스턴스(pid=%s)에 연결하지 못했다: %s", pid, exc)
            continue

        target = Target(app=app, pid=pid, exe=exe, started=False)
        if target.close():
            closed.append(pid)

    if closed:
        log.info("기존 인스턴스 %d개를 닫았다: %s", len(closed), closed)
        wait_gone(exe, closed)
    else:
        log.warning("닫을 다른 인스턴스를 찾지 못했다.")
    return closed


def wait_gone(exe: str, pids: list[int], timeout: float | None = None) -> list[int]:
    """`pids` 가 **OS 프로세스 목록에서** 사라질 때까지 기다린다. 남은 pid 를 돌려준다.

    `Target.close()` 도 기다리지만 그쪽은 pywinauto 의 `is_process_running()`
    기준이다. 그것이 True 를 준 뒤에도 **프로세스 목록에는 잠시 더 남는다**
    (2026-09-08 소크: 종료 직후 잔여 1개, 잠시 뒤 0개).

    다음 실행이 같은 아이디로 로그인할 수 있는지는 **목록 기준**으로 봐야 한다.
    아직 살아 있는 인스턴스가 있으면 `동시에 같은 아이디로 로그인할 수 없습니다`
    로 막히기 때문이다.
    """
    if not pids:
        return []
    timeout = SETTINGS.timeouts.window if timeout is None else timeout
    remaining = list(pids)

    def gone() -> bool:
        nonlocal remaining
        alive = set(pids_for_exe(exe))
        remaining = [pid for pid in pids if pid in alive]
        return not remaining

    try:
        wait_for(gone, f"인스턴스 {pids} 종료 확인", timeout=timeout)
        log.info("인스턴스 %d개가 프로세스 목록에서 사라졌다.", len(pids))
    except WaitTimeout:
        # 조용히 넘어가면 다음 실행이 중복 로그인으로 실패하고 원인을 알 수 없다.
        log.warning("%.0f초 안에 종료되지 않은 인스턴스가 있다: %s", timeout, remaining)
    return remaining
