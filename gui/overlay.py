r"""대상 프로그램 위에 떠 있는 **진행 상황 오버레이.**

대상 프로그램은 최대화돼 화면을 다 덮는다. 그래서 RPA 창은 뒤로 숨고, 사람은
지금 무엇이 돌고 있는지 볼 수가 없다. 이 창은 **화면 전체**에 떠서 단계를
큰 칸으로 보여 준다.

    ┌──────────────────────────────────────────────────────────┐
    │ 2/4 둘째 묶음 · 세부 단계 [펼치기][숨기기][일시정지][상세][중단]│
    │ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░                             │
    │ ┌──────────┐→┌──────────┐→┌──────────┐→┌──────────┐      │
    │ │● 첫째    │ │◆ 둘째    │ │○ 셋째    │ │○ 넷째    │      │
    │ │완료 9:17 │ │진행 12초 │ │대기      │ │대기      │      │
    │ │그 결과   │ │지금 진행 │ │          │ │          │      │
    │ └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
    └──────────────────────────────────────────────────────────┘
       ↑ 여기는 클릭이 통과한다              ↑ 이 두 개만 눌린다

## ★ 왜 창이 둘인가

창은 **통째로** 클릭이 통과하거나 아니거나다. 둘을 한 창에 넣을 수 없다.

| 창 | 클릭 | 이유 |
| --- | --- | --- |
| 상태 표시 | **통과시킨다** | 화면 전체다. 통과시키지 않으면 그 아래를 못 누른다 |
| 조작 막대 | 통과시키지 않는다 | 사람이 눌러야 한다 |

`ui.click` 의 주력 수단이 `click_input()` = **실제 마우스로 그 좌표를 누르는
것**이라, 보통 창을 위에 띄우면 그 클릭이 우리 창으로 간다. 그래서 상태 표시
창에는 이 조합을 건다 (`tools/probe_overlay.py` 로 확인했다).

    WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

- `TRANSPARENT` — 마우스가 통과한다
- `NOACTIVATE` — 포커스를 뺏지 않는다. 뺏으면 키 입력이 엉뚱한 곳으로 간다
- `TOOLWINDOW` — 작업표시줄에 뜨지 않는다

## ★ `WS_EX_LAYERED` 는 **뿌리 창에만** 건다 (2026-09-16)

Tk 는 창 하나를 HWND 두 개로 만든다(내용 창 + 뿌리 창). 나머지 세 스타일은
둘 다에 걸어야 하지만, `LAYERED` 까지 내용 창에 걸면 **그 창은 자기 레이어드
표면을 갖는데 아무도 갱신하지 않아** 글자가 사라지고 배경색 상자만 남는다.
뿌리 창은 tkinter 의 `-alpha` 가 이미 레이어드로 만들어 갱신하고 있다.

스타일 검사로는 잡히지 않는다 — 스타일은 멀쩡하다. 그래서
`tools/probe_overlay.py` 가 **화면을 찍어 색이 몇 가지인지** 센다.

## ★ 조작 막대는 비켜 준다

작지만 통과시키지 않으므로, 마침 그 자리를 자동화가 누르려 하면 **우리 단추가
눌린다.** 그래서 `ui.set_click_guard()` 로 "여기를 누를 것이다" 를 미리 받아
겹치면 **스스로 다른 모서리로 옮긴다.**

막대도 `NOACTIVATE` 다. 눌러도 포커스를 가져가지 않아야 조작이 끊기지 않는다.

## ★ 화면을 덮는 양 — 카드 크기만큼만 (2026-09-16 저녁)

처음에는 화면 전체를 덮고 **배경색을 색 키로 비웠다.** 그런데 그 구조는
두 가지가 나빴다.

1. 판을 가진 것(제목·표)만 읽히고 **Stepper 글자는 대상 프로그램 위에 그대로
   얹혀** 읽히지 않았다
2. 카드 색이 우연히 색 키와 같아지면 **카드가 통째로 사라진다.** 실제로 그렇게
   만들어 화면에 글자가 하나도 안 보였다

지금은 **창을 카드 크기로만** 잡고 그 안을 칠한다. 색 키 투명은 쓰지 않는다.

| 밀도 | 크기 | 덮는 양 |
| --- | --- | --- |
| HUD | 약 300x170 | 화면의 2.5% |
| 펼침 | 최대 1080x740, 가운데 | 화면의 약 38% |

카드 밖은 창이 없으므로 대상 프로그램이 **그대로 보인다.**

## ★ 단축키를 쓰지 않는다 (사용자 확정 2026-09-16)

전역 단축키(`RegisterHotKey`)를 넣었다가 **뺐다.** RPA 는 키보드를 직접
두드리는 프로그램이라, 어떤 경로로든 그 조합이 눌리면 **실행이 중간에
끊긴다.** RPA 에서 가장 중요한 것은 끝까지 완수하는 것이다.

그래서 조작은 **막대의 단추로만** 한다. 대신 막대는

- **자리가 고정**이다 (오른쪽 맨 위). 접고 펼쳐도 움직이지 않는다
- 자동화가 그 좌표를 누르려 할 때만 잠깐 비키고, 다음 갱신에 **제자리로
  돌아온다**

## ★ 숨기기

화면 전체를 덮으므로 **뒤를 봐야 할 때가 있다.** [숨기기] 는 상태 띠만
감추고 조작 막대는 남긴다 — 다시 켤 곳이 없으면 안 되기 때문이다.
"""
from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes
from types import SimpleNamespace

from gui.pipeline import (DARK, FULL, HUD, CurrentView, PipelineView,
                          TableView, place)
from gui.common import RecentLog, work_area
from utils import ui
from utils.logger import get_logger

log = get_logger(__name__)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

_user32 = ctypes.windll.user32

# ★ 인자 형을 밝혀 둔다. 밝히지 않으면 ctypes 가 파이썬 int 를 **32비트**로
#   넘겨, 64비트 창 핸들과 `HWND_TOPMOST(-1)` 이 잘린다. 잘린 -1 은
#   0xFFFFFFFF 이 되어 맨 앞으로 올리라는 뜻이 되지 않는다 —
#   오류도 나지 않고 **조용히 아무 일도 하지 않는다** (2026-09-16에 겪었다).
_user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND,
                                 ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint)
_user32.SetWindowPos.restype = wintypes.BOOL
HWND_TOPMOST = wintypes.HWND(-1)

# 단추가 클릭 지점과 이만큼 안에 있으면 겹친 것으로 보고 비킨다.
AVOID_PADDING = 8

# --- 조작 막대의 치수 -----------------------------------------------------
#
# ★ **픽셀을 박아 두지 않고 글꼴에서 잰다** (2026-09-16).
#
# 전에는 단추마다 `width=9 / 8 / 10 / 7 / 7` 이었다. tk 의 `width` 는 **글자 수**
# 단위라 실제 폭이 제각각이었고, 글자가 바뀌면(중단↔닫기, 펼치기↔접기) 필요한
# 폭도 바뀌는데 지정은 고정이라 [닫기]는 37px 남아돌고 [일시정지]는 4px
# 모자랐다. 그 결과 글귀에 **31~91px** 밖에 안 남아 현재 단계가 잘렸다
# (필요한 자리는 139~166px).
#
# 그리고 고정 픽셀은 **고배율 DPI 에서 깨진다.** 150%/200% 에서 글자는 커지는데
# 막대는 그대로라 더 심하게 잘린다. 그래서 전부 `font.measure()` 로 잰다.
BTN_GAP = 4          # 같은 무리 안 단추 사이
# ★ 무리를 가르는 빈자리. **보기 옵션 | 실행 제어 | 중단** 셋으로 나눈다.
#   [일시정지] 를 누르려다 [중단] 을 누르는 사고를 자리로 막는다
#   (외부 검토 의견 2026-09-16).
GROUP_GAP = 12
BTN_PAD = 14         # 단추 글자 좌우 여백
BAR_FRAME = 22       # inner padx + 테두리 + 글귀 좌우 여백

# 단추에 **나올 수 있는 모든 글자.** 자리를 여기 맞춰야 글자가 바뀌어도
# 단추가 움직이지 않는다 — 움직이는 단추는 이미 한 번 지적받았다.
BTN_LABELS = ("펼치기", "접기", "숨기기", "보이기",
              "일시정지", "계속하기", "상세", "중단", "닫기")
# 도는 실행을 멈추는 단추 (09-29 사용자 요청 — 예전 이름 [잠깐 멈춤]/[이어 하기]). 실행 창 [홈] 의 단추와 같다.
# 예약 탭의 [일시정지] 는 예약을 멈춘다 — 실행 중에는 그 탭이 잠겨 한 화면에 같이 보이지 않는다
PAUSE_TEXT, RESUME_TEXT = "일시정지", "계속하기"

# 막대 글귀가 잘리지 않아야 하는 **가장 긴 경우.**
#
# ★ 처음에는 `"11/11 운송장출력 — 진행 중"` 으로 쟀는데 **코드가 만들지 않는
#   문장**이었다. 실제 형식은 `"{번호}/{전체} {단계이름}"` 이고 멈추면 앞에
#   표식이 붙는다. 견본을 잘못 잡으면 자리를 과소평가해 그대로 잘린다.
#
# 아래는 지금 단계 표에서 실제로 나올 수 있는 가장 긴 문장이다
# (`orchestrator/steps_collect.py` 의 `웹메일 로그인·2차 인증`).
# 단계 이름이 더 길어지면 이 값도 같이 늘린다 —
# `tools/probe_overlay.py` 가 **실제 단계 표와 대조**해 어긋나면 알려 준다.
BAR_SAMPLE = "⏸ 2/3 웹메일 로그인·2차 인증"

# 글귀 자리에 이만큼은 더 둔다. 딱 맞게 잡으면 글꼴이 조금만 달라져도 잘린다.
BAR_SLACK = 12

# --- 색 -----------------------------------------------------------------
#
# ★ 창이 카드 크기뿐이라 **여기 칠한 만큼만** 화면을 덮는다.
#   색 키 투명은 쓰지 않는다 — 카드 색이 그 키와 같아지면 카드가 통째로
#   사라진다(2026-09-16에 실제로 그렇게 만들어 글자가 하나도 안 보였다).
CARD = "#121b26"        # 카드 바탕
EDGE = "#4e6981"        # 카드 테두리
PANEL = "#16202b"       # 결과 띠처럼 카드 안에서 한 단 올라오는 판
FOREGROUND = "#eef3f8"
MUTED = "#9dabb9"

# 조작 막대. 카드 위에 앉으므로 **카드와 가까운 어두운 색**을 쓴다 —
# 흰 띠는 어두운 화면에서 너무 튀어 시선을 단추로 끌어간다.
#
# ★ **단추 바탕은 막대 바탕과 3:1 이상이어야 한다** (2026-09-16 계산).
#   전에는 [숨기기] 가 1.30:1, [중단] 이 2.51:1 이라 **단추가 단추로 보이지
#   않았다.** 사용자가 "중단 숨기기가 보이지 않는다" 고 지적한 것의 실제
#   원인이 이 숫자였다. 글자 대비만 보고 "잘 보인다" 고 판단했던 것이 틀렸다 —
#   글자는 5~9:1 로 멀쩡했고, 문제는 **단추 면이 배경에 잠긴** 것이었다.
BAR_BG = "#1a2530"
BAR_EDGE = "#567088"
STOP_BG = "#c53b3b"     # 막대 대비 3.01:1 / 글자 대비 5.17:1
STOP_FG = "#ffffff"
HIDE_BG = "#54708c"     # 막대 대비 3.01:1
# ★ 흰색이다. `#dfe7ef` 로는 글자 대비가 4.13:1 이라 모자랐다. 단추 바탕을
#   더 어둡게 해서 맞추려 하면 이번엔 **막대 대비 3:1** 이 깨진다 — 두 조건을
#   명도 하나로 동시에 만족시킬 수 없어서 글자를 올렸다.
HIDE_FG = "#ffffff"     # 글자 대비 5.16:1
# 못 누르는 단추의 글자. 바탕(HIDE_BG)과 3:1 이상이라 읽힌다.
DISABLED_FG = "#c3d0dd"

GA_ROOT = 2


def _hwnd(window: tk.Misc) -> int:
    """tkinter 창의 **진짜** 최상위 핸들. 없으면 0.

    ★ `GetParent()` 를 쓰면 안 된다 (2026-09-15에 그렇게 썼다가 겪었다).
      `overrideredirect(True)` 창은 `WS_POPUP` 이고, 팝업 창에 대해 `GetParent`
      는 부모가 아니라 **소유자**를 돌려준다 — 여기서는 본 창이다.
      그러면 확장 스타일이 **본 창에** 걸린다. 오버레이는 투명해지지 않고
      본 창이 이상해지는데, 창이 멀쩡히 떠 있어서 눈으로는 모른다.
      `tools/probe_overlay.py` 가 이것을 잡았다.

    `GetAncestor(GA_ROOT)` 는 **소유자를 따라가지 않고** 부모 사슬의 뿌리를 준다.
    """
    try:
        handle = window.winfo_id()
    except Exception as exc:
        log.debug("창 핸들을 읽지 못했다: %s", type(exc).__name__)
        return 0
    return _user32.GetAncestor(handle, GA_ROOT) or handle


def _window_chain(window: tk.Misc) -> list[int]:
    """그 창을 이루는 HWND 전부 (내용 창 + 뿌리 창).

    ★ Tk 는 창 하나를 **HWND 두 개**로 만든다 — `winfo_id()` 가 주는 내용 창과,
      그 위의 뿌리 창이다. 뿌리에만 `WS_EX_TRANSPARENT` 를 걸면
      `WindowFromPoint` 는 **내용 창**을 돌려준다. 즉 클릭이 통과하지 않는다.
      2026-09-15에 그렇게 만들었다가 `tools/probe_overlay.py` 가 잡았다.
    """
    try:
        handle = window.winfo_id()
    except Exception as exc:
        log.debug("창 핸들을 읽지 못했다: %s", type(exc).__name__)
        return []
    root = _user32.GetAncestor(handle, GA_ROOT) or handle
    return [h for h in dict.fromkeys((handle, root)) if h]


def _add_ex_style(window: tk.Misc, extra: int, root_only: int = 0) -> int:
    """확장 스타일을 건다. `extra` 는 모든 HWND 에, `root_only` 는 뿌리에만.

    ★ `root_only` 가 따로 있는 이유는 `WS_EX_LAYERED` 때문이다. 이 파일
      맨 위 "`WS_EX_LAYERED` 는 뿌리 창에만 건다" 참고.
    """
    style = 0
    chain = _window_chain(window)
    root = chain[-1] if chain else 0
    for handle in chain:
        style = _user32.GetWindowLongW(handle, GWL_EXSTYLE) | extra
        if handle == root:
            style |= root_only
        _user32.SetWindowLongW(handle, GWL_EXSTYLE, style)
    return style


# 작업 영역은 창 크기 맞추기와 같이 쓴다 — 한 곳(`gui/common.work_area`)에 둔다.
_work_area = work_area


class Overlay:
    """진행 상황을 대상 프로그램 위에 띄운다. 만들면 바로 보인다.

    `parent` 는 본 창(tk.Tk)이다. 이 창이 닫히면 오버레이도 함께 사라진다.
    """

    # 조작 막대 — 진행 글귀 + [중단] [펼치기] [숨기기].
    # 접었을 때의 막대 너비. HUD 판과 나란히 보이게 맞춘다.
    BAR_WIDTH = 360
    BAR_HEIGHT = 40
    # 화면 가장자리에서 이만큼 띄운다. 작을수록 "화면 전체" 가 된다.
    INSET = 8

    def __init__(self, parent, on_stop=None, title: str = "진행 상황",
                 token=None, on_report=None, on_pause=None) -> None:
        self.parent = parent
        self.on_stop = on_stop
        # [일시정지] 를 누른 뒤 — 실행 창 [홈] 의 같은 단추 글자를 맞춘다 (09-29)
        self.on_pause = on_pause
        # 일시정지는 **중단 토큰**이 들고 있다. 없으면 단추를 잠근다 —
        # 누를 수는 있는데 아무 일도 안 일어나면 안 된다.
        self.token = token
        # [상세 보기] — 리포트를 여는 창구. 없으면 단추를 만들지 않는다.
        self.on_report = on_report
        self.closed = False
        self.busy = False         # 실행 중이면 [중단], 아니면 [닫기]
        self.shown = True         # 상태 띠가 보이는가 ([숨기기] 가 바꾼다)
        # ★ 기본은 **코너 HUD** 다. 화면 전체로 펼치는 것은 끝났을 때와
        #   사용자가 [펼치기] 를 눌렀을 때뿐이다 (사용자 확정 2026-09-16).
        self.expanded = False
        self._corner = 0          # 조작 막대가 지금 있는 모서리 (0=기본)

        self.status = tk.Toplevel(parent)
        self.status.overrideredirect(True)
        self.status.attributes("-topmost", True)
        self.status.attributes("-alpha", 0.96)
        self.status.configure(bg=CARD)
        # ★ **색 키 투명을 쓰지 않는다** (2026-09-16 저녁에 뺐다).
        #   창이 카드 크기뿐이라 뚫을 배경이 없다. 그리고 카드 색이
        #   우연히 색 키와 같아지면 **카드가 통째로 사라진다** —
        #   실제로 그렇게 만들어 글자가 하나도 안 보였다.

        # ★ 카드 바탕. 예전에는 배경을 통째로 투명하게 비웠는데, 그러면
        #   판을 가진 것(제목·표)만 읽히고 **Stepper 글자가 대상 프로그램
        #   위에 그대로 얹혀 읽히지 않았다** (2026-09-16 구현 화면에서 확인).
        #   이제 창을 카드 크기로만 잡고 그 안을 칠한다.
        self.shell = tk.Frame(self.status, bg=EDGE, padx=1, pady=1)
        self.shell.pack(fill="both", expand=True)
        body = tk.Frame(self.shell, bg=CARD, padx=16, pady=12)
        body.pack(fill="both", expand=True)
        self.body = body

        # 제목은 **자기 배경을 갖는다.** 투명한 자리에 글자만 놓으면 뒤에
        # 무엇이 있느냐에 따라 읽히지 않는다.
        self.title_var = tk.StringVar(value=f"{title} — 준비됨")
        self.clock_var = tk.StringVar(value="")
        self.header = tk.Frame(body, bg=CARD, pady=2)
        self.title_label = tk.Label(self.header, textvariable=self.title_var,
                                    bg=CARD, fg=FOREGROUND, anchor="w",
                                    justify="left",
                                    font=("Segoe UI", 12, "bold"))
        self.title_label.pack(side="left", fill="x", expand=True)
        # 끝났을 때 나오는 띠. 평소에는 붙이지 않는다.
        self.result_var = tk.StringVar(value="")
        self.result_label = tk.Label(body, textvariable=self.result_var,
                                     bg=PANEL, fg=FOREGROUND, anchor="w",
                                     justify="left", padx=14, pady=10,
                                     font=("Segoe UI", 11))
        # 시작 시각 / 경과. **실제 값**이다 — 첫 단계의 시작 시각과
        # 단계들이 쓴 시간의 합에서 온다.
        tk.Label(self.header, textvariable=self.clock_var, bg=CARD,
                 fg=MUTED, anchor="e", justify="right",
                 font=("Segoe UI", 10)).pack(side="right", padx=(12, 0))
        # HUD 일 때는 붙이지 않는다 — HUD 가 머리글을 직접 그린다.
        self.title = title


        # 단계 칸들. 그리는 규칙은 `gui/pipeline.py` 에 있다.
        # ★ 화면 전체를 쓰므로 칸을 크게 잡고, 남는 높이는 칸에 나눠 준다.
        #   작은 칸으로 화면만 덮으면 자리만 차지하고 보여 주는 것이 없다.
        self.pipeline = PipelineView(body, theme=DARK, scale=_scale(),
                                     on_pick=self._on_pick,
                                     card_min_w=250, card_max_w=460,
                                     card_h=120, fill_height=True,
                                     density=HUD)
        self.pipeline.pack(fill="both", expand=True)

        # 펼쳤을 때만 붙인다 — 현재 단계 상세, 그리고 고른 칸의 표.
        self.recent = RecentLog()
        self.recent.attach()
        self.current_view = CurrentView(body, theme=DARK, scale=_scale(),
                                        recent=lambda: self.recent.latest(3))
        self.table = TableView(body, theme=DARK, scale=_scale())
        self.picked: str = ""

        # --- 조작 막대 : 이것만 누를 수 있다 ---
        self.bar = tk.Toplevel(parent)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.configure(bg=BAR_EDGE)
        inner = tk.Frame(self.bar, bg=BAR_BG, padx=4, pady=4)
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        # ★ 진행 글귀를 막대에 둔다. **숨긴 상태에서도 어디까지 왔는지**
        #   알 수 있어야 한다 — 막대만 남기 때문이다.
        self.bar_var = tk.StringVar(value="준비됨")
        self.bar_label = tk.Label(inner, textvariable=self.bar_var, bg=BAR_BG,
                                  fg=HIDE_FG, anchor="w",
                                  font=("Segoe UI", 10))
        unit = self._button_unit()
        # ★ `ttk.Button` 이 아니라 `tk.Button` 이다. 색을 직접 정해야
        #   어떤 화면 위에 놓여도 보인다 — ttk 는 OS 테마 색을 쓴다.
        self.stop_btn = self._bar_button(
            inner, unit, "중단", bg=STOP_BG, fg=STOP_FG,
            active="#a31f1f", bold=True, command=self._on_stop_clicked)
        # ★ 맨 오른쪽에 둔다 (사용자 요청 2026-09-16). 끝내는 단추는
        #   끝자리에 있어야 손이 간다.
        # ★ **[중단] 앞에 빈자리를 둔다** (2026-09-16, 외부 검토 의견).
        #   [일시정지] 를 누르려다 손이 미끄러져 [중단] 을 누르면 실행이
        #   끝나 버린다. 되돌릴 수 없는 단추만 한 칸 떼어 놓는다.
        self.stop_btn.master.pack(side="right", fill="y", padx=(GROUP_GAP, 0))
        if on_report is not None:
            self.report_btn = self._bar_button(
                inner, unit, "상세", command=self._on_report_clicked)
            self.report_btn.master.pack(side="right", fill="y",
                                        padx=(0, BTN_GAP))
        else:
            self.report_btn = None
        self.pause_btn = self._bar_button(
            inner, unit, PAUSE_TEXT, command=self.toggle_pause,
            state="normal" if token else "disabled")
        # 여기서부터 왼쪽이 **보기 옵션**, 오른쪽이 **실행 제어**다.
        self.pause_btn.master.pack(side="right", fill="y",
                                   padx=(GROUP_GAP, BTN_GAP))
        self.expand_btn = self._bar_button(
            inner, unit, "펼치기", command=self.toggle_expanded)
        self.hide_btn = self._bar_button(
            inner, unit, "숨기기", command=self.toggle)
        self.hide_btn.master.pack(side="right", fill="y")
        self.expand_btn.master.pack(side="right", fill="y", padx=(0, BTN_GAP))
        # ★ 글귀는 **맨 나중에** 붙인다. 먼저 붙이면 남은 자리를 다 차지해
        #   단추가 1x1 로 찌그러진다 (2026-09-16에 그렇게 만들었다).
        self.bar_label.pack(side="left", fill="both", expand=True, padx=(4, 6))
        # ★ 막대 너비를 **계산해서** 덮어쓴다. 클래스의 `BAR_WIDTH` 는 값을
        #   못 재는 환경에서 쓸 기본값일 뿐이다. 단추 개수는 [상세] 유무로
        #   달라지므로 실제로 만든 개수를 센다.
        buttons = 4 + (1 if self.report_btn is not None else 0)
        self.BAR_WIDTH = max(self._fit_bar_width(buttons), type(self).BAR_WIDTH)

        self.status.update_idletasks()
        self.bar.update_idletasks()
        self._apply_styles()
        self._place()

        # 좌표를 누르기 직전에 알려 달라고 건다. 겹치면 막대가 비킨다.
        ui.set_click_guard(self.avoid)

        self._sync_bar_text()

    # ------------------------------------------------------------------ 배치
    def _apply_styles(self) -> None:
        # 상태 표시: 클릭이 통과한다. **LAYERED 는 뿌리에만** (파일 머리 참고).
        _add_ex_style(self.status,
                      WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                      root_only=WS_EX_LAYERED)
        # 조작 막대: 통과시키지 않는다. 다만 포커스는 뺏지 않는다.
        _add_ex_style(self.bar, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    # 펼쳤을 때의 카드 크기 상한. **화면을 다 덮지 않는다** —
    # 뒤에서 대상 프로그램이 보여야 한다 (사용자 요청 2026-09-16).
    CARD_W = 1080
    CARD_H = 740

    # 펼침 카드가 **적어도** 이만큼은 된다. 이보다 작으면 Stepper 가 눌린다.
    CARD_MIN_H = 380

    def size(self) -> tuple[int, int]:
        """상태 띠의 크기. HUD 면 작은 판, 펼치면 **내용에 맞춘 가운데 카드**.

        ## ★ 높이를 고정하지 않는다 (2026-09-16, 화면을 찍어 보고 고쳤다)

        전에는 펼치면 **늘 1080x740** 이었다. 그런데 상세 표가 없는 단계에서는
        아래쪽 60% 가 통째로 빈 상자였다 — 화면의 40% 를 덮으면서 그 자리에
        아무것도 없는 상태다. 상세 표를 주는 단계가 일부뿐이라 **대부분의
        시간에 그랬다.**

        확인 도구로는 잡히지 않았다. 글자도 다 있고 대비도 맞았다.
        **찍어서 눈으로 보고서야** 알았다.
        """
        if not self.expanded:
            width, height = self.pipeline.hud_size()
            # 창 테두리 여백(`body` 의 padx/pady)을 더한다.
            return (width + 28, height + 24)
        left, top, right, bottom = _work_area()
        scale = _scale()
        width = min(int(self.CARD_W * scale), right - left - 160)
        ceiling = min(int(self.CARD_H * scale), bottom - top - 100)
        floor = min(int(self.CARD_MIN_H * scale), ceiling)
        return (width, max(min(self._wanted_height(), ceiling), floor))

    def _wanted_height(self) -> int:
        """지금 내용이 **실제로 쓰는** 높이. 창 여백까지 더한 값이다.

        tkinter 에게 물어본다(`winfo_reqheight`) — 우리가 더해서 세면 여백을
        빠뜨리거나 두 번 세기 쉽다.
        """
        parts = [self.header, self.pipeline.canvas]
        if self.result_var.get():
            parts.append(self.result_label)
        total = 0
        for widget in parts:
            try:
                widget.update_idletasks()
                total += widget.winfo_reqheight()
            except tk.TclError:
                # 아직 만들어지지 않았거나 창이 닫혔다. 그 조각은 0 으로 둔다.
                continue
        # ★ 현재 단계 판과 상세 표는 **자리를 받으면 늘어난다.** 그래서
        #   `winfo_reqheight` 를 물으면 "지금 받은 만큼" 을 돌려줘서 높이가
        #   줄어들지 않는다. 그린 내용의 아래끝을 직접 묻는다.
        total += max(self.current_view.needed_height(), 120)
        if self.table.columns:
            total += max(self.table.needed_height(), 120)
        parts += [self.current_view.canvas]
        if self.table.columns:
            parts.append(self.table.canvas)
        # 조각 사이 여백 + 카드 상하 여백 + 아래에 앉는 조작 막대 자리.
        return total + 8 * len(parts) + 24

    def width(self) -> int:
        """예전 이름. 확인 도구가 쓴다."""
        return self.size()[0]

    def _corners(self) -> list:
        """조작 막대를 둘 수 있는 자리들. 앞에서부터 쓴다.

        네 곳 모두 상태 띠 **안쪽 모서리**다. 화면 전체를 덮으므로 바깥에
        둘 자리가 없고, 안에 있어야 오버레이의 일부로 보인다.
        """
        left, top, right, bottom = _work_area()
        pad = self.INSET + 12
        # ★ **자리를 고정한다** (사용자 지적 2026-09-16).
        #   접기/펼치기마다 막대가 옮겨 다니면 같은 단추를 두 번 누를 수가
        #   없다. 밀도와 무관하게 늘 **오른쪽 맨 위**다.
        #   나머지 자리는 자동화가 그 좌표를 누르려 할 때만 잠깐 쓰는
        #   피난처이고, 다음 갱신에 제자리로 돌아온다(`_place`).
        return [
            (right - self.INSET - self.BAR_WIDTH, top + self.INSET),
            (left + pad, bottom - pad - self.BAR_HEIGHT),
            (right - self.INSET - self.BAR_WIDTH,
             bottom - pad - self.BAR_HEIGHT),
            (left + pad, top + pad),
        ]

    def _place(self) -> None:
        """HUD 는 오른쪽 위 코너에, 펼치면 화면 전체에.

        조작 막대는 **늘 상태 띠 밖**(HUD) 이나 **안쪽 모서리**(펼침)에 둔다.
        HUD 아래에 두는 이유: 작은 판 안에 넣으면 판이 그만큼 커진다.
        """
        left, top, right, _bottom = _work_area()
        width, height = self.size()
        if self.expanded:
            # 가운데에 놓는다. 양옆·위아래로 대상 프로그램이 보인다.
            x = left + (right - left - width) // 2
            y = top + max((_work_area()[3] - top - height) // 2, self.INSET)
        else:
            # 막대가 위에 오므로 그만큼 내려 놓는다.
            x = right - self.INSET - width
            y = top + self.INSET + self.BAR_HEIGHT + 6
        self.status.geometry(f"{width}x{height}+{x}+{y}")
        # ★ 늘 **제자리로** 돌려 놓는다. 자동화를 피해 잠깐 옮겨 갔더라도
        #   다음 갱신에 돌아와야 사람이 단추를 찾을 수 있다.
        self._place_bar(0)
        self._raise_bar()

    def bar_width(self) -> int:
        """조작 막대의 너비. **밀도와 무관하게 같다.**

        너비가 바뀌면 단추가 좌우로 밀려 자리가 바뀐 것과 같아진다.
        """
        return self.BAR_WIDTH

    # --- 막대 치수 계산 ------------------------------------------------
    def _bar_font(self):
        from tkinter import font as tkfont

        return tkfont.Font(family="Segoe UI", size=10)

    def _button_unit(self) -> int:
        """단추 하나의 너비(px). **모든 단추가 이 값을 함께 쓴다.**

        나올 수 있는 글자 중 가장 긴 것에 맞춘다. 그래야 글자가 바뀌어도
        (중단↔닫기, 펼치기↔접기) 단추 자리가 흔들리지 않는다.
        """
        font = self._bar_font()
        return max(font.measure(text) for text in BTN_LABELS) + BTN_PAD

    def _fit_bar_width(self, buttons: int) -> int:
        """단추 개수에 맞춰 **글귀가 잘리지 않는** 막대 너비를 계산한다."""
        font = self._bar_font()
        unit = self._button_unit()
        need = font.measure(BAR_SAMPLE) + BAR_SLACK
        # 무리 사이 빈자리 두 곳(실행 제어 앞 / 중단 앞)을 더 센다.
        gaps = BTN_GAP * (buttons - 1) + (GROUP_GAP - BTN_GAP) * 2
        return unit * buttons + gaps + BAR_FRAME + need

    def _bar_button(self, parent, unit: int, text: str, *, bg: str = HIDE_BG,
                    fg: str = HIDE_FG, active: str = "#38485a",
                    bold: bool = False, command=None, state: str = "normal"):
        """**고정 픽셀 너비**를 갖는 막대 단추.

        ★ `tk.Button(width=N)` 은 N 이 **글자 수**라 글자마다 실제 폭이 다르다.
          그래서 틀을 씌우고 `pack_propagate(False)` 로 픽셀을 못 박는다.
          돌려주는 것은 단추이고, 배치할 때는 `btn.master` 를 pack 한다.
        """
        holder = tk.Frame(parent, bg=BAR_BG, width=unit)
        holder.pack_propagate(False)
        button = tk.Button(holder, text=text, bg=bg, fg=fg,
                           activebackground=active, activeforeground=fg,
                           # ★ 못 누르는 단추도 **단추로 보여야 한다.** 기본값은
                           #   글자를 바탕에 가깝게 지워서 **빈칸처럼** 보였다 —
                           #   화면을 찍어 보니 [일시정지] 자리가 구멍 같았다
                           #   (2026-09-16). 지금은 흐리지만 읽힌다.
                           disabledforeground=DISABLED_FG,
                           relief="flat", bd=0, cursor="hand2",
                           highlightthickness=0,
                           font=("Segoe UI", 10, "bold") if bold
                           else ("Segoe UI", 10),
                           command=command, state=state)
        button.pack(fill="both", expand=True)
        return button


    def _place_bar(self, index: int) -> None:
        corners = self._corners()
        self._corner = index % len(corners)
        x, y = corners[self._corner]
        self.bar.geometry(f"{self.bar_width()}x{self.BAR_HEIGHT}+{x}+{y}")

    def _raise_bar(self) -> None:
        """조작 막대를 **맨 위로** 올린다.

        ★ 펼치면 상태 띠가 화면 전체를 덮으면서 **막대 위에 그려진다.**
          두 창 다 `WS_EX_TOPMOST` 라 나중에 자리를 잡은 쪽이 앞에 온다.
          그러면 [중단]·[숨기기] 가 어두운 제목 줄에 가려 안 보인다
          (2026-09-16에 사용자가 그 화면을 보고 알려 줬다).

        ★ 이것은 **눈에만** 보이는 문제라 히트 테스트로는 잡히지 않는다.
          상태 띠는 `WS_EX_TRANSPARENT` 라 `WindowFromPoint` 가 어차피
          건너뛴다 — 가려져 있어도 "막대가 잡힌다" 고 나온다.
          그래서 확인 도구는 **화면을 찍어 빨간 [중단] 이 보이는지** 센다.
        """
        handle = _hwnd(self.bar)
        if not handle:
            return
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        _user32.SetWindowPos(wintypes.HWND(handle), HWND_TOPMOST,
                             0, 0, 0, 0, 0x2 | 0x1 | 0x10)

    def _bar_rect(self) -> tuple:
        self.bar.update_idletasks()
        x, y = self.bar.winfo_x(), self.bar.winfo_y()
        return (x, y, x + self.bar_width(), y + self.BAR_HEIGHT)

    def _stop_rect(self) -> tuple:
        """예전 이름. 확인 도구가 쓴다."""
        return self._bar_rect()

    # --------------------------------------------------------------- 클릭 가드
    def avoid(self, rect) -> None:
        """자동화가 `rect` 를 누르려 한다. **겹치면 막대가 비킨다.**

        ★ 이 함수는 **작업 스레드**에서 불린다. tkinter 는 GUI 스레드에서만
          건드릴 수 있어 보통은 `after(0, ...)` 로 넘겨야 하지만, 클릭은 곧바로
          일어나므로 넘기기만 하면 **아직 안 비킨 채로** 눌릴 수 있다.
          그래서 창 이동은 Win32 로 **여기서 바로** 한다
          (`SetWindowPos` 는 다른 스레드에서 불러도 된다).
        """
        if self.closed:
            return
        left, top, right, bottom = rect
        for _ in range(len(self._corners())):
            sx1, sy1, sx2, sy2 = self._bar_rect()
            overlap = not (sx2 + AVOID_PADDING < left or sx1 - AVOID_PADDING > right
                           or sy2 + AVOID_PADDING < top or sy1 - AVOID_PADDING > bottom)
            if not overlap:
                return
            self._move_bar(self._corner + 1)
        log.debug("조작 막대를 비킬 자리를 찾지 못했다. 그대로 둔다.")

    def _move_bar(self, index: int) -> None:
        """막대 창을 옮긴다. **Win32 로 직접** 옮겨서 곧바로 반영되게 한다."""
        corners = self._corners()
        self._corner = index % len(corners)
        x, y = corners[self._corner]
        handle = _hwnd(self.bar)
        if not handle:
            return
        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE = 0x1 | 0x4 | 0x10
        _user32.SetWindowPos(wintypes.HWND(handle), wintypes.HWND(0),
                             int(x), int(y), 0, 0, 0x1 | 0x4 | 0x10)
        log.debug("조작 막대를 비켰다: (%d, %d)", x, y)

    # ------------------------------------------------------------------ 갱신
    def show_plan(self, events) -> None:
        """실행 전에 전체 칸을 그려 둔다. 그래야 "몇 개 중 몇 번째" 가 보인다."""
        if self.closed:
            return
        self.pipeline.show_plan(events)
        self._sync_bar_text()
        self._sync_title()
        # ★ 현재 단계 판과 표도 함께 갱신한다. 계획만 바꾸고 판을 그대로
        #   두면 **지나간 단계의 건수**가 남는다 (확인 도구가 잡았다).
        self._show_table()
        self._place()

    def update(self, event) -> None:
        """단계 사진 하나를 받아 그린다. **GUI 스레드에서만** 부른다."""
        if self.closed:
            return
        self.pipeline.update(event)
        self._sync_title()
        self._sync_bar_text()
        self._show_table()
        self._place()

    def started_at(self) -> float:
        """이번 실행이 **실제로 시작한 시각.** 아직이면 0.

        첫 단계가 시작한 시각을 쓴다. 오버레이를 만든 시각이 아니다 —
        만들어 놓고 한참 뒤에 시작할 수 있다.
        """
        stamps = [e.started_at for e in self.pipeline.events.values()
                  if e.started_at]
        return min(stamps) if stamps else 0.0

    def _sync_clock(self) -> None:
        """머리줄 오른쪽 — 시작 시각과 경과. **실제 값만** 적는다."""
        import time as _time

        started = self.started_at()
        if not started:
            self.clock_var.set("아직 시작하지 않았습니다")
            return
        spent = sum(e.elapsed or 0 for e in self.pipeline.events.values())
        begin = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(started))
        gap = chr(10)          # 줄바꿈. 리터럴로 쓰면 전달 과정에서 먹힌다
        self.clock_var.set(
            f"시작 {begin}{gap}경과 시간 {int(spent // 3600):02d}:"
            f"{int(spent % 3600 // 60):02d}:{int(spent % 60):02d}")

    def _sync_title(self) -> None:
        """펼침 머리줄. **지금 보여 줄 칸** + 상태별 개수 + 경과.

        방금 온 사진이 아니라 `current()` 를 쓴다 — 표와 제목이 다른
        단계를 가리키면 한 화면에서 말이 엇갈린다.
        """
        here = self.pipeline.current()
        if here is None:
            return
        # ★ **같은 말을 세 번 하고 있었다** (2026-09-16 화면에서 확인).
        #   `5/9 매출처리 — 진행 중` 이 제목과 작업 흐름 줄에 그대로 겹쳤고,
        #   `완료 4` 와 `4단계 완료`, 경과 시간까지 중복이었다. 화면을 찍어
        #   보고서야 알았다 — 글자 수를 세는 확인 도구로는 잡히지 않는다.
        #
        #   지금은 자리를 나눈다.
        #     · 제목      = 이 창이 무엇인가 + 지금 상태
        #     · 둘째 줄   = 몇 번째인가 + 상태별 개수
        #     · Stepper   = 어느 칸인가 (그림)
        #     · 현재 단계 판 = 그 칸의 이름과 건수 (가장 큰 글자)
        head = f"{self.title} — {here.state_label}"
        tally = self._tally()
        where = f"{place(here).replace('/', ' / ')} 단계"
        if getattr(here, "stage", ""):
            where = f"{where} · {here.stage}"
        second = f"{where} · {tally}" if tally else where
        self.title_var.set(f"{head}{chr(10)}{second}")
        self._sync_clock()

    def _sync_bar_text(self) -> None:
        """막대의 글귀를 **지금 보여 줄 칸**에 맞춘다.

        방금 온 사진이 아니라 `pipeline.current()` 를 쓴다 — 어긋나면 같은
        화면에서 HUD 와 막대가 다른 단계를 가리킨다. 숨긴 상태에서는
        이 글귀만 남으므로 **계획만 그린 뒤에도** 채워져 있어야 한다.
        """
        here = self.pipeline.current()
        where = f"{place(here)} {here.name}" if here else "준비됨"
        if self.token is not None and self.token.paused:
            # ★ 표시만 붙인다. `"⏸ 멈춤 — "` 라고 다 쓰면 66px 을 **늘** 비워
            #   둬야 해서 그만큼 단계 이름이 잘린다. 멈춘 상태는 옆의
            #   [재개] 단추가 이미 말해 주므로 여기서는 표식이면 충분하다.
            where = f"⏸ {where}"
        self.bar_var.set(where)

    # --------------------------------------------------------- 일시정지
    def toggle_pause(self) -> None:
        """일시정지 / 재개.

        ★ **다음 대기 지점에서** 멈춘다. 지금 누르고 있는 클릭을 중간에
          끊지 않는다 — 끊으면 대상 프로그램이 어중간한 상태로 남는다.
          멈춘 동안에도 [중단] 은 듣는다 (`utils/cancel.py`).
        """
        if self.token is None:
            log.info("일시정지할 토큰이 없다. 이 화면에서는 쓸 수 없다.")
            return
        if self.token.paused:
            self.token.resume()
        else:
            self.token.pause()
        self.sync_pause_btn()
        if self.on_pause is not None:
            self.on_pause()

    def sync_pause_btn(self) -> None:
        """단추 글자를 토큰에 맞춘다. 실행 창에서 멈췄을 때도 부른다."""
        if self.token is None or self.closed:
            return
        paused = self.token.paused
        self.pause_btn.configure(text=RESUME_TEXT if paused else PAUSE_TEXT,
                                 bg="#8a6a1e" if paused else HIDE_BG)
        self._sync_bar_text()

    # ------------------------------------------------------------------ 펼치기
    def toggle_expanded(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        """코너 HUD <-> 화면 전체.

        같은 자료를 **다른 밀도로** 다시 그리는 것뿐이다
        (`gui/pipeline.py` 의 `FULL` / `HUD`).
        """
        if self.closed:
            return
        self.expanded = expanded
        self.pipeline.set_density(FULL if expanded else HUD)
        self._sync_bar_text()
        if expanded:
            # 위에서부터: 머리줄 → Stepper → 현재 단계 판 → 고른 칸의 표.
            self.header.pack(before=self.pipeline.canvas, fill="x",
                             pady=(0, 2))
            # ★ Stepper 는 필요한 만큼만 쓰고 **남은 자리를 아래에 준다.**
            self.pipeline.fill_height = False
            self.pipeline.canvas.pack_configure(fill="x", expand=False)
            self.current_view.pack(fill="x", expand=False, pady=(8, 0))
            # 아래에 조작 막대가 앉으므로 그만큼 비워 둔다.
            # ★ 끝난 뒤에 사람이 직접 펼쳤다면 **결과 띠도 붙여 준다.**
            #   정상 완료는 펼치지 않고 넘어가므로, 그때 만들어 둔 결과가
            #   여기서 처음 화면에 나온다. 이게 없으면 나중에 펼쳤을 때
            #   "무엇으로 끝났는지" 를 볼 곳이 사라진다.
            if self.result_var.get():
                # 요약이 한 줄로 길다 — 카드 폭에서 접는다 (09-29 실기: 줄바꿈이 없어 잘렸다).
                # 카드 테두리·body·띠의 좌우 여백을 뺀다.
                self.result_label.configure(wraplength=max(self.size()[0] - 70, 200))
                self.result_label.pack(before=self.pipeline.canvas, fill="x",
                                       pady=(0, 8))
            # 아래에 여백을 크게 두지 않는다. 조작 막대는 **오른쪽 위 고정**이라
            # 카드 아래를 비워 둘 이유가 없다 (자리를 옮기던 때의 잔재였다).
            self.table.pack(fill="x", expand=False, pady=(8, 0))
            self._show_table()
        else:
            self.header.pack_forget()
            # 작은 판에서는 결과 띠를 떼어 둔다 — 300px 폭에서 잘려 읽히지 않았다 (09-29 실기).
            # 결과는 HUD 글귀와 [상세]·펼치기에 있다.
            self.result_label.pack_forget()
            self.current_view.canvas.pack_forget()
            self.table.canvas.pack_forget()
            self.pipeline.fill_height = True
            self.pipeline.canvas.pack_configure(fill="both", expand=True)
        self.expand_btn.configure(text="접기" if expanded else "펼치기")
        if not self.shown:
            self.set_visible(True)
        self._place()

    def _tally(self) -> str:
        """상태별 개수 + 전체 경과. 펼쳤을 때 한눈에 보라고 머리줄에 붙인다.

        경과는 **단계들이 실제로 쓴 시간의 합**이다. 시작 시각과 지금의
        차이가 아니다 — 구간 실행으로 건너뛴 앞 단계까지 세면 안 된다.
        """
        from gui.pipeline import FINISHED, RUNNING, group_state

        events = [self.pipeline.events[s] for s in self.pipeline.order]
        if not events:
            return ""
        spent = sum(e.elapsed or 0 for e in events)
        groups = self.pipeline.stage_groups()
        if groups:
            # 큰 단계로 센다 (09-29) — 제목 줄의 '2 / 4 단계' 와 같은 단위
            events = [SimpleNamespace(state=group_state(members), elapsed=0) for _n, members in groups]
        done = sum(1 for e in events if e.state in FINISHED)
        failed = sum(1 for e in events if e.state == "failed")
        running = sum(1 for e in events if e.state == RUNNING)
        waiting = len(events) - done - failed - running
        parts = [f"완료 {done}"]
        if running:
            parts.append(f"진행 {running}")
        if waiting:
            parts.append(f"대기 {waiting}")
        if failed:
            parts.append(f"실패 {failed}")
        if spent:
            parts.append(f"경과 {int(spent // 60)}분 {int(spent % 60)}초")
        return " · ".join(parts)

    # -------------------------------------------------------------- 상세 표
    def _on_pick(self, event) -> None:
        """칸을 골랐다 (마우스로 눌렀거나 단축키로 옮겼거나)."""
        self.picked = getattr(event, "step_id", "") if event else ""
        self._show_table()

    def _shown_event(self):
        """표에 보여 줄 칸. 고른 것이 없으면 지금 진행 중인 칸."""
        if self.picked and self.picked in self.pipeline.events:
            return self.pipeline.events[self.picked]
        return self.pipeline.current()

    def _show_table(self) -> None:
        if self.closed or not self.expanded:
            return
        # 현재 단계 판은 **늘 진행 중인 칸**을 보여 준다. 표만 고른 칸을
        # 따른다 — 둘 다 따라가면 "지금 무엇을 하는지" 를 놓친다.
        self.current_view.show(self.pipeline.current())
        event = self._shown_event()
        if event is None:
            self.table.show("상세", (), ())
            self._fit_table()
            return
        rows = tuple(getattr(event, "rows", ()) or ())
        title = f"{place(event)} {event.name}"
        if getattr(event, "error", ""):
            title = f"{title} — {event.error}"
        elif event.detail:
            title = f"{title} — {event.detail}"
        # 표의 줄 수는 붙이지 않는다 — "항목|값" 표에서는 주문 수로 읽혔다 (09-21 검토).
        self.table.show(title, getattr(event, "columns", ()), rows)
        self._fit_table()

    def _fit_table(self) -> None:
        """**표가 없으면 상세 칸을 접는다.** 상세 표를 주는 단계는 일부뿐이라, 빈 상자를 두면
        펼친 카드(화면의 약 40%)의 아래쪽이 비어 있기만 한다 (09-16 검토)."""
        if not self.expanded:
            return
        # ★ **높이를 직접 지정한다.** 둘 다 `expand=True` 로 두면 tkinter 가
        #   남는 자리를 **반씩 나눠** 준다 — 캔버스는 요청 높이가 1 이라
        #   내용과 무관하게 갈린다. 그래서 현재 단계 판은 아래가 비고
        #   표는 눌려서 `... N줄 더 있습니다` 가 됐다 (2026-09-16 화면).
        self.current_view.canvas.configure(
            height=self.current_view.needed_height())
        has_rows = bool(self.table.columns)
        if has_rows:
            self.table.canvas.configure(height=self.table.needed_height())
            self.table.canvas.pack_configure(fill="x", expand=False)
        else:
            # 자리를 놓아 준다. 카드가 그만큼 짧아진다.
            self.table.canvas.pack_forget()
        # 내용이 바뀌면 카드 높이도 달라진다. 다시 재서 놓는다.
        self._place()

    def step(self, delta: int) -> None:
        """보는 칸을 앞뒤로 옮긴다. **단축키로만 쓰라고 만든 것이다.**"""
        order = self.pipeline.order
        if not order:
            return
        here = self._shown_event()
        at = 0
        if here is not None and here.step_id in order:
            at = order.index(here.step_id)
        self.pipeline.select(order[(at + delta) % len(order)])
        self._on_pick(self.pipeline.selected())

    # ------------------------------------------------------------------ 숨기기
    def toggle(self) -> None:
        """상태 띠만 감춘다. **조작 막대는 남는다** — 다시 켤 곳이 있어야 한다."""
        self.set_visible(not self.shown)

    def set_visible(self, visible: bool) -> None:
        if self.closed:
            return
        self.shown = visible
        if visible:
            self.status.deiconify()
            self.status.attributes("-topmost", True)
            self._place()
        else:
            self.status.withdraw()
        self.hide_btn.configure(text="숨기기" if visible else "보이기")

    def set_busy(self, busy: bool) -> None:
        """실행 중이면 [중단], 끝났으면 [닫기].

        끝난 뒤에도 오버레이를 남기는 이유: 대상 프로그램이 아직 화면을 덮고
        있어서 **결과를 볼 곳이 여기뿐**이다. 사용자가 다 보고 직접 닫는다.
        """
        if self.closed:
            return
        self.busy = busy
        self.stop_btn.configure(text="중단" if busy else "닫기")

    def _on_report_clicked(self) -> None:
        if self.on_report is None:
            return
        try:
            self.on_report()
        except Exception as exc:
            log.warning("상세 보기를 열지 못했다: %s: %s",
                        type(exc).__name__, exc)

    # ------------------------------------------------------------ 끝났을 때
    def finish(self, state: str, summary: str = "", error: str = "") -> None:
        """실행이 끝났다. **결과 띠**를 머리줄 아래에 붙인다.

        ★ `error` 에 traceback 이 와도 **첫 줄만** 보여 준다. 나머지는
          [상세 보기] 의 리포트와 로그 파일에 있다. 화면에 파이썬 예외를
          그대로 쏟으면 사용자가 읽을 것이 없어진다.

        ## ★ 끝났다고 **무조건 펼치지 않는다** (2026-09-16, 외부 검토 의견)

        전에는 끝나는 순간 1080x740 카드를 화면 가운데 펼쳤다. 그런데 이
        프로그램에는 **예약 실행(무인)** 이 있다. 아무도 보고 있지 않은데
        화면의 40% 를 덮고, 사람이 돌아올 때까지 그대로 남는다. 하루 세 번
        도는 예약이면 그 창이 세 번 뜬다.

        그래서 **결과에 따라 다르게** 한다 — 정상 완료는 조용히, 문제가
        있을 때만 눈에 띄게.

        | 상황 | 어떻게 |
        | --- | --- |
        | 정상 완료 | HUD 를 유지한다. 결과는 HUD 와 [상세] 에 있다 |
        | 실패 / 중단 | **펼친다.** 사람이 봐야 하는 일이다 |
        | 실패 건수가 있는 완료 | 펼친다. 완료지만 확인이 필요하다 |
        """
        if self.closed:
            return
        from gui.pipeline import FINISHED, group_state

        events = list(self.pipeline.events.values())
        done = sum(1 for e in events if e.state in FINISHED)
        groups = self.pipeline.stage_groups()
        done_text = f"{done}단계"
        if groups:      # 큰 단계로 센다 (09-29)
            finished = sum(1 for _n, members in groups if group_state(members) in FINISHED)
            done_text = f"{finished} / {len(groups)}단계"
        spent = sum(e.elapsed or 0 for e in events)
        broke = next((e for e in events if e.state == "failed"), None)
        gap = chr(10)

        if state == "failed":
            head = "RPA 실행 실패"
            color = DARK["card"]["failed"][2]
            where = "알 수 없음"
            if broke is not None:
                where = f"{place(broke)} {broke.name}"
            first = (error or getattr(broke, "error", "") or summary or "")
            first = first.splitlines()[0] if first else "원인을 알 수 없다"
            lines = [head, first,
                     f"실패 단계  {where}",
                     f"완료 단계  {done_text}",
                     f"실행 시간  {_hms(spent)}"]
        elif state == "cancelled":
            head = "RPA 실행 중단"
            color = DARK["card"]["cancelled"][2]
            lines = [head, summary or "사용자가 중단했습니다.",
                     f"완료 단계  {done_text}",
                     f"실행 시간  {_hms(spent)}"]
        else:
            head = "RPA 실행 완료"
            color = DARK["card"]["done"][2]
            # 단계별 건수를 더한 '처리 결과 N건 중 M건' 은 뺐다 (09-29 실기) — 메일 파일·사이트·주문·
            # 보류 주문처럼 단위가 달라 "26건 중 21건 성공" 이 실패 5건처럼 읽혔다. 건수는 요약에 있다.
            lines = [head, summary or "모든 작업이 끝났습니다.",
                     f"총 실행 시간  {_hms(spent)}"]
        self.result_var.set(gap.join(lines))
        self.result_label.configure(fg=color)

        # ★ 눈에 띄어야 하는가. 위 머리말의 표대로 가른다.
        hurt = sum(e.failed_items or 0 for e in events)
        needs_eyes = state != "done" or hurt > 0
        if needs_eyes:
            self.set_expanded(True)
            self.result_label.pack(before=self.pipeline.canvas, fill="x",
                                   pady=(0, 8))
        else:
            # 정상 완료 — 조용히 둔다. 결과는 HUD 글귀와 [상세] 에 있다.
            self.set_visible(True)
            log.info("정상 완료라 오버레이를 펼치지 않는다. "
                     "[펼치기] 를 누르면 결과가 보인다.")
        self._place()

    def _on_stop_clicked(self) -> None:
        if not self.busy:
            self.close()
            return
        if self.on_stop is not None:
            self.on_stop()

    # ------------------------------------------------------------------ 정리
    def close(self) -> None:
        """창을 없애고 클릭 가드를 뗀다. 두 번 불러도 괜찮다."""
        if self.closed:
            return
        self.closed = True
        ui.set_click_guard(None)
        self.recent.detach()
        for window in (self.status, self.bar):
            try:
                window.destroy()
            except Exception as exc:
                log.debug("오버레이 창을 닫지 못했다: %s", type(exc).__name__)


def _hms(seconds: float) -> str:
    """`00:04:21`. 초 단위 실수를 사람이 읽는 시간으로."""
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _scale() -> float:
    from gui.common import ui_scale

    return ui_scale()
