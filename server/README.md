# 서버 (Supabase) — 적용 순서

설계는 `docs/SERVER_PLAN.md`. 여기는 **손으로 하는 순서**만 적는다. SQL 은 Claude 가 `supabase-rw` MCP 로
적용하거나(사용자 승인 뒤, `CLAUDE.md` 서버 연동 도구 규칙) 콘솔의 SQL Editor 에 붙여 넣어 실행한다.
어느 쪽이든 원본은 `server/schema.sql` 이다.

## 1. 프로젝트 만들기 (한 번)

1. https://supabase.com/dashboard → New project. **Region: Northeast Asia (Seoul)**, 무료 플랜.
2. 키는 **왼쪽 아래 톱니(Project Settings) → API Keys** 에 있다 (Integrations → Data API 화면에는 URL 만 보인다).
   - **Publishable key** (`sb_publishable_...`) 가 예전의 anon 키다 → `server_anon_key` 와 `web/config.js` 의 `anonKey`.
     예전 프로젝트면 같은 화면의 **Legacy API keys** 탭에 `anon` (`eyJ...`) 이 있고, 둘 다 된다.
   - **Secret key** (`sb_secret_...`) / 예전의 `service_role` 은 **어디에도 적지 않는다** (훅이 막는다).
   - URL 은 `https://<ref>.supabase.co` 까지다. Data API 화면의 `.../rest/v1/` 는 **떼고** 적는다 → `server_url`, `config.js` 의 `url`.
3. Authentication → Sign In / Providers: 맨 위 **User Signups → "Allow new users to sign up" 을 끈다** (가입 막기).
   아래 Email 의 **"Enable email provider" 는 켜 둔다** — 이것이 이메일 로그인 자체라 끄면 아무도 못 들어간다.
   (Authentication → URL Configuration 의 Site URL / Redirect URLs 는 **지금은 건드리지 않는다.** 대시보드 주소는
   `web/README.md` 대로 Cloudflare Workers 에 올린 뒤에 생기고, 그때 그 주소(`https://<이름>.<계정 서브도메인>.workers.dev`)를 넣는다.)
4. Database → Extensions 에서 `pg_cron`, `pg_net` 이 켜져 있는지 본다 (스키마 SQL 이 켜지만, 콘솔에서 먼저 켜도 된다).

## 2. 스키마 적용

SQL Editor → New query → `server/schema.sql` **전체**를 붙여 넣고 Run. 다시 실행해도 된다 (열 추가·이름 바꾸기·옛 함수 지우기가 전부 조건부다). **09-28 빌드 ID 판으로 올릴 때는 반드시 한 번 실행한다** — `accounts` 의 enroll 컬럼 3개가 지워지고 `devices.enrolled_at` 이 `bound_at` 으로 바뀐다.
끝나면 확인:

```sql
select tablename, rowsecurity from pg_tables where schemaname = 'public';        -- 전부 true
select jobname, schedule from cron.job;                                         -- rpa_mark_lost / rpa_purge_old / rpa_send_alerts(1분, 10-02) / rpa_expire_questions(1분) / rpa_refresh_usage(10분)
select n.nspname, proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname in ('public', 'private');                                      -- public: ingest, register_build, revoke_device, mark_lost, purge_old, send_alerts, poll, send_command, set_device_settings, request_settings, take_settings, suspend_account, resume_account, ask_question, llm_online, llm_take, llm_write, expire_questions, refresh_usage / private: is_admin, my_account_ids, can_control, clean_settings, bind_device, llm_worker …
```

Database → Advisors → Security 를 한 번 돌린다. **`public.ingest` 를 anon 이 부를 수 있다는 경고 하나는 정상**이다
(RPA 가 로그인 없이 빌드 ID 로 보내는 유일한 창구, 검사가 함수 안에 있다). 그 밖의 경고가 있으면 알린다.
`is_admin` / `my_account_ids` 경고는 09-23 에 두 함수를 `private` 스키마로 옮겨 없앴다 — 옛 스키마를 적용한 프로젝트는 파일을 한 번 더 실행하면 된다.

## 3. 관리자 계정

1. Authentication → Users → **Add user → Create new user** 에 **내 이메일**과 비밀번호를 넣고 **Auto Confirm User 를 켠다** (메일 인증 없이 바로 쓴다).
2. SQL Editor 에 아래를 붙여 넣고 이메일만 고쳐 실행한다 (UUID 를 복사할 필요 없다):

```sql
insert into public.account_members (user_id, account_id, role)
select id, null, 'admin' from auth.users where email = '내이메일@example.com'
on conflict do nothing;
select u.email, m.role from public.account_members m join auth.users u on u.id = m.user_id;   -- 확인
```

업체 사용자(자기 것만 보는 계정)는 같은 방식으로 `role = 'owner'` 와 그 업체의 `account_id`(`select id, name from public.accounts`)를 넣는다.

## 4. 빌드가 곧 등록이다 — 관리자는 아무것도 복사해 붙이지 않는다 (09-28)

09-23 의 "업체 등록 토큰" 방식을 대체한다. **빌드 프로그램이 빌드할 때 서버에 등록하고 빌드 ID 를 받아 굽는다.**

1. 빌드 프로그램(`build_tool.bat`) **7절**에 넣는다: 서버 URL·공개 키 / **업체 이름** / **이 빌드의 이름**
   (대시보드 PC 표에 보인다) / **관리자 이메일·비밀번호**(대시보드 로그인 계정).
   관리자 계정은 그 PC 의 설정에만 남고 **굽지 않는다** — 사용자 exe 에 들어가지 않는다.
2. [저장하고 빌드] → 빌드 프로그램이 관리자로 로그인해 `register_build('업체','빌드이름')` 을 부르고,
   받은 `bld_...` 를 설정에 넣어 굽는다 (`tools/register_build.py`). 업체가 없으면 만들어진다.
   등록이 실패하면 **빌드하지 않는다** — ID 없는 exe 는 대시보드에 안 보이고 그걸 나중에 알기 어렵다.
3. 사용자가 exe 를 켜면 실행 창이 서버에 한 번 확인한다(`ingest` 를 빈 이벤트로). **그 순간 그 PC 에 묶인다.**
   확인이 끝날 때까지 [실행] 이 잠긴다.

| 상황 | 서버 | 실행 창 |
| --- | --- | --- |
| 처음 켠 PC | `machine_hash` 를 채워 그 PC 에 묶는다 | 통과 — [실행] 풀림 |
| 같은 PC 에서 다시 켬 / exe 를 여러 벌 둠 | 같은 `machine_hash` 라 그대로 받는다 | 통과. **같은 PC 면 exe 몇 개든 된다** |
| **다른 PC** 에서 같은 exe 를 켬 | 401 | "이미 다른 PC 에 등록되어 있어…" — [실행] 잠김 |
| 빌드를 폐기했다 | 401 | 같음 |
| 인터넷이 안 됨 | — | 1분마다 다시. 확인 전에는 [실행] 잠김. **묶인 뒤에는** 인터넷이 없어도 돈다 (보고만 outbox) |
| 돌던 중 401 | — | 그 회차는 끝까지 가고, 그 뒤 [실행] 이 잠긴다 |

**PC 를 바꿔 줘야 하면 다시 빌드해서 준다** (사용자 확정 09-28 — 바인딩을 푸는 길은 두지 않는다).
옛 빌드를 못 쓰게 하려면 폐기한다. 인자는 ID 가 아니라 기기의 **id(uuid)** 다.

```sql
select id, name, last_seen_at, bound_at, revoked_at from public.devices;   -- 기기의 id (uuid)
select public.revoke_device('<그 id>');                                     -- 예: '3f1c...-....'
```

SQL 로 직접 등록해야 하면 (SQL Editor 는 관리자 검사를 건너뛴다):

```sql
select public.register_build('업체이름', '빌드이름');   -- 돌려주는 bld_... 가 평문이고 이번 한 번만 보인다
```

## 5. 알림 메일 (10-02 확장 — 끊김 · 무인 실행 결과 · UAC 대기 · 예약 전 휴대폰 점검)

Resend(https://resend.com) 무료 계정 → API 키. 콘솔 → Project Settings → Vault 에 넣는다:

| 이름 | 값 |
| --- | --- |
| `resend_api_key` | Resend 가 준 키 |
| `alert_from` | `RPA <onboarding@resend.dev>` (검증한 도메인이 있으면 그 주소) |
| `dashboard_url` | (선택) 메일의 '자세히 보기' 주소. 없으면 `https://rpa-dashboard.rpa-dashboard.workers.dev` |

- 둘(`resend_api_key`·`alert_from`)이 없으면 보내지 않고, 24시간(UAC 는 15분) 지난 것은 '오래됨' 으로 닫는다.
- 받는 사람: **그 업체 owner 와 admin 에게 따로 한 통씩** (한 요청이면 담당자 주소 때문에 거부될 때 관리자 메일까지 끊긴다). 직접 실행의 끊김·UAC 는 admin 만. 업체 담당자에게 보내려면 3절로 owner 계정을 만든다 (owner 는 웹 제어 권한도 갖는다).
- `onboarding@resend.dev` 는 Resend 계정 본인에게만 보낸다(추정) — 업체 담당자에게 보내려면 Resend 에서 발신 도메인을 검증한다.
- 한도: 같은 PC·같은 종류 30분에 1건(UAC·휴대폰), 실행 하나에 종류마다 1건, 업체당 하루 10건, cron 한 번(1분)에 1건(메일 2통).
- 1분 잡이 둘(`expire_questions`·`send_alerts`)이라 `cron.job_run_details` 가 하루 약 2,900줄 는다 — 가끔 지운다 (권한 확인 후 `delete from cron.job_run_details where end_time < now() - interval '7 days';`).
- 확인: `select kind, result, created_at, sent_at from public.alerts order by id desc limit 20;` / 실제 전달은 `net._http_response`.

## 6. 손으로 한 번 확인 (curl)

빌드를 등록한 뒤, 개발 PC 에서 (`machine` 은 아무 8자 이상 문자열이면 되지만, **이 호출이 그 빌드를 그 값에
묶는다** — 진짜 빌드 ID 로 시험하면 그 빌드는 이 가짜 PC 에 묶여 쓸 수 없게 된다. 시험용 ID 를 따로 등록해 쓴다):

```
curl -X POST "https://<ref>.supabase.co/rest/v1/rpc/ingest" ^
  -H "apikey: <anon key>" -H "Content-Type: application/json" ^
  -d "{\"device_key\":\"bld_...\",\"machine\":\"test-machine\",\"events\":[{\"type\":\"idle\",\"seq\":1,\"at\":1758500000}]}"
```

| 기대 | 뜻 |
| --- | --- |
| `{"accepted":1,"server_time":"..."}` | 정상 |
| 401 `unauthorized` | 빌드 ID 가 틀리거나 폐기됨, 또는 **다른 PC 에 묶인 빌드** |
| 400 `bad request` | 형식이 틀림 (events 가 배열이 아님, 100개 초과, 모르는 type) |
| 429 `too many` | 하루 상한 (run 50 / 이벤트 2만) |

`select * from public.devices;` 에서 `last_seen_at` 이 바뀌면 끝. 이 curl 은 실서버에 쓰므로 Claude 가 돌릴 때는 `APPROVED-RUN` 이 필요하다.

## 7. 권한·RLS 시험 (10-07)

`server/tests/rls_check.sql` — 가짜 두 업체·사용자로 남의 업체 읽기·anon 표 읽기·표 직접 쓰기·잘못된 빌드 ID·다른 PC·제어 RPC·
업체 중지(적용돼 있으면)를 **실제로 거부시켜** 보고, 설정 주고받기(웹 요청 → PC 답 → 한 번만 읽힘 · 건넨 값·읽힌 답이 서버에 안 남음 ·
비밀번호 키가 실린 답은 버림, 10-07)를 한 바퀴 돌린다. 확인 도구는 schema.sql 글자만 대조하므로 정책이 실제로 막는지는 이것으로 본다.
한 DO 블록(한 트랜잭션)이고 **끝에서 일부러 예외를 던져 전부 되돌린다.** 결과는 오류 문구다: `RLS_CHECK_OK n/n (되돌림)` 이면 통과,
`RLS_CHECK_FAIL` 이면 줄마다 어디가 틀렸는지 나온다. 스키마·정책·권한을 고쳐 적용한 뒤마다 한 번 돌린다.
실서버에 쓰는 SQL 이라 사용자에게 보인 뒤 본문 첫 줄에 `-- APPROVED-SQL: ...` 을 붙인다. 운영 시간대는 피한다 (수 초 행 잠금).

## 8. 업체 단위 중지 (10-07)

웹 관리자: [PC] 탭 → 위쪽에서 업체를 고른다 → [업체 사용] 의 [이 업체 RPA 중지] / [중지 풀기] (확인 창 뒤). SQL 로는
`select public.suspend_account('<업체 id>');` / `select public.resume_account('<업체 id>');` — 두 함수는 안에서 관리자인지 본다.
중지하면 그 업체의 모든 빌드가 다음 보고·poll(30초 안)에서 401 이라 실행 창이 잠기고(도는 실행은 끝까지 간다), 그 업체 owner 의
웹 [실행]·설정 저장은 403. 읽기·자료·빌드는 그대로다. 중지 중 끊긴 실행은 10분 뒤 `lost` 로 바뀌고 알림 메일이 갈 수 있다.
풀면 잠긴 실행 창이 **10분 안에** 서버 확인을 다시 해 풀린다 (`gui/run_app.py` `VERIFY_BLOCKED_MS`, 10-07 — 그 전에는 껐다 켜야 했다).

## 지우는 것

180일 지난 `runs`(하위 표 포함)는 매일 03:30(KST, pg_cron 은 UTC 라 SQL 에는 18:30) 에 자동 삭제된다. 그 전에 받으려면 대시보드 [엑셀로 내려받기] (③).
**사용량 `usage_daily` 는 지우지 않는다** — 과금 근거다 (9절, 10분마다 runs 에서 다시 센다). 사용법 질문은 30일 + 사람당 20개, 원격 명령은 30일.
**PC 설정은 서버에 남지 않는다** (10-07) — 명령에 실린 값(`commands.payload`)은 건넨 뒤·읽힌 뒤 지우고, 남은 것은 `mark_lost`(5분)가 지운다.
PC·업체 행은 지우지 말고 `revoke_device` 로 폐기한다 — 지우면 그 PC 의 사용량도 cascade 로 같이 지워진다.
`ingest` 의 하루 상한(run 50 / 이벤트 2만)도 UTC 날짜 기준이라 KST 09:00 에 바뀐다.

## 다시 적용할 때

`schema.sql` 은 `if not exists` 라 **표의 컬럼·제약은 두 번째 실행에서 안 바뀐다.** 표를 고쳤으면 그 표를 `drop table public.<표> cascade;` 한 뒤 다시 실행한다 (데이터가 지워진다 — 베타 초기에만).
함수·정책·잡은 `or replace` / `drop policy if exists` / `unschedule` 이라 그냥 다시 실행하면 된다.
