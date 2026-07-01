"""runner 상태 파일 두 개를 관리한다 — 소유자별로 분리.

규약 v1.1 따름. ``common.config.STATE_DIR`` 하위에 둔다.

분리 이유:
    - ``runner.json`` 은 **runner 자체** 의 동작 상태(현재 살아있는 자식 PID,
      마지막 heartbeat 시각). 매 tick 빈번히 변경됨.
    - ``scheduler.json`` 은 **스케줄** 상태(마지막 실행 시각, hourly_plan, 1회
      실행 완료 기록). 의미가 더 안정적이라 별도 파일로 두면 디버깅 시 한쪽만
      보면 된다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from common import config as common_config
from common.logging import get_logger


_log = get_logger("runner.state")


# ---------------------------------------------------------------------------
# 파일 경로
# ---------------------------------------------------------------------------

RUNNER_STATE_PATH: Path = common_config.STATE_DIR / "runner.json"
SCHEDULER_STATE_PATH: Path = common_config.STATE_DIR / "scheduler.json"

# 백그라운드 종료 신호 파일. ``tools.runnerctl stop`` 이 작성하고, runner 본 루프가
# 매 tick 확인해 존재하면 기존 ``kill_all_running`` 경로로 우아하게 종료한다.
# 신호(SIGINT) 전달이 까다로운 Windows detached 실행에서도 안전하게 동작한다.
STOP_FLAG_PATH: Path = common_config.STATE_DIR / "stop.flag"


# ---------------------------------------------------------------------------
# 기본값
# ---------------------------------------------------------------------------

def _default_runner_state() -> Dict[str, Any]:
    return {
        "running_pid": {},        # {"job_name": pid_int}
        "last_heartbeat_at": None,  # ISO 문자열(local time) 또는 None
        "runner_pid": None,       # runner 본체 PID. 살아있으면 실행 중(정확).
        "last_tick_at": None,     # 매 tick 갱신(Supabase 와 무관). 신선도 보조 지표.
    }


def _default_scheduler_state() -> Dict[str, Any]:
    return {
        "last_run": {},        # {"job_name": ISO 문자열}
        "hourly_plan": {},     # {"YYYY-MM-DD HH": ISO 문자열}
        "one_time_done": {},   # {"state_key": True}
    }


# ---------------------------------------------------------------------------
# 로드 / 저장
# ---------------------------------------------------------------------------

def _safe_load_json(path: Path, default_factory) -> Dict[str, Any]:
    """JSON 로드. 누락/깨짐이면 기본값."""
    if not path.exists():
        return default_factory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _log.warning("상태 파일이 dict 가 아님 -> 기본값 사용: %s", path)
            return default_factory()
        return data
    except Exception as e:
        _log.warning("상태 파일 로드 실패 -> 기본값 사용: %s | %r", path, e)
        return default_factory()


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    """JSON 저장. 실패는 경고만(저장 실패가 본 루프를 죽이지 않게)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning("상태 파일 저장 실패: %s | %r", path, e)


def load_runner_state() -> Dict[str, Any]:
    """``runner.json`` 로드. 누락 키는 기본값으로 보강."""
    state = _safe_load_json(RUNNER_STATE_PATH, _default_runner_state)
    for k, v in _default_runner_state().items():
        state.setdefault(k, v)
    return state


def save_runner_state(state: Dict[str, Any]) -> None:
    """``runner.json`` 저장."""
    _save_json(RUNNER_STATE_PATH, state)


def load_scheduler_state() -> Dict[str, Any]:
    """``scheduler.json`` 로드. 누락 키는 기본값으로 보강."""
    state = _safe_load_json(SCHEDULER_STATE_PATH, _default_scheduler_state)
    for k, v in _default_scheduler_state().items():
        state.setdefault(k, v)
    return state


def save_scheduler_state(state: Dict[str, Any]) -> None:
    """``scheduler.json`` 저장."""
    _save_json(SCHEDULER_STATE_PATH, state)


# ---------------------------------------------------------------------------
# PID 헬퍼
# ---------------------------------------------------------------------------

def is_pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` 로 프로세스 생존 확인.

    Windows / Unix 모두에서 동작. pid<=0 은 즉시 False.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_dead_pids(runner_state: Dict[str, Any]) -> List[str]:
    """``runner_state["running_pid"]`` 에서 죽은 PID 항목을 제거한다.

    제거된 job 이름 목록을 반환한다. 호출부에서 변경이 있으면 save 하면 된다.

    Args:
        runner_state: ``load_runner_state()`` 가 돌려준 dict (in-place 변경).

    Returns:
        제거된 job 이름 리스트.
    """
    running: Dict[str, int] = runner_state.get("running_pid", {})
    dead = [name for name, pid in running.items() if not is_pid_alive(int(pid))]
    for name in dead:
        running.pop(name, None)
    return dead


# ---------------------------------------------------------------------------
# 편의 헬퍼
# ---------------------------------------------------------------------------

def update_last_run(scheduler_state: Dict[str, Any], job_name: str) -> None:
    """job 의 마지막 실행 시각을 지금으로 갱신한다."""
    scheduler_state.setdefault("last_run", {})[job_name] = datetime.now().isoformat()


# ---------------------------------------------------------------------------
# 종료 신호(stop.flag) 헬퍼
# ---------------------------------------------------------------------------

def request_stop() -> None:
    """``stop.flag`` 를 작성한다(외부에서 runner 우아한 종료를 요청).

    runner 본 루프가 매 tick :func:`stop_requested` 로 확인한다.
    """
    STOP_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    STOP_FLAG_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")


def stop_requested() -> bool:
    """``stop.flag`` 존재 여부."""
    return STOP_FLAG_PATH.exists()


def clear_stop_flag() -> None:
    """``stop.flag`` 를 제거한다(없으면 무시).

    runner 시작 직후(이전 실행의 잔류 플래그 제거)와 종료 처리 직후 호출한다.
    """
    try:
        STOP_FLAG_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning("stop.flag 제거 실패(무시): %r", e)
