# RPA 요구사항

Windows 업무 프로그램을 Python으로 자동 조작하는 RPA.
좌표 하드코딩 방식이 아니라, 안정적으로 동작하고 업무 추가가 쉬운 구조를 목표로 한다.

> **출발점(2026-09-03) 문서다. 자동으로 읽지 않는다** (사용자 확정 09-29). 원칙은 지금도 맞지만,
> 구체적인 것(흐름·실행 방식·구조·라이브러리)은 만들면서 바뀌었다 — 09-29 에 달라진 곳을 **★ 지금** 으로 고쳐 두었다.
> 작업 규칙의 최신판은 `CLAUDE.md`, 지금 상태는 `docs/HANDOFF.md`, 흐름·코드 지도는 `docs/PROGRESS.md`.

---

## 1. 개발 환경

- OS: Windows
- 언어: Python (공식 배포판, Anaconda 사용 안 함)
- IDE: VS Code
- 가상환경: 프로젝트별 `venv` (`.venv`)

### 라이브러리 후보

| 라이브러리 | 용도 | 사용 조건 |
|---|---|---|
| `pywinauto` | UI Automation | 주력 |
| `pyautogui` | 클릭/드래그/스크롤 | 상위 방법 불가 시 |
| `opencv-python`, `Pillow` | 이미지 인식 | 필요 시 |
| OCR 라이브러리 | 문자 인식 | 최후 수단 |

전부 설치하지 않는다. 실제 필요 여부를 판단해서 추가하고 `requirements.txt`를 갱신한다.

**★ 지금** 쓰는 것: `pywinauto`(+`comtypes`·`pywin32`) — ERPia 전부 / `Playwright`(설치된 Edge) — 메일 수집 /
`Pillow` — 실패 스크린샷 / `psutil`(개발 계측만) / `PyInstaller`(빌드). **`pyautogui`·OpenCV·OCR 은 한 번도 필요 없었다**
— 좌표가 필요한 곳도 런타임 `rectangle()` + pywinauto 마우스로 끝났다.

---

## 2. 자동화 대상 업무 흐름

1. 업무 프로그램 실행
2. 로그인
3. 특정 업무 화면 열기
4. 화면의 버튼/입력창/메뉴 조작
5. 필요 시 클릭
6. 필요 시 드래그
7. 필요 시 스크롤
8. Excel 불러오기 기능 실행
9. 지정된 Excel 파일 선택
10. 이후 추가 클릭/입력/드래그/스크롤
11. 작업 완료 여부 확인
12. 성공/실패 결과 기록

**★ 지금** 의 실제 흐름: 메일 사이트에서 사이트별 엑셀 받기 → ERPia 로그인 → 주문매핑(엑셀 업로드·자동수집)
→ 매출처리(조회된 주문만) → 물류대기(부족 재고 배송보류 → 저장) → 물류관리(개별배송 → 택배사·박스 → 저장 → 운송장출력).
고른 기능만 돈다. 화면 단위 상세는 `docs/PROCESS.md`, 단계 표는 `docs/PROGRESS.md`.

---

## 3. 프로그램 실행 방식

바탕화면 바로가기를 더블클릭하지 않는다. 설정 파일에 EXE 경로를 지정하고 직접 실행한다.

```
C:\Program Files\Company\ERP\ERP.exe
```

### 동작 순서

1. 프로그램이 이미 실행 중인지 확인
2. 실행 중이면 기존 프로세스에 연결
3. 실행 중이 아니면 EXE 직접 실행
4. 로그인 창 또는 메인 창이 나타날 때까지 대기
5. 자동화 시작

EXE 경로는 코드에 하드코딩하지 않고 설정에서 관리한다.
바로가기에 실행 인자나 Working Directory가 지정되어 있다면 확인해서 반영 가능한 구조로 만든다.

**★ 지금** (위 1~2 와 다르다): 업무 흐름은 **늘 새 인스턴스를 띄운다** (`application.start_new_instance`).
같은 계정이 이미 로그인돼 있으면 ERPia 가 막으므로 **그 계정의 앞 인스턴스만 닫고** 다시 로그인한다 — 다른 계정으로 쓰던
ERPia 는 건드리지 않는다. 기존 인스턴스에 붙는 것(`connect`, 가장 최근 것)은 **중간 단계부터 이어 하는 구간 실행**과 조사 도구만 쓴다 (붙을 것이 없으면 막는다). EXE 경로(`target_exe`)가
비었거나 틀리면 실행용 창이 이 PC 에서 `<실행 파일 이름>`(설정 `target_exe_name`) 을 찾아 넣는다 (`docs/SETTINGS.md` 1절).

---

## 4. UI 조작 우선순위

**상위 방법이 불가능한 이유를 확인하기 전에 하위 방법으로 내려가지 않는다.**

### 1순위 — Windows UI Automation

`pywinauto`의 `backend="uia"`로 실제 UI Control을 직접 찾는다.

대상 Control: Window, Button, Edit, Text, ComboBox, DataGrid, Menu, CheckBox 등

```python
window.child_window(title="로그인", control_type="Button").click()
```

### 2순위 — 키보드 조작

UI Automation으로 직접 처리하기 어려운 경우 먼저 검토한다.

Tab, Enter, Arrow, PageUp, PageDown, Home, End, Ctrl+C, Ctrl+V, 프로그램 자체 단축키

### 3순위 — 이미지 인식

Control을 찾을 수 없는 경우 화면 이미지 기반으로 탐색. 필요 시 OpenCV 사용.

### 4순위 — 좌표 기반 조작

다른 방법이 모두 불가능한 경우에만 사용한다.

```python
pyautogui.click(x, y)
```

좌표는 코드 전체에 흩어놓지 않고 설정 또는 UI 조작 모듈에서 관리한다.

---

## 5. 클릭 / 드래그 / 스크롤

사람의 행동을 그대로 재현할 필요는 없다. 사람이 ScrollBar를 드래그하더라도,
더 안정적인 대안이 있으면 그쪽을 쓴다.

- PageDown / End
- Scroll
- UI Automation ScrollPattern

드래그가 반드시 필요한 경우에만 `dragTo()` 등을 사용한다.

---

## 6. Excel 파일 선택

"Excel 불러오기" 버튼을 누르면 Windows 파일 선택 Dialog가 나타날 가능성이 높다.

폴더를 하나씩 클릭해서 찾지 않는다. Dialog를 UI Automation으로 제어해서
파일 전체 경로를 파일명 입력창에 직접 입력하고 연다.

```
C:\RPA\data\input.xlsx
```

Excel 경로도 설정에서 관리한다.

---

## 7. 화면 문자 읽기

### 1순위 — UI Automation

Control의 실제 Value/Text를 읽는다.
`window_text()`, UIA Value Pattern, Text Control, Edit Control, DataGrid Cell

### 2순위 — 클립보드

선택 가능한 값이면 `Ctrl+C`로 복사해서 클립보드에서 실제 문자열을 가져온다.

### 3순위 — OCR

위 방법이 모두 불가능한 경우에만 화면 일부를 캡처해서 OCR.

오인식 가능성이 있으므로 최후 수단으로만 쓴다.

| 혼동 |
|---|
| `0` / `O` |
| `1` / `l` |
| `5` / `S` |

상품코드, 금액, 수량 등 정확도가 중요한 데이터는 OCR 결과를 그대로 신뢰하지 않고 검증한다.

---

## 8. 대기 방식

`time.sleep()`을 남발하지 않는다.

```
잘못된 방식:  클릭 → 3초 대기 → 다음 작업
올바른 방식:  클릭 → 다음 화면/Control 등장 확인 → 다음 작업
```

### 조건 대기 대상

- Window가 나타날 때까지
- Button이 enabled 될 때까지
- 특정 Text가 나타날 때까지
- Dialog가 닫힐 때까지

UI Automation으로 상태 확인이 불가능한 경우에만 짧은 sleep을 제한적으로 허용한다.
**모든 대기에는 timeout을 둔다.**

---

## 9. 오류 처리

실패해도 원인을 찾을 수 있어야 한다.

- 단계별 로그
- 작업 시작/종료 기록
- 오류 내용 기록
- 현재 수행 중인 단계 기록
- timeout 처리
- 예외 처리 (무시 금지)
- 실패 시 화면 Screenshot 저장
- 제한된 횟수만 Retry

### 로그 예시

```
프로그램 실행 성공
로그인 화면 확인
로그인 성공
매출등록 화면 이동
Excel 불러오기
Excel 파일 선택 성공
```

사람이 로그만 보고 어느 단계에서 실패했는지 바로 알 수 있어야 한다.

---

## 10. 프로젝트 구조

`main.py` 하나에 전부 작성하지 않는다.
아래는 기준안이며, 더 나은 구조가 있으면 이유를 설명하고 변경해도 된다.

핵심은 **업무 흐름(`automation/`)과 공통 UI 조작(`utils/`)의 분리**다.

**★ 지금** 의 구조 (처음 기준안의 `work_screen.py`·`excel_import.py`·`image.py`·`images/`·`data/` 는 만들지 않았다):
`automation/`(ERPia 화면별 — application·login·order_mapping·logistics_wait·logistics 등) / `collect/`(메일 수집) /
`orchestrator/`(업무 흐름·단계 표·서버 보고) / `gui/`(창) / `utils/`(공통 UI 조작 — `ui.py` 가 단일 창구) / `config/` /
`tools/`(개발 전용) / `server/`·`web/`·`llm/`(서버 연동). 전체 트리는 `CLAUDE.md` "구조", 파일별 역할은 `docs/PROGRESS.md` 코드 지도.

---

## 11. 유지보수성

추후 추가될 수 있는 업무:

- 매출 등록
- 상품 등록
- 거래처 등록
- Excel 업로드
- 각종 조회 업무

특정 업무 하나에 종속된 구조로 만들지 않는다.

### 공통화 대상

프로그램 실행 및 연결, Window 찾기, Control 기다리기, 클릭, 텍스트 입력,
이미지 찾기, Scroll, Drag, 파일 Dialog 제어, 오류 Screenshot, Logging

---

## 12. 첫 번째 작업 — UI 조사

전체 코드를 먼저 만들지 않는다. 대상 프로그램이 UI Automation을 어느 정도
지원하는지 확인하는 것이 우선이다.

### 조사 항목

1. 실행 중인 대상 프로그램에 연결
2. 최상위 Window 확인
3. `print_control_identifiers()` 등으로 UI Control 구조 확인
4. Button / Edit / Text / DataGrid가 실제로 인식되는지 확인
5. Control의 `title`, `auto_id`, `control_type` 확인
6. 화면 문자열을 실제로 읽을 수 있는지 확인

조사 결과를 기준으로 각 UI 작업의 구현 방식을 분류한다.

- UI Automation으로 처리 가능
- 키보드로 처리하는 것이 적합
- 이미지 인식 필요
- 좌표 기반 조작 필요
- OCR 필요

결과는 `docs/UI_SURVEY.md`에 기록한다.

**★ 지금**: 조사는 끝났다. 확정된 식별값은 `docs/CONTROLS.md`(주력)에 있고, `docs/UI_SURVEY.md` 에는 초기 판정 근거와
좌표로 내려간 미해결 항목만 남는다. 새 화면은 스킬 `ui-survey` 절차로 조사한 뒤에 코드를 쓴다.

---

## 13. 개발 단계

추측으로 전체를 작성하지 않는다. 각 단계에서 동작을 확인하고 다음으로 넘어간다.

| Phase | 내용 |
|---|---|
| 1 | 프로젝트 기본 구조 생성 |
| 2 | 대상 프로그램 실행/연결 기능 |
| 3 | UI Automation 구조 조사 도구 |
| 4 | 로그인 자동화 |
| 5 | 업무 화면 이동 |
| 6 | Excel 파일 선택 |
| 7 | 클릭/스크롤/드래그 등 세부 업무 |
| 8 | 오류 처리, 로그, Retry, Screenshot |
| 9 | 전체 시나리오 통합 테스트 |

**★ 지금**: 1~9 전부 끝났다 (09-17 빌드본 완주). 그 뒤는 요청 단위로 한다 — 상태 표는 `docs/PROGRESS.md`.

---

## 14. 구현 원칙

### 금지

- 좌표 하드코딩
- `time.sleep()` 남용
- OCR 남용
- 프로그램 경로 하드코딩
- Excel 경로 하드코딩
- 예외 무시 (`except: pass`)
- 무제한 Retry
- 사용하지 않는 라이브러리 추가
- **존재를 확인하지 않은 `auto_id`, 좌표, Window 이름을 임의로 만들어 완성된 것처럼 구현**

### 필수

- UI Automation 우선
- 반복 코드 함수/모듈화
- 설정값과 업무 로직 분리
- 오류 발생 위치를 로그에서 확인 가능하게
- 코드 작성 전 기존 프로젝트 파일 구조 분석
- 필요한 패키지만 설치

---

## 현재 상태

이 문서는 **출발점(2026-09-03)** 이다 (09-29 에 달라진 곳을 ★ 로 고쳤다). 조사와 구현은 끝났고, 지금 상태는
`docs/HANDOFF.md`, 단계 표와 코드 지도는 `docs/PROGRESS.md` 에 있다. 처음 요구 뒤에 붙은 것 — 메일 수집·기능 선택·예약·
서버 보고·웹 대시보드·원격 제어·사용법 질문 — 은 `docs/BILLING.md`·`docs/SERVER_PLAN.md`·`docs/COLLECT_RPA.md` 가 기준이다.
