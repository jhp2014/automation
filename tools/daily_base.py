"""``config/daily.base.yaml`` 로더 — 매일 *안 바뀌는* 것들의 단일 소스.

위상:
    - ``.env`` / ``config/daily.yaml`` 과 같이 git 추적 대상이 아니다(비밀값 포함).
    - 운영자가 1회 세팅해두는 템플릿 원본. :mod:`tools.gen_daily` 가 이 파일에
      날짜만 입혀 ``config/daily.yaml`` 을 생성한다.

``common.settings`` 와 동일하게 파일 부재 / 파싱 실패 / 스키마 위반을 모두
**예외로 raise** 한다(폴백 없음 — 잘못된 템플릿으로 daily.yaml 을 만드는 사고 방지).

스키마::

    operators:                       # 자격증명 풀(여러 운영자)
      <name>: { user_id, user_pw }
    default_operator: <name>
    title_template: "{date}({wd}) {shift} OP관제 일일보고"
    shifts:                          # 근무별(09/18/21 …) 독립 정의
      "09":
        label: 주간
        run_until: { day: 0, time: "20:40" }
        server_times:
          - { day: 0, time: "10:15", folders: ["..."] }
    launch_windows:                  # 실행 시각 → shift 자동 매칭
      - { from: "08:00", to: "10:00", shift: "09" }

``day`` 는 운영기준일 D 로부터의 오프셋(0=D, 1=D+1). ``time`` 은 ``"HH:MM"``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


# common.config 를 import 하지 않고 BASE_DIR 를 직접 계산 — 순환 의존 방지.
_BASE_DIR = Path(__file__).resolve().parent.parent
DAILY_BASE_YAML_PATH: Path = _BASE_DIR / "config" / "daily.base.yaml"


_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


def _validate_hhmm(v: str) -> str:
    if not _HHMM_RE.match(v):
        raise ValueError("'HH:MM' 형식이어야 합니다")
    datetime.strptime(v, "%H:%M")
    return v


class BaseOperator(BaseModel):
    """운영자 1명의 KWorks 자격증명."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    user_pw: str


class BaseTimeOffset(BaseModel):
    """``run_until`` 처럼 (날짜 오프셋, 시각) 한 쌍."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=0, le=1)
    time: str

    _check_time = field_validator("time")(_validate_hhmm)


class BaseCapture(BaseModel):
    """server_times entry 하나(촬영 시각 + 폴더 세트)."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=0, le=1)
    time: str
    folders: List[str] = Field(default_factory=list)

    _check_time = field_validator("time")(_validate_hhmm)

    @field_validator("folders")
    @classmethod
    def _check_folders(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("folders 는 최소 1개 이상이어야 합니다")
        return v


class BaseShift(BaseModel):
    """근무 1종(예: 09=주간, 18/21=야간)."""

    model_config = ConfigDict(extra="forbid")

    label: str                       # 제목에 들어갈 표기(주간 / 야간)
    run_until: BaseTimeOffset
    server_times: List[BaseCapture] = Field(default_factory=list)


class LaunchWindow(BaseModel):
    """실행 시각대 → shift 자동 매칭 한 줄."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    shift: str

    _check_from = field_validator("from_")(_validate_hhmm)
    _check_to = field_validator("to")(_validate_hhmm)


class DailyBase(BaseModel):
    """daily.base.yaml 전체 스키마."""

    model_config = ConfigDict(extra="forbid")

    operators: Dict[str, BaseOperator]
    default_operator: str
    title_template: str
    shifts: Dict[str, BaseShift]
    launch_windows: List[LaunchWindow] = Field(default_factory=list)

    @field_validator("shifts", "operators", mode="before")
    @classmethod
    def _stringify_keys(cls, v):
        """YAML 이 ``18`` 을 int 키로 줄 수 있어 문자열로 정규화."""
        if isinstance(v, dict):
            return {str(k): val for k, val in v.items()}
        return v

    @model_validator(mode="after")
    def _cross_checks(self) -> "DailyBase":
        if self.default_operator not in self.operators:
            raise ValueError(
                f"default_operator '{self.default_operator}' 가 operators 에 없습니다"
            )
        if not self.shifts:
            raise ValueError("shifts 가 비어 있습니다")
        for w in self.launch_windows:
            if str(w.shift) not in self.shifts:
                raise ValueError(
                    f"launch_windows 의 shift '{w.shift}' 가 shifts 에 없습니다"
                )
        return self


def load_daily_base(path: Optional[Path] = None) -> DailyBase:
    """``config/daily.base.yaml`` 을 로드+검증해 반환한다.

    Args:
        path: 테스트용 경로 오버라이드. None 이면 기본 경로.

    Returns:
        검증을 통과한 :class:`DailyBase`.

    Raises:
        FileNotFoundError: 파일이 없는 경우(폴백 없음).
        RuntimeError: YAML 파싱 실패, 최상위가 dict 아님, 또는 스키마 검증 실패.
    """
    p = path or DAILY_BASE_YAML_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"daily.base.yaml 없음: {p} "
            "(config/daily.base.yaml.example 을 복사해 채우세요)."
        )

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"daily.base.yaml 파싱 실패: {e!r}") from e

    if not isinstance(raw, dict):
        raise RuntimeError("daily.base.yaml 최상위가 dict 가 아닙니다.")

    try:
        return DailyBase.model_validate(raw)
    except ValidationError as e:
        details = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise RuntimeError(f"daily.base.yaml 스키마 검증 실패:\n{details}") from e
