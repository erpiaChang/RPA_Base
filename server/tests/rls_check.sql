-- RLS·권한 거부 시험 (10-07). 한 DO 블록 = 한 트랜잭션. 끝에서 일부러 예외를 던져 **전부 되돌린다.**
--   결과는 오류 문구로 온다: 'RLS_CHECK_OK n/n (되돌림)' = 통과 / 'RLS_CHECK_FAIL ...' = 실패 목록. 둘 다 오류로 보이는 것이 정상.
--   남는 흔적: identity 번호(account_members·commands)가 몇 칸 건너뜀뿐. 가짜 값만 쓴다.
--   실행: 사용자에게 이 SQL 을 보인 뒤, execute_sql 본문 첫 줄에 `-- APPROVED-SQL: RLS 시험 (되돌림)` 을 붙인다.
--   업체 중지(accounts.suspended_at)가 적용돼 있으면 2단계(중지된 업체의 401·403)도 돈다.
--   1단계 끝에 설정 주고받기(10-07 — 서버는 설정을 저장하지 않는다)를 한 줄씩 본다: 웹 요청 → PC 답 → 한 번만 읽힘.
--   session_user 는 postgres 로 고정이라 register_build·suspend_account 안의 '관리자 아니면 403' 분기는 여기서 못 본다.
--   ★ VALUES 줄 순서에 기대는 사례가 있다 (ingest 로 PC 를 묶은 뒤 다른 PC) — 줄 순서를 바꾸지 않는다.
do $do$
declare
    ua  constant uuid := 'a0000000-0000-4000-8000-0000000000a1';   -- 업체 A owner
    uv  constant uuid := 'a0000000-0000-4000-8000-0000000000a2';   -- 업체 A viewer
    ub  constant uuid := 'b0000000-0000-4000-8000-0000000000b1';   -- 업체 B owner
    uad constant uuid := 'c0000000-0000-4000-8000-0000000000c1';   -- admin
    acc_a constant uuid := 'a1000000-0000-4000-8000-000000000001';
    acc_b constant uuid := 'b1000000-0000-4000-8000-000000000002';
    dev_a constant uuid := 'a2000000-0000-4000-8000-000000000001';
    dev_b constant uuid := 'b2000000-0000-4000-8000-000000000002';
    run_a constant uuid := 'a3000000-0000-4000-8000-000000000001';
    run_b constant uuid := 'b3000000-0000-4000-8000-000000000002';
    key_a constant text := 'bld_rls_check_a';
    key_b constant text := 'bld_rls_check_b';
    r     record;
    p     int;
    got   text;
    pass  int := 0;
    total int := 0;
    bad   text := '';
    has_suspend boolean;
    cmd   bigint;
    cmd2  bigint;
    put   bigint;
    js    jsonb;
begin
    -- 가짜 자료 (postgres 권한). account_members.user_id 가 auth.users 를 참조하므로 사용자 행이 필요하다
    insert into auth.users (id, email) values
        (ua, 'rls-a@example.com'), (uv, 'rls-v@example.com'), (ub, 'rls-b@example.com'), (uad, 'rls-admin@example.com');
    insert into public.accounts (id, name) values (acc_a, '__rls_check_A'), (acc_b, '__rls_check_B');
    insert into public.account_members (user_id, account_id, role) values
        (ua, acc_a, 'owner'), (uv, acc_a, 'viewer'), (ub, acc_b, 'owner'), (uad, null, 'admin');
    insert into public.devices (id, account_id, name, key_hash) values
        (dev_a, acc_a, 'rls-a', encode(sha256(convert_to(key_a, 'UTF8')), 'hex')),
        (dev_b, acc_b, 'rls-b', encode(sha256(convert_to(key_b, 'UTF8')), 'hex'));
    insert into public.runs (id, account_id, device_id, state) values
        (run_a, acc_a, dev_a, 'done'), (run_b, acc_b, dev_b, 'done');
    insert into public.run_steps (run_id, step_id, seq) values (run_a, 's', 1), (run_b, 's', 1);
    insert into public.run_logs (run_id, seq) values (run_a, 1), (run_b, 1);
    insert into public.run_attention (run_id, seq) values (run_a, 1), (run_b, 1);
    insert into public.alerts (run_id, kind, account_id, device_id) values (run_b, 'result', acc_b, dev_b);
    insert into public.commands (account_id, device_id, kind) values (acc_b, dev_b, 'stop');

    has_suspend := exists (select 1 from information_schema.columns
                           where table_schema = 'public' and table_name = 'accounts' and column_name = 'suspended_at');

    for p in 1..2 loop
        if p = 2 then
            if not has_suspend then exit; end if;                       -- 업체 중지 적용 전이면 2단계는 건너뜀
            execute 'update public.accounts set suspended_at = now() where id = $1' using acc_a;
        end if;
        for r in select * from (values
            -- (단계, 이름, 역할, 사용자, SQL, 기대)
            -- ===== anon: 표 권한 0 =====
            (1, 'anon 은 accounts 를 못 읽는다', 'anon', null::uuid, $q$select count(*)::text from public.accounts$q$, 'err:42501'),
            (1, 'anon 은 devices 를 못 읽는다', 'anon', null, $q$select count(*)::text from public.devices$q$, 'err:42501'),
            (1, 'anon 은 runs 를 못 읽는다', 'anon', null, $q$select count(*)::text from public.runs$q$, 'err:42501'),
            (1, 'anon 은 account_members 를 못 읽는다', 'anon', null, $q$select count(*)::text from public.account_members$q$, 'err:42501'),
            (1, 'anon 은 alerts 를 못 읽는다', 'anon', null, $q$select count(*)::text from public.alerts$q$, 'err:42501'),
            (1, 'device_settings 표는 없다 (설정은 PC 에만)', 'anon', null, $q$select coalesce(to_regclass('public.device_settings')::text, 'none')$q$, 'none'),
            (1, 'anon 은 commands 를 못 읽는다', 'anon', null, $q$select count(*)::text from public.commands$q$, 'err:42501'),
            (1, 'anon 은 표에 쓰지 못한다', 'anon', null, $q$with x as (insert into public.accounts (name) values ('zz') returning 1) select count(*)::text from x$q$, 'err:42501'),
            -- ===== anon: RPC =====
            (1, '잘못된 빌드 ID 로 ingest → 401', 'anon', null, $q$select public.ingest('bld_wrong', '[]'::jsonb, 'machine-x-0001')::text$q$, 'err:PT401'),
            (1, '잘못된 빌드 ID 로 poll → 401', 'anon', null, $q$select public.poll('bld_wrong', 'machine-x-0001')::text$q$, 'err:PT401'),
            (1, '안 묶인 빌드에 PC 식별값이 없으면 400', 'anon', null, format($q$select public.ingest(%L, '[]'::jsonb)::text$q$, key_a), 'err:PT400'),
            (1, '맞는 빌드 ID + 첫 PC → 통과(그 PC 에 묶임)', 'anon', null, format($q$select (public.ingest(%L, '[]'::jsonb, 'machine-a-0001')->>'accepted')$q$, key_a), '0'),
            (1, '묶인 뒤 다른 PC → 401', 'anon', null, format($q$select public.ingest(%L, '[]'::jsonb, 'machine-b-0002')::text$q$, key_a), 'err:PT401'),
            (1, '묶인 PC 의 poll → 통과', 'anon', null, format($q$select (public.poll(%L, 'machine-a-0001')->>'commands')$q$, key_a), '[]'),
            (1, 'anon 은 register_build 를 못 부른다', 'anon', null, $q$select public.register_build('x', 'y')$q$, 'err:42501'),
            (1, 'anon 은 revoke_device 를 못 부른다', 'anon', null, format($q$select public.revoke_device(%L)::text$q$, dev_a), 'err:42501'),
            (1, 'anon 은 send_command 를 못 부른다', 'anon', null, format($q$select public.send_command(%L, 'stop')::text$q$, dev_a), 'err:42501'),
            (1, 'anon 은 set_device_settings 를 못 부른다', 'anon', null, format($q$select public.set_device_settings(%L, '{}'::jsonb)::text$q$, dev_a), 'err:42501'),
            (1, 'anon 은 request_settings 를 못 부른다', 'anon', null, format($q$select public.request_settings(%L)::text$q$, dev_a), 'err:42501'),
            (1, 'anon 은 take_settings 를 못 부른다', 'anon', null, $q$select public.take_settings(1)::text$q$, 'err:42501'),
            (1, 'anon 은 suspend_account 를 못 부른다', 'anon', null, format($q$select public.suspend_account(%L)::text$q$, acc_a), 'err:42501'),
            (1, 'anon 은 resume_account 를 못 부른다', 'anon', null, format($q$select public.resume_account(%L)::text$q$, acc_a), 'err:42501'),
            (1, 'anon 은 private 함수를 못 부른다', 'anon', null, $q$select private.is_admin()::text$q$, 'err:42501'),
            -- ===== authenticated 업체 A owner: 자기 업체만 =====
            (1, 'A owner — accounts 1건(자기 업체)', 'authenticated', ua, $q$select count(*)::text from public.accounts$q$, '1'),
            (1, 'A owner — devices 1건', 'authenticated', ua, $q$select count(*)::text from public.devices$q$, '1'),
            (1, 'A owner — runs 1건', 'authenticated', ua, $q$select count(*)::text from public.runs$q$, '1'),
            (1, 'A owner — run_steps·run_logs·run_attention 각 1건', 'authenticated', ua, $q$select ((select count(*) from public.run_steps) || '/' || (select count(*) from public.run_logs) || '/' || (select count(*) from public.run_attention))$q$, '1/1/1'),
            (1, 'A owner — commands 0건(B 것만 있음)', 'authenticated', ua, $q$select count(id)::text from public.commands$q$, '0'),
            (1, 'commands.payload(설정 값)는 표로 못 읽는다', 'authenticated', ua, $q$select count(payload)::text from public.commands$q$, 'err:42501'),
            (1, 'A owner — account_members 는 자기 줄만', 'authenticated', ua, $q$select count(*)::text from public.account_members$q$, '1'),
            (1, 'A owner — alerts 는 admin 전용이라 0건', 'authenticated', ua, $q$select count(*)::text from public.alerts$q$, '0'),
            (1, 'A owner — B 의 run 을 id 로 찍어도 0건', 'authenticated', ua, format($q$select count(*)::text from public.runs where id = %L$q$, run_b), '0'),
            (1, 'A owner — B 의 device 를 id 로 찍어도 0건', 'authenticated', ua, format($q$select count(*)::text from public.devices where id = %L$q$, dev_b), '0'),
            (1, 'A viewer — A 의 runs 는 보인다', 'authenticated', uv, $q$select count(*)::text from public.runs$q$, '1'),
            (1, 'B owner — A 의 account 를 id 로 찍어도 0건', 'authenticated', ub, format($q$select count(*)::text from public.accounts where id = %L$q$, acc_a), '0'),
            (1, 'authenticated 도 표에 직접 못 쓴다 (insert)', 'authenticated', ua, $q$with x as (insert into public.accounts (name) values ('zz') returning 1) select count(*)::text from x$q$, 'err:42501'),
            (1, 'authenticated 도 표에 직접 못 쓴다 (update)', 'authenticated', ua, format($q$with x as (update public.devices set name = 'zz' where id = %L returning 1) select count(*)::text from x$q$, dev_a), 'err:42501'),
            (1, 'authenticated 도 표에 직접 못 쓴다 (delete)', 'authenticated', ua, format($q$with x as (delete from public.runs where id = %L returning 1) select count(*)::text from x$q$, run_a), 'err:42501'),
            (1, 'authenticated 는 ingest 를 못 부른다 (anon 전용)', 'authenticated', ua, format($q$select public.ingest(%L, '[]'::jsonb, 'machine-a-0001')::text$q$, key_a), 'err:42501'),
            (1, 'authenticated 는 poll 을 못 부른다 (anon 전용)', 'authenticated', ua, format($q$select public.poll(%L, 'machine-a-0001')::text$q$, key_a), 'err:42501'),
            (1, 'authenticated 는 private.bind_device 를 못 부른다', 'authenticated', ua, format($q$select (private.bind_device(%L, 'machine-a-0001')).id::text$q$, key_a), 'err:42501'),
            -- ===== 제어 RPC 권한 (private.can_control) =====
            (1, 'A owner 는 B 의 설정을 못 바꾼다 → 403', 'authenticated', ua, format($q$select public.set_device_settings(%L, '{"sales_mode":"전체"}'::jsonb)::text$q$, dev_b), 'err:PT403'),
            (1, 'A owner 는 B 의 설정을 못 본다 → 403', 'authenticated', ua, format($q$select public.request_settings(%L)::text$q$, dev_b), 'err:PT403'),
            (1, 'A viewer 는 A 의 설정을 못 바꾼다 → 403', 'authenticated', uv, format($q$select public.set_device_settings(%L, '{"sales_mode":"전체"}'::jsonb)::text$q$, dev_a), 'err:PT403'),
            (1, '비밀번호 키는 설정으로 못 보낸다 → 400', 'authenticated', ua, format($q$select public.set_device_settings(%L, '{"login_password":"x"}'::jsonb)::text$q$, dev_a), 'err:PT400'),
            (1, 'A viewer 는 A 의 설정 읽기를 요청한다 (답 전 = 대기)', 'authenticated', uv, format($q$select coalesce(public.take_settings(public.request_settings(%L))::text, 'pending')$q$, dev_a), 'pending'),
            (1, 'A owner 는 B 에게 명령을 못 보낸다 → 403', 'authenticated', ua, format($q$select public.send_command(%L, 'stop')::text$q$, dev_b), 'err:PT403'),
            (1, 'A viewer 는 A 에게도 명령을 못 보낸다 → 403', 'authenticated', uv, format($q$select public.send_command(%L, 'stop')::text$q$, dev_a), 'err:PT403'),
            (1, 'A owner 는 A 에게 명령을 보낸다', 'authenticated', ua, format($q$select (public.send_command(%L, 'pause') > 0)::text$q$, dev_a), 'true'),
            -- ===== admin: 전부 =====
            (1, 'admin — 두 업체 runs 가 다 보인다', 'authenticated', uad, format($q$select count(*)::text from public.runs where id in (%L, %L)$q$, run_a, run_b), '2'),
            (1, 'admin — alerts 가 보인다', 'authenticated', uad, format($q$select count(*)::text from public.alerts where run_id = %L$q$, run_b), '1'),
            (1, 'admin — 두 업체 account 가 다 보인다', 'authenticated', uad, format($q$select count(*)::text from public.accounts where id in (%L, %L)$q$, acc_a, acc_b), '2'),
            -- ===== 2단계: 업체 A 중지 (업체 중지 적용 시) =====
            (2, '중지된 업체 A 의 ingest → 401', 'anon', null, format($q$select public.ingest(%L, '[]'::jsonb, 'machine-a-0001')::text$q$, key_a), 'err:PT401'),
            (2, '중지된 업체 A 의 poll → 401', 'anon', null, format($q$select public.poll(%L, 'machine-a-0001')::text$q$, key_a), 'err:PT401'),
            (2, '업체 B 는 영향 없음 (첫 PC 로 묶임)', 'anon', null, format($q$select (public.ingest(%L, '[]'::jsonb, 'machine-b-0001')->>'accepted')$q$, key_b), '0'),
            (2, '중지된 업체 A owner 는 명령을 못 보낸다 → 403', 'authenticated', ua, format($q$select public.send_command(%L, 'stop')::text$q$, dev_a), 'err:PT403'),
            (2, '중지된 업체 A owner 는 설정을 못 바꾼다 → 403', 'authenticated', ua, format($q$select public.set_device_settings(%L, '{"sales_mode":"전체"}'::jsonb)::text$q$, dev_a), 'err:PT403'),
            (2, 'admin 은 중지된 업체에도 명령을 보낸다', 'authenticated', uad, format($q$select (public.send_command(%L, 'pause') > 0)::text$q$, dev_a), 'true'),
            (2, '중지돼도 A owner 의 읽기는 그대로', 'authenticated', ua, $q$select count(*)::text from public.runs$q$, '1')
        ) as t(phase, label, who, uid, q, expect) where t.phase = p loop
            total := total + 1;
            reset role;
            perform set_config('request.jwt.claims',
                case when r.uid is null then '{"role":"anon"}'
                     else json_build_object('sub', r.uid, 'role', 'authenticated')::text end, true);
            perform set_config('request.jwt.claim.sub', coalesce(r.uid::text, ''), true);
            execute format('set local role %I', r.who);
            begin
                execute r.q into got;
            exception when others then
                got := 'err:' || sqlstate;
            end;
            reset role;
            if got is not distinct from r.expect then
                pass := pass + 1;
            else
                bad := bad || E'\n  - ' || r.label || ' — 기대 ' || r.expect || ' / 실제 ' || coalesce(got, 'null');
            end if;
        end loop;

        if p = 1 then
            -- 설정 주고받기 (10-07). key_a 는 위에서 machine-a-0001 에 묶였다. 판정은 postgres 로 돌아와서 본다
            perform set_config('request.jwt.claims', json_build_object('sub', ua, 'role', 'authenticated')::text, true);
            perform set_config('request.jwt.claim.sub', ua::text, true);
            set local role authenticated;
            cmd := public.request_settings(dev_a);
            put := public.set_device_settings(dev_a, '{"delivery_box":"박스A"}'::jsonb);
            reset role;
            perform set_config('request.jwt.claims', '{"role":"anon"}', true);
            perform set_config('request.jwt.claim.sub', '', true);
            set local role anon;
            js := public.poll(key_a, 'machine-a-0001');                        -- PC 가 명령을 가져간다
            perform public.poll(key_a, 'machine-a-0001', results => jsonb_build_array(jsonb_build_object(
                'id', cmd, 'result', '보냄', 'settings', '{"sales_mode":"전체"}'::jsonb, 'locked_modules', '["orders"]'::jsonb)));
            reset role;
            total := total + 2;
            if exists (select 1 from jsonb_array_elements(js->'commands') c
                       where (c->>'id')::bigint = put and c->'settings'->>'delivery_box' = '박스A') then
                pass := pass + 1;
            else
                bad := bad || E'\n  - poll 이 웹이 바꾼 값을 건네지 않았다 / 실제 ' || coalesce(js::text, 'null');
            end if;
            if (select payload is null from public.commands where id = put) then
                pass := pass + 1;
            else
                bad := bad || E'\n  - 건넨 뒤에도 웹 값이 서버에 남았다';
            end if;
            perform set_config('request.jwt.claims', json_build_object('sub', ua, 'role', 'authenticated')::text, true);
            perform set_config('request.jwt.claim.sub', ua::text, true);
            set local role authenticated;
            js := public.take_settings(cmd);
            got := public.take_settings(cmd)::text;
            cmd2 := public.request_settings(dev_a);
            reset role;
            total := total + 2;
            if js->'settings'->>'sales_mode' = '전체' and js->'locked_modules' = '["orders"]'::jsonb then
                pass := pass + 1;
            else
                bad := bad || E'\n  - PC 의 답을 읽지 못했다 / 실제 ' || coalesce(js::text, 'null');
            end if;
            if got::jsonb ? 'error' and (select payload is null from public.commands where id = cmd) then
                pass := pass + 1;
            else
                bad := bad || E'\n  - PC 의 답이 두 번 읽혔거나 서버에 남았다 / 실제 ' || coalesce(got, 'null');
            end if;
            -- 규칙에 어긋난 답(비밀번호 키)은 그 답만 버린다
            perform set_config('request.jwt.claims', '{"role":"anon"}', true);
            perform set_config('request.jwt.claim.sub', '', true);
            set local role anon;
            perform public.poll(key_a, 'machine-a-0001');
            perform public.poll(key_a, 'machine-a-0001', results => jsonb_build_array(jsonb_build_object(
                'id', cmd2, 'result', '보냄', 'settings', '{"login_password":"x"}'::jsonb)));
            reset role;
            total := total + 1;
            if (select payload is null and result like '설정 값이 서버 규칙에%' from public.commands where id = cmd2) then
                pass := pass + 1;
            else
                bad := bad || E'\n  - 비밀번호 키가 실린 답을 버리지 않았다';
            end if;
        end if;
    end loop;

    -- 성공이든 실패든 예외로 끝낸다 → 위에서 만든 모든 행·변경이 되돌려진다
    if bad <> '' then
        raise exception 'RLS_CHECK_FAIL % / % 실패:%', total - pass, total, bad;
    end if;
    raise exception 'RLS_CHECK_OK %/% (되돌림)', pass, total;
end
$do$;
