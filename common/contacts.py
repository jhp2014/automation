"""``config/contacts.yaml`` 로더 — 담당자 이름 → (팀, 직책) 매핑.

본 파일의 위상:
    - 사내 담당자 실명을 담으므로 ``daily.yaml`` 처럼 git ignore 대상이다.
      추적되는 것은 ``config/contacts.yaml.example`` 뿐이며, 실행 PC에는
      예제를 복사해 실명 매핑을 직접 만들어 둔다(pull 로 오지 않음).
    - 보고 메시지 품질을 올리는 **보조 데이터**이므로 settings.yaml 과 달리
      거친 실패를 하지 않는다: 파일이 없으면 빈 매핑으로 동작한다(메시지에는
      플레이스홀더가 들어가므로 사람이 바로 알아챌 수 있다). 파싱/스키마
      오류는 raise 하되, 호출부(job)가 잡아서 경고 후 빈 매핑으로 계속한다.

스키마::

    우상원: {team: IT개발지원팀, title: 파트장}
    홍길동: {team: 정보보안팀, title: 책임}
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


# common.config 를 import 하지 않고 BASE_DIR 를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
CONTACTS_YAML_PATH: Path = _BASE_DIR / "config" / "contacts.yaml"


class Contact(BaseModel):
    """담당자 1명의 팀/직책."""

    model_config = ConfigDict(extra="forbid")

    team: str
    title: str


_cached: Optional[Dict[str, Contact]] = None


def load_contacts(*, force: bool = False) -> Dict[str, Contact]:
    """``config/contacts.yaml`` 을 로드+검증해 반환한다(1회 캐시).

    Args:
        force: True 이면 캐시 무시하고 다시 읽는다(테스트용).

    Returns:
        ``{이름: Contact}`` 매핑. 파일이 없으면 빈 dict.

    Raises:
        RuntimeError: YAML 파싱 실패, 최상위가 dict 아님, 또는 스키마 검증 실패.
    """
    global _cached

    if _cached is not None and not force:
        return _cached

    if not CONTACTS_YAML_PATH.exists():
        _cached = {}
        return _cached

    try:
        raw = yaml.safe_load(CONTACTS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"contacts.yaml 파싱 실패: {e!r}") from e

    if raw is None:
        _cached = {}
        return _cached

    if not isinstance(raw, dict):
        raise RuntimeError("contacts.yaml 최상위가 dict 가 아닙니다.")

    result: Dict[str, Contact] = {}
    errors: list[str] = []
    for name, value in raw.items():
        try:
            result[str(name).strip()] = Contact.model_validate(value)
        except ValidationError as e:
            msgs = "; ".join(err["msg"] for err in e.errors())
            errors.append(f"  - {name}: {msgs}")

    if errors:
        raise RuntimeError("contacts.yaml 스키마 검증 실패:\n" + "\n".join(errors))

    _cached = result
    return _cached
