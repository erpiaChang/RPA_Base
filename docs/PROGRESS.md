# 진행 상태

**지금 상태·열린 항목·다음 할 일은 `docs/HANDOFF.md` 하나에 있다.** 이 문서는
단계 표 / 지켜야 할 제약 / 흐름 / **코드 지도** / 문서 지도만 둔다.
날짜별 실행 기록과 예전 "다음 할 일" 은 `docs/archive/PROGRESS_20260917.md` 로 옮겼다.

| Phase | 내용 | 상태 |
| --- | --- | --- |
| 1~3 | 구조 / 실행·연결 / UIA 조사 도구 | 완료 (09-03) |
| 4~5 | 로그인 / 주문매핑 진입 | 완료 (09-03) |
| 6~7 | 주문수집(자동·엑셀) / 매출처리 | 완료 (09-04) |
| 8 | 물류대기 (보류 → 저장 → 물류처리) | 완료. **물류대기가 없는 계정은 건너뛴다** (09-17). **새 보류 걸기 실행 확인** (09-18, 8행 보류 → 저장 21→20행) |
| 9 | 물류관리 (등록 → 개별배송 → 택배사·박스 → 저장 → 마지막 버튼) | 완료. 자동이면 [운송장출력], **수동이면 [엑셀파일생성]** (09-17) |
| 10 | 오류 처리 / 로그 / Retry / Screenshot | 완료 (09-08) — `docs/ERROR_HANDLING.md` |
| 11 | 전체 통합 테스트 | **완료 (09-17)** — 빌드본으로 11단계 완주 |

> 코드를 고쳤으면 **확인 도구부터** 돌린다(무엇을 돌릴지는 `CLAUDE.md` 표, 무엇을 보는지는 아래 도구 표). 색·글자 크기를 건드렸으면
> `tools/probe_contrast.py`, 화면을 고쳤으면 `tools/probe_shot.py` 로 **찍어서 본다** —
> 확인 도구는 글자 유무와 대비만 센다.

---

## 프로젝트 전역 제약 (모든 Phase)

**개발 PC 외 다른 사용자 PC에서도 실행된다.** 해상도·DPI·창 크기·계정 설정이 다르다.

1. **절대좌표를 저장하지 않는다.** 좌표는 런타임에 `rectangle()` 에서 얻어 쓰고 버린다
2. **식별 우선순위**: `automation_id` > `control_type` + `title` > `class_name`. 인덱스는 최후수단(이유를 주석으로)
3. **DPI 인식을 명시한다** (`utils/dpi.py`). 메뉴 높이 상한 등은 배율을 곱한다
4. **창 상태를 정규화한다.** 작업 전 메인 창 최대화, 해상도를 로그에 남긴다
5. **이미지 매칭·화면 색상 판정은 쓰지 않는다** (지금까지 필요 없었다)
6. **고정 sleep 금지.** `utils/wait.py` 의 조건 대기(timeout 필수)
7. **조사 결과 없이 업무 흐름을 작성하지 않는다**
8. **계정마다 화면 구성이 다르다** — 프로세스바 항목(물류대기 유무), 자동/수동 기억값,
   출력 버튼 구성. 자리나 존재를 가정하지 않는다 (09-17)

---

## RPA 3종 (사용자 확정 09-08)

1과 2는 각각 단독으로 완결되고, 3은 둘을 잇는다. 연결은 **`manifest.json` 파일 계약**이다.
**배포 진입점은 실행용 하나다** (10-06 — 기능별 진입점·창·빌드를 지웠다). 1~3 은 흐름(`orchestrator/*_flow.py`)과 개발 도구로만 남는다.

| # | 흐름 | 진입점 |
| --- | --- | --- |
| 1 수집 | 메일 사이트 로그인 → 2차 인증(문자) → 대상 메일 선별 → 첨부 엑셀 저장 → 매니페스트 | `tools/test_collect.py` |
| 2 ERPia | 실행 → 로그인 → 주문매핑 → 주문수집(엑셀 폴더 → 자동) → 매출처리 → 물류대기 → 물류관리 | `tools/test_flow.py` |
| 3 통합 | [수집] → 매니페스트의 엑셀을 사이트명 완전일치로 업로드(성공분만 `consumed_at`) → 이하 2와 같다 | `tools/test_full.py` |
| 실행용 | 통합과 같은 흐름 + **기능 선택**(09-21) + 설정을 빌드에 굽는다 + 자동 실행(예약) | `main_run.py` → `dist/run/RPA_1.exe` |

- 엑셀수집(2)은 **폴더**를 받는다. 파일명 첫 밑줄 앞이 사이트명 (`사이트A_20260908.xlsx`)
- 메일을 읽지 않음으로 되돌리지 않는다 (09-30, 코드 삭제). 받기 실패는 매니페스트 기록으로 다음 실행에서 다시 연다 (`webmail.retry_keys`)

### 대상 업무 (화면 단위 상세는 `docs/CONTROLS.md`)

```
로그인 → 주문매핑 진입
→ 주문수집: 엑셀(사이트 행 [엑셀업로드]) → 자동(가져오기)
→ 매출처리 (설정 sales_mode, 10-01): 전체 = [매출처리] 단추 → '일괄' 확인 [예] → 결과 [확인] (조회 안 된 미매출도)
                                     선택주문 = 헤더 전체 체크 → 행 우클릭 → [선택주문 매출처리] → [예] → [확인]
→ 물류대기 (메뉴가 없는 계정은 건너뛴다)
    상품별 재고검토 → 조회 → 부족 상품마다 하단 동일코드 행 체크 → 우클릭 → 배송보류
    → 일반 탭 → 조회 → 전체선택 → 저장 → [물류처리]
→ 물류관리
    [자동/수동] 맞추기 → 등록모드 → 하단 전체선택 → 우클릭 → 개별 배송(B)
    → 택배사 선택 → [적용] → 박스 선택 → [적용] → [저장(S)]
    → 저장 확인([등록] 활성) → 자동: [운송장출력] / 수동: [엑셀파일생성]
```

---

## 코드 지도

| 파일 | 역할 |
| --- | --- |
| `main_run.py` | 실행용 배포 진입점. 설정을 빌드에 굽는다. **로그온 때 켜진 빌드본은 작업 스케줄러에 넘긴다** (09-29 — 5분 트리거가 떠 있는 것을 알게. 두 번 누름은 바로 뜬다) |
| `main_build.py` | [개발] 빌드 프로그램 진입점 (`build_tool.bat`) |
| `config/settings.py` | 설정 읽기의 **단일 지점** + `Timeouts` + 비밀 키 풀기 (재시도 상한은 자리마다 상수) |
| `config/fields_collect.py` / `fields_erpia.py` | 기능별 설정 항목 정의 |
| `automation/application.py` | 실행/연결. 흐름은 **늘 새 인스턴스**(`start_new_instance`), 같은 계정만 닫기(`close_others`). `connect` 는 **생성시각이 가장 최근인 인스턴스**(`test_flow --from`·조사 도구). `find_exe`·`is_erpia_exe` — 이 PC 의 ERPia 찾기. 파일 이름은 설정 `target_exe_name` (다른 exe 는 ERPia 로 보지 않는다, 09-29 / 이름을 설정으로 10-02). Program Files 아래 두 단계 → 등록 정보 → 바로가기. `logged_in_pids` — 같은 아이디로 로그인된 ERPia 를 읽기만 (예약 전 경고, 10-02) |
| `automation/login.py` | 로그인 + 직후 팝업 분기. 동시 로그인이면 이 PC 의 앞 인스턴스를 닫고 한 번 더 |
| `automation/sidebar.py` | 좌측 프로세스바 auto_id 3개 (주문수집·물류대기·물류처리) — 화면 모듈 셋이 같이 쓴다 (09-22) |
| `automation/updater.py` | 업데이트 안내 창은 누르고, **UAC 동의 창은 탐지해 사람에게 알린다**(누를 수 없다). `alert` 로 서버에도 — 업체 담당자 메일 (10-02) |
| `automation/order_mapping.py` | 화면 진입 / 주문수집(자동·엑셀) / 매출처리. **버튼 없는 사이트 행은 안 누른다**. `collect_counts_all` — 수집로그를 **끝까지** 내려 센다 (09-22). **매출처리 방식은 설정 `sales_mode`** (10-01) — `전체` [매출처리] 단추 → '일괄' [예] / `선택주문` `select_all_orders`·`open_selected_sales` |
| `automation/logistics_wait.py` | 물류대기 전체. `menu_available()` — **메뉴가 없는 계정 판정** / `ensure_bottom_sorted()` — 하단 정렬 후 묶음 끝에서 스캔 중단 (09-17). **주문 수는 행이 아니라 전표로** (09-29) — 보류는 체크한 행의 `전표번호`, 저장은 [일반] 탭 전후 `count_general_slips` (전표번호가 오른쪽 밖이라 가로로 넘겨 읽는다) |
| `automation/logistics.py` | 물류관리 전체. `select_mode()` 자동/수동, `press_finish()` 마지막 버튼. `count_slips` — 하단 `매출번호` 를 **끝까지** 읽어 중복 없이 = 올라간 주문 (09-29). `count_boxes` — **만든 전표 = 박스(송장) 수**, 저장 직전 상단 `배송업체` 가 찬 줄 (09-29 실기: 주문 2건 → 송장 31) |
| `collect/webmail.py` | **메일 사이트 틀** (10-02) — `MailSite` 계약(로그인 한 번·목록·메일 열기·첨부 단추·목록으로) + `login`(재시도)·`collect`(대상 선별·받기·매니페스트). 사이트마다 다른 것은 사이트 파일에만 — 목록을 기간 안쪽이 끝날 때까지 넘기는 일도 사이트 파일(`list_rows_until`, 상한 `MAX_PAGES`=20) |
| `collect/browser.py` | 시크릿 창 브라우저 (Playwright, `CHANNELS` msedge·chrome) |
| `collect/sites/__init__.py` | 사이트 목록 — git 밖 `collect/sites/local/` 을 글자 그대로 불러온다(빌드가 따라 묶는다). 없으면 `SiteMissing` — 실행 창은 '메일 사이트 연결' 입력 필요. **원본에는 사이트가 없다** (`docs/SITES.md`) |
| `collect/pw_driver.py` | **메일 브라우저 node.exe 를 켤 때마다 풀지 않는다** (09-29) — 빌드가 exe 리소스로 넣은 것을 메일 기능을 처음 쓸 때 exe 옆 `driver\<해시>\` 로 한 번 꺼낸다(해시 검사·옛 판 지우기) |
| `collect/auth_code.py` | 인증번호 경로 선택 (`sms_source`: phonelink / adb / auto). `precheck` — 예약 전 **보기만 하는** 점검 (10-02) |
| `collect/phonelink.py` | [휴대폰 연결] 앱에서 인증 문자 읽기. **읽기 전 [메시지] 탭 맞추기**(UIA 선택), **대화 목록 우선**(대화창 비면 폴백), 끊기면 [다시 시도] 한 번 + **최대 5분** 대기. **오프라인이면 캐시 목록으로 준비됨이라 하지 않는다**. `peek` — 앱을 띄우거나 누르지 않고 연결 상태만 (10-02) |
| `collect/sms.py` | 인증 문자 읽기 (adb). 문자 조회만 허용 |
| `collect/storage.py` / `manifest.py` | 날짜·차수 폴더 규칙 / `manifest.json`. 소비 표시는 **그 항목에** (`mark_consumed(item)` — 받기 실패 뒤 다시 받으면 같은 메일 키가 둘이다, 10-02) |
| `utils/ui.py` | **모든 UI 조작의 단일 창구.** `reveal_cell()` 잘린 셀 보이게 / `grid_data_area()`. **건수 세기** (09-29) — `scan_column`·`distinct_count` 끝까지 내려 한 컬럼을 중복 없이(스크롤바 버튼만) / `reveal_column`·`columns_home` 가로로 가려진 컬럼 보이게·되돌리기 |
| `utils/dialogs.py` / `filedialog.py` | 팝업 / 파일 선택 창. `dismiss_message_box()` — ERPia 알림이 **[확인] 하나면 닫는다** (09-22) |
| `utils/wait.py` / `cancel.py` | 조건 대기(timeout 필수, 취소 확인) / 중단 토큰(`Cancelled` 는 BaseException) |
| `utils/process.py` | PID·생성시각·**이름으로 찾기**(`pids_by_name`) / `screen_locked()` (잠금 화면 앱 포함) |
| `utils/winprobe.py` | 최상위 창을 싸게 찾기 (Win32). `rect_of()` |
| `utils/schedule.py` / `autorun.py` | 예약 계산 — **예약 여러 개, 날 조건(매일·요일·매월 N일·날짜 지정), 줄마다 기능** (09-21) / 지금 돌려도 되는가 + 회차 기록. **10-02**: [일시정지] 를 `logs/autorun_state.json` 에 남긴다, `skip()` — 예약 전 경고의 [이번만 건너뛰기], 시각을 10분 넘게 놓치면 건너뜀 |
| `utils/secret.py` / `envfile.py` | 비밀번호 감싸기. 로컬 `dpapi:`(그 PC에서만) / 구운 값 `baked:` — 씨앗은 git 밖 (`.env` 의 `BAKED_SEED`, 빌드본은 번들 안 `config/baked.key`, 10-02) / `.env` 읽기 |
| `orchestrator/collect_flow.py` / `erpia_flow.py` / `full_flow.py` | 기능별 업무 흐름. `full_flow` 는 **고른 기능만** 돈다. **메일 단계가 실패해도 뒤 기능으로 넘어간다** (09-30 — 메일만 골랐거나 [중단] 이면 멈춘다) |
| `orchestrator/modules.py` | **기능 선택(모듈)** — 메일 엑셀 받기 / 주문수집·매출처리 / 물류대기 / 물류관리. 순서 고정, 과금 행위 단위 (`docs/BILLING.md`). `plan` 이 단계마다 **큰 단계**(`Step.stage` = 모듈 이름)를 붙인다 — 화면은 11단계가 아니라 큰 단계 넷으로 보인다 (09-29, `steps.stage_numbers`). `resume_from` — 이력 한 줄에서 멈춘 기능부터 끝까지 ([멈춘 곳부터 다시], 10-02). `baked_required` — 기능별로 꼭 구울 값 (굽기 도구·실행 창 알림이 같이 쓴다, 10-02) |
| `orchestrator/common.py` | `Hooks` — 단계 통지·건수·구간 실행·확인. **단계마다 사용자 로그 한 줄**, 실패는 `explain` 으로 사람 말 (원문은 `error_detail`). `recover` — 단계 실패 때 ERPia 알림을 닫았으면 **다음 단계로** (09-22) |
| `orchestrator/friendly.py` | **실패를 사람 말로** — 예외 클래스·문구 규칙 → "무슨 일 / 할 일" 두 줄. 엑셀 한 건 실패 사유도 (09-21) |
| `orchestrator/steps.py` / `steps_erpia.py` / `steps_collect.py` | 단계의 틀 / 기능별 단계 표 (**재진입 정책의 기준**) |
| `orchestrator/report.py` | 실행 후 HTML 리포트 (`logs/report_*.html`) |
| `orchestrator/history.py` | **지난 실행 이력** — 실행이 끝날 때마다 `logs/history.jsonl` 에 한 줄(시각·어떻게·기능·결과·걸린 시간·확인할 것·리포트 경로). 상한 500줄 (09-22). 건너뛴 예약도 `record_skipped` 로 — 같은 이유가 이어지면 한 줄에 횟수 (10-02) |
| `utils/machine.py` | 이 PC 의 `MachineGuid` (`machine_id`) — 서버 확인·PC 바인딩이 쓴다 |
| `orchestrator/telemetry.py` | **서버 보고** — `verify()` 서버 확인·PC 바인딩(빈 이벤트 `ingest` 한 번 — OK / BLOCKED / LATER, 09-28) · `Hooks.on_step`·`attend`·`user` 로거를 감싸 큐에 넣고 스레드 하나가 `rpc/ingest` 로 보낸다(2초/20개, 심박 30초, 5초 상한). 401/400/429 는 버리고 5xx·네트워크는 `logs/outbox.jsonl`. 끝을 못 알린 실행은 `run_inflight.json` 으로 `lost`. dry-run 은 안 보낸다 (`docs/SERVER_PLAN.md` D-2·D-10). `alert` — 사람 손이 지금 필요한 일(UAC 대기·휴대폰 점검 실패)을 **따로 한 건씩, outbox 없이** (10-02) |
| `orchestrator/remote.py` | **원격 설정·명령** (09-28) — 스레드 하나가 30초마다 `rpc/poll`. 웹 설정(`REMOTE_KEYS`, 비밀번호·경로 없음)과 명령(`start`·`stop`·`pause`·`resume`)을 큐에 넣고, 창이 1초 시계에서 적용한다. 창에서 고친 값은 `push()` — 서버 최신판을 받은 뒤에만 서버가 받는다. 서버 판에 없는 키(새 설정)는 창이 채워 올린다 (10-02, `sales_mode`) |
| `llm/worker.py` | **사용법 질문 워커** (09-28, LLM 전용 PC 에서만. RPA 빌드에 안 들어간다) — `llm_take`(서버가 2초 기다림)로 질문을 가져가 LM Studio(`qwen/qwen3-8b`, 이름 `rpa-help`, 문맥 8192)로 스트리밍 답을 만들며 `llm_write` 로 흘려 쓴다(쓰기는 따로 도는 스레드). 설명서만 보고 답한다. `--init`(워커 키, 해시만 찍음) / `--install`(자동 켜기) / `--bench`(속도·답). 로그 `logs/llm/` |
| `llm/guide.md` | 워커가 답할 때 넣는 **사용자용 설명서** — 화면 이름 기준, 내부 값 금지. 고치면 다음 질문부터 바뀐다 (9000자 이하, 늘리면 `--bench` 로 문맥 토큰을 본다) |
| `utils/instance.py` | **한 벌만 뜬다** (09-28) — exe 경로로 이름 붙인 뮤텍스. 자동 켜기가 5분마다 띄워도 겹치지 않는다 |
| `utils/crashlog.py` | **처리 안 된 예외를 logs 에 남긴다** (10-07) — exe 는 stderr 가 없어 Tk 콜백·스레드·켜는 중 예외가 사라졌다. `logs/crash_YYYYMMDD.log` + 처음 보는 오류만 알림(프로세스당 3번). 실행 중·무인이면 상태줄만, 창이 뜨기 전 실패는 사람이 켰을 때만 알림 창. 표준 라이브러리만 (설정이 깨져도 남긴다) |
| `utils/autostart.py` | **자동 켜기** (09-28) — 로그온 때(`HKCU\...\Run`) + 5분마다 "안 떠 있으면 켜기"(작업 스케줄러 XML — 72시간 종료·배터리 조건 끔). `--background` 로 최소화 시작. 인자·작업 폴더·이름을 넘기면 LLM 워커도 같은 방식으로 건다 (`RPA_LLM_worker`). **09-29**: RPA 작업은 `--background --task`·우선순위 보통(5), `hand_over` — 로그온(Run)으로 켜진 것은 작업에 넘긴다 |
| `gui/erpia_app.py` | 실행용 창의 **부모** — 실행·예외 처리·무인 경로. 혼자 띄우지 않는다 (10-06) |
| `gui/run_app.py` | 실행용 창 (760×640, `erpia_app` 상속 — 구간 실행 UI 는 끈다 `SUPPORTS_SEGMENT=False`). **위쪽 탭 넷** — [홈] 상태 한 줄·큰 [실행]·실행할 기능·다음 예약·마지막 실행, [실행] 하면 같은 자리가 진행 화면 / [예약] / [실행 기록] 지난 실행 + 건너뛴 예약 / [설정] 기능별 묶음·**바꾸면 바로 저장**(사람이 친 것만, 바뀐 값만). **실행 중에는 홈에 고정**(다른 탭 disabled)·홈 아래 [일시정지]·[중단]. 입력 13가지(`docs/SETTINGS.md` "고정값은 로컬 설정이 못 덮는다" 절의 화면 항목 표). ERPia 경로는 켤 때와 [실행] 때 `_repair_paths` 가 찾아 맞추고(못 찾으면 `_fail_no_erpia`), 엑셀 저장 폴더는 기본값이 없어 비면 입력 필요 (10-02). **창이 뜰 때 서버 확인**(`_verify_build`) — 확인 중·거부면 [실행] 잠김. 안 고른 기능의 입력은 잠근다. 예약 회차는 그 줄의 기능으로. 기능 고정 빌드면 기능 체크가 잠긴다. **10-02**: 예약 5분 전 같은 아이디 ERPia 경고 창·[이번만 건너뛰기](`_warn_before_run`·`_skip_this`) / 메일 예약 30분 전 휴대폰 점검(`_precheck_before_run`, 홈 '다음 예약' 둘째 줄) / [멈춘 곳부터 다시](`on_resume`) / 맨 위 알림 창은 비모달 하나(`_show_notice`) |
| `gui/build_app.py` | [개발] **빌드 프로그램** — 설정값을 절별로 보여 주고 고쳐서 저장·빌드. 기능 고정/실행 창 선택을 고른다 (09-22) |
| `gui/common.py` / `pipeline.py` / `overlay.py` | GUI 공통 조각 / 진행 파이프라인(큰 단계가 있으면 큰 단계 동그라미 + 아래 세부 단계 줄, `place()` = `2/4`) / ERPia 위 오버레이([일시정지]/[계속하기] — 예전 [잠깐 멈춤]) |
| `gui/history_window.py` | 지난 실행 표 — `HistoryPane`(실행 창 [실행 기록] 탭, 건너뛴 예약도 기록 파일에서, 10-02) / `HistoryWindow`(따로 뜨는 창 — 기능별 창이 쓰던 것. 10-06 부터 부르는 창이 없다, `probe_history` 만). 줄을 두 번 누르면 그 리포트 (09-22) |
| `gui/autorun_pane.py` | 자동 실행 설정(**예약 목록 — 반복·요일·시각·기능 골라 [추가]/[삭제]**) + 회차 기록 표(`history=False` 면 없음 — 실행 창). 예약 멈춤 단추는 웹과 같은 [일시정지]/[계속하기] (09-29) |
| `build_run.spec` / `build_tool.bat` | 빌드 정의(유일) / 빌드 프로그램. `tools` 는 빌드에서 뺀다. 기능별 빌드(collect/erpia/full)는 10-06 에 지웠다 |
| `server/schema.sql` / `server/README.md` | **서버(Supabase) 스키마 원본** — 표·RLS·`ingest`(PC 의 보고 창구. **빌드 ID + PC 바인딩** — 처음 보고한 PC 에 묶고 그 뒤로는 대조)·`register_build`(관리자, 빌드 프로그램이 부른다)·`revoke_device`·pg_cron(끊김 판정·180일 삭제·알림 메일 1분 — 끊김·무인 실행 결과·UAC·휴대폰, 업체 owner+관리자, 10-02) (09-28, `docs/SERVER_PLAN.md` D 절). 7절 원격 설정·명령, **8절 사용법 질문**(`questions`·`llm_workers`, `ask_question`·`llm_online`(웹) / `llm_take`·`llm_write`(워커 키) / `expire_questions` cron 1분, D-8), **9절 사용량**(`usage_daily` — `refresh_usage` cron 10분이 run_steps 에서 요금 기준 횟수를 센다, 지우지 않는다, D-9). 빌드와 무관 |
| `web/index.html` / `app.js` / `config.js` / `_headers` / `wrangler.jsonc` / `README.md` + `deploy_web.bat` | **웹 대시보드** — 정적 한 장. **09-28 개편**: 위쪽 탭 넷(요약·사용량·PC·실행 기록, 주소 `#/...`) + 어디서나 여는 [사용법 질문] 옆 패널(입력칸 아래 고정·대화만 스크롤). **사용량** = `usage_daily`(요금 기준 사용 횟수, 관리자는 업체별·사용자는 자기 업체만 — RLS). 쓰기는 RPC 셋(`send_command`·`set_device_settings`·`ask_question`)뿐, 제어·설정은 admin·그 업체 owner 만. 진행 중인 실행 상세는 5초마다 다시 읽는다 (10-02). `textContent` 만, SRI, CSP. `deploy_web.bat`(wrangler@4). **SQL 9절을 먼저 적용하고 배포한다** |

### 개발 도구 (`tools/`, 빌드 제외) — `probe_*` 읽기 전용 / `test_*` 조작

| 파일 | 역할 |
| --- | --- |
| **확인 (대상 프로그램을 안 건드린다)** | |
| `probe_progress.py` | 단계 통지·중단·구간 실행·상세 표·결과 문구(물류관리 = 송장 수 + 주문 수) (47) |
| `probe_rect_settle.py` | 위치 계측·잘린 셀 보이게·버튼 판정·`ui.py` 검토 수정(키 입력 이스케이프·화면 밖 클릭 안 함·체크 반영 대기·모니터 배율) (27) |
| `probe_hold_loop.py` | 물류대기 보류 루프·메뉴 없는 계정·하단 정렬·**건수는 데이터로**(끝까지 세기·[일반] 가로 컬럼·보류 주문 중복 없이·조회된 주문수·물류관리 박스 수) (53) |
| `probe_failure.py` | 실패 주입·알림 닫고 다음 단계로·같은 계정만 닫기·인증 재시도·매출처리 팝업 경합·'일괄' 창 [예]·선택주문 메뉴는 마우스 클릭만·매출처리 방식(전체/선택주문) 분기·빈 값은 멈춤·같은 아이디 ERPia 찾기·[멈춘 곳부터 다시] 계산·재시도 상한이 상수로 있는지·자동수집 대기 중 알림 닫기와 버튼 읽기 실패(10-07)·**진짜 창**(가짜 ERPia WinForms 팝업 — 같은 auto_id) (61) |
| `probe_schedule.py` / `probe_autorun.py` | 예약 계산(요일·매월·날짜 지정·줄마다 기능·방식이 비면 안 돈다) (97) / 자동 실행 판정·잠금 화면·이번만 건너뛰기·놓친 시각·일시정지 저장 (59) |
| `probe_updater.py` | 업데이트·UAC 판정·실행 직후 UAC·관리자 권한 재실행 (18) |
| `probe_phone_wait.py` | 휴대폰 연결 대기 상한·인증번호 목록 우선·[메시지] 탭·지난 번호 방지·가려진 창·예약 전 점검은 보기만 (48) |
| `probe_secret.py` | 비밀번호 감싸기/풀기 (37) — 씨앗이 코드에 없는지·없으면 멈추는지 |
| `probe_report.py` / `probe_pipeline.py` | 리포트 (12) / 진행 파이프라인·큰 단계 넷 (41) |
| `probe_contrast.py` | 색 대비와 글자 위계 (60) |
| `probe_modules.py` | 기능 선택·순서·**메일이 멈춰도 뒤 기능**·입력 잠금·예약 회차의 기능·작은 화면·경로 두 칸 맞추기·실행 중 홈 고정·예약 전 경고·이번만 건너뛰기·휴대폰 점검·[멈춘 곳부터 다시]·메일 기능의 입력 필요(저장 폴더·인증 문자·사이트 연결·무선 주소)·무인 예약이 시작을 못 하면 기록 ·처리 안 된 예외 알림(10-07) (93). `--shot` 창 찍기 (알림 창 둘 포함) |
| `probe_webmail.py` | 메일 사이트 틀 — 사이트 폴더가 **진짜로 없을 때**·필수 설정·받기 실패 다시 열기(다시 받은 엑셀만 소비)·`[ ]` 제목·가짜 사이트로 수집 (17). 사이트 전용(페이지 넘김 등)은 git 밖 `tools/local/` |
| `probe_userlog.py` | **사용자가 보는 글** — 10장면을 가짜로 돌려 내부 값이 새는지·건수·상품명·확인할 것·시험 실행 되돌리기·상세 페이지·브라우저 없음(10-07) (118). `--write` 로 검토본 |
| `probe_hooks.py` | 훅이 막을 것을 막는지 — 조작 실행·경로·비밀 키·서버 쓰기·실서버 SQL(APPROVED-SQL)·서브에이전트·PowerShell·규칙 경고(`wait_for_timeout`)·GitHub 에 올리기(`git push`·`gh repo create`·`--no-verify`·`npx wrangler@4 deploy`)·서브에이전트의 `.env`·구운 설정 읽기(대소문자 섞어도)·git/gh 다른 꼴(`git.exe`·`-C`·`bash -c`·커밋 검사 끄기·훅 자리 바꾸기·`gh gist/release/api` 쓰기)·서브에이전트의 셸 재귀 검색·비밀 파일 역슬래시 경로 (134) |
| `probe_leaks.py` | **git 에 실값이 없나** (10-02) — 낱말 목록 없이 `settings.local.json`·`.env`·git 밖 사이트/업체 파일·이 PC 이름에서 값을 뽑고, 메일·전화·사용자 경로·내부 IP 모양도 본다. `파일:줄 — 어디서 온 값` 만 찍는다. 인자 없이 = 추적·새 파일의 지금 내용 / `--staged`(`.githooks/pre-commit`) / `--pre-push`(`pre-push`, 올리는 커밋만) / `--history`(손으로, 모든 ref) / `--selftest` (44). 감싼 비밀번호(dpapi:·baked:)는 풀어서 평문도 찾는다. 설정·.env 를 못 읽으면 커밋·push 를 막는다 |
| `probe_history.py` | 지난 실행 이력·[실행 기록] 탭·건너뛴 예약(기록 파일·이어지면 한 줄) (19) |
| `probe_build.py` | 빌드 프로그램·기능 고정 빌드·빌드 등록(가짜 서버)·고정값 잠금 `LOCKED_KEYS`·구울 값 기능별 목록(`modules.baked_required`) (35) |
| `probe_telemetry.py` | 서버 보고 — 가짜 서버로 이벤트·outbox·401 버림·끊김·심박·비밀 값 없음·서버 확인(바인딩·잠금)·알림(따로·outbox 없이·서버 종류와 같게)·서버 인증서 오류(구분 문구·루트 채우기 1회, 10-07) (67) |
| `probe_web.py` | 웹 정적 검사 — innerHTML·SRI·CSP·비밀·제어 단추·탭·사용량·실행 중 설정 잠금·매출처리 칸·실행 상세 자동 갱신·설정 칸 기본값 없음·매출처리·택배사·박스 요구·node 구문 (63) — git 의 web/ 에는 주소·키 없이 자리표시만·build_web |
| `probe_web_shot.py` | 웹 화면 찍기 — Edge 로 가짜 데이터를 넣어 세 폭으로 찍고 콘솔 오류·'null'·남의 업체·가로 스크롤 검사 (15) |
| `probe_remote.py` | 원격 설정·명령·자동 켜기 — poll 주고받기·실행 중 미루기·명령 넷·설정 바로 저장·서버 판에 빠진 키 채우기·웹 매출처리 반영·설정 파일(BOM 읽기·임시 파일 교체 저장, 10-07) (56) |
| `probe_llm.py` | 사용법 질문 워커 — 가짜 LM Studio·가짜 서버, 설명서에 내부 값 없음, 끊김·재시도·워커 키·빌드에서 뺌 (42) |
| `probe_startup.py` | 켜기 비용 — node.exe 리소스 꺼내기·작업 스케줄러 넘기기·`main_run.py` 순서·한 벌 판정(권한이 다른 벌)·처리 안 된 예외 기록(10-07) (35). `--exe [--browser]` 빌드본 |
| `probe_guard.py` | **확인 도구가 실서버에 닿지 않게** — `offline()` 이 서버 설정 키를 비운다 (실행 창은 뜨기만 해도 서버 확인을 보내 그 PC 에 묶인다, 09-23 사고) |
| `probe_gui.py` | 우리 창이 만들어지는지·창 크기·단계 표 |
| `probe_overlay.py` / `probe_shot.py` | 오버레이 확인 (81) / **PNG 로 찍기** (`--stages` 실행용 창 모양, 끝난 결과 띠 포함) |
| **조사 (읽기 전용)** | |
| `probe_screen.py` | 화면 조사 주력. `--all` 은 그리드 셀 값까지 |
| `probe_grid_raw.py` | 그리드 **원시 UIA 트리** + LegacyIAccessible 값 + Grid/Table/Scroll 패턴 (09-29 — 좌측 숫자가 UIA 에 있는지 본 도구. 없었다). 행의 Legacy 값에는 모든 컬럼이 `;` 로 이어져 있다 |
| `survey_windows.py` / `dump_controls.py` | 최상위 창 / Control 트리 덤프 |
| `probe_phone_window.py` | [휴대폰 연결] 창 상태·탭·동기화 관찰 (`--dump` / `--min-test` / `--watch` / `--refresh` / `--ready-test`) |
| `probe_speed.py` / `probe_resources.py` / `probe_search_trace.py` | 속도 배율 / 자원 / 탐색 집계 (`test_flow --trace`) |
| **조작 (`APPROVED-RUN` 필요)** | |
| `test_flow.py` | 전체 흐름 콘솔 실행. `--from` `--yes` `--dry-run` `--trace` |
| `test_open_screen.py` | **물류 화면 열기만** (09-29, 저장 없음) — `--screen wait` [일반]→[조회] / `stock` 재고검토→[조회] / `logistics` [등록(I)]. `--count` 는 흐름과 같은 함수로 전표 수를 센다(스크롤만) |
| `test_collect.py` | 수집 RPA. `--check` / `--sms-only` / `--dry-run` |
| `test_full.py` | 통합 흐름 콘솔 실행. `--modules orders,logistics` 로 기능 선택. 끝나면 창과 같은 리포트(`logs/report_*.html`) |
| `test_adb_wireless.py` | adb 무선 페어링·연결. 성공하면 `adb_wireless_address` 저장 |
| `test_run_exe.py` | **빌드본으로 완주** — [실행] 은 사람이 누른다 |
| `test_slow.py` | **느린 환경 만들기** (CPU 부하). 배율은 `probe_speed` 로 잰다 |
| `test_autorun_task.py` | 로그온 자동 시작 등록 (`--show` 만 읽기). 미검증 항목(HANDOFF 2-2) |
| `bake_settings.py` | 지금 설정을 빌드용으로 굽는다 (파일만 쓴다). 씨앗(`baked.key`)도 같이 — `.env` 에 `BAKED_SEED` 가 없으면 멈춘다. `missing()` — 빈 필수 값 (빌드 프로그램이 서버 등록 **전에** 부른다) |
| `build_web.py` | 웹 배포 준비 — `.env` 값으로 `web/` 의 자리표시를 채워 `build/web/` 에 만든다 (`deploy_web.bat`, 10-02) |
| `register_build.py` | **빌드를 서버에 등록해 빌드 ID 를 받는다** (09-28). 빌드 프로그램이 [저장하고 빌드] 때 부른다 — 관리자 계정으로 로그인 → `register_build` RPC → `bld_...` |
| `build_run.py` | 실행용 빌드 한 번에 — 굽기 → 지난 산출물 삭제 → PyInstaller → 구운 파일 삭제. 빌드 프로그램이 쓴다. 콘솔로 바로 돌리면 서버 등록 없이 지금 빌드 ID 로 |

지운 도구(주석·옛 문서에 이름이 남아 있다): 09-18 에 29개 — `docs/archive/HANDOFF_20260928.md` "09-18 — 개발 환경 정리" /
09-29 에 `test_sales_menu.py` (선택주문 매출처리 결과 창 문구를 실기로 확인해 끝남) /
10-06 에 옛 판 정리 — 기능별 빌드(`build_collect/erpia/full`·`build_run.bat`)·진입점(`main.py`·`main_collect/erpia/full.py`)·런처(`gui/launcher.py`·`run.bat`·`run_admin.bat`)·기능별 창(`gui/collect_app.py`·`full_app.py`)·설정 샘플, 문서 `REQUIREMENTS.md`·`PROCESS.md`.

---

## 문서 지도

```
docs/
  HANDOFF.md          ★ 지금 상태 / 열린 항목 / 함정 / 새 세션 프롬프트 — 이것부터
  PROGRESS.md         이 문서. 단계 표 / 제약 / 흐름 / 코드 지도
  SETTINGS.md         config/settings.local.json 설명서 ← 값 고치기 전 필독
  CONTROLS.md         화면별 컨트롤 식별표 (확정본) ← 구현 전 필독
  UI_SURVEY.md        초기 UI 조사 (09-03). 로그인·팝업·권한 문제
  COLLECT_RPA.md      수집 RPA 확정사항·조사 결과
  ERROR_HANDLING.md   실패 시 동작 / 재시도 상한 / 재개 규칙
  BUILD.md            빌드
  RESOURCES.md        자원 사용량 / 탐색 계측 (09-08)
  BILLING.md          ★ 기능 선택(모듈화)·과금·트래픽 설계 — 확정 사항 (09-21, 과금 = 접속 사이트 수 09-30)
  SITES.md            다른 웹 사이트 추가 — Site/Task 계약·설정 경계·절차·체크리스트 (09-30). 스킬 site-add
  CUSTOMERS.md        업체 전용 RPA 구조 — 업체 파일 + 끼움 자리 + 빌드에 그 업체만 (10-02 기획, 프로세스 확정 뒤 만든다)
  WATCH.md            시간대 반복 조회 — 실행 한 번 안에서 N분마다 조회·저장 (10-06 기획, 만들기 전)
  SERVER_PLAN.md      서버 연동·웹 대시보드 — 지금 구조(D): RPA 와 서버의 역할·경계·원격·사용법 질문·
                      사용량 + D-10 규칙(ingest 순서·권한·RLS·웹·cron). 만들기 전 기획은 archive
  local/              [git 밖] 사이트 전용 조사 기록 (10-02 — 메일 사이트 코드는 collect/sites/local, 시험은 tools/local)
  customers/          [git 밖] 업체 전용 요구·미팅·결정 (10-02, 원칙은 CLAUDE.md). 그 업체 작업 때만 연다 — .ignore 로 Grep 제외
                      첫 업체 파일 하나 (회의 2회·시연 영상 요약, 실화면 확인 전. 원문은 요약 뒤 지움)
  archive/            끝난 기록. 자동으로 읽히지 않는다. 색인은 HANDOFF 6절
                      HANDOFF_20260929(09-28~09-29) / HANDOFF_20260928(09-17~09-28) / HANDOFF_20260917(09-15~09-17) /
                      TEST_20260904-0910 / PROGRESS_20260917  (코드 주석이 근거로 가리킨다. 옛 판·백업은 10-06 에 지웠다)
```

조사 결과는 **글로 정리해 `CONTROLS.md` 에 남긴다.** 스크린샷·덤프는 보관하지 않는다.

**도구는 기능이 끝나면 지운다** (사용자 확정 09-18, 09-07 규칙을 대체). 알아낸 것을
`CONTROLS.md` 로 옮기고 파일과 위 표의 줄을 같이 지운다. 회귀 확인용 `probe_*`(위 "확인" 묶음,
CLAUDE.md 의 "고친 곳 → 돌릴 것" 표)는 예외다. **삭제는 승인을 받고 한다** (git 으로 관리 — 10-02. 커밋 전 변경·무시 대상은 되살릴 수 없다).
실행하고 나온 것(로그, 스크린샷, 덤프)은 그대로 지운다. 새 도구는 위 표에 한 줄 남긴다.
