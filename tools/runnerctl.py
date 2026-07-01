"""runner 백그라운드 제어 — start / status / logs / stop.

배경:
    runner 는 ``KeyboardInterrupt`` 에서만 자식(브라우저 등)을 정리한다. 백그라운드
    detached 로 띄우면 SIGINT 전달이 어렵고, ``taskkill /F`` 로 죽이면 정리 경로를
    건너뛰어 자식이 고아로 남는다. 그래서 ``stop`` 은 ``state/stop.flag`` 를 써서
    runner 가 *스스로* 기존 ``kill_all_running`` 경로로 우아하게 내려가게 한다.

명령:
    start          백그라운드로 runner 기동(이미 실행 중이면 거부).
    status         runner_pid 생존 / last_tick 신선도 / 실행 중 job 표시.
    logs [-f] [-n] runner.log 끝부분 출력(-f 면 추적).
    stop [--timeout N]  stop.flag 작성 → 종료 대기 → 무응답 시 taskkill 폴백.

사용 예::

    python -m tools.runnerctl start
    python -m tools.runnerctl status
    python -m tools.runnerctl logs -f
    python -m tools.runnerctl stop
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from common import config as common_config
from . import enable_utf8_console
from runner.state import (
    clear_stop_flag,
    is_pid_alive,
    load_runner_state,
    request_stop,
)

_RUNNER_LOG: Path = common_config.LOG_DIR / "runner.log"


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _alive_runner_pid() -> Optional[int]:
    """살아 있는 runner 본체 PID 또는 None."""
    pid = load_runner_state().get("runner_pid")
    if pid and is_pid_alive(int(pid)):
        return int(pid)
    return None


def _fmt_age(iso: Optional[str]) -> str:
    if not iso:
        return "없음"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    sec = (datetime.now() - dt).total_seconds()
    return f"{iso} ({sec:.0f}s 전)"


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------

def cmd_start(_args) -> int:
    pid = _alive_runner_pid()
    if pid:
        print(f"[runnerctl] 이미 실행 중입니다 (runner_pid={pid}).", file=sys.stderr)
        return 1

    # 이전 잔류 stop.flag 제거(있다면 새 runner 가 즉시 죽는 사고 방지).
    clear_stop_flag()

    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS: 부모 콘솔에서 분리. NEW_PROCESS_GROUP: 신호 격리.
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [sys.executable, "-m", "runner"],
        cwd=str(common_config.BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    print("[runnerctl] runner 기동 요청 — runner_pid 기록을 기다립니다...")

    # runner 가 runner.json 에 자기 pid 를 쓸 때까지 잠깐 폴링.
    for _ in range(20):  # 최대 ~10s
        time.sleep(0.5)
        pid = _alive_runner_pid()
        if pid:
            print(f"[runnerctl] 실행 중 (runner_pid={pid}). 로그: runnerctl logs -f")
            return 0

    print(
        "[runnerctl] 경고: 10초 내 runner_pid 가 기록되지 않았습니다. "
        "logs 로 기동 실패 여부를 확인하세요.",
        file=sys.stderr,
    )
    return 1


def cmd_status(_args) -> int:
    state = load_runner_state()
    pid = state.get("runner_pid")
    alive = bool(pid and is_pid_alive(int(pid)))

    print(f"[runnerctl] status @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   상태       : {'● 실행 중' if alive else '○ 정지'}")
    print(f"   runner_pid : {pid if pid else '없음'}{'' if alive else ' (생존 안 함)' if pid else ''}")
    print(f"   last_tick  : {_fmt_age(state.get('last_tick_at'))}")
    print(f"   heartbeat  : {_fmt_age(state.get('last_heartbeat_at'))}")

    running = state.get("running_pid") or {}
    if running:
        print("   실행 중 job:")
        for name, child in running.items():
            mark = "생존" if is_pid_alive(int(child)) else "좀비"
            print(f"      - {name} (pid={child}, {mark})")
    else:
        print("   실행 중 job: 없음")
    return 0


def cmd_logs(args) -> int:
    if not _RUNNER_LOG.exists():
        print(f"[runnerctl] 로그 파일 없음: {_RUNNER_LOG}", file=sys.stderr)
        return 1

    # 끝에서 n 줄 출력.
    with _RUNNER_LOG.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        tail = lines[-args.lines:] if args.lines > 0 else lines
        sys.stdout.write("".join(tail))
        if not args.follow:
            return 0

        # follow: 파일 끝에서부터 새 줄을 계속 출력(Ctrl+C 로 중단).
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.4)
        except KeyboardInterrupt:
            return 0


def cmd_stop(args) -> int:
    pid = _alive_runner_pid()
    if not pid:
        print("[runnerctl] 이미 정지 상태입니다.")
        clear_stop_flag()
        return 0

    print(f"[runnerctl] stop.flag 작성 — runner(pid={pid}) 우아한 종료 대기...")
    request_stop()

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if not is_pid_alive(pid):
            print("[runnerctl] 정상 종료되었습니다.")
            clear_stop_flag()
            return 0

    # 시간 초과 — 강제 종료 폴백. /T 로 자식 트리까지 함께 종료(고아 방지).
    print(
        f"[runnerctl] {args.timeout}s 내 종료되지 않음 — 강제 종료(taskkill /T /F).",
        file=sys.stderr,
    )
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass

    time.sleep(1.0)
    clear_stop_flag()
    if is_pid_alive(pid):
        print(f"[runnerctl] 경고: pid={pid} 가 여전히 살아 있습니다.", file=sys.stderr)
        return 1
    print("[runnerctl] 강제 종료 완료.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tools.runnerctl",
        description="runner 백그라운드 제어(start/status/logs/stop).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="백그라운드로 runner 기동.")
    sub.add_parser("status", help="실행 상태 표시.")

    p_logs = sub.add_parser("logs", help="runner.log 출력.")
    p_logs.add_argument("-n", "--lines", type=int, default=40, help="끝에서 N줄(기본 40).")
    p_logs.add_argument("-f", "--follow", action="store_true", help="새 로그를 계속 추적.")

    p_stop = sub.add_parser("stop", help="우아한 종료(무응답 시 강제).")
    p_stop.add_argument("--timeout", type=int, default=60, help="우아한 종료 대기 초(기본 60).")

    return p.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    handlers = {
        "start": cmd_start,
        "status": cmd_status,
        "logs": cmd_logs,
        "stop": cmd_stop,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
