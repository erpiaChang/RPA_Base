# RPA (Windows 데스크톱 자동화)

설정 설명서: @docs/SETTINGS.md
단계 표·제약·코드 지도·확인 도구 목록: @docs/PROGRESS.md

아래 문서는 **자동으로 읽지 않는다** (컨텍스트를 아끼려고). 필요할 때 연다.
`docs/HANDOFF.md`(지금 상태·열린 항목·함정) / `docs/CONTROLS.md`(컨트롤 식별표) /
`docs/UI_SURVEY.md`(초기 조사·좌표로 내려간 미해결) / `docs/BUILD.md`(빌드) / `docs/archive/`(끝난 기록) /
`docs/SITES.md`(**다른 웹 사이트 추가** — 규칙·계약·절차. 스킬 `site-add` 가 연다, 09-30) /
`docs/CUSTOMERS.md`(**업체 전용 RPA 구조** 기획, 10-02) / `docs/WATCH.md`(**시간대 반복 조회** 기획, 10-06) /
`docs/customers/<업체>.md`(**업체 전용** 요구·미팅·결정, 10-02 — **그 업체 작업 때만 연다. 다른 작업에서는 열지도, 근거로 쓰지도 않는다.**
`.ignore` 로 Grep 에서 빠지니 경로로 직접 연다).
규칙은 이 파일이 최신이다 (사용자 확정 09-29). 출발점 `REQUIREMENTS.md`·`PROCESS.md` 는 10-06 에 지웠다 — 남은 규칙은 이 파일과 `CONTROLS.md` 로 옮겼다.

`docs/later/` 는 **ERPia 작업에서는 열지도, 근거로 쓰지도 않는다** — 다른 프로그램을
붙일 때 쓸 공용화 조사다(2026-09-17). 두 번째 프로그램 작업을 시작할 때만 연다.

## 원본 RPA 와 업체 전용 (사용자 확정 2026-10-02)

원본 RPA 하나를 두고 업체마다 성격을 달리한다. **업체 요구를 원본에 무조건 넣지 않는다** —
공용으로 들어갈 만한 것만 원본에, 나머지는 그 업체 전용으로 둔다. 무엇이 공용인지는 **사용자가 정한다.**
**여러 업체가 같은 규칙을 원해도 원본 설정으로 올리지 않는다** (10-02).
업체별 요구·결정은 `docs/customers/<업체>.md` (첫 업체의 파일이 이미 있다 — 이름은 그 폴더에서 본다. 폴더는 git 밖).
구조는 `docs/CUSTOMERS.md` (업체 파일 `customers/<업체>.py` + 원본의 끼움 자리, 빌드에 그 업체만) — **기획만, 정확한 프로세스가 나오면 만든다.**

## 현재 상태

**작업을 시작하면 `docs/HANDOFF.md` 부터 읽는다** — 지금 상태·열린 항목·함정·새 세션 프롬프트가 거기 있다.
실행용 RPA(흐름: 메일 → 주문수집·매출처리 → 물류대기 → 물류관리)와 서버 연동(스키마·보고·웹·원격 제어·사용법 질문·사용량)이 다 붙었고, 빌드본으로 저장까지 완주했다.
**`dist/` 의 exe 는 시험 빌드다** — 실사용 exe 는 다음 빌드에서 만든다.

**코드를 고쳤으면 확인 도구부터 돌린다** — 대상 프로그램을 건드리지 않는다. 각 도구가 무엇을 보는지는
`docs/PROGRESS.md` 도구 표. 모듈 이름은 **적어서** 돌린다 (`python -m tools.$m` 처럼 변수로 쓰면 훅이 막는다).

| 고친 곳 | 돌릴 것 (건수) |
| --- | --- |
| 흐름·단계·결과 문구 (`orchestrator/`) | `probe_progress` 47 · `probe_failure` 61 · `probe_pipeline` 41 · `probe_report` 12 · `probe_modules` 93 (실행 창 흐름·기능 선택·메일 실패 뒤 계속) |
| **사용자에게 보이는 글** (어디든) | `probe_userlog` 119 — **필수** |
| ERPia 조작 (`automation/`, `utils/ui.py`) | `probe_hold_loop` 53 · `probe_rect_settle` 27 · `probe_failure` 61 |
| 창·화면 (`gui/`) | `probe_gui`(실패 0) · `probe_modules` 93 · `probe_overlay` 81(화면이 잠겨 있으면 73) · `probe_history` 19 — **찍어서 본다** (`probe_shot --stages`, `probe_modules --shot`) |
| 색·글자 크기 | `probe_contrast` 60 — **필수** |
| 예약·자동 실행 | `probe_schedule` 97 · `probe_autorun` 59 |
| 수집 (`collect/`) · 비밀번호 감싸기 | `probe_phone_wait` 57 · `probe_webmail` 17 · `probe_browser` 23 · `probe_secret` 37 (사이트 전용은 git 밖 `tools.local.*`) |
| ERPia 업데이트·UAC | `probe_updater` 18 |
| 빌드 프로그램·고정값 (`gui/build_app.py`, `LOCKED_KEYS`) · `.bat` | `probe_build` 54 |
| 서버 보고 (`orchestrator/telemetry.py`) | `probe_telemetry` 67 — **필수** |
| 원격 설정·명령 (`orchestrator/remote.py`, `run_app` 원격 부분) · 설정 파일 읽기·저장 | `probe_remote` 56 — **필수** |
| 웹 (`web/`) | `probe_web` 63 · `probe_web_shot` 15 (찍어서 본다) — **필수** |
| LLM 워커 (`llm/`) | `probe_llm` 42 — **필수** (속도·답은 `python llm\worker.py --bench`) |
| 켜기 (`utils/autostart.py`·`utils/instance.py`·`utils/crashlog.py`·`utils/selfcheck.py`·`main_run.py`·`collect/pw_driver.py`·`build_run.spec`) | `probe_startup` 39 — **필수** (빌드 뒤 `--exe --browser`) |
| 훅 (`.claude/hooks`) | `probe_hooks` 134 — **필수** |
| 커밋·push (git 에 실값이 없나) | `probe_leaks --selftest` 44 · 인자 없이 걸린 줄 0 — 커밋 전·push 전 훅이 `--staged`·`--pre-push` 로 돈다 |

**조사 결과가 없는 화면은 여전히 자동화 코드를 작성하지 않는다.**
확정된 컨트롤 식별값은 `docs/CONTROLS.md`(주력) 와 `docs/UI_SURVEY.md`(초기 조사)에 있다.
모르면 추측하지 말고 조사 단계부터 진행한다. **계정마다 화면 구성이 다르다**
(프로세스바 항목, 자동/수동 기억값, 출력 버튼) — 있다고 가정하지 않는다.

## Env

- Windows / Python 공식 배포판 3.14 / venv (`.venv`)
- Anaconda 사용 안 함
- 패키지 추가 시 `requirements.txt` 갱신

## 실행

| 목적 | 명령 |
|---|---|
| 실행용 창 (개발 폴더에서) | `.venv\Scripts\python.exe main_run.py` — 뜨기만 해도 서버 확인이 나간다 (확인 도구는 `probe_guard.offline()`) |
| **빌드** — 유일한 빌드 (설정을 화면에서 고쳐서 굽는다, 기능 고정 선택) | `build_tool.bat` → `dist/run/RPA_1.exe` (`gui/build_app.py`, `docs/BUILD.md`). 기능별 빌드·런처는 10-06 에 지웠다 |
| 콘솔 빌드 (설정 그대로, 서버 등록 없이 지금 빌드 ID 로) | `.venv\Scripts\python.exe -m tools.build_run` → `dist/run/RPA_1.exe` |
| 굽는 설정 만들기 (읽기 전용) | `.venv/Scripts/python.exe -m tools.bake_settings` |
| 창 목록 조사 (읽기 전용) | `survey.bat` (또는 `.venv\Scripts\python.exe -m tools.survey_windows --save`) |
| 메일 단계 설정 점검 (개발, 읽기 전용) | `.venv\Scripts\python.exe -m tools.test_collect --check` |
| 메일 단계만 실행 (개발, 조작) | `.venv\Scripts\python.exe -m tools.test_collect` (먼저 `--dry-run`) |
| 단계·중단·구간 확인 (읽기 전용) | `.venv\Scripts\python.exe -m tools.probe_progress` |
| 창·단계표 확인 (읽기 전용) | `.venv\Scripts\python.exe -m tools.probe_gui` |
| 구간 실행 (조작) | `.venv\Scripts\python.exe -m tools.test_flow --from <단계> --yes` (먼저 `--dry-run`) |
| Control 트리 덤프 (읽기 전용) | `.venv\Scripts\python.exe -m tools.dump_controls --pid <PID> --name <화면명>` |

**빌드는 사용자가 요청할 때만 한다.** 코드를 고쳤다고 자동으로 다시 빌드하지 않는다.
`dist/` 는 마지막으로 요청받아 빌드한 시점의 산출물이며, 최신 코드와 다를 수 있다.

**반드시 `.venv` 인터프리터로 실행한다.** 시스템 python에는 `pywinauto`가 없어
`ModuleNotFoundError`가 나고, 더블클릭하면 창이 즉시 닫혀 "아무 일도 없는 것"처럼 보인다.
VS Code에서 실행할 때도 인터프리터가 `.venv`인지 확인한다.

`tools/`는 **개발 전용**이라 exe 에 들어가지 않는다. `automation/`은 제품 코드다.
`tools/probe_*`는 읽기 전용, `tools/test_*`는 실제로 조작한다. 섞지 않는다.

## 파일 접근 범위

아래 두 폴더 밖의 파일은 읽지도 쓰지도 않는다.

- `%USERPROFILE%\Desktop\RPA`
- `%USERPROFILE%\.claude`

`.claude/hooks/guard_paths.py`가 Read / Write / Edit / Glob / Grep / Bash에서 강제한다.
대상 프로그램 EXE나 Excel처럼 바깥 경로가 실제로 필요한 값은 코드에 쓰지 않고
`config/settings.local.json`에 두고 읽는다.
조사 목적으로 불가피하면 Bash 명령에 사유를 남긴다: `# ALLOW-OUTSIDE: <이유>`

## 실행 명령 — 두 가지 관례

**1. 조작 실행은 사유를 붙여야 통과한다.** `.claude/hooks/confirm_run.py` 가
`python -m ...` 형태를 막는다. 이름에 `probe` / `survey` / `dump` 가 든 읽기 전용
도구는 그냥 통과하고, 그 밖의 것은 명령 끝에 사유를 붙여야 통과한다.

```
.venv\Scripts\python.exe -m tools.test_flow --yes   # APPROVED-RUN: 물류대기 구간 재현
```

**사용자 승인을 받은 뒤에만 붙인다.** 훅을 지나가려고 붙이는 것이 아니다.

**2. 긴 실행에는 `timeout` 을 넉넉히 준다.** Bash 기본 2분을 넘기면
**셸만 죽고 파이썬 자식이 살아남아 ERPia 를 계속 조작한다.** 화면에서는 명령이
끝난 것처럼 보이는데 실제로는 실계정 데이터가 계속 바뀌고 있는 상태가 된다.
전체 흐름은 3~5분 걸리므로 `timeout` 을 `540000` (9분)으로 준다.

## 서브에이전트 — 읽기는 맡기고, 쓰기는 메인이 한다

사용자 확정 2026-09-17. 정의는 `.claude/agents/`에 있다.

| 서브에이전트에 맡긴다 | 메인이 한다 |
| --- | --- |
| 여러 파일·문서에서 근거 찾기 → `rpa-scout` | 코드·문서 수정 |
| 코드를 고친 뒤 확인 도구 실행·요약 → `rpa-verifier` | `test_*` 실계정 조작, 빌드 |
| 수정 결과 독립 검토, 원인 가설 병렬 확인 (읽기 전용으로 지시) | 실제 화면 조사 (화면이 하나다), 작은 수정 |

- 서브에이전트는 이 대화를 모른다. 넘길 때 목적·범위·지켜야 할 사용자 지시를 적는다.
- 서브에이전트는 `APPROVED-RUN` / `ALLOW-OUTSIDE` 를 **쓸 수 없다** — 붙여도 훅이 무시한다
  (훅 입력의 `agent_id` 로 구별, 09-17). 훅에 막히면 멈추고 메인에 보고한다.
- `rpa-verifier` 의 Bash 는 **`python -m tools.probe_*` 실행만** 통과한다 (`ls`, `rm`, `>` 모두 막힘).
- 두 훅은 **PowerShell 도구에도** 걸린다 (09-17 전에는 빠져 있었다).
- 남은 한계: 문자열 검사라 **일부러** 피하면 못 막는다 (환경변수로 인터프리터 이름 감추기, `.bat` 감싸기).
- 훅(`guard_paths`, `confirm_run`)은 서브에이전트에도 걸린다 (09-17 확인).
- `confirm_run` 은 **실행 대상마다** 판정한다 — `probe && test` 처럼 이어 붙여도, 주석에
  `probe` 가 있어도 조작이면 막힌다. `python -c` 로 `automation`·`orchestrator`·`collect` 를
  불러도 막힌다. 읽기 확인은 `tools/probe_*` 로 만들어 쓴다.
- 서브에이전트는 비밀 파일 — `config/settings.local.json`·`config/settings.baked.json`·`baked.key`·`.env` — 을
  **읽을 수 없다** (훅이 막는다. 도구 입력에 그 이름이 들어가도, 대소문자를 섞어도). 프로젝트 전체나 `config/` 를
  Grep 할 때는 `type`/`glob` 으로 좁힌다.
- Serena MCP 는 **읽기·탐색만** 쓴다. 편집 도구는 `.claude/settings.json` 에서 거부했다
  (편집이 훅을 비켜 가기 때문, 09-17). 편집은 Edit/Write 로 한다.
- **서버 연동 도구** (09-22, `docs/SERVER_PLAN.md`): Supabase MCP 는 `.mcp.json` 의 `supabase-rw` 하나다 (09-28 사용자가 `supabase-ro` 를 빼고 넣음) — 읽기 도구는 그냥 쓰고,
  **SQL 은 직접 적용한다 (사용자 확정 09-28)** — 단, **적용 전에 SQL 을 보여 주고
  사용자의 결정을 받은 뒤** 본문 첫 줄에 `-- APPROVED-SQL: <사유>` 를 붙인다. `.claude/hooks/guard_sql.py` 가
  `execute_sql`·`apply_migration` 에서 이것을 강제한다(서브에이전트는 붙여도 막힘). 파괴적 도구(프로젝트 일시정지·브랜치·Edge 배포)는
  settings 에서 거부. 스키마 원본은 늘 `server/schema.sql` 이다 (스킬 `server-sql`). 플러그인 `claude-security` 는 `/claude-security` 로 ②·③ 코드를 스캔한다 — 리포트
  폴더(`CLAUDE-SECURITY-*`)는 반영 뒤 지운다. `supabase db push`·`psql`·`wrangler deploy`(`npx wrangler@4 deploy` 포함)·실서버 `curl`·`git push`·`git remote add/set-url`·`gh repo create`·`--no-verify` 는 `APPROVED-RUN` 이 필요하다.
  웹 배포는 `deploy_web.bat`(사용자) 또는 `.venv\Scripts\python.exe -m tools.build_web` 뒤 `cd web && npx wrangler@4 deploy` — 둘 다 `# APPROVED-RUN: ...`(Claude, 09-28 / 10-02 build_web 먼저).
  **비밀 키(service_role·`sb_secret_`·메일 API·기기 키)는 `config/settings.local.json` 에만** — `guard_secrets` 훅이 다른 파일 쓰기를 막는다.
- `rpa-scout` 은 Serena 기호 도구(`find_symbol` / `find_referencing_symbols` /
  `get_symbols_overview`)를 먼저 쓴다. ponytail 지침은 `rpa-*` 에는 주입되지 않는다.
- 실계정 실행 중에는 화면에 창을 띄우는 확인 도구(`probe_gui` / `probe_overlay` / `probe_shot`)를 맡기지 않는다.

## git (사용자 확정 2026-10-02)

| 무엇 | 규칙 |
| --- | --- |
| 언제 커밋 | 요청 하나를 끝내고 **확인 도구가 통과하면 바로** 커밋한다 (문서만 고쳤으면 그대로). 따로 묻지 않는다 |
| 무엇을 | **그 요청에서 고친 파일만** `git add <경로>` 로. `git add -A`·`git add .` 는 쓰지 않는다 — 다른 세션이 같은 폴더에서 고치는 중일 수 있다 |
| 시작할 때 | `git status` 로 남의 커밋 전 변경을 본다. 그 파일을 고쳐야 하면 그 위에서 고치고, 커밋은 각자 자기 몫만 |
| 지우는 명령 | `git reset --hard`·`git checkout -- <경로>`·`git restore`·`git clean`·`git stash` 처럼 커밋 전 변경을 지우는 것은 **사용자 승인 뒤에만** (남의 작업까지 지운다) |
| 메시지 | 한국어 한 줄 요약 + 필요하면 본문 |
| 빌드 | 빌드 전에 커밋하고, 그 해시를 `docs/HANDOFF.md` 의 exe 줄에 적는다 |
| 원격 (GitHub **공개**) | `origin` = `RPA_Base` (10-02 비공개로 첫 push, 같은 날 사용자가 공개로 바꿈 — **누구나 본다.** 한 번 올라간 실값은 기록을 고쳐도 남의 사본에 남는다). push 는 사용자 승인 뒤 `APPROVED-RUN` 으로. 작성자는 저장소 설정의 GitHub noreply 주소 — 전역 설정(기기 이름·회사 메일)으로 커밋하지 않는다 |
| **git 에 넣지 않는 값** (사용자 확정 10-02) | 실계정·개인정보·업체 정보·아이디·이메일·전화번호·내부 IP·기기 이름·Windows 사용자 경로·API 주소·키·씨앗·**이 PC·이 계정의 설정값**. 코드·문서·테스트 예시에도 쓰지 않는다 — 예시는 `user01`·`comp01`·`홍길동`·`사이트A`·`택배사A`·`박스A`·`상품A`·`user01@example.com`·`192.0.2.50`·`%USERPROFILE%` 같은 가짜로. **설정값은 기본값으로도 코드에 두지 않는다** (선택지 목록만) |
| 그 값은 어디에 | RPA 프로그램 값 → `config/settings.local.json` / 웹 주소·키·구운 값 씨앗 → `.env` (꼴 `.env.example`). 둘 다 git 밖 |
| git 밖 폴더 | `logs/`·`dist/`·`build/`·`docs/archive/`(옛 기록 — 실값이 그대로)·`docs/customers/`(업체 정보)·**사이트 전용** `collect/sites/local/`·`tools/local/`·`docs/local/` (원본에는 메일 사이트가 없다 — `docs/SITES.md`). 새로 만드는 파일에 실값이 들면 `.gitignore` 부터 |
| 올리기 전 검사 | `.githooks/`(`git config core.hooksPath .githooks` — 새로 clone 하면 한 번) — 커밋 전 `tools/probe_leaks --staged`, push 전 `--pre-push`(올리는 커밋의 파일·작성자·메시지). 모든 ref 는 손으로 `--history`. **낱말 목록이 없다** — 찾을 값은 `settings.local.json`·`.env`·git 밖 사이트/업체 파일·이 PC 이름에서 그때그때 뽑고, 메일 주소·전화번호·Windows 사용자 경로·내부 IP 모양도 본다. 설정에 없는 실값(화면에서 본 이름 등)은 못 잡는다 — 예시는 처음부터 가짜로 |

## 조사 / 테스트 도구 — 기능이 끝나면 지운다

**사용자 확정 2026-09-18. 09-07 의 "지우지 않는다" 를 대체한다.**
도구는 그 기능을 만드는 동안만 둔다. **기능이 완성·검증되면 그 기능 전용 도구를 지운다.**

- 지우기 전에 **알아낸 것을 문서로 옮긴다** — 컨트롤 식별값은 `docs/CONTROLS.md`,
  실측치·결정은 `docs/HANDOFF.md`. 도구는 지워도 근거는 남아야 한다.
- 지울 때 `docs/PROGRESS.md` 코드 지도의 줄도 같이 지운다.
- **회귀 확인용 `probe_*` 는 예외다** — 위 확인 도구 표에 있는 것들. 코드를 고칠 때마다
  돌려야 해서 기능이 끝나도 쓸모가 남는다. 표에서 빼기로 정한 뒤에 지운다.
- **삭제는 사용자 승인을 받고 한다.** git 으로 관리한다 (10-02) — 커밋된 것은 되살릴 수 있지만, 커밋 전 변경과 무시 대상(`logs/`·`dist/`·`config/settings.local.json`)은 지우면 끝이다.
- 새 도구: `probe_*` 읽기 전용 / `test_*` 조작(첫 줄에 그렇다고 적는다).
  `_probe_xxx.py` 같은 임시 이름으로 두지 않고, `docs/PROGRESS.md` 코드 지도에 한 줄 남긴다.
- `build_run.spec`의 `DEV_ONLY`가 `tools`를 통째로 빼므로 exe 에는 들어가지 않는다.

## 로그 / 임시 산출물 — 이건 지운다

도구는 남기지만 **실행하고 나온 것**은 남기지 않는다.

- 실행 로그(`logs/*.log`), 실패 스크린샷(`logs/*.png`)은 원인 파악이 끝나면 지운다.
- 조사 덤프(`docs/_dump_*.txt`, `docs/_windows_*.txt`)는 **산출물이 아니라 중간 파일**이다.
  내용을 `docs/UI_SURVEY.md`에 정리해 옮긴 뒤 삭제한다.
- 남아 있으면 다음 구현 작업을 시작하기 전에 지운다.
  `.claude/hooks/cleanup_temp.py`가 매 요청 시 잔여 파일을 보고한다.
- 영구 보관 대상은 `docs/`의 md 문서와 `tools/`의 도구다.

## Stack

- `pywinauto` (backend="uia") — 주력
- `pyautogui` — 좌표/드래그가 불가피할 때만
- `opencv-python`, `Pillow` — 이미지 인식이 필요할 때만
- OCR — 최후 수단

전부 설치하지 않는다. 실제 필요할 때 추가한다.

## UI 조작 우선순위 (반드시 이 순서로 검토)

1. UI Automation Control (`child_window`, `control_type`, `auto_id`)
2. 키보드 (Tab / Enter / 방향키 / PageDown / 프로그램 단축키)
3. 이미지 인식
4. 좌표 (`pyautogui`)

상위 방법이 불가능한 이유를 확인하지 않고 하위 방법으로 내려가지 않는다.
좌표로 내려간 항목은 `docs/UI_SURVEY.md`의 미해결 항목에 근거와 함께 남긴다.
파일 선택 창은 폴더를 눌러 찾지 않고 **전체 경로를 파일명 칸에 넣는다** (`utils/filedialog.py`).

## 문자 읽기 우선순위

1. UI Automation (`window_text()`, Value Pattern)
2. 클립보드 (Ctrl+C)
3. OCR — 상품코드/금액/수량 등은 결과를 그대로 신뢰하지 않고 검증한다.

## 주석·설명 길이 (사용자 확정 2026-09-17)

주석은 **Claude Code 가 읽는다.** 이해할 만큼만 짧게 쓴다. 긴 경위·실측표는 `docs/` 로 보낸다.
서브에이전트에 넘기는 지시와 받는 보고도 같다 — 근거(`파일:줄`)는 남기고 말은 줄인다.

## 금지

- 좌표 / 경로(EXE, Excel) 하드코딩 → `config/`에서만 관리
- `time.sleep` 고정 대기 → 조건 대기 + timeout 사용
- 존재를 확인하지 않은 `auto_id` / `title` / Window 이름을 추측해서 작성
- 무제한 Retry
- `except: pass` 형태의 예외 무시
- 사용하지 않는 라이브러리 추가

## 필수

- 모든 단계에 진입/종료 로그 (사람이 읽고 실패 지점을 바로 알 수 있게)
- 예외 발생 시 스크린샷을 `logs/`에 저장
- 모든 대기에 timeout
- 업무 흐름(`automation/`)과 공통 UI 조작(`utils/`) 분리

## 구조

```
main_run.py                   배포 진입점 (실행용 — 설정을 빌드에 굽는다). 유일한 진입점
main_build.py                 [개발] 빌드 프로그램 (설정을 고쳐서 실행용 exe 를 만든다)
build_tool.bat(빌드)  deploy_web.bat(웹 배포)  llm_worker.bat(LLM 워커)  survey.bat  probe_screen.bat(조사)

config/       설정. **좌표는 두지 않는다** (런타임에 rectangle() 로 계산)
              settings.py + fields_collect.py / fields_erpia.py
gui/          run_app(실행용 창) / erpia_app(그 부모 — 실행·예외 처리) / build_app(개발, 빌드 프로그램) / common /
              pipeline(진행 화면) / overlay(ERPia 위) / autorun_pane(예약) / history_window(지난 실행)
              run_app 은 erpia_app 을 상속한다. 입력 화면만 다시 그린다
orchestrator/ 업무 흐름 묶음: collect_flow / erpia_flow / full_flow / common / modules(기능 선택) /
              steps·steps_erpia·steps_collect(단계 표) / friendly(실패를 사람 말로) / report(HTML) / history(지난 실행 이력) /
              telemetry(서버 보고 — 큐·스레드·outbox)
              (run_app 은 full_flow 를 쓴다. collect_flow·erpia_flow 는 그 부품이자 개발 도구 test_collect·test_flow 의 흐름)
automation/   ERPia 조작: application, login, updater(업데이트·UAC), sidebar(프로세스바 id),
              order_mapping, logistics_wait, logistics
collect/      메일 수집 틀: webmail(MailSite 계약·로그인·수집), browser, sites(사이트 목록 — 사이트 파일은 git 밖 sites/local),
              storage, manifest, auth_code, phonelink, sms, pw_driver  (메일을 읽지 않음으로 되돌리지 않는다, 09-30)
utils/        공통: ui, wait, cancel, logger, dialogs, filedialog, dpi,
              elevation, process, winprobe, secret, envfile(.env 읽기), schedule, autorun, autostart, instance, machine
tools/        개발 전용 (probe_* 조사 / test_* 조작) — 빌드 제외. tools/local 은 사이트 전용 시험(git 밖)
server/       Supabase 스키마 SQL(원본) + 적용 순서 README (docs/SERVER_PLAN.md D). 적용은 승인 뒤 MCP 로 (스킬 server-sql)
llm/          [LLM 전용 PC] 사용법 질문 워커(worker.py) + 답의 근거 설명서(guide.md). RPA 빌드에 안 들어간다 (spec 이 막는다). llm_worker.bat 으로 켠다
web/          웹 대시보드 정적 파일 (index.html·app.js·config.js·_headers·wrangler.jsonc). deploy_web.bat 으로 Cloudflare Workers 에 올린다. textContent 만·SRI·CSP
logs/  docs/
```

## 진행 방식

처음 계획한 Phase 1~9 는 모두 끝났다 (`docs/PROGRESS.md` 표). 지금은 요청 단위로 한다 —
**요청받은 것만** 하고, 고친 것은 확인 도구로 확인한 뒤 다음으로 넘어간다.

## 실행 환경 주의

- 자동화 코드는 실제 업무 프로그램을 조작한다. 데이터를 변경/전송/저장하는
  동작이 포함되면 실행 전에 반드시 확인을 받는다.
- 조사(read-only) 코드와 조작(write) 코드를 구분해서 작성한다.
