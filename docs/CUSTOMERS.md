# 업체 전용 RPA 구조

원본 RPA 하나에 업체마다 다른 규칙·동작을 붙이는 구조. 기획·검토 10-02, **10-08 에 만들었다** — 첫 업체의 전용 RPA 를
다른 저장소에서 이 프로젝트로 옮겼다 (사용자 결정: 남의 저장소에 PR 하는 구조라 제약이 많다). 아래 2~4절은 기획 때의 설계이고,
실제로 만든 것은 0절이다 — 둘이 다르면 0절이 맞다.

## 0. 만든 것 (10-08)

| 무엇 | 어디 |
| --- | --- |
| 틀 — `Field`(칸 정의)·`Profile`·`load`·`raw_id`·`available` | `config/customer.py` (git 안, 업체 이름 없음). 기획의 `customers/__init__.py` 대신 — 원본이 import 하는 것이라 업체 폴더가 없는 clone 에서도 돌아야 한다 |
| 업체 패키지 | git 밖 `customers/<id>/__init__.py`(`PROFILE` — 가볍게, 설정 읽는 도중에 불린다) + 화면 코드 |
| 업체 고르기 | 설정 `customer` — 빌드 프로그램 2절에서 고르고 **굽고 잠근다**(`LOCKED_KEYS`). 바꾸면 빌드 프로그램을 다시 켜야 그 업체 기능 목록이 보인다. 개발 폴더는 환경변수 `RPA_CUSTOMER` (빌드본은 무시) |
| 칸 | `Profile.fields` → `DEFAULTS` 에 더함(`config/settings.py`) · 실행 창 [설정] 탭에 업체 이름 묶음(종류별로 그림, `gui/run_app._customer_section`) · 웹 — PC 가 설정 답에 칸 정의를 싣는다(`remote.described`) |
| 기능 | `Profile.base`(원본 기능 중 쓰는 것) + `modules_factory()`(업체 기능 `Module(steps=, run=)`, id `cx_`). 원본 기능 뒤에 돈다 (`modules.plan`, `full_flow._full`) |
| 영역마다 시험 실행 | `Profile.rehearse(area)` — `collect`·`sales`·`logistics_wait`·`logistics`·업체 기능 id. 참이면 그 영역만 누르지 않고 확인 (`full_flow.rehearsed`) |
| 물류대기 보류 | `Profile.hold=False` → `logistics_wait.run(hold=False)` — 보류 없이 [일반] 저장만, [물류처리] 안 누름 |
| 빌드 | `build_run.spec` — 구운 업체 패키지 하나만 넣고 다른 업체는 누출 검사 금지 목록. 자가 점검(`utils/selfcheck`)이 업체 패키지도 불러 본다 |
| 서버 | `devices.customer`(register_build 가 넣음) · 기능 목록 `private.modules_ok`(원본 넷 + `cx_`) · 업체 칸 `cx_` 은 종류·크기만(`private.cx_value_ok` — 고르는 값은 PC 가 거른다) · 설정 답의 칸 정의·기능 이름(`private.described_ok`) — **실서버 미적용** |
| 웹 | 업체 칸·기능 이름은 PC 가 보낸 것으로 그린다 (`app.js` 에 업체 이름 없음) · PC 표에 `전용 <id>` |
| 공용 부품 (업체 이름 없음) | `automation/menu.py` 메뉴 검색으로 화면 열기 · `utils/dialogs.confirm_all` 팝업 모두 [예](없으면 [확인]) · `has_message_box` |
| 확인 | `tools/probe_customer` (가짜 업체) · git 밖 `tools/local/probe_customer_<id>` |

안 만든 것: 빌드 프로그램에서 업체 칸의 처음 값 넣기(칸의 기본값 = 안전한 쪽으로 굽힌다 — 바꾸려면 실행 창·웹) /
업체 기능의 사용량 집계(`usage_daily` 는 원본 네 기능만 센다) / 5절 미정 1·2.
업체별 요구·미팅 기록은 `docs/customers/<업체>.md` (그 업체 작업 때만 연다). **업체 전용 코드·문서는 전부 git 밖이다** — `customers/` · `docs/customers/` · 사이트 파일 `collect/sites/local/` · 그 시험 `tools/local/` · 조사 기록 `docs/local/` (`.gitignore`, 확정 7).

## 1. 확정 (사용자 10-02)

| # | 결정 |
| --- | --- |
| 1 | 원본 하나 + 업체 전용. 업체 요구를 원본에 무조건 넣지 않는다 (CLAUDE.md) |
| 2 | **여러 업체가 같은 규칙을 원해도 원본 설정으로 올리지 않는다** — 업체 파일마다 둔다 |
| 3 | **빌드 등록에 업체 id 를 남긴다** (어느 업체용 빌드인지 대시보드에서 보이게) |
| 4 | 만드는 시점: 그 업체의 정확한 프로세스가 나온 뒤. 끼움 자리가 하나도 없는 틀을 미리 만들지 않는다 |
| 5 | **업체 전용 값은 그 업체로 빌드한 프로그램과 웹사이트에서는 보인다.** 원본·다른 업체의 빌드와 설정에는 없다 |
| 6 | ERPia 자체가 다른 업체(업체 전용 판)를 어떻게 할지는 **사용자가 그 업체 프로세스가 정확히 나왔을 때 알려 준다** |
| 7 | **업체 전용 코드·문서는 git 밖** — 업체 파일(`customers/`)·사이트 파일(`collect/sites/local/`)·그 시험(`tools/local/`)·조사 기록(`docs/local/`)·요구 기록(`docs/customers/`). 원본(git)에는 업체·사이트 이름, 화면 요소, 상품코드·거래처 값이 없다 |

## 2. 달라지는 것마다 둘 곳

| 무엇이 다른가 | 둘 곳 | 예 |
| --- | --- | --- |
| 모든 업체에 있는 값 (사용자가 바꿈) | 원본 설정 — 지금 그대로 | 택배사, 자동/수동, 수집 방식 |
| 관리자가 정하고 사용자는 못 바꾸는 값 | 구운 값 + `LOCKED_KEYS` — 지금 그대로 | 업체코드, 기능 고정 |
| **그 업체에만 있는 값** | **업체 전용 설정** — 업체 파일이 칸(키·이름·종류·기본값)을 정의, 그 업체 빌드의 프로그램 [설정]·웹에만 보인다 (확정 5) | 무조건 보류할 상품코드·거래처 |
| 업체만의 동작 | **업체 파일의 함수 → 원본의 끼움 자리** | 거래처 기준 보류 |
| 업체만의 웹 사이트 | 사이트 파일 — **git 밖** `collect/sites/local/<사이트>.py` (`docs/SITES.md`) | 거래처 사이트 |
| 업체만의 화면·단계 (ERPia 자체가 다름) | 확정 6 — 사용자가 알려 줄 때 | 업체 전용 이관 화면 |

## 3. 구조

```
customers/            원본 코드와 같은 층이지만 **git 밖** (`.gitignore` `/customers/`). 검색에서 빼지 않는다 (원본의 끼움 자리를 고칠 때 업체 쪽 사용처를 찾아야 한다)
  __init__.py         Profile(값만: id·name·fields·hooks) + Field + load() + hook(name). 구운 업체가 없으면 원본 그대로
  <업체>.py            업체 하나 = 파일 하나: 칸 정의 + 함수 + PROFILE
```

- **끼움 자리는 업체가 실제로 필요할 때만** 원본에 하나씩 만든다. 자리마다 이름·인자·돌려줄 값·업체가 없을 때의 동작을 한 표로 남긴다.
- **원본은 조작 부품·기본 규칙, 업체 파일은 판정만.** 업체 파일은 ERPia 를 직접 누르지 않는다 — "이 행을 보류할지" 만 돌려주고 체크·우클릭·배송보류는 원본 부품이 한다 (UIA 우선·조건 대기 규칙이 업체마다 갈라지지 않게).
- **사람이 보는 글은 원본이 만든다.** 업체 함수는 건수·사유 코드만 돌려준다 (`probe_userlog` 를 업체마다 늘리지 않게. `SITES.md` runner 와 같은 규칙).
- 업체 전용 설정 키는 **`cx_` 로 시작한다** — 원본 키와 섞이지 않고, 서버·웹이 접두어로 구별한다 (4절).

예 — A업체 "특정 상품·거래처는 무조건 보류":

```python
# customers/a_corp.py — A 빌드에만 들어간다
FIELDS = (Field("cx_force_hold_codes", "무조건 보류 상품코드", "list"),
          Field("cx_force_hold_clients", "무조건 보류 거래처", "list"))

def hold_force(code: str, client: str | None) -> bool:
    return (code in SETTINGS.cx_force_hold_codes
            or client in SETTINGS.cx_force_hold_clients)

PROFILE = Profile("a_corp", "A업체", fields=FIELDS, hooks={"hold_force": hold_force})

# automation/logistics_wait.py hold_shortage_items — 원본의 끼움 자리
force = customer.hook("hold_force")          # 업체가 없으면 None → 지금 동작
if not shortage and not (force and force(code, client)):
    normal += 1
    continue
```

- 상품 기준은 지금 루프에 들어간다 (상단 재고 그리드는 부족이 없는 상품도 보인다).
- 거래처 기준은 **조사부터** — 거래처는 주문 단위라 하단 그리드에서 골라야 하고, 물류대기 그리드의 거래처 컬럼은 `CONTROLS.md` 에 없다.
- 강제 보류와 `hold_exclude_codes` 가 겹칠 때 어느 쪽이 이기는지는 업체 요구로 정한다.

## 4. 빌드·설정·서버·웹

| 어디 | 무엇 |
| --- | --- |
| 빌드 프로그램 | [업체] 고르기 → `customer` 를 굽고 `LOCKED_KEYS` 에 넣는다. 업체 전용 칸의 처음 값도 여기서 넣고 굽는다 |
| `build_run.spec` | 그 업체 파일 하나를 `hiddenimports` 로, 나머지 업체 파일은 금지 목록 → 기존 누출 검사(`_leaked`)가 섞이면 빌드를 멈춘다. `_OURS` 에 `customers` |
| 업체 전용 웹 사이트 | 사이트 파일(**git 밖** `collect/sites/local/`)은 `local/__init__.py` 가 리터럴 import 하는 것이 전부 그 빌드에 묶인다 (`docs/SITES.md`) — 업체 파일처럼 `hiddenimports` 를 따로 두지 않는다. 사이트가 없으면 메일 기능이 '메일 사이트 연결' 입력 필요로 잠긴다 |
| 실행 중 | `importlib.import_module(f"customers.{id}")` — `hiddenimports` 로 넣었으니 PyInstaller 가 놓치지 않는다 |
| 설정 읽기 (`config/settings.py`) | `DEFAULTS` 에 업체 칸을 더한다 — 그 업체 빌드에서는 "모르는 키" 경고가 안 나고 `settings.local.json` 에 저장된다 |
| 프로그램 (`gui/run_app.py`) | [설정] 탭에 **업체 전용 묶음** — 업체 칸이 있을 때만, `fields` 를 돌며 그린다 (업체마다 화면 코드를 쓰지 않는다) |
| 웹 (`web/app.js`) | **app.js 는 모든 업체가 받는 공개 파일이라 업체 칸 이름을 박지 않는다** (박으면 A업체 칸이 B업체 화면 소스에 보인다). PC 가 칸 정의(키·이름·종류)를 서버에 올리고, 웹은 그 PC 의 칸 정의로 그린다. 서버의 RLS 가 그 업체 사람에게만 보인다 |
| 서버 원격 설정 (`server/schema.sql` `clean_settings`) | 지금은 **모르는 키면 요청 전체를 거부한다.** 업체 칸은 `cx_` 접두어 + **그 PC 가 올린 칸 정의에 있는 키만** + 종류 검사(목록·글·참거짓·숫자, 크기 상한)로 받는다 — 업체마다 SQL 을 고치지 않게. `orchestrator/remote.py` `REMOTE_KEYS` 에도 업체 칸을 더한다 |
| 업체 id 기록 (확정 3) | 서버 빌드 행(`devices`, 빌드 하나 = 한 행)에 `customer` 칸, `register_build` 가 넘긴다. 원본 빌드는 비운다. 서버의 '업체'(`accounts`, 그 RPA 를 쓰는 회사)와는 다른 값이다 — 이쪽은 **exe 에 든 업체 파일** |

서버 쪽(`devices.customer`·칸 정의 저장·`clean_settings`)은 **SQL 승인 필요**, 웹은 배포 승인 필요.

## 5. 미정

| # | 무엇 | 메모 |
| --- | --- | --- |
| 1 | 업체 전용 설정을 **누가 고치나** | 그 업체 사용자가 프로그램·웹에서 고친다 (지금 `hold_exclude_codes` 처럼) / 관리자만 고치고 사용자는 보기만. 보기만이면 서버 `clean_settings` 를 넓힐 필요가 없다 |
| 2 | 두 업체 파일에 같은 함수가 생기면 | `customers/` 안에서 함께 쓸지, 파일마다 복사할지 (원본 설정으로는 올리지 않는다 — 확정 2) |

## 6. 한계·위험

- 업체 파일·업체 칸은 **다른 업체 exe 에는 안 들어간다.** 그 업체의 exe 를 뜯으면 보인다 (구운 값과 같은 한계) — 그 업체에 보여도 되는 값이니 문제가 아니다 (확정 5).
- 원본을 고치면 업체 끼움 자리가 깨질 수 있다 → 업체마다 `probe_customer_<id>` (가짜 그리드, **git 밖** `tools/local/`), 끼움 자리를 고친 날은 전부 돌린다 (CLAUDE.md 확인 도구 표에 한 줄). 원본 probe 는 업체 없이 지금처럼 통과해야 한다.
- 업체가 늘면 "어느 업체 exe 가 어느 원본으로 빌드됐나" 가 중요해진다 (확정 3). 10-02 부터 git 으로 관리한다 — 빌드할 때 커밋 해시를 남기면 된다 (원본 쪽만 — git 밖 업체·사이트 파일은 해시에 잡히지 않는다).

## 7. 검토한 다른 안

| 안 | 판단 |
| --- | --- |
| 업체마다 폴더 복사 | 탈락 — 원본 수정을 업체 수만큼 |
| 원본 곳곳에 `if 업체 == ...` | 탈락 — 업체 규칙이 흩어지고 모든 exe 에 다른 업체의 거래처명·상품코드가 들어간다 |
| 업체 칸을 원본 `DEFAULTS` 에 두고 화면에서만 감춤 | 탈락 — 모든 빌드에 다른 업체 키가 들어가고, 확정 2 와 맞지 않는다 |
| 웹 app.js 에 업체 칸을 박기 | 탈락 — 공개 파일이라 다른 업체에 보인다 (4절) |
| 범용 규칙 엔진 (설정으로 "컬럼=값이면 보류") | 안 만든다 — 확정 2 와 맞지 않고, 쓰지 않는 일반화 |
