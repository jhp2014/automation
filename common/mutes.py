"""``state/zenius_mutes.yaml`` — zenius 알람 제외(뮤트) 목록 관리.

용도: 짧은 주기로 꺼졌다 켜졌다를 반복(flapping)하는 호스트의 알람을
근무자가 근무 중에만 끌 수 있게 한다. 호스트 단위, 또는 호스트+이벤트
제목 단위로 제외한다.

생명주기(근무 인수인계 안전):
    - **runner 기동 시** :func:`reset_mutes_file` 로 초기화한다. 기존 파일은
      ``zenius_mutes_YYYYmmdd_HHMMSS.yaml.old`` 로 아카이브되므로 이전
      근무자의 뮤트가 다음 근무로 새어 들어가지 않는다(크래시/강제종료
      포함 — 종료 경로가 아니라 시작 경로에서 초기화하기 때문).
    - 근무자는 근무 중 이 파일을 직접 편집한다. zenius job 이 매 실행마다
      다시 읽으므로 저장 즉시 다음 감시 주기부터 반영된다.

실패 방향(fail-open): 파일이 없거나 비어 있으면 뮤트 없음. 파싱/스키마
오류는 raise 하되 호출부(zenius job)가 잡아서 **뮤트 없음으로 계속**한다
— 실수로 알람 전체가 꺼지는 것보다 시끄러운 쪽이 안전하다.

스키마::

    - host: C9300_Kyowon_Institutional_SW_A      # 호스트 알람 전부 제외
    - host: adwebds1kgcmm
      title: Memory Used (%)                      # 이 이벤트 제목만 제외
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml


# common.config 를 import 하지 않고 경로를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
MUTES_PATH: Path = _BASE_DIR / "state" / "zenius_mutes.yaml"


_TEMPLATE = """\
# zenius 알람 제외(뮤트) 목록 — 이번 근무에서만 유효.
# runner 기동 시 자동 초기화되며, 이전 근무 파일은 zenius_mutes_*.yaml.old 로 보관된다.
#
# 형식 (host 만 쓰면 그 호스트 알람 전부 제외, title 까지 쓰면 그 이벤트 제목만 제외):
#   - host: C9300_Kyowon_Institutional_SW_A
#   - host: adwebds1kgcmm
#     title: Memory Used (%)
#
# host 는 EMS "호스트명", title 은 EMS "이벤트 제목" 과 정확히 일치해야 한다
# (보고 메시지의 "3. 이슈현상" 맨 앞 부분이 이벤트 제목이다).
# 저장하면 다음 감시 주기부터 바로 반영되고, 줄을 지우면 다시 알람이 살아난다.
"""


@dataclass(frozen=True)
class MuteRule:
    """뮤트 규칙 1건. ``title`` 이 None 이면 호스트 전체 뮤트."""

    host: str
    title: Optional[str] = None

    def matches(self, host: str, title: str) -> bool:
        """이벤트(host, title)가 본 규칙에 걸리는지 판정한다."""
        if self.host != (host or "").strip():
            return False
        if self.title is None:
            return True
        return self.title == (title or "").strip()


def load_mutes(path: Path = MUTES_PATH) -> List[MuteRule]:
    """뮤트 파일을 로드한다(캐시 없음 — 근무 중 수정이 즉시 반영돼야 한다).

    Returns:
        :class:`MuteRule` 리스트. 파일이 없거나 내용이 비면 빈 리스트.

    Raises:
        RuntimeError: YAML 파싱 실패 또는 스키마 위반(리스트 아님, host 누락 등).
    """
    if not path.exists():
        return []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"뮤트 파일 파싱 실패: {path} -> {e!r}") from e

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError(f"뮤트 파일 최상위가 리스트가 아닙니다: {path}")

    rules: List[MuteRule] = []
    errors: List[str] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            errors.append(f"  - [{i}] 항목이 dict 가 아님: {item!r}")
            continue
        unknown = set(item) - {"host", "title"}
        if unknown:
            errors.append(f"  - [{i}] 알 수 없는 키: {sorted(unknown)}")
            continue
        host = str(item.get("host") or "").strip()
        if not host:
            errors.append(f"  - [{i}] host 누락/빈 값: {item!r}")
            continue
        title_raw = item.get("title")
        title = str(title_raw).strip() if title_raw is not None else None
        if title == "":
            errors.append(f"  - [{i}] title 이 빈 값(생략하거나 제목을 기입): {item!r}")
            continue
        rules.append(MuteRule(host=host, title=title))

    if errors:
        raise RuntimeError("뮤트 파일 스키마 오류:\n" + "\n".join(errors))

    return rules


def find_mute(rules: List[MuteRule], host: str, title: str) -> Optional[MuteRule]:
    """이벤트에 걸리는 첫 번째 뮤트 규칙을 반환한다(없으면 None)."""
    for r in rules:
        if r.matches(host, title):
            return r
    return None


def reset_mutes_file(path: Path = MUTES_PATH) -> Optional[Path]:
    """뮤트 파일을 초기화한다(runner 기동 시 호출).

    기존 파일이 있으면 ``zenius_mutes_YYYYmmdd_HHMMSS.yaml.old`` 로 아카이브한
    뒤 주석 템플릿만 담긴 새 파일을 만든다. 아카이브(rename)가 실패하면
    템플릿으로 덮어써서라도 이전 뮤트가 새 근무로 새지 않게 한다.

    Returns:
        아카이브된 파일 경로(기존 파일이 없었으면 None).

    Raises:
        OSError: 아카이브도 덮어쓰기도 모두 실패한 경우(호출부가 로그로 경고).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    archived: Optional[Path] = None
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = path.with_name(f"{path.stem}_{ts}{path.suffix}.old")
        try:
            path.rename(candidate)
            archived = candidate
        except OSError:
            # rename 실패(파일 잠김 등) -> 아래 덮어쓰기로라도 초기화.
            archived = None

    path.write_text(_TEMPLATE, encoding="utf-8")
    return archived
