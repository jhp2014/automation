"""스케줄 판정: interval / hourly_jitter / one_time_list 세 가지 모드.

원본 runner/scheduler.py 의 의미를 그대로 보존하되, ``JobConfig`` (pydantic)
와 ``scheduler.json`` dict 를 받는 시그니처로 다듬었다.

one_time_list 의 entry 표현은 원본의 ``folders`` → 본 모노레포의 ``args``
(임의 인자 리스트)로 일반화했다. state_key 도 args 기반으로 만든다.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from common.logging import get_logger

from .config import JobConfig, OneTimeEntry


_log = get_logger("runner.scheduler")


# ---------------------------------------------------------------------------
# interval
# ---------------------------------------------------------------------------

def should_run_interval(job: JobConfig, scheduler_state: Dict[str, Any]) -> bool:
    """마지막 실행으로부터 ``interval_sec`` 이상 지났는지.

    Args:
        job: interval 모드 JobConfig.
        scheduler_state: ``last_run`` dict 가 들어 있는 scheduler 상태.

    Returns:
        실행해야 하면 True. 실행 기록이 없거나 파싱 실패면 즉시 True.
    """
    assert job.mode == "interval" and job.interval_sec is not None
    last = scheduler_state.get("last_run", {}).get(job.name)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception as e:
        _log.warning("[%s] last_run 파싱 실패 -> 즉시 실행: %r", job.name, e)
        return True
    return (datetime.now() - last_dt).total_seconds() >= job.interval_sec


# ---------------------------------------------------------------------------
# hourly_jitter
# ---------------------------------------------------------------------------

def should_run_hourly_jitter(job: JobConfig, scheduler_state: Dict[str, Any]) -> bool:
    """매 시간마다 ``[0, jitter_max_sec]`` 범위 무작위 시점에 ``grace_sec`` 윈도우.

    이미 이번 시간(시간 단위)에 실행했다면 더 안 한다.

    Args:
        job: hourly_jitter 모드 JobConfig.
        scheduler_state: ``hourly_plan`` 과 ``last_run`` 을 읽고 쓰는 scheduler 상태.

    Returns:
        실행 윈도우 안이면 True.
    """
    assert job.mode == "hourly_jitter"
    assert job.jitter_max_sec is not None and job.grace_sec is not None

    now = datetime.now()
    hour_key = now.strftime("%Y-%m-%d %H")

    # 1) 이번 시간 계획이 없으면 만든다.
    hourly_plan: Dict[str, str] = scheduler_state.setdefault("hourly_plan", {})
    if hour_key not in hourly_plan:
        base = now.replace(minute=0, second=0, microsecond=0)
        delay = random.randint(0, job.jitter_max_sec)
        scheduled = base + timedelta(seconds=delay)
        hourly_plan[hour_key] = scheduled.isoformat()
        _log.info("[%s] hourly_plan 생성: %s", job.name, scheduled.strftime("%H:%M:%S"))

    try:
        scheduled_dt = datetime.fromisoformat(hourly_plan[hour_key])
    except Exception as e:
        _log.warning("[%s] hourly_plan 파싱 실패 -> 이번 시간 스킵: %r", job.name, e)
        return False

    # 2) 이번 시간(시간 단위)에 이미 실행했나?
    last = scheduler_state.get("last_run", {}).get(job.name)
    if last:
        try:
            if datetime.fromisoformat(last).strftime("%Y-%m-%d %H") == hour_key:
                return False
        except Exception:
            pass

    # 3) 계획 시각 ~ +grace 윈도우 안인가?
    return scheduled_dt <= now <= scheduled_dt + timedelta(seconds=job.grace_sec)


# ---------------------------------------------------------------------------
# one_time_list
# ---------------------------------------------------------------------------

def _make_state_key(job_name: str, entry: OneTimeEntry) -> str:
    """one_time entry 의 완료 여부 키. args 까지 포함해 동일 시각·다른 인자를 구분."""
    args_key = "||".join(entry.args)
    return f"{job_name}|{entry.at}|{args_key}"


def check_one_time_list(
    job: JobConfig,
    scheduler_state: Dict[str, Any],
) -> Optional[OneTimeEntry]:
    """지금 실행해야 할 1회성 entry 가 있으면 반환.

    윈도우 정책:
        - target(at) 이전이면 대기(스킵).
        - target ~ target+grace 안이면 실행 가능 → entry 반환.
        - target+grace 를 초과해 지난 건 done 처리하고 스킵(캐치업 방지).
        - 잘못된 형식의 entry 는 done 처리하고 스킵.

    Args:
        job: one_time_list 모드 JobConfig.
        scheduler_state: ``one_time_done`` 을 읽고 쓰는 scheduler 상태.

    Returns:
        실행할 entry 또는 None.
    """
    assert job.mode == "one_time_list" and job.times is not None and job.grace_sec is not None

    now = datetime.now()
    done: Dict[str, bool] = scheduler_state.setdefault("one_time_done", {})

    for entry in job.times:
        key = _make_state_key(job.name, entry)
        if done.get(key):
            continue

        try:
            target = datetime.strptime(entry.at, "%Y-%m-%d %H:%M")
        except Exception:
            # 사실 pydantic 단계에서 잡혔어야 하지만 방어적으로.
            done[key] = True
            continue

        if now > target + timedelta(seconds=job.grace_sec):
            # 너무 늦었음 — 캐치업 방지.
            done[key] = True
            continue

        if now < target:
            continue

        return entry

    return None


def mark_one_time_done(
    scheduler_state: Dict[str, Any],
    job_name: str,
    entry: OneTimeEntry,
) -> None:
    """one_time entry 를 완료 처리한다(실행 직후 호출)."""
    key = _make_state_key(job_name, entry)
    scheduler_state.setdefault("one_time_done", {})[key] = True
