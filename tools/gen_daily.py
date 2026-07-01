"""``config/daily.yaml`` 생성기 — daily.base.yaml + 실행 시각 → 자동 생성.

운영자는 매일 근무 시작 무렵 ``scripts/gen-daily.bat`` 한 번만 돌리면 된다. 실행
시각으로 근무(주간/야간)와 기준 시각을 자동 판정하고, base 템플릿에 날짜만 입혀
``config/daily.yaml`` 을 만든다.

운영기준일 D (06시 경계):
    - now.hour < 6 이면 아직 '전날 근무' 로 보고 D = 어제.
    - 그 외에는 D = 오늘.
    각 값의 날짜는 ``D + (base 의 day 오프셋)`` 으로 정해진다(야간 새벽 캡처는
    day:1 이라 자동으로 D+1).

근무 선택:
    - 인자 없이 실행 → 현재 시각을 launch_windows 와 매칭(08~10→09 …).
    - ``python -m tools.gen_daily 18`` → shift 직접 지정(윈도우 밖이거나 강제).

사용 예::

    python -m tools.gen_daily                 # 시각 자동 판정
    python -m tools.gen_daily 21              # 야간(21시) 강제
    python -m tools.gen_daily --operator hong # 자격증명 운영자 지정
    python -m tools.gen_daily --date 2026-07-02 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from . import enable_utf8_console
from .daily_base import DailyBase, load_daily_base

# common.config 를 import 하지 않고 직접 계산 — daily.yaml 출력 경로.
_BASE_DIR = Path(__file__).resolve().parent.parent
DAILY_YAML_PATH: Path = _BASE_DIR / "config" / "daily.yaml"

_WEEKDAYS_KR = "월화수목금토일"


# ---------------------------------------------------------------------------
# 날짜 / 근무 판정
# ---------------------------------------------------------------------------

def compute_base_date(now: datetime) -> date:
    """운영기준일 D 를 계산한다(다음날 06시 이전이면 전날)."""
    d = now.date()
    if now.hour < 6:
        d = d - timedelta(days=1)
    return d


def select_shift_key(
    base: DailyBase, now: datetime, explicit: Optional[str]
) -> str:
    """실행할 shift 키를 정한다.

    explicit 이 주어지면 그것을 검증해 반환. 없으면 현재 시각을 launch_windows
    와 매칭한다. 매칭 실패 시 RuntimeError(명시 입력 안내).
    """
    if explicit is not None:
        key = str(explicit)
        if key not in base.shifts:
            valid = ", ".join(sorted(base.shifts))
            raise RuntimeError(f"알 수 없는 shift '{key}' (가능: {valid})")
        return key

    hhmm = now.strftime("%H:%M")
    for w in base.launch_windows:
        if w.from_ <= hhmm <= w.to:
            return str(w.shift)

    windows = " / ".join(
        f"{w.from_}~{w.to}→{w.shift}" for w in base.launch_windows
    )
    valid = ", ".join(sorted(base.shifts))
    raise RuntimeError(
        f"현재 시각 {hhmm} 이 어떤 launch_windows 에도 속하지 않습니다 ({windows}).\n"
        f"shift 를 직접 지정하세요: python -m tools.gen_daily <{valid}>"
    )


# ---------------------------------------------------------------------------
# daily.yaml 조립
# ---------------------------------------------------------------------------

def build_daily(
    base: DailyBase, shift_key: str, d: date, operator_key: str
) -> Dict[str, Any]:
    """base + (shift, D, operator) → daily.yaml 딕셔너리."""
    shift = base.shifts[shift_key]
    op = base.operators[operator_key]

    wd = _WEEKDAYS_KR[d.weekday()]
    title = base.title_template.format(
        date=d.strftime("%Y.%m.%d"), wd=wd, shift=shift.label
    )

    ru_date = d + timedelta(days=shift.run_until.day)
    run_until = f"{ru_date.strftime('%Y-%m-%d')} {shift.run_until.time}"

    server_times = []
    for cap in shift.server_times:
        at_date = d + timedelta(days=cap.day)
        at = f"{at_date.strftime('%Y-%m-%d')} {cap.time}"
        args = []
        for folder in cap.folders:
            args.extend(["--folder", folder])
        server_times.append({"at": at, "args": args})

    return {
        "run_until": run_until,
        "kworks": {
            "user_id": op.user_id,
            "user_pw": op.user_pw,
            "target_title": title,
        },
        "server_times": server_times,
    }


def _dump_yaml(daily: Dict[str, Any], shift_key: str, operator_key: str) -> str:
    """헤더 주석 + YAML 본문 문자열."""
    header = (
        "# =============================================================================\n"
        "# config/daily.yaml — tools.gen_daily 자동 생성. 직접 수정해도 되지만 다음\n"
        "#   gen-daily 실행 시 덮어쓰여진다(직전 파일은 daily.yaml.bak 로 백업됨).\n"
        f"#   생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"shift={shift_key} | operator={operator_key}\n"
        "# =============================================================================\n"
    )
    body = yaml.safe_dump(daily, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return header + body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tools.gen_daily",
        description="daily.base.yaml + 실행 시각 → config/daily.yaml 생성.",
    )
    p.add_argument(
        "shift",
        nargs="?",
        default=None,
        help="shift 키(예: 09 18 21). 생략 시 현재 시각으로 자동 판정.",
    )
    p.add_argument(
        "--operator", "-o", default=None,
        help="자격증명 운영자 키. 생략 시 base 의 default_operator.",
    )
    p.add_argument(
        "--date", "-d", default=None,
        help="운영기준일 D 강제(YYYY-MM-DD). 생략 시 06시 경계 규칙으로 자동.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="파일을 쓰지 않고 생성 결과만 출력.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    enable_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        base = load_daily_base()
    except (FileNotFoundError, RuntimeError) as e:
        print(f"[gen-daily] base 로드 실패: {e}", file=sys.stderr)
        return 2

    now = datetime.now()

    try:
        shift_key = select_shift_key(base, now, args.shift)
    except RuntimeError as e:
        print(f"[gen-daily] {e}", file=sys.stderr)
        return 2

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"[gen-daily] --date 형식 오류(YYYY-MM-DD): {args.date}", file=sys.stderr)
            return 2
    else:
        d = compute_base_date(now)

    operator_key = args.operator or base.default_operator
    if operator_key not in base.operators:
        valid = ", ".join(sorted(base.operators))
        print(f"[gen-daily] 알 수 없는 operator '{operator_key}' (가능: {valid})", file=sys.stderr)
        return 2

    daily = build_daily(base, shift_key, d, operator_key)
    text = _dump_yaml(daily, shift_key, operator_key)

    shift = base.shifts[shift_key]
    print(
        f"[gen-daily] shift={shift_key}({shift.label}) operator={operator_key} "
        f"기준일 D={d.isoformat()}"
    )
    print(f"[gen-daily] target_title = {daily['kworks']['target_title']}")
    print(f"[gen-daily] run_until    = {daily['run_until']}")
    print(f"[gen-daily] server_times = {len(daily['server_times'])}건")

    if args.dry_run:
        print("---- (dry-run) " + "-" * 50)
        print(text)
        return 0

    # 기존 파일 백업 후 덮어쓰기.
    if DAILY_YAML_PATH.exists():
        backup = DAILY_YAML_PATH.with_suffix(".yaml.bak")
        backup.write_text(DAILY_YAML_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[gen-daily] 기존 파일 백업 -> {backup.name}")

    DAILY_YAML_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_YAML_PATH.write_text(text, encoding="utf-8")
    print(f"[gen-daily] 작성 완료 -> {DAILY_YAML_PATH}")

    # 생성물이 common.daily 스키마를 통과하는지 검증(폴백 흡수 전에 잡는다).
    try:
        from common.daily import load_daily
        result = load_daily(force=True)
        if result is None:
            print("[gen-daily] 경고: 생성물이 common.daily 검증을 통과하지 못했습니다(로그 확인).", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"[gen-daily] 검증 중 예외(무시 가능): {e!r}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
