"""runner 진입점. ``python -m runner`` 로 기동.

규약 v1.1 따름. 본 모듈은 메인 루프만 담당하고 부품 책임은 다음에 위임한다:
    - runner.config     : jobs.yaml 로딩 + 스키마 검증, Supabase 자격증명
    - runner.state      : runner.json + scheduler.json 두 상태 파일 관리
    - runner.scheduler  : 3개 모드의 스케줄 판정
    - runner.executor   : sys.executable -m <module> subprocess 실행
    - runner.heartbeat  : 데드맨 스위치 (Supabase upsert + 연속 실패 경고)
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path에 추가한다.
# python -m runner 로 실행하든, 이 파일을 직접 실행하든
# common / runner / site_selectors 패키지를 항상 import할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import time
from datetime import datetime
from typing import Optional

from common import config as common_config
from common.logging import get_logger

from .config import RunnerConfig, SUPABASE_KEY, SUPABASE_URL, load_runner_config
from .executor import kill_all_running, run_job
from .heartbeat import HeartbeatSender
from .scheduler import (
    check_one_time_list,
    mark_one_time_done,
    should_run_hourly_jitter,
    should_run_interval,
)
from .state import (
    cleanup_dead_pids,
    load_runner_state,
    load_scheduler_state,
    save_runner_state,
    save_scheduler_state,
    update_last_run,
)


LOG = get_logger("runner", "runner.log")


def _compute_stop_time(run_until: Optional[str]) -> Optional[datetime]:
    """``run_until`` 문자열을 datetime 으로(빈/None 이면 무기한)."""
    if not run_until:
        return None
    return datetime.strptime(run_until, "%Y-%m-%d %H:%M")


def _try_send_heartbeat(
    hb: HeartbeatSender,
    runner_state: dict,
) -> None:
    """heartbeat 주기 도달 시 송신하고 runner.json 의 last_heartbeat_at 갱신.

    송신 실패는 :class:`HeartbeatSender` 내부에서 흡수하고 임계 실패 시
    Pushover 까지 처리하므로 본 함수는 발생 예외만 마지막에 한 번 더 흡수한다.
    """
    try:
        if hb.should_send(runner_state.get("last_heartbeat_at")):
            new_ts = hb.send()
            if new_ts:
                runner_state["last_heartbeat_at"] = new_ts
                save_runner_state(runner_state)
    except Exception as e:
        # 모든 예외는 본 루프 보호를 위해 흡수한다(규약: heartbeat 실패가 runner
        # 를 죽이면 안 된다). 카운터 기반 Pushover 경고는 HeartbeatSender 가 담당.
        LOG.warning("heartbeat 시도 중 예외(흡수): %r", e)


def main() -> int:
    """runner 메인 루프."""
    common_config.ensure_dirs()
    LOG.info("==== [RUNNER START] ====")

    # 1) 설정 로딩 (실패하면 즉시 종료).
    try:
        cfg: RunnerConfig = load_runner_config()
    except Exception as e:
        LOG.exception("jobs.yaml 로딩 실패: %r", e)
        return 2
    LOG.info(
        "설정 로드 OK: jobs=%d tick=%ds heartbeat=%ds source=%s run_until=%s",
        len(cfg.jobs),
        cfg.tick_sec,
        cfg.heartbeat_interval_sec,
        cfg.heartbeat_source,
        cfg.run_until or "(무기한)",
    )

    # 2) heartbeat 클라이언트 (Supabase 키 누락도 즉시 실패: 데드맨 스위치 약화 금지).
    try:
        hb = HeartbeatSender(
            url=SUPABASE_URL,
            key=SUPABASE_KEY,
            source=cfg.heartbeat_source,
            interval_sec=cfg.heartbeat_interval_sec,
        )
    except Exception as e:
        LOG.exception("Supabase heartbeat 초기화 실패(데드맨 스위치 필수): %r", e)
        return 3

    # 3) 상태 로드.
    runner_state = load_runner_state()
    scheduler_state = load_scheduler_state()

    stop_time = _compute_stop_time(cfg.run_until)
    if stop_time:
        LOG.info("[RUNLIMIT] 종료 예정: %s", stop_time)

    # 시작 직후 1회 heartbeat 시도.
    _try_send_heartbeat(hb, runner_state)

    try:
        while True:
            # 1) 자동 종료.
            if stop_time and datetime.now() >= stop_time:
                LOG.info("[RUNLIMIT] 종료 시각 도달 -> 자식 정리 후 종료")
                kill_all_running(runner_state)
                save_runner_state(runner_state)
                break

            # 2) 죽은 PID 정리.
            dead = cleanup_dead_pids(runner_state)
            if dead:
                LOG.info("죽은 PID 정리: %s", dead)
                save_runner_state(runner_state)

            # 3) heartbeat (주기 도달 시만).
            _try_send_heartbeat(hb, runner_state)

            # 4) 각 job 판정 + 실행.
            for job in cfg.jobs:
                # 이미 실행 중이면 스킵.
                if job.name in runner_state.get("running_pid", {}):
                    continue

                run_now = False
                schedule_entry = None

                if job.mode == "interval":
                    run_now = should_run_interval(job, scheduler_state)
                elif job.mode == "hourly_jitter":
                    run_now = should_run_hourly_jitter(job, scheduler_state)
                    # hourly_plan 이 갱신됐을 수 있으니 저장.
                    save_scheduler_state(scheduler_state)
                elif job.mode == "one_time_list":
                    schedule_entry = check_one_time_list(job, scheduler_state)
                    run_now = schedule_entry is not None
                    # 만료 정리(done 처리)된 entry 가 있으면 저장.
                    save_scheduler_state(scheduler_state)

                if not run_now:
                    continue

                # 실제 실행 — 블로킹.
                # 실행 직전에 runner_state 저장 안 해도 무방(running_pid 는
                # run_job 안에서 in-place 갱신·종료 시 제거).
                run_job(job, runner_state, schedule_entry=schedule_entry)

                # last_run 갱신 + one_time done 처리.
                update_last_run(scheduler_state, job.name)
                if job.mode == "one_time_list" and schedule_entry is not None:
                    mark_one_time_done(scheduler_state, job.name, schedule_entry)
                save_scheduler_state(scheduler_state)

            # 5) tick 대기.
            time.sleep(cfg.tick_sec)

    except KeyboardInterrupt:
        LOG.info("[USER] KeyboardInterrupt -> 자식 정리")
        kill_all_running(runner_state)
        save_runner_state(runner_state)
    finally:
        LOG.info("==== [RUNNER END] ====")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
