"""runner heartbeat 송신기 — 데드맨 스위치(약화 금지).

== 데드맨 스위치 ==
이 모듈은 runner 의 생존을 **외부에서** 감지하기 위한 신호를 Healthchecks.io
에 주기적으로 보낸다(ping URL 에 GET 1회). runner 가 죽거나 멈추면 ping 이
끊기고, Healthchecks.io 가 period + grace 를 넘긴 시점에 연동된 Pushover 로
"runner down" 알람을 보낸다. 감시 로직은 전부 Healthchecks.io 쪽에 있다.

규약 v1.1 (데드맨 스위치 조항): 본 송신을 **제거하거나 약화해서는 안 된다**.
구체적으로는:
    1) 전송 실패가 runner 본 루프를 죽이면 안 된다 (예외는 모두 흡수).
    2) 그러나 연속 실패가 임계(MAX_CONSECUTIVE_FAILURES = 3) 에 도달하면
       Pushover 로 1회 경고를 보내어, "runner 자체는 살아있는데 heartbeat 만
       실패하는" 상태가 침묵 속에 흘러가지 않게 한다.
    3) 성공 시 연속 실패 카운터와 경고 플래그를 reset.

종료 사유와 무관하게(run_until / stop.flag / Ctrl+C / 크래시) ping 이 끊기면
down 알람이 간다. 정상 종료도 알림을 받는 것이 운영 의도다.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import requests

from common.logging import get_logger
from common.notify import send_pushover_emergency


_log = get_logger("runner.heartbeat")

# 데드맨 스위치 임계값 — 약화 금지.
MAX_CONSECUTIVE_FAILURES = 3

# ping 1회 HTTP 타임아웃(초). 본 루프를 오래 붙잡지 않도록 짧게.
PING_TIMEOUT_SEC = 10

# 실패 후 재시도 최소 간격(초). job 대기 루프(0.5초)에서도 불리므로, 네트워크
# 장애 때 0.5초마다 재시도하지 않게 막는다(기존 tick_sec 페이스와 동일).
RETRY_GAP_SEC = 10


class HeartbeatSender:
    """Healthchecks.io ping URL 로 생존 신호를 보낸다."""

    def __init__(
        self,
        *,
        ping_url: str,
        interval_sec: int,
    ) -> None:
        """송신기를 만든다.

        Args:
            ping_url: Healthchecks.io 체크의 ping URL
                (예: ``https://hc-ping.com/<uuid>``). 필수.
            interval_sec: 송신 간격.
        """
        if not ping_url:
            raise RuntimeError(
                "HEALTHCHECK_PING_URL 이 .env 에 비어 있습니다 — "
                "runner 데드맨 스위치 전용 ping URL 을 채우세요."
            )
        self._ping_url = ping_url.strip()
        self._interval = interval_sec

        # 연속 실패 카운터와 경고 플래그 — 데드맨 스위치 핵심 상태.
        self._consecutive_failures: int = 0
        self._warned_for_current_streak: bool = False

        # 마지막 송신 시도 시각(monotonic). 실패 재시도 간격 제한용.
        self._last_attempt: Optional[float] = None

    # ------------------------------------------------------------------ public

    def should_send(self, last_heartbeat_at: Optional[str]) -> bool:
        """주기가 지났는지(=다시 보내야 하는지).

        Args:
            last_heartbeat_at: 마지막 송신 시각 ISO 문자열 또는 None.

        Returns:
            True 이면 송신해야 한다.
        """
        if (
            self._last_attempt is not None
            and time.monotonic() - self._last_attempt < RETRY_GAP_SEC
        ):
            return False
        if not last_heartbeat_at:
            return True
        try:
            last_dt = datetime.fromisoformat(last_heartbeat_at)
        except Exception:
            return True
        return (datetime.now() - last_dt).total_seconds() >= self._interval

    def send(self) -> Optional[str]:
        """ping 1회. 성공 시 갱신할 ISO 시각 문자열을 반환.

        실패는 예외를 던지지 않고 None 을 반환한다. 연속 실패가 임계에 도달
        하면 Pushover 로 경고 1회.

        Returns:
            성공 시 ``datetime.now().isoformat()``. 실패 시 None.
        """
        self._last_attempt = time.monotonic()
        try:
            resp = requests.get(self._ping_url, timeout=PING_TIMEOUT_SEC)
            resp.raise_for_status()
        except Exception as e:
            self._on_failure(e)
            return None

        self._on_success()
        return datetime.now().isoformat()

    # ----------------------------------------------------------------- private

    def _on_success(self) -> None:
        """성공: 카운터/경고 플래그 reset, INFO 로깅."""
        if self._consecutive_failures > 0:
            _log.info(
                "heartbeat 복구 — 직전 연속 실패 %d회는 해소됨",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._warned_for_current_streak = False
        _log.info("[HEARTBEAT] ping to Healthchecks OK")

    def _on_failure(self, err: BaseException) -> None:
        """실패: 카운터 증가, 임계 도달 시 Pushover 1회."""
        self._consecutive_failures += 1
        _log.warning(
            "[HEARTBEAT] 송신 실패 #%d: %r",
            self._consecutive_failures,
            err,
        )

        # 임계 도달 시 1회만 경고(같은 실패 스트릭 동안 추가 경고는 보내지 않음).
        if (
            self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
            and not self._warned_for_current_streak
        ):
            send_pushover_emergency(
                title="[Runner] heartbeat 실패",
                message=(
                    f"runner alive but heartbeat failing — "
                    f"연속 실패 {self._consecutive_failures}회 (임계 {MAX_CONSECUTIVE_FAILURES}). "
                    f"마지막 에러: {err!r}"
                ),
            )
            self._warned_for_current_streak = True
