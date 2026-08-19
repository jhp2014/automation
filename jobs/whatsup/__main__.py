"""WhatsUp Gold(NMS) 맵별 Items Down 감시 job (규약 v1.1).

동작:
    1. ``http://nms.kyowon.co.kr/`` 를 HTTP 인증으로 1회 GET (브라우저 미사용 —
       JS 없는 서버렌더 페이지라 requests 로 충분하다).
    2. 맵 표를 파싱해 맵별 ``Items Down`` 을 읽는다.
    3. 상태 파일(``state/whatsup_down.json``)을 갱신하며 알림 여부를 판정한다.

알림 정책(합의):
    - **2회 연속 확인 후 알림.** 장비가 잠깐 떴다 꺼지는 순간 이벤트로 알림이
      나가지 않도록, 같은 맵이 연속 :data:`CONFIRM_HITS` 회 down 으로 잡혀야
      비로소 보낸다. 1회차에 사라지면 조용히 무시된다(알림 지연 = 폴링 1주기).
    - 재확인 기준은 "down > 0" 이다. 1회차 1대가 2회차에 3대가 돼도 확인으로
      보고 현재 수치로 알린다(악화 상황에서 알림이 밀리지 않게).
    - **이미 알린 맵의 증가는 즉시 알린다.** 진짜 장애로 확인된 맵이므로 확대는
      대기 없이 보낸다.
    - 복구 알림은 보내지 않는다. 복구되면 상태에서 조용히 사라지고, 나중에
      재발하면 다시 (2회 확인 후) 알림이 간다.
    - 일부만 복구되면(3대 -> 1대) 알림 기준선도 함께 내려가, 다시 2대가 되면
      증가 알림이 나간다.

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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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

# 감시 상태 파일. {맵이름: {"down": 수, "hits": 연속 감지 횟수, "alerted_down": 알린 수}}
STATE_DOWN = config.STATE_DIR / "whatsup_down.json"

# 알림 전 요구되는 연속 감지 횟수. 2 면 "감지 -> 다음 실행에도 여전히 down" 일 때
# 알린다(폴링 180초 기준 알림이 3분 늦는 대신 순간 이벤트를 걸러낸다).
CONFIRM_HITS = 2

# HTTP 요청 타임아웃(초). runner 의 job timeout_sec 보다 충분히 작아야 한다.
HTTP_TIMEOUT_SEC = 15

LOG = get_logger("jobs.whatsup", "whatsup.log")


# ---------------------------------------------------------------------------
# 상태 스냅샷
# ---------------------------------------------------------------------------

def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """상태 파일의 값 하나를 현재 포맷으로 정규화한다.

    구버전 포맷(``{맵이름: down 수}``)도 받아들여 '이미 알림 완료' 로 흡수한다.
    덕분에 상태 파일 마이그레이션이 필요 없다.

    Args:
        raw: JSON 에서 읽은 값 (정수 또는 dict).

    Returns:
        ``{"down": int, "hits": int, "alerted_down": int | None}``.
        해석할 수 없으면 None.
    """
    if isinstance(raw, bool):  # bool 은 int 의 하위형 — 잘못된 값으로 본다.
        return None
    if isinstance(raw, int):
        # 구버전: 값이 곧 알림까지 끝난 down 수.
        return {"down": raw, "hits": CONFIRM_HITS, "alerted_down": raw}
    if isinstance(raw, dict):
        try:
            down = int(raw["down"])
            hits = int(raw.get("hits", 1))
            alerted = raw.get("alerted_down")
            return {
                "down": down,
                "hits": hits,
                "alerted_down": None if alerted is None else int(alerted),
            }
        except (KeyError, TypeError, ValueError):
            return None
    return None


def load_state() -> Dict[str, Dict[str, Any]]:
    """감시 상태를 로드한다.

    Returns:
        ``{맵이름: {"down", "hits", "alerted_down"}}``. 파일이 없거나 깨졌으면
        빈 dict — 그 경우 현재 down 들이 '1회차'부터 다시 시작하므로, 알림이
        한 주기 늦어질 수는 있어도 장애를 놓치지는 않는다.
    """
    if not STATE_DOWN.exists():
        return {}
    try:
        data = json.loads(STATE_DOWN.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 손상 파일은 빈 상태로 진행
        LOG.warning("상태 파일 로드 실패(빈 값으로 진행): %r", e)
        return {}

    if not isinstance(data, dict):
        LOG.warning("상태 파일 형식이 dict 가 아님(빈 값으로 진행)")
        return {}

    state: Dict[str, Dict[str, Any]] = {}
    for name, raw in data.items():
        entry = _normalize_entry(raw)
        if entry is None:
            LOG.warning("상태 항목 해석 실패(무시): %s=%r", name, raw)
            continue
        state[str(name)] = entry
    return state


def save_state(state: Dict[str, Dict[str, Any]]) -> None:
    """감시 상태를 저장한다.

    Args:
        state: ``{맵이름: {"down", "hits", "alerted_down"}}``.
            down 이 0 인 맵은 애초에 들어 있지 않다.
    """
    STATE_DOWN.parent.mkdir(parents=True, exist_ok=True)
    STATE_DOWN.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
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

@dataclass(frozen=True)
class Alert:
    """알림 1건.

    Attributes:
        name: 맵 이름.
        down: 현재 down 수.
        alerted_before: 직전에 알렸던 down 수. None 이면 이 맵의 첫 알림.
        hits: 연속 감지 횟수(첫 알림이면 :data:`CONFIRM_HITS` 이상).
    """

    name: str
    down: int
    alerted_before: Optional[int]
    hits: int


def advance(
    prev: Dict[str, Dict[str, Any]],
    current: Dict[str, int],
) -> Tuple[Dict[str, Dict[str, Any]], List[Alert]]:
    """상태를 한 주기 진행시키고 알릴 대상을 고른다.

    규칙:
        - down > 0 인 맵은 ``hits`` 를 1 올린다(직전에 없던 맵이면 1부터).
        - down 이 0 이 된 맵은 결과 상태에서 사라진다(= 복구, 무음).
        - 아직 안 알린 맵이 ``hits >= CONFIRM_HITS`` 가 되면 알린다.
        - 이미 알린 맵은 현재 down 이 알림 기준선을 넘을 때만 알린다(즉시).
        - 일부 복구로 down 이 기준선보다 낮아지면 기준선도 내린다(무음).

    Args:
        prev: 직전 상태 ``{맵이름: {"down", "hits", "alerted_down"}}``.
        current: 이번 실행의 ``{맵이름: down 수}`` (down > 0 인 맵만).

    Returns:
        ``(새 상태, 알림 목록)``. 알림 목록은 down 수 내림차순.
    """
    new_state: Dict[str, Dict[str, Any]] = {}
    alerts: List[Alert] = []

    for name, down in current.items():
        old = prev.get(name)
        hits = old["hits"] + 1 if old else 1
        alerted: Optional[int] = old["alerted_down"] if old else None

        if alerted is None:
            if hits >= CONFIRM_HITS:
                alerts.append(Alert(name, down, None, hits))
                alerted = down
        elif down > alerted:
            alerts.append(Alert(name, down, alerted, hits))
            alerted = down
        elif down < alerted:
            # 일부 복구 — 조용히 기준선을 내려, 다시 늘면 증가 알림이 나가게 한다.
            alerted = down

        new_state[name] = {"down": down, "hits": hits, "alerted_down": alerted}

    alerts.sort(key=lambda a: a.down, reverse=True)
    return new_state, alerts


def build_alert_message(alerts: List[Alert], ts: str) -> str:
    """Pushover 본문을 만든다.

    Args:
        alerts: :func:`advance` 가 고른 알림 목록.
        ts: 실행 시각 문자열.

    Returns:
        맵별 한 줄 + 실행 시각으로 구성된 본문.
    """
    lines = []
    for a in alerts:
        mark = (
            f"신규 · {a.hits}회 연속 확인"
            if a.alerted_before is None
            else f"{a.alerted_before} -> {a.down}"
        )
        lines.append(f"- {a.name}: {a.down}대 down ({mark})")
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
        prev = load_state()
        new_state, alerts = advance(prev, current)

        if current:
            LOG.info(
                "down 감지: %s",
                ", ".join(f"{n}={v}" for n, v in sorted(current.items())),
            )
            # 아직 확인 대기 중인 맵을 남겨둔다 — "왜 알림이 안 왔지?" 의 답이 된다.
            pending = [
                f"{n}({e['hits']}/{CONFIRM_HITS}회)"
                for n, e in sorted(new_state.items())
                if e["alerted_down"] is None
            ]
            if pending:
                LOG.info("확인 대기(알림 보류): %s", ", ".join(pending))
        else:
            LOG.info("down 없음 (전 맵 정상)")

        recovered = sorted(set(prev) - set(new_state))
        if recovered:
            LOG.info("복구(무음): %s", ", ".join(recovered))

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
            LOG.info("dry-run: 상태 저장 생략")
        else:
            save_state(new_state)

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
