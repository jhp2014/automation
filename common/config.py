"""Shared configuration loaded from ``.env``.

Importing this module has no side effects beyond reading environment variables
(no directory creation, no network). Use :func:`ensure_dirs` explicitly when
runtime directories are needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR: Path = Path(__file__).resolve().parent.parent
LOG_DIR: Path = BASE_DIR / "logs"
STATE_DIR: Path = BASE_DIR / "state"

load_dotenv(BASE_DIR / ".env")

PUSHOVER_TOKEN: str = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER: str = os.getenv("PUSHOVER_USER", "")


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
