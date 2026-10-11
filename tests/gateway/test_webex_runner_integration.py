"""Integration coverage for Webex adapter creation by the gateway runner."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner


def _make_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    return runner


def test_create_webex_adapter_injects_gateway_runner(monkeypatch):
    from gateway.platforms import webex

    runner = _make_runner()
    config = PlatformConfig(enabled=True, token="webex-token")
    adapter = MagicMock()
    adapter_factory = MagicMock(return_value=adapter)
    monkeypatch.setattr(webex, "check_webex_requirements", lambda: True)
    monkeypatch.setattr(webex, "WebexAdapter", adapter_factory)

    result = runner._create_adapter(Platform.WEBEX, config)

    adapter_factory.assert_called_once_with(config)
    assert result is adapter
    assert adapter.gateway_runner is runner


def test_create_webex_adapter_preserves_requirement_check(monkeypatch):
    from gateway.platforms import webex

    runner = _make_runner()
    config = PlatformConfig(enabled=True, token="webex-token")
    adapter_factory = MagicMock()
    monkeypatch.setattr(webex, "check_webex_requirements", lambda: False)
    monkeypatch.setattr(webex, "WebexAdapter", adapter_factory)

    result = runner._create_adapter(Platform.WEBEX, config)

    assert result is None
    adapter_factory.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_reconnect_can_start_webex_adapter(monkeypatch):
    from gateway.platforms import webex

    runner = _make_runner()
    adapter = webex.WebexAdapter(PlatformConfig(enabled=True, token="webex-token"))
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(webex.aiohttp, "ClientSession", MagicMock(return_value=session))
    adapter._acquire_platform_lock = MagicMock(return_value=True)
    adapter._release_platform_lock = MagicMock()
    adapter._load_bot_identity = AsyncMock()
    adapter._start_websocket_listener = AsyncMock()
    adapter._poll_loop = AsyncMock()

    try:
        assert await runner._connect_adapter_with_timeout(
            adapter, Platform.WEBEX, is_reconnect=True,
        ) is True
        adapter._start_websocket_listener.assert_awaited_once()
    finally:
        await adapter.disconnect()

    session.close.assert_awaited_once()


@pytest.mark.parametrize("thread_sessions_per_user", [False, True])
def test_stream_final_and_result_share_speaker_prefix_cleanup(thread_sessions_per_user):
    from gateway.run_turn_runner import TurnRunner
    from gateway.session import SessionSource

    runner = _make_runner()
    runner.config.thread_sessions_per_user = thread_sessions_per_user
    source = SessionSource(
        platform=Platform.WEBEX, chat_id="room-1", chat_type="group",
        thread_id="parent-1", user_name="Alice",
    )
    ctx = SimpleNamespace(source=source, result_holder=[None])
    turn = TurnRunner(runner, ctx)
    consumer = MagicMock()
    result = {"final_response": "[Alice] Reply", "messages": [], "completed": True}

    turn._finish_stream_consumer(result, [], consumer)

    expected = "[Alice] Reply" if thread_sessions_per_user else "Reply"
    consumer.finish.assert_called_once_with(expected)
    assert result["final_response"] == expected
    assert ctx.result_holder[0] is result
