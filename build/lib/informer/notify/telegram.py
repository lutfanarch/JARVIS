"""Telegram notification helper for JARVIS.

This module implements a simple wrapper around the Telegram Bot API to
send messages to a configured chat.  It enforces a strict chat ID
allowlist specified via the ``TELEGRAM_CHAT_ID_ALLOWLIST`` environment
variable.  Duplicate messages are avoided by storing an idempotency
key derived from the trade parameters in a state directory on disk.

The expected environment variables are:

* ``TELEGRAM_BOT_TOKEN`` – the Telegram bot token used to authenticate.
* ``TELEGRAM_CHAT_ID`` – the chat ID (or user ID) to which the
  notification should be sent.
* ``TELEGRAM_CHAT_ID_ALLOWLIST`` – a comma‑separated list of chat IDs
  that are permitted to receive notifications.
* ``TELEGRAM_STATE_DIR`` – optional path to a directory where
  idempotency keys are stored.  Defaults to ``artifacts/state``.

Notifications are only sent when the chat ID is in the allowlist
and when an idempotency key derived from the trade (or run ID)
has not been recorded previously.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import requests


def _is_allowed(chat_id: str, allowlist_str: str) -> bool:
    """Return True if chat_id is in the comma‑separated allowlist."""
    allowlist = [cid.strip() for cid in allowlist_str.split(",") if cid.strip()]
    return chat_id in allowlist


def send_message(token: str, chat_id: str, text: str, *, dedupe_key: str | None = None) -> bool:
    """Send a message via the Telegram Bot API.

    This function validates the chat ID against the allowlist and uses
    a dedupe mechanism to avoid sending duplicate notifications for
    the same trade.  When a dedupe key is provided, a file named
    ``telegram_<dedupe_key>.sent`` is created in the state directory
    (``TELEGRAM_STATE_DIR`` or ``artifacts/state``).  If this file
    exists, the function returns without sending.

    Args:
        token: Telegram bot token.
        chat_id: Chat or user ID as a string.
        text: Message text to send.
        dedupe_key: Optional idempotency key.  If provided, duplicate
            messages with the same key will not be sent again.

    Returns:
        True if a message was sent, False otherwise.
    """
    # Enforce allowlist from environment
    allowlist_str = os.getenv("TELEGRAM_CHAT_ID_ALLOWLIST", "")
    if not _is_allowed(chat_id, allowlist_str):
        # Refuse to send to disallowed chat ID
        return False
    # Dedupe: check if key file exists
    if dedupe_key:
        state_dir = Path(os.getenv("TELEGRAM_STATE_DIR", "artifacts/state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        key_path = state_dir / f"telegram_{dedupe_key}.sent"
        if key_path.exists():
            # Already sent
            return False
    # Compose API request
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception:
        # Fail silently; do not crash on network errors
        return False
    # Record idempotency key
    if dedupe_key:
        try:
            key_path.touch()
        except Exception:
            # Ignore filesystem errors
            pass
    return True