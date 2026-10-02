r"""메일 사이트 연결 등록표 (10-02). **원본(git)에는 사이트가 없다.**

사이트 파일은 git 밖 `collect/sites/local/` 에 둔다 — 업체·시험 사이트의 주소·화면 요소가 git 에 들어가지 않게
(사용자 결정 10-02). 그 폴더의 `__init__.py` 가 `SITES = (모듈.SITE, …)` 를 **리터럴 import** 로 내놓는다 —
아래 import 를 PyInstaller 가 따라가 사이트 파일이 빌드에 들어간다. 계약은 `collect/webmail.MailSite`, 절차는 `docs/SITES.md`.
"""
from __future__ import annotations

from collect.webmail import MailError, MailSite, SiteMissing


def available() -> tuple[MailSite, ...]:
    """이 PC(이 빌드)에 있는 메일 사이트. 없으면 빈 튜플 — 원본(git)이 그렇다."""
    # ★ `from collect.sites import local` 꼴은 폴더가 없을 때 ModuleNotFoundError 가 아니라 ImportError 를 낸다 (10-02 검토)
    try:
        import collect.sites.local as local
    except ModuleNotFoundError as exc:
        if exc.name != "collect.sites.local":
            raise                       # 사이트 파일 안의 import 오류는 숨기지 않는다
        return ()
    return tuple(getattr(local, "SITES", ()))


def mail_site() -> MailSite:
    """메일 기능이 쓸 사이트 하나. 없으면 `SiteMissing`, 둘 이상이면 고를 설정이 아직 없어 멈춘다."""
    found = available()
    if not found:
        raise SiteMissing("메일 사이트 연결이 없다 — 사이트 파일(collect/sites/local/)이 이 빌드에 없다")
    if len(found) > 1:
        raise MailError(f"메일 사이트 연결이 {len(found)}개다 — 고르는 설정이 아직 없다 (docs/SITES.md)")
    return found[0]
