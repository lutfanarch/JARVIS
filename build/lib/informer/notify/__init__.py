"""Notification utilities for JARVIS.

This package contains modules responsible for dispatching messages to
external channels such as Telegram.  Notifications are sent only for
validated trades and are subject to a strict chat allowlist and
idempotency keys to prevent duplicate dispatches.
"""

from .telegram import send_message  # noqa: F401

__all__ = ["send_message"]