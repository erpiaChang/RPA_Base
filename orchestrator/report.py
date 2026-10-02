r"""실행이 끝난 뒤 보는 **HTML 리포트 한 장.**

    logs/report_20260915_183012.html

## 왜 파일로 만드나

화면은 자리가 좁다. ERPia 가 최대화돼 있고, 오버레이는 한 줄뿐이고, 본 창의
상세 표도 몇 줄만 보인다. 그런데 사용자가 실제로 확인하고 싶은 것은
**"오늘 무엇을 처리했나"** — 올린 엑셀 목록, 보류된 상품 목록, 미매출 주문수
변화다. 브라우저면 표가 길어도 스크롤하면 되고, 복사해서 전달할 수도 있다.

## 기능 이름을 모른다

`Result.steps` 에 들어 있는 것만 그린다 — 단계 이름, 상태, 상세 표(컬럼과 줄).
그 표는 **기능을 아는 흐름 쪽이 만들어 담아 둔 것**이다
(`orchestrator/steps.py` 의 `StepEvent.columns` 주석).
그래서 이 파일은 어느 배포본에 들어가도 안전하다.

## 숫자를 지어내지 않는다

여기서 새로 계산하는 값은 **단계 상태 개수와 총 소요시간뿐**이다. 나머지는
흐름이 담아 둔 것을 그대로 옮긴다. 그리드 행 수를 총 건수처럼 보이게 바꾸는
따위를 하지 않는다 (가상 스크롤이라 보이는 행만 세진다).
"""
from __future__ import annotations

import html
import time
from datetime import datetime
from pathlib import Path

from config.settings import LOG_DIR
from orchestrator import steps
from utils.logger import get_logger

log = get_logger(__name__)

# 파일 로그와 같은 폴더 (exe 옆 `logs`). 상대 경로 `logs` 로 두면 cwd 를 따라간다 (09-22 정리)
REPORT_DIR = LOG_DIR
REPORT_PREFIX = "report_"

# 단계 표와 같은 색을 쓴다. 화면에서 본 것과 리포트가 달라 보이면 헷갈린다.
STATE_COLORS = {
    steps.PENDING: "#8a949d",
    steps.RUNNING: "#0b5fa5",
    steps.DONE: "#1b7a3d",
    steps.SKIPPED: "#8a949d",
    steps.FAILED: "#b00020",
    steps.CANCELLED: "#a06000",
}

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 28px 64px;
       font: 14px/1.6 "Malgun Gothic", "Segoe UI", system-ui, sans-serif;
       color: #1d2530; background: #f6f7f9; }
main { max-width: 1040px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 10px; padding-bottom: 6px;
     border-bottom: 1px solid #dfe3e8; }
.when { color: #6b7684; font-size: 13px; margin: 0 0 18px; }
.summary { background: #fff; border: 1px solid #dfe3e8; border-radius: 10px;
           padding: 14px 16px; margin: 0 0 8px; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 0; }
.badge { border-radius: 999px; padding: 3px 11px; font-size: 12px;
         color: #fff; white-space: nowrap; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #dfe3e8; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #eef1f4;
         vertical-align: top; word-break: break-word; }
th { background: #f0f2f5; font-weight: 600; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td.num { white-space: nowrap; color: #6b7684; }
.state { font-weight: 600; white-space: nowrap; }
.detail { margin: 26px 0 0; }
.detail h3 { font-size: 14px; margin: 0 0 8px; }
.detail .none { color: #8a949d; font-size: 13px; }
.err { color: #b00020; font-size: 13px; margin: 6px 0 0; white-space: pre-wrap; }
.summary { white-space: pre-line; }
.fail { background: #fff; border: 1px solid #f0c4c9; border-left: 4px solid #b00020;
        border-radius: 8px; padding: 10px 14px; margin: 0 0 10px; }
.fail p { margin: 4px 0 0; white-space: pre-line; }
details { margin: 8px 0 0; font-size: 12px; color: #6b7684; }
details pre { white-space: pre-wrap; word-break: break-all; background: #f6f7f9;
              padding: 8px; border-radius: 6px; margin: 6px 0 0; }
a { color: #0b5fa5; }
.attention { background: #fff; border: 1px solid #ecd3a8; border-left: 4px solid #a06000;
             border-radius: 8px; padding: 6px 14px; margin: 0 0 10px; }
.attention ol { margin: 6px 0; padding-left: 20px; }
.attention li { margin: 4px 0; }
footer { color: #8a949d; font-size: 12px; margin-top: 40px; }
"""


class Link(str):
    """상세 표의 **누를 수 있는 칸**. 글자는 그대로 `str` 이고 `href` 를 하나 더 든다.

    엑셀 파일명·폴더처럼 사용자가 눌러서 열어 봐야 하는 값에 쓴다 (사용자 요청 09-21).
    str 이므로 `str` 만 아는 곳(화면 표, 오버레이)에서는 그냥 글자로 보인다.
    """

    href: str = ""
    path: str = ""          # 로컬 파일·폴더면 그 경로. 화면 표가 두 번 누르면 연다

    def __new__(cls, text: str, href: str, path: str = "") -> "Link":
        obj = super().__new__(cls, text)
        obj.href = href
        obj.path = path
        return obj


def path_link(path, text: str | None = None) -> Link | str:
    """로컬 파일·폴더를 `file:///` 링크로. 경로가 비었으면 `-`."""
    if not path:
        return "-"
    target = Path(path)
    try:
        resolved = target.resolve()
        href = resolved.as_uri()
    except (OSError, ValueError):
        return text or str(target)
    return Link(text or target.name or str(target), href, str(resolved))


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _cell(value) -> str:
    if isinstance(value, Link) and value.href:
        return f'<a href="{html.escape(value.href, quote=True)}">{_esc(value)}</a>'
    return _esc(value)


def _badge(label: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{_esc(label)}</span>'


def _state_cell(state: str) -> str:
    color = STATE_COLORS.get(state, "#1d2530")
    label = steps.STATE_LABELS.get(state, state)
    return f'<td class="state" style="color:{color}">{_esc(label)}</td>'


def _clock(stamp) -> str:
    """벽시계 시각 한 칸. **없으면 빈 칸이다** — 시각을 지어내지 않는다."""
    if not stamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def _steps_table(rows: list[dict]) -> str:
    out = ["<table><thead><tr><th>#</th><th>단계</th><th>상태</th>"
           "<th>내용</th><th>끝난 시각</th><th>시간</th></tr></thead><tbody>"]
    for index, entry in enumerate(rows, start=1):
        # 실패한 단계는 내용 칸이 비어 있었다 (09-21 검토) — 멈춘 이유 첫 줄을 쓴다.
        stopped = (entry.get("error") or "").split("\n")[0]
        detail = entry.get("detail") or stopped or entry.get("progress") or ""
        elapsed = entry.get("elapsed") or 0
        # 끝났으면 끝난 시각, 돌다 만 것이면 시작 시각을 보여 준다.
        when = _clock(entry.get("finished_at") or entry.get("started_at"))
        out.append(
            f"<tr><td class='num'>{index}</td><td>{_esc(entry.get('name'))}</td>"
            + _state_cell(entry.get("state", ""))
            + f"<td>{_esc(detail)}</td>"
            f"<td class='num'>{_esc(when)}</td>"
            f"<td class='num'>{elapsed:.1f}초</td></tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _detail_table(entry: dict) -> str:
    columns = list(entry.get("columns") or ())
    rows = list(entry.get("rows") or ())
    name = _esc(entry.get("name"))
    if not columns:
        return ""
    # ★ 제목 옆에 **표의 줄 수를 쓰지 않는다** (09-21 검토: "매출처리 — 5건" 이 44건 처리한
    #   날에 주문 5건으로 읽혔다). 대신 그 단계의 한 줄 결과를 붙인다.
    summary = entry.get("detail") or ""
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    if rows:
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(value)}</td>" for value in row) + "</tr>"
            for row in rows)
    else:
        body = (f"<tr><td colspan='{len(columns)}' class='num'>"
                "내용이 없습니다.</td></tr>")
    title = f"{name} — {_esc(summary)}" if summary else name
    return (f"<section class='detail'><h3>{title}</h3>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            "</section>")


def _errors(rows: list[dict]) -> str:
    """멈춘 단계. **사람 말**(`error`)을 보여 주고, 원문(`error_detail`)은 접어 둔다.

    원문에는 화면 컨트롤 이름·경로가 들어 있어 사용자는 읽지 못한다. 그래도 관리자에게
    리포트를 보내면 원인을 찾을 수 있어야 해서 버리지 않는다 (09-21).
    """
    bad = [e for e in rows if e.get("error")]
    if not bad:
        return ""
    items = []
    for entry in bad:
        items.append(f"<div class='fail'><b>{_esc(entry.get('name'))}</b>"
                     f"<p>{_esc(entry['error'])}</p>")
        detail = entry.get("error_detail")
        if detail and detail != entry["error"]:
            items.append("<details><summary>담당자용 기술 정보 — 문의할 때 이 리포트 파일을 "
                         "함께 보내 주세요</summary>"
                         f"<pre>{_esc(detail)}</pre></details>")
        items.append("</div>")
    # '멈춘 곳' 만 쓰면 ERPia 알림을 닫고 **넘어간** 단계(09-22)를 멈춘 것으로 읽는다
    return f"<h2>멈추거나 끝나지 않은 곳</h2>{''.join(items)}"


ATTENTION_COLOR = "#a06000"      # 주의(주황). 단계 표의 '중단' 과 같은 색이다


def _attention(items: list[str]) -> str:
    """**사람이 챙길 일** 상자. 없으면 빈 문자열 (09-21 검토: 문제가 상세 표에 흩어져 있었다)."""
    if not items:
        return ""
    lines = "".join(f"<li>{_esc(item)}</li>" for item in items)
    return (f"<h2>확인할 것 ({len(items)})</h2>"
            f"<div class='attention'><ol>{lines}</ol></div>")


def build(result, title: str = "RPA 실행 리포트", when=None) -> str:
    """리포트 HTML 한 장을 만든다. 파일로 쓰지는 않는다."""
    when = when or datetime.now()
    rows = list(getattr(result, "steps", None) or [])
    summary = getattr(result, "summary", "") or "(요약 없음)"
    extra = getattr(result, "details", None) or {}
    attention = list(extra.get("attention") or [])
    trigger = extra.get("trigger") or ""

    counted: dict[str, int] = {}
    for entry in rows:
        state = entry.get("state", "")
        counted[state] = counted.get(state, 0) + 1
    badges = "".join(
        _badge(f"{steps.STATE_LABELS.get(state, state)} {count}",
               STATE_COLORS.get(state, "#6b7684"))
        for state, count in sorted(counted.items()))
    if attention:
        # 초록 '완료 11' 만 보이면 "오늘 문제 없음" 으로 읽힌다 (09-21 검토).
        badges = _badge(f"확인할 것 {len(attention)}", ATTENTION_COLOR) + badges
    total = sum((entry.get("elapsed") or 0) for entry in rows)
    minutes, seconds = divmod(int(total), 60)
    took = f"{minutes}분 {seconds}초" if minutes else f"{seconds}초"
    # 흐름은 요약 조각을 " / " 로 잇는다. 한 줄로 두면 길어서 안 읽힌다 — 조각마다 한 줄.
    summary_lines = "\n".join(part.strip() for part in summary.split(" / "))

    details = "".join(_detail_table(entry) for entry in rows)
    if not details:
        details = "<p class='none'>상세를 담은 단계가 없습니다.</p>"
    head = " · ".join(part for part in (
        trigger, f"끝난 시각 {when:%Y-%m-%d %H:%M:%S}", f"걸린 시간 {took}",
        f"단계 {len(rows)}개") if part)

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>{_esc(title)}</title><style>{STYLE}</style></head>
<body><main>
<h1>{_esc(title)}</h1>
<p class="when">{_esc(head)}</p>
<div class="summary">{_esc(summary_lines)}<div class="badges">{badges}</div></div>
{_attention(attention)}
{_errors(rows)}
<h2>단계별 상세</h2>
{details}
<h2>진행 단계</h2>
{_steps_table(rows)}
<footer>이 파일은 실행이 끝날 때 자동으로 만들어집니다.
파란 글자(파일·폴더 이름)를 누르면 그 파일이나 폴더가 열립니다.
숫자는 프로그램이 읽은 값을 그대로 옮긴 것이며, 화면에 다 보이지 않아 끝까지
셀 수 없었던 수는 "이상" 으로, 읽지 못한 수는 "확인 못 함" 으로 적었습니다.</footer>
</main></body></html>"""


def write(result, directory: Path | None = None, when=None) -> Path | None:
    """리포트를 파일로 쓴다. 경로를 돌려준다. **실패해도 예외를 올리지 않는다.**

    리포트는 보조 산출물이다. 이것 때문에 실행이 실패로 끝나면 안 된다.
    """
    when = when or datetime.now()
    folder = Path(directory) if directory else REPORT_DIR
    path = folder / f"{REPORT_PREFIX}{when:%Y%m%d_%H%M%S}.html"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(build(result, when=when), encoding="utf-8")
    except Exception as exc:
        log.warning("리포트를 쓰지 못했다(계속): %s: %s", type(exc).__name__, exc)
        return None
    log.info("실행 리포트: %s", path)
    return path
