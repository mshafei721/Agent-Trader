"""Notifications. Console always; Telegram optional. Never crashes the caller."""
from __future__ import annotations

import requests

from ..config import Settings
from ..logging_setup import get_logger

log = get_logger("goldtrader.notify")


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings
        self._tg_enabled = bool(
            settings.telegram_bot_token and settings.telegram_chat_id
        )

    def notify(self, title: str, message: str = "") -> None:
        text = f"[goldtrader] {title}" + (f"\n{message}" if message else "")
        log.info("notify", title=title, message=message)
        if self._tg_enabled:
            self._telegram(text)

    def _telegram(self, text: str) -> None:
        try:
            token = self.s.telegram_bot_token.get_secret_value()
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(
                url,
                json={"chat_id": self.s.telegram_chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001 — notifications must never break the loop
            log.warning("telegram_failed", error=str(exc))
