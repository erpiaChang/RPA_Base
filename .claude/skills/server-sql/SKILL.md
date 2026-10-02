---
name: server-sql
description: Supabase(Postgres) 스키마·함수·정책 SQL 을 쓰거나 고칠 때 반드시 사용한다. "스키마", "테이블 만들기", "RLS", "ingest 함수", "security definer", "정책(policy)", "pg_cron", "마이그레이션" 같은 요청, 그리고 server/schema.sql 을 고치는 모든 작업이 대상이다. SQL 한 줄이라도 서버에 적용될 것이면 이 체크리스트를 먼저 본다.
---

# Server SQL

서버 연동 설계는 `docs/SERVER_PLAN.md` D (규칙은 D-10 — `ingest` 순서·권한·RLS·cron) 가 기준이다.
SQL 은 늘 `server/schema.sql` 같은 **파일이 원본**이다. 적용은 `supabase-rw` MCP 의 `apply_migration` 으로
Claude 가 한다 (사용자 확정 09-28) — **SQL 을 보여 주고 결정을 받은 뒤** 본문 첫 줄에 `-- APPROVED-SQL: <사유>`
를 붙인다 (`.claude/hooks/guard_sql.py` 가 강제. 서브에이전트는 붙여도 막힌다). 파일을 고치지 않고 서버만
고치지 않는다 — 다음 적용 때 되돌아간다.

## 파일 하나를 끝내기 전 체크리스트 (전부 예여야 한다)

| # | 확인 | 왜 |
| --- | --- | --- |
| 1 | 모든 표에 `alter table ... enable row level security;` | `public` 은 Data API 로 노출된다. RLS 없는 표는 anon 이 읽는다 |
| 2 | 읽기 정책은 `private.is_admin() or account_id in (select private.my_account_ids())` 꼴. 정책이 부르는 도우미 함수는 **`private` 스키마**(API 비노출)에 두고 authenticated 에 usage+execute 만 준다. `auth.uid()` 는 `(select ...)` 로 감싼다 | public 에 두면 Advisor 가 "signed-in users can execute" 로 경고한다 / 행마다 재평가를 막는다 |
| 3 | 쓰기 정책은 **없다.** 쓰기는 RPC 함수만 (`ingest`·`poll`·웹 RPC·LLM 워커 RPC) | 클라이언트가 표를 직접 못 만진다 |
| 4 | `revoke all on all tables in schema public from anon, authenticated;` | anon 은 표 권한 0 |
| 5 | `revoke execute on all functions in schema public from anon, authenticated;` + `alter default privileges in schema public revoke execute on functions from anon, authenticated;` + `grant execute on function public.ingest(...) to anon;` | Supabase 는 새 함수에 anon 실행 권한을 **기본으로** 준다 |
| 6 | `security definer` 함수는 `set search_path = ''` 이고 안의 이름은 전부 `public.` 을 붙인다 | search_path 가로채기 방지 (린트 0011) |
| 7 | 오류는 고정 문구 하나 (`raise exception 'unauthorized'`) — 키 없음/폐기를 구분하지 않는다 | 키 존재 여부가 새지 않는다 |
| 8 | 자유 문자열 컬럼은 `check (length(x) <= 300)`, 이벤트 배열은 100개·본문 64 KB 상한 | anon 키로 DB 를 채우지 못한다 |
| 9 | 시각 판정은 서버 `now()` (`received_at`, `heartbeat_at`). 클라이언트 `at` 은 정렬용 | PC 시계를 믿지 않는다 |
| 10 | `run_id` 소유 검사 — `runs.device_id` 가 이 키의 기기가 아니면 요청 전체 거부 | 다른 업체 실행을 덮어쓰지 못한다 |
| 11 | 빌드 ID·LLM 워커 키는 `sha256` 해시만 저장. 평문 컬럼 없음 | DB 가 새도 키는 안 샌다 |
| 12 | fk 는 `on delete cascade`, `pg_cron` 잡은 `lost` 판정(5분)·180일 삭제(하루)·카운터 리셋 | 운영 자동화 |
| 13 | `usage_daily` 는 삭제 대상에서 뺀다 | 과금 증빙 |
| 14 | 상태 문자열은 `orchestrator/history.py`·`orchestrator/steps.py` 와 **같은 값** | 두 곳이 다른 말을 쓰면 안 된다 |

## 하지 않는다

- `service_role` 키를 어디에도 적지 않는다 (`guard_secrets` 훅이 막는다).
- 사용자 결정 없이 적용하지 않는다 (`-- APPROVED-SQL:` 은 승인을 받은 뒤에 붙인다).
- 전문을 손으로 옮겨 적는 경로라, 적용 뒤 `md5(prosrc)` 를 파일 본문과 맞춰 전사 오류를 확인한다.
- Edge Function 은 만들지 않는다 — SQL 함수 + `pg_cron` + `pg_net` 으로 끝난다 (SERVER_PLAN D-10).
- 이미 있는 표·컬럼 이름을 바꾸지 않는다 (보고기·웹·워커가 그 이름을 쓴다). 바꾸면 `server/schema.sql` 과 SERVER_PLAN D 를 같이 고친다.
