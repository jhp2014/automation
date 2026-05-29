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
    token = config.PUSHOVER_TOKEN
    user = config.PUSHOVER_USER
    if not token or not user:
        _logger.warning("Pushover not configured (PUSHOVER_TOKEN/PUSHOVER_USER missing) - skip send")
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
            _logger.warning("Pushover send failed: status=%s body=%s", resp.status_code, resp.text[:300])
    except Exception as e:
        _logger.warning("Pushover send error: %r", e)


def send_telegram_message(target: TelegramTarget, text: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{target.bot_token}/sendMessage"
    payload = {"chat_id": target.chat_id, "text": text}
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code >= 400:
            _logger.warning("Telegram sendMessage failed: status=%s body=%s", resp.status_code, resp.text[:300])
    except Exception as e:
        _logger.warning("Telegram sendMessage error: %r", e)


def send_telegram_photo(target: TelegramTarget, caption: str, image_path: Union[str, Path]) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{target.bot_token}/sendPhoto"
    path = Path(image_path)
    try:
        f = open(path, "rb")
    except Exception as e:
        _logger.warning("Telegram sendPhoto open failed: path=%s err=%r", path, e)
        return

    try:
        files = {"photo": f}
        data = {"chat_id": target.chat_id, "caption": caption}
        try:
            resp = requests.post(url, data=data, files=files, timeout=25)
            if resp.status_code >= 400:
                _logger.warning("Telegram sendPhoto failed: status=%s body=%s", resp.status_code, resp.text[:300])
        except Exception as e:
            _logger.warning("Telegram sendPhoto error: %r", e)
    finally:
        try:
            f.close()
        except Exception:
            pass


def send_heartbeat(target: TelegramTarget, source: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    send_telegram_message(target, f"[HB] {source} running - {ts}")
