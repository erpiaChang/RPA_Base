# 웹 사이트 추가 (Site / Task)

웹 사이트(로그인 → 메뉴 → 조회 → 엑셀 받기 / 입력)를 붙이는 규칙과 절차. **원본(git)에는 사이트가 하나도 없다** — 사이트 파일·그 시험·조사 기록은 git 밖에 둔다 (1절 4행).
**새 사이트 요청을 받으면 이 문서와 스킬 `site-add` 만 보고 시작한다** — 기존 흐름 전체를 다시 읽지 않는다.
설계 검토 09-30, 결정은 사용자 확정 09-30. 지금 만들어진 계약은 메일 사이트용 `MailSite` 하나다 (`collect/webmail.py`, 2절). 아래 Site/Task 계약과 5절 "첫 사이트 때 한 번" 은 아직 만들지 않았다.

## 1. 확정 사항 (사용자 09-30)

| # | 항목 | 결정 |
| --- | --- | --- |
| 1 | 과금 | **접속한 사이트 수.** 사이트A + 사이트B = 2, 사이트B만 = 1. 메일 사이트는 메일 수와 무관하게 1. 처리한 것이 0건이면 세지 않는다 (`docs/BILLING.md` 1절, `server/schema.sql` 9절 — 메일 사이트 쪽은 09-30 적용함) |
| 2 | 실패 | **한 곳이 실패해도 나머지는 계속.** 메일 단계 실패도 뒤 기능으로 넘어간다 (`full_flow._full`, 09-30 적용함). 메일만 골랐거나 [중단] 이면 멈춘다 |
| 3 | ERPia 사이트명 | 사이트 그리드 이름은 **사이트 파일의 상수** (업체마다 같다). 다른 업체가 생기면 사용자가 알려 준다 → 그때 설정으로 뺀다 |
| 4 | 사이트 전용 코드 (사용자 10-02) | 사이트 파일(`collect/sites/local/`)·그 시험(`tools/local/`)·조사 기록(`docs/local/`)은 **git 밖** (`.gitignore`). 원본(git)에는 사이트가 하나도 없고, 이름·주소·화면 요소·계정을 git 에 쓰지 않는다 (자리표시 `사이트A`) |
| — | 모듈 | 사이트는 **모듈이 아니다.** 새 모듈은 `sites`("사이트 엑셀 받기") 하나만, 메일이 아닌 사이트를 처음 붙일 때 만든다. 모듈을 늘리면 서버·웹을 고쳐야 한다 (`server/schema.sql` run_modules 허용값·`send_command`·`usage_daily` 칸·`refresh_usage`, `web/app.js` `MODULES`·`USAGE_COLS`) |

## 2. 구조

```
collect/
  browser.py        공용 (git) — browser_page()·page_text()·eval_json(). 시크릿(InPrivate) 창. 메일 사이트와 Site/Task 가 같이 쓴다
  sites/
    __init__.py     (git) available()·mail_site() — local/ 을 불러온다. 사이트가 없으면 SiteMissing, 둘 이상이면 MailError
    base.py         (아직 없음 — Site/Task 때) Site · Task · TaskResult · NeedsAttention (값과 예외만)
    runner.py       (아직 없음 — 같은 때) 순회·결과 분류·실패 스크린샷+페이지 글·사용자 로그·manifest 기록 (공통은 여기 한 곳)
    local/          **git 밖** — 사이트 전용 코드는 전부 여기 (1절 4행)
      __init__.py   SITES = (site_a.SITE, …) 등록표. **리터럴 import 만** (from . import site_a) — 문자열 import 는 PyInstaller 가 놓친다
      <site_id>.py  사이트 하나 = 파일 하나. 메일 사이트: SEL_* 상수 + SITE = MailSite(...) + 그 함수들. Site/Task: SEL_* 상수 + ERPIA_SITE + login() + Task 함수
  webmail.py        메일 사이트 공용 틀 (git) — `MailSite` 계약·login()·collect()·재시도 상한·예외. 사이트 파일은 화면 조작만
orchestrator/steps_sites.py   (아직 없음 — Site/Task 때) 단계 1개 (id `sites`). 사이트마다 단계를 만들지 않는다
tools/probe_webmail.py        (git) 메일 사이트 틀 확인 — 가짜 사이트·가짜 Page
tools/local/                  **git 밖** — 그 사이트의 시험 (가짜 Page)
docs/local/<site_id>.md       **git 밖** — 그 사이트의 조사 기록 (화면별 URL·요소·팝업·방해 요소)
```

`collect/` 안에 둔다 — 빌드 금지 모듈 검사(`build_run.spec` `_OURS`)와 `confirm_run` 의 `-c` 실행 검사가 `collect` 를 본다.

사이트 전용 코드는 git 밖이다 (1절 4행).

- **빌드** — `collect/sites/local/` 에 사이트 파일이 있으면 같이 묶인다. `collect/sites/__init__.py` 의 `from collect.sites import local` 과 `local/__init__.py` 의 리터럴 import 를 PyInstaller 가 따라간다 (`build_run.spec` 에 따로 적지 않는다). 없으면 사이트 없이 빌드되고(`tools/bake_settings` 가 알린다) 그 빌드의 **메일 기능은 '메일 사이트 연결' 입력 필요로 잠긴다** ([실행] 불가).
- **사이트가 둘 이상이면** `mail_site()` 가 `MailError` 로 멈춘다 — 고르는 설정이 아직 없다 (9절).
- **새는 것 막기** — `tools/probe_leaks` 가 `collect/sites/local` 의 `MailSite` id·이름·주소와 `docs/customers` 파일 이름을 낱말로 삼아, 추적 파일에 있으면 커밋 전(`.githooks/pre-commit`)·push 전(`pre-push`)에 막는다.

같은 사이트에 메일 말고 새 업무(Site/Task)가 생기면 그 `Site.login` 이 `webmail.login(page, SITE)` 를 쓴다 — 로그인 코드를 복사하지 않는다.

### 메일 사이트 — `MailSite` (만들어져 있다, `collect/webmail.py`)

사이트 파일 하나가 `SITE = MailSite(id, name, login_once, list_rows_until, open_mail, attachment_buttons, back_to_list)` 를 내놓는다. 사이트 파일은 **화면 조작만** 한다 — 로그인+2차 인증 한 번(거부는 `LoginError`, 인증 문자가 안 오면 `auth_code.CodeNotArrived`) · 목록 읽기(최신순, 기간 안쪽이 끝날 때까지) · 메일 한 번 열기 · 보기 화면의 첨부 단추 · 목록으로 돌아가기. 대상 고르기·재시도·받기·엑셀 판정·매니페스트는 틀(`webmail.login`·`webmail.collect`)이 한다.

| 틀의 상수 | 값 | 뜻 |
| --- | --- | --- |
| `AUTH_ATTEMPTS` · `AUTH_RETRY_DELAY` | 2 · 60초 | 로그인·인증 거부 재시도 (분이 바뀌도록 기다린다) |
| `CODE_RETRIES` | 2 | 인증번호가 안 올 때 새로 요청 |
| `OPEN_ATTEMPTS` | 2 | 메일 열기 |
| `MAX_PAGES` | 20 | 목록 페이지 상한 — 사이트 파일이 쓴다 |

예외: `LoginError` · `MailError` · `NoExcelAttachment`(엑셀이 아님 — 실패가 아니라 skipped) · `SiteMissing`(사이트 파일 없음).

### Site/Task 계약 — 메일이 아닌 사이트, 아직 만들지 않았다 (값 — Module·Step 과 같은 frozen dataclass)

```python
@dataclass(frozen=True)
class Task:
    id: str              # "site_a.orders" — 설정 site_tasks·결과 표·로그
    name: str            # "몰A 주문 엑셀" (사람이 보는 이름)
    run: Callable        # (page, ctx) -> TaskResult
    writes: bool = False # 사이트에 저장·전송하면 True → dry-run 에서 돌지 않는다

@dataclass(frozen=True)
class Site:
    id: str; name: str
    login: Callable      # (page, ctx) -> None. 실패는 예외 (NeedsAttention 또는 일반)
    tasks: tuple[Task, ...]
```

- Site 하나에 로그인 한 번, 그 사이트의 Task 들을 이어서 돈다. 사이트마다 **새 InPrivate 브라우저** (쿠키가 섞이지 않는다).
- **사람에게 보이는 글은 runner 가 만든다.** 사이트 코드는 건수·파일·사유 코드만 돌려준다 (`probe_userlog` 를 사이트마다 늘리지 않으려고).
- 받은 파일은 기존 manifest 에 넣는다: `Item.site = ERPIA_SITE`, `mail_key` = 이 파일의 고유 키(`task.id` + 날짜 + 파일명), `mail_subject` = Task 이름. ERPia 업로드(`order_mapping.upload_excels`)는 `(사이트명, 경로)` 만 받으므로 그대로 올라간다. 두 번 올리지 않는 것은 `consumed_at` 이 막는다.

### Task 결과 상태 (Task 단위. 단계 상태 6개와 섞지 않는다)

| 값 | 뜻 | 다음 |
| --- | --- | --- |
| `done` | 성공 | |
| `skipped` | 할 일 없음 (0건) | 정상. 과금 안 셈 |
| `failed` | 실행 안에서 상한까지 재시도한 뒤 실패 (시간 초과·화면 바뀜·불량 파일) | 다음 예약 때 다시 (곧바로 재시도 안 함 — 09-16 규칙) |
| `attention` | 사람이 해야 함 (CAPTCHA·잠금·비밀번호 틀림·인증 거부) | 재시도 안 함. `hooks.attend` |

단계 `sites` 는 일부가 실패해도 `done` 이고 표 한 줄 = Task 하나(사이트·작업·결과·건수·사유). `hooks.count(ok=처리한 것이 있는 사이트 수)` — 서버가 이 값으로 센다.

## 3. 설정과 코드의 경계

| 설정 (`settings.local.json`) — PC·계정마다 다른 값만 | 사이트 파일 (상수·코드) |
| --- | --- |
| `site_tasks` (돌릴 Task id 목록. 순서는 등록표 순서) | URL, selector, 메뉴명, 로그인 성공 판정, 파일 패턴, timeout, 재시도 상한 |
| `site_user_ids` `{site_id: 아이디}` | `ERPIA_SITE` (ERPia 사이트 그리드 이름) |
| `site_passwords` `{site_id: "dpapi:.."}` — `SECRET_MAP_KEYS` 에 넣는다 (**1단계 dict 만** 감싸진다, `config/settings.py` `_wrap_value`) | 절차: 특수 로그인·다단계 메뉴·팝업·iframe·동적 페이지·특수 다운로드 |

selector 를 설정에 두지 않는 이유: 사이트가 바뀌면 개발자가 고쳐 다시 빌드하는 값이다. 설정에 두면 구운 값과 PC 의 값이 갈려 고친 것이 퍼지지 않는다.
등록표에 없는 `site_tasks` id 는 실행 전에 거절하고 화면에 알린다 (예약 줄 형식 오류와 같은 처리).

메일 사이트(`MailSite`)는 먼저 만들어져 설정이 다르다 — 로그인 주소·계정이 설정(`mail_url`·`mail_user_id`·`mail_password`)이고, 비면 `webmail.login` 이 멈춘다. 사이트 파일은 그 값을 읽고 selector·메뉴·화면 판정만 가진다.

## 4. 자동화 기술과 방해 요소

- 기본은 Playwright 하나. locator 순서: role/label/text → 안정적인 id·속성 CSS → 키보드. 좌표·이미지 금지.
- 조작마다 **화면 확인 → 조작 → 결과 확인.** 확인이 틀리면 이름 붙은 사유로 멈춘다 (`화면이 바뀜: 주문조회 버튼`). 실행 중에 다른 기술로 넘어가지 않는다 — 기술은 조사 때 정해 문서에 적는다.
- 브라우저 밖 창(파일 저장·인증서·Windows 보안)은 기존 UIA 부품(`utils/filedialog.py`, `utils/ui.py`)을 사이트 코드가 **명시적으로** 부른다. OCR·CV·CDP 직접 사용은 실례가 생기면 조사 뒤에.
- 고정 대기 금지 — `page.wait_for_timeout` 도 `time.sleep` 과 같다 (`check_rpa_rules` 가 경고, 09-30).

| 상황 | 처리 |
| --- | --- |
| CAPTCHA · 보안 키패드 · 키보드 보안 프로그램 · 공동인증서 | **조사 단계에서** 발견하면 자동화할지 사용자가 정한다. 풀려고 하지 않는다 |
| 문자 인증 | `collect/auth_code.prepare()` → 요청 → `wait(since)` 그대로. 발신번호·키워드는 지금 전역(`sms_senders`·`sms_keyword`) |
| 비밀번호 틀림 | `attention`, 재시도 0회 (잠금 방지) |
| 계정 잠금 · 봇 차단 안내 · 약관상 자동화 금지 안내 | `attention` |
| 속도 제한 ("잠시 후 다시") | `failed`, 곧바로 재시도 안 함 |
| 무인 예약 중 사람이 필요 | 기다리지 않는다 — `attention` + 확인할 것 + 서버 보고 |

## 5. 첫 사이트 때 한 번 만들 것 (그 뒤로는 안 고친다)

메일이 아닌 사이트(Site/Task)를 처음 붙일 때다. 메일 사이트의 틀(`collect/webmail.py`·`collect/sites/__init__.py`)과 `collect/browser.py` 는 이미 있다.

| 어디 | 무엇 |
| --- | --- |
| `collect/browser.py` | 이미 있다 (10-02) — `browser_page()`·`page_text()`·`eval_json()`. 옮길 것 없이 Site/Task 가 같이 쓴다 |
| `collect/sites/` | `base`·`runner` (git) + 첫 사이트 파일 (**git 밖** `local/`). `local/__init__.py` 의 `SITES` 는 지금 메일 사이트용이라(`mail_site()` 가 그렇게 읽는다) Site/Task 등록표는 **따로 둔다** — 이름은 그때 정한다 |
| `orchestrator/modules.py` · `steps_sites.py` · `full_flow.py` | `SITES = Module("sites", "사이트 엑셀 받기", erpia=False)` — 순서 메일 다음·주문수집 앞. `plan()` 분기. `_full` 의 메일 except 와 같은 규칙(실패해도 뒤 기능으로) |
| `config/fields_collect.py` · `config/settings.py` | `site_tasks`·`site_user_ids`·`site_passwords` + `SECRET_MAP_KEYS` |
| `gui/run_app.py` · `gui/build_app.py` | [설정] 사이트 묶음(등록표를 돌며 아이디·비밀번호 칸) / `SECTIONS` 에 dict 칸 |
| `collect/pw_driver.py` `needed()` | `"mail"` 과 `"sites"` 둘 다 Playwright 가 필요하다 |
| `server/schema.sql` | run_modules 허용값·`send_command` 검증에 `sites`, `usage_daily.sites` 칸 + `actions`, `refresh_usage` 는 `sites` 단계의 `ok_items` 합 — **SQL 승인 필요** |
| `web/app.js` | `MODULES`·`USAGE_COLS`·세는 법 안내 — 배포 승인 |
| 문서 | `BILLING.md` 2절 표, `SETTINGS.md`, `llm/guide.md`(사용자 설명서), `PROGRESS.md` 코드 지도, CLAUDE.md 확인 도구 표 (사이트 이름·주소는 적지 않는다 — 자리표시). 그 사이트의 조사 기록은 **git 밖** `docs/local/<id>.md` |
| 도구 | `probe_sites` (공통, 7절, git), 첫 사이트의 `probe_site_<id>`·`test_site_<id>` (**git 밖** `tools/local/`). `probe_failure` 상한 검사에 runner 상수 |

## 6. 새 사이트 추가 절차 (게이트 = 사용자 결정)

| # | 단계 | 누가 | 남길 것 |
| --- | --- | --- | --- |
| 0 | 접수 — 8절 체크리스트 | 사용자 | |
| 1 | 로그인·업무 흐름 기록. 사람이 브라우저를 움직이고 Playwright codegen(`python -m playwright codegen --channel msedge <URL>`)으로 locator 를 받는다 (**이 PC 에서 아직 안 돌려 봄**) | 사람 + Claude | |
| 2 | 방해 요소 판정 → 가능 / 조건부 / 불가 | Claude → **사용자 결정** | |
| 3 | 화면별: URL·진입 확인 요소·조작·결과 확인·팝업·iframe | Claude | **git 밖** `docs/local/<id>.md` |
| 4 | Task 설계: 읽기/쓰기, 중복 방지 기준, 0건일 때 | Claude → 사용자 확인 | |
| 5 | 코드: 가장 최근 사이트 파일을 복사해 교체, 등록표 한 줄 | Claude | **git 밖** `collect/sites/local/<id>.py` + `local/__init__.py` 의 `SITES` |
| 6 | 오프라인: `tools/local/probe_site_<id>` (**git 밖**) + 틀 `probe_webmail`(메일 사이트)·`probe_sites`(Site/Task)·`probe_userlog`·`probe_failure` | 메인(사이트 시험) · rpa-verifier(틀) | |
| 7 | 실사이트 점검 `tools/local/test_site_<id> --check` (**git 밖**, 로그인·화면 도달·요소만) | **승인** (`APPROVED-RUN`) | |
| 8 | 실행 (쓰기 Task 는 dry-run 먼저) | **승인** | |
| 9 | 문서: SETTINGS · PROGRESS 코드 지도 · CLAUDE 표 · HANDOFF — **사이트 이름·주소는 적지 않는다** (자리표시) | Claude | |

- 실패 상황은 실사이트에서 만들지 않는다 (틀린 비밀번호 → 잠금). 가짜 페이지로 본다.
- 조사 없이 사이트 코드를 쓰지 않는다 (CLAUDE.md 규칙 그대로).
- **사이트 이름·주소·계정·화면 요소는 git 에 쓰지 않는다** — 코드·문서·시험 예시·커밋 메시지 모두 자리표시(`사이트A`·`user01@example.com`)로. 실값은 git 밖 `collect/sites/local/`·`tools/local/`·`docs/local/` 에만 둔다.
- `tools/local/` 의 시험은 rpa-verifier 가 못 돌린다 (`.claude/hooks/confirm_run.py` 가 `tools.probe_*` 만 통과시킨다) — 메인이 돌린다.

## 7. 시험

| 층 | 도구 | 보는 것 |
| --- | --- | --- |
| 메일 사이트 틀 (만들어져 있다) | `probe_webmail` — 가짜 사이트·가짜 Page (15) | 등록표가 비면 `SiteMissing`, 필수 설정(기간·읽지 않음)이 비면 멈춤, 받기 실패한 메일 다시 열기, 열기 재시도, 엑셀이 아니면 skipped, 시험 실행은 메일을 열지 않음 |
| Site/Task 공통 (한 번, 아직 안 만듦) | `probe_sites` — 가짜 사이트 2개 | 순서, 한 곳 실패해도 계속, 상태 4개, 확인할 것, 사용자 글에 내부 값 없음, manifest, dry-run 에서 쓰기 Task 안 돎, 취소 |
| 사이트마다 (**git 밖**) | `tools/local/probe_site_<id>` — `tools/local/probe_mail_pages.py` 의 가짜 Page 방식 | 그 사이트 코드의 분기 (정상·로그인 실패·방해 문구·0건·불량 파일·시간 초과) |
| 실사이트 (**git 밖**) | `tools/local/test_site_<id> --check` (승인) | selector 가 아직 맞는가 — 화면 변경 감지. 가짜 Page 는 이것을 못 본다 |

## 8. 접수 체크리스트 (사용자에게 받을 것 · 조사로 채울 것)

- URL, 테스트 계정(실계정이면 그렇다고), 업무: 무엇을 받나/넣나, 받은 파일을 ERPia 어느 사이트 행에 올리나
- 로그인 방식: 아이디·비밀번호 / 문자 인증 / OTP 앱 / 공동인증서 / 간편 로그인
- 방해 요소: CAPTCHA, 보안 키패드, 설치 요구 프로그램, 동시 로그인 제한, 로그인 실패 몇 번에 잠기나
- 중복: 같은 주문을 다시 받는가 (기간 조건, 사이트의 '다운로드 완료' 표시, 다시 받으면 ERPia 에서 중복되나)
- 되돌릴 수 없는 단계 (쓰기 Task 인가), 0건일 때 화면, 계정마다 화면이 다른가
- 무인 예약으로 돌리나
- 받은 값(URL·계정·사이트 이름)은 **git 밖** `docs/local/<id>.md` 에만 적는다 — git 문서·커밋 메시지에는 자리표시(`사이트A`)로

## 9. 사이트가 3~5개를 넘으면

- 가짜 Page 공용 부품 (`tools/local/probe_site_*` 에 중복이 보이면)
- 문자 인증 사이트가 둘 이상 → `auth_code.wait` 에 사이트별 발신번호·키워드 인자
- 메일 사이트가 둘 이상 → 지금은 `mail_site()` 가 `MailError` 로 멈춘다. 고르는 설정과 `mail_*` 키의 사이트별 값을 그때 만든다
- 예약 줄·웹에서 Task 고르기 (`orchestrator/remote.py` `REMOTE_KEYS` 에 `site_tasks`, 등록표로 검증)
- 실행 전 화면 점검(`--check`) 자동화
- 입력형 Task(예: 송장 등록)는 흐름상 물류관리 뒤 → 모듈 하나 더 (그때 한 번)
- 사람이 있는 실행에서 CAPTCHA 를 화면에 알리고 몇 분 기다리기
- 실패 때 Playwright trace — 주문자 정보가 담기므로 보관 규칙을 먼저 정한다

## 10. 만들지 않는 것

selector 를 설정 파일로 빼는 범용 설정 · 문자열 동적 import 플러그인 · 자동 fallback 체인 · CAPTCHA 풀기 ·
OCR·CV·CDP 직접 사용(실례 전) · scaffold 생성기·템플릿 파일(실행되지 않는 파일은 낡는다) · 새 서브에이전트·플러그인 ·
사이트마다 모듈·단계 · 전체 중단 설정 · 로그인 세션 저장 · 2단계 중첩 비밀값
