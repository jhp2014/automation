"""Shared configuration loaded from ``.env`` 와 ``config/daily.yaml``.

세 소스의 역할 구분:
    - ``.env`` : 거의 안 바뀌는 비밀 (Pushover/Telegram/Supabase 토큰,
      Zenius·DailyService·Jennifer 자격증명 등). git ignore.
    - ``config/daily.yaml`` : 매일 갱신하는 값 (KWorks 자격증명·target_title,
      runner ``run_until``, ``jobs.server`` 의 ``times``). git ignore — 비밀값
      넣어도 안전.
    - ``config/settings.yaml`` : 거의 안 바뀌는 동작 토글 (job 별 headless,
      KWorks 업로드 후 Enter 등록 여부). **git 추적, 비밀값 금지**.
      조회는 :func:`get_headless` / :func:`get_submit_by_enter` 헬퍼만 사용.

import 만으로 부작용 없음(디렉터리 생성·네트워크 X). 디렉터리는
:func:`ensure_dirs` 호출 시에만 생성.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from .daily import DailyServerTime, load_daily
from .settings import load_settings


BASE_DIR: Path = Path(__file__).resolve().parent.parent
LOG_DIR: Path = BASE_DIR / "logs"
STATE_DIR: Path = BASE_DIR / "state"

load_dotenv(BASE_DIR / ".env")

PUSHOVER_TOKEN: str = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER: str = os.getenv("PUSHOVER_USER", "")


# ---------------------------------------------------------------------------
# daily.yaml 에서 노출되는 값들 — 매일 갱신
# ---------------------------------------------------------------------------

# 파일이 없으면 None. 이 경우 아래 노출 상수들은 모두 빈 값이 되고, 실제로
# 그 값을 쓰는 job 이 사용 시점에 명확한 에러("daily.yaml ... 비어 있음")로 실패.
_daily = load_daily()

KWORKS_USER_ID: str = _daily.kworks.user_id if _daily else ""
KWORKS_USER_PW: str = _daily.kworks.user_pw if _daily else ""
KWORKS_TARGET_TITLE: str = _daily.kworks.target_title if _daily else ""

# runner 자동 종료 시각. 빈 문자열이면 무기한.
RUN_UNTIL: str = _daily.run_until if _daily else ""

# jobs.server one_time_list 의 times — daily.yaml 이 단일 소스.
SERVER_TIMES: List[DailyServerTime] = (
    list(_daily.server_times) if _daily else []
)


def ensure_dirs() -> None:
    """Create runtime directories (``LOG_DIR``, ``STATE_DIR``) if they do not exist.

    Idempotent. Safe to call multiple times.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TelegramTarget:
    """A Telegram destination bundle (bot token + chat id).

    Callers pass this object as a whole instead of handling the raw bot token,
    so notify helpers do not leak token strings through their call sites.
    """

    bot_token: str
    chat_id: str


def get_telegram_target(job_key: str, purpose: str) -> TelegramTarget:
    """Look up a Telegram bot/chat pair from environment variables.

    The lookup uses the naming convention::

        TELEGRAM_BOT__<JOBKEY>             = <bot_token>
        TELEGRAM_CHAT__<JOBKEY>__<PURPOSE> = <chat_id>

    ``job_key`` and ``purpose`` are case-insensitive (normalized to UPPER
    internally). Adding a new job is done by adding two ``.env`` keys — no
    code change required.

    Args:
        job_key: Logical job name (e.g. ``"zenius"``, ``"dailyservice"``).
        purpose: Logical channel purpose (e.g. ``"report"``, ``"heartbeat"``).

    Returns:
        A :class:`TelegramTarget` bundling the bot token and chat id.

    Raises:
        KeyError: If either env key is missing or empty. The message lists
            exactly which env key(s) were not set.
    """
    job = job_key.strip().upper()
    pur = purpose.strip().upper()

    bot_env = f"TELEGRAM_BOT__{job}"
    chat_env = f"TELEGRAM_CHAT__{job}__{pur}"

    bot_token = os.getenv(bot_env, "")
    chat_id = os.getenv(chat_env, "")

    missing = []
    if not bot_token:
        missing.append(bot_env)
    if not chat_id:
        missing.append(chat_env)
    if missing:
        raise KeyError(
            f"Telegram target not configured: missing env keys -> {', '.join(missing)}"
        )

    return TelegramTarget(bot_token=bot_token, chat_id=chat_id)


# ---------------------------------------------------------------------------
# settings.yaml 조회 헬퍼 (동작 토글 — 폴백 없음)
# ---------------------------------------------------------------------------

def get_headless(job_key: str) -> bool:
    """``settings.yaml`` 에서 ``<job_key>.headless`` 를 조회.

    폴백 없음: 파일이 없거나(:func:`load_settings` 가 raise) 해당 job 키가
    없으면 예외로 죽는다. CLI 로 ``--headless`` / ``--no-headless`` 를 명시한
    경우에는 호출부가 본 함수를 부르지 않으므로 settings.yaml 이 없어도 된다.

    Args:
        job_key: settings.yaml 최상위 job 키(예: ``"zenius"``).

    Returns:
        bool — 해당 job 의 headless 값.

    Raises:
        FileNotFoundError: settings.yaml 이 없는 경우.
        RuntimeError: 파싱/스키마 실패 또는 해당 job 키 누락.
    """
    cfg = load_settings()
    job = getattr(cfg, job_key, None)
    if job is None:
        raise RuntimeError(f"settings.yaml: {job_key} 누락")
    return job.headless


def get_submit_by_enter(job_key: str) -> bool:
    """``settings.yaml`` 에서 ``<job_key>.submit_by_enter`` 를 조회(server/capture 전용).

    폴백 없음: 파일·job 키·필드 중 하나라도 없으면 예외로 죽는다. CLI 로
    ``--submit`` / ``--no-submit`` 을 명시한 경우에는 호출부가 본 함수를 부르지
    않으므로 settings.yaml 이 없어도 된다.

    Args:
        job_key: settings.yaml 최상위 job 키(``"server"`` 또는 ``"capture"``).

    Returns:
        bool — 해당 job 의 submit_by_enter 값.

    Raises:
        FileNotFoundError: settings.yaml 이 없는 경우.
        RuntimeError: 파싱/스키마 실패, job 키 누락, 또는 submit_by_enter 미설정.
    """
    cfg = load_settings()
    job = getattr(cfg, job_key, None)
    if job is None:
        raise RuntimeError(f"settings.yaml: {job_key} 누락")
    if job.submit_by_enter is None:
        raise RuntimeError(f"settings.yaml: {job_key}.submit_by_enter 누락")
    return job.submit_by_enter
