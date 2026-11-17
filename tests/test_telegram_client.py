"""Telegram Bot API response-envelope tests, with no network access."""
import asyncio
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rwa_plugin_pershop.telegram import TelegramAPIError, TgClient


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _fake_httpx(body):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return FakeResponse(body)

    return types.SimpleNamespace(AsyncClient=FakeAsyncClient)


def _run(body, operation):
    with patch.dict(sys.modules, {"httpx": _fake_httpx(body)}):
        return asyncio.run(operation(TgClient("TOKEN")))


def _expect_api_error(body, operation, method, description=None):
    try:
        _run(body, operation)
    except TelegramAPIError as exc:
        assert exc.method == method
        if description is not None:
            assert exc.description == description
        assert method in str(exc)
    else:
        raise AssertionError(f"{method} accepted an unsuccessful/malformed response")


def test_call_rejects_error_and_missing_result():
    operation = lambda tg: tg.call("sendMessage", {})
    _expect_api_error(
        {"ok": False, "description": "bot was blocked", "result": {"message_id": 99}},
        operation, "sendMessage", "bot was blocked")
    _expect_api_error({"ok": True}, operation, "sendMessage")
    assert _run({"ok": True, "result": True}, operation) == {"ok": True, "result": True}


def test_get_updates_surfaces_errors_and_validates_result():
    operation = lambda tg: tg.get_updates(None)
    _expect_api_error(
        {"ok": False, "description": "conflicting polls", "result": []},
        operation, "getUpdates", "conflicting polls")
    _expect_api_error({"ok": True, "result": {}}, operation, "getUpdates")
    updates = [{"update_id": 10}]
    assert _run({"ok": True, "result": updates}, operation) == updates


def test_send_message_surfaces_errors_and_malformed_results():
    operation = lambda tg: tg.send_message(123, "hello")
    _expect_api_error(
        {"ok": False, "description": "chat not found", "result": {"message_id": 55}},
        operation, "sendMessage", "chat not found")
    for result in (None, {}, {"message_id": "not-an-id"}):
        _expect_api_error({"ok": True, "result": result}, operation, "sendMessage")
    assert _run({"ok": True, "result": {"message_id": 42}}, operation) == 42


def test_forward_message_surfaces_errors():
    _expect_api_error(
        {"ok": False, "description": "message to forward not found"},
        lambda tg: tg.forward_message(1, 2, 3),
        "forwardMessage", "message to forward not found")


def test_send_photo_stays_nonthrowing_without_accepting_errors():
    operation = lambda tg: tg.send_photo(123, b"image")
    assert _run(
        {"ok": False, "description": "bad photo", "result": {"message_id": 77}},
        operation) is None
    assert _run({"ok": True, "result": {}}, operation) is None
    assert _run({"ok": True, "result": {"message_id": 76}}, operation) == 76


def test_nonthrowing_convenience_behaviour_is_preserved():
    not_modified = {"ok": False, "description": "Bad Request: message is not modified"}
    assert _run(not_modified, lambda tg: tg.edit_text(1, 2, "same")) is True
    rejected = {"ok": False, "description": "message not found"}
    assert _run(rejected, lambda tg: tg.edit_text(1, 2, "new")) is False
    assert _run(rejected, lambda tg: tg.delete_message(1, 2)) is False
    assert _run(rejected, lambda tg: tg.edit_reply_markup(1, 2)) is False


if __name__ == "__main__":
    for name, test in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(test):
            test()
    print("TELEGRAM CLIENT TESTS PASSED")
