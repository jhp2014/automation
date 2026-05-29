"""job 실행기: ``python -m <module> <args...>`` 로 subprocess 호출.

규약 v1.1 (인터프리터 통일 조항): 모든 job 은 runner 자신의 ``sys.executable``
로 호출한다. job 별 venv 탐색이나 pythonw 분기는 두지 않는다.

블로킹 실행: ``run_job`` 은 자식 종료까지 대기한다(원본 동작 보존). 동시
실행은 후속 과제.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from common import config as common_config
from common.logging import get_logger

from .config import JobConfig, OneTimeEntry


_log = get_logger("runner.executor")


# ---------------------------------------------------------------------------
# 안전 디코딩 (원본 safe_decode 계승)
# ---------------------------------------------------------------------------

def _safe_decode(b: Optional[bytes]) -> str:
    """Windows 콘솔이 cp949 인 경우가 많아 cp949 우선, 실패 시 utf-8 → replace."""
    if not b:
        return ""
    try:
        return b.decode("cp949")
    except UnicodeDecodeError:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def _build_cmd(job: JobConfig, schedule_entry: Optional[OneTimeEntry]) -> List[str]:
    """``[sys.executable, "-m", module, *공통args, *entry.args]`` 조립."""
    cmd: List[str] = [sys.executable, "-m", job.module]
    cmd.extend(job.args)
    if schedule_entry is not None:
        cmd.extend(schedule_entry.args)
    return cmd


def run_job(
    job: JobConfig,
    runner_state: Dict[str, Any],
    schedule_entry: Optional[OneTimeEntry] = None,
) -> int:
    """job 을 subprocess 로 실행하고 종료까지 대기한다.

    Args:
        job: 실행할 JobConfig.
        runner_state: ``running_pid`` 를 in-place 로 업데이트할 runner 상태.
        schedule_entry: one_time entry 인 경우 그 entry. 그 외에는 None.

    Returns:
        자식의 returncode. timeout 으로 강제 종료된 경우는 음수가 될 수 있다.
    """
    name = job.name
    timeout_sec = job.timeout_sec
    cmd = _build_cmd(job, schedule_entry)

    _log.info("[%s] START | cmd=%s", name, " ".join(cmd))

    # stderr 만 PIPE 로 받아 조기 크래시를 추적할 수 있게 한다.
    # stdout 은 DEVNULL(원본 동작).
    p = subprocess.Popen(
        cmd,
        cwd=str(common_config.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    running: Dict[str, int] = runner_state.setdefault("running_pid", {})
    running[name] = p.pid

    start_time = time.time()
    timed_out = False
    try:
        while True:
            if time.time() - start_time > timeout_sec:
                _log.warning("[%s] TIMEOUT(%ds) -> kill pid=%d", name, timeout_sec, p.pid)
                try:
                    p.kill()
                except Exception as e:
                    _log.warning("[%s] kill 실패(무시): %r", name, e)
                timed_out = True
                # kill 후 stderr 수거를 위해 communicate 까지 간다.
                break

            ret = p.poll()
            if ret is not None:
                break

            time.sleep(0.5)

        # 잔여 stderr 수거(블로킹 짧게).
        try:
            _stdout_b, stderr_b = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except Exception:
                pass
            _stdout_b, stderr_b = p.communicate()
        except Exception as e:
            _log.warning("[%s] communicate 실패(무시): %r", name, e)
            stderr_b = None

        rc = p.returncode if p.returncode is not None else -1
        _log.info("[%s] END | exit=%d%s", name, rc, " (timeout)" if timed_out else "")

        if rc != 0 and stderr_b:
            stderr_txt = _safe_decode(stderr_b).strip()
            if stderr_txt:
                _log.warning("[%s] STDERR:\n%s", name, stderr_txt[:4000])

        return rc

    finally:
        running.pop(name, None)


def kill_all_running(runner_state: Dict[str, Any]) -> None:
    """``running_pid`` 에 남아 있는 모든 자식을 강제 종료한다.

    runner 가 ``run_until`` 도달이나 KeyboardInterrupt 로 종료될 때 호출한다.
    """
    running: Dict[str, int] = runner_state.get("running_pid", {}) or {}
    if not running:
        return

    _log.info("[RUNLIMIT] running 전부 kill: %s", list(running.keys()))

    for name, pid in list(running.items()):
        try:
            pid_int = int(pid)
            # 9 == SIGKILL (Windows 에서도 os.kill 은 TerminateProcess 로 동작).
            os.kill(pid_int, 9)
            _log.info("[RUNLIMIT] killed [%s] pid=%d", name, pid_int)
        except Exception as e:
            _log.warning("[RUNLIMIT] kill 실패 [%s]: %r", name, e)
        finally:
            running.pop(name, None)
