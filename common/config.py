"""Shared configuration loaded from ``.env`` 와 ``config/daily.yaml``.

두 소스의 역할 구분:
    - ``.env`` : 거의 안 바뀌는 비밀 (Pushover/Telegram/Supabase 토큰,
      Zenius·DailyService·Jennifer 자격증명 등). git ignore.
    - ``config/daily.yaml`` : 매일 갱신하는 값 (KWorks 자격증명·target_title,
      runner ``run_until``, ``jobs.server`` 의 ``times``). git ignore — 비밀값
      넣어도 안전.

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
