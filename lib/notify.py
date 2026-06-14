"""Telegram control plane — reuse of your Fastmail-agent pattern.

Agents post here so you stay in the loop: results logged, params proposed, injuries
flagged. No-ops cleanly if env vars are unset, so local runs don't require Telegram.
"""
from __future__ import annotations
import os
import httpx


def notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[notify] {text}")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:  # never let notification failure break an agent
        print(f"[notify failed: {e}] {text}")
