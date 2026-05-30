"""``config/settings.yaml`` 로더 — 동작 토글(headless / submit 등)의 단일 소스.

본 파일의 위상:
    - ``.env`` / ``daily.yaml`` 과 달리 git 추적 대상이며, 비밀값을 절대 담지
      않는다(거의 안 바뀌는 동작 스위치만).
    - 우선순위 규약: ``CLI 인자 > config/settings.yaml > 코드 기본값``.

스키마(부분 입력 허용):
    defaults:
      headless: true
    jobs:
      <job_key>:
        headless: true
        submit_by_enter: true        # server / capture 만 의미가 있음

파일이 없으면 :func:`load_settings` 가 None 을 반환한다 — 호출부는 코드 기본값으로
폴백한다. 파싱/스키마 검증에 실패해도 None + 경고 로그(파일이 운영자 수정 대상이라
거친 실패 대신 흡수). ``common.daily`` 와 동일 패턴.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError


# common.config 를 import 하지 않고 BASE_DIR 를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_YAML_PATH: Path = _BASE_DIR / "config" / "settings.yaml"


_log = logging.getLogger("common.settings")


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

class JobSettings(BaseModel):
    """job 1개의 동작 토글. 모든 필드가 선택(None=미지정)."""

    headless: Optional[bool] = None
    submit_by_enter: Optional[bool] = None


class SettingsConfig(BaseModel):
    """settings.yaml 전체 스키마."""

    defaults: JobSettings = Field(default_factory=JobSettings)
    jobs: Dict[str, JobSettings] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 로딩 (1회 캐시)
# ---------------------------------------------------------------------------

_cached: Optional[SettingsConfig] = None
_already_attempted = False


def load_settings(*, force: bool = False) -> Optional[SettingsConfig]:
    """``config/settings.yaml`` 을 1회 로드해 캐시한다.

    Args:
        force: True 이면 캐시 무시하고 다시 읽는다(테스트용).

    Returns:
        파일이 정상이면 :class:`SettingsConfig`. 파일이 없으면 None. 파싱/스키마
        검증에 실패해도 None — 다만 경고 로그를 남긴다.
    """
    global _cached, _already_attempted

    if _already_attempted and not force:
        return _cached

    _already_attempted = True

    if not SETTINGS_YAML_PATH.exists():
        _cached = None
        return None

    try:
        raw = yaml.safe_load(SETTINGS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        _log.warning("settings.yaml 파싱 실패 — 기본값으로 폴백: %r", e)
        _cached = None
        return None

    # 빈 파일은 정상 — 모든 값이 기본(빈 dict / None) 으로 셋업된다.
    if raw is None:
        _cached = SettingsConfig()
        return _cached

    if not isinstance(raw, dict):
        _log.warning("settings.yaml 최상위가 dict 아님 — 기본값으로 폴백")
        _cached = None
        return None

    try:
        _cached = SettingsConfig.model_validate(raw)
    except ValidationError as e:
        details = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        _log.warning(
            "settings.yaml 스키마 검증 실패 — 기본값으로 폴백:\n%s", details
        )
        _cached = None
        return None

    return _cached
