import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_TIMEOUT = 10


def send_telegram(bot_token: str, chat_id: str, message: str):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
    }
    try:
        resp = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        logger.warning("Failed to send Telegram message", exc_info=True)
