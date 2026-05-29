"""``config/daily.yaml`` 로더 — 매일 갱신하는 값들의 단일 소스.

본 파일의 위상:
    - ``.env`` 와 동격이다(git ignore 대상). 따라서 비밀값(KWorks 비밀번호 등)을
      넣어도 안전하다.
    - 매일 1회 운영자가 열어 갱신하는 단일 파일. 여기 한 곳을 고치면 runner /
      jobs.server / jobs.capture 가 모두 새 값을 본다.

스키마(부분 입력 허용):
    run_until: ""                              # "YYYY-MM-DD HH:MM" 또는 빈 값
    kworks:
      user_id: ""
      user_pw: ""
      target_title: ""
    server_times: []                            # [{at, args}, ...]

파일이 없으면 ``load_daily()`` 가 None 을 반환한다 — 호출부(common.config)는
None 일 때 빈 값으로 폴백하고, 실제로 그 값을 쓰는 job 이 사용 시점에 명확한
에러를 낸다("daily.yaml 의 ... 가 비어 있음").
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


# common.config 를 import 하지 않고 BASE_DIR 를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
DAILY_YAML_PATH: Path = _BASE_DIR / "config" / "daily.yaml"


_log = logging.getLogger("common.daily")


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class DailyKworks(BaseModel):
    """KWorks 일일 자격증명 + 작업 제목."""

    user_id: str = ""
    user_pw: str = ""
    target_title: str = ""


class DailyServerTime(BaseModel):
    """``jobs.server`` 의 one_time entry 하나."""

    at: str
    args: List[str] = Field(default_factory=list)

    @field_validator("at")
    @classmethod
    def _check_at(cls, v: str) -> str:
        if not _AT_RE.match(v):
            raise ValueError("at 형식은 'YYYY-MM-DD HH:MM' 이어야 합니다")
        datetime.strptime(v, "%Y-%m-%d %H:%M")
        return v


class DailyConfig(BaseModel):
    """daily.yaml 전체 스키마. 모든 필드 선택(누락은 빈 값으로 간주)."""

    run_until: str = ""
    kworks: DailyKworks = Field(default_factory=DailyKworks)
    server_times: List[DailyServerTime] = Field(default_factory=list)

    @field_validator("run_until")
    @classmethod
    def _check_run_until(cls, v: str) -> str:
        if not v:
            return ""
        if not _AT_RE.match(v):
            raise ValueError("run_until 형식은 'YYYY-MM-DD HH:MM' 또는 빈 값")
        datetime.strptime(v, "%Y-%m-%d %H:%M")
        return v


# ---------------------------------------------------------------------------
# 로딩 (1회 캐시)
# ---------------------------------------------------------------------------

_cached: Optional[DailyConfig] = None
_already_attempted = False


def load_daily(*, force: bool = False) -> Optional[DailyConfig]:
    """``config/daily.yaml`` 을 1회 로드해 캐시한다.

    Args:
        force: True 이면 캐시 무시하고 다시 읽는다(테스트용).

    Returns:
        파일이 정상이면 :class:`DailyConfig`. 파일이 없으면 None. 파싱/스키마
        검증에 실패해도 None — 다만 경고 로그를 남긴다(파일 자체가 운영자
        수정 대상이라 거친 실패 대신 흡수).
    """
    global _cached, _already_attempted

    if _already_attempted and not force:
        return _cached

    _already_attempted = True

    if not DAILY_YAML_PATH.exists():
        _cached = None
        return None

    try:
        raw = yaml.safe_load(DAILY_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        _log.warning("daily.yaml 파싱 실패 — 빈 값으로 폴백: %r", e)
        _cached = None
        return None

    # 빈 파일(파싱 결과 None)은 정상 — 모든 값이 기본(빈)으로 셋업된다.
    if raw is None:
        _cached = DailyConfig()
        return _cached

    if not isinstance(raw, dict):
        _log.warning("daily.yaml 최상위가 dict 아님 — 빈 값으로 폴백")
        _cached = None
        return None

    try:
        _cached = DailyConfig.model_validate(raw)
    except ValidationError as e:
        details = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        _log.warning(
            "daily.yaml 스키마 검증 실패 — 빈 값으로 폴백:\n%s", details
        )
        _cached = None
        return None

    return _cached
