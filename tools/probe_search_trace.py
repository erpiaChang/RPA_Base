r"""[개발 전용 / 읽기 전용] 컨트롤 탐색을 집계한다. **무엇을 몇 번, 몇 초 찾는지.**

이 프로그램은 UIA 트리가 커서 전체 시간의 대부분이 탐색이다
(`docs/CONTROLS.md` — 컨트롤 탐색이 느린 이유와 해결).
캐시를 넣기 전에 **같은 것을 정말 반복해서 찾는지**를 숫자로 확인한다.

    from tools.probe_search_trace import SearchTrace
    with SearchTrace() as trace:
        ...업무 흐름...
    trace.report()

`utils/ui.py` 의 `trace_hook` 에 꽂아서 동작한다. 화면을 조작하지 않는다.
빌드에는 포함되지 않는다(`build_run.spec` 의 `DEV_ONLY`).

## 캐시 후보 / 위험 판정

RPA 를 쓰는 환경이 하나가 아니다. 해상도·DPI 가 다르고, 같은 화면이라도
**컨트롤이 한두 개 더 있거나 없을 수 있다.** 그래서 단순히 "느린 것"이 아니라
**환경이 달라져도 하나로 확정되는 조건**만 캐시 후보로 본다.

| 판정 | 조건 |
| --- | --- |
| 캐시 후보 | 2회 이상 찾았고, **매번 정확히 1개**가 잡혔다 |
| 캐시 위험 | 일치 개수가 2개 이상이거나 호출마다 달랐다 → 환경/화면 상태에 흔들린다 |
| 못 찾음 | 0개가 나온 적이 있다 → 대기·재시도 경로. 캐시 대상이 아니다 |
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils import ui


def _key(criteria: dict) -> str:
    """조건을 한 줄로. 같은 조건을 같은 키로 묶는다."""
    return ", ".join(f"{k}={criteria[k]!r}" for k in sorted(criteria))


@dataclass
class Entry:
    kind: str
    criteria: str
    calls: int = 0
    total: float = 0.0
    worst: float = 0.0
    matches: set[int] = field(default_factory=set)
    callers: dict[str, int] = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return self.total / self.calls if self.calls else 0.0

    @property
    def verdict(self) -> str:
        if -1 in self.matches:
            return "탐색실패"
        if 0 in self.matches:
            return "못찾음"
        if self.matches != {1}:
            # 환경에 따라 컨트롤 개수가 흔들리는 조건이다. 캐시하면 조용히 틀린다.
            return f"위험(일치 {sorted(self.matches)})"
        return "캐시후보" if self.calls >= 2 else "1회"


class SearchTrace:
    """`utils.ui` 의 탐색을 집계한다. `with` 블록 안에서만 켜진다."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], Entry] = {}
        self.started = 0.0
        self.elapsed = 0.0
        self._previous = None

    # ------------------------------------------------------------ 수집
    def __call__(self, kind: str, criteria: dict, elapsed: float,
                 matches: int, caller: str) -> None:
        key = (kind, _key(criteria))
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = Entry(kind=kind, criteria=key[1])
        entry.calls += 1
        entry.total += elapsed
        entry.worst = max(entry.worst, elapsed)
        entry.matches.add(matches)
        entry.callers[caller] = entry.callers.get(caller, 0) + 1

    def __enter__(self) -> "SearchTrace":
        self._previous = ui.trace_hook
        ui.trace_hook = self
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        self.elapsed = time.perf_counter() - self.started
        ui.trace_hook = self._previous

    # ------------------------------------------------------------ 보고
    def rows(self) -> list[Entry]:
        return sorted(self.entries.values(), key=lambda e: e.total, reverse=True)

    def totals(self) -> dict:
        rows = self.rows()
        # `find` 는 내부적으로 `search_shallow` 를 부른다. 합계를 두 번 세지 않도록
        # 층별로 나눠서 낸다.
        by_kind: dict[str, tuple[int, float]] = {}
        for row in rows:
            calls, total = by_kind.get(row.kind, (0, 0.0))
            by_kind[row.kind] = (calls + row.calls, total + row.total)
        return {
            "구간(초)": round(self.elapsed, 1),
            "조건 종류": len(rows),
            "층별": {k: (c, round(t, 1)) for k, (c, t) in sorted(by_kind.items())},
        }

    def report(self, limit: int = 25) -> None:
        rows = self.rows()
        print("=" * 100)
        print(f" 컨트롤 탐색 집계 — 구간 {self.elapsed:.1f}초, 조건 {len(rows)}종")
        by_kind = self.totals()["층별"]
        for kind, (calls, total) in by_kind.items():
            share = total / self.elapsed * 100 if self.elapsed else 0
            print(f"   {kind:<16} {calls:>5}회  {total:>7.1f}초  (구간의 {share:.0f}%)")
        print("-" * 100)
        print(f" {'층':<14} {'총초':>6} {'회':>4} {'평균':>6} {'최대':>6}  "
              f"{'판정':<14} 조건")
        for row in rows[:limit]:
            print(f" {row.kind:<14} {row.total:>6.2f} {row.calls:>4} "
                  f"{row.mean:>6.2f} {row.worst:>6.2f}  {row.verdict:<14} {row.criteria}")
        if len(rows) > limit:
            print(f" ... {len(rows) - limit}종 생략")

        cache = [r for r in rows if r.verdict == "캐시후보"]
        risky = [r for r in rows if r.verdict.startswith("위험")]
        saved = sum(r.total - r.mean for r in cache)
        print("-" * 100)
        print(f" 캐시 후보 {len(cache)}종 — 두 번째부터 재사용하면 최대 {saved:.1f}초 절약")
        for row in cache[:10]:
            print(f"    {row.total - row.mean:>6.2f}초  {row.calls}회  {row.criteria}")
        if risky:
            print(f" 캐시 위험 {len(risky)}종 — 일치 개수가 환경/상태에 따라 흔들린다")
            for row in risky[:10]:
                print(f"    {row.verdict:<18} {row.criteria}")
        print("=" * 100)

    def save_csv(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["층", "조건", "호출수", "총초", "평균초", "최대초",
                             "일치개수", "판정", "호출자"])
            for row in self.rows():
                top = sorted(row.callers.items(), key=lambda kv: -kv[1])[:3]
                writer.writerow([
                    row.kind, row.criteria, row.calls, f"{row.total:.3f}",
                    f"{row.mean:.3f}", f"{row.worst:.3f}",
                    "|".join(str(m) for m in sorted(row.matches)),
                    row.verdict,
                    " ".join(f"{name}({count})" for name, count in top),
                ])
        return path
