r"""[개발 전용] 빌드 프로그램 — 설정값을 보고 고쳐서 **그 값대로** 실행용 exe 를 만든다.

    build_tool.bat   (= .venv\Scripts\python.exe main_build.py)

화면에 `config/settings.local.json` 의 값이 전부 보인다. 고쳐서 [저장] 하면 그 파일에
쓰고, [저장하고 빌드] 하면 **서버에 빌드를 등록하고**(`tools/register_build`, 09-28)
굽고(`tools/bake_settings`) PyInstaller 로 `dist/run/RPA_1.exe` 를 만든다 (`tools/build_run`).
만들어진 exe 는 실행용 창(`gui/run_app.py`) 그대로다.

**빌드 ID 는 서버가 준다** (09-28). 7절에 업체 이름·빌드 이름·관리자 계정을 넣으면 빌드할 때
자동으로 발급받아 굽는다 — 관리자가 대시보드에서 토큰을 복사해 붙이던 단계는 없앴다.
그 exe 는 **처음 켜는 PC 에 묶인다.** PC 를 바꾸려면 다시 빌드해서 준다 (사용자 확정 09-28).

**실행할 기능**은 둘 중 하나로 굽는다 (사용자 확정 2026-09-22):
  · 기능 고정 — exe 는 고른 기능만 돌고, 실행 창에서 바꿀 수 없다 (`run_modules_locked`)
  · 실행 창에서 선택 — 지금까지처럼 실행 창의 [실행할 기능] 체크로 고른다

배포본에는 들어가지 않는다 (`build_*.spec` 의 `DEV_ONLY`). PyInstaller 와 소스가 있는
개발 폴더에서만 돈다.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import customer
from config.settings import CUSTOMER, DEFAULTS, LOCAL_SETTINGS_PATH, SETTINGS, save_local
from gui.common import HELP_FONT_SIZE, apply_scaling, ui_scale
from collect.browser import CHANNELS
from orchestrator import modules

# 개발 도구 전용이라 굽지도, 화면에 보이지도 않는다 (`tools/bake_settings.SKIP_KEYS`).
# ERPia 경로·엑셀 저장 폴더는 실행 창이 그 PC 에서 찾아 채운다 (09-23).
# 빌드 ID 는 굽지만 화면에 칸이 없다 — [저장하고 빌드] 가 서버에서 받아 넣는다 (09-28).
# 자동 켜기는 사용자 PC 에서 처음 뜰 때 묻는다 — 관리자가 정하지 않는다 (09-28).
SKIP_KEYS = ("collect_site_code", "excel_path", "server_build_id",
             "target_exe", "target_work_dir", "download_base_dir", "auto_start")
MAX_DRAIN = 200     # 한 박자에 로그 칸으로 옮기는 줄 수 상한 (PyInstaller 가 줄을 쏟아낸다)
LIST_SEP = ";"      # 목록 칸의 구분자. 예약 줄(`평일 09:00 mail,orders`)에 쉼표가 있어 쉼표는 못 쓴다

# 칸의 종류: str / secret / path / dir / int / bool / list / dict / choice:a,b / modules / lock / sources / mode
SECTIONS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("1. ERPia 프로그램", (
        ("target_exe_name", "ERPia 실행 파일 이름 (경로 없이)", "str"),
        ("target_args", "실행 인자 (예비)", "list"),
        ("window_title_re", "메인 창 제목 정규식 (예비)", "str"),
        ("window_class_name", "메인 창 class (예비)", "str"),
        ("login_company_code", "업체코드", "str"),
        ("login_user_id", "아이디", "str"),
        ("login_password", "비밀번호", "secret"),
    )),
    ("2. ERPia 업무", (
        # 업체 전용 빌드 (10-08, `docs/CUSTOMERS.md`) — 굽고 잠근다. 바꾸면 이 프로그램을 다시 켜야 그 업체의 기능이 보인다
        ("customer", "업체 전용 (비우면 원본)", "choice:" + ",".join(customer.available())),
        ("run_modules", "실행할 기능", "modules"),
        ("run_modules_locked", "기능 선택", "lock"),
        ("collect_sources", "주문수집 방식", "sources"),
        ("excel_dir", "엑셀 폴더 (ERPia 단독 배포본만)", "dir"),
        ("delivery_company", "택배사 (완전일치)", "str"),
        ("delivery_box", "박스 (완전일치)", "str"),
        ("logistics_mode", "물류관리 자동/수동", "mode"),
        ("sales_mode", "매출처리 방식", "choice:전체,선택주문"),
        ("hold_exclude_codes", "보류 제외 상품코드", "list"),
        ("excel_passwords", "엑셀 비밀번호 — 사이트별 (사이트=비번)", "dict"),
        ("excel_password", "엑셀 기본 비밀번호", "secret"),
    )),
    ("3. 메일", (
        ("mail_url", "로그인 페이지", "str"),
        ("mail_user_id", "아이디", "str"),
        ("mail_password", "비밀번호", "secret"),
        ("mail_senders", "발신자 (비우면 조건 없음)", "list"),
        ("mail_sites", "사이트명 (비우면 대괄호만)", "list"),
        ("mail_unread_only", "읽지 않은 메일만", "yesno"),
        ("mail_days_back", "며칠 전까지 (0 오늘 / -1 제한 없음)", "int"),
    )),
    ("4. 브라우저", (
        ("browser_channel", "브라우저", "choice:" + ",".join(CHANNELS)),
        ("browser_headless", "창 없이 실행 (권하지 않음)", "bool"),
    )),
    ("5. 인증 문자", (
        ("phone_os", "휴대폰", "choice:android,ios"),
        ("sms_source", "문자 읽는 방법", "choice:phonelink,adb,auto"),
        ("adb_connection", "adb 연결", "choice:usb,wireless"),
        ("sms_senders", "인증 문자 발신번호 (비우면 조건 없음)", "list"),
        ("sms_keyword", "인증 문자 키워드", "str"),
        ("adb_path", "adb.exe 경로 (비우면 PATH)", "path"),
        ("adb_wireless_address", "무선 디버깅 주소 IP:포트", "str"),
    )),
    ("6. 자동 실행 (예약)", (
        ("auto_run_enabled", "예약 실행 켬 (사람 없이 실계정이 돈다)", "bool"),
        ("auto_run_mode", "방식", "choice:daily,interval"),
        ("auto_run_times", "예약 줄 (예: 평일 09:00 mail,orders)", "list"),
        ("auto_run_interval_minutes", "간격 (분, 10~1440)", "int"),
    )),
    ("7. 서버 보고", (
        ("server_url", "Supabase 프로젝트 URL (비우면 보고 안 함)", "str"),
        ("server_anon_key", "공개(anon) 키", "str"),
        ("build_account_name", "업체 이름 (서버에 없으면 확인 뒤 만듭니다)", "str"),
        ("build_label", "이 빌드의 이름 (대시보드 PC 표에 보입니다)", "str"),
        ("build_admin_email", "관리자 이메일 (대시보드 로그인 계정)", "str"),
        ("build_admin_password", "관리자 비밀번호", "secret"),
    )),
)
KINDS = {key: kind for _title, fields in SECTIONS for key, _label, kind in fields}

LOCK_HELP = ("기능 고정: exe 는 위에서 고른 기능만 돌고 실행 창에서 바꿀 수 없습니다.\n"
             "실행 창에서 선택: 지금처럼 실행 창의 [실행할 기능] 체크로 고릅니다.")
SEP_HELP = f"({LIST_SEP} 로 나눔)"


# ------------------------------------------------------------ 값 ↔ 글
YESNO = {"예": True, "아니오": False}       # 세 상태 칸 — 빈 칸은 값 없음 (기본값 없음, 10-02)


def format_value(key: str, value: object) -> str:
    """설정값을 칸에 넣을 글로. 목록은 `;`, 사전은 `이름=값;`."""
    if value is None:
        return ""
    if KINDS.get(key) == "yesno":
        return next((text for text, flag in YESNO.items() if flag is value), "")
    if isinstance(value, list):
        return f"{LIST_SEP} ".join(str(v) for v in value)
    if isinstance(value, dict):
        return f"{LIST_SEP} ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def parse_value(key: str, text: str) -> object:
    """칸의 글을 설정값으로. 틀리면 `ValueError` (칸 이름이 든 메시지)."""
    kind = KINDS.get(key, "str")
    text = text.strip()
    if kind == "list":
        return [part.strip() for part in text.split(LIST_SEP) if part.strip()]
    if kind == "dict":
        out: dict[str, str] = {}
        for part in text.split(LIST_SEP):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise ValueError(f"{key}: '{part}' — `이름=값` 꼴이어야 합니다")
            name, value = part.split("=", 1)
            out[name.strip()] = value.strip()
        return out
    if kind == "int":
        if not text:
            return None        # 빈 칸 = 값 없음 (기본값 없음, 10-02). 그 값이 필요한 빌드인지는 굽기가 본다
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{key}: 숫자여야 합니다 ('{text}')") from None
    if kind == "yesno":
        return YESNO.get(text)
    return text or None        # 빈 칸은 None — `DEFAULTS` 의 빈 값과 같다


class BuildWindow:
    """설정 폼 + [저장] / [저장하고 빌드] + 빌드 로그."""

    def __init__(self, root: tk.Tk, *, saver=save_local, builder=None) -> None:
        # `builder` 는 `tools.build_run.build` — 확인 도구가 가짜를 넣을 수 있게 늦게 고른다
        self.root = root
        self.saver = saver
        self.builder = builder
        self.vars: dict[str, tk.Variable] = {}
        self.module_vars = {module.id: tk.BooleanVar(value=False) for module in modules.ALL}
        self.site_var = tk.BooleanVar(value=False)
        self.excel_var = tk.BooleanVar(value=False)
        self.lines: queue.Queue = queue.Queue()
        self.building = False
        self.result: object = None       # 마지막 빌드 결과 — Path 또는 예외
        self._build_ui()
        self.load()

    # ----------------------------------------------------------- 화면
    def _build_ui(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(0, weight=1)

        # 왼쪽: 스크롤되는 설정 폼
        holder = ttk.Frame(root)
        holder.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        # ★ 폭을 정해 준다. 안 정하면 오른쪽 로그 칸(Text 기본 80자)이 폭을 다 가져가 입력칸이 좁아진다
        canvas = tk.Canvas(holder, highlightthickness=0, width=int(640 * ui_scale()))
        canvas.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        bar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=bar.set)
        form = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        form.columnconfigure(1, weight=1)

        self.small = ("TkDefaultFont", HELP_FONT_SIZE)
        row = 0
        ttk.Label(form, text=f"설정 파일: {LOCAL_SETTINGS_PATH}", font=self.small,
                  foreground="#666666").grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        for title, fields in SECTIONS:
            box = ttk.LabelFrame(form, text=title, padding=(10, 6))
            box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            box.columnconfigure(1, weight=1)
            row += 1
            for r, (key, label, kind) in enumerate(fields):
                self._field(box, r, key, label, kind)

        # 오른쪽: 단추 + 로그
        side = ttk.Frame(root)
        side.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(3, weight=1)
        self.save_btn = ttk.Button(side, text="설정 저장", command=self.on_save)
        self.save_btn.grid(row=0, column=0, sticky="ew")
        self.build_btn = ttk.Button(side, text="저장하고 빌드", command=self.on_build)
        self.build_btn.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(side, textvariable=self.status_var, wraplength=int(360 * ui_scale()),
                  justify="left").grid(row=2, column=0, sticky="w", pady=(8, 4))
        log_frame = ttk.LabelFrame(side, text="빌드 로그", padding=6)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=20, width=44)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("error", foreground="#b00020")

    def _field(self, box: ttk.Frame, r: int, key: str, label: str, kind: str) -> None:
        ttk.Label(box, text=label, anchor="w").grid(row=r, column=0, padx=(0, 8), pady=3, sticky="w")
        # 체크·라디오 줄은 [찾기] 칸(column 2)까지 써서 잘리지 않게 한다
        if kind == "modules":
            frame = ttk.Frame(box)
            frame.grid(row=r, column=1, columnspan=2, sticky="w")
            for i, module in enumerate(modules.ALL):
                ttk.Checkbutton(frame, text=module.name,
                                variable=self.module_vars[module.id]).grid(row=0, column=i, padx=(0, 12))
            return
        if kind == "lock":
            var = tk.BooleanVar(value=False)
            self.vars[key] = var
            frame = ttk.Frame(box)
            frame.grid(row=r, column=1, columnspan=2, sticky="w")
            ttk.Radiobutton(frame, text="기능 고정", variable=var, value=True).grid(row=0, column=0, padx=(0, 12))
            ttk.Radiobutton(frame, text="실행 창에서 선택", variable=var, value=False).grid(row=0, column=1)
            ttk.Label(frame, text=LOCK_HELP, font=self.small, foreground="#666666", justify="left",
                      wraplength=int(400 * ui_scale())).grid(row=1, column=0, columnspan=2, sticky="w")
            return
        if kind == "sources":
            frame = ttk.Frame(box)
            frame.grid(row=r, column=1, columnspan=2, sticky="w")
            ttk.Checkbutton(frame, text="자동수집", variable=self.site_var).grid(row=0, column=0, padx=(0, 12))
            ttk.Checkbutton(frame, text="엑셀수집", variable=self.excel_var).grid(row=0, column=1)
            return
        if kind == "mode":
            var = tk.StringVar(value="")            # 기본값 없음 (10-02) — 고르지 않으면 굽지 않는다
            self.vars[key] = var
            frame = ttk.Frame(box)
            frame.grid(row=r, column=1, columnspan=2, sticky="w")
            ttk.Radiobutton(frame, text="자동 (운송장출력)", variable=var, value="자동").grid(row=0, column=0, padx=(0, 12))
            ttk.Radiobutton(frame, text="수동 (엑셀파일생성)", variable=var, value="수동").grid(row=0, column=1)
            return
        if kind == "bool":
            var = tk.BooleanVar(value=False)
            self.vars[key] = var
            ttk.Checkbutton(box, variable=var).grid(row=r, column=1, sticky="w")
            return
        if kind.startswith("choice:") or kind == "yesno":
            var = tk.StringVar(value="")
            self.vars[key] = var
            choices = list(YESNO) if kind == "yesno" else kind.split(":", 1)[1].split(",")
            ttk.Combobox(box, textvariable=var, values=["", *choices],      # 빈 칸 = 고르지 않음
                         state="readonly", width=14).grid(row=r, column=1, sticky="w")
            return
        var = tk.StringVar(value="")
        self.vars[key] = var
        entry = ttk.Entry(box, textvariable=var, show="*" if kind == "secret" else "")
        entry.grid(row=r, column=1, sticky="ew")
        if kind in ("path", "dir"):
            ttk.Button(box, text="찾기", width=6,
                       command=lambda k=key, d=(kind == "dir"): self._browse(k, d)).grid(row=r, column=2, padx=(6, 0))
        elif kind in ("list", "dict"):
            ttk.Label(box, text=SEP_HELP, font=self.small, foreground="#666666").grid(row=r, column=2, padx=(6, 0), sticky="w")

    def _browse(self, key: str, directory: bool) -> None:
        chosen = (filedialog.askdirectory(title="폴더 선택") if directory
                  else filedialog.askopenfilename(title="파일 선택"))
        if chosen:
            self.vars[key].set(chosen.replace("/", "\\"))

    # ----------------------------------------------------------- 값
    def load(self, values: dict | None = None) -> None:
        """설정을 칸에 채운다. 기본은 지금 `SETTINGS` (비밀은 푼 값)."""
        values = SETTINGS.as_dict() if values is None else values
        for key, var in self.vars.items():
            value = values.get(key, DEFAULTS.get(key))
            kind = KINDS[key]
            if kind in ("bool", "lock"):
                var.set(bool(value))
            elif kind == "mode":
                var.set(value or "")
            else:
                var.set(format_value(key, value))
        chosen = set(values.get("run_modules") or [])
        for key, var in self.module_vars.items():
            var.set(key in chosen)
        sources = set(values.get("collect_sources") or [])
        self.site_var.set("site" in sources)
        self.excel_var.set("excel" in sources)

    def values(self) -> dict[str, object]:
        """칸의 값을 설정 사전으로. 틀린 칸이 있으면 `ValueError`."""
        out: dict[str, object] = {}
        for key, var in self.vars.items():
            kind = KINDS[key]
            if kind in ("bool", "lock"):
                out[key] = bool(var.get())
            elif kind == "mode":
                out[key] = var.get() or None
            else:
                out[key] = parse_value(key, str(var.get()))
        out["run_modules"] = [module.id for module in modules.ALL if self.module_vars[module.id].get()]
        out["collect_sources"] = ([s for s, on in (("excel", self.excel_var.get()),
                                                   ("site", self.site_var.get())) if on])
        if not out["run_modules"]:
            raise ValueError("실행할 기능을 하나 이상 고르세요")
        # 수집방식은 주문수집을 고른 빌드만 (그 밖의 빌드에는 필요 없다). 나머지 필수 값은 굽기가 기능별로 본다
        if modules.ORDERS.id in out["run_modules"] and not out["collect_sources"]:
            raise ValueError("주문수집 방식을 하나 이상 고르세요")
        return out

    # ----------------------------------------------------------- 동작
    def on_save(self) -> bool:
        try:
            values = self.values()
        except ValueError as exc:
            self._say(str(exc), error=True)
            return False
        try:
            path = self.saver(**values)
        except (OSError, ValueError) as exc:
            self._say(f"저장하지 못했습니다: {exc}", error=True)
            return False
        self._say(f"저장했습니다: {path}")
        return True

    def on_build(self) -> None:
        if self.building or not self.on_save():
            return
        try:
            values = self.values()      # 화면의 값을 그대로 쓴다 — 전역 설정을 읽으면 저장 경로에 기댄다
        except ValueError as exc:
            self._say(str(exc), error=True)
            return
        from tools import bake_settings           # 개발 전용 — 늦게 불러 배포본에 흔적을 남기지 않는다

        # 업체를 바꿨으면 이 창의 기능 목록·구울 칸은 옛 업체 것이다 — 다시 켜야 맞는다 (기능 목록은 켤 때 정해진다)
        if (values.get("customer") or "") != (CUSTOMER.id if CUSTOMER else ""):
            self._say("업체를 바꿨습니다. 저장했으니 빌드 프로그램을 닫고 다시 켠 뒤 빌드하세요 "
                      "(실행할 기능 목록이 그 업체 것으로 바뀝니다).", error=True)
            return
        # 서버에 등록하기 **전에** 막는다 — 등록부터 하면 쓰지 못할 빌드(PC 행)가 대시보드에 쌓인다 (10-02 검토)
        empty = bake_settings.missing(values)
        if empty:
            self._say("구울 값이 비어 있습니다: " + ", ".join(empty) + " — 채운 뒤 다시 빌드하세요.", error=True)
            return
        if not self._register_build(values):
            return
        if self.builder is None:
            from tools import build_run       # 개발 전용 — 늦게 불러 배포본에 흔적을 남기지 않는다
            self.builder = build_run.build
        self._set_building(True)
        self.result = None
        self._log("빌드를 시작합니다. 몇 분 걸립니다.\n")
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(200, self._poll)

    def _ask_new_account(self, name: str, similar: list[str]) -> bool:
        """서버에 없는 업체 이름 — 새 업체로 만들어도 되는지 묻는다 (10-07, 기본 [아니요] — 오타로 업체가 생기지 않게)."""
        text = f"'{name}' 은(는) 서버에 없는 업체 이름입니다.\n[예] 를 누르면 새 업체로 만들고 이 빌드를 그 아래에 등록합니다."
        if similar:
            text += "\n\n비슷한 이름이 이미 있습니다: " + ", ".join(similar) + "\n오타라면 [아니요] 를 누르고 이름을 고치세요."
        return messagebox.askyesno("새 업체 만들기", text, icon="warning", default="no", parent=self.root)

    def _register_build(self, values: dict) -> bool:
        """서버에 이 빌드를 등록하고 빌드 ID 를 설정에 넣는다 (09-28). 계속해도 되면 True.

        `values` 는 **화면의 값**이다 (`self.values()`). 서버 URL 이 비어 있으면 건너뛴다 — 서버를
        쓰지 않는 빌드다. 등록이 실패하면 빌드하지 않는다: ID 없이 구우면 사용자가 받아도
        대시보드에 안 보이고, 그걸 나중에 알기 어렵다.
        """
        if not (values.get("server_url") or "").strip():
            self._log("서버 URL 이 비어 있어 서버 등록을 건너뜁니다 (보고하지 않는 빌드).\n")
            return True
        from tools import register_build      # 개발 전용 — 배포본에 흔적을 남기지 않는다

        self._log("서버에 빌드를 등록하는 중입니다...\n")
        try:
            build_id = register_build.register(
                values.get("server_url") or "", values.get("server_anon_key") or "",
                values.get("build_admin_email") or "", values.get("build_admin_password") or "",
                values.get("build_account_name") or "", values.get("build_label") or "",
                confirm_new=self._ask_new_account, customer=values.get("customer") or None)
        except register_build.RegisterError as exc:
            self._say(f"서버 등록 실패: {exc}", error=True)
            self._log(f"서버 등록 실패: {exc}\n")
            return False
        try:
            self.saver(server_build_id=build_id)
        except (OSError, ValueError) as exc:
            self._say(f"빌드 ID 를 저장하지 못했습니다: {exc}", error=True)
            return False
        SETTINGS.server_build_id = build_id     # 이어지는 굽기가 이 값을 쓴다
        self._log(f"서버에 등록했습니다 ({values.get('build_account_name')} / "
                  f"{values.get('build_label')}). 이 빌드는 처음 켜는 PC 에 묶입니다.\n")
        return True

    def _worker(self) -> None:
        try:
            self.result = self.builder(self.lines.put)
        except Exception as exc:        # noqa: BLE001 — 실패 이유를 화면에 보인다
            self.result = exc
        finally:
            self.lines.put(None)         # 끝 표시

    def _poll(self) -> None:
        """작업 스레드의 줄을 로그 칸에 옮긴다. `None` 이 오면 끝. 한 번에 `MAX_DRAIN` 줄까지."""
        for _ in range(MAX_DRAIN):
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                self._finish()
                return
            self._log(str(line) + "\n")
        self.root.after(200, self._poll)

    def _finish(self) -> None:
        self._set_building(False)
        if isinstance(self.result, Exception):
            self._say(f"빌드 실패 — {self.result}", error=True)
        else:
            self._say(f"빌드 완료: {self.result}")

    def _set_building(self, on: bool) -> None:
        self.building = on
        state = "disabled" if on else "normal"
        self.save_btn.configure(state=state)
        self.build_btn.configure(state=state)

    def _say(self, text: str, error: bool = False) -> None:
        self.status_var.set(text)
        self._log(text + "\n", error=error)

    def _log(self, text: str, error: bool = False) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, ("error",) if error else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def run() -> int:
    from utils.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    root = tk.Tk()
    apply_scaling(root)
    root.title("RPA 빌드 — 설정을 고쳐서 실행용 exe 만들기")
    scale = ui_scale()
    root.geometry(f"{int(1100 * scale)}x{int(820 * scale)}")
    root.minsize(int(860 * scale), int(600 * scale))
    window = BuildWindow(root)

    def on_close() -> None:
        if window.building and not messagebox.askyesno(
                "빌드 중", "빌드가 아직 돌고 있습니다. 창을 닫으면 결과를 볼 수 없습니다. 닫을까요?"):
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0
