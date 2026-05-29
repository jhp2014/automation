"""runner 설정 로더: ``config/jobs.yaml`` 을 pydantic 으로 검증해 반환.

규약 v1.1 따름. 비밀값(SUPABASE_URL/KEY)은 ``.env`` 에서 읽는다 — YAML 에
박지 않는다.

스키마 핵심:
    - 최상위 ``RunnerConfig``: run_until, tick_sec, heartbeat_interval_sec,
      heartbeat_source, jobs
    - 각 ``JobConfig``: name, module, mode, timeout_sec, args(선택). mode 별
      추가 필드는 ``model_validator`` 로 사후 검증한다(잘못된 조합이면 어떤
      job·어떤 필드인지 명확한 메시지를 낸다).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from common import config as common_config
from common.daily import load_daily


# ---------------------------------------------------------------------------
# Supabase 자격증명 (runner heartbeat 전용, .env 에서만)
# ---------------------------------------------------------------------------

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")


# ---------------------------------------------------------------------------
# 모델
# ---------------------------------------------------------------------------

_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class OneTimeEntry(BaseModel):
    """one_time_list 의 한 entry."""

    at: str = Field(..., description='실행 시각, "YYYY-MM-DD HH:MM" 형식.')
    args: List[str] = Field(default_factory=list, description="job 에 그대로 전달할 인자 리스트.")

    @field_validator("at")
    @classmethod
    def _check_at(cls, v: str) -> str:
        if not _AT_RE.match(v):
            raise ValueError("at 형식은 'YYYY-MM-DD HH:MM' 이어야 합니다")
        # 실제 파싱 가능 여부도 확인.
        datetime.strptime(v, "%Y-%m-%d %H:%M")
        return v


JobMode = Literal["interval", "hourly_jitter", "one_time_list"]


class JobConfig(BaseModel):
    """job 설정 한 건."""

    name: str
    module: str = Field(..., description='실행할 모듈 (예 "jobs.zenius"). python -m <module> 로 호출된다.')
    mode: JobMode
    timeout_sec: int = Field(..., gt=0)
    args: List[str] = Field(default_factory=list, description="공통 인자(모드와 무관).")

    # mode 별 필드들 — 누락은 사후 검증에서 처리한다.
    interval_sec: Optional[int] = Field(default=None, gt=0)
    jitter_max_sec: Optional[int] = Field(default=None, ge=0)
    grace_sec: Optional[int] = Field(default=None, ge=0)
    times: Optional[List[OneTimeEntry]] = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "JobConfig":
        """모드별 필수 필드를 확인하고 잘못된 조합을 막는다."""
        if self.mode == "interval":
            if self.interval_sec is None:
                raise ValueError(
                    f"job '{self.name}': mode=interval 인데 interval_sec 누락"
                )
        elif self.mode == "hourly_jitter":
            if self.jitter_max_sec is None:
                raise ValueError(
                    f"job '{self.name}': mode=hourly_jitter 인데 jitter_max_sec 누락"
                )
            if self.grace_sec is None:
                raise ValueError(
                    f"job '{self.name}': mode=hourly_jitter 인데 grace_sec 누락"
                )
        elif self.mode == "one_time_list":
            if self.grace_sec is None:
                raise ValueError(
                    f"job '{self.name}': mode=one_time_list 인데 grace_sec 누락"
                )
            if not self.times:
                raise ValueError(
                    f"job '{self.name}': mode=one_time_list 인데 times 가 비었거나 누락"
                )
        return self


class RunnerConfig(BaseModel):
    """jobs.yaml 최상위 구조."""

    run_until: Optional[str] = Field(default=None, description='"YYYY-MM-DD HH:MM" 또는 빈 문자열/None.')
    tick_sec: int = Field(default=10, gt=0)
    heartbeat_interval_sec: int = Field(default=60, gt=0)
    heartbeat_source: str = Field(default="main_runner")
    jobs: List[JobConfig]

    @field_validator("run_until")
    @classmethod
    def _check_run_until(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not _AT_RE.match(v):
            raise ValueError("run_until 형식은 'YYYY-MM-DD HH:MM' 또는 빈 값")
        datetime.strptime(v, "%Y-%m-%d %H:%M")
        return v

    @model_validator(mode="after")
    def _check_job_names_unique(self) -> "RunnerConfig":
        names = [j.name for j in self.jobs]
        if len(names) != len(set(names)):
            dup = [n for n in set(names) if names.count(n) > 1]
            raise ValueError(f"중복된 job name: {dup}")
        return self


# ---------------------------------------------------------------------------
# 로딩
# ---------------------------------------------------------------------------

# YAML 파일 경로(BASE_DIR 기준 절대경로).
JOBS_YAML_PATH: Path = common_config.BASE_DIR / "config" / "jobs.yaml"


def _apply_daily_overrides(raw: dict) -> dict:
    """daily.yaml 의 값으로 jobs.yaml 의 raw dict 를 덮어쓴다(검증 전).

    덮어쓰는 규칙:
        - daily.run_until 이 비어 있지 않으면 raw.run_until 에 적용.
        - daily.server_times 가 비어 있지 않으면 jobs[name='server'].times 를
          통째로 daily 값으로 치환.

    Returns:
        in-place 수정된 raw dict (체이닝 편의용).
    """
    daily = load_daily()
    if daily is None:
        return raw

    if daily.run_until:
        raw["run_until"] = daily.run_until

    if daily.server_times:
        for j in raw.get("jobs", []) or []:
            if not isinstance(j, dict):
                continue
            if j.get("name") == "server" and j.get("mode") == "one_time_list":
                j["times"] = [
                    {"at": t.at, "args": list(t.args)}
                    for t in daily.server_times
                ]
    return raw


def load_runner_config(path: Optional[Path] = None) -> RunnerConfig:
    """``config/jobs.yaml`` 을 읽어 pydantic 으로 검증한 :class:`RunnerConfig` 반환.

    ``config/daily.yaml`` 이 존재하면 그 값을 ``run_until`` / ``server.times``
    에 우선 적용한 뒤 검증한다(매일 갱신하는 값들의 단일 소스).

    Args:
        path: 검증용으로 다른 YAML 경로를 지정할 때 사용. 기본은 ``JOBS_YAML_PATH``.

    Returns:
        검증을 통과한 :class:`RunnerConfig`.

    Raises:
        FileNotFoundError: jobs.yaml 파일이 없는 경우.
        ValueError: YAML 파싱 실패 또는 스키마 검증 실패 시 — 어느 job·어느
            필드가 잘못됐는지 메시지에 명시.
    """
    p = path or JOBS_YAML_PATH
    if not p.exists():
        raise FileNotFoundError(f"runner 설정 없음: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"jobs.yaml 파싱 실패: {p} | {e}") from e

    if not isinstance(raw, dict):
        raise ValueError(f"jobs.yaml 최상위는 dict 여야 합니다: {p}")

    # daily.yaml 가 있으면 그 값을 먼저 주입한다.
    raw = _apply_daily_overrides(raw)

    try:
        return RunnerConfig.model_validate(raw)
    except ValidationError as e:
        # pydantic v2 의 에러는 어느 필드인지 충분히 명확하지만, 사용자가 한눈에
        # 보도록 한 줄 요약을 앞에 붙인다.
        details = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise ValueError(
            f"jobs.yaml 스키마 검증 실패 ({p}):\n{details}"
        ) from e
