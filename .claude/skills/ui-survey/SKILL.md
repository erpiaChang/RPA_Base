---
name: ui-survey
description: Windows 데스크톱 프로그램의 UI Automation 구조를 조사하고, 각 UI 요소를 어떤 방식(UIA/키보드/이미지/좌표/OCR)으로 자동화할지 판정한다. pywinauto Control 트리 덤프, print_control_identifiers 출력 분석, auto_id/control_type 확인, "이 화면 조사해줘", "이 버튼 어떻게 클릭해야 해", "Control이 안 잡힌다", "UIA로 되는지 확인" 같은 요청에 반드시 사용한다. 새로운 업무 화면을 자동화하기 전 단계에서도 항상 먼저 사용한다. RPA 자동화 코드를 작성하기 전에 대상 화면의 조사 결과가 없다면 코드부터 쓰지 말고 이 스킬을 사용한다.
---

# UI Survey

pywinauto 기반 Windows 자동화에서, **코드를 쓰기 전에 대상 화면이 무엇을 지원하는지 확인**하는 절차.

이 스킬의 목적은 추측 코드를 막는 것이다. `auto_id`, `title`, 좌표를 실제로 확인하지 않은 채
자동화 코드를 작성하면 반드시 실패하고, 실패 원인도 알 수 없다.

## 전제

- 작업 규칙: `CLAUDE.md` (UI 조작·문자 읽기 우선순위)
- 조사 결과: **확정 식별값은 `docs/CONTROLS.md`**(화면별, 주력) / 판정 근거·좌표로 내려간 미해결은 `docs/UI_SURVEY.md`
- 조사 코드는 **읽기 전용**이다. 값을 입력하거나 버튼을 누르지 않는다. 화면을 열거나 조회만 하는 것도 조작이다 —
  `tools/test_*` 로 만들고 승인(`APPROVED-RUN`)을 받는다.
- 조사 도구는 이미 있다. 새로 만들지 않는다.
  - `tools/probe_screen.py` — 화면 조사 주력 (`--all` 은 그리드 셀 값까지)
  - `tools/probe_grid_raw.py` — 그리드 원시 UIA 트리·LegacyIAccessible 값 (컨트롤 보기에 없는 것)
  - `tools/survey_windows.py` — 최상위 Window 목록
  - `tools/dump_controls.py` — Control 트리 덤프
- 파일 접근은 프로젝트 폴더와 `~/.claude` 안으로 제한된다.
  덤프 출력은 `docs/` 밖에 쓰지 않는다.

---

## 절차

### 1. 조사 전 확인

사용자에게 확인할 것:

- 대상 프로그램이 지금 실행 중인가
- 조사할 화면이 지금 떠 있는가 (로그인 화면 / 메인 / 특정 업무 화면)
- 그 화면에 **되돌릴 수 없는 동작**(전송, 저장, 삭제)이 걸린 버튼이 있는가

화면이 특정되지 않으면 조사 결과가 섞인다. 화면 단위로 조사한다.

### 2. 연결

```python
from pywinauto import Application, Desktop

# 실행 중인 프로세스에 연결 (실행하지 않는다)
app = Application(backend="uia").connect(path=EXE_PATH)   # 또는 title_re, process
```

연결이 실패하면 그 자체가 결과다. 다음을 순서대로 확인한다.

1. `Desktop(backend="uia").windows()`로 최상위 Window 목록 덤프 → 실제 title 확인
2. `backend="uia"`로 안 잡히면 `backend="win32"`로 재시도
   (win32만 잡히면 구형 프레임워크. UIA 트리가 얕을 가능성이 높다)
3. 둘 다 실패 → 관리자 권한 문제일 수 있다. Claude Code를 같은 권한 수준에서 실행 중인지 확인

### 3. 트리 덤프

```python
win = app.window(title_re=".*")
win.print_control_identifiers(depth=None, filename="docs/_dump_<화면명>.txt")
```

`depth`를 제한하면 DataGrid 내부가 잘린다. 처음엔 전체를 뜨고, 너무 크면 하위 노드만 다시 뜬다.

덤프 파일은 크다. **전체를 읽지 말고** 조사 대상 요소 주변만 grep 해서 확인한다.

### 4. 요소별 판정

덤프에서 화면의 각 조작 대상에 대해 아래를 순서대로 판정한다.
**위에서 통과하면 아래는 보지 않는다.**

| 순위 | 조건 | 판정 |
|---|---|---|
| 1 | `auto_id`가 있고 안정적으로 보임 | UIA — `auto_id` 기준 |
| 1 | `auto_id`는 없지만 `title` + `control_type` 조합이 고유함 | UIA — title 기준 |
| 2 | Control은 잡히나 클릭/입력이 안 먹음, 또는 Control이 아예 없지만 Tab 순서로 도달 가능 | 키보드 |
| 3 | Control 자체가 트리에 없고, 시각적으로는 명확히 구분됨 | 이미지 인식 |
| 4 | 위 전부 불가 | 좌표 (최후) |

문자 읽기는 별도로 판정한다.

| 순위 | 조건 | 판정 |
|---|---|---|
| 1 | `window_text()` 또는 Value Pattern으로 값이 나옴 | UIA |
| 2 | 값이 비어 있지만 선택 후 Ctrl+C가 먹음 | 클립보드 |
| 3 | 위 전부 불가 | OCR (검증 필수) |

### 5. 안정성 확인

`auto_id`가 있다고 끝이 아니다. 다음을 확인한다.

- **동적 ID인가**: 화면을 닫았다 다시 열고 재덤프해서 `auto_id`가 바뀌는지 본다.
  숫자 인덱스만 있는 ID(`Button1`, `item_3`)는 순서에 의존할 수 있다.
- **중복인가**: 같은 `title` + `control_type` 조합이 여러 개면 `found_index`에 의존하게 된다.
  가능하면 부모 Container로 범위를 좁힌다.
- **지연 생성인가**: 화면 진입 직후엔 없다가 나중에 생기는 Control인지.
  → 이 경우 조건 대기 대상으로 기록한다.

### 6. 기록

확정한 식별값은 `docs/CONTROLS.md` 의 그 화면 절에, 판정 근거와 좌표·OCR 로 내려간 항목은 `docs/UI_SURVEY.md`
"미해결" 에 적는다. 기존 내용을 덮어쓰지 않는다 — 뒤집혔으면 옛 내용에 정정 표시를 하고 결론만 남긴다.

### 7. 덤프 파일 정리

`docs/_dump_*.txt` / `docs/_windows_*.txt`는 **중간 파일이지 산출물이 아니다.**

기록이 끝나면 삭제한다. 남겨두면 다음 조사 때 어느 것이 최신인지 알 수 없고,
`.claude/hooks/cleanup_temp.py`가 매 요청마다 잔여 파일로 보고한다.

삭제 전에 확인할 것:

- 판정에 쓴 `auto_id` / `title` / `control_type`이 `docs/CONTROLS.md`에 옮겨졌는가
- 대기 조건(진입/완료)이 기록됐는가
- 좌표/OCR로 내려간 항목이 "미해결"에 남았는가

세 가지가 모두 되어 있으면 덤프를 지운다. 다시 필요하면 다시 뜨면 된다.

---

## 기록 형식

```markdown
## 화면: 로그인
조사일: YYYY-MM-DD
Window: title="로그인", class_name="..."
backend: uia

### 요소

| 요소 | control_type | auto_id | title | 방식 | 비고 |
|---|---|---|---|---|---|
| 아이디 입력 | Edit | txtUserId | - | UIA | |
| 비밀번호 입력 | Edit | txtPasswd | - | UIA | |
| 로그인 버튼 | Button | - | 로그인 | UIA | title 고유 |
| 저장 체크박스 | - | - | - | 좌표 | 트리에 없음. 대안 없음 |

### 대기 조건
- 진입: Window title="로그인" 등장
- 완료: Window title="로그인" 소멸 + 메인 Window 등장

### 문자 읽기
- 오류 메시지: Static Control, window_text() 정상 동작

### 미해결
- 저장 체크박스가 UIA/이미지 모두 실패. 현재 좌표 사용 중이며 해상도 의존.
```

---

## 판정 시 주의

- **"안 잡힌다"를 확인 없이 결론내지 않는다.** UIA에서 안 보이는 흔한 원인:
  - depth 제한으로 잘림
  - 부모가 아니라 Desktop 최상위에 별도 Window로 떠 있음 (특히 Dialog, Popup, ComboBox 드롭다운)
  - 아직 생성 전 (대기 필요)
  - backend가 win32
- **DataGrid는 별도 취급한다.** 셀 단위로 Control이 잡히는지, 아니면 그리드 전체가 하나의
  Custom Control인지에 따라 접근이 완전히 달라진다. 후자면 키보드 이동 + 클립보드 조합을 먼저 검토한다.
- **좌표로 판정했으면 반드시 "미해결"에 남긴다.** 나중에 다른 방법이 발견될 수 있고,
  해상도/DPI 변경 시 깨지는 지점을 알아야 한다.
- 조사 결과가 없는 요소에 대해 **자동화 코드를 작성하지 않는다.** 모르면 모른다고 기록하고 멈춘다.

### ERPia 에서 겪은 것 (DevExpress / WinForms)

- **팝업은 최상위 창이 아니다** — 부모 창의 자식으로 뜬다. `Desktop.windows()` 로는 안 잡힌다.
- **그리드는 가상 스크롤이다** — 보이는 행·보이는 컬럼만 UIA 에 있다. 화면 밖 컬럼은 가로 스크롤바로 넘겨야 셀이 생긴다.
  세로로 넘길 때 **키보드를 쓰지 않는다** (행이 선택돼 다른 그리드가 다시 조회된다). 스크롤바 버튼만.
- **좌측 숫자(행 인디케이터)는 UIA 에 없다** (원시 보기·Legacy·Grid 패턴 전부). 건수는 라벨이나 번호 컬럼으로 센다.
  그룹 행·한 전표의 여러 상품 행 때문에 **행 수 ≠ 데이터 수**다.
- 행의 LegacyIAccessible 값에 모든 컬럼이 `;` 로 이어져 있다 — 셀 이름과 헤더가 어긋날 때 확인용.
- **모달을 여는 메뉴 항목은 Invoke 하면 창이 닫힐 때까지 돌아오지 않는다** — 마우스 클릭으로.
- `wrapper.descendants()` 는 `auto_id`·`title_re` 를 받지 않는다 (TypeError 가 묻힌다) — `ui.search` 를 쓴다.
- 우리 창(tkinter)은 UIA 에 위젯이 없다 — 코드 안에서 만들어 확인한다 (`probe_gui`).

## 조사 후

판정 결과를 사용자에게 표로 보고하고, 좌표/OCR로 떨어진 항목이 있으면 그것을 먼저 알린다.
그 항목들이 이 자동화의 가장 취약한 지점이기 때문이다.
