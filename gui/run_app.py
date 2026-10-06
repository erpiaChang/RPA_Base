r"""실행용 GUI — 사용자는 exe 만 실행한다.

메일에서 주문 엑셀을 받아 저장하고, 이어서 업무 프로그램에 업로드·처리까지 한다.
흐름은 통합(`orchestrator/full_flow.py`)과 **같다.** 다른 것은 화면뿐이다.

## 다른 창과 무엇이 다른가

| | 통합 창 | 이 창 |
| --- | --- | --- |
| 업체코드 / 아이디 | 사람이 넣는다 | **빌드에 구워 넣는다** (화면에 없다) |
| 메일 계정·인증 경로 | 설정 파일 | **빌드에 구워 넣는다** |
| ERPia 실행 파일 / 엑셀 저장 폴더 | 사람이 넣는다 | ERPia 는 **켤 때와 [실행] 때 이 PC 에서 찾아 넣는다** (`_repair_paths`). 저장 폴더는 기본값이 없어 비면 입력 필요 (10-02). 둘 다 [찾기] 로 바꿀 수 있다 |
| 비밀번호 2개 / 수집방식 / 택배사 / 박스 / 자동·수동 | 사람이 넣는다 | **구워 넣되 화면에서 고칠 수 있다** |

값이 어디서 오는지는 `config/settings.py` 를 보면 된다.
`DEFAULTS` → 구워 넣은 값 → exe 옆 `config/settings.local.json` 순으로 덮인다.

굽는 방법은 `tools/bake_settings.py`, 빌드는 `build_run.spec` 이다.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from automation import logistics, order_mapping
from automation.application import find_exe, is_erpia_exe, logged_in_pids
from config.settings import SETTINGS, save_local
from gui.autorun_pane import AutoRunPane
from gui.common import (WINDOW_FRAME_MARGIN, DetailPane, apply_scaling, ui_scale,
                        work_area)
from gui.history_window import HistoryPane
from gui.overlay import PAUSE_TEXT, RESUME_TEXT
from gui.pipeline import PipelinePane
from gui.erpia_app import SOURCE_EXCEL, SOURCE_SITE, ErpiaWindow
from collect import sites
from orchestrator import erpia_flow, full_flow, history, modules, remote, telemetry
from orchestrator.common import Result
from utils import autorun, autostart, instance, process, schedule
from utils.logger import get_logger

log = get_logger(__name__)

# 화면에 없지만 **없으면 실행할 수 없는** 값의 이름 — 목록은 `modules.baked_required` (기능별, 굽기 도구와 같다).
# 여기서 비어 있다는 것은 설정을 굽지 않았거나 굽다 만 빌드라는 뜻이다.
BAKED_LABELS = {
    "login_company_code": "ERPia 업체코드",
    "login_user_id": "ERPia 아이디",
    "target_exe_name": "ERPia 실행 파일 이름",
    "mail_url": "메일 로그인 주소",
    "mail_user_id": "메일 아이디",
    "mail_unread_only": "읽지 않은 메일만",
    "mail_days_back": "메일 기간",
    "browser_channel": "브라우저",
    "phone_os": "휴대폰 종류",
    "sms_keyword": "인증 문자를 고르는 말",
}
EXE_HELP = ("비어 있거나 맞지 않으면 켤 때와 [실행] 할 때 이 PC 에서 찾아 넣습니다. "
            "다른 곳에 설치했으면 [찾기] 로 고릅니다.")
DOWNLOAD_HELP = ("메일에서 받은 주문 엑셀을 날짜별로 저장하는 폴더입니다. "
                 "[찾기] 로 고릅니다. 비어 있거나 없는 폴더면 메일 엑셀 받기를 실행하지 않습니다.")
# ERPia 를 찾지 못해 [실행] 이 돌아섰을 때 (09-29 사용자 요청 — 실패로 남기고 알린다)
NO_ERPIA = "ERPia 프로그램을 찾지 못해 실행하지 않았습니다"
NO_ERPIA_MESSAGE = (f"{NO_ERPIA}.\n\nERPia 가 이 PC 에 설치돼 있는지 확인하세요. "
                    "다른 곳에 설치했으면 [설정] 탭 [ERPia 프로그램] 의 [찾기] 로 고릅니다.")

MODULE_HELP = ("고른 것만 이 순서로 돕니다. 최소 하나는 골라야 합니다.\n"
               "  · 여기서도 예약에서도 고르지 않은 기능의 입력칸은 잠깁니다\n"
               "  · 예약은 예약마다 고른 기능으로 돕니다 ([예약] 탭)")
# 빌드 프로그램에서 '기능 고정' 으로 구운 exe (`run_modules_locked`, 사용자 확정 2026-09-22).
LOCKED_HELP = ("이 프로그램은 위 기능으로 고정돼 있습니다 (빌드할 때 정함). 바꾸려면 다시 빌드합니다.\n"
               "  · 예약도 이 기능 안에서만 고릅니다")
# ERPia 를 띄워야 하는 기능. 하나라도 고르면 프로그램 실행·로그인이 들어간다.
ERPIA_MODULES = {module.id for module in modules.ALL if module.erpia}
SOURCE_HELP = ("자동수집은 사이트에서 직접 가져오고, 엑셀수집은 메일로 받은 엑셀을 올립니다.\n"
               "  · 최소 하나는 골라야 합니다. 둘 다 고르면 엑셀수집 → 자동수집 순입니다")
COURIER_HELP = "물류관리 [업체] 목록의 이름과 완전히 같아야 합니다."
BOX_HELP = "물류관리 [박스] 목록의 이름과 완전히 같아야 합니다."
MODE_HELP = "자동이면 저장 뒤 [운송장출력], 수동이면 [엑셀파일생성] 을 누릅니다."
SALES_HELP = ("전체는 [매출처리] 단추로 화면에 조회되지 않은 미매출 주문까지 처리합니다. "
              "선택주문은 화면에 조회된 주문만 체크해 [선택주문 매출처리] 로 처리합니다.")
# 매출처리 방식 라디오 글 (값은 `order_mapping.SALES_MODES`)
SALES_LABELS = {order_mapping.SALES_ALL: "전체 매출처리", order_mapping.SALES_SELECTED: "선택주문 매출처리"}
VERIFY_RETRY_MS = 60 * 1000     # 서버 확인이 네트워크로 실패하면 이 주기로 다시 (09-28)
VERIFY_KEY = "이 PC 의 서버 확인"  # `_values` 의 항목 이름 — 확인 전·거부면 [실행] 이 잠긴다
DRAIN_LIMIT = 20                # 1초 시계 한 번에 적용할 원격 이벤트 상한 (09-28)

# 사용자가 고치는 값 3종 (09-28) --------------------------------------------
HOLD_HELP = "배송보류를 걸지 않을 상품코드입니다. 코드와 완전히 같아야 하고, 여러 개면 쉼표로 나눕니다."
EXCEL_PW_HELP = "암호가 걸린 주문 엑셀의 비밀번호입니다. 사이트마다 다르면 \"사이트B=0000; 다른곳=2222\" 로 적습니다."
SMS_HELP = "2차 인증 문자를 읽는 방법입니다. adb 는 폰의 USB 디버깅이 필요하고, 무선 주소는 \"IP:포트\" 입니다."
MAIL_PW_HELP = "주문 엑셀이 오는 메일 사이트 로그인 비밀번호입니다."
ERPIA_PW_HELP = "ERPia 로그인 비밀번호입니다."
AUTOSTART_HELP = "켜 두면 PC 에 로그온할 때 켜지고, 창을 닫아도 5분 안에 작게 다시 켜집니다. 예약은 켜져 있을 때만 돕니다."

# 화면 짜임 (09-29 개편 — 사용자 결정은 docs/HANDOFF.md 3절). 위쪽 탭 넷, 창 크기는 고정
TAB_HOME, TAB_SCHEDULE, TAB_HISTORY, TAB_SETTINGS = "홈", "예약", "실행 기록", "설정"
WINDOW_W, WINDOW_H = 760, 640      # 제목 표시줄을 합쳐 720px 이하 — 1024x768 에서 작업 표시줄을 뺀 높이
SAVE_DELAY_MS = 800                # 설정은 바꾸면 바로 저장 (사용자 결정 09-29). 치는 동안은 모았다가 한 번
# 비어 있으면 바로 저장이 쓰지 않는 값 — 구운 값을 덮으면 되살릴 수 없다 (`_autosave`)
KEEP_IF_BLANK = ("login_password", "mail_password", "delivery_company", "delivery_box",
                 "download_base_dir", "target_exe", "target_work_dir")
BUSY_HINT = "실행하는 동안에는 설정을 바꿀 수 없습니다. 끝나면 다시 바꿀 수 있습니다."
# 예약 전 경고 (10-01) — 같은 아이디로 로그인된 ERPia 가 있으면 '곧 그 창을 닫는다' 를 맨 위 창으로
WARN_BEFORE = 5 * 60            # 예약 몇 초 전부터
WARN_LOOK_EVERY = 30            # 그 사이 같은 아이디 ERPia 를 다시 찾는 주기
SKIP_TEXT = "이번만 건너뛰기"     # 미루기 대신 (10-02 사용자 결정) — 그 한 번만 돌지 않는다
SKIP_BY_PERSON = "사람이 이번 회차를 건너뛰었다 (같은 아이디로 ERPia 를 쓰는 중)"
# 예약 전 휴대폰 인증 점검 (10-01) — 메일 기능이 든 회차만. 보기만 한다 (`auth_code.precheck`)
PRECHECK_BEFORE = 30 * 60       # 예약 몇 초 전부터
PRECHECK_AGAIN = 5 * 60         # 다시 보는 주기
PRECHECK_MIN_LEAD = 2 * 60      # 이보다 가까우면 안 본다 — 회차의 사전 점검과 휴대폰 연결 앱을 같이 만지지 않게
PRECHECK_GIVE_UP = 2 * 60       # 점검이 이만큼 안 끝나면 '확인 못 함'
NOTICE_ERPIA, NOTICE_PHONE = "erpia", "phone"
# [멈춘 곳부터 다시] (10-01) — 지난 실행이 멈춘 기능부터 끝까지 한 번. 저장된 실행할 기능은 바꾸지 않는다
RERUN_TEXT = "멈춘 곳부터 다시"     # ★ RESUME_TEXT 는 overlay 의 [계속하기] 다 — 같은 이름을 쓰면 덮는다
RERUN_TRIGGER = "이어서 실행"       # 리포트·[실행 기록]·서버의 '어떻게'
PAUSED_STATUS = "일시정지 — 지금 하던 동작을 마치고 멈춥니다. [계속하기] 를 누르면 이어서 합니다."
SETTINGS_HINT = "칸을 누르면 여기에 설명이 나옵니다. 바꾸면 바로 저장됩니다."
OVERLAY_TEXT = "화면 위 진행 표시"
RUN_STYLE = "Run.TButton"
# [설정에서 넣기] — 비어 있는 값 이름(`_values` 의 키) → 그 칸
# ERPia 경로는 없다 — 비어도 [실행] 이 찾아 넣는다 (`_repair_paths`). 저장 폴더·인증 문자는 아래에 있다
FIX_TARGETS = {"실행할 기능": "module_checks", "ERPia 비밀번호": "pw_entry",
               "사이트 비밀번호": "mail_pw_entry", "수집방식": "auto_check",
               "택배사": "courier_entry", "박스": "box_entry", "자동/수동": "mode_radios",
               "매출처리": "sales_radios", "엑셀 저장 폴더": "download_btn", "인증 문자": "sms_combo",
               "무선 주소": "adb_entry"}
# 화면 글 ↔ (sms_source, adb_connection). 설정 값을 사용자에게 그대로 보이지 않는다
SMS_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("휴대폰 연결", "phonelink", "usb"),
    ("adb (USB 케이블)", "adb", "usb"),
    ("adb (무선)", "adb", "wireless"),
    ("자동으로 고름", "auto", "usb"),
)


def _join_codes(codes: object) -> str:
    return "; ".join(str(c).strip() for c in (codes or []) if str(c).strip())


def _split_codes(text: str) -> list[str]:
    return [part.strip() for part in text.replace(",", ";").split(";") if part.strip()]


def _join_excel_pw(per_site: object, default: object) -> str:
    """사이트별이 있으면 `사이트=비번; ...`, 없으면 기본 비밀번호 하나."""
    if isinstance(per_site, dict) and per_site:
        return "; ".join(f"{k}={v}" for k, v in per_site.items())
    return str(default or "")


def _split_excel_pw(text: str) -> tuple[dict[str, str], str]:
    """한 칸을 (사이트별, 기본) 으로 가른다. `=` 가 있으면 사이트별이다."""
    text = text.strip()
    if "=" not in text:
        return {}, text
    per_site = {}
    # `;` 로만 나눈다 — 쉼표가 든 비밀번호가 잘렸다 (09-29 검토). 화면은 `; ` 로 이어 보인다
    for part in text.split(";"):
        if "=" in part:
            name, _, value = part.partition("=")
            if name.strip():
                per_site[name.strip()] = value.strip()
    return per_site, ""


def _sms_label(source: object, connection: object) -> str:
    """설정값 → 화면 글. 비었거나 모르는 값이면 빈 글 — 기본값이 없다 (10-02), [실행] 이 '입력 필요' 로 잠긴다."""
    for label, src, conn in SMS_CHOICES:
        if src == source and (src != "adb" or conn == connection):
            return label
    return ""


def _sms_values(label: str) -> tuple[str | None, str | None]:
    """화면 글 → (sms_source, adb_connection). 안 골랐으면 (None, None)."""
    for name, src, conn in SMS_CHOICES:
        if name == label:
            return src, conn
    return None, None


class RunWindow(ErpiaWindow):
    """입력을 줄인 실행 화면 (고칠 수 있는 값은 docs/SETTINGS.md 화면 항목 표). 흐름은 통합과 같다."""

    # 흐름이 통합과 같아 구간 실행은 지원하지 않는다 — 대신 [멈춘 곳부터 다시] (개발은 `tools/test_flow --from`).
    SUPPORTS_SEGMENT = False

    def __init__(self, root: tk.Tk) -> None:
        # ★ `_build()` 가 이 변수를 쓰므로 **부모 생성자보다 먼저** 만든다.
        self.mail_pw_var = tk.StringVar(value=SETTINGS.mail_password or "")
        # 엑셀 저장 폴더 (09-23). 기본값 없음 (10-02) — 비면 메일 기능이 '입력 필요'. 저장은 [실행] 때 (`_saved_values`)
        self.download_var = tk.StringVar(value=getattr(SETTINGS, "download_base_dir", None) or "")
        # 서버 확인 (09-28). 빌드 ID 는 구워져 있고 화면에 칸이 없다 — 사용자가 넣을 것이 없다.
        # verify_state: "" 정상 / "pending" 확인 중 / "blocked" 거부(다른 PC) — 뒤 둘은 [실행] 이 잠긴다
        self.verify_state = ""
        self.verify_text = ""
        # 사용자가 고치는 값 3종 (09-28, 사용자 요청). 저장은 [실행] 때 (`_saved_values`)
        self.hold_var = tk.StringVar(value=_join_codes(getattr(SETTINGS, "hold_exclude_codes", None)))
        self.excel_pw_var = tk.StringVar(value=_join_excel_pw(
            getattr(SETTINGS, "excel_passwords", None), getattr(SETTINGS, "excel_password", None)))
        self.sms_var = tk.StringVar(value=_sms_label(getattr(SETTINGS, "sms_source", None),
                                                     getattr(SETTINGS, "adb_connection", None)))
        self.adb_addr_var = tk.StringVar(value=getattr(SETTINGS, "adb_wireless_address", None) or "")
        # 매출처리 방식 (10-01). 비었거나 모르는 값이면 어느 쪽도 안 골린 채로 뜨고 [실행] 이 잠긴다 (`_values`, 기본값 없음 10-02)
        self.sales_var = tk.StringVar(value=(getattr(SETTINGS, "sales_mode", None) or "").strip())
        self.sales_var.trace_add("write", self._on_field_changed)     # 고르면 [실행] 잠금을 다시 본다
        # 맨 위 알림 창 하나 (예약 전 경고·휴대폰 점검, 10-01) — 모달이 아니다
        self._notice: tk.Toplevel | None = None
        self._notice_kind = ""
        self._notice_var: tk.StringVar | None = None
        self._warned_key = None             # 경고를 띄운 예약 차례
        self._warn_looked = 0.0
        # 예약 전 휴대폰 점검 — 스레드가 결과를 두면 1초 시계가 집는다 (`_verify_build` 와 같은 방식)
        self._phone_thread: threading.Thread | None = None
        self._phone_started = 0.0
        self._phone_result: dict | None = None
        self._phone_seen: dict | None = None
        self._phone_next = 0.0
        self._phone_alerted = None
        # [멈춘 곳부터 다시] — 지난 실행 기록으로 계산한다 (`_refresh_last`). 누르면 이번 실행에만 그 기능
        self.resume_ids: list[str] = []
        self.resume_run = False
        # 실행할 기능. 부모 생성자가 계획을 그릴 때(`_plan`) 이미 있어야 한다.
        # 기능 고정 빌드면 **구운 값**이다 — exe 옆 설정이 덮어도 실행 창은 그것을 쓰지 않는다.
        self.locked_modules = _locked_modules()
        saved = self.locked_modules
        if saved is None:
            saved = getattr(SETTINGS, "run_modules", None) or []    # 기본값 없음 (10-02) — 비면 '입력 필요'
        self.module_vars = {module.id: tk.BooleanVar(value=module.id in saved)
                            for module in modules.ALL}
        # 예약 회차가 도는 동안만 채운다 — 그 예약 줄의 기능 (`_start_scheduled_run`)
        self.scheduled_modules: list[str] | None = None
        # 원격 (09-28). 서버 확인을 통과하면 `_start_remote` 가 만든다
        self.remote: remote.Remote | None = None
        self.remote_run = False                 # 지금 회차가 웹의 [실행] 으로 시작됐다
        self._pending_remote: dict | None = None    # 실행 중에 받은 웹 설정 — 끝나면 적용한다
        self._pending_version = 0                   # 그 설정의 서버 판
        self._remote_seen: dict | None = None       # 서버와 맞춘 마지막 값 — 바뀐 것만 올린다
        # 자동 켜기 (09-28). 빌드본(exe)에서만 — 개발 중에 python 을 등록하지 않는다
        self.frozen = bool(getattr(sys, "frozen", False))
        self.autostart_var = tk.BooleanVar(value=bool(getattr(SETTINGS, "auto_start", None)))
        # 설정 탭의 안내 줄 (기능 고정 안내 등). `_build()` 가 채운다.
        self.notes: list[ttk.Label] = []
        self._ran = False                       # 이 창에서 한 번이라도 실행했나 — [처음 화면]·[진행 화면] 단추
        self._save_job: str | None = None       # 바로 저장 (09-29) — 치는 동안 모은다

        super().__init__(root)
        self._repair_paths()
        self._verify_build()

        root.title("주문 자동화")
        # 창 크기는 고정이다 (09-29). 홈은 이 안에 늘 다 들어가고, 설정 탭만 스크롤한다 — 예전의
        # "작은 화면이면 표·도움말을 줄이기" 는 필요 없어졌다. 작업 영역보다 크게 만들지는 않는다.
        scale = ui_scale()
        _left, top, _right, bottom = work_area()
        room = bottom - top - int(WINDOW_FRAME_MARGIN * scale)
        self.window_height = min(int(WINDOW_H * scale), room)
        root.geometry(f"{int(WINDOW_W * scale)}x{self.window_height}")
        root.minsize(int(620 * scale), min(int(480 * scale), room))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.mail_pw_var.trace_add("write", self._on_field_changed)
        self.download_var.trace_add("write", self._on_field_changed)    # [찾기] 뒤에도 [실행] 을 다시 판단
        for var in self.module_vars.values():
            var.trace_add("write", self._on_field_changed)
        self._warn_if_not_baked()
        self._update_run_state()
        self._refresh_modules_line()
        self._refresh_last()

    def _repair_paths(self) -> None:
        """ERPia 경로를 이 PC 에 맞춘다 — 켤 때와 [실행] 때마다 (09-23 → 09-29 사용자 요청).

        ERPia 프로그램: ERPia 를 쓰는 기능일 때, 비었거나·없는 파일이거나·ERPia 가 아닌 exe 면 찾아 넣는다.
        엑셀 저장 폴더는 **되돌리지 않는다** (기본값 없음, 10-02) — 비었거나 없는 폴더면 [실행] 이 '입력 필요' 로 잠긴다.
        여기서는 칸만 바꾼다. 파일에는 [실행] 이 쓰고, 그 전에 다른 칸을 고치면 바로 저장이 같이 쓴다.
        """
        current = self.exe_var.get().strip().strip('"')
        if self._needs_exe() and not is_erpia_exe(current):     # 찾기는 등록 정보·바로가기를 훑는다
            found = find_exe()
            if found:
                log.info("ERPia 프로그램 경로를 이 PC 에 맞췄다: %s → %s", current or "없음", found)
                self.exe_var.set(found)
            else:
                log.warning("이 PC 에서 ERPia 프로그램을 찾지 못했다 (칸의 값: %s)", current or "없음")

    def _fail_no_erpia(self) -> None:
        """ERPia 가 이 PC 에 없어 [실행] 이 돌아선다 — 로그·[실행 기록] 에 실패로 남기고 알린다 (09-29)."""
        log.error("%s (칸의 값: %s)", NO_ERPIA, self.exe_var.get().strip() or "없음")
        try:
            # 이력은 `_give_up_run` 전에 — 그 뒤면 '예약 실행'·'원격 실행' 표시와 회차의 기능이 사라진다
            history.record(summary=f"실패 — {NO_ERPIA}", step_rows=[], attention=[],
                           trigger=self._trigger(), report=None, modules=self._module_names())
            self._refresh_last()
            self.history_pane.refresh()
            self._tell_problem("ERPia 프로그램 없음", NO_ERPIA_MESSAGE)
            self.status_var.set(NO_ERPIA + ".")     # 무인이면 `_tell_problem` 이 알림 전문을 넣었다 — 한 줄로
        finally:
            self._give_up_run(NO_ERPIA)             # 예약 회차는 무슨 일이 있어도 닫는다 (안 닫으면 다음 회차가 없다)

    def _give_up_run(self, reason: str) -> None:
        """시작도 못 하고 돌아섰다. **무인 예약 회차**면 [실행 기록]·서버에도 남긴다 — 화면을 보는 사람이 없다 (10-02 검토).

        ERPia 없음은 `_fail_no_erpia` 가 이미 남겼다. 원격 실행은 사유가 웹 명령 기록에 남는다.
        """
        if self.unattended and not self.remote_run and reason != NO_ERPIA:
            text = "시작하지 못함 — " + reason.replace("입력값이 비어 있다: ", "입력 필요: ")
            names = self._module_names()
            run = self.autorunner.current if self.autorunner is not None else None
            try:
                history.record(summary=f"실패 — {text}", step_rows=[], attention=[],
                               trigger=self._trigger(), report=None, modules=names)
                reporter = self._reporter()
                if reporter is not None:
                    reporter.skipped(run.planned_at if run else time.time(), text, modules=names)
                self._refresh_last()
                self.history_pane.refresh()
            except Exception:
                log.exception("시작하지 못한 예약 회차를 기록하지 못했다")   # 회차는 아래에서 그래도 닫는다
        super()._give_up_run(reason)

    # ------------------------------------------------------------ 서버 확인
    def _verify_build(self) -> None:
        """이 PC 에서 이 빌드를 쓸 수 있는지 서버에 확인한다 (09-28).

        `telemetry.verify` 가 `ingest` 를 **빈 이벤트**로 한 번 부른다 — 서버는 처음 보는 빌드면 이 PC 로
        묶고, 이미 다른 PC 에 묶였으면 거부한다. 그래서 등록과 확인이 같은 호출이다.

        네트워크 스레드에서 돌고 결과는 `root.after` 로 받는다. 끝날 때까지 [실행] 이 잠긴다 —
        확인 전에 돌면 "다른 PC 에서 사용 불가" 가 무의미하다. 네트워크 실패는 1분 뒤 다시.
        """
        if not (getattr(SETTINGS, "server_build_id", None) or "").strip():
            return                                  # 서버를 쓰지 않는 빌드 — 막지 않는다
        self._set_verify("pending", "서버에 이 PC 를 확인하는 중입니다...")
        log.info("서버 확인 시작 (빌드 ID 있음)")
        # 결과는 스레드가 여기 두고, GUI 스레드가 `after` 로 집어 간다 — 스레드에서 `root.after` 를 부르면
        # mainloop 밖(확인 도구)에서 "main thread is not in main loop" 가 난다
        self._verify_result: tuple[str, str] | None = None

        def work() -> None:
            try:
                self._verify_result = telemetry.verify(SETTINGS)
            except Exception as exc:                    # noqa: BLE001 — 확인 때문에 창이 죽으면 안 된다
                self._verify_result = (telemetry.VERIFY_LATER, f"확인 중 오류: {type(exc).__name__}")

        threading.Thread(target=work, daemon=True, name="verify").start()
        self.root.after(200, self._poll_verify)

    def _poll_verify(self) -> None:
        result = self._verify_result
        if result is None:
            self.root.after(200, self._poll_verify)
            return
        self._verify_result = None
        state, text = result
        if state == telemetry.VERIFY_OK:
            self._set_verify("", "")
            log.info("서버 확인 통과")
            if not self._missing():                 # 비어 있는 값이 있으면 '입력 필요' 를 그대로 둔다
                self.status_var.set("서버 확인을 마쳤습니다. [실행]을 누르세요.")
            self._start_remote()
            return
        if state == telemetry.VERIFY_BLOCKED:
            log.warning("서버가 이 PC 를 거부했다: %s", text)
            self._set_verify("blocked", text)
            return
        log.warning("서버 확인 실패(다시 시도): %s", text)
        self._set_verify("pending", text)
        self.root.after(VERIFY_RETRY_MS, self._verify_build)

    def _set_verify(self, state: str, text: str) -> None:
        self.verify_state = state
        self.verify_text = text
        self._update_run_state()

    def _update_run_state(self) -> None:
        super()._update_run_state()
        if getattr(self, "verify_state", "") and not self.busy:
            self.status_var.set(self.verify_text)
        fix = getattr(self, "fix_btn", None)
        if fix is not None:
            # 사람이 넣을 수 있는 값이 비었을 때만 — 서버 확인·구운 값은 여기서 고칠 수 없다
            fixable = not self.busy and any(key in FIX_TARGETS for key in self._missing())
            fix.grid() if fixable else fix.grid_remove()

    def _on_field_changed(self, *args: object) -> None:
        super()._on_field_changed(*args)
        if getattr(self, "modules_var", None) is not None:
            self._refresh_modules_line()

    # ------------------------------------------------------------------ UI
    # 09-29 개편 (사용자 결정 — docs/HANDOFF.md 3절·5절). 한 장에 세로로 쌓던 것을 위쪽 탭 넷으로 나눈다.
    #   홈      상태 한 줄 + 큰 [실행] + 실행할 기능·다음 예약·마지막 실행. [실행] 하면 같은 자리가 진행 화면
    #   예약    자동 실행 칸 (자주 바꾸는 것은 이것뿐이다)
    #   실행 기록  지난 실행 + 건너뛴 예약 한 표
    #   설정    기능별 묶음, 도움말은 아래 한 줄, **바꾸면 바로 저장**. 이 탭만 스크롤한다
    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.home_tab = ttk.Frame(self.tabs, padding=(12, 10, 12, 8))
        self.schedule_tab = ttk.Frame(self.tabs, padding=(10, 8))
        self.history_tab = ttk.Frame(self.tabs, padding=(10, 8))
        self.settings_tab = ttk.Frame(self.tabs)
        for frame, text in ((self.home_tab, TAB_HOME), (self.schedule_tab, TAB_SCHEDULE),
                            (self.history_tab, TAB_HISTORY), (self.settings_tab, TAB_SETTINGS)):
            self.tabs.add(frame, text=f"  {text}  ")
        self._build_home(self.home_tab)
        self._build_settings(self.settings_tab)
        self._build_schedule(self.schedule_tab)
        self.history_pane = HistoryPane(self.history_tab)
        self.history_pane.frame.pack(fill="both", expand=True)
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # 실행 중에 잠글 위젯. 다른 탭도 잠근다 — 실행 중에는 홈에 고정 (`_set_busy`, 09-29)
        self.input_widgets = (
            self.modules_btn, self.schedule_btn,
            *self.module_checks,
            self.exe_entry, self.browse_btn, self.download_entry, self.download_btn,
            self.mail_pw_entry, self.pw_entry,
            self.auto_check, self.excel_check,
            self.courier_entry, self.box_entry,
            *self.mode_radios, *self.sales_radios,
            self.hold_entry, self.excel_pw_entry, self.sms_combo, self.adb_entry,
        )

    def _build_home(self, home: ttk.Frame) -> None:
        scale = ui_scale()
        home.columnconfigure(0, weight=1)
        home.rowconfigure(1, weight=1)

        # 상태 한 줄 — 늘 보인다. 값이 비어 막혔으면 고칠 칸으로 데려가는 단추를 붙인다
        bar = ttk.Frame(home)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status_var, anchor="w", foreground="#1f5f9e",
                  wraplength=int(560 * scale)).grid(row=0, column=0, sticky="ew")
        self.fix_btn = ttk.Button(bar, text="설정에서 넣기", command=self._go_fix)
        self.fix_btn.grid(row=0, column=1, padx=(8, 0))
        self.fix_btn.grid_remove()

        # 대기 화면. [실행] 하면 이 자리를 진행 화면이 쓴다 (`_set_form_visible`)
        form = self.form = ttk.Frame(home)
        form.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        form.columnconfigure(1, weight=1)
        self.modules_var = tk.StringVar()
        self.next_var = tk.StringVar(value="자동 실행 꺼짐")
        self.last_var = tk.StringVar()
        ttk.Label(form, text="실행할 기능", width=12, anchor="w").grid(row=0, column=0, sticky="nw")
        ttk.Label(form, textvariable=self.modules_var, anchor="w",
                  wraplength=int(470 * scale)).grid(row=0, column=1, sticky="ew")
        self.modules_btn = ttk.Button(form, text="바꾸기", width=10, command=self._go_modules)
        self.modules_btn.grid(row=0, column=2, padx=(8, 0), sticky="n")
        # 가장 중요한 단추라 크게, 가운데에 (09-29 — 예전에는 왼쪽 위 작은 단추였다)
        font = tkfont.nametofont("TkDefaultFont").copy()
        font.configure(size=font.cget("size") + 3, weight="bold")
        self._run_font = font                       # 참조를 쥐고 있어야 글꼴이 사라지지 않는다
        ttk.Style().configure(RUN_STYLE, font=font, padding=(36, 12))
        self.run_btn = ttk.Button(form, text="실행", style=RUN_STYLE, command=self.on_run)
        self.run_btn.grid(row=1, column=0, columnspan=3, pady=(22, 22))
        ttk.Label(form, text="다음 예약", width=12, anchor="w").grid(row=2, column=0, sticky="nw")
        # 둘째 줄에 예약 전 휴대폰 인증 점검 결과가 붙는다 (10-01)
        ttk.Label(form, textvariable=self.next_var, anchor="w", justify="left",
                  wraplength=int(470 * scale)).grid(row=2, column=1, sticky="ew")
        self.schedule_btn = ttk.Button(form, text="예약 바꾸기", width=10,
                                       command=lambda: self._show_tab(self.schedule_tab))
        self.schedule_btn.grid(row=2, column=2, padx=(8, 0))
        ttk.Label(form, text="마지막 실행", width=12, anchor="w").grid(row=3, column=0, sticky="nw", pady=(12, 0))
        ttk.Label(form, textvariable=self.last_var, anchor="w", justify="left",
                  wraplength=int(560 * scale)).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(12, 0))
        # 빌드에 값이 덜 구워졌을 때만 채워지는 자리. 평소에는 비어 있다.
        self.baked_warning = ttk.Label(form, text="", foreground="#b00020", justify="left")
        self.baked_warning.grid(row=4, column=0, columnspan=3, pady=(12, 0), sticky="w")

        # 진행 화면 — 단계 표 + 상세 + 로그
        run_view = self.run_view = ttk.Frame(home)
        run_view.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        run_view.columnconfigure(0, weight=1)
        for row in (0, 1, 2):
            run_view.rowconfigure(row, weight=1)
        self.step_pane = PipelinePane(run_view, row=0, scale=scale)
        self.detail_pane = DetailPane(run_view, row=1, height=5)
        self.step_pane.attach_detail(self.detail_pane)
        log_frame = ttk.LabelFrame(run_view, text="진행 로그", padding=6)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 4))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="none", height=6, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("error", foreground="#b00020")
        self.log_text.tag_configure("warning", foreground="#a06000")
        # 켤 때는 대기 화면만. 진행 화면은 [실행] 뒤에 나온다.
        self.progress_frames = (run_view,)
        self._set_progress_visible(False)

        # 아래 단추 줄 — 지금 쓸 수 있는 것만 보인다 (`_sync_actions`)
        actions = ttk.Frame(home)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(3, weight=1)
        # 도는 실행을 멈추고 잇는다 — 오버레이의 같은 단추와 같은 토큰 (09-29 사용자 요청)
        self.pause_btn = ttk.Button(actions, text=PAUSE_TEXT, width=10, command=self.toggle_pause)
        self.pause_btn.grid(row=0, column=0, padx=(0, 4))
        # ★ `_set_busy()` / `_on_report_ready()` (부모)가 이 단추들을 켜고 끈다. 이름을 바꾸지 않는다.
        # [일시정지] 옆에 붙이지 않는다 — 잘못 누르면 실행이 끝난다 (오버레이와 같은 이유)
        self.stop_btn = ttk.Button(actions, text="중단", width=8, command=self.on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=(12, 16))
        # 오버레이는 ERPia 위를 덮는다. 뒤를 봐야 할 때가 있어 여기서도 여닫는다 (사용자 요청 09-16)
        self.overlay_btn = ttk.Button(actions, text=OVERLAY_TEXT, command=self.toggle_overlay, state="disabled")
        self.overlay_btn.grid(row=0, column=2)
        # 지난 실행이 멈췄을 때만 보인다 (10-01). 대기 화면·진행 화면 어디서나
        self.resume_btn = ttk.Button(actions, text=RERUN_TEXT, command=self.on_resume)
        self.resume_btn.grid(row=0, column=3, sticky="e")
        self.form_btn = ttk.Button(actions, text="처음 화면", width=10, command=self.toggle_form)
        self.form_btn.grid(row=0, column=4, padx=(8, 0))
        self.report_btn = ttk.Button(actions, text="결과 보기", width=10, command=self.open_report,
                                     state="disabled")
        self.report_btn.grid(row=0, column=5, padx=(8, 0))
        self._sync_actions()

    def _build_settings(self, tab: ttk.Frame) -> None:
        scale = ui_scale()
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        background = ttk.Style().lookup("TFrame", "background") or None
        canvas = self.settings_canvas = tk.Canvas(tab, highlightthickness=0, borderwidth=0,
                                                  background=background)
        bar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        bar.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        body = self.settings_body = ttk.Frame(canvas, padding=(4, 4, 10, 4))
        body.columnconfigure(0, weight=1)
        inner = canvas.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner, width=e.width))
        # 휠은 설정 칸 위에 있을 때만 이 탭을 굴린다 — 다른 표의 휠을 뺏지 않는다
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", self._on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        known = set(self._known_modules())
        row = 0
        box = self._section(body, row, "실행할 기능")
        checks = ttk.Frame(box)
        checks.grid(row=0, column=0, columnspan=2, sticky="w")
        self.module_checks = []
        for position, module in enumerate(modules.ALL):
            check = ttk.Checkbutton(checks, text=module.name, variable=self.module_vars[module.id],
                                    command=self._schedule_save)
            if module.id in known:                  # 기능 고정 빌드는 그 기능만 보인다
                check.grid(row=0, column=position, padx=(0, 14))
            self._help_for(check, LOCKED_HELP if self.locked_modules is not None else MODULE_HELP)
            self.module_checks.append(check)
        note = ttk.Label(box, text=LOCKED_HELP if self.locked_modules is not None else MODULE_HELP,
                         font=self._small_font(), foreground="#666666", justify="left")
        note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.notes.append(note)

        row += 1
        erpia = self._section(body, row, "ERPia", shown=bool(known & ERPIA_MODULES))
        self.exe_entry, self.browse_btn = self._path_row(erpia, 0, "ERPia 프로그램", self.exe_var, self.on_browse)
        self._help_for(self.exe_entry, EXE_HELP)
        self.pw_entry = self._labeled(erpia, 1, "ERPia 비밀번호", self.pw_var, mask=True, help_text=ERPIA_PW_HELP)

        row += 1
        mail = self._section(body, row, modules.MAIL.name, shown=modules.MAIL.id in known)
        self.mail_pw_entry = self._labeled(mail, 0, "사이트 비밀번호", self.mail_pw_var, mask=True,
                                           help_text=MAIL_PW_HELP)
        self.download_entry, self.download_btn = self._path_row(
            mail, 1, "엑셀 저장 폴더", self.download_var, self.on_browse_download)
        self._help_for(self.download_entry, DOWNLOAD_HELP)
        ttk.Label(mail, text="인증 문자", width=14, anchor="w").grid(
            row=2, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        sms_box = ttk.Frame(mail)
        sms_box.grid(row=2, column=1, pady=(8, 0), sticky="ew")
        sms_box.columnconfigure(2, weight=1)
        self.sms_combo = ttk.Combobox(sms_box, textvariable=self.sms_var, state="readonly",
                                      width=16, values=[label for label, _s, _c in SMS_CHOICES])
        self.sms_combo.grid(row=0, column=0, padx=(0, 10))
        self.sms_combo.bind("<<ComboboxSelected>>", self._schedule_save, add="+")
        self._help_for(self.sms_combo, SMS_HELP)
        ttk.Label(sms_box, text="무선 주소").grid(row=0, column=1, padx=(0, 6))
        self.adb_entry = ttk.Entry(sms_box, textvariable=self.adb_addr_var)
        self.adb_entry.grid(row=0, column=2, sticky="ew")
        self._watch(self.adb_entry, SMS_HELP)

        row += 1
        orders = self._section(body, row, modules.ORDERS.name, shown=modules.ORDERS.id in known)
        ttk.Label(orders, text="수집방식", width=14, anchor="w").grid(
            row=0, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        source_box = ttk.Frame(orders)
        source_box.grid(row=0, column=1, pady=(8, 0), sticky="w")
        self.auto_check = ttk.Checkbutton(source_box, text="자동수집", variable=self.site_source_var,
                                          command=self._schedule_save)
        self.auto_check.grid(row=0, column=0, padx=(0, 16))
        self.excel_check = ttk.Checkbutton(source_box, text="엑셀수집", variable=self.excel_source_var,
                                           command=self._schedule_save)
        self.excel_check.grid(row=0, column=1)
        self._help_for(self.auto_check, SOURCE_HELP)
        self._help_for(self.excel_check, SOURCE_HELP)
        self.excel_pw_entry = self._labeled(orders, 1, "엑셀 비밀번호", self.excel_pw_var,
                                            help_text=EXCEL_PW_HELP)
        ttk.Label(orders, text="매출처리", width=14, anchor="w").grid(
            row=2, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        sales_box = ttk.Frame(orders)
        sales_box.grid(row=2, column=1, pady=(8, 0), sticky="w")
        self.sales_radios = []
        for index, value in enumerate(order_mapping.SALES_MODES):
            radio = ttk.Radiobutton(sales_box, text=SALES_LABELS[value], value=value,
                                    variable=self.sales_var, command=self._schedule_save)
            radio.grid(row=0, column=index, padx=(0, 16))
            self._help_for(radio, SALES_HELP)
            self.sales_radios.append(radio)

        row += 1
        wait = self._section(body, row, modules.LOGI_WAIT.name, shown=modules.LOGI_WAIT.id in known)
        self.hold_entry = self._labeled(wait, 0, "보류제외 상품코드", self.hold_var, help_text=HOLD_HELP)

        row += 1
        logi = self._section(body, row, modules.LOGI.name, shown=modules.LOGI.id in known)
        self.courier_entry = self._labeled(logi, 0, "택배사", self.courier_var, help_text=COURIER_HELP)
        self.box_entry = self._labeled(logi, 1, "박스", self.box_var, help_text=BOX_HELP)
        self.mode_radios = self._mode_selector(logi, 2, label_width=14)
        for radio in self.mode_radios:
            radio.configure(command=self._schedule_save)
            self._help_for(radio, MODE_HELP)

        # 자동 켜기 (09-28). 빌드본에서만 보인다 — 개발 중에 python 을 로그온 켜기로 등록하지 않는다.
        # 이것은 체크하는 순간 저장한다 (작업 스케줄러 등록까지 같이 한다)
        row += 1
        program = self._section(body, row, "프로그램", shown=self.frozen)
        self.autostart_check = ttk.Checkbutton(
            program, text="PC 에 로그온하면 켜고, 꺼져 있으면 5분 안에 다시 켜기",
            variable=self.autostart_var, command=self._on_autostart_toggle)
        self.autostart_check.grid(row=0, column=0, columnspan=2, sticky="w")
        self._help_for(self.autostart_check, AUTOSTART_HELP)

        # 도움말 한 줄 — 칸마다 붙이던 회색 설명 12줄을 이 한 줄로 (09-29)
        foot = ttk.Frame(tab, padding=(10, 6, 10, 8))
        foot.grid(row=1, column=0, columnspan=2, sticky="ew")
        foot.columnconfigure(0, weight=1)
        foot.rowconfigure(0, minsize=int(46 * scale))
        self.help_var = tk.StringVar(value=SETTINGS_HINT)
        ttk.Label(foot, textvariable=self.help_var, font=self._small_font(), foreground="#555555",
                  justify="left", wraplength=int(560 * scale)).grid(row=0, column=0, sticky="nw")
        self.saved_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.saved_var, font=self._small_font(),
                  foreground="#1b7f3a").grid(row=0, column=1, sticky="ne", padx=(8, 0))

    def _build_schedule(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        # 회차 기록은 [실행 기록] 탭에 합쳐 보인다 (`history=False`)
        self.autorun_pane = AutoRunPane(
            tab, schedule.from_settings(SETTINGS.as_dict(), known_modules=self._known_modules()),
            on_apply=self._apply_autorun,
            on_pause_toggle=self._toggle_autorun_pause,
            scale=ui_scale(),
            # 기능 고정 빌드면 예약도 그 기능 안에서만 고른다
            choices=tuple((module.id, module.name) for module in modules.ALL
                          if module.id in self._known_modules()),
            chosen=tuple(self._checked_modules()),
            history=False,
        )
        self.autorun_pane.grid(row=0, column=0, sticky="new")

    def _section(self, parent: ttk.Frame, row: int, title: str, shown: bool = True) -> ttk.LabelFrame:
        """설정 묶음 하나. 이 빌드에 없는 기능의 묶음은 숨긴다 — 칸은 만들어 둔다(잠금·저장이 칸을 본다)."""
        box = ttk.LabelFrame(parent, text=title, padding=(10, 4, 10, 10))
        box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(1, weight=1)
        if not shown:
            box.grid_remove()
        return box

    def _labeled(self, parent: ttk.Frame, row: int, label: str,
                 var: tk.StringVar, mask: bool = False, help_text: str = "") -> ttk.Entry:
        ttk.Label(parent, text=label, width=14, anchor="w").grid(
            row=row, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        entry = ttk.Entry(parent, textvariable=var, show="*" if mask else "")
        entry.grid(row=row, column=1, pady=(8, 0), sticky="ew")
        self._watch(entry, help_text)
        return entry

    def _path_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar,
                  command) -> tuple[ttk.Entry, ttk.Button]:
        """경로 칸 + [찾기]. 단추를 칸 안쪽 틀에 두어 다른 줄의 폭을 건드리지 않는다."""
        ttk.Label(parent, text=label, width=14, anchor="w").grid(
            row=row, column=0, padx=(0, 8), pady=(8, 0), sticky="w")
        box = ttk.Frame(parent)
        box.grid(row=row, column=1, pady=(8, 0), sticky="ew")
        box.columnconfigure(0, weight=1)
        entry = ttk.Entry(box, textvariable=var)
        entry.grid(row=0, column=0, sticky="ew")
        self._watch(entry)
        button = ttk.Button(box, text="찾기", width=8, command=command)
        button.grid(row=0, column=1, padx=(8, 0))
        return entry, button

    def _watch(self, entry: ttk.Entry, help_text: str = "") -> None:
        """사람이 친 것만 저장을 부른다 — 코드가 칸을 바꾼 것(웹 설정·경로 찾기)은 저장하지 않는다."""
        entry.bind("<KeyRelease>", self._schedule_save, add="+")
        if help_text:
            self._help_for(entry, help_text)

    def _help_for(self, widget: tk.Widget, text: str) -> None:
        widget.bind("<FocusIn>", lambda _e: self._on_field_focus(widget, text), add="+")

    def _on_field_focus(self, widget: tk.Widget, text: str) -> None:
        self.help_var.set(text)
        self._reveal(widget)

    def _reveal(self, widget: tk.Widget) -> None:
        """설정 탭에서 그 칸이 보이게 굴린다 (Tab 으로 옮겨 가거나 [설정에서 넣기] 로 왔을 때)."""
        canvas, body = self.settings_canvas, self.settings_body
        body.update_idletasks()
        total = max(body.winfo_height(), 1)
        top = widget.winfo_rooty() - body.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_top, view_bottom = (fraction * total for fraction in canvas.yview())
        if top < view_top:
            canvas.yview_moveto(max(top - 30, 0) / total)
        elif bottom > view_bottom:
            canvas.yview_moveto(max(bottom + 30 - (view_bottom - view_top), 0) / total)

    def _on_wheel(self, event) -> None:
        self.settings_canvas.yview_scroll(int(-event.delta / 120), "units")

    def on_browse(self) -> None:
        before = self.exe_var.get()
        super().on_browse()
        if self.exe_var.get() != before:
            self._schedule_save()

    def on_browse_download(self) -> None:
        current = self.download_var.get().strip().strip('"')
        initial = Path(current) if current and Path(current).is_dir() else Path.home()
        chosen = filedialog.askdirectory(title="엑셀을 저장할 폴더 선택", initialdir=str(initial))
        if not chosen:
            return
        self.download_var.set(str(Path(chosen)))
        log.info("엑셀 저장 폴더 선택: %s", self.download_var.get())
        self._schedule_save()

    # --------------------------------------------------------- 탭·홈 (09-29)
    def _show_tab(self, tab: ttk.Frame) -> None:
        if self.busy and tab is not self.home_tab:
            return                  # 실행 중에는 홈에 고정 — 다른 탭은 잠겨 있다 (09-29)
        self.tabs.select(tab)

    def _go_modules(self) -> None:
        """홈 [바꾸기] — 설정 탭 맨 위(실행할 기능)로. 아래로 굴려 둔 채면 체크가 안 보였다 (09-29 실기)."""
        self._show_tab(self.settings_tab)
        self.settings_canvas.yview_moveto(0)

    def _on_tab_changed(self, _event=None) -> None:
        if self.tabs.select() == str(self.history_tab):
            self.history_pane.refresh()

    def open_history(self) -> None:
        """예전의 [지난 실행] 창 — 이제 [실행 기록] 탭이다."""
        self._show_tab(self.history_tab)
        self.history_pane.refresh()

    def _refresh_modules_line(self) -> None:
        names = [module.name for module in modules.ALL if self.module_vars[module.id].get()]
        text = " → ".join(names) if names else "고른 기능이 없습니다 — [바꾸기] 로 고르세요"
        if self.locked_modules is not None:
            text += "  (이 프로그램에 고정)"
        self.modules_var.set(text)

    def _refresh_last(self) -> None:
        """홈의 '마지막 실행' 줄과 [결과 보기]. 켤 때와 실행이 끝날 때. 건너뛴 예약은 [실행 기록] 탭에서만 (10-01)."""
        # 파일 전체를 본다 (load 는 어차피 다 읽는다) — 건너뜀이 줄을 채워도 마지막 실행이 사라지지 않게
        entry = next((entry for entry in history.load(history.KEEP) if entry.get("state") != history.SKIPPED), None)
        if entry is None:
            self.last_var.set("아직 실행한 적이 없습니다.")
            return
        when, trigger, _names, result, took, attention = history.row_of(entry)
        # 멈춘 기능부터 다시 할 것 (10-01) — 기능 고정 빌드면 이 빌드가 아는 기능만
        self.resume_ids = modules.resume_from(entry, self._known_modules())
        stopped = next((module.name for module in modules.ALL
                        if self.resume_ids and module.id == self.resume_ids[0]), "")
        parts = [f"{when} ({trigger})", result, took, f"확인할 것 {attention}" if attention else "",
                 f"멈춘 곳: {stopped}" if stopped else ""]
        self.last_var.set(" · ".join(part for part in parts if part))
        if hasattr(self, "resume_btn"):
            self._sync_actions()
        report_path = entry.get("report") or ""
        if not self.busy and self.report_path is None and report_path and Path(report_path).is_file():
            self.report_path = Path(report_path)
            self.report_btn.configure(state="normal")

    def _sync_actions(self) -> None:
        """홈 아래 단추 줄 — 지금 쓸 수 있는 것만 보인다."""
        for button, shown in ((self.pause_btn, self.busy), (self.stop_btn, self.busy),
                              (self.overlay_btn, self.overlay is not None),
                              (self.resume_btn, bool(self.resume_ids) and not self.busy),
                              (self.form_btn, self._ran)):
            button.grid() if shown else button.grid_remove()

    def _set_form_visible(self, visible: bool) -> None:
        """대기 화면과 진행 화면은 같은 자리를 번갈아 쓴다."""
        self.form.grid() if visible else self.form.grid_remove()
        self.run_view.grid_remove() if visible else self.run_view.grid()
        self.form_btn.configure(text="진행 화면" if visible else "처음 화면")
        self._sync_actions()

    def _fit_to_content(self) -> None:
        """창 크기는 고정이다 (09-29) — 탭 안에서 대기 화면과 진행 화면이 자리를 바꿀 뿐이다."""

    def _set_busy(self, busy: bool) -> None:
        super()._set_busy(busy)
        if busy:
            self._ran = True
            self.tabs.select(self.home_tab)
            self._close_notice()            # 맨 위 알림 창이 ERPia 클릭을 가로채지 않게
        # 실행 중에는 홈에 고정 — 예약·실행 기록·설정 탭으로 가서 값을 바꾸지 못하게 (09-29 사용자 요청)
        for tab in (self.schedule_tab, self.history_tab, self.settings_tab):
            self.tabs.tab(tab, state="disabled" if busy else "normal")
        self.help_var.set(BUSY_HINT if busy else SETTINGS_HINT)     # 칸이 왜 잠겼는지
        self._sync_pause()
        self._sync_actions()

    # ------------------------------------------------ 일시정지 (09-29)
    def toggle_pause(self) -> None:
        """[일시정지]/[계속하기] — 도는 실행을 **다음 대기 지점에서** 멈춘다. 오버레이와 같은 토큰."""
        token = self.token
        if not self.busy or token is None or token.cancelled:
            return
        token.resume() if token.paused else token.pause()
        if self.overlay is not None:
            self.overlay.sync_pause_btn()
        self._sync_pause()

    def _sync_pause(self) -> None:
        paused = bool(self.busy and self.token is not None and self.token.paused)
        self.pause_btn.configure(text=RESUME_TEXT if paused else PAUSE_TEXT)
        if self.busy:
            self.status_var.set(PAUSED_STATUS if paused else "실행 중...")

    def _sync_overlay_btn(self) -> None:
        button = getattr(self, "overlay_btn", None)
        if button is None:
            return
        try:
            if self.overlay is None:
                button.configure(state="disabled", text=OVERLAY_TEXT)
            else:
                button.configure(state="normal",
                                 text="진행 표시 숨기기" if self.overlay.shown else "진행 표시 보기")
            self._sync_actions()
        except tk.TclError:
            pass    # 창이 닫히는 중 — 단추가 이미 없다

    def _on_report_ready(self, path) -> None:
        super()._on_report_ready(path)
        self._refresh_last()
        self.history_pane.refresh()

    def _go_fix(self) -> None:
        """[설정에서 넣기] — 비어 있는 첫 칸으로 데려간다."""
        self._show_tab(self.settings_tab)
        for key in self._missing():
            target = getattr(self, FIX_TARGETS.get(key, ""), None)
            widgets = target if isinstance(target, (list, tuple)) else [target]
            for widget in widgets:
                if widget is not None and str(widget.cget("state")) != "disabled":
                    widget.focus_set()
                    self._reveal(widget)
                    return

    # ------------------------------------------------ 바로 저장 (09-29)
    def _schedule_save(self, _event=None) -> None:
        """사람이 설정을 바꿨다. 잠깐 모았다가 저장한다 — 글자 하나마다 파일을 쓰지 않게."""
        if self.busy:
            return
        self._cancel_save()
        self._save_job = self.root.after(SAVE_DELAY_MS, self._autosave)

    def _cancel_save(self) -> None:
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
            self._save_job = None

    def _flush_save(self) -> None:
        """모아 둔 저장이 있으면 지금 쓴다 — [실행]·창 닫기 앞에서. 버리면 시작을 못 한 [실행] 뒤에 편집이 사라진다."""
        if self._save_job is not None:
            self._cancel_save()
            self._autosave()

    def _autosave(self) -> None:
        """**바뀐 값만** 쓴다. 빈 필수값·비밀번호는 쓰지 않는다 (09-29 검토).

        지우고 다시 치는 중에 저장되면 관리자가 구워 둔 비밀번호·택배사를 exe 옆 설정이 영구히 덮는다 —
        구운 값은 화면에서 되살릴 길이 없다. 비운 칸은 다음에 켜면 전의 값으로 돌아온다.
        """
        self._save_job = None
        if self.busy:
            return                                  # 실행 중에는 칸이 잠겨 있다. [실행] 이 이미 저장했다
        exe = self.exe_var.get().strip().strip('"')
        values = self._saved_values(exe, self.company_var.get().strip(), self.id_var.get().strip(),
                                    self.pw_var.get())
        if not exe:                                 # 빈 경로로 작업 폴더를 '.' 로 만들지 않는다
            values.pop("target_work_dir", None)     # 빈 target_exe 는 아래 빈 칸 규칙이 거른다 (알림도 거기서)
        for key in ("login_company_code", "login_user_id"):
            values.pop(key, None)                   # 고정값 — 화면에서 못 바꾼다 (`LOCKED_KEYS`)
        blank = [key for key in KEEP_IF_BLANK if key in values and not values[key]]
        for key in blank:
            values.pop(key)
        # 순서만 다른 목록은 같은 값이다 — 화면은 수집방식을 자동·엑셀 순으로 만든다 (09-29 실기)
        changed = {key: value for key, value in values.items()
                   if remote.normalize(key, getattr(SETTINGS, key, None)) != remote.normalize(key, value)}
        if not changed:
            if blank:
                self.saved_var.set("빈 칸은 저장하지 않았습니다")
            return                                  # Tab 으로 옮겨 가기만 했다 — 쓸 것이 없다
        try:
            save_local(**changed)
        except (OSError, ValueError, RuntimeError) as exc:     # RuntimeError: 비밀 감싸기 실패
            log.warning("설정을 저장하지 못했다: %s", exc)
            self.saved_var.set("저장하지 못했습니다")
            return
        self.saved_var.set(f"저장했습니다 {time.strftime('%H:%M:%S')}"
                           + (" · 빈 칸은 저장하지 않음" if blank else ""))
        self._push_remote()

    def _on_close(self) -> None:
        """창을 닫는다 — 모아 둔 저장이 있으면 먼저 쓴다. 저장이 터져도 창은 닫는다."""
        try:
            self._flush_save()
        except Exception:                           # noqa: BLE001 — 닫기를 막으면 안 된다
            log.exception("닫기 전 저장 중 오류")
        finally:
            self.root.destroy()

    # --------------------------------------------------------------- 값
    def _modules(self) -> list[str]:
        """이번 실행의 기능. **실행 순서대로.** 예약 회차면 그 예약 줄의 기능."""
        if self.scheduled_modules is not None:
            return list(self.scheduled_modules)
        return self._checked_modules()

    def _checked_modules(self) -> list[str]:
        """창에서 체크한 기능. 저장·입력 잠금은 이것을 본다 (예약 회차와 무관)."""
        return [module.id for module in modules.ALL
                if self.module_vars[module.id].get()]

    def _module_names(self) -> list[str]:
        """이력 표의 '기능' 칸 — 이번 실행(예약 회차면 그 줄)의 기능 이름."""
        chosen = set(self._modules())
        return [module.name for module in modules.ALL if module.id in chosen]

    def _known_modules(self) -> tuple[str, ...]:
        """이 빌드가 아는 기능. 기능 고정 빌드면 구운 것만 — 예약 줄의 다른 기능은 버린다."""
        return tuple(self.locked_modules) if self.locked_modules is not None else tuple(modules.IDS)

    def _slot_modules(self) -> set[str]:
        """예약 줄에 붙은 기능 전부."""
        pane = getattr(self, "autorun_pane", None)     # `_build()` 전에도 불린다
        if pane is None:
            return set()
        return {key for slot in pane.plan().slots() for key in slot.modules}

    def _values(self) -> dict[str, str]:
        """[실행]을 누를 수 있는지 판단할 값들. **고른 기능에 필요한 것만** 본다.

        화면에 없는 값(실행파일·업체코드·아이디)도 그대로 둔다. 빌드에 덜
        구워졌으면 **조용히 돌다 실패하는 대신** 여기서 막힌다.
        """
        chosen = set(self._modules())
        base = super()._values()
        # 비면 `입력 필요: 실행할 기능` 으로 막힌다.
        values = {"실행할 기능": " ".join(self._modules())}
        if getattr(self, "verify_state", ""):
            values[VERIFY_KEY] = ""          # 확인 중·거부 — 예약 회차도 `_give_up_run` 으로 돌아선다
        if chosen & ERPIA_MODULES:
            # '입력 필요: …' 는 [설정] 탭의 칸 이름으로 (09-29 실기). ERPia 경로는 [실행] 이 찾아 넣는다
            # (`_repair_paths`) — 비었다고 막지 않고, 못 찾으면 `_fail_no_erpia` 가 실패로 남긴다
            for key, label in (("업체코드", "업체코드"), ("아이디", "아이디"), ("비밀번호", "ERPia 비밀번호")):
                values[label] = base[key]
            # 이름이 없으면 어떤 exe 도 ERPia 로 보지 않는다 — 빌드에 굽는 값이라 화면 칸은 없다 (10-02)
            values["ERPia 실행 파일 이름"] = getattr(SETTINGS, "target_exe_name", None) or ""
        if modules.ORDERS.id in chosen:
            values["수집방식"] = base["수집방식"]
            sales = self.sales_var.get()
            values["매출처리"] = sales if sales in order_mapping.SALES_MODES else ""
        if modules.LOGI.id in chosen:
            for key in ("택배사", "박스", "자동/수동"):
                values[key] = base[key]
        if modules.MAIL.id in chosen:
            values["사이트 비밀번호"] = self.mail_pw_var.get()
            # 기본값 없음 (10-02) — 저장 폴더·인증 문자도 비면 막는다. 사이트 연결은 원본(git)에 없다
            folder = self.download_var.get().strip().strip('"')
            values["엑셀 저장 폴더"] = folder if folder and Path(folder).is_dir() else ""
            values["인증 문자"] = self.sms_var.get()
            if _sms_values(self.sms_var.get()) == ("adb", "wireless"):
                values["무선 주소"] = self.adb_addr_var.get().strip()
            values["메일 사이트 연결"] = "있음" if sites.available() else ""
        return values

    def _needs_excel_dir(self) -> bool:
        """올릴 엑셀은 방금 받은 것이다. 폴더를 사람이 지정하지 않는다."""
        return False

    def _needs_exe(self) -> bool:
        """메일만 고르면 ERPia 를 띄우지 않는다. 실행 파일이 없는 PC 여도 된다."""
        return bool(set(self._modules()) & ERPIA_MODULES)

    def _update_source_fields(self) -> None:
        """고르지 않은 기능에만 쓰이는 입력칸을 잠근다.

        `_set_busy(False)` 가 입력을 전부 풀고 이 함수를 부르므로, 실행이 끝나도
        잠금이 맞게 돌아온다.
        """
        super()._update_source_fields()
        # 예약 줄에만 있는 기능의 칸도 연다 — 잠그면 그 예약에 필요한 값을 넣을 수 없다.
        chosen = set(self._checked_modules()) | self._slot_modules()
        enable = {
            self.exe_entry: bool(chosen & ERPIA_MODULES),
            self.browse_btn: bool(chosen & ERPIA_MODULES),
            self.download_entry: modules.MAIL.id in chosen,
            self.download_btn: modules.MAIL.id in chosen,
            self.mail_pw_entry: modules.MAIL.id in chosen,
            self.pw_entry: bool(chosen & ERPIA_MODULES),
            self.auto_check: modules.ORDERS.id in chosen,
            self.excel_check: modules.ORDERS.id in chosen,
            self.courier_entry: modules.LOGI.id in chosen,
            self.box_entry: modules.LOGI.id in chosen,
            # 09-28 사용자 값 3종 — 그 값을 쓰는 기능을 골랐을 때만 연다
            self.hold_entry: modules.LOGI_WAIT.id in chosen,
            self.excel_pw_entry: modules.ORDERS.id in chosen,
            self.sms_combo: modules.MAIL.id in chosen,
            self.adb_entry: modules.MAIL.id in chosen,
        }
        for radio in self.mode_radios:
            enable[radio] = modules.LOGI.id in chosen
        for radio in self.sales_radios:
            enable[radio] = modules.ORDERS.id in chosen
        if self.locked_modules is not None:
            # 기능 고정 빌드. `_set_busy(False)` 가 입력을 전부 풀어도 여기서 다시 잠근다
            for check in self.module_checks:
                enable[check] = False
        for widget, on in enable.items():
            widget.configure(state="normal" if on else "disabled")

    def _plan(self) -> list:
        """고른 기능이 지나갈 단계. 기능을 하나도 안 고르면 `ValueError`(표를 비운다)."""
        chosen = modules.normalize(self._modules())
        sources: list[str] = []
        if modules.ORDERS in chosen:
            sources = erpia_flow.Options(
                exe="", company="", user_id="", password="",
                sources=self._sources()).normalized_sources()
        return modules.plan(chosen, sources)

    def _saved_values(self, exe: str, company: str,
                      user_id: str, password: str) -> dict[str, object]:
        values = super()._saved_values(exe, company, user_id, password)
        values["mail_password"] = self.mail_pw_var.get()
        values["download_base_dir"] = self.download_var.get().strip().strip('"') or None
        # 사용자가 고치는 값 3종 (09-28)
        values["hold_exclude_codes"] = _split_codes(self.hold_var.get())
        per_site, default_pw = _split_excel_pw(self.excel_pw_var.get())
        # 사이트별이 있으면 화면은 기본 비밀번호를 보이지 않는다 — 보이지 않는 값을 지우지 않는다 (09-29 검토)
        values["excel_passwords"] = per_site
        values["excel_password"] = default_pw or (getattr(SETTINGS, "excel_password", None) if per_site else None)
        source, connection = _sms_values(self.sms_var.get())
        if source != "adb":
            # 화면 글은 adb 일 때만 유선/무선을 가른다. 아니면 지금 값을 둔다 — 웹의 auto+무선을 usb 로 바꿔 올렸다
            connection = getattr(SETTINGS, "adb_connection", None) or connection
        values["sms_source"], values["adb_connection"] = source, connection
        values["adb_wireless_address"] = self.adb_addr_var.get().strip() or None
        values["sales_mode"] = self.sales_var.get() or None
        # 창에서 체크한 기능을 남긴다. 예약 회차의 기능으로 덮지 않는다.
        # 기능 고정 빌드는 구운 값이 전부라 exe 옆 설정에 쓰지 않는다.
        if self.locked_modules is None:
            values["run_modules"] = self._checked_modules()
        return values

    # ------------------------------------------------------- 자동 실행
    def start_autorun(self) -> None:
        """예약 시계를 돌린다. 창을 만든 뒤 한 번 부른다.

        ★ 설정이 꺼져 있어도 시계는 돌린다. 사람이 화면에서 켜는 순간
          바로 반응해야 하고, 꺼진 규칙은 `AutoRunner` 가 아무 일도 하지 않는다.
        """
        plan = schedule.from_settings(SETTINGS.as_dict(), known_modules=self._known_modules())
        runner = self.attach_autorun(plan, paused=autorun.load_paused())    # 예약 [일시정지] 는 껐다 켜도 이어진다 (10-01)
        if runner.paused:
            log.warning("예약 일시정지 상태로 켰다 — [예약] 탭이나 웹의 [계속하기] 로 푼다")
        if plan.enabled:
            log.warning("자동 실행이 켜져 있다 — %s. 사람 없이 실계정 저장이 "
                        "돈다는 뜻이다.", plan.describe())
            # ★ 켜져 있을 때만 경고한다. 꺼져 있으면 "실행할 시각이 없다" 는
            #   **문제가 아니라 정상**이다. 그것까지 경고로 남기면 켤 때마다
            #   경고가 하나 뜨고, 진짜 경고가 그 속에 묻힌다
            #   (2026-09-17 빌드본 실행에서 실제로 그렇게 나왔다).
            #   사람이 화면에서 값을 고칠 때는 `_apply_autorun` 이 알린다.
            for problem in plan.problems():
                log.warning("자동 실행 설정 문제: %s", problem)
        self.autorun_pane.refresh(runner)
        self.next_var.set(runner.status_line())

    def _apply_autorun(self, plan: schedule.Plan) -> None:
        """화면에서 값을 바꿨다. **저장하고** 다음 차례를 다시 잡는다."""
        for problem in plan.problems():
            log.warning("자동 실행 설정 문제: %s", problem)
        if self.autorunner is not None:
            self.autorunner.set_plan(plan)
        if not self.busy:
            self._update_source_fields()       # 예약 줄의 기능이 바뀌면 열 칸도 바뀐다
        try:
            save_local(**schedule.to_settings(plan))
        except OSError as exc:
            # 저장에 실패해도 이번 실행에는 반영돼 있다. 조용히 넘기지 않는다.
            log.warning("자동 실행 설정을 저장하지 못했다: %s", exc)
        self._push_remote()

    def _toggle_autorun_pause(self) -> None:
        if self.autorunner is not None:
            self._set_autorun_paused(not self.autorunner.paused, "창")

    def _set_autorun_paused(self, paused: bool, by: str) -> None:
        """예약 멈춤·재개 — 창 [예약] 탭과 웹 명령이 같이 쓴다. 파일에 남겨 껐다 켜도 이어진다 (10-01)."""
        runner = self.autorunner
        runner.paused = paused
        runner.paused_since = time.time() if paused else 0.0
        autorun.save_paused(paused, runner.paused_since)
        log.info("%s에서 자동 실행 %s", by, "멈춤" if paused else "재개")
        self.autorun_pane.refresh(runner)
        self.send_idle(force=True)          # 웹의 [일시정지]·[계속하기] 가 15분 기다리지 않게

    def on_autorun_tick(self) -> None:
        """1초에 한 번. 남은 시간과 회차 표를 갱신하고, 원격으로 받은 것을 적용하고,
        서버가 이 PC 를 거부했으면 실행을 잠근다."""
        if self.autorunner is not None:
            self.autorun_pane.refresh(self.autorunner)
            self._warn_before_run()
            self._precheck_before_run()
            line, note = self.autorunner.status_line(), self._phone_note()
            self.next_var.set(f"{line}\n{note}" if note else line)
        self._drain_remote()
        self._lock_if_unauthorized()

    def _lock_if_unauthorized(self) -> None:
        """보고가 401 로 거부되면 [실행] 을 잠근다 (09-28).

        빌드 ID 가 폐기됐거나 이 빌드가 다른 PC 에 묶인 것이다. 어느 쪽이든 사용자가 풀 수 없으므로
        다시 시도하지 않고 잠근다. 실행 중에는 건드리지 않는다 — 돌던 회차는 끝까지 간다.
        """
        reporter = getattr(self, "_reporter_obj", None)
        refused = (bool(reporter is not None and getattr(reporter, "unauthorized", False))
                   or bool(self.remote is not None and self.remote.unauthorized))
        if self.busy or self.verify_state or not refused:
            return
        log.warning("서버가 이 PC 를 거부했다 (401). 실행을 잠근다.")
        self._set_verify("blocked", "이 프로그램은 이 PC 에서 쓸 수 없습니다. 관리자에게 문의하세요.")

    def _slot_ids(self, planned: float) -> list[str] | None:
        """그 예약 차례의 기능 id (실행 순서). 같은 시각에 걸린 줄은 합친다 (09-21).
        걸린 줄이 없으면(간격 실행 등) None — 창에서 체크한 기능으로 돈다."""
        runner = self.autorunner
        slots = runner.plan.slots_at(planned) if runner else []
        if not slots:
            return None
        wanted: set[str] = set()
        for slot in slots:
            wanted |= set(slot.modules or self._checked_modules())
        return [key for key in modules.IDS if key in wanted]

    def _start_scheduled_run(self) -> None:
        """예약 줄에 붙은 기능으로 돈다 (09-21). 기능을 붙이지 않은 줄과 간격 실행은 창에서 체크한 기능이다."""
        run = self.autorunner.current if self.autorunner is not None else None
        ids = self._slot_ids(run.planned_at) if run else None
        if ids is not None:
            self.scheduled_modules = ids
            log.info("예약 실행 기능 — %s", ", ".join(
                module.name for module in modules.ALL if module.id in ids) or "없음")
        super()._start_scheduled_run()

    def _on_autorun_skipped(self, planned_at: float, reason: str) -> None:
        """건너뛴 예약 — 기록 파일에도 남긴다 (10-01, 전에는 앱을 끄면 사라졌다). 서버에는 회차마다 간다."""
        ids = self._slot_ids(planned_at) or self._checked_modules()
        names = [module.name for module in modules.ALL if module.id in ids]
        history.record_skipped(planned_at, reason, modules=names)
        reporter = self._reporter()
        if reporter is not None:
            reporter.skipped(planned_at, reason, modules=names)
        self.history_pane.refresh()

    # ------------------------------------------------ 맨 위 알림 창 (10-01)
    def _show_notice(self, kind: str, title: str, buttons: list[tuple[str, object]]) -> tk.StringVar:
        """맨 위에 뜨는 알림 창 하나. **모달이 아니다** — 흐름·예약을 막지 않는다. 사람이 없으면 아무도 안 누르고,
        실행이 시작되면 `_set_busy` 가 닫는다. 글은 돌려준 변수로 고친다."""
        self._close_notice()
        top = tk.Toplevel(self.root)
        top.title(title)
        top.attributes("-topmost", True)            # 최소화된 본 창에 묶이지 않는다 (transient 를 쓰지 않는다)
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self._close_notice)
        var = tk.StringVar()
        body = ttk.Frame(top, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, textvariable=var, justify="left", wraplength=int(440 * ui_scale())).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(anchor="e", pady=(14, 0))
        for text, command in buttons:
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(8, 0))
        self._notice, self._notice_kind, self._notice_var = top, kind, var
        return var

    def _close_notice(self) -> None:
        notice, self._notice, self._notice_kind = self._notice, None, ""
        if notice is not None:
            try:
                notice.destroy()
            except tk.TclError:
                pass    # 이미 닫혔다 (본 창과 같이 닫히는 중)

    # ------------------------------------------------ 예약 전 경고 (10-01)
    def _warn_before_run(self) -> None:
        """예약 5분 전 — 이 PC 에 같은 아이디로 로그인된 ERPia 가 있으면 '실행하면서 그 창을 닫는다, 저장하라' 를
        맨 위 창으로 알리고 [이번만 건너뛰기] 를 준다. 사람이 없으면 아무것도 누르지 않으니 예약 시각에 그대로 돈다.
        사람 [실행]·웹 [실행] 에는 경고하지 않는다 (바로 시작)."""
        runner = self.autorunner
        due, left = runner.next_run, runner.remaining()
        key = due
        blocked = due is None or self.busy or runner.paused or bool(self.verify_state)
        if self._notice_kind == NOTICE_ERPIA:
            if blocked or key != self._warned_key:
                self._close_notice()
            elif left is not None:
                self._notice_var.set(self._erpia_notice_text(due, left))
        if blocked or key == self._warned_key or left is None or not 0 < left <= WARN_BEFORE:
            return
        now = time.time()
        if now - self._warn_looked < WARN_LOOK_EVERY:
            return
        self._warn_looked = now
        if process.screen_locked():
            return                          # 볼 사람이 없다 — 이 회차는 어차피 건너뛴다
        if not set(self._slot_ids(due) or self._checked_modules()) & ERPIA_MODULES:
            self._warned_key = key          # 메일만 도는 회차는 ERPia 를 닫지 않는다
            return
        if not logged_in_pids(SETTINGS.login_company_code or "", SETTINGS.login_user_id or ""):
            return                          # 30초 뒤 다시 본다 — 그 사이 사람이 켤 수 있다
        self._warned_key = key
        var = self._show_notice(NOTICE_ERPIA, "곧 예약 실행",
                                [(SKIP_TEXT, self._skip_this), ("닫기", self._close_notice)])
        var.set(self._erpia_notice_text(due, left))
        log.info("예약 전 경고 — 같은 아이디로 로그인된 ERPia 가 있다 (예약 %s)",
                 time.strftime("%H:%M", time.localtime(due)))

    @staticmethod
    def _erpia_notice_text(due: float, left: float) -> str:
        return (f"{time.strftime('%H:%M', time.localtime(due))} 에 예약 실행이 시작됩니다 "
                f"({schedule.countdown(left)} 남음).\n\n"
                "이 PC 에 같은 아이디로 로그인된 ERPia 가 있습니다. 실행하면서 그 창을 닫고 다시 로그인하니, "
                "하던 작업을 저장해 두세요.\n\n"
                f"지금 ERPia 를 쓰는 중이면 [{SKIP_TEXT}] 를 누르세요. 다음 예약은 그대로 돕니다.")

    def _skip_this(self) -> None:
        """[이번만 건너뛰기] — 이번 예약 한 번만 돌지 않는다. 다음 예약은 그대로 (10-02 사용자 결정, 미루기 대신)."""
        runner = self.autorunner
        self._close_notice()
        if runner is None or runner.next_run is None or self.busy:
            return
        runner.skip(SKIP_BY_PERSON)         # [실행 기록]·서버에 건너뜀으로 남는다 (`_on_autorun_skipped`)
        self.autorun_pane.refresh(runner)
        self.send_idle(force=True)          # 웹의 '다음 예약' 도 다음 차례로

    # ------------------------------------------------ 예약 전 휴대폰 점검 (10-01)
    def _precheck_before_run(self) -> None:
        """메일 기능이 든 예약 30분 전부터 휴대폰 인증 준비를 **보기만** 한다 (`auth_code.precheck` — 앱을 띄우거나
        단추를 누르지 않는다). 5분마다 다시 본다. 그 회차에서 '안 됨' 이 처음 나오면 알림 창 + 서버 알림(담당자 메일).
        실행을 막지는 않는다 — 실행 때의 재연결(최대 5분)은 그대로다."""
        runner = self.autorunner
        self._collect_phone_result()
        key = runner.next_run
        if self._phone_seen is not None and self._phone_seen["key"] != key:
            self._phone_seen = None         # 다음 회차다 — 지난 결과는 버린다
        if key is None or self.busy or runner.paused or self.verify_state:
            return
        left = runner.remaining()
        if left is None or not PRECHECK_MIN_LEAD < left <= PRECHECK_BEFORE or time.time() < self._phone_next:
            return
        if self._phone_thread is not None and self._phone_thread.is_alive():
            return
        self._phone_next = time.time() + PRECHECK_AGAIN
        if modules.MAIL.id not in (self._slot_ids(key) or self._checked_modules()):
            return
        if process.screen_locked():
            return
        self._start_phone_check(key)

    def _start_phone_check(self, key: float) -> None:
        def work() -> None:
            try:
                ready, reason = self._phone_check()
            except Exception as exc:                    # noqa: BLE001 — 점검이 예약 시계를 죽이면 안 된다
                log.warning("예약 전 휴대폰 점검 중 오류: %s", exc)
                ready, reason = None, "휴대폰 인증 준비를 점검하지 못했습니다"
            self._phone_result = {"key": key, "ready": ready, "reason": reason, "at": time.time()}

        self._phone_started = time.time()
        self._phone_thread = threading.Thread(target=work, name="phone-precheck", daemon=True)
        self._phone_thread.start()

    def _phone_check(self) -> tuple[bool | None, str]:
        """점검 함수 하나 — 확인 도구가 바꿔 끼운다."""
        from collect import auth_code

        return auth_code.precheck()

    def _collect_phone_result(self) -> None:
        """스레드가 둔 결과를 GUI 스레드에서 집는다. 바뀌었을 때만 로그를 남긴다."""
        result, self._phone_result = self._phone_result, None
        if result is None:
            thread = self._phone_thread
            stuck = (thread is not None and thread.is_alive() and self.autorunner.next_run is not None
                     and time.time() - self._phone_started > PRECHECK_GIVE_UP)
            if stuck and (self._phone_seen is None or self._phone_seen["ready"] is not None):
                result = {"key": self.autorunner.next_run, "ready": None,
                          "reason": "휴대폰 인증 점검이 끝나지 않았습니다", "at": time.time()}
            else:
                return
        if result["key"] != self.autorunner.next_run:
            return          # 늦게 끝난 점검 — 그 차례는 이미 지났거나 도는 중이다 (10-02)
        old, self._phone_seen = self._phone_seen, result
        if old is None or (old["ready"], old["reason"]) != (result["ready"], result["reason"]):
            log.info("예약 전 휴대폰 점검 — %s", self._phone_note())
        # 손 실행이 도는 중이면 알리지 않는다 — 흐름이 휴대폰 연결을 쓰는 중이라 '안 됨' 이 나올 수 있다
        if result["ready"] is False and self._phone_alerted != result["key"] and not self.busy:
            self._phone_alerted = result["key"]     # 그 회차에 한 번만 — 알림 창·담당자 메일
            reporter = self._reporter()
            if reporter is not None:
                reporter.alert("phone", result["reason"])
            if self._notice is None:
                var = self._show_notice(NOTICE_PHONE, "휴대폰 인증 확인",
                                        [("다시 확인", self._recheck_phone), ("닫기", self._close_notice)])
                var.set(self._phone_notice_text(result["key"], result["reason"]))

    @staticmethod
    def _phone_notice_text(planned: float, reason: str) -> str:
        return (f"{time.strftime('%H:%M', time.localtime(planned))} 예약 실행에 메일 엑셀 받기가 있는데, "
                f"휴대폰 인증 준비가 안 됐습니다.\n\n{reason}\n\n"
                "그대로 두면 메일 엑셀 받기가 실패합니다 (다른 기능은 그대로 돕니다).")

    def _recheck_phone(self) -> None:
        """[다시 확인] — 사람이 고쳤는지 지금 본다. 결과는 홈 '다음 예약' 둘째 줄에 나온다."""
        self._close_notice()
        self._phone_next = 0.0

    def _phone_note(self) -> str:
        """홈 '다음 예약' 둘째 줄 — 그 회차의 휴대폰 점검 결과. 점검 전이면 빈 문자열."""
        seen, runner = self._phone_seen, self.autorunner
        if seen is None or runner is None or seen["key"] != runner.next_run:
            return ""
        when = time.strftime("%H:%M", time.localtime(seen["at"]))
        if seen["ready"]:
            return f"휴대폰 인증: 준비됨 ({when} 확인)"
        head = "안 됨" if seen["ready"] is False else "확인 못 함"
        return f"휴대폰 인증: {head} — {seen['reason']} ({when} 확인)"

    def _end_autorun_cycle(self, state: str, summary: str) -> None:
        self.resume_run = False             # [멈춘 곳부터 다시] 는 그 한 번뿐
        if self.remote_run:
            # 원격 실행은 예약 회차가 아니다 — 예약 시계에 끝났다고 알리면 없는 회차를 닫는다
            self.remote_run = False
            self.unattended = False
        else:
            super()._end_autorun_cycle(state, summary)
        if self.scheduled_modules is None:
            return
        self.scheduled_modules = None
        # [실행] 단추를 창의 체크 기준으로 되돌린다. 상태줄(결과)은 건드리지 않는다.
        if not self.busy:
            self.run_btn.configure(state="disabled" if self._missing() else "normal")

    # ------------------------------------------------------- 원격 (09-28)
    def _begin_run(self, start_step: str) -> None:
        self._flush_save()                  # 시작을 못 하고 돌아서도 방금 고친 값은 남긴다
        self._repair_paths()                # 예약·웹 [실행] 도 여기를 지난다. 맞춘 경로는 부모가 저장한다
        # 비어 있는 값(서버 확인 전·거부 포함)이 있으면 부모가 '입력 필요' 로 돌아선다 — ERPia 없음으로 쌓지 않는다
        if self._needs_exe() and not self._missing() and not is_erpia_exe(self.exe_var.get()):
            self._fail_no_erpia()
            return
        super()._begin_run(start_step)      # 홈으로 옮기고 다른 탭을 잠그는 것은 `_set_busy`
        if self.busy:
            self._push_remote()             # [실행] 이 방금 저장한 값을 서버에도 (바뀐 것이 있을 때만)

    def _trigger(self) -> str:
        if self.resume_run:
            return RERUN_TRIGGER
        return "원격 실행" if self.remote_run else super()._trigger()

    def on_resume(self) -> None:
        """[멈춘 곳부터 다시] — 지난 실행이 멈춘 기능부터 끝까지 **이번에만** 돈다 (10-01). 사람이 누른 실행이라
        무인 표시를 켜지 않는다(오류 창이 뜬다). 저장된 실행할 기능(run_modules)은 그대로다 (`_saved_values`)."""
        if self.busy or not self.resume_ids:
            return
        ids = list(self.resume_ids)
        log.info("멈춘 곳부터 다시 — %s", ", ".join(module.name for module in modules.ALL if module.id in ids))
        self.scheduled_modules = ids
        self.resume_run = True
        try:
            self.on_run()
        finally:
            if not self.busy:
                # 시작도 못 하고 돌아섰다 — 표시를 거둔다 (남으면 다음 [실행] 이 이 기능만 돈다)
                self.resume_run = False
                self.scheduled_modules = None
                self._update_run_state()

    def _start_remote(self) -> None:
        """서버 확인을 통과하면 원격 확인을 켠다 — 30초마다 웹 설정·명령을 가져온다."""
        if self.remote is not None:
            return
        self.remote = remote.from_settings(SETTINGS, locked_modules=self.locked_modules)
        if self.remote is None:
            return
        self._remote_seen = remote.snapshot(SETTINGS)
        # 서버에 아직 판이 없으면(새 빌드) 지금 값이 첫 판이 된다. 있으면 서버가 이것을 버리고 제 판을 준다
        self.remote.start(initial=self._remote_seen)
        log.info("원격 확인 켜짐 — %d초마다 웹 설정·명령을 가져온다", int(remote.POLL_SECONDS))

    def _push_remote(self) -> None:
        if self.remote is None:
            return
        now = remote.snapshot(SETTINGS)
        changed = {key: value for key, value in now.items()
                   if (self._remote_seen or {}).get(key) != value}
        if changed:
            self.remote.push(changed)
            self._remote_seen = now

    def _drain_remote(self) -> None:
        """1초 시계에서. 받은 설정·명령을 **GUI 스레드에서** 적용한다 (원격 스레드는 큐에 넣기만 한다)."""
        if self.remote is None:
            return
        for _ in range(DRAIN_LIMIT):                # 남은 것은 다음 1초에
            try:
                event = self.remote.inbox.get_nowait()
            except queue.Empty:
                break
            if event[0] == remote.SETTINGS_EVENT:
                self._pending_remote = {**(self._pending_remote or {}), **event[1]}
                self._pending_version = event[2]
                self._apply_pending_remote()
            else:
                _kind, command_id, kind, wanted = event
                try:
                    self._apply_pending_remote()    # [실행] 이 새 설정으로 돌게 먼저 적용한다
                    result = self._run_command(kind, wanted)
                except Exception as exc:            # noqa: BLE001 — 결과는 꼭 웹에 남긴다
                    log.exception("원격 명령 %s 처리 중 오류", kind)
                    result = f"PC 에서 오류 — {type(exc).__name__}"
                log.info("원격 명령 %s → %s", kind, result)
                self.remote.done(command_id, result)
        self._apply_pending_remote()                # 실행 중에 받아 둔 것 — 끝났으면 지금

    def _apply_pending_remote(self) -> None:
        """웹 설정을 적용한다. **실행 중이면 끝날 때까지 미룬다** — 도는 회차의 값을 중간에 바꾸지 않는다."""
        if self._pending_remote is None or self.busy:
            return
        values, self._pending_remote = self._pending_remote, None
        if self.locked_modules is not None:
            values.pop("run_modules", None)
        changed = {key: value for key, value in values.items()
                   if remote.normalize(key, getattr(SETTINGS, key, None)) != value}
        self._remote_seen = {**(self._remote_seen or {}), **values}
        if self.remote is not None:
            self.remote.applied(self._pending_version)     # 다시 켜면 이 판에서 이어간다
            # 서버 판에 없는 키(새로 생긴 설정 — 10-01 매출처리 방식)는 이 PC 값으로 채워 올린다. 받은 값은 되올리지 않는다
            missing = {key: value for key, value in remote.snapshot(SETTINGS).items() if key not in values}
            if missing:
                self.remote.push(missing)
        if not changed:
            return
        log.info("웹에서 바꾼 설정을 받았다 — %s", ", ".join(sorted(changed)))
        try:
            save_local(**changed)
        except (OSError, ValueError) as exc:
            log.warning("웹 설정을 저장하지 못했다 (이번 실행에는 반영): %s", exc)
            for key, value in changed.items():
                setattr(SETTINGS, key, value)
        self._show_settings(changed)
        self.status_var.set("웹에서 바꾼 설정을 받았습니다.")

    def _show_settings(self, changed: dict) -> None:
        """바뀐 설정을 화면 칸에 옮긴다. 칸을 바꿔도 저장·서버 올리기는 일어나지 않는다."""
        if "run_modules" in changed:
            chosen = set(SETTINGS.run_modules or [])
            for key, var in self.module_vars.items():
                var.set(key in chosen)
        if "collect_sources" in changed:
            sources = SETTINGS.collect_sources or []
            self.site_source_var.set(SOURCE_SITE in sources)
            self.excel_source_var.set(SOURCE_EXCEL in sources)
        if "delivery_company" in changed:
            self.courier_var.set(SETTINGS.delivery_company or "")
        if "delivery_box" in changed:
            self.box_var.set(SETTINGS.delivery_box or "")
        if "logistics_mode" in changed:
            self.mode_var.set((SETTINGS.logistics_mode or "").strip())
        if "sales_mode" in changed:
            # ★ 빠지면 라디오가 옛 값이라 다음 [실행]·바로 저장이 웹 값을 되돌려 덮고 서버에도 되올린다
            self.sales_var.set((getattr(SETTINGS, "sales_mode", None) or "").strip())
        if "hold_exclude_codes" in changed:
            self.hold_var.set(_join_codes(SETTINGS.hold_exclude_codes))
        if {"sms_source", "adb_connection"} & set(changed):
            self.sms_var.set(_sms_label(SETTINGS.sms_source, SETTINGS.adb_connection))
        if "adb_wireless_address" in changed:
            self.adb_addr_var.set(SETTINGS.adb_wireless_address or "")
        if any(key.startswith("auto_run_") for key in changed):
            plan = schedule.from_settings(SETTINGS.as_dict(), known_modules=self._known_modules())
            for problem in plan.problems():
                log.warning("웹에서 받은 예약 설정 문제: %s", problem)
            self.autorun_pane.set_plan(plan)
            if self.autorunner is not None:
                self.autorunner.set_plan(plan)
                self.autorun_pane.refresh(self.autorunner)
        self._update_source_fields()
        self._update_run_state()
        self._refresh_modules_line()

    def _run_command(self, kind: str, wanted: list[str]) -> str:
        """웹의 명령 하나. 돌려준 말이 웹의 명령 목록에 결과로 남는다."""
        if kind == remote.STOP:
            if not self.busy or self.token is None or self.token.cancelled:
                return "실행 중이 아니라 할 일이 없다"
            self.token.cancel("웹에서 [RPA 종료]")
            self.stop_btn.configure(state="disabled")
            self.status_var.set("웹에서 RPA 종료 요청 — 지금 단계에서 빠져나옵니다...")
            return "RPA 종료 요청함 — 지금 단계에서 빠져나온다 (저장된 것은 그대로)"
        if kind in (remote.PAUSE, remote.RESUME):
            if self.autorunner is None:
                return "예약 시계가 없다"
            self._set_autorun_paused(kind == remote.PAUSE, "웹")
            return "일시정지 — 예약을 멈췄다" if self.autorunner.paused else "계속하기 — 예약이 다시 돈다"
        return self._remote_start(wanted)

    def _remote_start(self, wanted: list[str]) -> str:
        """웹의 [실행]. 예약 회차와 같은 조건으로 막는다 — 실행 중·확인 전·잠긴 화면."""
        if self.busy or self._starting:
            return "이미 실행 중이라 시작하지 않았다"
        if self.verify_state:
            return "서버 확인 전이거나 이 PC 에서 쓸 수 없어 시작하지 않았다"
        if process.screen_locked():
            return "화면이 잠겨 있어 시작하지 않았다 (잠금을 풀어야 돈다)"
        known = set(self._known_modules())
        if wanted:
            # 고른 기능이 이 빌드에 없으면 **거절한다** — 창의 체크로 대신 돌면 요청하지 않은 저장이 돈다
            chosen = [key for key in modules.IDS if key in set(wanted) & known]
            if not chosen:
                return "요청한 기능이 이 PC 의 빌드에 없어 시작하지 않았다"
        else:
            chosen = [key for key in self._checked_modules() if key in known]
        if not chosen:
            return "실행할 기능이 없어 시작하지 않았다"
        names = ", ".join(module.name for module in modules.ALL if module.id in chosen)
        log.info("원격 실행 — %s (사람을 기다리는 창을 띄우지 않는다)", names)
        self.scheduled_modules = chosen
        self.remote_run = True
        self.unattended = True
        try:
            self.on_run()
        except Exception:
            if not self.busy:
                # 켜 둔 표시를 거둔다 — 남으면 다음 손 실행이 '원격 실행' 이 되고 오류 창이 안 뜬다
                self.remote_run = self.unattended = False
                self.scheduled_modules = None
            raise
        if self.busy:
            return f"시작함 — {names}"
        # 시작도 못 하고 돌아섰다 (`_give_up_run` → `_end_autorun_cycle` 이 표시를 거뒀다)
        return f"시작하지 못함 — {self.status_var.get() or '입력값을 확인할 것'}"[:200]

    # ------------------------------------------------------- 자동 켜기 (09-28)
    def ask_autostart(self) -> None:
        """빌드본이 처음 뜰 때 한 번 묻는다 (사용자 확정 09-28 — 동의를 받고 등록)."""
        yes = messagebox.askyesno(
            "자동으로 켜기",
            "PC 에 로그온하면 이 프로그램을 자동으로 켤까요?\n\n"
            "· 창을 닫아도 5분 안에 다시 켜집니다 (작업 표시줄에 작게).\n"
            "· 예약 시각에 돌고, 웹에서 [실행] 을 누르면 돕니다.\n"
            "· 나중에 [설정] 탭에서 끌 수 있습니다.")
        self.autostart_var.set(yes)
        self._on_autostart_toggle()

    def ensure_autostart(self) -> None:
        """켜 두기로 했으면 등록돼 있는지 본다 — 지워졌거나 exe 를 옮겼으면 다시 건다."""
        if not self.frozen or not getattr(SETTINGS, "auto_start", None):
            return
        exe = instance.exe_path()
        # 인자까지 본다 — 09-28 판 작업은 `--task` 없이 띄워 넘기기가 안 된다 (09-29)
        if not autostart.enabled(exe, task_args=autostart.TASK_ARGS):
            self._set_autostart(True)

    def _on_autostart_toggle(self) -> None:
        self._set_autostart(bool(self.autostart_var.get()))

    def _set_autostart(self, on: bool) -> None:
        exe = instance.exe_path()
        try:
            if on:
                autostart.enable(exe, task_args=autostart.TASK_ARGS)
            else:
                autostart.disable(exe)
        except OSError as exc:
            log.warning("자동 켜기를 %s 못했다: %s", "등록하지" if on else "지우지", exc)
            self.autostart_var.set(not on)
            self._tell_problem("자동 켜기", f"자동 켜기를 바꾸지 못했습니다.\n{exc}", kind="warning")
            return
        try:
            save_local(auto_start=on)
        except (OSError, ValueError) as exc:
            log.warning("자동 켜기 선택을 저장하지 못했다: %s", exc)

    def _call_flow(self, options, hooks) -> "Result":
        """흐름은 통합과 같다.

        ★ `_run_worker` 를 통째로 재정의하지 않는다. 그렇게 하면 부모의 예외
          분기(로그인 실패 시 인스턴스 닫기 / traceback / 중복 실행 안내)를
          전부 잃는다. 고른 기능만 여기서 얹는다.
        """
        options.modules = self._modules()
        return full_flow.run(options, hooks=hooks)

    # ------------------------------------------------------- 빌드 값 점검
    def _missing_baked(self) -> list[str]:
        """이 빌드가 아는 기능에 필요한 구운 값 중 빈 것. False·0 은 값이다 (읽은 메일도 / 오늘만)."""
        need = modules.baked_required(self._known_modules())
        return [BAKED_LABELS.get(name, name) for name in need
                if getattr(SETTINGS, name, None) in (None, "", [])]

    def _warn_if_not_baked(self) -> None:
        """화면에 없는 값이 비어 있으면 **왜 실행이 막히는지** 알려 준다.

        이 창에는 그 값을 넣을 칸이 없다. 아무 설명 없이 [실행]만 비활성이면
        사용자가 할 수 있는 일이 없다.
        """
        missing = self._missing_baked()
        if not missing:
            log.info("빌드에 구워 넣은 값이 모두 있다 (%d항목).", len(modules.baked_required(self._known_modules())))
            # 빈 줄도 여백째 자리를 먹는다 — 작은 화면에서 모자랐다 (09-21, 예약 기능 줄)
            self.baked_warning.grid_remove()
            return
        text = ("이 빌드에 값이 덜 들어 있습니다: " + ", ".join(missing)
                + "\n관리자에게 다시 빌드를 요청하거나, "
                  "exe 옆 config\\settings.local.json 에 값을 넣으세요.")
        log.error("빌드에 구워 넣은 값이 비어 있다: %s", ", ".join(missing))
        self.baked_warning.configure(text=text)


def _locked_modules() -> list[str] | None:
    """기능 고정 빌드(`run_modules_locked`)면 **구운** `run_modules`. 아니면 None.

    exe 옆 `settings.local.json` 이 `run_modules` 를 덮어도 고정은 구운 값이다 — 그래서
    번들 안의 구운 파일을 직접 읽는다. 못 읽으면 지금 설정값으로 대신한다.
    """
    if not getattr(SETTINGS, "run_modules_locked", False):
        return None
    chosen = None
    try:
        import json

        from config.settings import BAKED_SETTINGS_PATH

        if BAKED_SETTINGS_PATH.is_file():
            chosen = json.loads(BAKED_SETTINGS_PATH.read_text(encoding="utf-8")).get("run_modules")
    except (OSError, ValueError) as exc:
        log.warning("구운 설정을 읽지 못했다(지금 설정값을 쓴다): %s", exc)
    if not isinstance(chosen, list):
        chosen = list(getattr(SETTINGS, "run_modules", None) or [])
    locked = [key for key in modules.IDS if key in chosen]
    if not locked:
        # 기본값 없음 (10-02) — 전부로 메우지 않는다. 빈 고정이면 [실행] 이 '입력 필요: 실행할 기능' 으로 잠긴다
        log.error("기능 고정 빌드인데 고정된 기능이 없다 — 빌드를 다시 만들어야 한다")
    return locked


def run() -> int:
    from utils.dpi import ensure_dpi_awareness
    from utils.logger import setup_logging

    from config.settings import BAKED_SETTINGS_PATH, LOCAL_SETTINGS_PATH, log_locked_ignored

    ensure_dpi_awareness()
    setup_logging()   # 로그 파일 경로는 setup_logging() 이 남긴다

    # 값이 어디서 왔는지 남긴다. **값 자체는 남기지 않는다.**
    # 이것이 없으면 "설정이 안 먹는다" 를 로그만 보고 가릴 수 없다.
    log.info("설정 출처 — 빌드에 구운 값: %s / exe 옆 설정: %s",
             "있음" if BAKED_SETTINGS_PATH.exists() else "없음",
             LOCAL_SETTINGS_PATH if LOCAL_SETTINGS_PATH.exists() else "없음")
    # 설정은 이 모듈을 불러오는 순간 읽혔다 — 그때 막은 고정값은 로그가 열린 지금 남긴다 (09-28)
    log_locked_ignored()

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass  # 테마가 없는 환경. 기본 테마로 진행한다.

    apply_scaling(root)
    window = RunWindow(root)
    window.start_autorun()
    # 자동 켜기가 띄운 것이면 작게 뜬다 (09-28). 사람이 연 것이면 처음 한 번 자동 켜기를 묻는다
    if autostart.BACKGROUND in sys.argv[1:]:
        log.info("자동 켜기로 떴다 — 최소화로 시작한다")
        root.iconify()
    elif window.frozen and getattr(SETTINGS, "auto_start", None) is None:
        root.after(800, window.ask_autostart)
    window.ensure_autostart()
    root.after(0, _log_startup)
    root.mainloop()
    return 0


def _log_startup() -> None:
    """켜는 데 걸린 시간을 남긴다 (09-29). 사용자 PC 에서 전후를 비교할 근거.

    onefile 은 부모(부트로더)가 먼저 떠서 임시 폴더에 풀고 자식(이 파이썬)을 띄운다 — 부모 시각부터 잰다.
    """
    now = time.time()
    child = process.created_epoch(os.getpid())
    if child is None:
        return
    meipass = getattr(sys, "_MEIPASS", None)
    onefile = bool(meipass) and Path(meipass).parent != Path(sys.executable).parent
    parent = process.created_epoch(os.getppid()) if onefile else None
    if parent is not None and parent <= child:
        log.info("켜는 데 %.2f초 — 풀기 %.2f초 + 파이썬·창 %.2f초", now - parent, child - parent, now - child)
    else:
        log.info("켜는 데 %.2f초 (창이 뜰 때까지)", now - child)
