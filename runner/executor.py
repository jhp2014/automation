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
import threading
import time
from typing import IO, Any, Dict, List, Optional

from common import config as common_config
from common.logging import get_logger
from common.notify import send_pushover_emergency

from .config import JobConfig, OneTimeEntry


_log = get_logger("runner.executor", "runner.log")


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

# 비정상 종료 요약에 남길 stderr tail 최대 줄 수(메모리 상한).
_STDERR_TAIL_MAX = 200


def _drain_stderr(stream: IO[bytes], name: str, sink: List[str]) -> None:
    """자식 프로세스의 stderr 를 한 줄씩 읽어 runner.log 에 실시간 기록한다.

    파이썬 자식의 콘솔 로그(logging.StreamHandler 는 stderr 로 출력)와 Playwright
    Node 드라이버의 출력(EPIPE 등 포함)이 모두 이 스트림으로 들어온다. 자식이
    중간에 hang/크래시해도 직전 출력을 잃지 않도록 즉시 기록하고, 동시에 PIPE
    버퍼가 가득 차 자식이 write 에서 블로킹되는 교착을 막는다.

    Args:
        stream: 자식의 stderr (바이트 스트림).
        name: 로그 접두에 쓸 job 이름.
        sink: 비정상 종료 요약용 tail 버퍼. 최근 ``_STDERR_TAIL_MAX`` 줄만 유지.
    """
    try:
        for raw in iter(stream.readline, b""):
            line = _safe_decode(raw).rstrip("\r\n")
            if not line:
                continue
            sink.append(line)
            if len(sink) > _STDERR_TAIL_MAX:
                del sink[:-_STDERR_TAIL_MAX]
            _log.info("[%s] » %s", name, line)
    except Exception as e:
        _log.warning("[%s] stderr 드레인 예외(무시): %r", name, e)
    finally:
        try:
            stream.close()
        except Exception:
            pass


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

    # 자식 stderr 를 실시간으로 runner.log 에 흘린다. hang/크래시(예: Playwright
    # Node 드라이버 EPIPE)에도 직전 출력을 잃지 않고, PIPE 버퍼 포화 교착을 막는다.
    stderr_tail: List[str] = []
    drainer: Optional[threading.Thread] = None
    if p.stderr is not None:
        drainer = threading.Thread(
            target=_drain_stderr,
            args=(p.stderr, name, stderr_tail),
            daemon=True,
        )
        drainer.start()

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
                break

            ret = p.poll()
            if ret is not None:
                break

            time.sleep(0.5)

        # 종료 확정 + 드레이너가 EOF 까지 읽도록 join.
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _log.warning("[%s] wait timeout -> kill 재시도", name)
            try:
                p.kill()
            except Exception as e:
                _log.warning("[%s] kill 재시도 실패(무시): %r", name, e)
            try:
                p.wait(timeout=5)
            except Exception as e:
                _log.warning("[%s] kill 후 wait 실패(무시): %r", name, e)
        except Exception as e:
            _log.warning("[%s] wait 실패(무시): %r", name, e)
        if drainer is not None:
            drainer.join(timeout=5)

        rc = p.returncode if p.returncode is not None else -1
        elapsed = time.time() - start_time
        _log.info(
            "[%s] END | exit=%d%s elapsed=%.1fs stderr_lines=%d",
            name,
            rc,
            " (timeout)" if timed_out else "",
            elapsed,
            len(stderr_tail),
        )

        if rc != 0:
            tail = "\n".join(stderr_tail[-40:]).strip()
            if tail:
                _log.warning("[%s] STDERR(tail):\n%s", name, tail[:4000])
            else:
                _log.warning(
                    "[%s] 비정상 종료(exit=%d)인데 stderr 출력이 없습니다.", name, rc
                )

        # 러너 차원의 안전망 알림.
        #   - rc == 0 : 정상 → 무시.
        #   - rc == 1 : job 이 Fail-Fast 규약에 따라 자체 except 에서 이미 Pushover
        #     를 보냈다 → 중복 발송 방지를 위해 러너는 침묵.
        #   - timed_out(watchdog kill) 또는 rc 가 0/1 이 아닌 하드 크래시(OOM 으로
        #     인한 강제 종료 등) : job 이 스스로 보고하지 못한 죽음이므로 여기서
        #     알린다. asyncio 파이프 단의 MemoryError 처럼 job 의 except 를 우회해
        #     무음 hang 으로 빠지던 구간이 정확히 여기로 잡힌다.
        if timed_out or rc not in (0, 1):
            reason = "watchdog timeout(hang 의심)" if timed_out else f"비정상 종료(exit={rc})"
            tail = "\n".join(stderr_tail[-20:]).strip()
            send_pushover_emergency(
                title="Runner: job 비정상 종료",
                message=(
                    f"job={name} | {reason} | elapsed={elapsed:.0f}s\n"
                    + (tail[-800:] if tail else "(stderr 출력 없음)")
                ),
            )

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
