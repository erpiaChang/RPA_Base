# 공용 코어 / 프로그램별 어댑터 경계 조사

기준일 2026-09-17. **조사 문서다. 코드는 고치지 않았다.**

방향(사용자 확정): 새 프로그램이 오면 "공용 코어 + 프로그램별 어댑터" 로 만든다.
코어를 실제로 떼어 내는 일은 **두 번째 프로그램이 들어올 때** 한다 — 그 전에 나누면
경계를 추측으로 긋게 된다. 이 문서는 그때 쓸 지도다.

- 줄 번호는 2026-09-17 소스 기준이다. 코드가 바뀌면 어긋난다 — 함수·상수 이름으로 다시 찾는다
- 분류: **A 공용**(그대로 코어로) / **B 섞임**(공용 로직에 ERPia·메일 사이트(사이트A) 값이나 가정이 끼어 있다) / **C 전용**(어댑터에 남는다)
- 여기서 "전용" 은 ERPia 뿐 아니라 **메일 사이트(사이트A) 웹메일**(수집)도 포함한다.
  [휴대폰 연결]·adb 문자 읽기는 메일 사이트(사이트A)에 묶이지 않은 **2차 인증 부품**이라 공용으로 본다

---

## 1. 요약

제품 코드 61개 파일(`__init__.py` 제외, `tools/` 제외)

| 분류 | 건수 | 대표 |
| --- | ---: | --- |
| A 공용 | 23 | `utils/` 10개, `orchestrator/steps·common·report`, `gui/common·pipeline·autorun_pane`, `automation/application`, `collect/storage·manifest·auth_code·phonelink·sms` |
| B 섞임 | 16 | `utils/ui.py`, `gui/erpia_app.py`, `config/settings.py`, `build_*.spec`·`build_*.bat` 8개 |
| C 전용 | 22 | `automation/` 업무 화면 3개, `orchestrator/*_flow`·`steps_*`, `collect/<메일 사이트>·mail`, 기능별 `main_*`·`gui/*_app` |

의존 방향은 대체로 건강하다. `utils/` 는 `automation/`·`collect/` 를 import 하지 않고
(`config.settings` 만 본다), `orchestrator/steps.py`·`common.py`·`report.py` 도 기능별
모듈을 import 하지 않는다. `automation/` ↔ `collect/` 사이 직접 import 도 없다
(`manifest.json` 파일로만 이어진다).

### 떼어 낼 때 가장 큰 걸림돌 3개

1. **`gui/erpia_app.py` 의 `ErpiaWindow` 한 클래스에 실행 골격과 ERPia 값이 같이 있다.**
   바쁨 상태·로그 큐·중단 토큰·오버레이·리포트·자동 실행 연결(공용)과 로그인 3종·택배사·박스·
   자동/수동·ERPia 예외 분기(전용)가 섞였고, `run_app.RunWindow`(`gui/run_app.py:53`)와
   `full_app.FullWindow`(`gui/full_app.py:18`)가 이 클래스를 **상속**한다. 두 번째 프로그램의
   창은 지금 구조로는 ERPia 창을 상속하게 된다
2. **`utils/ui.py`(2,023줄) 뒤쪽 절반이 ERPia(DevExpress) 그리드·메뉴·드롭다운 규약이다.**
   585줄 앞은 범용 UIA 래퍼, 586줄부터 `"행 N"` 행 이름, `"row Check Box"`/`"선택"`,
   `"헤더 패널"`, 무제목 `WindowsForms10` 팝업 메뉴, 스크롤바 버튼 이름이 박혀 있다.
   "모든 UI 조작의 단일 창구" 라서 파일을 쪼개야 한다
3. **설정이 하나의 평평한 이름공간이고, 목록이 코어에 박혀 있다.**
   `FIELD_MODULES`(`config/settings.py:64`), `SECRET_KEYS`/`SECRET_MAP_KEYS`(`:89`, `:91`),
   `Timeouts` 의 기능별 항목(`:107-117`)이 코어 파일에 있고, 업무 코드가 `SETTINGS.target_exe`
   같은 전역 키를 직접 읽는다(`automation/login.py:536` 등). 두 번째 프로그램도
   `target_exe` 가 필요한데 키가 하나뿐이다

---

## 2. 파일 분류표

### config / 진입점 / 빌드

| 파일 | 분류 | 근거 | B라면 섞인 위치 |
| --- | --- | --- | --- |
| `config/settings.py` | B | 병합(`_collect_defaults`, `_apply_file`, `_load`)·비밀 풀기는 공용. 기능 목록·비밀 키·기능별 타임아웃을 코어가 직접 나열 | `FIELD_MODULES` :64, `SECRET_KEYS` :89, `SECRET_MAP_KEYS` :91, `Timeouts.collect*`·`page_load`·`sms_code`·`phone_connect`·`download` :107-117 |
| `config/fields_collect.py` | C | 수집 전용 설정 항목 `DEFAULTS` (:10) | — |
| `config/fields_erpia.py` | C | ERPia 전용 설정 항목 `DEFAULTS` (:8) | — |
| `main.py` | A | 개발 런처 진입만 (`gui.launcher`) | — |
| `main_collect.py` / `main_erpia.py` / `main_full.py` / `main_run.py` | C ×4 | 각자 `gui.*_app` 하나만 import | — |
| `build_collect.spec` / `build_erpia.spec` / `build_full.spec` / `build_run.spec` | B ×4 | 누출 검사 구조(`DEV_ONLY + FORBIDDEN`)는 공용. 진입 파일·exe 이름·기능 모듈 목록이 하드코딩, `DEV_ONLY` 가 네 벌 중복 | 아래 표 |
| `build_collect.bat` / `build_erpia.bat` / `build_full.bat` / `build_run.bat` | B ×4 | 배치 골격은 같고 `dist\<기능>` 경로·샘플 설정 파일명이 하드코딩. `build_run.bat` 은 굽기(`tools.bake_settings` :24)가 더 있다 | `build_collect.bat:28-40`, `build_erpia.bat:28-40` |

spec 하드코딩 위치

| 무엇 | collect | erpia | full | run |
| --- | --- | --- | --- | --- |
| `HIDDEN +=` 설정 항목 모듈 | :27 | :27 | :27 | :40 |
| `DEV_ONLY = [` | :30 | :30 | :30 | :52 |
| `FORBIDDEN = [` | :38 | :38 | :38 | :63 |
| `Analysis(["main_*.py"]` | :56-57 | :57-58 | :50-51 | :77-78 |
| `name="…"` | :110 `Collect_RPA` | :111 `ERPia_RPA` | :104 `Integrated_RPA` | :135 `RPA_1` |
| 굽는 설정 | — | — | — | `BAKED_SOURCE` :44 |

### gui

| 파일 | 분류 | 근거 | B라면 섞인 위치 |
| --- | --- | --- | --- |
| `gui/common.py` | A | tk 공통 조각(`FormToggle`, `DetailPane`, `LogPane`, `apply_scaling`). 업무 import 없음 | — |
| `gui/pipeline.py` | A | 단계 표를 그리기만 한다. 업무 import 없음 (:956 의 `매출처리` 는 docstring 그림) | — |
| `gui/autorun_pane.py` | A | `utils.autorun`/`schedule` 만 쓴다 | — |
| `gui/overlay.py` | B (경미) | 대상 창 제목·클래스를 찾지 않는다(작업 영역 기준). 폭 계산 견본이 수집 단계명 | `BAR_SAMPLE` :161 (`웹메일 로그인·2차 인증`. `tools/probe_overlay.py` 가 단계 표와 대조) |
| `gui/erpia_app.py` | B | 실행 골격 + ERPia 입력·예외 | import :28-31, :37 / `_mode_selector` :363-379(`logistics.MODES` :374) / `_plan` :524 / `_saved_values` :685-699 / 예외 분기 :761-762 / `_call_flow` :905 |
| `gui/launcher.py` | C (개발 전용) | 기능별 창 모듈 이름을 나열 | — |
| `gui/collect_app.py` | C | `collect_flow`·`steps_collect` 직접 사용 (:27) | — |
| `gui/full_app.py` | C | `ErpiaWindow` 상속(:18), `_plan`·`_call_flow` 만 재정의 | — |
| `gui/run_app.py` | C | `ErpiaWindow` 상속(:53), 입력 화면을 다시 그리고 `full_flow.run` 호출(:293) | — |

### orchestrator

| 파일 | 분류 | 근거 | B라면 섞인 위치 |
| --- | --- | --- | --- |
| `orchestrator/steps.py` | A | `Step`(:65, frozen dataclass: `id`/`name`/`reentry`/`replay`), 재진입 등급 `SAFE`/`CONDITIONAL`/`WRITE`/`IRREVERSIBLE`(:28-31), `StepEvent`. 업무 이름은 주석 예시뿐 | — |
| `orchestrator/common.py` | A | `Hooks`(:110)·`StepRun`(:38)·`Result`(:345). 기능 모듈 import 없음 | — |
| `orchestrator/report.py` | A | `Result.steps` 에 든 것만 그린다. `ERPia` 는 docstring(:7)에만 | — |
| `orchestrator/steps_erpia.py` | C | ERPia 단계 8개(:24-60), `plan(ordered_sources)`(:67) | — |
| `orchestrator/steps_collect.py` | C | 수집 단계 3개(:12-26), `plan()`(:29) | — |
| `orchestrator/erpia_flow.py` | C | `automation.*` 호출(:18-22), `Options`(:53), 상세 표 함수들 | — |
| `orchestrator/full_flow.py` | C | `automation.*` + `collect.manifest`/`storage` (:23-33) | — |
| `orchestrator/collect_flow.py` | C | `collect.*` 만 호출 | — |
| `orchestrator/pipeline.py` | C (개발 전용) | 세 흐름 분기. 부르는 곳 없음 | — |

### utils

| 파일 | 분류 | 근거 | B라면 섞인 위치 |
| --- | --- | --- | --- |
| `utils/wait.py` / `cancel.py` / `process.py` / `winprobe.py` / `schedule.py` / `autorun.py` / `secret.py` / `dpi.py` / `logger.py` | A ×9 | 프로그램 이름·화면 문자열 없음 (`process.py` 의 `LockApp.exe` 는 Windows 프로세스) | — |
| `utils/dialogs.py` | A | 확인 버튼 정규식(`예/확인/OK`)과 UIA 탐색. "팝업은 부모 창의 자식" 이라는 가정은 docstring 에만 있고 ERPia 고유 문자열은 없다 | — |
| `utils/ui.py` | B | :1-584 범용 UIA(`find` :185, `bring_forward` :380, `click` :510, `set_text` :557). :586 이후 그리드 규약 | `ROW_TITLE_RE` :586, `cell` :611, `is_group_row` :1131, `CHECK_COLUMN` :1136, `CHECK_ON/OFF` :1146-1147, `popup_menu_windows` :1248, `header_select_all` :1442, `HEADER_PANEL_NAME` :1457, `click_row_indicator` :1483, `dropdown_lists` :1520 ~ `click_dropdown_item` :1727, `SCROLL_*_NAMES` :1789-1803 |
| `utils/filedialog.py` | B (경미) | 표준 `#32770` 처리는 공용. 제목 정규식에 업무 단어 | `DIALOG_TITLE_RE` :26 (후보 끝에 `업로드`) |
| `utils/elevation.py` | B (경미) | 판정은 Win32 API. 안내 문구에 `ERPia` | docstring :6, `ElevationMismatch` 메시지 :36 |

`utils/ui.py` 의 :586 이후 가운데 `wait_rect_settled`(:647), `grid_data_area`(:721), `reveal_cell`(:755),
`cell_value`(:1033)는 **기법은 일반적**이다(위치 안정 대기, 잘린 셀 보이게, 값 읽기 순서).
행·셀 이름 규칙(`"행 N"`)을 인자로 받게 하면 코어에 남길 수 있다. 어디까지 남길지는
두 번째 프로그램의 그리드를 조사한 뒤 정한다.

### automation / collect

| 파일 | 분류 | 근거 | B라면 섞인 위치 |
| --- | --- | --- | --- |
| `automation/application.py` | A | exe·pid 를 인자로 받는다. 메인 창 = 프로세스의 가장 큰 최상위 창(:93-102) | — |
| `automation/updater.py` | B | UAC 탐지(`consent_pids` :108, `consent_windows` :178)는 Windows 공통. 업데이트 창 제목은 ERPia 후보값 | `UPDATE_TITLE_RE` :95 |
| `automation/login.py` | B | 흐름(창 찾기 → 입력 → 버튼 → 팝업 루프 → 완료 대기)은 일반적, 값은 전부 ERPia | `FIELD_AUTO_IDS` :41, `LOGIN_BUTTON_AUTO_ID` :46, `DUPLICATE_RE` :58, `FATAL_PATTERNS` :61, `SETTINGS.target_exe` :536 |
| `automation/order_mapping.py` | C | 주문매핑 화면 전체. 화면과 무관한 순수 함수 둘은 코어 후보 | (코어 후보) `site_name_from_filename` :706, `excel_items_in` :719 |
| `automation/logistics_wait.py` | C | 물류대기 화면 전체 | — |
| `automation/logistics.py` | C | 물류관리 화면 전체 | — |
| `collect/storage.py` / `manifest.py` | A ×2 | 날짜·차수 폴더 규칙 / 파일 계약 | — |
| `collect/auth_code.py` / `phonelink.py` / `sms.py` | A ×3 | 2차 인증 문자 읽기. 발신자·키워드는 설정에서만 받는다 | — |
| `collect/<메일 사이트>.py` / `mail.py` | C ×2 | 메일 사이트(사이트A) URL·CSS 셀렉터·메일 DOM | — |
| `collect/unread_restore.py` | C (테스트 전용) | `mail.mark_unread` 래퍼 | — |

---

## 3. 결합 지점

| 위치 | 무엇이 묶여 있나 | 풀 방법 한 줄 |
| --- | --- | --- |
| `gui/run_app.py:53`, `gui/full_app.py:18` | `ErpiaWindow` 상속. `_run_worker`(`gui/erpia_app.py:701-780`)·`_mode_selector` 를 그대로 물려받는다 (`run_app.py:289-292` 주석에 의도라고 적혀 있다) | 실행 골격을 공용 기반 창으로 올리고, 입력 칸·예외 목록·흐름 호출은 어댑터가 준다 |
| `gui/erpia_app.py:28-31` | GUI 가 `automation` 예외·상수를 직접 import | 어댑터가 "화면 오류 예외 목록"·"선택지 목록" 을 노출한다 |
| `gui/erpia_app.py:761-762` | `ScreenError` 세 벌(`order_mapping.py:161`, `logistics_wait.py:105`, `logistics.py:93`)을 나열해 잡는다 | 공용 기반 예외(예: 화면 상태 오류) 하나를 두고 어댑터 예외가 상속 |
| `gui/erpia_app.py:374` | 자동/수동 라디오가 `automation.logistics.MODES` 를 순회 | 어댑터의 GUI 입력 정의로 옮긴다 |
| `gui/erpia_app.py:685-699`, `gui/run_app.py:235` | 화면 값 → 설정 키 매핑(`delivery_company` 등) | 어댑터의 입력 항목 정의에 설정 키를 같이 둔다 |
| `gui/erpia_app.py:37,524,905` / `gui/collect_app.py:27` / `gui/full_app.py:15,47,56` / `gui/run_app.py:29,233,293` | 창이 단계 표·흐름을 직접 고른다 | 어댑터가 `plan()`·`run()` 을 제공하고 창은 받기만 한다 |
| `gui/overlay.py:161` | 폭 견본이 수집 단계명 | 단계 표에서 가장 긴 이름을 런타임에 고른다 |
| `config/settings.py:64` | 기능 목록 튜플 | 어댑터 등록 목록(또는 진입점이 넘기는 목록)으로 |
| `config/settings.py:89,91` | 비밀 키 이름을 코어가 나열. **빠뜨리면 조용히 평문 저장** (주석 :86-88) | 필드 모듈이 자기 비밀 키를 선언 |
| `config/settings.py:107-117` | 수집·ERPia 전용 타임아웃이 공용 `Timeouts` 에 | 공용 대기(창·컨트롤·팝업)만 남기고 나머지는 어댑터로 |
| `automation/login.py:536`, `automation/logistics_wait.py:71`, `automation/logistics.py:800-803`, `automation/order_mapping.py:588-591,992-993` | 업무 코드가 전역 `SETTINGS` 키를 직접 읽는다 | 키에 어댑터 이름공간을 두거나, 흐름이 값을 인자로 넘긴다(`logistics.run` 은 이미 인자 우선) |
| `collect/<메일 사이트>.py:57,60,88`, `collect/mail.py:195-221`, `collect/auth_code.py:74-85`, `collect/phonelink.py:140,306`, `collect/sms.py:88,92` | 수집 쪽도 같은 방식 | 같음 |
| `orchestrator/erpia_flow.py:18-34`, `full_flow.py:23-33`, `collect_flow.py` | 흐름이 업무 모듈을 부른다 | **이것은 정상** — 흐름은 어댑터에 속한다 |
| `build_*.spec` (위 표) | 진입 파일·exe 이름·모듈 목록·`DEV_ONLY` 네 벌 | 공용 spec 하나 + 어댑터별 값 표 |
| `build_*.bat` | `dist\<기능>`, 샘플 설정 파일명 | 인자 하나 받는 공용 배치 |
| `config/fields_erpia.py:13-14` | `window_title_re` / `window_class_name` 는 **제품 코드에서 읽는 곳을 못 찾았다** (`tools/` 제외 검색). `docs/SETTINGS.md` 는 "비우면 기본 판정" 이라고 적었다 | 떼어 낼 때 쓸지 지울지 정한다 |

---

## 4. 어댑터 인터페이스 초안

새 코드는 쓰지 않았다. **지금 ERPia 에서 그 역할을 하는 코드**를 근거로 적는다.

| 어댑터가 제공할 것 | 지금 ERPia / 수집에서 | 메모 |
| --- | --- | --- |
| **설정 필드 정의** | `config/fields_erpia.py:8` `DEFAULTS: dict[str, object]`, `config/fields_collect.py:10` | 비밀 키 목록도 여기서 선언하게 한다(지금은 `settings.py:89-91`) |
| **실행·연결** | `application.start_new_instance(exe, args=None, work_dir=None, timeout=None) -> Target` (:276), `connect(exe, timeout=None, pid=None) -> Target \| None` (:146), `close_others(exe, keep_pid) -> list[int]` (:347), `wait_gone(exe, pids, timeout=None)` (:391), `Target.main_window()` :93 / `normalize()` :104 / `close()` | **거의 그대로 코어.** 어댑터는 exe·인자·작업 폴더만 준다 |
| 업데이트·권한 | `updater.settle(exe, pid=None, status=None, timeout=None) -> str` (:271), `state(exe, pid=None) -> tuple[str, str]` (:149), `utils/elevation.py` | UAC 부분은 코어. 업데이트 창 제목(`UPDATE_TITLE_RE`)은 어댑터 |
| **로그인** | `login.login(company_code, user_id, password, pid=None, timeout=None) -> str` (:502). 예외 `LoginError` :68 / `DuplicateLogin` :72 / `LoginRejected` :85 | 인자 구성이 프로그램마다 다르다. 코어는 "입력 → 버튼 → 팝업 분기 → 완료 대기" 틀과 예외 종류만 |
| **단계 표** | `steps_erpia.plan(ordered_sources: list[str]) -> list[Step]` (:67), `ALL` :60. 수집은 `steps_collect.plan() -> list[Step]` (:29) | `Step(id, name, reentry, replay)` 형식은 코어(`steps.py:65`). 재진입 등급은 어댑터가 단계마다 정한다 |
| **흐름 실행** | `erpia_flow.run(options: Options, hooks: Hooks \| None = None) -> Result` (:335), `Options` (:53). `collect_flow.run(hooks=None, dry_run=False) -> Result` (:41) | 흐름 안에서 `hooks.stage(step)` (`common.py:220`)·`hooks.skip`·`hooks.count` 를 쓰는 규칙은 코어 |
| **단계별 조작 함수** | `order_mapping.enter_order_mapping(target, dry_run=False)` :310, `collect_orders(screen, source="site", …, dry_run=False) -> int` :881, `upload_excels(screen, items, pid=None, …)` :752, `process_sales(screen, main_window=None, wait_collect=True, dry_run=False) -> dict` :1388, `go_to_logistics_wait(target, dry_run=False)` :1451, `logistics_wait.run(target, dry_run=False) -> dict` :1101, `logistics.run(target, courier=None, box=None, mode=None, dry_run=False) -> dict` :791 | 공통 형태: `(target 또는 screen, …, dry_run) -> 결과 dict`. **`dry_run` 을 모든 조작 함수가 받는다** — 새 어댑터도 지킨다 |
| **완료 판정** | `logistics.wait_saved(screen, main_window=None, timeout=None) -> bool` :627, `logistics_wait.wait_bottom_reloaded(bottom_grid, timeout=None) -> bool` :678, `wait_bottom_settled(bottom_grid, timeout=None) -> int` :739, `order_mapping.collect_counts(screen) -> dict[str, int]` :1217 | 판정 **기법**(폴링, 창 응답 지연)은 코어, 판정 **대상**(auto_id, 문구)은 어댑터. 6번 함정 "상태 표시는 완료 신호가 아니다" |
| **계정별 화면 차이 판정** | `logistics_wait.menu_available(target) -> bool` :132, `logistics.current_mode(screen) -> str` :544, `select_mode(screen, mode, pid, dry_run=False) -> str` :553, `normalized_mode(mode) -> str` :527 | 없는 메뉴는 **건너뛰는 경로**(`hooks.skip`)까지 어댑터가 책임진다 |
| 재개할 때 화면 상태 설명 | `erpia_flow.screen_state_for(step, screen, uploads=None) -> str` :106 → `Hooks.approve_start(step, screen_state="")` (`common.py:188`) | 구간 실행 확인 문구 |
| 상세 표 | `erpia_flow.upload_table`/`sales_table`/`hold_table`/`logistics_table` :153-213, 열 이름 `*_COLUMNS` :146-150 → `Hooks.count(step, …)` (`common.py:277`) | 오버레이·리포트는 받은 표를 그리기만 한다 |
| 화면 오류 예외 | `order_mapping.ScreenError` :161, `logistics_wait.ScreenError` :105, `logistics.ScreenError` :93 | 공용 기반 예외를 상속하게 한다(3번 절) |
| **GUI 입력 항목** | `erpia_app._build` 의 입력 칸(비밀번호 :216, 택배사 :259, 박스 :272, 자동/수동 `_mode_selector` :363), `_values`, `_saved_values` :685. 실행용은 `run_app._build` :79, `_saved_values` :235 | 항목마다 (라벨, 설정 키, 종류: 글자/비밀/선택, 선택지, 도움말)만 주면 코어 창이 그리게 |
| 배포 값 | spec 의 진입 파일·exe 이름·기능 모듈 목록 | 2번 절 spec 표 |

코어가 어댑터에 주는 것: `utils/`(대기·중단·프로세스·잠금 화면·DPI·로그·비밀·예약),
`utils/ui.py` 앞부분, `orchestrator/steps·common·report`, `gui/common·pipeline·overlay·autorun_pane`,
설정 로더(`config/settings.py` 의 병합·비밀 풀기), `automation/application.py`, UAC 탐지,
`collect/storage·manifest·auth_code·phonelink·sms`.

---

## 5. 재사용할 개발 체계

### 훅 / 스킬 / 에이전트

| 파일 | 판정 | 근거 |
| --- | --- | --- |
| `.claude/hooks/guard_paths.py` | 무관 | 허용 경로는 `CLAUDE_PROJECT_DIR`·`~/.claude`. docstring(:4-5)에 경로 예시만 |
| `.claude/hooks/check_rpa_rules.py` | 무관 | `time.sleep`·좌표·경로 확장자·`except: pass`·무제한 루프 같은 일반 규칙 |
| `.claude/hooks/confirm_run.py` | 무관 | `python -m …` 실행 사유 확인만 |
| `.claude/hooks/cleanup_temp.py` | 무관 | `docs/_dump_*`, `logs/*.log` 같은 파일명 규칙 |
| `.claude/settings.json` | 무관 | 훅 등록 |
| `.claude/skills/ui-survey/` | 무관 | 조사 절차·판정표가 일반적 |
| `.claude/agents/rpa-scout.md` | **고친다** | 역할(읽기 전용 조사)은 그대로. "어디서 찾나"(:19-24)가 이 프로젝트 문서·폴더 이름에 묶였다. 어댑터 폴더가 생기면 목록을 바꾼다 |
| `.claude/agents/rpa-verifier.md` | **고친다** | 원칙은 그대로. 대상 이름(:9 `ERPia, 메일 사이트`)과 probe 표(:19-29)가 이 프로젝트 것 |

### `tools/probe_*` (33개) + 덤프 도구

| 판정 | 파일 |
| --- | --- |
| **무관** — 그대로 쓴다 (probe 14 + 덤프 2) | `probe_autorun`, `probe_contrast`, `probe_dialog`, `probe_failure`, `probe_overlay`, `probe_phone_wait`, `probe_phonelink`, `probe_schedule`, `probe_search_trace`, `probe_secret`, `probe_shot`, `probe_sms`, `probe_speed`, `probe_updater` + `survey_windows`, `dump_controls` |
| **섞임** — 틀은 쓰고 값을 바꾼다 (7) | `probe_resources`(기본 프로세스 `<실행 파일 이름>` :309) / `probe_timing`(기본 화면·auto_id :38-39) / `probe_rect_settle`(시험 영역 실측값 :162) / `probe_gui`(창 모듈 표 :31-35) / `probe_report`·`probe_pipeline`(표본 단계명이 ERPia 업무) / `probe_progress`(검증 대상이 `erpia_flow`·`steps_erpia`) |
| **묶임** — ERPia·메일 사이트(사이트A) 전용 (probe 12 + 덤프 1) | `probe_<메일 사이트>`, `probe_collect`, `probe_delivery`, `probe_entry`, `probe_excel_upload`, `probe_grid_access`, `probe_hold_loop`, `probe_mail_search`, `probe_screen`, `probe_segment`, `probe_sidebar`, `probe_site_names` + `dump_erpia` |

- `probe_phonelink`·`probe_phone_wait` 는 [휴대폰 연결] 앱 대상이다. 메일 사이트(사이트A)에 묶이지 않아 무관으로 뒀다
- `probe_overlay` 는 `BAR_SAMPLE` 을 **실제 단계 표와 대조**한다 — 어댑터 단계 표가 늘면 대조 대상도 늘린다
- `probe_screen`(화면 조사 주력)·`probe_sidebar` 는 ERPia 에 묶였지만 **새 프로그램용 같은 도구를 먼저 만들 가치가 있다**
- `tools/test_*` 는 이번에 조사하지 않았다 (미확인)

---

## 6. 함정 목록 — 다른 프로그램에도 해당될 것

| # | 함정 | 대처 | 근거 |
| --- | --- | --- | --- |
| 1 | **예외가 없다고 동작한 것이 아니다.** 클릭·Invoke 가 조용히 헛돈다 | 반환값이 아니라 화면 변화(창 등장·소멸, 값 변화)로 판정 | `docs/CONTROLS.md:312` |
| 2 | **잠긴 화면에서는 클릭이 조용히 무시된다.** 로그인 실패처럼 보인다 | 조작 전 잠금 확인, 잠겼으면 건너뛴다(풀 수 없다) | `docs/archive/HANDOFF_20260917.md:1602` |
| 3 | 입력 데스크톱이 열려도 잠금 화면 앱이 맨 앞일 수 있다 | 맨 앞 창의 프로세스(`LockApp.exe`)까지 본다 | `docs/archive/HANDOFF_20260917.md:2566` (33-3절), `utils/process.py` |
| 4 | **UAC 동의 창은 누를 수 없다** (보안 데스크톱·UIPI) | 탐지하고 사람에게 알린다 | `docs/archive/HANDOFF_20260917.md:1043`, `docs/HANDOFF.md:71` |
| 5 | SYSTEM 프로세스(`consent.exe`)는 경로를 못 읽는다 | 이름으로 찾는다 | `docs/HANDOFF.md:105` |
| 6 | **상태 표시(성공/진행중/실패)는 완료 신호가 아니다** | 독립된 신호(버튼 활성 등)로 판정 | `docs/CONTROLS.md:1185`, 메모리 `erpia-collect-status-not-completion` |
| 7 | "조회가 끝났다" 신호가 아예 없는 화면이 있다 — 대기 상한이 그대로 소요시간이 된다 | 창 메시지 응답 지연으로 바쁨/한가를 재고 짧은 유예를 둔다 | `docs/CONTROLS.md:1218` |
| 8 | 가상 스크롤 그리드·드롭다운은 **보이는 항목만** UIA 에 나온다 | 맨 위로 올린 뒤 끝까지 스크롤하며 센다 | `docs/CONTROLS.md:173`, `:596`, `:938` |
| 9 | `Is…PatternAvailable` 이 참이어도 패턴 획득이 실패할 수 있다 | 실제 획득까지 확인 | `docs/CONTROLS.md:596` |
| 10 | ScrollPattern 없는 그리드에서 키보드 스크롤이 다른 컨트롤로 간다 | 스크롤바 안의 Invoke 버튼을 쓴다. 더 내릴 수 있는지는 버튼 유무로 | `docs/CONTROLS.md:544`, `docs/HANDOFF.md:99` |
| 11 | **닫힌 콤보 위의 휠·방향키는 "읽기만" 해도 값을 바꾼다** | 값을 안 바꾸는 수단만 쓰고 전후 값을 대조 | `docs/CONTROLS.md:969` |
| 12 | **잘린 셀** — 일부만 보이는 셀을 누르면 스크롤바를 누른다. 클릭 순간에도 행이 움직인다 | 누르기 직전 완전히 보이게 하고, 신원으로 다시 잡아 위치가 멎을 때까지 기다린다 | `docs/CONTROLS.md:1081`, `utils/ui.py` `reveal_cell` :755 / `wait_rect_settled` :647 |
| 13 | 셀 `Name` 이 값이 아니라 `<열> 행 N` 식별자다 | Value → LegacyIAccessible → 자식 Text 순으로 읽는다 | `docs/CONTROLS.md:159` |
| 14 | Toggle 패턴이 없어도 상태는 Value 에 있을 수 있다. 확인 없이 누르면 켜진 것을 끈다 | 누르기 전에 읽고, 이미 원하는 상태면 안 누른다 | `docs/CONTROLS.md:425` |
| 15 | **팝업이 어디에 붙는지는 종류마다 다르다.** 로그인 팝업은 부모 창의 자식이라 최상위 탐색에 안 잡히고, 컨텍스트 메뉴는 **제목 없는 별도 최상위 창**이다. 프로세스 전체 탐색은 수십 초 | 종류마다 조사해 확정하고, 팝업 창 범위부터 좁혀 찾는다 | `docs/CONTROLS.md:33`(로그인 팝업), `:811`(컨텍스트 메뉴) |
| 16 | 큰 UIA 트리에서 조건 탐색은 조건과 무관하게 끝까지 훑는다 | 하나 찾기는 한 단계씩 내려가는 얕은 탐색 | `docs/CONTROLS.md:1288` |
| 17 | **계정마다 화면이 다르다** (메뉴 유무, 버튼 구성, 기억값) | 자리가 아니라 auto_id 로 찾고, 없을 때 건너뛰는 경로를 명시 | `docs/HANDOFF.md:101-102`, `docs/CONTROLS.md:735` |
| 18 | 뒤에 있는 탭의 컨트롤은 **찾아지지만** 좌표가 창 밖이다. 예외도 없다 | 조작 전 탭·창을 앞으로 | `docs/CONTROLS.md:1041` |
| 19 | 표준 파일 창을 이름으로 찾으면 엉뚱한 컨트롤이 걸린다. 숫자뿐인 auto_id 는 실행마다 바뀐다 | 고정 컨트롤 ID(IDOK=1 등) + control_type | `docs/CONTROLS.md:324` |
| 20 | **계측기가 죽어 있어도 "정상" 처럼 보인다** (`tuple(RECT)` 사고) | 계측을 넣으면 값을 내는지 먼저 본다. 실행 중 소스를 고치지 않는다 | `docs/CONTROLS.md:1157`, 메모리 `verify-the-instrument-first` |
| 21 | **셸 timeout 이 끝나도 파이썬 자식이 살아 대상 프로그램을 계속 조작한다** | 넉넉한 timeout (전체 흐름 9분) | `docs/archive/HANDOFF_20260917.md:1515`, `CLAUDE.md` |
| 22 | 대상 프로그램은 자동화가 죽어도 고아로 남아 다음 실행이 중복 로그인에 걸린다. 오류 문구가 원인과 다르게 나올 수 있다 | 시작 전 기존 인스턴스 정리, 문구 분기가 못 잡는 경우를 로그로 | `docs/ERROR_HANDLING.md:203-204` |
| 23 | 확인하려는 코드가 dry-run `return` 뒤에 있으면 dry-run 으로는 영원히 검증 못 한다 | dry-run 경계를 조작 직전에 둔다 | `docs/HANDOFF.md:103` |
| 24 | 느린 PC 는 CPU 부하로 재현한다. 배율을 재지 않고 "느린 환경" 이라 말하지 않는다 | 배율을 재고 기록 | `docs/HANDOFF.md:104` |
| 25 | tkinter 위젯은 UIA 에 없다 — 우리 창은 도구로 누를 수 없다 | 코드 안에서 만들어 확인, [실행] 은 사람이 | `docs/HANDOFF.md:96-97` |
| 26 | **셸을 거쳐 파일을 쓰면 역슬래시 이스케이프가 먹힌다** — 경로의 `\b` 가 백스페이스로, heredoc 의 `\n` 이 실제 줄바꿈으로 박힌다 | 파일 편집 도구로 쓰거나 `chr(0x5C)` | 메모리 `windows-path-backslash-eaten`(경로), `docs/HANDOFF.md:106`(heredoc) |

고배율 DPI(150%/200%)는 실측이 없어 넣지 않았다 (`docs/HANDOFF.md:48` 미검증).

---

## 7. 새 프로그램 접수 체크리스트

사용자에게 받는다. 빠진 것이 있으면 조사를 시작하지 않는다.

- [ ] **실행 파일 경로와 버전.** 바로가기 인자·작업 폴더. 단일 인스턴스만 허용하는가. 관리자 권한으로 뜨는가
- [ ] **자동 업데이트가 있는가.** UAC 를 거치는가 (함정 4)
- [ ] **망가뜨려도 되는 테스트 계정.** 없으면 저장 동작은 실계정 승인 없이는 돌리지 않는다
- [ ] **같은 계정 동시 로그인** 이 막히는가 (함정 22)
- [ ] **계정마다 다른 화면** — 메뉴·버튼·기억값이 다른 계정이 있는가 (함정 17)
- [ ] **화면마다 완료를 판단하는 기준.** 사람이 "끝났다" 고 보는 근거가 무엇인가 (함정 6, 7)
- [ ] **자주 나는 예외와 대처.** 팝업 문구, 사람이 누르는 버튼
- [ ] **저장·전송의 되돌리기 방법.** 되돌릴 수 없는 단계는 무엇인가 (재진입 등급 `IRREVERSIBLE`)
- [ ] **작업 녹화.** 사람이 처음부터 끝까지 하는 화면 녹화 한 벌
- [ ] 설정으로 받을 값 목록 (경로·계정·업무 선택값), 그중 비밀인 것
- [ ] 무인 실행(예약)이 필요한가

---

## 8. 새 프로그램 진행 절차

담당: **메인** = 메인 세션 / **scout** = `rpa-scout`(읽기 전용) / **verifier** = `rpa-verifier` / **승인** = 사용자 승인.
화면은 하나라서 실제 화면 조사는 메인이 한다.

| # | 단계 | 담당 | 산출물 / 끝나는 조건 |
| --- | --- | --- | --- |
| 0 | 접수 | 사용자 → 메인 | 7번 체크리스트가 채워졌다 |
| 1 | **UIA 판정** — 실행·연결, 최상위 창, 컨트롤이 UIA 로 보이는가 | 메인 (`ui-survey` 스킬, `survey_windows`, `dump_controls`) | 화면별 판정(UIA/키보드/이미지/좌표/OCR) |
| 2 | **화면 조사** — 단계마다 컨트롤·팝업·완료 신호·계정 차이 | 메인. 녹화·과거 문서에서 근거 찾기는 scout | 덤프(중간 파일) |
| 3 | **식별표 작성** — 확정 auto_id·control_type·읽기 방법 | 메인. 덤프 정리 후 덤프는 지운다 | 프로그램별 `CONTROLS` 문서 |
| 4 | **단계 표 작성** — `Step(id, name, reentry, replay)`, 재진입 등급 | 메인 → **승인**(등급과 저장 범위) | 어댑터의 단계 표 |
| 5 | **코어 분리** (두 번째 프로그램일 때만) — 이 문서 3번 절 결합 지점부터 | 메인. 영향 범위 검색은 scout | 분리 뒤 기존 probe 전부 통과 |
| 6 | **코드 작성** — 어댑터(설정 필드, 실행, 단계별 조작, 완료 판정, 계정 차이, GUI 입력). 모든 조작에 `dry_run` | 메인 | 식별표에 없는 값은 쓰지 않는다 |
| 7 | **확인 도구 실행** — 대상 프로그램을 건드리지 않는 probe | verifier (새 probe 작성은 메인). 화면을 띄우는 probe 는 이름을 짚어 요청 | 실패 0건 |
| 8 | 독립 검토 | scout (읽기 전용) | 틀린 것 목록 → 메인이 고친다 |
| 9 | **dry-run 실행** (`test_*` 는 조작 도구) | **승인** → 메인 (`APPROVED-RUN`, timeout 넉넉히) | 저장 직전까지 화면 확인 |
| 10 | **승인받은 실제 실행** — 테스트 계정 먼저 | **승인** → 메인 | 실행 기록, 로그 정리 후 삭제 |
| 11 | 빌드 | **요청받을 때만** 메인 | spec 누출 검사 통과 |

---

## 미확인으로 남긴 것

- `tools/test_*` 의 분류 (조사 범위 밖)
- `tools/probe_*` 분류 가운데 메인과 검토가 소스를 직접 연 것은 `probe_resources`·`probe_timing`·`probe_rect_settle`·`probe_gui`·`probe_hold_loop`·`probe_phonelink` 뿐이다. 나머지 27개는 조사 에이전트 한 곳의 보고에 기댄다
- `build_full.bat` / `build_run.bat` 의 샘플 설정 복사 줄 (`build_collect.bat`·`build_erpia.bat` 만 줄을 확인했다)
- `window_title_re` / `window_class_name` 이 정말 안 쓰이는지 — `tools/` 를 뺀 검색에서 읽는 곳이 없었다. 동적 접근(`getattr` 에 문자열 조합)까지는 보지 않았다
- `utils/ui.py` :586 이후 함수 가운데 어디까지 코어에 남길지 — 두 번째 프로그램의 그리드를 봐야 정한다
- `CLAUDE.md` 구조도에 있는 `utils/mouse.py` 는 **파일이 없다** (2026-09-17 목록 확인)
