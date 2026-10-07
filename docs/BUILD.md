# 실행 파일 빌드

Python 설치나 터미널 없이 **exe 하나만 실행하면 동작하는** 형태로 묶는다.

## 빌드 — 하나뿐이다

**빌드는 실행용 `dist\run\RPA_1.exe` 하나다** (10-06 — 기능별 빌드 `build_collect/erpia/full` 과 그 진입점·창·런처를 지웠다.
기능만 따로 쓰려면 아래 빌드 프로그램에서 **기능 고정**으로 굽는다).

```
build_tool.bat  →  gui/build_app.py (창)  →  tools/build_run.py  →  dist\run\RPA_1.exe
.venv\Scripts\python.exe -m tools.build_run   콘솔 — 설정 그대로, 서버 등록 없이 (지금 빌드 ID 그대로)
```

빌드는 이전 `build\build_run` / `dist\run` 을 지우고 다시 만든다 (`tools/build_run.py`).
남겨두면 옛 코드가 섞인 exe 가 나온다. **앞 exe 를 남기려면 빌드 전에 다른 폴더로 옮긴다.**

**판 번호·자가 점검 (10-07)**
- 판 번호는 빌드가 정한다: `날짜-커밋 해시` (커밋 안 된 변경이 있으면 끝에 `+`). 번들 `config/version.txt` → `APP_VERSION` → 서버 보고·웹 PC 표. 개발 폴더에서 돌면 `dev`. HANDOFF 의 exe 줄에 이 값을 적는다.
- 빌드 끝에 **exe 의 임시 사본을 `--selfcheck` 로 켜 본다** (`build\selfcheck\`, 끝나면 지움) — 우리 모듈 전부 불러오기·구운 필수 값·씨앗·Tcl·UIA 클라이언트. 실패하면 빌드 실패("이 exe 를 쓰지 말 것"). 창·한 벌 확인·서버 확인 앞에서 끝나므로 빌드를 이 PC 에 묶지 않는다 (`utils/selfcheck.py`, `main_run.py` 맨 앞).
- 빌드 프로그램은 서버 등록이 빌드보다 먼저라, 점검이 실패해도 빌드 ID 는 이미 생겨 있다 (쓰지 않는 ID 로 남는다).

> **빌드는 요청받았을 때만 한다** (사용자 확정 2026-09-07).
> 코드를 고칠 때마다 자동으로 다시 빌드하지 않는다.
> 따라서 `dist/` 의 exe 는 **마지막으로 빌드한 시점의 코드**이며,
> 그 뒤의 수정은 반영돼 있지 않다. 배포 전에 한 번 빌드한다.

## 설정을 exe 안에 굽는다

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

화면(`gui/run_app.py`)에서 고칠 수 있는 값은 `docs/SETTINGS.md` '고정값은 로컬 설정이 못 덮는다' 절의 화면 항목 표(열세 가지 + 자동 실행 칸)뿐이다. 나머지는 구운 값을 그대로 쓴다. 흐름은 통합(`orchestrator/full_flow.py`)과 같다.
실행하면 화면에서 고친 값이 **exe 옆 `config\settings.local.json`** 에 저장돼
다음 실행에 다시 뜬다. 로그는 **exe 옆 `logs\`** 에 쌓이고, 실패 시 스크린샷도 같은 폴더에 남는다.

> ★ **구운 비밀번호는 `baked:` 로 감싸지만 약하다** — 키가 프로그램 안에 있어 exe 를
> 뜯으면 풀 수 있다 (`utils/secret.py`, 09-17). 이 exe 는 믿을 수 있는 사용자에게만 준다.
> 남에게 줄 때는 실행하며 생긴 `logs\` 와 `config\settings.local.json` 을 지우고 exe 만 준다
> (업체코드·아이디는 로그에서 `us****01` 로 가리지만 실패 스크린샷에는 화면이 그대로 찍힌다).

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

창에 `config/settings.local.json` 의 값이 절별로 보인다 — 개발 도구 전용 2개, 서버가 발급하는 빌드 ID, 그 PC 에서 정하는 값(ERPia 경로 둘·엑셀 저장 폴더·자동 켜기)은 빼고.
고쳐서 [설정 저장] 하면 그 파일에 쓰고(비밀번호는 `dpapi:` 로), [저장하고 빌드] 하면
서버에 빌드를 등록해 **새 빌드 ID** 를 받고(서버 주소가 비면 건너뛴다) `tools/build_run.py` 가 굽고 → 지난 산출물을 지우고 → PyInstaller → 구운 파일을 지운다.
로그가 오른쪽 칸에 흐르고 끝나면 exe 경로가 상태줄에 나온다. 몇 분 걸린다.

**실행할 기능**은 둘 중 하나로 굽는다 (사용자 확정 09-22, 설정 키 `run_modules_locked`):

| 고른 것 | 만들어진 exe |
| --- | --- |
| 기능 고정 | 고른 기능만 돈다. 실행 창의 [실행할 기능] 이 잠기고 예약도 그 기능 안에서만 고른다. exe 옆 설정이 `run_modules` 를 덮어도 무시한다 (번들 안 구운 값을 읽는다). 메일이 없으면 Playwright 가 빠진다 — **다른 기능의 코드는 들어간다** (기능별 빌드가 없어서, 10-06) |
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

PyInstaller 는 진입점 `main_run.py` 부터 **import 그래프를 따라간 것만** 넣는다.
아무 데서도 import 하지 않는 모듈은 자동으로 빠진다.

그 자동 동작에만 기대지 않는다. 프로그램이 커지면 실수로 개발용 모듈을
import 하는 일이 생기고, 그러면 조용히 배포본에 섞인다.
그래서 `build_run.spec` 에서 **이름으로 못 박고, 매 빌드마다 확인한다.**

| 항목 | 내용 |
|---|---|
| `HIDDEN` | pywinauto / comtypes 서브모듈 전부 + 설정 항목 파일 둘(`config.fields_collect`·`fields_erpia`). 문자열로 동적 import 해서 정적 분석에 안 잡힌다 — **빠뜨리면 빌드는 되지만 배포본에서 설정이 통째로 빈다** (실제로 한 번 겪었다) |
| `DEV_ONLY` | 개발·조사 전용. `tools` / `gui.build_app` / `llm` |
| `UNUSED_LIBS` | 설치돼 있으면 딸려 들어갈 수 있는 무거운 패키지 (numpy, pandas, PyQt, setuptools, pip …) |
| `datas` | 구운 설정과 씨앗 둘뿐. 문서·이미지는 넣지 않는다 |
| `console=False` | 검은 콘솔 창을 띄우지 않는다 |

빌드할 때마다 번들에 든 프로젝트 모듈 목록과 "금지 모듈 누출 없음" 이 출력된다.
의도하지 않은 모듈이 보이면 `DEV_ONLY` 에 추가한다. `DEV_ONLY` 모듈이 번들에 들어가면 **빌드가 그 자리에서 중단된다**
(런타임 코드가 개발용 모듈을 import 하고 있다는 뜻이므로 의존을 끊어야 한다).

★ **`excludes` 에 `DEV_ONLY` 를 넣지 않는다** (2026-09-10 수정). `excludes` 로 준 이름은 `a.pure` 에
**애초에 나타나지 않아** 누출 검사가 항상 통과한다. `excludes` 에는 `UNUSED_LIBS` 만 둔다.

### 앞으로 파일이 늘어날 때

| 종류 | 두는 곳 | 빌드 |
|---|---|---|
| 업무 흐름 | `automation/` | 포함 |
| 공통 UI 조작 | `utils/` | 포함 |
| 화면 조사 도구 (`probe_*`) | `tools/` | **제외** (`DEV_ONLY`) |
| 흐름 실행/테스트 도구 (`test_*`) | `tools/` | **제외** (`DEV_ONLY`) |
| 조사 자료 / 문서 | `docs/` | 제외 |
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

업무 흐름 전체를 exe 로 돌린 검증은 **09-17 에 했다** (빌드본 11단계 완주, `docs/HANDOFF.md` 1절).

## 권한

RPA 가 ERPia 를 **직접 띄우므로** 일반 권한으로 충분하다.
사용자가 관리자 권한으로 띄워 둔 ERPia 인스턴스에는 붙을 수 없다(Windows UIPI).
그런 환경이면 exe 를 관리자 권한으로 실행해야 한다.

## 주의

- 화면이 잠기면 마우스 클릭이 대상 프로그램에 전달되지 않는다.
  로그인 단계에서 이를 감지해 중단한다. 실행 중에는 **화면 잠금을 꺼 둔다.**
- 백신이 PyInstaller 산출물을 오탐하는 경우가 있다. 사내 배포 시 예외 등록이 필요할 수 있다.
- **Windows 경로를 파일에 쓸 때는 백슬래시를 리터럴로 쓰지 않는다** (2026-09-15). 전달 과정에서 한 겹 먹혀
  `\b`·`\f`·`\a`·`\v`·`\t`·`\n` 이 제어 문자로 박힌다 (옛 빌드 bat 의 `build\build_run` 이 `build`+0x08+`uild_run` 이 됐다).
  `chr(0x5C)` / `bytes([0x5C])` 로 만들어 붙이고, 고친 뒤 `cat -A` 로 `^H` 같은 제어문자가 없는지 본다.
