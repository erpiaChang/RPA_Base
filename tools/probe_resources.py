r"""[개발 전용 / 읽기 전용] CPU·메모리·핸들 사용량을 샘플링한다.

RPA 는 오래 켜두고 반복 실행할 프로그램이라 **누수가 있으면 며칠 뒤에 죽는다.**
그 전에 숫자로 확인한다. 화면을 조작하지 않는다.

    # 다른 콘솔에서 도는 흐름을 옆에서 지켜본다
    .venv\Scripts\python.exe -m tools.probe_resources --seconds 300

    # 코드 안에서 구간을 감싼다
    from tools.probe_resources import ResourceMonitor
    with ResourceMonitor() as res:
        ...업무 흐름...
    res.report()

빌드에는 포함되지 않는다(`build_run.spec` 의 `DEV_ONLY`).

## 무엇을 보는가

| 항목 | 왜 |
| --- | --- |
| `rss` 워킹셋 | 눈에 보이는 메모리. 다만 OS 가 회수해 가므로 누수 판정에는 약하다 |
| `private` | **누수 판정의 기준.** 이 프로세스만 쓰는 실제 메모리 |
| `handles` | **COM/UIA 누수는 메모리보다 핸들에 먼저 나타난다** |
| `threads` | pywinauto/comtypes 가 스레드를 남기는지 |
| `gdi` / `user` | GUI 오브젝트. 기본 상한 10,000 개라 새면 창이 안 그려지고 죽는다 |

Windows 에는 PSS 가 없다. 그래서 `private` 를 쓴다.

## 대상

- **RPA** — 이 프로세스와 그 자식 전부 (자식 프로세스로 흐름을 돌릴 때 대비)
- **대상 프로그램** (설정 `target_exe_name`) — 우리 조작이 저쪽 사용량을 밀어 올리는지 본다

프로세스가 도중에 뜨거나 죽어도 샘플링은 계속한다. 회차마다 새로 띄우는 구조를
쓸 예정이므로 **이름으로 매번 다시 찾는다.**
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil  # noqa: E402

from utils.logger import get_logger  # noqa: E402

log = get_logger("probe_resources")


def _target_names() -> tuple[str, ...]:
    """감시할 대상 프로그램 — 설정 `target_exe_name` (실행 파일 이름은 git 에 두지 않는다, 10-02)."""
    from automation.application import exe_name

    name = exe_name()
    return (name,) if name else ()

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except OSError as exc:
            # 파이프로 넘길 때 실패할 수 있다. 출력 인코딩만 영향받으므로 계속 간다.
            print(f"[경고] 출력 인코딩 설정 실패: {exc}", file=sys.stderr)

MB = 1024 * 1024
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GR_GDIOBJECTS = 0
_GR_USEROBJECTS = 1


def gui_objects(pid: int) -> tuple[int, int]:
    """GDI / USER 오브젝트 수. psutil 에 없어서 직접 부른다. 실패하면 (0, 0)."""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0, 0
    try:
        return (user32.GetGuiResources(handle, _GR_GDIOBJECTS),
                user32.GetGuiResources(handle, _GR_USEROBJECTS))
    finally:
        kernel32.CloseHandle(handle)


@dataclass
class Sample:
    at: float
    group: str
    procs: int
    cpu: float
    rss: float
    private: float
    handles: int
    threads: int
    gdi: int
    user: int


@dataclass
class Group:
    """한 묶음(RPA / 대상 프로그램)의 추이."""

    name: str
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    def _series(self, field_name: str) -> list[float]:
        return [getattr(s, field_name) for s in self.samples if s.procs]

    def summary(self) -> dict:
        live = [s for s in self.samples if s.procs]
        if not live:
            return {}
        out: dict = {"샘플": len(live), "최대 동시 프로세스": max(s.procs for s in live)}
        for label, key, unit in (
            ("CPU%", "cpu", ""), ("워킹셋MB", "rss", ""), ("privateMB", "private", ""),
            ("핸들", "handles", ""), ("스레드", "threads", ""),
            ("GDI", "gdi", ""), ("USER", "user", ""),
        ):
            series = self._series(key)
            out[label] = {
                "처음": round(series[0], 1),
                "최대": round(max(series), 1),
                "평균": round(sum(series) / len(series), 1),
                "마지막": round(series[-1], 1),
                "증감": round(series[-1] - series[0], 1),
            }
            del unit
        return out


class ResourceMonitor:
    """백그라운드 스레드로 주기 샘플링. `with` 로 감싸거나 start/stop 한다."""

    def __init__(self, interval: float = 0.5, root_pid: int | None = None,
                 names: tuple[str, ...] | None = None,
                 rescan: float = 3.0) -> None:
        self.interval = interval
        # 대상 목록 갱신 주기. `process_iter()` 는 프로세스 전체를 훑어서 비싸다.
        # 매 샘플마다 돌렸더니 **샘플러 자신이 CPU 13%** 를 썼다 (2026-09-08 실측).
        # 측정 대상보다 측정 도구가 더 튀면 안 되므로 목록만 따로 갱신한다.
        self.rescan = rescan
        self._targets_cache: dict[str, list[psutil.Process]] = {}
        self._rescanned = 0.0
        self.root_pid = root_pid or os.getpid()
        self.names = tuple(n.lower() for n in (_target_names() if names is None else names))
        self.groups: dict[str, Group] = {}
        # 그룹에 실제로 무엇이 들어갔는지. 섞이면 수치가 통째로 틀리므로 보고에 찍는다.
        self.members: dict[str, set[str]] = {}
        self.marks: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._seen: dict[int, psutil.Process] = {}

    # ------------------------------------------------------------ 수집
    def _targets(self) -> dict[str, list[psutil.Process]]:
        now = time.perf_counter()
        if self._targets_cache and now - self._rescanned < self.rescan:
            # 캐시된 목록에서 죽은 것만 걸러 쓴다. 새로 뜬 것은 다음 갱신에 잡힌다.
            return {name: [p for p in procs if p.is_running()]
                    for name, procs in self._targets_cache.items()}
        self._rescanned = now

        found: dict[str, list[psutil.Process]] = {"RPA": [], }
        named: set[int] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info["name"] or "").lower()
            except psutil.Error:
                continue
            if name in self.names:
                found.setdefault(proc.info["name"], []).append(proc)
                named.add(proc.pid)
                # **하위 트리째 뺀다.** ERPia 는 이름이 다른 자식 프로세스를 띄운다.
                # 프로세스 이름만 걸렀더니 RPA 쪽에 528MB / 스레드 197 이 남았다
                # (2026-09-08 실측). 그 자식들은 ERPia 것이지 우리 것이 아니다.
                try:
                    named.update(child.pid for child in proc.children(recursive=True))
                except psutil.Error as exc:
                    log.debug("자식 프로세스 조회 실패(pid=%s): %s", proc.pid, exc)
        try:
            root = psutil.Process(self.root_pid)
            # **대상 프로그램을 RPA 에서 뺀다.** `start_new_instance()` 가 ERPia 를
            # 파이썬의 자식으로 띄우기 때문에, 그냥 자식을 다 더하면 ERPia 가
            # RPA 사용량에 통째로 섞인다 (2026-09-08: 워킹셋 892MB / 스레드 200개로
            # 잡혔다). 우리 코드의 사용량을 보려면 반드시 갈라야 한다.
            found["RPA"] = [p for p in [root] + root.children(recursive=True)
                            if p.pid not in named]
            self.members.setdefault("RPA", set()).update(
                f"{p.name()}({p.pid})" for p in found["RPA"])
        except psutil.Error as exc:
            # 감시 대상이 이미 죽었다. 샘플링은 계속한다(프로세스 수 0으로 기록된다).
            log.debug("RPA 프로세스(%s) 조회 실패: %s", self.root_pid, exc)
        self._targets_cache = found
        return found

    def _measure(self, group: str, procs: list[psutil.Process]) -> Sample:
        cpu = rss = private = 0.0
        handles = threads = gdi = user = 0
        alive = 0
        for proc in procs:
            try:
                # cpu_percent 는 **같은 객체로 두 번째 호출부터** 값이 나온다.
                # 그래서 프로세스 객체를 캐시해 재사용한다.
                cached = self._seen.setdefault(proc.pid, proc)
                cpu += cached.cpu_percent(None)
                info = cached.memory_info()
                rss += info.rss / MB
                private += getattr(info, "private", info.rss) / MB
                handles += cached.num_handles()
                threads += cached.num_threads()
                one_gdi, one_user = gui_objects(proc.pid)
                gdi += one_gdi
                user += one_user
                alive += 1
            except psutil.Error:
                self._seen.pop(proc.pid, None)
        return Sample(time.perf_counter() - self._started, group, alive, cpu,
                      rss, private, handles, threads, gdi, user)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for group, procs in self._targets().items():
                sample = self._measure(group, procs)
                self.groups.setdefault(group, Group(group)).add(sample)
            self._stop.wait(self.interval)

    # ------------------------------------------------------------ 제어
    def start(self) -> "ResourceMonitor":
        self._started = time.perf_counter()
        self._thread = threading.Thread(target=self._loop, name="resmon", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> "ResourceMonitor":
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 4)
        return self

    def mark(self, label: str) -> None:
        """구간 표시. CSV 를 볼 때 어느 단계였는지 알 수 있게."""
        self.marks.append((time.perf_counter() - self._started, label))

    def __enter__(self) -> "ResourceMonitor":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # ------------------------------------------------------------ 보고
    def report(self) -> None:
        print("=" * 92)
        print(" 자원 사용량")
        for name, group in self.groups.items():
            summary = group.summary()
            if not summary:
                print(f"\n [{name}] 실행된 적 없음")
                continue
            print(f"\n [{name}] 샘플 {summary['샘플']}개 / "
                  f"최대 동시 {summary['최대 동시 프로세스']}개")
            members = sorted(self.members.get(name, ()))
            if members:
                print(f"   포함: {', '.join(members)}")
            print(f"   {'항목':<12} {'처음':>9} {'최대':>9} {'평균':>9} "
                  f"{'마지막':>9} {'증감':>9}")
            for label in ("CPU%", "워킹셋MB", "privateMB", "핸들", "스레드", "GDI", "USER"):
                row = summary[label]
                print(f"   {label:<12} {row['처음']:>9} {row['최대']:>9} "
                      f"{row['평균']:>9} {row['마지막']:>9} {row['증감']:>+9}")
        if self.marks:
            print("\n 구간 표시")
            for at, label in self.marks:
                print(f"   {at:>7.1f}초  {label}")
        print("=" * 92)

    def save_csv(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(
            (s for group in self.groups.values() for s in group.samples),
            key=lambda s: s.at,
        )
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["초", "대상", "프로세스수", "CPU%", "워킹셋MB",
                             "privateMB", "핸들", "스레드", "GDI", "USER"])
            for s in rows:
                writer.writerow([f"{s.at:.1f}", s.group, s.procs, f"{s.cpu:.1f}",
                                 f"{s.rss:.1f}", f"{s.private:.1f}", s.handles,
                                 s.threads, s.gdi, s.user])
        return path


def _cli() -> int:
    parser = argparse.ArgumentParser(description="자원 사용량 샘플링 (읽기 전용)")
    parser.add_argument("--seconds", type=float, default=60.0, help="샘플링 시간")
    parser.add_argument("--interval", type=float, default=0.5, help="샘플 간격(초)")
    parser.add_argument("--attach", type=int, default=None,
                        help="이 PID 와 그 자식을 'RPA' 로 본다. 기본은 자기 자신")
    parser.add_argument("--name", action="append", default=None,
                        help="이름으로 감시할 프로세스. 여러 번 줄 수 있다")
    parser.add_argument("--csv", default=None, help="CSV 저장 경로")
    args = parser.parse_args()

    names = tuple(args.name or _target_names())
    monitor = ResourceMonitor(interval=args.interval, root_pid=args.attach, names=names)
    print(f"샘플링 {args.seconds:.0f}초 / 간격 {args.interval}초 / "
          f"대상 RPA(pid={monitor.root_pid}) + {', '.join(names)}")
    with monitor:
        try:
            # UI 대기가 아니라 **샘플링 지속 시간**이다. 조건 대기로 바꿀 대상이 아니다.
            threading.Event().wait(args.seconds)
        except KeyboardInterrupt:
            print("\n중단됨. 지금까지의 결과를 낸다.")
    monitor.report()
    path = args.csv or f"logs/res_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    print(f"CSV: {monitor.save_csv(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
