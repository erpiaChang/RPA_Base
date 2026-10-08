-- RPA 서버 스키마 (Supabase / Postgres). 설계: docs/SERVER_PLAN.md D (규칙 D-10). 적용 순서: server/README.md
-- 처음부터 끝까지 SQL Editor 에 한 번에 붙여 넣어 실행한다. 다시 실행해도 된다 (if not exists / or replace).
-- 상태 문자열은 orchestrator/history.py (runs) · orchestrator/steps.py (run_steps) 와 같은 값이다.

-- ---------------------------------------------------------------------------------------------
-- 0. 확장
-- ---------------------------------------------------------------------------------------------
create extension if not exists pgcrypto with schema extensions;   -- gen_random_bytes (빌드 ID 발급)
-- pg_cron: 5분·하루 잡 (스키마 cron). 시각은 UTC.
-- ★ 이미 있으면 `create extension` 을 아예 부르지 않는다 — Supabase 가 그 명령 뒤에 postgres 권한을 다시 손보는
--   트리거를 돌리는데, 두 번째 실행에서 "dependent privileges exist" 로 깨졌다 (09-23). 권한은 그 트리거가
--   알아서 주므로 여기서 `grant ... to postgres` 를 **하지 않는다** (초판의 그 두 줄이 자기-부여 권한을 만들어 원인이 됐다).
do $do$
begin
    if not exists (select 1 from pg_extension where extname = 'pg_cron') then
        create extension pg_cron with schema pg_catalog;
    end if;
end
$do$;
-- 초판(09-22)이 만든 postgres → postgres 자기-부여 권한을 걷어 낸다. 실행자가 postgres 라 postgres 가 준 것만 지워지고,
-- supabase_admin 이 준 원래 권한은 그대로다. 없으면 아무 일도 안 한다.
revoke all on all tables in schema cron from postgres;
revoke usage on schema cron from postgres;
create extension if not exists pg_net with schema extensions;    -- 끊김 알림 메일. 함수는 net. 에 생긴다
create extension if not exists supabase_vault cascade;           -- 메일 API 키 보관 (vault.decrypted_secrets)

-- ---------------------------------------------------------------------------------------------
-- 1. 표
-- ---------------------------------------------------------------------------------------------
create table if not exists public.accounts (
    id          uuid primary key default gen_random_uuid(),
    name        text not null check (length(name) <= 100),
    created_at  timestamptz not null default now()
);
-- 업체 단위 중지 (10-07): 값이 있으면 그 업체의 모든 빌드가 ingest·poll 에서 401 (private.bind_device), owner 의 웹 제어 403
-- (private.can_control). 읽기는 그대로. 중지·재개는 public.suspend_account / resume_account (웹 관리자·SQL). 자료는 지우지 않는다
alter table public.accounts add column if not exists suspended_at timestamptz;

create table if not exists public.devices (
    id               uuid primary key default gen_random_uuid(),
    account_id       uuid not null references public.accounts(id) on delete cascade,
    name             text not null check (length(name) <= 100),      -- 관리자가 빌드할 때 정한다. 클라이언트 값은 무시
    key_hash         text not null unique,                            -- sha256(빌드 ID) hex. 평문 없음
    app_version      text check (length(app_version) <= 50),
    last_seen_at     timestamptz,
    next_run_at      timestamptz,                                     -- idle 심박이 준다
    auto_run_enabled boolean,
    paused           boolean,
    revoked_at       timestamptz,
    runs_today       int not null default 0,
    events_today     int not null default 0,
    counted_on       date,
    flag             text check (length(flag) <= 100),                -- 이상 표시 (같은 키로 running 둘 이상 등)
    created_at       timestamptz not null default now()
);
create index if not exists devices_account_idx on public.devices(account_id);

-- PC 바인딩 (09-28, 09-23 의 업체 등록 토큰 방식을 대체한다):
-- 빌드 하나 = devices 한 행. `key_hash` 는 exe 에 구운 **빌드 ID** 의 해시다 (기기 키가 아니다).
-- `machine_hash` 가 null 이면 아직 안 묶인 빌드이고, 첫 `ingest` 가 그 PC 로 묶는다. 그 뒤로는 그 PC 만 받는다.
-- PC 를 바꾸려면 **다시 빌드해서 준다** (푸는 길은 두지 않는다 — 사용자 확정 09-28).
alter table public.devices add column if not exists machine_hash text;   -- sha256(Windows MachineGuid)
alter table public.devices add column if not exists bound_at     timestamptz;   -- 그 PC 에 묶인 시각
-- 업체 전용 빌드의 업체 id (10-08, `docs/CUSTOMERS.md` 확정 3) — exe 에 든 업체 패키지. 원본 빌드는 null.
-- 서버의 '업체'(accounts, 그 RPA 를 쓰는 회사)와는 다른 값이다. register_build 가 넣는다
alter table public.devices add column if not exists customer text
    check (customer is null or customer ~ '^[a-z0-9_]{1,30}$');
-- 09-23 판에서 넘어오는 경우: 이름만 바꾼다 (자기 등록 시각 = 묶인 시각)
do $do$
begin
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'devices' and column_name = 'enrolled_at') then
        update public.devices set bound_at = coalesce(bound_at, enrolled_at);
        alter table public.devices drop column enrolled_at;
    end if;
end
$do$;
-- 업체 등록 토큰은 더 쓰지 않는다
alter table public.accounts drop column if exists enroll_hash;
alter table public.accounts drop column if exists enroll_max_devices;
alter table public.accounts drop column if exists enroll_expires_at;
create index if not exists devices_machine_idx on public.devices(account_id, machine_hash);

create table if not exists public.runs (
    id               uuid primary key,                                -- 클라이언트 uuid4
    account_id       uuid not null references public.accounts(id) on delete cascade,
    device_id        uuid not null references public.devices(id) on delete cascade,
    trigger          text check (length(trigger) <= 50),
    modules          text[] not null default '{}' check (cardinality(modules) <= 10),   -- 빈 배열 = 전체
    state            text not null default 'running'
                     check (state in ('running','done','unfinished','failed','cancelled','lost','skipped')),
    started_at       timestamptz,
    finished_at      timestamptz,
    elapsed          real,
    summary          text check (length(summary) <= 300),
    attention        int not null default 0,
    step_total       int,
    current_index    int,
    current_name     text check (length(current_name) <= 100),
    current_activity text check (length(current_activity) <= 300),
    heartbeat_at     timestamptz,                                     -- 서버 시각
    screen_locked    boolean,
    paused           boolean,
    received_at      timestamptz not null default now(),              -- 서버 시각
    last_seq         int not null default 0,
    flags            text[] not null default '{}'
);
create index if not exists runs_account_started_idx on public.runs(account_id, started_at desc);
create index if not exists runs_device_running_idx on public.runs(device_id) where state = 'running';

create table if not exists public.run_steps (
    run_id        uuid not null references public.runs(id) on delete cascade,
    step_id       text not null check (length(step_id) <= 50),
    seq           int not null,
    index         int,
    name          text check (length(name) <= 100),
    state         text check (state in ('pending','running','done','skipped','failed','cancelled')),
    detail        text check (length(detail) <= 300),
    activity      text check (length(activity) <= 300),
    total_items   int, done_items int, ok_items int, failed_items int,
    started_at    timestamptz,
    finished_at   timestamptz,
    elapsed       real,
    error         text check (length(error) <= 300),
    primary key (run_id, step_id)
);

create table if not exists public.run_logs (
    run_id      uuid not null references public.runs(id) on delete cascade,
    seq         int not null,
    at          timestamptz,
    received_at timestamptz not null default now(),
    level       text check (length(level) <= 10),
    step_id     text check (length(step_id) <= 50),
    text        text check (length(text) <= 300),
    primary key (run_id, seq)
);

create table if not exists public.run_attention (
    run_id  uuid not null references public.runs(id) on delete cascade,
    seq     int not null,
    text    text check (length(text) <= 300),
    primary key (run_id, seq)
);

-- 대시보드 로그인 ↔ 계정. admin 은 account_id 가 없어도 된다 (전 업체를 본다).
create table if not exists public.account_members (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references auth.users(id) on delete cascade,
    account_id  uuid references public.accounts(id) on delete cascade,     -- admin 은 null
    role        text not null check (role in ('owner','viewer','admin')),
    check (role = 'admin' or account_id is not null),
    unique nulls not distinct (user_id, account_id, role)
);

create table if not exists public.alerts (
    id          bigint generated always as identity primary key,
    run_id      uuid references public.runs(id) on delete set null,
    kind        text not null,                                -- 종류 검사는 아래 alter (이미 있는 DB 와 같게)
    created_at  timestamptz not null default now(),
    sent_at     timestamptz
);
-- 10-01 알림 확장 — 실행 밖 알림(예약 전 휴대폰 점검)·업체별 받는 사람·처리 결과. 이미 있는 DB 에 다시 돌려도 된다
alter table public.alerts add column if not exists account_id uuid references public.accounts(id) on delete cascade;
alter table public.alerts add column if not exists device_id  uuid references public.devices(id) on delete cascade;
alter table public.alerts add column if not exists text       text check (length(text) <= 300);   -- PC 가 보낸 사람 말
alter table public.alerts add column if not exists result     text check (length(result) <= 50);  -- 보냄 / 오래됨 / 받는 사람 없음 / 하루 한도
-- lost 끊김 / result 무인 실행의 실패·미완료·확인할 것 / uac ERPia 업데이트 동의 대기 / phone 예약 전 휴대폰 점검 실패
-- (uac·phone = orchestrator/telemetry.ALERT_KINDS)
alter table public.alerts drop constraint if exists alerts_kind_check;
alter table public.alerts add constraint alerts_kind_check check (kind in ('lost', 'result', 'uac', 'phone'));
-- 실행 하나에 같은 종류는 한 번 — 재전송·mark_lost 와 겹쳐도 (insert 는 on conflict do nothing)
create unique index if not exists alerts_run_kind_uq on public.alerts (run_id, kind)
    where run_id is not null and kind in ('lost', 'result');

-- ---------------------------------------------------------------------------------------------
-- 2. RLS — 읽기는 로그인한 회원이 자기 계정 것만, admin 은 전부. 쓰기 정책은 없다 (ingest 만 쓴다)
-- ---------------------------------------------------------------------------------------------
alter table public.accounts        enable row level security;
alter table public.devices         enable row level security;
alter table public.runs            enable row level security;
alter table public.run_steps       enable row level security;
alter table public.run_logs        enable row level security;
alter table public.run_attention   enable row level security;
alter table public.account_members enable row level security;
alter table public.alerts          enable row level security;

-- 정책이 부르는 도우미 둘은 API 에 노출되지 않는 `private` 스키마에 둔다 (Security Advisor 09-23:
-- public 에 두면 /rest/v1/rpc/ 로 불린다고 경고한다). 정책은 부르는 사람(authenticated) 권한으로 실행되므로
-- 그 역할에 스키마 usage + 함수 execute 가 있어야 한다. SECURITY INVOKER 로 바꾸면 account_members 정책이
-- 자기 자신을 다시 불러 무한 재귀가 난다 — definer 를 유지한다.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

-- 정책이 함수에 의존하므로 정책부터 지운다 (첫 배포본은 함수가 public 에 있었다)
drop policy if exists accounts_read on public.accounts;
drop policy if exists devices_read on public.devices;
drop policy if exists runs_read on public.runs;
drop policy if exists run_steps_read on public.run_steps;
drop policy if exists run_logs_read on public.run_logs;
drop policy if exists run_attention_read on public.run_attention;
drop policy if exists members_read on public.account_members;
drop policy if exists alerts_read on public.alerts;
drop function if exists public.is_admin();
drop function if exists public.my_account_ids();

create or replace function private.is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (select 1 from public.account_members m
                   where m.user_id = (select auth.uid()) and m.role = 'admin');
$$;

create or replace function private.my_account_ids() returns setof uuid
language sql stable security definer set search_path = '' as $$
    select m.account_id from public.account_members m
    where m.user_id = (select auth.uid()) and m.account_id is not null;
$$;

revoke execute on function private.is_admin(), private.my_account_ids() from public, anon;
grant execute on function private.is_admin(), private.my_account_ids() to authenticated;

create policy accounts_read on public.accounts for select to authenticated
    using (private.is_admin() or id in (select private.my_account_ids()));

create policy devices_read on public.devices for select to authenticated
    using (private.is_admin() or account_id in (select private.my_account_ids()));

create policy runs_read on public.runs for select to authenticated
    using (private.is_admin() or account_id in (select private.my_account_ids()));

create policy run_steps_read on public.run_steps for select to authenticated
    using (exists (select 1 from public.runs r where r.id = run_id));

create policy run_logs_read on public.run_logs for select to authenticated
    using (exists (select 1 from public.runs r where r.id = run_id));

create policy run_attention_read on public.run_attention for select to authenticated
    using (exists (select 1 from public.runs r where r.id = run_id));

create policy members_read on public.account_members for select to authenticated
    using (user_id = (select auth.uid()) or private.is_admin());

create policy alerts_read on public.alerts for select to authenticated
    using (private.is_admin());

-- ---------------------------------------------------------------------------------------------
-- 3. 권한 — anon 은 ingest 실행만. authenticated 는 select 만 (행은 RLS 가 거른다)
-- ---------------------------------------------------------------------------------------------
revoke all on all tables in schema public from anon, authenticated;
-- 함수는 Postgres 기본으로 PUBLIC 에 실행 권한이 있고 anon/authenticated 는 PUBLIC 을 물려받는다 → PUBLIC 부터 뺀다
revoke execute on all functions in schema public from public, anon, authenticated;
alter default privileges in schema public revoke execute on functions from public, anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
-- authenticated 는 select 만. 행은 RLS 정책이 거른다 (규칙 훅: 읽기 권한이라 허용)
grant select on public.accounts, public.devices, public.runs, public.run_steps, public.run_logs,
                public.run_attention, public.account_members, public.alerts to authenticated;

-- ---------------------------------------------------------------------------------------------
-- 4. ingest — 클라이언트가 부르는 유일한 쓰기 창구. POST /rest/v1/rpc/ingest {"device_key","events"}
--    순서: 크기 상한 → 키 → 하루 상한 → runs 보장 → run_id 소유 → 종류별 반영 (SERVER_PLAN D-10)
--    오류는 고정 문구 셋. errcode PT4xx 는 PostgREST 가 그 HTTP 상태로 돌려준다 (401 / 429 / 400).
-- ---------------------------------------------------------------------------------------------
-- device_key 자리에는 exe 에 구운 **빌드 ID** 가 온다 (09-28). machine 은 그 PC 의 Windows MachineGuid.
--    events 를 **빈 배열**로 부르면 그것이 곧 등록·확인이다 — 실행 창이 뜰 때 한 번 불러 잠금을 판정한다.
--    옛 signature 는 지운다 — 둘 다 있으면 PostgREST 가 어느 것을 부를지 못 정한다 (PGRST203).
drop function if exists public.ingest(text, jsonb);

-- 빌드 ID + PC 바인딩 판정 (09-28). `ingest` 와 `poll` 이 같이 쓴다 — 보안 판정을 두 벌로 두지 않는다.
-- 처음이면 이 PC 로 묶고, 이미 묶였으면 그 PC 에서 온 것만 받는다. 빌드를 복사해 다른 PC 에서 켜면 401.
-- 폐기된 빌드도 같은 401 이다 (ID 존재 여부를 새지 않는다).
create or replace function private.bind_device(device_key text, machine text) returns public.devices
language plpgsql security definer set search_path = '' as $$
declare
    v_dev     public.devices%rowtype;
    v_machine text;
begin
    select * into v_dev from public.devices d
    where d.key_hash = encode(sha256(convert_to(coalesce(device_key, ''), 'UTF8')), 'hex')
      and d.revoked_at is null;
    if not found then
        raise exception 'unauthorized' using errcode = 'PT401';
    end if;
    -- 중지된 업체 (10-07) — 폐기·다른 PC 와 같은 401 (이유를 알리지 않는다). PC 에 묶기 전에 막는다
    if exists (select 1 from public.accounts a where a.id = v_dev.account_id and a.suspended_at is not null) then
        raise exception 'unauthorized' using errcode = 'PT401';
    end if;
    v_machine := encode(sha256(convert_to(coalesce(machine, ''), 'UTF8')), 'hex');
    if v_dev.machine_hash is null then
        if machine is null or length(machine) < 8 then      -- MachineGuid 를 못 읽었다
            raise exception 'bad request' using errcode = 'PT400';
        end if;
        -- 조건부 update — 같은 순간 다른 PC 가 먼저 묶었으면 여기서 걸린다
        update public.devices set machine_hash = v_machine, bound_at = now()
        where id = v_dev.id and machine_hash is null;
        -- 못 묶었으면 누군가 먼저 묶었다. **같은 PC 의 다른 요청**(확인과 poll 이 겹침)이면 통과시킨다
        if not found and not exists (select 1 from public.devices d
                                     where d.id = v_dev.id and d.machine_hash = v_machine) then
            raise exception 'unauthorized' using errcode = 'PT401';
        end if;
        v_dev.machine_hash := v_machine;
    elsif v_dev.machine_hash <> v_machine then
        raise exception 'unauthorized' using errcode = 'PT401';
    end if;
    return v_dev;
end;
$$;
revoke execute on function private.bind_device(text, text) from public, anon, authenticated;

create or replace function public.ingest(device_key text, events jsonb, machine text default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    v_dev       public.devices%rowtype;
    v_today     date := current_date;
    v_starts    int;
    v_accepted  int := 0;
    v_ev        jsonb;
    v_type      text;
    v_run       uuid;
    v_seq       int;
    v_state     text;
    v_running   int;
    v_trigger   text;
    v_attention int;
begin
    if events is null or jsonb_typeof(events) <> 'array'
       or jsonb_array_length(events) > 100 or pg_column_size(events) > 65536 then
        raise exception 'bad request' using errcode = 'PT400';
    end if;

    v_dev := private.bind_device(device_key, machine);

    if v_dev.counted_on is distinct from v_today then
        v_dev.runs_today := 0;
        v_dev.events_today := 0;
    end if;
    select count(*) into v_starts from jsonb_array_elements(events) e where e->>'type' = 'run_start';
    if v_dev.events_today + jsonb_array_length(events) > 20000 or v_dev.runs_today + v_starts > 50 then
        raise exception 'too many' using errcode = 'PT429';
    end if;

    for v_ev in select * from jsonb_array_elements(events) loop
        begin
            v_type := v_ev->>'type';

            if v_type = 'idle' then
                update public.devices set
                    next_run_at      = case when v_ev ? 'next_run_at' then to_timestamp((v_ev->>'next_run_at')::double precision) end,
                    auto_run_enabled = (v_ev->>'auto_run_enabled')::boolean,
                    paused           = (v_ev->>'paused')::boolean,
                    app_version      = coalesce(left(v_ev->>'app_version', 50), app_version)
                where id = v_dev.id;
                v_accepted := v_accepted + 1;
                continue;
            end if;

            v_run := (v_ev->>'run_id')::uuid;
            v_seq := coalesce((v_ev->>'seq')::int, 0);

            if v_run is not null then
                -- runs 를 먼저 보장한다 — run_start 가 유실돼도 고아가 생기지 않는다
                insert into public.runs (id, account_id, device_id)
                values (v_run, v_dev.account_id, v_dev.id)
                on conflict (id) do nothing;
                -- run_id 소유: 다른 기기의 실행이면 요청 전체를 거부한다 (예외 = 롤백)
                perform 1 from public.runs r where r.id = v_run and r.device_id = v_dev.id;
                if not found then
                    raise exception 'unauthorized' using errcode = 'PT401';
                end if;
            elsif v_type is distinct from 'alert' then
                -- 실행 밖에서 오는 것은 idle·alert 뿐 (10-01 — 전에는 not null 위반 → 5xx → PC 가 하루 동안 다시 보냈다)
                raise exception 'bad request' using errcode = 'PT400';
            end if;

            if v_type = 'run_start' then
                update public.runs set
                    trigger     = left(v_ev->>'trigger', 50),
                    modules     = coalesce((select array_agg(x) from jsonb_array_elements_text(v_ev->'modules') x), '{}'),
                    step_total  = jsonb_array_length(coalesce(v_ev->'plan', '[]'::jsonb)),
                    started_at  = to_timestamp((v_ev->>'at')::double precision),
                    heartbeat_at = now(),
                    last_seq    = greatest(last_seq, v_seq)
                where id = v_run;
                update public.devices set app_version = coalesce(left(v_ev->>'app_version', 50), app_version)
                where id = v_dev.id;

            elsif v_type = 'step' then
                insert into public.run_steps (run_id, step_id, seq, index, name, state, detail, activity,
                                              total_items, done_items, ok_items, failed_items,
                                              started_at, finished_at, elapsed, error)
                values (v_run, left(v_ev->>'step_id', 50), v_seq, (v_ev->>'index')::int, left(v_ev->>'name', 100),
                        v_ev->>'state', left(v_ev->>'detail', 300), left(v_ev->>'activity', 300),
                        (v_ev->>'total_items')::int, (v_ev->>'done_items')::int,
                        (v_ev->>'ok_items')::int, (v_ev->>'failed_items')::int,
                        case when coalesce((v_ev->>'started_at')::double precision, 0) > 0
                             then to_timestamp((v_ev->>'started_at')::double precision) end,
                        case when coalesce((v_ev->>'finished_at')::double precision, 0) > 0
                             then to_timestamp((v_ev->>'finished_at')::double precision) end,
                        (v_ev->>'elapsed')::real, left(v_ev->>'error', 300))
                on conflict (run_id, step_id) do update set
                    seq = excluded.seq, index = excluded.index, name = excluded.name, state = excluded.state,
                    detail = excluded.detail, activity = excluded.activity,
                    total_items = excluded.total_items, done_items = excluded.done_items,
                    ok_items = excluded.ok_items, failed_items = excluded.failed_items,
                    started_at = excluded.started_at, finished_at = excluded.finished_at,
                    elapsed = excluded.elapsed, error = excluded.error
                where public.run_steps.seq < excluded.seq;           -- 늦게 온 옛 사진은 버린다
                -- 현재 단계는 **더 새 사진**(seq) 일 때만 바꾼다 — 재전송으로 옛 사진이 늦게 와도 되돌리지 않는다
                update public.runs set
                    heartbeat_at     = now(),
                    step_total       = coalesce((v_ev->>'total')::int, step_total),
                    current_index    = case when v_ev->>'state' = 'running' and last_seq <= v_seq then (v_ev->>'index')::int else current_index end,
                    current_name     = case when v_ev->>'state' = 'running' and last_seq <= v_seq then left(v_ev->>'name', 100) else current_name end,
                    current_activity = case when v_ev->>'state' = 'running' and last_seq <= v_seq then left(v_ev->>'activity', 300) else current_activity end,
                    last_seq         = greatest(last_seq, v_seq)
                where id = v_run and state = 'running';

            elsif v_type = 'log' then
                insert into public.run_logs (run_id, seq, at, level, step_id, text)
                values (v_run, v_seq, to_timestamp((v_ev->>'at')::double precision),
                        left(v_ev->>'level', 10), left(v_ev->>'step_id', 50), left(v_ev->>'text', 300))
                on conflict (run_id, seq) do nothing;

            elsif v_type = 'attend' then
                insert into public.run_attention (run_id, seq, text)
                values (v_run, v_seq, left(v_ev->>'text', 300))
                on conflict (run_id, seq) do nothing;

            elsif v_type = 'heartbeat' then
                update public.runs set
                    heartbeat_at  = now(),
                    screen_locked = (v_ev->>'screen_locked')::boolean,
                    paused        = (v_ev->>'paused')::boolean
                where id = v_run and state = 'running';

            elsif v_type = 'alert' then
                -- 사람 손이 **지금** 필요한 일 (10-01) — send_alerts 가 업체 담당자에게 메일. 종류 = telemetry.ALERT_KINDS.
                -- 실행 안이면 run_id 가 붙고(UAC 동의 대기), 밖이면 없다(예약 전 휴대폰 점검). PC 는 따로 한 건씩 보낸다
                if coalesce(v_ev->>'kind', '') not in ('uac', 'phone') then
                    raise exception 'bad request' using errcode = 'PT400';
                end if;
                -- 같은 PC·같은 종류는 30분에 한 줄 — 기다리는 동안·점검을 되풀이해도 메일이 쌓이지 않는다
                insert into public.alerts (kind, account_id, device_id, run_id, text)
                select v_ev->>'kind', v_dev.account_id, v_dev.id, v_run, left(v_ev->>'text', 300)
                where not exists (select 1 from public.alerts a
                                  where a.device_id = v_dev.id and a.kind = v_ev->>'kind'
                                    and a.created_at > now() - interval '30 minutes');

            elsif v_type = 'run_end' then
                v_state := v_ev->>'state';
                if v_state is null or v_state not in ('done','unfinished','failed','cancelled','lost','skipped') then
                    raise exception 'bad request' using errcode = 'PT400';
                end if;
                -- running 은 어떤 끝으로든, lost 는 진짜 끝(done 등)으로만 바뀐다. 끝난 실행을 lost 로 되돌리지 않는다.
                update public.runs set
                    state        = v_state,
                    summary      = left(v_ev->>'summary', 300),
                    elapsed      = (v_ev->>'elapsed')::real,
                    attention    = coalesce((v_ev->>'attention')::int, attention),
                    modules      = coalesce((select array_agg(x) from jsonb_array_elements_text(v_ev->'modules') x), modules),
                    trigger      = coalesce(left(v_ev->>'trigger', 50), trigger),
                    finished_at  = now(),
                    heartbeat_at = now(),
                    last_seq     = greatest(last_seq, v_seq),
                    current_activity = null
                where id = v_run
                  and (state = 'running' or (state = 'lost' and v_state <> 'lost'))
                returning trigger, attention into v_trigger, v_attention;
                -- 알림은 **이번에** 끝으로 바뀔 때만 넣는다 — 재전송·mark_lost 와 겹쳐도 한 번 (보내기는 send_alerts)
                if found then
                    if v_state = 'lost' then
                        insert into public.alerts (run_id, kind, account_id, device_id)
                        values (v_run, 'lost', v_dev.account_id, v_dev.id)
                        on conflict do nothing;
                    elsif v_trigger in ('예약 실행', '원격 실행')       -- 무인 실행만 (gui/erpia_app·run_app 의 trigger 글)
                          and (v_state in ('failed', 'unfinished') or coalesce(v_attention, 0) > 0) then
                        insert into public.alerts (run_id, kind, account_id, device_id)
                        values (v_run, 'result', v_dev.account_id, v_dev.id)
                        on conflict do nothing;
                    end if;
                end if;

            else
                raise exception 'bad request' using errcode = 'PT400';
            end if;

            v_accepted := v_accepted + 1;
        exception
            when sqlstate 'PT401' or sqlstate 'PT429' or sqlstate 'PT400' then
                raise;
            when data_exception or check_violation or not_null_violation then     -- 필드가 빠진 이벤트도 형식 오류다 (10-02)
                raise exception 'bad request' using errcode = 'PT400';   -- 형변환·형식 오류. 원문은 밖으로 안 낸다
            -- 그 밖(교착·직렬화 등 서버 쪽 일시 오류)은 그대로 올린다 → 5xx → 클라이언트가 다시 보낸다
        end;
    end loop;

    -- 같은 기기에서 running 이 둘 이상이면 표시만 한다 (다중 PC 같은 키 의심)
    select count(*) into v_running from public.runs r where r.device_id = v_dev.id and r.state = 'running';
    update public.devices set
        last_seen_at = now(),
        counted_on   = v_today,
        runs_today   = v_dev.runs_today + v_starts,
        events_today = v_dev.events_today + v_accepted,
        flag         = case when v_running > 1 then 'running ' || v_running || '건' else null end
    where id = v_dev.id;

    return jsonb_build_object('accepted', v_accepted, 'server_time', now());
end;
$$;

revoke execute on function public.ingest(text, jsonb, text) from public, authenticated;
grant execute on function public.ingest(text, jsonb, text) to anon;

-- ---------------------------------------------------------------------------------------------
-- 5. 빌드 등록 — 관리자가 exe 를 구울 때 부른다 (09-28, 09-23 의 enroll/토큰 방식을 대체)
--    빌드 프로그램이 관리자 계정으로 로그인해 `register_build` 를 부르고, 받은 빌드 ID 를 exe 에 굽는다.
--    관리자는 토큰을 복사해 붙이지 않는다. 빌드 ID 평문은 이 반환값 한 번뿐이고 서버에는 해시만 남는다.
--    그 빌드가 처음 보고할 때 `ingest` 가 그 PC 에 묶는다. 다른 PC 에서 켜면 401 → 실행 창 잠김.
-- ---------------------------------------------------------------------------------------------
-- 09-23 판의 함수들은 지운다 (자기 등록 토큰 방식은 쓰지 않는다)
drop function if exists public.enroll(text, text, text);
drop function if exists public.issue_enroll_token(text, int, int);
drop function if exists public.issue_device_key(text, text);

-- 10-08: 업체 전용 빌드의 업체 id 를 받는다 (`customer`, 없으면 원본). 옛 두 인자 판은 지운다 — 남기면 두 인자로 부를 때
-- 어느 판인지 정하지 못한다
drop function if exists public.register_build(text, text);
create or replace function public.register_build(account_name text, build_name text, customer text default null)
returns text
language plpgsql security definer set search_path = '' as $$
declare
    v_account uuid;
    v_id      text;
begin
    -- definer 안에서 current_user 는 늘 소유자(postgres)라 못 쓴다. session_user 는 접속 역할 그대로다
    -- (SQL Editor = postgres / PostgREST = authenticator).
    if session_user <> 'postgres' and not private.is_admin() then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if account_name is null or length(trim(account_name)) = 0 or length(account_name) > 100
       or build_name is null or length(trim(build_name)) = 0 or length(build_name) > 100
       or (customer is not null and customer !~ '^[a-z0-9_]{1,30}$') then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    select id into v_account from public.accounts where name = account_name;
    if v_account is null then
        insert into public.accounts (name) values (account_name) returning id into v_account;
    end if;
    v_id := 'bld_' || translate(encode(extensions.gen_random_bytes(32), 'base64'), '+/=', '-_');
    insert into public.devices (account_id, name, key_hash, customer)
    values (v_account, trim(build_name), encode(sha256(convert_to(v_id, 'UTF8')), 'hex'), register_build.customer);
    return v_id;
end;
$$;
revoke execute on function public.register_build(text, text, text) from public, anon;
grant execute on function public.register_build(text, text, text) to authenticated;   -- 안에서 is_admin 검사

-- 빌드 폐기. 그 뒤로 그 빌드 ID 의 ingest 는 401 이고, 실행 창이 잠긴다.
create or replace function public.revoke_device(device uuid) returns void
language sql security definer set search_path = '' as $$
    update public.devices set revoked_at = now() where id = device;
$$;
revoke execute on function public.revoke_device(uuid) from public, anon, authenticated;

-- 업체 중지·재개 (10-07). 중지하면 그 업체의 모든 빌드가 401 이고 실행 창이 잠긴다 — PC 마다 폐기하지 않아도 된다.
-- 자료·빌드는 그대로라 재개하면 다시 돈다 (PC 는 10분마다 다시 확인한다). 웹 관리자 화면(PC 탭)과 SQL 이 부른다
create or replace function public.suspend_account(account uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
    if session_user <> 'postgres' and not private.is_admin() then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    update public.accounts set suspended_at = coalesce(suspended_at, now()) where id = account;
    if not found then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
end;
$$;
revoke execute on function public.suspend_account(uuid) from public, anon;
grant execute on function public.suspend_account(uuid) to authenticated;   -- 안에서 is_admin 검사

create or replace function public.resume_account(account uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
    if session_user <> 'postgres' and not private.is_admin() then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    update public.accounts set suspended_at = null where id = account;
    if not found then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
end;
$$;
revoke execute on function public.resume_account(uuid) from public, anon;
grant execute on function public.resume_account(uuid) to authenticated;   -- 안에서 is_admin 검사
-- ---------------------------------------------------------------------------------------------
-- 6. 정리 잡 (pg_cron, **UTC**) — 끊김 판정 5분 / 180일 삭제 하루(18:30 UTC = 03:30 KST) / 알림 메일 1분
-- ---------------------------------------------------------------------------------------------
create or replace function public.mark_lost() returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int;
begin
    with lost as (
        update public.runs set state = 'lost', finished_at = coalesce(heartbeat_at, received_at)
        where state = 'running' and coalesce(heartbeat_at, received_at) < now() - interval '10 minutes'
        returning id, account_id, device_id
    )
    insert into public.alerts (run_id, kind, account_id, device_id)
    select id, 'lost', account_id, device_id from lost
    on conflict do nothing;
    get diagnostics v_n = row_count;
    -- 설정 값은 서버에 남기지 않는다 (7절, 10-07) — 안 읽힌 PC 의 답·안 가져간 웹 값. 7절 전(열이 없음)에도 깨지지 않게
    -- — 깨지면 이 5분 잡의 끊김 판정·알림까지 멈춘다
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'commands' and column_name = 'payload') then
        update public.commands set payload = null
        where payload is not null and (done_at < now() - interval '2 minutes' or expires_at < now());
    end if;
    return v_n;
end;
$$;

create or replace function public.purge_old() returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int;
begin
    delete from public.runs where coalesce(finished_at, received_at) < now() - interval '180 days';
    get diagnostics v_n = row_count;
    delete from public.alerts where created_at < now() - interval '180 days';
    -- 원격 명령은 감사 기록이라 30일만 둔다 (7절). 표가 아직 없는 첫 실행에도 깨지지 않게 조건부
    if to_regclass('public.commands') is not null then
        delete from public.commands where created_at < now() - interval '30 days';
    end if;
    -- 사용법 질문 (8절): 30일 + 사람당 최근 20개 (사용자 확정 09-28) — 크기가 '사용자 수 × 20건' 으로 묶인다
    if to_regclass('public.questions') is not null then
        delete from public.questions where created_at < now() - interval '30 days';
        delete from public.questions q
        using (select id, row_number() over (partition by user_id order by id desc) as rn
               from public.questions) r
        where q.id = r.id and r.rn > 20;
    end if;
    return v_n;
end;
$$;

-- 알림 메일 (10-01 확장 — 끊김만 → 끊김·무인 실행 결과·사람 손이 필요한 일). Resend, 키는 Vault:
--   resend_api_key / alert_from (필수), dashboard_url (선택 — 없으면 아래 기본 주소). 키가 없으면 보내지 않고 늦은 것만 닫는다.
--   Resend 무료 플랜은 검증된 발신 도메인 또는 onboarding@resend.dev(계정 본인에게만) 만 허용한다.
-- 받는 사람: 그 업체 owner 와 admin 에게 **따로** 한 통씩. 직접 실행의 끊김·UAC 는 admin 만 (사람이 앞에 있었다).
-- 폭주 막기: 늦은 것은 안 보낸다(UAC 15분·그 밖 24시간) / 1분에 알림 1건(= 메일 2통, 메일 API 초당 한도) / 업체당 하루(한국 날짜) 10건.
-- '보냄' 은 pg_net 에 맡겼다는 뜻이다 — 실제 결과는 net._http_response.
create or replace function public.send_alerts() returns int
language plpgsql security definer set search_path = '' as $$
declare
    v_key     text;
    v_from    text;
    v_url     text;
    v_admins  text[];
    v_owners  text[];
    v_alert   record;
    v_subject text;
    v_body    text;
    v_n       int := 0;
begin
    -- 늦은 것은 보내지 않고 닫는다 — 메일 키를 나중에 넣어도 지난 알림이 한꺼번에 나가지 않는다
    update public.alerts set sent_at = now(), result = '오래됨'
    where sent_at is null
      and created_at < now() - case when kind = 'uac' then interval '15 minutes' else interval '24 hours' end;
    if not exists (select 1 from public.alerts where sent_at is null) then
        return 0;                                            -- 1분 잡의 대부분은 여기서 끝난다
    end if;
    select decrypted_secret into v_key  from vault.decrypted_secrets where name = 'resend_api_key';
    select decrypted_secret into v_from from vault.decrypted_secrets where name = 'alert_from';
    select decrypted_secret into v_url  from vault.decrypted_secrets where name = 'dashboard_url';
    if v_key is null or v_from is null then
        return 0;
    end if;
    v_url := rtrim(coalesce(v_url, 'https://rpa-dashboard.rpa-dashboard.workers.dev'), '/');
    select array_agg(distinct u.email) into v_admins
    from public.account_members m join auth.users u on u.id = m.user_id
    where m.role = 'admin' and u.email is not null;

    for v_alert in
        select a.id, a.kind, a.run_id, a.text, a.created_at,
               coalesce(a.account_id, r.account_id) as account_id,
               coalesce(a.device_id, r.device_id)   as device_id,
               r.state, r.trigger, r.summary, r.attention,
               coalesce(r.started_at, r.received_at) as run_at,
               (select string_agg(s.name, ', ' order by s.index) from public.run_steps s
                where s.run_id = a.run_id and s.state = 'failed') as failed_steps,
               d.name as device_name, d.next_run_at, ac.name as account_name
        from public.alerts a
        left join public.runs r      on r.id  = a.run_id
        left join public.devices d   on d.id  = coalesce(a.device_id, r.device_id)
        left join public.accounts ac on ac.id = coalesce(a.account_id, r.account_id)
        where a.sent_at is null
        order by (a.kind = 'uac') desc, a.id                 -- 기다리는 UAC 가 먼저
        limit 1
    loop
        -- 직접 실행(사람이 앞에 있다)의 끊김·UAC 는 업체 담당자에게 보내지 않는다 (admin 은 받는다)
        v_owners := null;
        if v_alert.kind not in ('lost', 'uac') or v_alert.trigger is null or v_alert.trigger in ('예약 실행', '원격 실행') then
            select array_agg(distinct u.email) into v_owners
            from public.account_members m join auth.users u on u.id = m.user_id
            where m.role = 'owner' and m.account_id = v_alert.account_id and u.email is not null;
        end if;
        if v_owners is null and v_admins is null then
            update public.alerts set sent_at = now(), result = '받는 사람 없음' where id = v_alert.id;
            continue;
        end if;
        if (select count(*) from public.alerts x
            where x.account_id = v_alert.account_id and x.result = '보냄'
              and x.sent_at >= (date_trunc('day', now() at time zone 'Asia/Seoul') at time zone 'Asia/Seoul')) >= 10 then
            update public.alerts set sent_at = now(), result = '하루 한도' where id = v_alert.id;
            continue;
        end if;

        v_subject := '[RPA] ' || case v_alert.kind
                when 'lost'  then '실행이 끝나기 전에 끊겼습니다'
                when 'uac'   then '지금 PC 에서 [예] 를 눌러 주세요'
                when 'phone' then '휴대폰 연결을 확인해 주세요'
                else case v_alert.state when 'failed'     then '무인 실행 실패'
                                        when 'unfinished' then '끝나지 않은 단계가 있습니다'
                                        else '확인할 것 ' || coalesce(v_alert.attention, 0) || '건' end
            end || ' — ' || coalesce(v_alert.device_name, 'PC');
        -- 상태 글자는 orchestrator/history.py STATE_LABELS · web/app.js RUN_STATES 와 같다
        v_body := concat_ws(E'\n',
            '업체: ' || coalesce(v_alert.account_name, ''),
            'PC: ' || coalesce(v_alert.device_name, ''),
            '시각: ' || to_char(coalesce(v_alert.run_at, v_alert.created_at) at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI')
                || ' (한국 시각)' || coalesce(' · ' || v_alert.trigger, ''),
            '',
            case v_alert.kind
                when 'lost' then 'PC 와 10분 넘게 연락이 끊겨 실행이 끝나지 않은 것으로 봤습니다. PC 가 꺼졌거나 인터넷이 끊겼거나 '
                                 || '프로그램이 닫혔을 수 있습니다. ERPia 에서 어디까지 처리됐는지 확인하세요.'
                when 'uac' then 'ERPia 업데이트 중 Windows [사용자 계정 컨트롤] 창(또는 잠금 화면)이 떠서 RPA 가 기다리고 있습니다. '
                                || 'RPA 는 이 창을 대신 누를 수 없습니다. PC 앞에서 [예] 를 누르면(잠금 화면이면 잠금을 풀면) 실행이 이어집니다. '
                                || '늦으면 이번 실행은 실패할 수 있습니다.'
                when 'phone' then '인증 문자를 받을 휴대폰이 PC 와 연결돼 있지 않습니다. 휴대폰 화면을 켜고 연결을 확인하세요 '
                                  || '([휴대폰 연결] 앱이면 ''연결됨'', USB 면 케이블과 USB 디버깅 허용). '
                                  || '그대로 두면 메일 엑셀 받기가 실패합니다 (다른 기능은 그대로 돕니다).'
                else '결과: ' || case v_alert.state when 'done' then '완료'
                                                    when 'unfinished' then '완료 (끝나지 않은 단계 있음)'
                                                    when 'failed' then '실패'
                                                    when 'cancelled' then '중단' else '' end
                     || coalesce(' — ' || v_alert.summary, '')
            end,
            case when v_alert.failed_steps is not null then '멈춘 단계: ' || v_alert.failed_steps end,
            case when coalesce(v_alert.attention, 0) > 0
                 then '확인할 것 ' || v_alert.attention || '건 — 아래 주소의 실행 상세에서 봅니다.' end,
            case when v_alert.kind = 'phone' and v_alert.next_run_at > now()
                 then '다음 예약: ' || to_char(v_alert.next_run_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI') end,
            case when v_alert.text is not null then 'PC 가 알린 내용: ' || v_alert.text end,
            '',
            '자세히 보기 (로그인): ' || v_url || case when v_alert.run_id is not null then '/#/run/' || v_alert.run_id
                                                      when v_alert.device_id is not null then '/#/device/' || v_alert.device_id
                                                      else '' end,
            '이 메일은 RPA 서버가 자동으로 보냈습니다. 받는 사람을 바꾸려면 관리자에게 알려 주세요.');
        -- 담당자와 관리자는 **따로** 보낸다 — 한 요청이면 발신 도메인을 검증하기 전에 담당자 주소 때문에
        -- 요청째 거부돼 관리자 메일까지 끊긴다 (onboarding@resend.dev 는 Resend 계정 본인에게만 간다)
        if v_owners is not null then
            perform net.http_post(
                url     := 'https://api.resend.com/emails',
                headers := jsonb_build_object('Authorization', 'Bearer ' || v_key, 'Content-Type', 'application/json'),
                body    := jsonb_build_object('from', v_from, 'to', to_jsonb(v_owners),
                                              'subject', v_subject, 'text', v_body));
        end if;
        if v_admins is not null then
            perform net.http_post(
                url     := 'https://api.resend.com/emails',
                headers := jsonb_build_object('Authorization', 'Bearer ' || v_key, 'Content-Type', 'application/json'),
                body    := jsonb_build_object('from', v_from, 'to', to_jsonb(v_admins),
                                              'subject', v_subject, 'text', v_body));
        end if;
        update public.alerts set sent_at = now(), result = '보냄' where id = v_alert.id;
        v_n := v_n + 1;
    end loop;
    return v_n;
end;
$$;

revoke execute on function public.mark_lost(), public.purge_old(), public.send_alerts()
    from public, anon, authenticated;

select cron.unschedule(jobname) from cron.job where jobname in ('rpa_mark_lost', 'rpa_purge_old', 'rpa_send_alerts');
select cron.schedule('rpa_mark_lost',   '*/5 * * * *', $$select public.mark_lost()$$);
select cron.schedule('rpa_purge_old',   '30 18 * * *', $$select public.purge_old()$$);
-- 1분 — UAC 동의는 PC 가 600초만 기다린다 (config/settings.py Timeouts.update). 보낼 것이 없으면 첫 select 에서 끝난다
select cron.schedule('rpa_send_alerts', '* * * * *', $$select public.send_alerts()$$);

-- ---------------------------------------------------------------------------------------------
-- 7. 원격 설정·명령 (09-28, 10-07 개편) — 웹에서 PC 의 설정을 보고 바꾸고, 실행·중단·예약 멈춤/재개를 시킨다.
--    서버는 PC 를 부르지 않는다. RPA 가 30초마다 `poll` 로 가져간다 (연결 방향은 그대로 RPA → 서버).
--    ★ 설정은 PC 에만 있다 (사용자 확정 10-07). 서버는 설정을 저장하지 않고 명령(`payload`)에 실어 잠깐 건넨다:
--      보기   웹 `request_settings` → PC 가 다음 poll 에 지금 값을 답한다 → 웹 `take_settings` 가 한 번 읽고 지운다
--      바꾸기 웹 `set_device_settings` → PC 가 가져갈 때 지운다
--      남은 것(안 읽힌 답·안 가져간 값)은 `mark_lost`(5분)가 지운다. payload 는 표로 못 읽는다(열 권한)
--    비밀번호·경로는 오가지 않는다 — `private.clean_settings` 가 허용한 키만 받는다 (사용자 확정 09-28).
--    바꾸는 사람은 admin 과 그 업체의 owner 뿐 (`private.can_control`). 보기는 그 업체 사람 모두.
-- ---------------------------------------------------------------------------------------------
-- 10-07 전 판: 서버가 PC 마다 설정 한 판(device_settings)을 들고 있었다 — 자료째 지운다
drop function if exists public.set_device_settings(uuid, jsonb, bigint);
drop table if exists public.device_settings;

create table if not exists public.commands (
    id          bigint generated always as identity primary key,
    account_id  uuid not null references public.accounts(id) on delete cascade,
    device_id   uuid not null references public.devices(id) on delete cascade,
    kind        text not null,
    modules     text[] not null default '{}' check (cardinality(modules) <= 10),   -- start 만. 빈 배열 = PC 의 선택
    created_by  text check (length(created_by) <= 200),
    created_at  timestamptz not null default now(),
    -- PC 가 꺼져 있다가 한참 뒤 켜져서 옛 [실행] 을 받으면 안 된다. 자동 켜기(5분) + poll(30초) 보다 넉넉히
    expires_at  timestamptz not null default now() + interval '10 minutes',
    taken_at    timestamptz,
    done_at     timestamptz,
    result      text check (length(result) <= 200)
);
-- 설정 (10-07): settings = 웹이 바꾼 값(PC 가 가져갈 때 지운다) / read_settings = PC 의 답(웹이 한 번 읽으면 지운다)
alter table public.commands add column if not exists payload jsonb check (pg_column_size(payload) <= 16384);
alter table public.commands drop constraint if exists commands_kind_check;
alter table public.commands add constraint commands_kind_check
    check (kind in ('start','stop','pause','resume','settings','read_settings'));
create index if not exists commands_device_pending_idx on public.commands(device_id) where taken_at is null;
create index if not exists commands_device_created_idx on public.commands(device_id, created_at desc);

alter table public.commands enable row level security;
drop policy if exists commands_read on public.commands;
create policy commands_read on public.commands for select to authenticated
    using (private.is_admin() or account_id in (select private.my_account_ids()));
-- 쓰기 정책은 없다 — 아래 함수만 쓴다. payload(설정 값)는 열 권한에서 뺀다 — `take_settings` 로 한 번만 읽는다
revoke all on public.commands from anon, authenticated;
grant select (id, account_id, device_id, kind, modules, created_by, created_at, expires_at, taken_at, done_at, result)
    on public.commands to authenticated;

-- 이 업체의 PC 를 제어할 수 있나 — admin 이거나 그 업체의 owner
create or replace function private.can_control(account uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (select 1 from public.account_members m
                   where m.user_id = (select auth.uid())
                     and (m.role = 'admin'
                          or (m.role = 'owner' and m.account_id = account
                              -- 중지된 업체의 owner 는 제어 못 한다 (10-07). admin 은 그대로
                              and not exists (select 1 from public.accounts a
                                              where a.id = account and a.suspended_at is not null))));
$$;
revoke execute on function private.can_control(uuid) from public, anon, authenticated;

-- 문자열 목록 검사: 개수·길이·허용 값
create or replace function private.text_list_ok(v jsonb, min_n int, max_n int, max_len int,
                                                allowed text[] default null) returns boolean
language sql immutable set search_path = '' as $$
    select jsonb_typeof(v) = 'array'
       and jsonb_array_length(v) between min_n and max_n
       and not exists (select 1 from jsonb_array_elements(v) x
                       where jsonb_typeof(x) <> 'string'
                          or length(x #>> '{}') > max_len
                          or (allowed is not null and not ((x #>> '{}') = any (allowed))));
$$;

-- 기능 목록 (10-08) — 원본 네 기능 + 업체 기능(`cx_`, 그 업체 빌드만 안다 — `docs/CUSTOMERS.md`). 겹치지 않게, 8개까지
create or replace function private.modules_ok(v jsonb, min_n int) returns boolean
language sql immutable set search_path = '' as $$
    select jsonb_typeof(v) = 'array'
       and jsonb_array_length(v) between min_n and 8
       and (select count(distinct x) from jsonb_array_elements(v) x) = jsonb_array_length(v)
       and not exists (select 1 from jsonb_array_elements(v) x
                       where jsonb_typeof(x) <> 'string'
                          or not ((x #>> '{}') in ('mail','orders','logistics_wait','logistics')
                                  or (x #>> '{}') ~ '^cx_[a-z0-9_]{1,30}$'));
$$;

-- 업체 칸 값 (10-08) — 서버는 업체 칸 정의를 모른다. 종류와 크기만 본다 (고르는 값 등은 PC 가 거른다, `orchestrator/remote.py`).
-- 상한은 remote.CX_TEXT·CX_ITEMS·CX_ITEM 과 같다
create or replace function private.cx_value_ok(v jsonb) returns boolean
language sql immutable set search_path = '' as $$
    select case jsonb_typeof(v)
        when 'null' then true
        when 'boolean' then true
        when 'string' then length(v #>> '{}') <= 300
        when 'array' then private.text_list_ok(v, 0, 200, 100)
        else false
    end;
$$;

-- PC 가 설정 답에 싣는 '웹이 그릴 것' (10-08) — 업체 이름·업체 칸 정의·기능 이름. 웹은 textContent 로만 쓴다.
-- 틀리면 null (그 부분만 버린다 — 설정 답은 그대로 쓴다)
create or replace function private.described_ok(r jsonb) returns jsonb
language sql immutable set search_path = '' as $$
    select case when
        jsonb_typeof(r->'customer') = 'string' and length(r->>'customer') <= 40
        and jsonb_typeof(r->'fields') = 'array' and jsonb_array_length(r->'fields') <= 20
        and not exists (select 1 from jsonb_array_elements(r->'fields') f
                        where jsonb_typeof(f) <> 'object'
                           or (f->>'key') is null or (f->>'key') !~ '^cx_[a-z0-9_]{1,40}$'
                           or jsonb_typeof(f->'label') <> 'string' or length(f->>'label') > 40
                           or coalesce(f->>'kind', '') not in ('choice','list','text','bool')
                           or not private.text_list_ok(coalesce(f->'choices', '[]'::jsonb), 0, 10, 30))
        and jsonb_typeof(r->'modules') = 'array' and jsonb_array_length(r->'modules') <= 8
        and not exists (select 1 from jsonb_array_elements(r->'modules') m
                        where not private.text_list_ok(m, 2, 2, 40)
                           or not private.modules_ok(jsonb_build_array(m->0), 1))
    then jsonb_build_object('customer', r->'customer', 'fields', r->'fields', 'modules', r->'modules')
    end;
$$;

-- 웹·PC 가 올리는 설정을 거른다. **모르는 키는 요청 전체를 거부한다** — 비밀번호 키도 여기서 막힌다.
-- 키 목록은 `orchestrator/remote.py` 의 REMOTE_KEYS 와 같다. 예약 줄의 꼴은 PC 가 다시 검사한다 (틀린 줄은 버린다).
create or replace function private.clean_settings(s jsonb) returns jsonb
language plpgsql immutable set search_path = '' as $$
declare
    v_key text;
    v_val jsonb;
    v_ok  boolean;
begin
    -- 15KB — 키마다 상한을 다 채운 값(보류 코드 200×50자 등)도 들어간다. PC 의 답이 통째로 버려지지 않게 (10-07 검토).
    -- commands.payload check(16KB) 보다 작아야 한다 — 답은 이것 + 고정 기능
    if s is null or jsonb_typeof(s) <> 'object' or pg_column_size(s) > 15360 then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    for v_key, v_val in select * from jsonb_each(s) loop
        v_ok := case v_key
            when 'run_modules' then private.modules_ok(v_val, 1)
            when 'collect_sources' then private.text_list_ok(v_val, 1, 2, 10, array['excel','site'])
            when 'auto_run_times' then private.text_list_ok(v_val, 0, 12, 60)
            when 'hold_exclude_codes' then private.text_list_ok(v_val, 0, 200, 50)
            when 'auto_run_enabled' then jsonb_typeof(v_val) = 'boolean'
            when 'auto_run_mode' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('daily','interval')
            when 'auto_run_interval_minutes' then jsonb_typeof(v_val) = 'number'
                 and (v_val #>> '{}')::numeric between 10 and 1440
                 and (v_val #>> '{}')::numeric = trunc((v_val #>> '{}')::numeric)
            when 'logistics_mode' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('자동','수동')
            -- 매출처리 방식 (10-01) — automation/order_mapping.SALES_MODES = orchestrator/remote._CHOICES
            when 'sales_mode' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('전체','선택주문')
            when 'sms_source' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('phonelink','adb','auto')
            when 'adb_connection' then jsonb_typeof(v_val) = 'string' and (v_val #>> '{}') in ('usb','wireless')
            when 'delivery_company' then jsonb_typeof(v_val) = 'null'
                 or (jsonb_typeof(v_val) = 'string' and length(v_val #>> '{}') <= 50)
            when 'delivery_box' then jsonb_typeof(v_val) = 'null'
                 or (jsonb_typeof(v_val) = 'string' and length(v_val #>> '{}') <= 50)
            when 'adb_wireless_address' then jsonb_typeof(v_val) = 'null'
                 or (jsonb_typeof(v_val) = 'string' and length(v_val #>> '{}') <= 50)
            -- 업체 칸 (10-08) — 키 꼴과 값의 종류·크기만. 그 업체 빌드의 PC 가 칸 정의로 다시 거른다
            else v_key ~ '^cx_[a-z0-9_]{1,40}$' and private.cx_value_ok(v_val)
        end;
        if not coalesce(v_ok, false) then
            raise exception 'bad request' using errcode = 'PT400';
        end if;
    end loop;
    return s;
end;
$$;
revoke execute on function private.text_list_ok(jsonb, int, int, int, text[]),
                           private.modules_ok(jsonb, int), private.cx_value_ok(jsonb), private.described_ok(jsonb),
                           private.clean_settings(jsonb) from public, anon, authenticated;

-- 웹이 부른다. 바꿀 값만 명령에 실어 둔다 — PC 가 다음 poll 에 가져가 제 설정 파일에 저장하고, 서버는 그때 지운다.
-- 그 PC 가 실행 중이면 423 (09-29 사용자 요청. 웹도 칸을 잠근다). 2분 안에 안 가져가면 버린다 — PC 가 꺼졌다.
-- 기능 고정 빌드의 run_modules 는 PC 가 버린다 (서버는 빌드 구성을 모른다)
create or replace function public.set_device_settings(device uuid, settings jsonb) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
    v_dev public.devices%rowtype;
    v_id  bigint;
begin
    select * into v_dev from public.devices d where d.id = device and d.revoked_at is null;
    if not found or not private.can_control(v_dev.account_id) then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if exists (select 1 from public.runs r where r.device_id = v_dev.id and r.state = 'running') then
        raise exception 'running' using errcode = 'PT423';
    end if;
    if settings = '{}'::jsonb then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    if (select count(*) from public.commands c
        where c.device_id = v_dev.id and c.taken_at is null and c.expires_at > now()) >= 5 then
        raise exception 'too many' using errcode = 'PT429';
    end if;
    insert into public.commands (account_id, device_id, kind, payload, created_by, expires_at)
    values (v_dev.account_id, v_dev.id, 'settings', private.clean_settings(settings),
            left(coalesce((select u.email from auth.users u where u.id = (select auth.uid())), '웹'), 200),
            now() + interval '2 minutes')
    returning id into v_id;
    return v_id;
end;
$$;
revoke execute on function public.set_device_settings(uuid, jsonb) from public, anon;
grant execute on function public.set_device_settings(uuid, jsonb) to authenticated;   -- 안에서 권한 검사

-- 웹이 부른다 (설정 화면을 열 때). 그 업체 사람이면 누구나 — 보기만 하는 사람도 설정을 본다.
-- PC 가 다음 poll 에 지금 값을 답하고, 웹은 `take_settings` 로 기다렸다 읽는다. 2분 안에 답이 없으면 PC 가 꺼진 것
create or replace function public.request_settings(device uuid) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
    v_dev public.devices%rowtype;
    v_id  bigint;
begin
    select * into v_dev from public.devices d where d.id = device and d.revoked_at is null;
    if not found or not (private.is_admin() or v_dev.account_id in (select private.my_account_ids())) then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if (select count(*) from public.commands c
        where c.device_id = v_dev.id and c.taken_at is null and c.expires_at > now()) >= 5 then
        raise exception 'too many' using errcode = 'PT429';
    end if;
    insert into public.commands (account_id, device_id, kind, created_by, expires_at)
    values (v_dev.account_id, v_dev.id, 'read_settings',
            left(coalesce((select u.email from auth.users u where u.id = (select auth.uid())), '웹'), 200),
            now() + interval '2 minutes')
    returning id into v_id;
    return v_id;
end;
$$;
revoke execute on function public.request_settings(uuid) from public, anon;
grant execute on function public.request_settings(uuid) to authenticated;   -- 안에서 권한 검사

-- 웹이 부른다. PC 의 답을 **한 번만** 돌려주고 지운다 — {settings, locked_modules}.
-- 아직이면 null, 끝났는데 값이 없으면 {error: 결과}, 답 없이 시간이 지났으면 {error: 'expired'}
create or replace function public.take_settings(command bigint) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    v_cmd public.commands%rowtype;
begin
    select * into v_cmd from public.commands c where c.id = command and c.kind = 'read_settings' for update;
    if not found or not (private.is_admin() or v_cmd.account_id in (select private.my_account_ids())) then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if v_cmd.payload is not null then
        update public.commands c set payload = null where c.id = v_cmd.id;
        return v_cmd.payload;
    end if;
    if v_cmd.done_at is not null then
        -- '보냄' 인데 값이 없다 = 답이 왔으나 읽기 전에 정리됐다 (mark_lost). 그 밖에는 PC·서버가 남긴 사유
        return jsonb_build_object('error', case when v_cmd.result = '보냄' then 'expired' else coalesce(v_cmd.result, '') end);
    end if;
    if v_cmd.expires_at <= now() then
        return jsonb_build_object('error', 'expired');
    end if;
    return null;
end;
$$;
revoke execute on function public.take_settings(bigint) from public, anon;
grant execute on function public.take_settings(bigint) to authenticated;   -- 안에서 권한 검사

-- 웹이 부른다. 정해진 네 가지만. PC 는 다음 poll(30초 안)에 가져간다
create or replace function public.send_command(device uuid, kind text, modules text[] default '{}')
returns bigint
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_variable
declare
    v_dev public.devices%rowtype;
    v_id  bigint;
begin
    select * into v_dev from public.devices d where d.id = device and d.revoked_at is null;
    if not found or not private.can_control(v_dev.account_id) then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if kind is null or kind not in ('start','stop','pause','resume')
       or not private.modules_ok(to_jsonb(coalesce(modules, '{}'::text[])), 0) then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    -- 쌓아 두지 않는다. 가져가지 않은 명령이 다섯 개면 PC 가 꺼져 있는 것이다
    if (select count(*) from public.commands c
        where c.device_id = v_dev.id and c.taken_at is null and c.expires_at > now()) >= 5 then
        raise exception 'too many' using errcode = 'PT429';
    end if;
    insert into public.commands (account_id, device_id, kind, modules, created_by)
    values (v_dev.account_id, v_dev.id, kind, coalesce(modules, '{}'::text[]),
            left(coalesce((select u.email from auth.users u where u.id = (select auth.uid())), '웹'), 200))
    returning id into v_id;
    return v_id;
end;
$$;
revoke execute on function public.send_command(uuid, text, text[]) from public, anon;
grant execute on function public.send_command(uuid, text, text[]) to authenticated;

-- RPA 가 30초마다 부른다. POST /rest/v1/rpc/poll
--   results  [{id, result, settings?, locked_modules?}] 지난번에 받은 명령의 결과. read_settings 의 답에는 지금 설정과
--            기능 고정 빌드의 기능이 실린다 (웹이 한 번 읽으면 지운다). 값이 규칙에 어긋나면 그 답만 버린다
--   known_version·pushed·locked_modules 는 10-07 전 PC 가 보낸다 — 받기만 하고 쓰지 않는다 (설정을 서버에 두지 않는다)
-- 돌려주는 것: commands [{id, kind, modules, settings}] — settings 는 웹이 바꾼 값 (settings 명령). 건넨 뒤 지운다
create or replace function public.poll(device_key text, machine text, known_version bigint default 0,
                                       pushed jsonb default null, locked_modules text[] default null,
                                       results jsonb default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_variable
declare
    v_dev   public.devices%rowtype;
    v_res   jsonb;
    v_reply jsonb;
    v_cmds  jsonb;
begin
    if results is not null and (jsonb_typeof(results) <> 'array' or jsonb_array_length(results) > 20) then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    v_dev := private.bind_device(device_key, machine);
    update public.devices set last_seen_at = now() where id = v_dev.id;

    if results is not null then
        for v_res in select * from jsonb_array_elements(results) loop
            v_reply := null;
            if v_res ? 'settings' then
                begin
                    v_reply := jsonb_build_object('settings', private.clean_settings(v_res->'settings'),
                        'locked_modules', case when private.modules_ok(v_res->'locked_modules', 1)
                                          then v_res->'locked_modules' end)
                           -- 업체 빌드만 (10-08) — 업체 이름·칸 정의·기능 이름. 틀리면 그 부분만 뺀다
                           || coalesce(private.described_ok(v_res), '{}'::jsonb);
                exception when others then
                    v_reply := null;            -- 규칙에 어긋난 값 — 이 답만 버린다 (명령 결과는 계속 받는다)
                end;
            end if;
            update public.commands c set done_at = now(),
                   result = case when v_res ? 'settings' and v_reply is null
                                 then '설정 값이 서버 규칙에 맞지 않아 버렸다' else left(v_res->>'result', 200) end,
                   payload = case when c.kind = 'read_settings' then v_reply end
            where c.id = (v_res->>'id')::bigint and c.device_id = v_dev.id and c.done_at is null;
        end loop;
    end if;

    -- 만료된 것은 결과를 적고 닫는다 (실어 둔 값도 지운다). 남은 것을 가져간다 (한 번만 — taken_at)
    update public.commands c set taken_at = now(), done_at = now(), payload = null,
           result = '만료 — PC 가 제때 가져가지 않았다'
    where c.device_id = v_dev.id and c.taken_at is null and c.expires_at <= now();
    with picked as (
        select c.id, c.kind, c.modules, c.payload from public.commands c
        where c.device_id = v_dev.id and c.taken_at is null
        for update
    ), taken as (
        update public.commands c set taken_at = now(), payload = null      -- 웹이 바꾼 값은 건넨 뒤 지운다
        from picked p where c.id = p.id
        returning p.id, p.kind, p.modules, p.payload
    )
    select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'kind', t.kind, 'modules', to_jsonb(t.modules),
                                                 'settings', t.payload) order by t.id), '[]'::jsonb)
    into v_cmds from taken t;

    return jsonb_build_object('commands', v_cmds, 'server_time', now());
end;
$$;
revoke execute on function public.poll(text, text, bigint, jsonb, text[], jsonb) from public, authenticated;
grant execute on function public.poll(text, text, bigint, jsonb, text[], jsonb) to anon;

-- ---------------------------------------------------------------------------------------------
-- 8. 사용법 질문 (09-28, 사용자 확정) — 웹 사용자가 RPA 사용법을 물으면 LLM 전용 PC 가 답한다.
--    서버는 LLM PC 를 부르지 않는다. 워커(`llm/worker.py`)가 `llm_take` 로 가져가고(최대 2초 기다림)
--    `llm_write` 로 답을 흘려 쓴다. 웹은 자기 질문 행을 짧게 다시 읽어 답이 자라는 것을 보여 준다.
--    워커 키는 평문을 두지 않는다 (sha256 hex — 기기 키와 같다). 워커 등록은 관리자가 해시만 넣는다:
--      insert into public.llm_workers (name, key_hash) values ('LLM PC', '<python -m llm.worker --init 이 찍은 값>');
--    LLM 은 설명서만 보고 답한다 — 이 표 말고는 읽지도 쓰지도 않는다 (SERVER_PLAN D-6).
-- ---------------------------------------------------------------------------------------------
create table if not exists public.llm_workers (
    id           uuid primary key default gen_random_uuid(),
    name         text not null check (length(name) <= 100),
    key_hash     text not null unique,                                -- sha256(워커 키) hex. 평문 없음
    last_seen_at timestamptz,                                         -- llm_take 가 준다. 15초 안이면 켜짐
    revoked_at   timestamptz,
    created_at   timestamptz not null default now()
);

create table if not exists public.questions (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references auth.users(id) on delete cascade,
    account_id  uuid references public.accounts(id) on delete cascade,    -- admin 은 null
    question    text not null check (length(question) between 1 and 500),
    answer      text not null default '' check (length(answer) <= 4000),
    status      text not null default 'queued'
                check (status in ('queued','answering','done','failed','expired')),
    worker_id   uuid references public.llm_workers(id) on delete set null,
    created_at  timestamptz not null default now(),
    taken_at    timestamptz,
    updated_at  timestamptz,
    done_at     timestamptz
);
create index if not exists questions_queued on public.questions (id) where status = 'queued';
create index if not exists questions_user on public.questions (user_id, id desc);

alter table public.llm_workers enable row level security;
alter table public.questions  enable row level security;
-- 자기 질문만 본다. admin 은 설명서를 고치려고 전부 본다. 워커 표는 정책이 없어 아무도 못 읽는다 (RPC 만)
drop policy if exists questions_read on public.questions;
create policy questions_read on public.questions for select to authenticated
    using (user_id = (select auth.uid()) or private.is_admin());
revoke all on public.llm_workers from anon, authenticated;
revoke all on public.questions from anon, authenticated;
grant select on public.questions to authenticated;

create or replace function private.llm_worker(worker_key text) returns uuid
language plpgsql stable security definer set search_path = '' as $$
declare v_id uuid;
begin
    select w.id into v_id from public.llm_workers w
    where w.key_hash = encode(sha256(convert_to(coalesce(worker_key, ''), 'UTF8')), 'hex')
      and w.revoked_at is null;
    if v_id is null then
        raise exception 'unauthorized' using errcode = 'PT401';
    end if;
    return v_id;
end;
$$;

-- 웹이 부른다. 답하는 PC 가 꺼져 있으면(503) 받지 않는다 — 쌓아 두면 한참 뒤 엉뚱한 때 답이 온다.
-- 한 사람에 한 번에 하나(409), 하루 50개(429). 워커가 죽어 남은 것은 세지 않는다
create or replace function public.ask_question(question text) returns bigint
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_variable
declare
    v_uid uuid := (select auth.uid());
    v_q   text := btrim(coalesce(question, ''));
    v_id  bigint;
begin
    if v_uid is null or not exists (select 1 from public.account_members m where m.user_id = v_uid) then
        raise exception 'forbidden' using errcode = 'PT403';
    end if;
    if length(v_q) < 1 or length(v_q) > 500 then
        raise exception 'bad request' using errcode = 'PT400';
    end if;
    if not exists (select 1 from public.llm_workers w
                   where w.revoked_at is null and w.last_seen_at > now() - interval '15 seconds') then
        raise exception 'no worker' using errcode = 'PT503';
    end if;
    perform pg_advisory_xact_lock(hashtextextended(v_uid::text, 8));   -- 같은 사람이 두 번 눌러도 하나만
    if exists (select 1 from public.questions q where q.user_id = v_uid
               and ((q.status = 'queued' and q.created_at > now() - interval '60 seconds')
                 or (q.status = 'answering' and q.updated_at > now() - interval '2 minutes'))) then
        raise exception 'busy' using errcode = 'PT409';
    end if;
    if (select count(*) from public.questions q
        where q.user_id = v_uid and q.created_at > now() - interval '1 day') >= 50 then
        raise exception 'too many' using errcode = 'PT429';
    end if;
    insert into public.questions (user_id, account_id, question)
    values (v_uid, (select m.account_id from public.account_members m
                    where m.user_id = v_uid and m.account_id is not null order by m.id limit 1), v_q)
    returning id into v_id;
    return v_id;
end;
$$;
revoke execute on function public.ask_question(text) from public, anon;
grant execute on function public.ask_question(text) to authenticated;

-- 웹이 부른다. 질문 칸 옆에 "답변 가능 / 답하는 PC 꺼짐" 을 보여 준다
create or replace function public.llm_online() returns boolean
language sql stable security definer set search_path = '' as $$
    select exists (select 1 from public.llm_workers w
                   where w.revoked_at is null and w.last_seen_at > now() - interval '15 seconds');
$$;
revoke execute on function public.llm_online() from public, anon;
grant execute on function public.llm_online() to authenticated;

-- 늦거나 워커가 죽어 남은 질문을 닫는다 (웹은 이 상태를 보고 안내한다). llm_take 가 부르고,
-- 워커가 꺼져 있어도 닫히게 pg_cron 이 1분마다 부른다 (runs 의 mark_lost 와 같은 자리)
create or replace function public.expire_questions() returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int; v_m int;
begin
    update public.questions set status = 'expired', done_at = now()
    where status = 'queued' and created_at < now() - interval '60 seconds';
    get diagnostics v_n = row_count;
    update public.questions set status = 'failed', done_at = now()
    where status = 'answering' and updated_at < now() - interval '2 minutes';
    get diagnostics v_m = row_count;
    return v_n + v_m;
end;
$$;
revoke execute on function public.expire_questions() from public, anon, authenticated;
select cron.unschedule(jobname) from cron.job where jobname = 'rpa_expire_questions';
select cron.schedule('rpa_expire_questions', '* * * * *', $$select public.expire_questions()$$);

-- 워커가 쉬지 않고 부른다. 질문이 없으면 2초까지 기다렸다가 null (anon 은 3초에 끊긴다).
-- 기다리는 동안 0.2초마다 본다 — 질문이 들어오면 0.2초 안에 가져간다. 한 번에 하나, 오래된 것부터
create or replace function public.llm_take(worker_key text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    v_worker uuid := private.llm_worker(worker_key);
    v_until  timestamptz := clock_timestamp() + interval '2 seconds';
    v_q      public.questions%rowtype;
    v_prev   public.questions%rowtype;
begin
    update public.llm_workers set last_seen_at = now() where id = v_worker;
    perform public.expire_questions();
    loop
        update public.questions q set status = 'answering', worker_id = v_worker,
               taken_at = clock_timestamp(), updated_at = clock_timestamp()
        where q.id = (select n.id from public.questions n where n.status = 'queued'
                      order by n.id limit 1 for update skip locked)
        returning q.* into v_q;
        exit when found or clock_timestamp() >= v_until;
        perform pg_sleep(0.2);
    end loop;
    if v_q.id is null then
        return null;
    end if;
    -- 바로 앞 질문 하나 — "그럼 그건요?" 같은 이어 묻기용 (같은 사람, 10분 안)
    select * into v_prev from public.questions p
    where p.user_id = v_q.user_id and p.id < v_q.id and p.status = 'done'
      and p.done_at > now() - interval '10 minutes'
    order by p.id desc limit 1;
    return jsonb_build_object('id', v_q.id, 'question', v_q.question,
                              'prev_question', v_prev.question, 'prev_answer', v_prev.answer);
end;
$$;
revoke execute on function public.llm_take(text) from public, authenticated;
grant execute on function public.llm_take(text) to anon;

-- 워커가 답을 흘려 쓴다 (지금까지의 전체 글). final 이면 닫는다.
-- false 를 돌려주면 그 질문은 이미 닫혔다 (만료·실패) — 워커는 만들기를 멈춘다
create or replace function public.llm_write(worker_key text, question_id bigint, answer text,
                                            final boolean default false, failed boolean default false)
returns boolean
language plpgsql security definer set search_path = '' as $$
#variable_conflict use_variable
declare
    v_worker uuid := private.llm_worker(worker_key);
begin
    -- 답을 만드는 동안은 llm_take 를 안 부른다. 긴 답 중에 "꺼짐" 으로 보이지 않게 여기서도 준다 (5초에 한 번)
    update public.llm_workers set last_seen_at = now()
    where id = v_worker and last_seen_at < now() - interval '5 seconds';
    update public.questions q
    set answer = left(coalesce(answer, ''), 4000),
        updated_at = clock_timestamp(),
        status = case when final then (case when failed then 'failed' else 'done' end) else q.status end,
        done_at = case when final then clock_timestamp() end
    where q.id = question_id and q.worker_id = v_worker and q.status = 'answering';
    return found;
end;
$$;
revoke execute on function public.llm_write(text, bigint, text, boolean, boolean) from public, authenticated;
grant execute on function public.llm_write(text, bigint, text, boolean, boolean) to anon;

-- ---------------------------------------------------------------------------------------------
-- 9. 사용량(트래픽) — 과금 근거 (09-28, 사용자 요청. 규칙은 docs/BILLING.md 1·2절)
--    행위 수 = 접속한 사이트 수 (09-30 사용자 확정). 메일 엑셀 받기 = 메일 사이트 한 곳 = 1 — 처리한 메일
--              (받기 실패 제외, 엑셀 아니었던 것 포함)이 한 통이라도 있으면 1, 없으면 0. 메일 수와 무관하다 /
--              주문수집·매출처리·물류대기·물류관리: 그 기능이 끝까지 된 실행 1회. 실패·건너뜀은 세지 않는다.
--    run_steps 에서 센다 (메일은 받은메일함 단계의 전체 - 실패 — RPA 가 09-28 빌드부터 이 건수를 보낸다).
--    runs 는 180일 뒤 지워지지만 이 표는 지우지 않는다 — 과금 근거가 남아야 한다.
--    날짜는 한국 시각, 서버가 받은 시각(received_at) 기준 (PC 시계는 틀릴 수 있다).
--    사용자는 자기 업체만, admin 은 전부 본다 (RLS). 쓰기는 cron 의 refresh_usage 만 한다.
-- ---------------------------------------------------------------------------------------------
create table if not exists public.usage_daily (
    device_id      uuid not null references public.devices(id) on delete cascade,
    day            date not null,                                          -- 한국 시각 날짜
    account_id     uuid not null references public.accounts(id) on delete cascade,
    runs           int not null default 0,        -- 끝난 실행 수 (참고. 건너뛴 예약 회차·진행 중은 빼고)
    mail           int not null default 0,
    orders         int not null default 0,
    logistics_wait int not null default 0,
    logistics      int not null default 0,
    actions        int generated always as (mail + orders + logistics_wait + logistics) stored,
    updated_at     timestamptz not null default now(),
    primary key (device_id, day)
);
create index if not exists usage_daily_account_day on public.usage_daily (account_id, day);

alter table public.usage_daily enable row level security;
drop policy if exists usage_daily_read on public.usage_daily;
create policy usage_daily_read on public.usage_daily for select to authenticated
    using (private.is_admin() or account_id in (select private.my_account_ids()));
revoke all on public.usage_daily from anon, authenticated;
grant select on public.usage_daily to authenticated;

-- 최근에 받았거나 움직인 실행이 있는 (PC, 날) 을 통째로 다시 센다 — 같은 날을 몇 번 다시 세도 결과가 같다.
-- 늦게 온 보고(outbox)도 받은 뒤 since 안에 다시 세어진다
create or replace function public.refresh_usage(since interval default interval '3 days') returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int;
begin
    -- 180일 정리(purge_old)로 반쯤 지워진 날을 다시 세면 적은 값으로 덮어쓴다 — 그보다 앞은 보지 않는다
    since := least(since, interval '179 days');
    with touched as (
        select distinct r.device_id, (r.received_at at time zone 'Asia/Seoul')::date as day
        from public.runs r
        where r.received_at > now() - since or r.heartbeat_at > now() - since
           or r.finished_at > now() - since
    ), per_run as (
        select r.device_id, t.day,
               (r.state in ('done','unfinished','failed','cancelled','lost'))::int as finished,
               -- 메일 사이트 = 접속 사이트 1곳. 처리한 메일이 0통이면 세지 않는다 (09-30)
               coalesce(max(case when s.step_id = 'mailbox' and s.state = 'done'
                                  and coalesce(s.total_items, 0) - coalesce(s.failed_items, 0) > 0 then 1 end), 0) as mail,
               coalesce(max(case when s.step_id = 'sales' and s.state = 'done' then 1 end), 0) as orders,
               coalesce(max(case when s.step_id = 'logistics_wait' and s.state = 'done' then 1 end), 0) as logistics_wait,
               -- 물류관리 저장 실패는 RPA 가 failed_items=1 로 알린다 (실패는 세지 않는다)
               coalesce(max(case when s.step_id = 'logistics' and s.state = 'done'
                                  and coalesce(s.failed_items, 0) = 0 then 1 end), 0) as logistics
        from touched t
        join public.runs r on r.device_id = t.device_id
                          and (r.received_at at time zone 'Asia/Seoul')::date = t.day
        left join public.run_steps s on s.run_id = r.id
        group by r.id, r.device_id, t.day, r.state
    )
    -- 업체는 PC 의 지금 소속 — (PC, 날) 하나에 한 줄이어야 on conflict 가 두 번 부딪히지 않는다
    insert into public.usage_daily as u (device_id, day, account_id, runs, mail, orders, logistics_wait, logistics, updated_at)
    select p.device_id, p.day, d.account_id, sum(p.finished), sum(p.mail), sum(p.orders),
           sum(p.logistics_wait), sum(p.logistics), now()
    from per_run p join public.devices d on d.id = p.device_id
    group by p.device_id, p.day, d.account_id
    on conflict (device_id, day) do update
        set runs = excluded.runs, mail = excluded.mail, orders = excluded.orders,
            logistics_wait = excluded.logistics_wait, logistics = excluded.logistics, updated_at = now();
    get diagnostics v_n = row_count;
    return v_n;
end;
$$;
revoke execute on function public.refresh_usage(interval) from public, anon, authenticated;
select cron.unschedule(jobname) from cron.job where jobname = 'rpa_refresh_usage';
select cron.schedule('rpa_refresh_usage', '*/10 * * * *', $$select public.refresh_usage()$$);
-- 처음 적용할 때 지난 실행을 채운다 (몇 번 다시 실행해도 결과가 같다. 179일 한도는 함수 안에서 건다)
select public.refresh_usage(interval '179 days');
