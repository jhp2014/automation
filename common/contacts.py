"""``config/contacts.yaml`` 로더 — 담당자 이름 → (팀, 직책) 매핑.

본 파일의 위상:
    - 사내 담당자 실명을 담으므로 ``daily.yaml`` 처럼 git ignore 대상이다.
      추적되는 것은 ``config/contacts.yaml.example`` 뿐이며, 실행 PC에는
      예제를 복사해 실명 매핑을 직접 만들어 둔다(pull 로 오지 않음).
    - 보고 메시지 품질을 올리는 **보조 데이터**이므로 settings.yaml 과 달리
      거친 실패를 하지 않는다: 파일이 없으면 빈 매핑으로 동작한다(메시지에는
      플레이스홀더가 들어가므로 사람이 바로 알아챌 수 있다). 파싱/스키마
      오류는 raise 하되, 호출부(job)가 잡아서 경고 후 빈 매핑으로 계속한다.

스키마 — 동명이인은 두 가지 방법 모두 지원(파일에 적힌 순서가 우선순위)::

    # 1) 리스트로 기입
    홍길동:
      - {team: 정보보안팀, title: 책임}
      - {team: 네트워크팀, title: 선임}

    # 2) 같은 이름을 여러 번 기입 — 사람이 자연스럽게 저지르는 실수까지
    #    데이터 손실 없이 병합한다(일반 YAML 로더는 마지막 값으로 조용히
    #    덮어쓰므로, 본 로더는 노드 수준에서 직접 읽어 중복 키를 합친다).
    홍길동: {team: 정보보안팀, title: 책임}
    홍길동: {team: 네트워크팀, title: 선임}
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


_cached: Optional[Dict[str, List[Contact]]] = None


def load_contacts(*, force: bool = False) -> Dict[str, List[Contact]]:
    """``config/contacts.yaml`` 을 로드+검증해 반환한다(1회 캐시).

    값은 단일 매핑 또는 리스트(동명이인) 둘 다 허용하며, 항상
    ``{이름: [Contact, ...]}`` 로 정규화해 반환한다. 리스트 순서가 우선순위다.

    Args:
        force: True 이면 캐시 무시하고 다시 읽는다(테스트용).

    Returns:
        ``{이름: [Contact, ...]}`` 매핑(각 리스트는 비어 있지 않음).
        파일이 없으면 빈 dict.

    Raises:
        RuntimeError: YAML 파싱 실패, 최상위가 dict 아님, 또는 스키마 검증 실패.
    """
    global _cached

    if _cached is not None and not force:
        return _cached

    if not CONTACTS_YAML_PATH.exists():
        _cached = {}
        return _cached

    # 중복 키 보존을 위해 safe_load 대신 노드 수준으로 읽는다. 일반 로더는
    # 같은 이름이 두 번 나오면 마지막 값으로 **조용히 덮어써** 앞 항목이
    # 사라지므로, 최상위 매핑의 (키, 값) 쌍을 순서대로 직접 꺼낸다.
    text = CONTACTS_YAML_PATH.read_text(encoding="utf-8")
    loader = yaml.SafeLoader(text)
    try:
        try:
            root = loader.get_single_node()
        except yaml.YAMLError as e:
            raise RuntimeError(f"contacts.yaml 파싱 실패: {e!r}") from e

        if root is None:
            _cached = {}
            return _cached

        if not isinstance(root, yaml.MappingNode):
            raise RuntimeError("contacts.yaml 최상위가 dict 가 아닙니다.")

        try:
            pairs = [
                (
                    loader.construct_object(k, deep=True),
                    loader.construct_object(v, deep=True),
                )
                for k, v in root.value
            ]
        except yaml.YAMLError as e:
            raise RuntimeError(f"contacts.yaml 파싱 실패: {e!r}") from e
    finally:
        loader.dispose()

    result: Dict[str, List[Contact]] = {}
    errors: list[str] = []
    for name, value in pairs:
        name = str(name).strip()
        items = value if isinstance(value, list) else [value]
        if not items:
            errors.append(f"  - {name}: 빈 리스트(최소 1개 매핑 필요)")
            continue
        contacts: List[Contact] = []
        for item in items:
            try:
                contacts.append(Contact.model_validate(item))
            except ValidationError as e:
                msgs = "; ".join(err["msg"] for err in e.errors())
                errors.append(f"  - {name}: {msgs}")
        if len(contacts) == len(items):
            # 같은 이름이 또 나오면(중복 키) 동명이인으로 간주해 뒤에 잇는다.
            result.setdefault(name, []).extend(contacts)

    if errors:
        raise RuntimeError("contacts.yaml 스키마 검증 실패:\n" + "\n".join(errors))

    _cached = result
    return _cached
