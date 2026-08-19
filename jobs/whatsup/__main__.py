"""WhatsUp Gold(NMS) 맵별 Items Down 감시 job (규약 v1.1).

동작:
    1. ``http://nms.kyowon.co.kr/`` 를 HTTP 인증으로 1회 GET (브라우저 미사용 —
       JS 없는 서버렌더 페이지라 requests 로 충분하다).
    2. 맵 표를 파싱해 맵별 ``Items Down`` 을 읽는다.
    3. 직전 실행 스냅샷(``state/whatsup_down.json``)과 비교해 **신규 발생** 또는
       **수 증가** 인 맵만 Pushover 로 알린다.

알림 정책(합의):
    - 복구 알림은 보내지 않는다. 복구되면 스냅샷에서 조용히 사라진다.
    - 따라서 복구 후 같은 맵이 다시 down 되면 (직전 스냅샷 기준 0 -> n 이므로)
      **다시 알림이 간다.**
    - down 이 같은 수로 계속 유지되는 동안은 무음이다.

비밀값(로그인 id/pw)은 ``.env`` 에서 읽는다
(``WHATSUP_USER_ID`` / ``WHATSUP_USER_PW``).
"""

from __future__ import annotations

# 프로젝트 루트를 sys.path 에 추가한다.
# python -m jobs.whatsup 로 실행하든 이 파일을 직접 실행하든
# common / site_selectors 패키지를 항상 import 할 수 있게 하기 위함이다.
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # automation/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json
import os
from datetime import datetime
from typing import Dict, List, Tuple

import requests
from requests.auth import HTTPBasicAuth

from common import config
from common.logging import get_logger
from common.notify import send_pushover_emergency
from jobs.whatsup.parser import MapStatus, parse_map_rows
from site_selectors import whatsup as W


# ---------------------------------------------------------------------------
# 설정 (비밀 아님)
# ---------------------------------------------------------------------------

# 직전 실행에서 down > 0 이었던 맵들의 스냅샷 {맵이름: down 수}.
STATE_DOWN = config.STATE_DIR / "whatsup_down.json"

# HTTP 요청 타임아웃(초). runner 의 job timeout_sec 보다 충분히 작아야 한다.
HTTP_TIMEOUT_SEC = 15

LOG = get_logger("jobs.whatsup", "whatsup.log")


# ---------------------------------------------------------------------------
# 상태 스냅샷
# ---------------------------------------------------------------------------

def load_prev_down() -> Dict[str, int]:
    """직전 실행의 down 스냅샷을 로드한다.

    Returns:
        ``{맵이름: down 수}``. 파일이 없거나 깨졌으면 빈 dict.
        (깨진 경우 '전부 신규'로 취급되어 알림이 한 번 더 갈 수는 있어도
        장애를 놓치지는 않는 방향으로 폴백한다.)
    """
    if not STATE_DOWN.exists():
        return {}
    try:
        data = json.loads(STATE_DOWN.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except Exception as e:  # noqa: BLE001 - 손상 파일은 빈 스냅샷으로 진행
        LOG.warning("down 스냅샷 로드 실패(빈 값으로 진행): %r", e)
    return {}


def save_prev_down(snapshot: Dict[str, int]) -> None:
    """현재 down 스냅샷을 저장한다.

    Args:
        snapshot: ``{맵이름: down 수}`` (down 이 0 인 맵은 넣지 않는다).
    """
    STATE_DOWN.parent.mkdir(parents=True, exist_ok=True)
    STATE_DOWN.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------

def _decode(resp: requests.Response) -> str:
    """응답 본문을 디코딩한다.

    Content-Type 에 charset 이 명시돼 있으면 그대로 신뢰하고, 없으면
    :data:`site_selectors.whatsup.ENCODING_CANDIDATES` 를 순서대로 시도한다.
    (charset 미표기 응답을 requests 기본 추정에 맡기면 한글 맵 이름이 깨진다.)

    Args:
        resp: requests 응답 객체.

    Returns:
        디코딩된 HTML 문자열. 모든 후보가 실패하면 첫 후보로 ``errors="replace"``.
    """
    if "charset=" in resp.headers.get("Content-Type", "").lower():
        return resp.text
    for enc in W.ENCODING_CANDIDATES:
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    LOG.warning("본문 디코딩 후보 모두 실패 -> %s(replace)", W.ENCODING_CANDIDATES[0])
    return resp.content.decode(W.ENCODING_CANDIDATES[0], errors="replace")


def fetch_main_html(user_id: str, user_pw: str) -> str:
    """메인 페이지를 HTTP 인증으로 1회 GET 한다.

    Args:
        user_id: WhatsUp 로그인 id.
        user_pw: WhatsUp 로그인 비밀번호.

    Returns:
        디코딩된 HTML 전문.

    Raises:
        RuntimeError: 401(인증 실패) 인 경우. 서버가 요구한 인증 방식을
            메시지에 담는다 — Basic 이 아니면 여기서 바로 드러난다.
        requests.HTTPError: 그 밖의 4xx/5xx.
        requests.RequestException: 연결 실패/타임아웃 등.
    """
    resp = requests.get(
        W.BASE_URL,
        auth=HTTPBasicAuth(user_id, user_pw),
        timeout=HTTP_TIMEOUT_SEC,
    )
    if resp.status_code == 401:
        scheme = resp.headers.get("WWW-Authenticate", "(헤더 없음)")
        raise RuntimeError(
            f"인증 실패(401). 서버 요구 방식: {scheme}."
            " Basic 이 아니면 인증 방식을 그에 맞게 바꿔야 합니다."
        )
    resp.raise_for_status()
    return _decode(resp)


# ---------------------------------------------------------------------------
# 판정 / 메시지
# ---------------------------------------------------------------------------

def decide_alerts(
    prev: Dict[str, int],
    current: Dict[str, int],
) -> List[Tuple[str, int, int]]:
    """알릴 대상을 고른다 (신규 발생 또는 수 증가).

    Args:
        prev: 직전 스냅샷 ``{맵이름: down 수}``.
        current: 이번 실행의 ``{맵이름: down 수}`` (down > 0 인 맵만).

    Returns:
        ``(맵이름, 현재 down, 직전 down)`` 리스트. down 수 내림차순.
        직전과 같거나 줄어든 맵은 포함하지 않는다.
    """
    alerts = [
        (name, now, prev.get(name, 0))
        for name, now in current.items()
        if now > prev.get(name, 0)
    ]
    alerts.sort(key=lambda t: t[1], reverse=True)
    return alerts


def build_alert_message(alerts: List[Tuple[str, int, int]], ts: str) -> str:
    """Pushover 본문을 만든다.

    Args:
        alerts: :func:`decide_alerts` 결과.
        ts: 실행 시각 문자열.

    Returns:
        맵별 한 줄 + 실행 시각으로 구성된 본문.
    """
    lines = []
    for name, now, before in alerts:
        mark = "신규" if before == 0 else f"{before} -> {now}"
        lines.append(f"- {name}: {now}대 down ({mark})")
    lines.append(f"Time: {ts}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _read_credentials() -> Tuple[str, str]:
    """환경변수에서 WhatsUp 로그인 자격증명을 읽는다.

    Returns:
        ``(user_id, user_pw)``.

    Raises:
        RuntimeError: 필수 키가 없거나 비어 있는 경우.
    """
    user_id = os.getenv("WHATSUP_USER_ID", "").strip()
    user_pw = os.getenv("WHATSUP_USER_PW", "").strip()
    missing = [
        k for k, v in (
            ("WHATSUP_USER_ID", user_id),
            ("WHATSUP_USER_PW", user_pw),
        ) if not v
    ]
    if missing:
        raise RuntimeError(f"WhatsUp 로그인 자격증명 누락: {', '.join(missing)}")
    return user_id, user_pw


def main() -> int:
    """WhatsUp 모니터 진입점. ``--dry-run`` 이면 알림/상태 저장을 하지 않는다."""
    parser = argparse.ArgumentParser(description="WhatsUp Gold items-down monitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파싱 결과만 출력하고 알림 전송/상태 저장을 하지 않는다.",
    )
    args = parser.parse_args()

    config.ensure_dirs()

    stage = "init"
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.info("[START] whatsup run at %s dry_run=%s", start_ts, args.dry_run)

    try:
        stage = "fetch"
        LOG.info("[STAGE] %s", stage)
        user_id, user_pw = _read_credentials()
        html = fetch_main_html(user_id, user_pw)

        stage = "parse"
        LOG.info("[STAGE] %s", stage)
        rows: List[MapStatus] = parse_map_rows(html)
        LOG.info("맵 %d개 파싱 완료", len(rows))

        current = {r.name: r.down for r in rows if r.down > 0}
        for r in rows:
            LOG.debug(
                "%s up=%d down=%d svc_down=%d", r.name, r.up, r.down, r.services_down
            )

        stage = "decide"
        prev = load_prev_down()
        alerts = decide_alerts(prev, current)

        if current:
            LOG.info(
                "down 감지: %s",
                ", ".join(f"{n}={v}" for n, v in sorted(current.items())),
            )
        else:
            LOG.info("down 없음 (전 맵 정상)")

        stage = "notify"
        if alerts:
            message = build_alert_message(alerts, start_ts)
            LOG.info("[ALERT] %d건\n%s", len(alerts), message)
            if args.dry_run:
                LOG.info("dry-run: 알림 전송 생략")
            else:
                send_pushover_emergency(
                    title=f"[WhatsUp] 장애 감지 {len(alerts)}건",
                    message=message,
                )
        else:
            LOG.info("알림 대상 없음 (신규/증가 없음)")

        stage = "save_state"
        if args.dry_run:
            LOG.info("dry-run: 스냅샷 저장 생략")
        else:
            save_prev_down(current)

        LOG.info("[OK] 실행 완료")
        return 0

    except Exception as e:
        # 파일 로그에 traceback 까지 남긴다.
        LOG.exception("[FAIL] stage=%s err=%r", stage, e)
        send_pushover_emergency(
            title="[WhatsUp] 모니터링 실패",
            message=f"Stage: {stage}\nError: {e}\nTime: {start_ts}",
        )
        return 1

    finally:
        LOG.info("[END] whatsup run finished")


if __name__ == "__main__":
    raise SystemExit(main())
