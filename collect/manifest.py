r"""수집 RPA 와 ERPia RPA 를 잇는 계약 — `manifest.json`.

코드로 직접 부르지 않고 **파일로 넘긴다.** 그래야 각각 단독 실행이 되고,
중간에 실패해도 이어서 돌릴 수 있다.

날짜 폴더 하나에 매니페스트 하나다.

    <기준경로>\2026-09-08\manifest.json

```json
{
  "date": "2026-09-08",
  "items": [
    {
      "seq": 1,
      "site": "사이트A",
      "path": "2026-09-08_001_사이트A/주문내역.xlsx",
      "mail_subject": "[사이트A] 주문내역",
      "mail_key": "0123456789abcdef01234567",
      "downloaded_at": "2026-09-08T09:12:03",
      "status": "downloaded",
      "error": null,
      "consumed_at": null
    }
  ]
}
```

- `path` 는 **날짜 폴더 기준 상대경로**다. 폴더째 옮겨도 깨지지 않는다.
- ERPia 쪽은 `site` 를 사이트 그리드의 `사이트명` 과 **완전일치**로 찾는다.
- 업로드에 성공하면 `consumed_at` 을 채운다 → 같은 파일을 두 번 올리지 않는다.
- `status="failed"` 인 항목은 **메일이 이미 읽음이 되어 미읽음 조건에 안 걸린다** (읽음을 되돌리지 않는다, 09-30).
  그래서 실패도 반드시 남긴다. 다음 실행은 이 기록으로 다시 연다 (`webmail.retry_keys`).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from collect import storage
from utils.logger import get_logger

log = get_logger(__name__)

FILE_NAME = "manifest.json"

STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"
# 열어 보니 대상이 아니었던 메일. **실패가 아니다.**
# 첨부가 엑셀이 아니면 여기로 간다. 다시 열 필요가 없으므로 읽음으로 둔다.
STATUS_SKIPPED = "skipped"


@dataclass
class Item:
    seq: int
    site: str
    path: str | None
    mail_subject: str
    mail_key: str
    downloaded_at: str | None = None
    status: str = STATUS_DOWNLOADED
    error: str | None = None
    consumed_at: str | None = None


@dataclass
class Manifest:
    date: str
    items: list[Item] = field(default_factory=list)

    # --- 조회 -------------------------------------------------------------
    def has_mail(self, mail_key: str) -> bool:
        return any(item.mail_key == mail_key for item in self.items)

    def find_mail(self, mail_key: str) -> "Item | None":
        """그 메일의 기록. 없으면 None. **상태를 보고 판단할 때 쓴다.**"""
        for item in self.items:
            if item.mail_key == mail_key:
                return item
        return None

    def failed(self) -> list["Item"]:
        return [i for i in self.items if i.status == STATUS_FAILED]

    def succeeded(self) -> list[Item]:
        return [i for i in self.items if i.status == STATUS_DOWNLOADED]

    def pending(self) -> list[Item]:
        """아직 ERPia 가 소비하지 않은 항목."""
        return [i for i in self.succeeded() if not i.consumed_at]

    # --- 변경 -------------------------------------------------------------
    def add(self, item: Item) -> None:
        self.items.append(item)

    def mark_consumed(self, item: Item) -> None:
        """그 항목에 소비 표시. mail_key 로 찾지 않는다 — 받기 실패 뒤 다시 받으면 같은 키가 둘이다 (10-02 검토)."""
        if not any(entry is item for entry in self.items):
            raise KeyError(f"매니페스트에 없는 항목이다: {item.mail_key}")
        item.consumed_at = datetime.now().isoformat(timespec="seconds")


def path_for(day: date | None = None) -> Path:
    return storage.date_dir(day, create=True) / FILE_NAME


def load(day: date | None = None) -> Manifest:
    """없으면 빈 매니페스트를 돌려준다."""
    day = day or date.today()
    file_path = path_for(day)
    if not file_path.exists():
        return Manifest(date=day.strftime("%Y-%m-%d"))
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{file_path} 를 읽을 수 없다: {exc}") from exc
    return Manifest(
        date=raw.get("date", day.strftime("%Y-%m-%d")),
        items=[Item(**entry) for entry in raw.get("items", [])],
    )


def save(manifest: Manifest, day: date | None = None) -> Path:
    file_path = path_for(day)
    payload = {"date": manifest.date, "items": [asdict(i) for i in manifest.items]}
    file_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("매니페스트 저장: %s (항목 %d개)", file_path, len(manifest.items))
    return file_path
