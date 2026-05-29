"""Unified notification helpers (Pushover + Telegram).

Per convention v1, transport failures here NEVER raise — they emit a warning
to the module logger and return silently. This guarantees that a failed
notification cannot kill the host job.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Union

import requests

from . import config
from .config import TelegramTarget
from .logging import get_logger


_logger = get_logger("common.notify")


PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
TELEGRAM_API_BASE = "https://api.telegram.org"


def send_pushover_emergency(title: str, message: str) -> None:
    """Send an emergency Pushover notification (priority=2).

    Uses ``retry=30`` and ``expire=900`` (15 minutes) per spec. The token and
    user key are read from :mod:`common.config`. If either is empty, the call
    is skipped with a warning log.

    Args:
        title: Notification title shown on the device.
        message: Notification body.

    Returns:
        None. Network failures are logged as warnings, not raised.
    """
    token = config.PUSHOVER_TOKEN
    user = config.PUSHOVER_USER
    if not token or not user:
        _logger.warning(
            "Pushover not configured (PUSHOVER_TOKEN/PUSHOVER_USER missing) - skip send"
        )
        return

    payload = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": 2,
        "retry": 30,
        "expire": 900,
    }
    try:
        resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=15)
        if resp.status_code >= 400:
            _logger.warning(
                "Pushover send failed: status=%s body=%s",
                resp.status_code,
                resp.text[:300],
            )
    except Exception as e:  # noqa: BLE001 - spec requires swallow + log
        _logger.warning("Pushover send error: %r", e)


def send_telegram_message(target: TelegramTarget, text: str) -> None:
    """Send a plain-text Telegram message.

    Args:
        target: Bot token + chat id bundle from
            :func:`common.config.get_telegram_target`.
        text: Message body.

    Returns:
        None. Network/API failures are logged and swallowed (timeout=10s).
    """
    url = f"{TELEGRAM_API_BASE}/bot{target.bot_token}/sendMessage"
    payload = {"chat_id": target.chat_id, "text": text}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code >= 400:
            _logger.warning(
                "Telegram sendMessage failed: status=%s body=%s",
                resp.status_code,
                resp.text[:300],
            )
    except Exception as e:  # noqa: BLE001
        _logger.warning("Telegram sendMessage error: %r", e)


def send_telegram_photo(
    target: TelegramTarget,
    caption: str,
    image_path: Union[str, Path],
) -> None:
    """Upload a photo to Telegram with a caption.

    Args:
        target: Bot token + chat id bundle.
        caption: Caption text shown under the photo.
        image_path: Local filesystem path to the image file.

    Returns:
        None. File-open failures and network/API failures are logged and
        swallowed (timeout=25s).
    """
    url = f"{TELEGRAM_API_BASE}/bot{target.bot_token}/sendPhoto"
    path = Path(image_path)
    try:
        f = open(path, "rb")
    except Exception as e:  # noqa: BLE001
        _logger.warning("Telegram sendPhoto open failed: path=%s err=%r", path, e)
        return

    try:
        files = {"photo": f}
        data = {"chat_id": target.chat_id, "caption": caption}
        try:
            resp = requests.post(url, data=data, files=files, timeout=25)
            if resp.status_code >= 400:
                _logger.warning(
                    "Telegram sendPhoto failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
        except Exception as e:  # noqa: BLE001
            _logger.warning("Telegram sendPhoto error: %r", e)
    finally:
        try:
            f.close()
        except Exception:
            pass


def send_heartbeat(target: TelegramTarget, source: str) -> None:
    """Send a standardized heartbeat ping over Telegram.

    Message format: ``[HB] <source> running - YYYY-MM-DD HH:MM:SS``.

    Args:
        target: Bot token + chat id bundle.
        source: Human-readable name of the calling job/process.

    Returns:
        None. Delegates to :func:`send_telegram_message`, so failures are
        logged and swallowed.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    send_telegram_message(target, f"[HB] {source} running - {ts}")
