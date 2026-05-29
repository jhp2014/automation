"""runner heartbeat 송신기 — 데드맨 스위치(약화 금지).

== 데드맨 스위치 ==
이 모듈은 runner 의 생존을 **외부에서** 감지하기 위한 신호를 Supabase 에
주기적으로 기록한다. runner 가 죽거나 멈추면 Supabase 의 마지막 ``last_seen_at``
가 갱신되지 않으므로 외부 감시 시스템이 이를 보고 경보를 띄울 수 있다.

규약 v1.1 (데드맨 스위치 조항): 본 송신을 **제거하거나 약화해서는 안 된다**.
구체적으로는:
    1) 전송 실패가 runner 본 루프를 죽이면 안 된다 (예외는 모두 흡수).
    2) 그러나 연속 실패가 임계(MAX_CONSECUTIVE_FAILURES = 3) 에 도달하면
       Pushover 로 1회 경고를 보내어, "runner 자체는 살아있는데 heartbeat 만
       실패하는" 상태가 침묵 속에 흘러가지 않게 한다.
    3) 성공 시 연속 실패 카운터와 경고 플래그를 reset.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from supabase import Client, create_client

from common.logging import get_logger
from common.notify import send_pushover_emergency


_log = get_logger("runner.heartbeat")

# 데드맨 스위치 임계값 — 약화 금지.
MAX_CONSECUTIVE_FAILURES = 3


class HeartbeatSender:
    """Supabase ``runner_heartbeat`` 테이블에 ``last_seen_at`` 을 upsert."""

    def __init__(
        self,
        *,
        url: str,
        key: str,
        source: str,
        interval_sec: int,
    ) -> None:
        """클라이언트를 생성한다.

        Args:
            url: Supabase 프로젝트 URL.
            key: service role key (정확한 권한은 운영자가 관리).
            source: ``source`` 컬럼에 들어갈 식별자(예: ``"main_runner"``).
            interval_sec: 송신 간격.
        """
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL / SUPABASE_KEY 가 .env 에 비어 있습니다 — "
                "runner 데드맨 스위치 전용 키를 채우세요."
            )
        self._client: Client = create_client(url, key)
        self._source = source
        self._interval = interval_sec

        # 연속 실패 카운터와 경고 플래그 — 데드맨 스위치 핵심 상태.
        self._consecutive_failures: int = 0
        self._warned_for_current_streak: bool = False

    # ------------------------------------------------------------------ public

    def should_send(self, last_heartbeat_at: Optional[str]) -> bool:
        """주기가 지났는지(=다시 보내야 하는지).

        Args:
            last_heartbeat_at: 마지막 송신 시각 ISO 문자열 또는 None.

        Returns:
            True 이면 송신해야 한다.
        """
        if not last_heartbeat_at:
            return True
        try:
            last_dt = datetime.fromisoformat(last_heartbeat_at)
        except Exception:
            return True
        return (datetime.now() - last_dt).total_seconds() >= self._interval

    def send(self) -> Optional[str]:
        """Supabase 에 1회 upsert. 성공 시 갱신할 ISO 시각 문자열을 반환.

        실패는 예외를 던지지 않고 None 을 반환한다. 연속 실패가 임계에 도달
        하면 Pushover 로 경고 1회.

        Returns:
            성공 시 ``datetime.now().isoformat()``. 실패 시 None.
        """
        now_utc_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "source": self._source,
            "last_seen_at": now_utc_iso,
        }
        try:
            self._client.table("runner_heartbeat").upsert(payload).execute()
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
        _log.info("[HEARTBEAT] sent to Supabase OK")

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
