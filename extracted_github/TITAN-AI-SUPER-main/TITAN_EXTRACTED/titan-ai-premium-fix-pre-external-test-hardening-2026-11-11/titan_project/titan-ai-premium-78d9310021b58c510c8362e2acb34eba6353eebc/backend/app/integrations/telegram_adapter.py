from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token_ref: str | None = None  # env:TELEGRAM_BOT_TOKEN
    allowed_chat_ids_ref: str | None = None  # env:TELEGRAM_ALLOWED_CHAT_IDS


class TelegramAdapter:
    """Allowlist-protected Telegram transport; never executes trading commands.

    Safe status/report messages can be transported by an application handler.
    Start/stop/emergency/live commands are parsed as requests only and always
    require a separate authenticated confirmation in the application.
    """

    name = "telegram"
    KNOWN_COMMANDS = {"/status", "/startbot", "/stopbot", "/report", "/help", "/emergency", "/live"}
    SENSITIVE_COMMANDS = {"/startbot", "/stopbot", "/emergency", "/live"}

    def __init__(self, config: TelegramConfig | None = None):
        self.config = config or TelegramConfig()

    @staticmethod
    def _env_value(reference: str | None) -> str | None:
        if not reference or not reference.startswith("env:"):
            return None
        name = reference[4:].strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", name):
            return None
        value = os.getenv(name, "").strip()
        return value or None

    def _token(self) -> str | None:
        return self._env_value(self.config.bot_token_ref)

    def _allowed_chat_ids(self) -> set[str]:
        raw = self._env_value(self.config.allowed_chat_ids_ref)
        if not raw:
            return set()
        return {item.strip() for item in raw.split(",") if item.strip() and re.fullmatch(r"-?\d{1,24}", item.strip())}

    def status(self) -> dict[str, Any]:
        token = self._token()
        allowed = self._allowed_chat_ids()
        configured = bool(self.config.enabled and token and allowed)
        return {
            "provider": self.name,
            "enabled": bool(self.config.enabled),
            "configured": configured,
            "authorized_chat_count": len(allowed),
            "token_present": bool(token),
            "commands_execute_trades": False,
            "note": "Token is resolved only from an environment reference and is never returned. Sensitive commands are requests only; a separate authenticated confirmation is required.",
        }

    def is_authorized(self, chat_id: str) -> bool:
        value = str(chat_id or "").strip()
        return bool(value and value in self._allowed_chat_ids())

    def parse_command(self, chat_id: str, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        token = raw.split()[0] if raw else ""
        command = token.split("@", 1)[0].lower()
        args = raw.split()[1:] if raw else []
        authorized = self.is_authorized(chat_id)
        known = command in self.KNOWN_COMMANDS
        sensitive = command in self.SENSITIVE_COMMANDS
        return {
            "chat_id": str(chat_id),
            "command": command,
            "args": args,
            "known": known,
            "sensitive": sensitive,
            "authorized": authorized,
            "requires_confirmation": bool(authorized and sensitive),
            "executed": False,
            "reason": (
                "unauthorized_chat" if not authorized else
                "unknown_command" if not known else
                "explicit_app_confirmation_required" if sensitive else
                "safe_command_ready_for_read_only_handler"
            ),
        }

    def _request(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._token()
        if not self.config.enabled or not token or not self._allowed_chat_ids():
            return {"ok": False, "error": "telegram_not_configured"}
        url = f"https://api.telegram.org/bot{token}/{method}"
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "TITAN-AI-PREMIUM/1.0"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, dict) or not result.get("ok"):
                return {"ok": False, "error": "telegram_api_rejected_request"}
            return {"ok": True, "result": result.get("result")}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            # Do not include the request URL: it contains the bot token.
            return {"ok": False, "error": "telegram_transport_failed"}

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        if not self.is_authorized(chat_id):
            return {"ok": False, "error": "unauthorized_chat"}
        message = str(text or "")
        if not message or len(message) > 4096:
            return {"ok": False, "error": "message_length_out_of_range"}
        result = self._request("sendMessage", {"chat_id": str(chat_id), "text": message, "disable_web_page_preview": True})
        if not result.get("ok"):
            return result
        message_id = result.get("result", {}).get("message_id") if isinstance(result.get("result"), dict) else None
        return {"ok": True, "message_id": message_id}

    def get_updates(self, *, offset: int | None = None, timeout: int = 0, limit: int = 100) -> dict[str, Any]:
        if isinstance(timeout, bool) or not 0 <= int(timeout) <= 50:
            return {"ok": False, "error": "telegram_poll_timeout_out_of_range"}
        if isinstance(limit, bool) or not 1 <= int(limit) <= 100:
            return {"ok": False, "error": "telegram_poll_limit_out_of_range"}
        params = {"timeout": int(timeout), "limit": int(limit)}
        if offset is not None:
            params["offset"] = int(offset)
        result = self._request("getUpdates", params)
        if not result.get("ok"):
            return result
        updates = result.get("result") or []
        authorized = []
        ignored = 0
        for update in updates:
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            parsed = self.parse_command(chat_id, message.get("text", ""))
            if parsed["authorized"]:
                authorized.append({"update_id": update.get("update_id"), **parsed})
            else:
                ignored += 1
        return {
            "ok": True,
            "updates": authorized,
            "ignored_unauthorized_updates": ignored,
            "next_offset": (max((int(item.get("update_id", 0)) for item in updates), default=-1) + 1),
        }
