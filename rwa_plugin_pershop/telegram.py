"""Minimal Telegram Bot API client over httpx (panel-shipped).

Used by BOTH the webhook handler and the fallback poller, and by the
approve/deny sender. No third-party bot framework.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


class TelegramAPIError(Exception):
    """An unsuccessful or malformed response from the Telegram Bot API."""

    def __init__(self, description: str, method: str):
        self.description = description
        self.method = method
        super().__init__(f"{method}: {description}")


def _response_result(data: Any, method: str) -> Any:
    if not isinstance(data, dict):
        raise TelegramAPIError("Malformed Telegram API response", method)
    if data.get("ok") is not True:
        description = data.get("description")
        if not isinstance(description, str) or not description:
            description = "Telegram API request failed"
        raise TelegramAPIError(description, method)
    if "result" not in data:
        raise TelegramAPIError("Telegram API response missing result", method)
    return data["result"]


class TgClient:
    def __init__(self, token: str, timeout: float = 25.0):
        self._token = token
        self._timeout = timeout

    @property
    def base(self) -> str:
        return f"https://api.telegram.org/bot{self._token}"

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(f"{self.base}/{method}", json=payload)
            r.raise_for_status()
            data = r.json()
            _response_result(data, method)
            return data

    async def get_updates(self, offset: int | None, timeout_s: int = 20) -> list[dict]:
        import httpx

        params: dict[str, Any] = {"timeout": timeout_s}
        if offset is not None:
            params["offset"] = offset
        async with httpx.AsyncClient(timeout=timeout_s + 10) as c:
            r = await c.post(f"{self.base}/getUpdates", json=params)
            r.raise_for_status()
            result = _response_result(r.json(), "getUpdates")
            if not isinstance(result, list):
                raise TelegramAPIError("Malformed Telegram API result", "getUpdates")
            return result

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> int | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = await self.call("sendMessage", payload)
        result = _response_result(res, "sendMessage")
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramAPIError("Malformed Telegram API result", "sendMessage")
        return result["message_id"]

    async def send_photo(self, chat_id: int, data: bytes, filename: str = "photo.jpg",
                         caption: str | None = None, reply_markup: dict | None = None) -> int | None:
        """Multipart photo upload (broadcast attachments). Returns message_id.

        Non-throwing by design: returns None on any failure so the broadcast
        tick can count it as failed. API errors are logged, never reported
        as success."""
        import httpx
        import json as _json

        form: dict[str, Any] = {"chat_id": str(chat_id), "parse_mode": "HTML"}
        if caption:
            form["caption"] = caption[:1024]
        if reply_markup:
            form["reply_markup"] = _json.dumps(reply_markup)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(f"{self.base}/sendPhoto", data=form,
                                 files={"photo": (filename, data, "image/jpeg")})
                r.raise_for_status()
                result = _response_result(r.json(), "sendPhoto")
                if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
                    raise TelegramAPIError("Malformed Telegram API result", "sendPhoto")
                return result["message_id"]
        except Exception as e:
            _log.warning("sendPhoto failed chat=%s err=%s: %s", chat_id, type(e).__name__, e)
            return None

    async def edit_text(self, chat_id: int, message_id: int, text: str,
                    reply_markup: dict | None = None) -> bool:
        """Edit an already-sent message's text in place (buyer screens and
        owner decide cards). Returns True when the message now shows the
        text; False when a fresh send is needed instead. Non-throwing by
        design: API errors are logged, never reported as success."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id,
                                   "text": text, "parse_mode": "HTML",
                                   "reply_markup": reply_markup or {"inline_keyboard": []}}
        try:
            await self.call("editMessageText", payload)
            return True
        except Exception as e:
            body = ""
            try:
                resp = getattr(e, "response", None)
                if resp is not None:
                    body = str(resp.json())
            except Exception:
                body = ""
            if "message is not modified" in (body + str(e)).lower():
                return True
            _log.debug("editMessageText failed chat=%s mid=%s err=%s: %s",
                       chat_id, message_id, type(e).__name__, e)
            return False

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete one bot message (the receipt prompt after a receipt
        arrives). Non-throwing by design; False means already gone or
        cleanup can be skipped, and the failure is logged."""
        try:
            await self.call("deleteMessage",
                            {"chat_id": chat_id, "message_id": message_id})
            return True
        except Exception as e:
            _log.debug("deleteMessage failed chat=%s mid=%s err=%s: %s",
                       chat_id, message_id, type(e).__name__, e)
            return False

    async def edit_reply_markup(self, chat_id: int, message_id: int,
                                reply_markup: dict | None = None) -> bool:
        """Replace/clear inline buttons on an already-sent message.
        Non-throwing by design; False means the buttons are stale and the
        failure is logged."""
        try:
            await self.call("editMessageReplyMarkup",
                            {"chat_id": chat_id, "message_id": message_id,
                             "reply_markup": reply_markup or {"inline_keyboard": []}})
            return True
        except Exception as e:
            _log.debug("editMessageReplyMarkup failed chat=%s mid=%s err=%s: %s",
                       chat_id, message_id, type(e).__name__, e)
            return False

    async def forward_message(self, to_chat: int, from_chat: int, message_id: int) -> None:
        await self.call(
            "forwardMessage",
            {"chat_id": to_chat, "from_chat_id": from_chat, "message_id": message_id},
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def kb(rows: list[list[dict[str, str]]]) -> dict:
    return {"inline_keyboard": rows}


def reply_kb(rows: list[list[str]]) -> dict:
    """Persistent bottom panel (reply keyboard). Rows = plain label strings."""
    return {"keyboard": [[{"text": t} for t in r] for r in rows],
            "resize_keyboard": True, "is_persistent": True}
