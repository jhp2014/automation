"""ETL 관제 오류테이블 치명 행 감시 job (규약 v1.1).

동작:
    1. ``get_monitor.jsp`` 를 1회 GET (브라우저·로그인 없음 — 관제 화면
       ``monitor.jsp`` 가 표를 채울 때 부르는 데이터 주소다).
    2. 중요도(``IMPORTANCE_DESC``)가 ``치명`` 인 행만 고른다.
    3. 보고 완료 목록(``state/etl_reported.json``)에 없는 새 행만 Pushover 로 알린다.

알림 정책(Zenius 방식):
    - **새 치명 행마다 1회.** ETL 은 실행마다 새 행이 쌓이는 시스템이라 행 자체를
      이벤트로 본다. 행에 고유 id 가 없으므로 ``시작일시|워크플로우|세션`` 을 키로 쓴다.
    - 새 행이 :data:`STORM_CAP` 건 이상이면 개별 알림 대신 요약 1건만 보내고
      전부 보고 완료로 흡수한다.
    - ``--baseline`` 은 지금 떠 있는 치명 행을 알림 없이 보고 완료로 흡수한다
      (첫 배포 / 대량 발생 후 정리용).
    - 보고 완료 목록은 시작일시 기준 :data:`REPORTED_KEEP_DAYS` 일이 지나면 지운다.
      "지금 응답에 없는 키 삭제" 방식은 응답이 순간 비었을 때 목록이 날아가 재알림이
      쏟아질 수 있어 쓰지 않는다.
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path 에 추가한다.
# python -m jobs.etl 로 실행하든 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import 할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests

from common import config
from common.logging import get_logger
from common.notify import send_pushover_emergency
from site_selectors import etl as E


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 알림 대상 중요도.
ALERT_IMPORTANCE = "치명"

# 새 치명 행이 이 수 이상이면 폭풍으로 보고 요약 1건만 보낸다(Zenius 와 동일).
STORM_CAP = 4

# 보고 완료 목록 보존 기간(시작일시 기준).
REPORTED_KEEP_DAYS = 7

# HTTP 요청 타임아웃(초). runner 의 job timeout_sec 보다 충분히 작아야 한다.
HTTP_TIMEOUT_SEC = 15

# 알림 본문에서 에러 메시지는 앞 몇 줄만 싣는다(SQL 전문이 길다).
ERROR_MSG_LINES = 2
ERROR_MSG_MAX_CHARS = 200

# Pushover 본문 한도(1024자)보다 조금 여유 있게 자른다.
PUSHOVER_MAX_CHARS = 1000

# 보고 완료 목록. {키: 시작일시}.
STATE_REPORTED = config.STATE_DIR / "etl_reported.json"

LOG = get_logger("jobs.etl", "etl.log")


# ---------------------------------------------------------------------------
# 보고 완료 목록
# ---------------------------------------------------------------------------

def load_reported() -> Dict[str, str]:
    """보고 완료 목록을 로드한다.

    Returns:
        ``{키: 시작일시}``. 파일이 없거나 깨졌으면 빈 dict — 그 경우 현재 치명
        행이 다시 알려질 수는 있어도 놓치지는 않는다.
    """
    if not STATE_REPORTED.exists():
        return {}
    try:
        data = json.loads(STATE_REPORTED.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 손상 파일은 빈 목록으로 진행
        LOG.warning("보고 완료 목록 로드 실패(빈 목록으로 진행): %r", e)
        return {}
    if not isinstance(data, dict):
        LOG.warning("보고 완료 목록 형식이 dict 가 아님(빈 목록으로 진행)")
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_reported(reported: Dict[str, str]) -> None:
    """보고 완료 목록을 저장한다."""
    STATE_REPORTED.parent.mkdir(parents=True, exist_ok=True)
    STATE_REPORTED.write_text(
        json.dumps(reported, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def prune_reported(reported: Dict[str, str], now: datetime) -> Dict[str, str]:
    """시작일시가 보존 기간을 넘긴 항목을 뺀다. 시각을 못 읽는 항목은 남긴다."""
    cutoff = now - timedelta(days=REPORTED_KEEP_DAYS)
    kept: Dict[str, str] = {}
    for key, start in reported.items():
        try:
            if datetime.strptime(start, E.TIME_FORMAT) < cutoff:
                continue
        except ValueError:
            pass
        kept[key] = start
    return kept


# ---------------------------------------------------------------------------
# 수집 / 판정
# ---------------------------------------------------------------------------

def _decode(resp: requests.Response) -> str:
    """응답 본문을 디코딩한다(charset 미표기면 후보 순서대로 시도)."""
    if "charset=" in resp.headers.get("Content-Type", "").lower():
        return resp.text
    for enc in E.ENCODING_CANDIDATES:
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    LOG.warning("본문 디코딩 후보 모두 실패 -> %s(replace)", E.ENCODING_CANDIDATES[0])
    return resp.content.decode(E.ENCODING_CANDIDATES[0], errors="replace")


def fetch_rows() -> List[Dict[str, Any]]:
    """오류테이블 데이터를 1회 GET 해 행 목록으로 돌려준다.

    Raises:
        requests.RequestException: 연결 실패/타임아웃/4xx·5xx.
        RuntimeError: 응답이 JSON 배열이 아닌 경우(관제 화면 변경 의심).
    """
    resp = requests.get(E.DATA_URL, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    text = _decode(resp)
    try:
        data = json.loads(text)
    except ValueError as e:
        raise RuntimeError(f"응답이 JSON 이 아님: {text[:200]!r}") from e
    if not isinstance(data, list):
        raise RuntimeError(f"응답이 JSON 배열이 아님: {type(data).__name__}")
    return [r for r in data if isinstance(r, dict)]


def row_key(row: Dict[str, Any]) -> str:
    """행 식별 키. ``시작일시|워크플로우|세션``."""
    return "|".join(
        str(row.get(f, "")).strip() for f in (E.F_START, E.F_WORKFLOW, E.F_SESSION)
    )


def critical_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """중요도가 치명인 행만 고른다."""
    return [r for r in rows if str(r.get(E.F_IMPORTANCE, "")).strip() == ALERT_IMPORTANCE]


# ---------------------------------------------------------------------------
# 메시지
# ---------------------------------------------------------------------------

def _field(row: Dict[str, Any], name: str) -> str:
    return str(row.get(name, "") or "").strip()


def _short_error(msg: str) -> str:
    """에러 메시지 앞 몇 줄만 남긴다(빈 줄 제외)."""
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    short = " / ".join(lines[:ERROR_MSG_LINES])
    if len(short) > ERROR_MSG_MAX_CHARS:
        short = short[:ERROR_MSG_MAX_CHARS] + "…"
    return short or "-"


def _clip(message: str) -> str:
    if len(message) <= PUSHOVER_MAX_CHARS:
        return message
    return message[: PUSHOVER_MAX_CHARS - 1] + "…"


def build_row_message(row: Dict[str, Any], ts: str) -> str:
    """치명 행 1건 알림 본문."""
    comments = _field(row, E.F_COMMENTS).splitlines()
    lines = [
        f"워크플로우: {_field(row, E.F_WORKFLOW)}",
        f"세션: {_field(row, E.F_SESSION)}",
        f"폴더: {_field(row, E.F_FOLDER)}",
        f"시작~종료: {_field(row, E.F_START)} ~ {_field(row, E.F_END)}"
        f" ({_field(row, E.F_PROG_TIME)})",
        f"상태: {_field(row, E.F_STATE)}"
        f" | READ 실패 {_field(row, E.F_READ_FAIL)}/{_field(row, E.F_READ_CNT)}"
        f" | WRITE 실패 {_field(row, E.F_WRITE_FAIL)}/{_field(row, E.F_WRITE_CNT)}",
        f"담당자: {_field(row, E.F_CHARGE) or '-'}",
    ]
    if comments and comments[0].strip():
        lines.append(f"설명: {comments[0].strip()}")
    lines.append(f"메시지: {_short_error(_field(row, E.F_ERROR_MSG))}")
    lines.append("야간에는 ETL 담당자에게 유선 연락(관제 화면 상단 연락처)")
    lines.append(f"Time: {ts}")
    return _clip("\n".join(lines))


def build_storm_summary(new_rows: List[Dict[str, Any]], ts: str) -> str:
    """새 치명 행이 많을 때 보내는 요약 1건 본문."""
    lines = [f"새 치명 {len(new_rows)}건 — 개별 알림 생략, 관제 화면 확인 필요"]
    for r in new_rows[:10]:
        lines.append(
            f"- {_field(r, E.F_START)} {_field(r, E.F_WORKFLOW)} / {_field(r, E.F_SESSION)}"
        )
    if len(new_rows) > 10:
        lines.append(f"... 외 {len(new_rows) - 10}건")
    lines.append(f"화면: {E.MONITOR_URL}")
    lines.append(f"Time: {ts}")
    return _clip("\n".join(lines))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETL 관제 오류테이블 치명 행 감시")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="판정 결과만 출력하고 알림 전송/상태 저장을 하지 않는다.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="현재 치명 행 전부를 알림 없이 보고 완료로 흡수한다.",
    )
    return parser.parse_args()


def main() -> int:
    """ETL 감시 진입점."""
    args = _parse_args()
    config.ensure_dirs()

    stage = "init"
    now = datetime.now()
    start_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    LOG.info(
        "[START] etl run at %s dry_run=%s baseline=%s",
        start_ts, args.dry_run, args.baseline,
    )

    try:
        stage = "fetch"
        LOG.info("[STAGE] %s", stage)
        rows = fetch_rows()
        crit = critical_rows(rows)
        LOG.info("행 %d개 수신, 치명 %d개", len(rows), len(crit))

        stage = "decide"
        reported = prune_reported(load_reported(), now)
        # 보고 완료 목록에 없는 행만. 같은 응답 안의 중복 행은 한 번만.
        new_rows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for r in crit:
            key = row_key(r)
            if key in reported or key in seen:
                continue
            seen.add(key)
            new_rows.append(r)

        if args.baseline:
            LOG.info("[BASELINE] 현재 치명 %d건 흡수(신규 %d건)", len(crit), len(new_rows))
        elif not new_rows:
            LOG.info("알림 대상 없음 (새 치명 행 없음)")
        else:
            stage = "notify"
            for r in new_rows:
                LOG.info("[ALERT] 새 치명: %s", row_key(r))
            if len(new_rows) >= STORM_CAP:
                messages = [(
                    f"[ETL] 치명 {len(new_rows)}건 (요약)",
                    build_storm_summary(new_rows, start_ts),
                )]
            else:
                messages = [
                    (f"[ETL] 치명 — {_field(r, E.F_WORKFLOW)}", build_row_message(r, start_ts))
                    for r in new_rows
                ]
            for title, message in messages:
                LOG.info("%s\n%s", title, message)
                if args.dry_run:
                    LOG.info("dry-run: 알림 전송 생략")
                else:
                    send_pushover_emergency(title=title, message=message)

        stage = "save_state"
        for r in (crit if args.baseline else new_rows):
            reported[row_key(r)] = _field(r, E.F_START)
        if args.dry_run:
            LOG.info("dry-run: 상태 저장 생략")
        else:
            save_reported(reported)

        LOG.info("[OK] 실행 완료")
        return 0

    except Exception as e:
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)
        send_pushover_emergency(
            title="[ETL] 모니터링 실패",
            message=f"Stage: {stage}\nError: {e}\nTime: {start_ts}",
        )
        return 1

    finally:
        LOG.info("[END] etl run finished")


if __name__ == "__main__":
    raise SystemExit(main())
