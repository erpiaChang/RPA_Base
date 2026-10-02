---
name: site-add
description: 웹 사이트(메일 사이트·쇼핑몰 관리자·거래처 사이트 등)를 RPA 에 붙이거나 고칠 때 반드시 사용한다. "새 사이트 추가", "OO사이트 붙여 줘", "사이트A 에서 주문 엑셀 받게 해 줘", "메일 사이트 연결", "사이트 로그인해서 다운로드", "사이트에 입력하게", "Site/Task 추가" 같은 요청, 그리고 collect/sites/ 아래(git 밖 local/ 포함) 파일을 새로 만들거나 고치는 모든 작업이 대상이다. 기존 흐름 전체를 다시 분석하지 말고 이 절차와 docs/SITES.md 로 시작한다.
---

# Site Add

웹 사이트 하나를 **Site 파일 1개 + 등록 1줄 + 계정 값**으로 붙이는 절차. **사이트 파일·시험·조사 기록은 전부 git 밖이다** (`collect/sites/local/` · `tools/local/` · `docs/local/`, 사용자 확정 10-02) — 원본(git)에는 사이트가 없고, git 쪽 코드·문서·커밋 메시지에는 사이트 이름·주소·화면 요소를 쓰지 않는다 (자리표시 `사이트A`). 규칙·계약·경계의 원본은 `docs/SITES.md` — 먼저 읽는다.
기존 RPA 전체(`orchestrator/`, `automation/`)를 다시 읽지 않는다. 필요한 근거는 `docs/SITES.md` 5절 표가 가리키는 곳만 연다.

## 먼저 확인

- **어떤 사이트인가.** 메일 사이트(받은메일함의 첨부 엑셀 받기)면 틀이 이미 있다 — `collect/webmail.py` 의 `MailSite`. 사이트 파일만 만들고 5절은 건너뛴다. 메일이 아닌 사이트(Site/Task)는 `collect/sites/base.py` 가 있는가 — **없으면 이번이 첫 Site/Task 사이트다** → `docs/SITES.md` 5절 "첫 사이트 때 한 번" 을 같이 한다 (서버 SQL·웹 배포는 승인 필요). 있으면 5절은 건너뛴다.
- 이 사이트의 조사 기록(**git 밖** `docs/local/<사이트>.md`)이 있는가. 없으면 코드부터 쓰지 않는다.

## 절차 (게이트에서 멈추고 사용자에게 묻는다)

1. **접수** — `docs/SITES.md` 8절 체크리스트를 사용자에게 받는다. 빈 칸은 추측하지 않는다.
2. **흐름 기록** — 사람이 브라우저를 움직이고 codegen 으로 locator 를 받는다(`docs/SITES.md` 6절 1행). Claude 가 실계정으로 로그인하는 조사는 `test_*` 이고 승인이 필요하다.
3. **방해 요소 판정** → 가능 / 조건부 / 불가. **게이트: 사용자 결정.** CAPTCHA·보안 키패드·공동인증서·설치 요구 프로그램은 풀지 않는다.
4. **화면 기록** — 화면별 URL·진입 확인 요소·조작·결과 확인을 **git 밖** `docs/local/<사이트>.md` 에. (`docs/CONTROLS.md` 는 ERPia 화면용이다 — 사이트 절을 넣지 않는다.)
5. **Task 설계** — 읽기/쓰기(`writes`), 중복 방지, 0건일 때. **게이트: 사용자 확인.**
6. **코드** — 가장 최근 사이트 파일을 복사해 바꾼다. 사이트 파일은 **git 밖 `collect/sites/local/<사이트>.py`** — 메일 사이트는 SEL_* 상수·`SITE = MailSite(...)`·그 함수들만(재시도·대상 고르기·받기는 `collect/webmail.py` 몫), Site/Task 는 SEL_* 상수·`ERPIA_SITE`·`login()`·Task 함수만. 사람에게 보이는 글·재시도 루프·스크린샷은 틀(`webmail`·runner) 몫이라 쓰지 않는다. 등록은 `collect/sites/local/__init__.py` 의 `SITES` 튜플에 **리터럴 import** 한 줄 — 문자열 import 는 PyInstaller 가 놓친다 (Site/Task 등록표는 `docs/SITES.md` 5절).
7. **오프라인 확인** — **git 밖 `tools/local/`** 에 `probe_site_<id>` 를 만든다(`tools/local/probe_mail_pages.py` 가짜 Page 방식: 정상·로그인 실패·방해 문구·0건·불량 파일·시간 초과) — 메인이 돌린다 (rpa-verifier 의 훅은 `tools.probe_*` 만 통과시킨다). 틀 확인 `probe_webmail`(메일 사이트) / `probe_sites`(Site/Task) 와 `probe_userlog`·`probe_failure` 는 rpa-verifier 에 맡긴다.
8. **실사이트 점검** — `tools/local/test_site_<id> --check` (git 밖). **게이트: 승인 뒤 `APPROVED-RUN`.**
9. **실행** — 쓰기 Task 는 dry-run 먼저. **게이트: 승인.**
10. **문서** — `docs/SETTINGS.md`(계정 키), `docs/PROGRESS.md` 코드 지도(공통 틀만 — 사이트 파일은 적지 않는다), CLAUDE.md 확인 도구 표, `docs/HANDOFF.md`. 사이트 이름·주소는 어디에도 적지 않는다(자리표시). 실값은 `docs/local/<사이트>.md` 에만.

## 하지 않는다

- 사이트를 모듈로 만들지 않는다 (서버·웹·과금을 매번 고치게 된다). 사이트는 `sites` 모듈 안의 등록표 한 줄이다.
- selector·URL 을 `settings.local.json` 에 넣지 않는다. 계정 값만 넣는다 (`docs/SITES.md` 3절 — 메일 사이트의 로그인 주소 `mail_url` 만 먼저 만들어진 설정 키다).
- 실행 중 자동 fallback(안 되면 다른 기술로)을 넣지 않는다. 확인이 틀리면 이름 붙은 사유로 멈춘다.
- `page.wait_for_timeout`·`time.sleep` 고정 대기를 쓰지 않는다. 틀린 비밀번호로 실사이트를 시험하지 않는다.
- ERPia 사이트명이 업체마다 다르다고 가정하지 않는다 — 상수다. 다르다는 말을 들으면 그때 설정으로 뺀다.
- 사이트 이름·주소·계정·화면 요소를 git 에 쓰지 않는다 — 코드·문서·시험 예시·커밋 메시지 모두 자리표시(`사이트A`·`user01@example.com`). 실값은 git 밖 `collect/sites/local/`·`tools/local/`·`docs/local/` 에만 둔다 (`tools/probe_leaks` 가 커밋·push 전에 본다).
