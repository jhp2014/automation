"""``config/settings.yaml`` 로더 — 동작 토글(headless / submit_by_enter)의 단일 소스.

본 파일의 위상:
    - ``.env`` / ``daily.yaml`` 과 달리 git 추적 대상이며, 비밀값을 절대 담지
      않는다(거의 안 바뀌는 동작 스위치만).
    - 우선순위 규약: ``CLI 인자 > config/settings.yaml``. settings.yaml 에 값이
      없고 CLI 로도 주지 않으면 해당 job 은 명확한 에러로 종료한다(**폴백 없음**).

스키마(전부 필수):
    zenius:        {headless: bool}
    daily_service: {headless: bool}
    jennifer:      {headless: bool}
    capture:       {headless: bool, submit_by_enter: bool,
                    refresh_targets: list[str], required_targets: list[str]}
    server:        {headless: bool, submit_by_enter: bool}

``common.daily`` 가 파일 부재를 None 으로 흡수하는 것과 달리, 본 로더는 파일 부재 /
파싱 실패 / 스키마 위반을 모두 **예외로 raise** 한다. 동작 토글이 정의되지 않은 채
실행되면 안 되기 때문이다(거친 실패가 의도).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


# common.config 를 import 하지 않고 BASE_DIR 를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_YAML_PATH: Path = _BASE_DIR / "config" / "settings.yaml"


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------

class JobSettings(BaseModel):
    """job 1개의 동작 토글.

    ``headless`` 는 모든 job 필수. ``submit_by_enter`` 는 server / capture 만
    의미가 있어 모델 수준에서는 선택이지만, 조회 헬퍼
    (:func:`common.config.get_submit_by_enter`) 가 None 이면 에러로 죽인다.
    ``refresh_targets`` / ``required_targets`` 는 capture 전용으로 동일하게 모델
    수준 선택 + 조회 헬퍼가 None 이면 에러로 죽인다.
    """

    model_config = ConfigDict(extra="forbid")

    headless: bool
    submit_by_enter: Optional[bool] = None
    refresh_targets: Optional[List[str]] = None
    required_targets: Optional[List[str]] = None


class SettingsConfig(BaseModel):
    """settings.yaml 전체 스키마. 5개 job 키가 모두 필수(누락은 검증 실패)."""

    model_config = ConfigDict(extra="forbid")

    zenius: JobSettings
    daily_service: JobSettings
    jennifer: JobSettings
    capture: JobSettings
    server: JobSettings


# ---------------------------------------------------------------------------
# 로딩 (1회 캐시)
# ---------------------------------------------------------------------------

_cached: Optional[SettingsConfig] = None


def load_settings(*, force: bool = False) -> SettingsConfig:
    """``config/settings.yaml`` 을 로드+검증해 반환한다(1회 캐시).

    Args:
        force: True 이면 캐시 무시하고 다시 읽는다(테스트용).

    Returns:
        검증을 통과한 :class:`SettingsConfig`.

    Raises:
        FileNotFoundError: settings.yaml 이 없는 경우(폴백 없음).
        RuntimeError: YAML 파싱 실패, 최상위가 dict 아님, 또는 스키마 검증 실패.
    """
    global _cached

    if _cached is not None and not force:
        return _cached

    if not SETTINGS_YAML_PATH.exists():
        raise FileNotFoundError(
            f"settings.yaml 없음: {SETTINGS_YAML_PATH} "
            "(동작 토글 파일은 폴백 없이 필수. CLI 로 --headless 등을 명시하면 "
            "본 파일 없이도 실행 가능)."
        )

    try:
        raw = yaml.safe_load(SETTINGS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"settings.yaml 파싱 실패: {e!r}") from e

    if not isinstance(raw, dict):
        raise RuntimeError("settings.yaml 최상위가 dict 가 아닙니다.")

    try:
        _cached = SettingsConfig.model_validate(raw)
    except ValidationError as e:
        details = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise RuntimeError(
            f"settings.yaml 스키마 검증 실패:\n{details}"
        ) from e

    return _cached
