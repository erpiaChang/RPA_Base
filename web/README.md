# 웹 대시보드 (`web/`)

정적 파일 네 개다. 서버 코드가 없다. 설계는 `docs/SERVER_PLAN.md` D-4·D-10.

| 파일 | 무엇 |
| --- | --- |
| `index.html` | 틀·스타일. 스크립트 본문은 없다 (CSP `script-src 'self'` 가 인라인을 막는다) |
| `app.js` | 화면 전부 (로그인 / 위쪽 탭 요약·사용량·PC·실행 기록 / 실행 상세 / 엑셀 내려받기 / 사용법 질문 옆 패널) |
| `config.js` | Supabase URL 과 **공개(anon) 키** 자리표시. 실제 값은 `.env` (git 밖, 10-02) — `tools/build_web` 이 채운다 |
| `_headers` | Cloudflare 가 붙이는 CSP 등 응답 헤더 (Workers 정적 파일·Pages 둘 다 읽는다) |

## 배포 (Cloudflare Workers, 기본 주소 `https://rpa-dashboard.rpa-dashboard.workers.dev`)

**평소: `deploy_web.bat` 더블클릭** (09-28). `tools/build_web` 이 `.env` 의 `SUPABASE_URL`·`SUPABASE_PUBLISHABLE_KEY` 로
`index.html`·`config.js`·`_headers` 의 자리표시(`__SUPABASE_URL__`·`__SUPABASE_PUBLISHABLE_KEY__`)를 채워 `build/web/` 에 만들고,
`web/wrangler.jsonc` 대로 **그 폴더**를 같은 주소에 올린다 (10-02 — git 에 주소·키를 두지 않는다). Claude 가 올릴 때는
`.venv\Scripts\python.exe -m tools.build_web` 뒤 `cd web && npx wrangler@4 deploy` — 둘 다 `APPROVED-RUN` 이 필요하다.

처음 한 번: `npx wrangler@4 login` (브라우저에서 Cloudflare 로그인. 토큰은 사용자 프로필(`%USERPROFILE%\.wrangler`)에 남고
프로젝트에는 없다). node 22 (09-28 올림). 큰 버전을 고정해 같은 판이 돈다.

손으로 올리려면 (처음 만들 때 그랬다):

1. `.env` 에 `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY` 를 넣고 (`server/README.md` 1절의 값) `tools/build_web` 을 돌린다.
2. https://dash.cloudflare.com → Workers & Pages → Create → **Upload and deploy** (정적 파일 끌어다 놓기) → `build/web/` 의
   파일 넷을 올린다. Assets directory 는 `/`.
   **Worker name 이 주소 앞부분이 된다** — `https://<Worker name>.<계정 서브도메인>.workers.dev`.
   ERPia 업체코드·아이디 같은 값을 이름으로 쓰지 않는다 (공개 주소다). 예: `rpa-dashboard`. 서브도메인은 Workers & Pages → Your subdomain 에서 바꾼다.
3. Supabase → Authentication → URL Configuration 의 Site URL / Redirect URLs 에 그 주소를 넣는다.
4. CSP(`_headers`·`index.html` meta)의 `connect-src` 는 `__SUPABASE_URL__` 자리표시 — `tools/build_web` 이 그 프로젝트 주소 하나로 채운다.
5. 고칠 때마다 같은 방법으로 다시 올린다 (같은 Worker name 이면 주소가 유지된다).

## 규칙 (고칠 때)

- 서버에서 온 문자열은 **`textContent` 로만** 그린다. `innerHTML`·`document.write`·`eval` 금지 — 규칙 훅이 잡는다.
- CDN 스크립트는 **버전 고정 + `integrity`**. 버전을 올리면 해시를 다시 잰다:
  `curl -sL <url> | openssl dgst -sha384 -binary | openssl base64 -A`.
  jsdelivr 는 **패키지에 실제로 든 파일**만 쓴다 (`dist/umd/supabase.js`). `.min.js` 를 붙이면 jsdelivr 가 즉석에서 만드는 파일이라 SRI 가 깨진다.
- 없는 숫자를 만들지 않는다 — `total_items` 가 0 이면 막대를 그리지 않는다.
- 상태 글자는 `orchestrator/history.py`·`steps.py` 의 `STATE_LABELS` 와 같아야 한다 (`tools/probe_web.py` 가 센다).

## 로컬에서 보기

`tools/build_web` 뒤 `build/web/` 폴더에서 `python -m http.server 8080` 을 띄우고 `http://localhost:8080` 을 연다 (`web/` 은 자리표시라 Supabase 에 못 붙는다).
