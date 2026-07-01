"""logs/ + state/ 정리 스크립트 — runner 실행 중이면 거부(안전장치).

기본 삭제 대상:
    - ``logs/`` 의 모든 로그 파일(``*.log`` + 로테이션 백업 ``*.log.N``).
    - ``state/runner.json`` , ``state/scheduler.json`` , ``state/stop.flag`` .

기본 제외(옵션 필요):
    - ``state/jennifer/`` 세션 파일 — 지우면 다음 실행 때 재로그인 비용. ``--sessions``
      를 줄 때만 삭제한다.

안전장치:
    ``state/runner.json`` 의 ``runner_pid`` (또는 ``running_pid`` 의 자식)가 살아
    있으면 **삭제를 거부**한다. 실행 중 로그/상태를 지우면 Windows 파일 잠금
    충돌 + 상태 꼬임이 생기기 때문이다. ``--force`` 로 무시할 수 있다(권장 안 함).

사용 예::

    python -m tools.clean              # 로그 + runner/scheduler 상태
    python -m tools.clean --sessions   # + jennifer 세션까지
    python -m tools.clean --logs-only  # 로그만
    python -m tools.clean --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from common import config as common_config
from . import enable_utf8_console
from runner.state import (
    RUNNER_STATE_PATH,
    SCHEDULER_STATE_PATH,
    STOP_FLAG_PATH,
    is_pid_alive,
    load_runner_state,
)


def _live_runner() -> List[str]:
    """실행 중으로 판정되는 근거 문자열 목록(비어 있으면 정지 상태)."""
    reasons: List[str] = []
    state = load_runner_state()

    pid = state.get("runner_pid")
    if pid and is_pid_alive(int(pid)):
        reasons.append(f"runner_pid={pid} 생존")

    for name, child in (state.get("running_pid") or {}).items():
        try:
            if is_pid_alive(int(child)):
                reasons.append(f"job '{name}' pid={child} 생존")
        except (TypeError, ValueError):
            continue

    return reasons


def _collect_targets(*, logs_only: bool, sessions: bool) -> List[Path]:
    """삭제 대상 파일 목록을 모은다(존재하는 것만)."""
    targets: List[Path] = []

    log_dir = common_config.LOG_DIR
    if log_dir.exists():
        # *.log 와 로테이션 백업(*.log.1 ...) 모두.
        targets.extend(sorted(p for p in log_dir.glob("*.log*") if p.is_file()))

    if not logs_only:
        for p in (RUNNER_STATE_PATH, SCHEDULER_STATE_PATH, STOP_FLAG_PATH):
            if p.exists():
                targets.append(p)
        if sessions:
            sess_dir = common_config.STATE_DIR / "jennifer"
            if sess_dir.exists():
                targets.extend(sorted(p for p in sess_dir.glob("*") if p.is_file()))

    return targets


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tools.clean",
        description="logs/ + state/ 정리(runner 실행 중이면 거부).",
    )
    p.add_argument("--logs-only", action="store_true", help="로그만 삭제(상태 보존).")
    p.add_argument("--sessions", action="store_true", help="jennifer 세션 파일도 삭제(재로그인 유발).")
    p.add_argument("--dry-run", action="store_true", help="삭제하지 않고 대상만 출력.")
    p.add_argument("--force", action="store_true", help="runner 실행 중 거부를 무시(권장 안 함).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    reasons = _live_runner()
    if reasons and not args.force:
        print("[clean] runner 가 실행 중으로 보입니다 — 삭제를 거부합니다:", file=sys.stderr)
        for r in reasons:
            print(f"   - {r}", file=sys.stderr)
        print("   먼저 'runnerctl stop' 으로 정지하세요(강행하려면 --force).", file=sys.stderr)
        return 1
    if reasons and args.force:
        print("[clean] 경고: runner 실행 중이지만 --force 로 강행합니다:")
        for r in reasons:
            print(f"   - {r}")

    targets = _collect_targets(logs_only=args.logs_only, sessions=args.sessions)
    if not targets:
        print("[clean] 삭제할 대상이 없습니다.")
        return 0

    print(f"[clean] 대상 {len(targets)}개:")
    for p in targets:
        print(f"   - {p.relative_to(common_config.BASE_DIR)}")

    if args.dry_run:
        print("[clean] (dry-run) 실제 삭제는 하지 않았습니다.")
        return 0

    deleted = 0
    skipped: List[str] = []
    for p in targets:
        try:
            p.unlink()
            deleted += 1
        except PermissionError:
            # 잠긴 파일(사용 중) — 건너뛰고 계속.
            skipped.append(f"{p.name} (사용 중/잠김)")
        except Exception as e:
            skipped.append(f"{p.name} ({e!r})")

    print(f"[clean] 삭제 {deleted}개 완료.")
    if skipped:
        print(f"[clean] 건너뜀 {len(skipped)}개:", file=sys.stderr)
        for s in skipped:
            print(f"   - {s}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
