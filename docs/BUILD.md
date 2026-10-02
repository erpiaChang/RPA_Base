# 실행 파일 빌드

Python 설치나 터미널 없이 **exe 하나만 실행하면 동작하는** 형태로 묶는다.

## 빌드

**기능별로 따로 만든다. 통짜 빌드는 없다** (구버전 `build.spec` / `build.bat` 는
2026-09-08 삭제했다 — 진입점이 개발용 `main.py` 라 세 기능이 전부 들어갔다).

```
build_collect.bat      dist\collect\Collect_RPA.exe
build_erpia.bat        dist\erpia\ERPia_RPA.exe
build_full.bat         dist\full\Integrated_RPA.exe
build_run.bat          dist\run\RPA_1.exe            <- 설정을 구워 넣는 실행용
```

또는

```
.venv\Scripts\python.exe -m PyInstaller build_erpia.spec --noconfirm
```

각 `build_*.bat` 은 이전 `build\` / `dist\` 를 지우고 다시 만든다.
남겨두면 옛 코드가 섞인 exe 가 나온다.

> **빌드는 요청받았을 때만 한다** (사용자 확정 2026-09-07).
> 코드를 고칠 때마다 자동으로 다시 빌드하지 않는다.
> 따라서 `dist/` 의 exe 는 **마지막으로 빌드한 시점의 코드**이며,
> 그 뒤의 수정은 반영돼 있지 않다. 배포 전에 한 번 빌드한다.

## 배포할 것

```
<기능별 exe 하나>
config\settings.local.json   (선택)
```

`settings.local.json` 이 없어도 실행된다. 런처에서 값을 입력하고 [실행] 하면
**exe 옆에** `config\settings.local.json` 으로 저장돼 다음 실행 때 다시 뜬다.
틀만 미리 주려면 기능별 샘플(`config\settings.sample.*.json`)을 복사해 쓴다.

로그는 **exe 옆 `logs\rpa_YYYYMMDD.log`** 에 쌓이고, 실패 시 스크린샷도 같은 폴더에 남는다.

> ★ **넘겨주기 전에 exe 옆 `logs\` 를 비운다.** 업체코드·아이디는 `us****01` 로
> 부분 마스킹하지만 로그가 그 폴더에 계속 쌓인다. 빌드 스크립트가 `dist\<기능>` 을
> 통째로 지우므로 **갓 빌드한 것에는 없다** — 문제는 그 폴더에서 한 번이라도
> 돌려 본 다음이다. 실패 스크린샷에는 화면이 그대로 찍혀 있다.

### 실행용 빌드(`build_run.bat`)만 다르다 — 설정을 exe 안에 굽는다

사용자가 **exe 하나만 받아 실행하면 되게** 하려는 것이다 (사용자 확정 2026-09-10).

```
dist\run\RPA_1.exe        <- 이것 하나만 준다. 설정 파일을 함께 주지 않는다
```

| 단계 | 하는 일 |
| --- | --- |
| 1 | `tools/bake_settings.py` 가 `config/settings.local.json` 을 읽어 `build/baked/settings.baked.json` 을 만든다. 비밀번호를 감쌀 씨앗(`.env` 의 `BAKED_SEED`, 10-02 — git 밖)도 `build/baked/baked.key` 로 — **`.env` 가 없으면 빌드가 멈춘다** |
| 2 | `build_run.spec` 이 둘을 번들 안 `config/` 에 넣는다 |
| 3 | `config/settings.py` 가 **`DEFAULTS` → 구운 값 → exe 옆 `settings.local.json`** 순으로 덮는다 |
| 4 | 빌드가 끝나면 `build/baked` 를 지운다 (평문 비밀번호가 들어 있다) |

★ **굽기가 멈추는 값** — 비어 있으면 안 되는 값이다 (`tools/bake_settings.py` `required`, 기능 고정이면 고른 기능만 본다). ERPia 기능이 든 빌드는 `login_company_code`·`login_user_id`·`target_exe_name`(ERPia 실행 파일 이름, 경로 없이 — 실행 창이 이 이름으로 ERPia 를 찾는다), 메일 기능이 든 빌드는 메일 주소·아이디·읽지 않은 메일만·기간·브라우저·휴대폰 종류·인증 문자 고르는 말 (목록은 `orchestrator/modules.py` `baked_required` — 실행 창의 알림도 같은 목록). `target_exe`·`target_work_dir`·`download_base_dir` 는 개발 PC 의 경로라 굽지 않는다.

★ **`dist\run` 에 `settings.local.json` 을 미리 두지 않는다.** 로컬이 구운 값보다
우선하므로, 빈 샘플을 두면 **구운 값이 전부 빈 값으로 덮인다.**

화면(`gui/run_app.py`)에서 고칠 수 있는 값은 `docs/SETTINGS.md` '고정값은 로컬 설정이 못 덮는다' 절의 화면 항목 표(열세 가지 + 자동 실행 칸)뿐이다. 나머지는 구운 값을 그대로 쓴다. 흐름은 통합과 같다.
실행하면 화면에서 고친 값이 **exe 옆 `config\settings.local.json`** 에 저장돼
다음 실행에 다시 뜬다.

> ★ **구운 비밀번호는 `baked:` 로 감싸지만 약하다** — 키가 프로그램 안에 있어 exe 를
> 뜯으면 풀 수 있다 (`utils/secret.py`, 09-17). 이 exe 는 믿을 수 있는 사용자에게만 준다.
> 남에게 줄 때는 실행하며 생긴 `logs\` 와 `config\settings.local.json` 을 지우고 exe 만 준다.


> 쓰기 권한이 있는 폴더에 두어야 한다. `C:\Program Files` 아래에 두면
> 설정과 로그를 쓰지 못한다. 바탕화면이나 `C:\ERPia_RPA\` 같은 위치를 쓴다.

#### 켜기 비용 줄이기 — exe 하나는 그대로 (2026-09-29, 사용자 결정)

onefile 은 켤 때마다 번들을 `%TEMP%\_MEI*` 에 전부 푼다. 09-28 빌드는 153MB 였고 그중 92MB 가
메일 기능만 쓰는 Playwright `node.exe` 였다. 자동 켜기가 5분마다 exe 를 띄우면서 이미 떠 있어도 매번 풀었다.
폴더 배포·설치 프로그램은 검토 뒤 버렸다 — 사용자가 exe 하나를 원했다.

| 무엇 | 어디 |
| --- | --- |
| `node.exe` 를 풀리는 칸에서 빼 **exe 리소스**(`RPADATA/PWNODE`, zlib)로 넣는다. 메일 기능을 처음 쓸 때 exe 옆 `driver\<해시>\node.exe` 로 한 번 꺼내고 `PLAYWRIGHT_NODEJS_PATH` 로 쓴다 | `build_run.spec`, `collect/pw_driver.py` |
| `PIL._avif`(7.9MB)를 뺀다 — PNG 만 쓴다 | `build_run.spec` `UNUSED_LIBS` |
| 메일이 없는 기능 고정 빌드는 Playwright 를 통째로 뺀다 | `build_run.spec` `NEED_MAIL` |
| 로그온 때(`--background`) 켜진 빌드본은 **작업 스케줄러에 넘긴다** — 작업이 띄운 것이 떠 있으면 5분 트리거가 아무것도 띄우지 않는다(IgnoreNew). 두 번 누름은 넘기지 않는다 — 두 번 풀어 창이 1.8초 → 3.6초로 늦었다 | `main_run.py`, `utils/autostart.hand_over` |
| 켜는 데 걸린 시간을 로그에 남긴다 (`켜는 데 N초 — 풀기 A초 + 파이썬·창 B초`) | `gui/run_app._log_startup` |

확인: `tools/probe_startup.py` (빌드 뒤 `--exe --browser` — 번들에 node.exe 가 없는지, 리소스에서 꺼낸 것으로 Edge 가 뜨는지).

### 빌드 프로그램 (`build_tool.bat`) — 설정을 화면에서 고쳐서 그대로 굽는다 (2026-09-22)

```
build_tool.bat  →  gui/build_app.py (창)  →  tools/build_run.py  →  dist\run\RPA_1.exe
build_run.bat   →  콘솔로 tools/build_run.py 만 (설정은 config\settings.local.json 그대로)
```

창에 `config/settings.local.json` 의 값이 절별로 보인다 — 개발 도구 전용 2개, 서버가 발급하는 빌드 ID, 그 PC 에서 정하는 값(ERPia 경로 둘·엑셀 저장 폴더·자동 켜기)은 빼고.
고쳐서 [설정 저장] 하면 그 파일에 쓰고(비밀번호는 `dpapi:` 로), [저장하고 빌드] 하면
`tools/build_run.py` 가 굽고 → 지난 산출물을 지우고 → PyInstaller → 구운 파일을 지운다.
로그가 오른쪽 칸에 흐르고 끝나면 exe 경로가 상태줄에 나온다. 몇 분 걸린다.

**실행할 기능**은 둘 중 하나로 굽는다 (사용자 확정 09-22, 설정 키 `run_modules_locked`):

| 고른 것 | 만들어진 exe |
| --- | --- |
| 기능 고정 | 고른 기능만 돈다. 실행 창의 [실행할 기능] 이 잠기고 예약도 그 기능 안에서만 고른다. exe 옆 설정이 `run_modules` 를 덮어도 무시한다 (번들 안 구운 값을 읽는다) |
| 실행 창에서 선택 | 지금까지처럼 실행 창의 체크로 고른다 |

목록 칸은 `;` 로 나눈다 (예약 줄 `평일 09:00 mail,orders` 에 쉼표가 있어서). 사전 칸은 `사이트=비번; …`.
빌드 프로그램은 개발 폴더에서만 돈다 (소스 + `.venv` + PyInstaller 필요). 배포본에는 들어가지 않는다 (`DEV_ONLY`).
확인 도구: `tools/probe_build.py` (창은 띄우지 않고 저장·빌드는 가짜로).

## 코드 보호 수준 — 정확히 알고 쓸 것

| 항목 | 결과 |
|---|---|
| `.py` 소스가 파일로 남는가 | **아니다.** 배포물에 `.py` 가 없다 |
| exe 를 텍스트 편집기로 열면 코드가 보이는가 | **아니다.** 압축된 바이트코드다 |
| 완전히 복원 불가능한가 | **아니다.** 전문가는 디컴파일할 수 있다 |

PyInstaller 는 `.py` 를 `.pyc`(바이트코드)로 컴파일해 exe 안에 압축해 넣는다.
실제로 확인했다 — 빌드된 exe 에서 소스 한 줄도, 한글 로그 문구도 평문으로 검색되지 않는다.

다만 이것은 **난독화이지 암호화가 아니다.** 전용 도구로 바이트코드를 뽑아
디컴파일하면 원본에 가까운 코드를 복원할 수 있다.
(PyInstaller 6.x 는 예전의 `--key` AES 암호화 옵션을 제거했다)

> 더 강한 보호가 필요하면 **Nuitka** 로 기계어 컴파일하는 방법이 있다.
> 디컴파일 난이도가 크게 올라가지만 빌드 시간과 호환성 검증 부담이 늘어난다.
> 필요해지면 그때 검토한다.

비밀번호는 `settings.local.json` 에 `dpapi:` 로 감싸 저장된다 (그 PC·그 Windows 계정에서만
풀린다). 그래도 exe 를 나눠 줄 때 이 파일을 함께 주지 않는다.

## 무엇이 들어가고 무엇이 빠지는가

PyInstaller 는 `main.py` 부터 **import 그래프를 따라간 것만** 넣는다.
아무 데서도 import 하지 않는 모듈은 자동으로 빠진다.

그 자동 동작에만 기대지 않는다. 프로그램이 커지면 실수로 개발용 모듈을
import 하는 일이 생기고, 그러면 조용히 배포본에 섞인다.
그래서 `build_*.spec` 에서 **이름으로 못 박고, 매 빌드마다 확인한다.**

| 항목 | 내용 |
|---|---|
| `HIDDEN` | pywinauto / comtypes 서브모듈 전부. 문자열로 동적 import 해서 정적 분석에 안 잡힌다 |
| `DEV_ONLY` | 개발·조사 전용. `tools` / `gui.launcher` / `gui.build_app` / `llm` |
| `UNUSED_LIBS` | 설치돼 있으면 딸려 들어갈 수 있는 무거운 패키지 (numpy, pandas, PyQt, setuptools, pip …) |
| `datas = []` | 설정·문서·이미지를 번들에 넣지 않는다 |
| `console=False` | 검은 콘솔 창을 띄우지 않는다 |

### 빌드할 때마다 포함 목록이 출력된다

```
============================================================
 번들에 포함된 프로젝트 모듈 19개
   automation / automation.application / automation.login
   automation.logistics / automation.logistics_wait / automation.order_mapping
   config / config.settings / gui / gui.launcher
   utils.dialogs / utils.dpi / utils.elevation / utils.filedialog
   utils.logger / utils.process / utils.ui / utils.wait
 개발 전용 모듈 누출 없음
============================================================
```

의도하지 않은 모듈이 보이면 `DEV_ONLY` 에 추가한다.
`DEV_ONLY` 모듈이 번들에 들어가면 **빌드가 그 자리에서 중단된다.**
(런타임 코드가 개발용 모듈을 import 하고 있다는 뜻이므로 의존을 끊어야 한다)

### 앞으로 파일이 늘어날 때

| 종류 | 두는 곳 | 빌드 |
|---|---|---|
| 업무 흐름 | `automation/` | 포함 |
| 공통 UI 조작 | `utils/` | 포함 |
| 화면 조사 도구 (`probe_*`) | `tools/` | **제외** (`DEV_ONLY`) |
| 흐름 실행/테스트 도구 (`test_*`) | `tools/` | **제외** (`DEV_ONLY`) |
| 조사 자료 / 문서 | `docs/` | 제외 (`datas=[]` 라 어차피 안 들어간다) |
| 개발용 실행 스크립트 | `*.bat` | 제외 (exe 와 무관) |

`tools/` 는 빌드에서 빼고, **기능이 끝나면 지운다** (사용자 확정 2026-09-18, 09-07 의
"지우지 않는다" 를 대체). 목록은 `docs/PROGRESS.md` 코드 지도.

## 빌드를 위해 고친 코드

### 1. 경로 — `config/settings.py`

```python
def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
```

onefile 로 묶으면 소스가 임시 폴더(`sys._MEIPASS`)에 풀렸다가 **종료 시 지워진다.**
거기를 기준으로 삼으면 설정과 로그가 매번 사라지고 사용자가 열어 볼 수도 없다.
그래서 빌드본에서는 **exe 가 있는 폴더**를 기준으로 한다.

### 2. 콘솔 없는 실행 — `utils/logger.py`

`console=False` 로 빌드하면 `sys.stdout` 이 `None` 이다.
`logging.StreamHandler(None)` 은 첫 로그에서 죽는다.
표준 출력이 없으면 **파일 로그만** 남기도록 했다.

## 확인한 것 (2026-09-07)

| 항목 | 결과 |
|---|---|
| 빌드 | 성공. 경고 0건 |
| exe 단독 실행 | 런처 GUI 정상 기동 (`RPA 런처` 창 확인) |
| 로그 경로 | `dist\logs\rpa_20260907.log` 생성 확인 |
| 소스 노출 | exe 에서 소스/로그 문구 평문 검색 안 됨 |

업무 흐름 전체를 exe 로 돌린 검증은 **09-17 에 했다** (빌드본 11단계 완주, `docs/HANDOFF.md` 1절).

## 권한

RPA 가 ERPia 를 **직접 띄우므로** 일반 권한으로 충분하다.
사용자가 관리자 권한으로 띄워 둔 ERPia 인스턴스에는 붙을 수 없다(Windows UIPI).
그런 환경이면 exe 를 관리자 권한으로 실행해야 한다.

## 주의

- 화면이 잠기면 마우스 클릭이 대상 프로그램에 전달되지 않는다.
  로그인 단계에서 이를 감지해 중단한다. 실행 중에는 **화면 잠금을 꺼 둔다.**
- 백신이 PyInstaller 산출물을 오탐하는 경우가 있다. 사내 배포 시 예외 등록이 필요할 수 있다.

---

# 기능별 배포 분리 (2026-09-08 확정·검증)

사용자 확정: **배포본 안에 다른 기능의 코드도, 흔적도 남기지 않는다.**
실행 파일은 뜯어볼 수 있으므로, 넣지 않은 것만이 안전하다.

| 빌드 | 산출물 | 진입점 | 스펙 |
| --- | --- | --- | --- |
| 주문 엑셀 수집 | `dist\collect\Collect_RPA.exe` | `main_collect.py` | `build_collect.spec` |
| ERPia 자동화 | `dist\erpia\ERPia_RPA.exe` | `main_erpia.py` | `build_erpia.spec` |
| 통합 | `dist\full\Integrated_RPA.exe` | `main_full.py` | `build_full.spec` |

각각 `build_collect.bat` / `build_erpia.bat` / `build_full.bat` 로 만든다.

## 어떻게 막는가 — 네 겹

1. **진입점 분리.** PyInstaller 는 진입점에서 import 로 닿는 것만 넣는다
2. **스펙의 `FORBIDDEN`.** 자동 제외에 기대지 않고 다른 기능의 모듈 이름을 못 박는다.
   빌드 끝에 번들 목록을 검사해 **하나라도 섞이면 빌드를 중단**한다
3. **화면 문구 분리.** `gui/collect_app.py` / `gui/erpia_app.py` / `gui/full_app.py`.
   공용 파일(`gui/common.py`, `orchestrator/common.py`)에는 어떤 기능의 이름도 적지 않는다.
   ★ 주석은 `.pyc` 에서 사라지지만 **docstring 과 문자열 리터럴은 남는다**
4. **설정 항목 분리.** `config/fields_collect.py` / `config/fields_erpia.py`.
   설정 키 이름 자체가 흔적이다. `config/settings.py` 는 없는 파일을 **정상으로** 취급한다
   (ImportError 를 건너뛴다). 배포용 `settings.local.json` 도 기능별 샘플에서 복사한다

### 항상 제외되는 것 (`DEV_ONLY`)

`tools`, `gui.launcher`(개발용 선택 창), `gui.build_app`(빌드 프로그램), `llm`(LLM 전용 PC 의 워커).

### 주의 — 동적 import 는 손으로 넣어야 한다

`config/settings.py` 는 설정 항목 파일을 **동적으로** import 한다. 정적 분석으로
잡히지 않아 스펙의 `HIDDEN` 에 이름을 넣는다. 빠뜨리면 빌드는 되지만
**배포본에서 설정이 통째로 비어 버린다** (실제로 한 번 겪었다).

## 검증 결과 (2026-09-08, 실제 빌드)

| 배포본 | 포함된 우리 모듈 | 교차 검사 |
| --- | --- | --- |
| `Collect_RPA.exe` (58MB) | collect.* / config.fields_collect / gui.collect_app / orchestrator.collect_flow / utils(dpi,logger,wait) | **ERPia 계열 문자열 0건** |
| `ERPia_RPA.exe` (22MB) | automation.* / config.fields_erpia / gui.erpia_app / orchestrator.erpia_flow / utils 전체 | **수집 계열 문자열 0건** |
| `Integrated_RPA.exe` (58MB) | 양쪽 전부 | 정상 (둘 다 포함) |

문자열 검사는 exe 바이너리에서 직접 찾았다 (utf-8 / utf-16-le 양쪽).
검사어: 설정에서 온 값(`tools/probe_leaks` 와 같은 출처 — ERPia 설치 폴더·실행 파일 이름) `target_exe` `delivery_company` `login_company_code`
`order_mapping` `물류대기` `매출처리` `택배사` `주문매핑` /
git 밖 사이트 파일의 값(`probe_leaks` 와 같은 출처) `mail_url` `mail_senders` `download_base_dir` `phonelink` `sms_source`
`받은메일함` `인증번호` `playwright` `차수`.

> `automation` 이라는 문자열은 수집 배포본에도 3번 나오지만 전부
> `comtypes.automation` 같은 **서드파티 모듈 이름**이다. 우리 코드가 아니다.

## 한계 — 분명히 해 둘 것

PyInstaller 실행 파일은 **뜯어서 파이썬 바이트코드를 복원할 수 있다.**
위 조치는 역공학을 막는 것이 아니라, **애초에 없는 것은 가져갈 수 없게** 하는 것이다.
그 이상이 필요하면 바이트코드 난독화를 얹는 선택지가 있으나, 시간을 버는 수준이지
근본 해결이 아니다.


---

## 2026-09-10 변경 — 배포 폴더를 기능별로 나눴다

예전에는 세 배포본이 `dist\` 를 공유하고 `dist\config\settings.local.json`
하나를 함께 썼다. 그래서 이런 일이 생겼다.

1. `dist` 의 exe 를 실제 계정으로 한 번 돌리면 그 파일에 **평문 계정**이 남는다
   (`gui/erpia_app.py` 가 [실행] 때 `save_local()` 로 기록한다)
2. `if not exist` 가드 때문에 **재빌드해도 지워지지 않는다**
3. 빌드 끝에 그 파일을 "함께 배포" 하라고 안내했다

같은 경로로 실제 사고가 한 번 있었다(`docs/archive/PROGRESS_20260917.md` 다음 할 일 2번).

지금은 이렇게 한다.

| | 값 |
| --- | --- |
| 산출물 | `dist\collect\` / `dist\erpia\` / `dist\full\` |
| 빌드 시작 | 그 폴더를 **통째로 지운다** (`rmdir /s /q`) |
| 설정 | 매 빌드마다 **샘플로 덮어쓴다** (`copy /y`) |
| 빌드 끝 안내 | "빈 샘플만 같이 준다. 계정이 채워진 파일은 배포 금지" |

**동작 확인은 `dist` 밖에 복사해서 한다.** 넘겨주기 전에 exe 옆 `logs\` 를
비우는 것도 잊지 않는다 — 위 "배포할 것" 절에 이유를 적어 뒀다
(2026-09-10부터 ERPia 배포본도 로그를 남긴다).

### 유출 검사가 실제로 작동한다 (2026-09-10 수정)

`build_*.spec` 이 `DEV_ONLY` / `FORBIDDEN` 을 `excludes` 에 넣으면서 그 뒤에
`a.pure` 에서 찾아 중단하도록 되어 있었는데, `excludes` 로 준 이름은 `a.pure` 에
**애초에 나타나지 않는다.** 그래서 검사가 항상 통과했다(이중 안전장치가
이름만 있었다). 지금은 `excludes` 에 `UNUSED_LIBS` 만 두고, 금지 모듈이 실제로
번들에 들어갔는지 보고 **들어가면 빌드를 중단한다.**

### 작업 폴더를 지우는 줄이 4개 다 틀려 있었다 (2026-09-15 수정)

PyInstaller 의 작업 폴더는 **spec 파일 이름**을 따른다. exe 이름이 아니다.

| bat | 지우려던 것 | 실제 폴더 |
| --- | --- | --- |
| `build_collect.bat` | `build\Collect_RPA` | `build\build_collect` |
| `build_erpia.bat` | `build\ERPia_RPA` | `build\build_erpia` |
| `build_full.bat` | `build\Integrated_RPA` | `build\build_full` |
| `build_run.bat` | `build` + **0x08** + `uild_run` | `build\build_run` |

넷 다 없는 폴더를 지우고 있었으므로 **"지난 산출물을 지운다"가 한 번도 동작하지
않았다.** PyInstaller 가 파일 변경을 보고 다시 컴파일하므로 잘못된 exe 가 나온
적은 없지만, 깨끗한 빌드를 보장하던 장치가 이름만 있었다.

★ **`build_run.bat` 의 0x08 은 오타가 아니라 도구가 만든 것이다.**
`build\build_run` 을 파이썬 문자열 리터럴로 적어 파일에 쓰면, 전달 과정에서
백슬래시가 한 겹 먹히고 남은 `\b` 가 **백스페이스 문자**로 해석된다.
`\f`(폼피드), `\a`, `\v`, `\t`, `\n` 도 같다 — 같은 원인으로
`docs/BUILD.md` 와 `docs/archive/TEST_20260910_FULL.md` 의 `dist\full` 이
`dist`+0x0C+`ull` 로 망가져 있었다(함께 고쳤다).

**Windows 경로를 파일에 쓸 때는 백슬래시를 리터럴로 쓰지 않는다.**
`chr(0x5C)` / `bytes([0x5C])` 로 만들어 붙인다. 고친 뒤에는
`cat -A` 로 `^H` 같은 제어문자가 없는지 본다.
