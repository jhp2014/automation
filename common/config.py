from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"

load_dotenv(BASE_DIR / ".env")

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER = os.getenv("PUSHOVER_USER", "")


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class TelegramTarget:
    bot_token: str
    chat_id: str


def get_telegram_target(job_key: str, purpose: str) -> TelegramTarget:
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
