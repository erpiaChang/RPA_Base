r"""업무 프로그램 자동화 기능의 설정 항목.

기능별로 파일을 나눈다 (`config/settings.py` 가 합친다).
값의 뜻은 `docs/SETTINGS.md` 에 있다.
"""
from __future__ import annotations

DEFAULTS: dict[str, object] = {
    # --- 실행 대상 ---
    "target_exe": None,
    # ERPia 실행 파일 **이름**(경로 없이). 굽는다 — 실행 창이 이 이름으로 이 PC 의 ERPia 를 찾고, 같은 아이디로 떠 있는
    # ERPia 를 가린다. 설치 경로·파일 이름은 코드·git 에 두지 않는다 (10-02 사용자 결정)
    "target_exe_name": None,
    "target_work_dir": None,
    "target_args": [],
    "window_title_re": None,
    "window_class_name": None,

    # --- 로그인 ---
    "login_company_code": None,
    "login_user_id": None,
    "login_password": None,         # SECRET_KEYS — 저장 때 dpapi:/baked: 로 감싼다 (utils/secret.py)

    # --- 업무 ---
    # 실행할 기능 (통합·실행용만 본다. `orchestrator/modules.py`, 2026-09-21).
    # 고른 것만 **아래 순서대로** 돈다. 최소 1개.
    #   "mail" 메일 엑셀 받기 / "orders" 주문수집·매출처리 /
    #   "logistics_wait" 물류대기 / "logistics" 물류관리
    # ★ 이 파일의 설정에는 **기본값이 없다** (10-02 사용자 결정) — 비면 [실행] 이 '입력 필요' 로 잠기거나 그 기능이
    #   시작하지 않는다. 남긴 값은 안전용 꺼짐(예약 꺼짐 등)과 '비우면 조건 없음' 목록뿐이다
    "run_modules": [],
    # 빌드 프로그램(`gui/build_app.py`)이 굽는다 (사용자 확정 2026-09-22).
    #   True  = 기능 고정 — exe 는 구운 `run_modules` 만 돌고 실행 창에서 바꿀 수 없다
    #   False = 실행 창의 [실행할 기능] 체크로 고른다 (지금까지의 동작)
    "run_modules_locked": False,
    # 주문수집 방식. **최소 1개, 최대 2개**를 고른다 (사용자 확정 2026-09-08).
    #   "site"  자동수집 — 사이트 전체선택 후 [가져오기]
    #   "excel" 엑셀수집 — 폴더 안의 엑셀을 파일명의 사이트명으로 각각 업로드
    "collect_sources": [],
    # 엑셀수집이 훑을 **폴더**. 그 안의 엑셀 파일 이름 앞부분(첫 `_` 앞)이
    # 사이트명이다. 예) `사이트A_20260908.xlsx` → 사이트명 `사이트A`
    "excel_dir": None,

    # 아래 둘은 **개발 도구 전용**이다. 화면에서는 받지 않는다.
    # 사이트코드 하나 + 엑셀 하나로 올려 보는 경로 (`python -m automation.order_mapping` 개발 CLI 만 읽는다. 굽지 않는다).
    "collect_site_code": None,
    "excel_path": None,
    # 물류대기에서 **배송보류를 걸지 않을 상품코드**. 부족수량이 있어도 건너뛴다.
    # 완전일치로 비교한다. 예) ["8800000000006", "8800000000007"]
    "hold_exclude_codes": [],

    "delivery_company": None,       # 완전 일치로 고른다
    "delivery_box": None,           # 완전 일치로 고른다
    # 물류관리 상단 [자동/수동] 콤보. 저장 뒤 누르는 버튼이 달라진다
    #   자동 → [운송장출력] / 수동 → [엑셀파일생성] (사용자 확정 2026-09-17)
    "logistics_mode": None,
    # 매출처리 방식 (사용자 요청 10-01)
    #   전체 → [매출처리] 단추 (조회 안 된 미매출도 처리) / 선택주문 → 조회된 주문 전부 체크 → 우클릭 [선택주문 매출처리]
    "sales_mode": None,

    # 암호가 걸린 엑셀을 올리면 프로그램이 비밀번호를 묻는다.
    # 사이트마다 다를 수 있어 사이트명별로 둔다. 예) {"사이트B": "0000"}
    "excel_passwords": {},
    # 사이트별 값이 없을 때 쓸 기본 비밀번호. 비우면 쓰지 않는다
    "excel_password": None,

    # --- 자동 실행 (예약) ---  사용자 확정 2026-09-16
    #
    # ★ **켜면 사람 없이 실계정 저장이 돈다.** 기본은 꺼짐이고, 화면에서
    #   명시적으로 켜야 한다. 값의 뜻과 한계는 `docs/SETTINGS.md` 참고.
    # ★ 잠긴 화면에서는 자동화가 동작하지 않으므로 그 회차는 **건너뛴다.**
    #   PC 가 켜져 있고 사람이 로그온한 상태가 전제다 (우회할 수 없다).
    "auto_run_enabled": False,
    # "daily"    = 정해진 날·시각에 돈다 (`auto_run_times`, 꼴은 utils/schedule.py)
    # "interval" = 앞 회차가 **끝난 뒤** 정해진 시간이 지나면 돈다
    "auto_run_mode": None,            # 켰는데 비면 '설정을 고쳐야 한다' (utils/schedule.py Plan.problems)
    # `daily` 에서 쓴다. 예약 줄 목록, 최대 12개.
    # 예) ["09:00", "월수금 13:00", "매월 25일 09:00", "2026-09-25 14:00"]
    # 형식이 아닌 값은 **버리고 화면에 알린다** — 0시로 읽어 새벽에 도는 것을 막는다
    "auto_run_times": [],
    # `interval` 에서 쓴다. 분. 10 ~ 1440 밖은 쓰지 않는다
    "auto_run_interval_minutes": None,
    # PC 에 로그온하면 켜고, 꺼져 있으면 5분 안에 다시 켠다 (09-28, `utils/autostart.py`).
    # None = 아직 안 물었다 — 빌드본이 처음 뜰 때 사람에게 묻고 답을 남긴다. **굽지 않는다** (그 PC 의 선택)
    "auto_start": None,

    # --- 서버 보고 (`docs/SERVER_PLAN.md` D 절) ---
    #
    # 셋 다 있어야 보고한다. 하나라도 비면 **보고하지 않고** 지금처럼 돈다.
    "server_url": None,             # Supabase 프로젝트 URL. 굽는다
    "server_anon_key": None,        # 공개(anon) 키. 굽는다
    # 빌드 ID (09-28). 빌드할 때 서버가 발급하고 **굽는다.** 사용자에게 보이지 않고 화면에 칸도 없다.
    # 첫 보고 때 서버가 이 빌드를 그 PC 에 묶는다 — 다른 PC 로 복사하면 401 이라 실행이 잠긴다.
    "server_build_id": None,

    # --- 빌드 프로그램 전용 (09-28) ---
    #
    # 관리자 PC 에만 남는다. **굽지 않는다** (`tools/bake_settings.SKIP_KEYS`) — 사용자 exe 에 관리자
    # 계정이 들어가면 안 된다. 빌드할 때 이 값으로 서버에 로그인해 빌드 ID 를 발급받는다.
    "build_account_name": None,     # 업체 이름. 서버에 없으면 만든다
    "build_label": None,            # 이 빌드의 이름 (대시보드 PC 표에 보인다. 예: "PC-A")
    "build_admin_email": None,      # 대시보드 관리자 계정
    "build_admin_password": None,   # 위 계정의 비밀번호. dpapi 로 감싸 저장한다
}
