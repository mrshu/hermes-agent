"""Webex's authenticated email and person ID authorize the same sender."""

from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.webex import WebexAdapter
from gateway.run import GatewayRunner


async def _inbound_runner(monkeypatch, allowlist_source, allowed_user, room_type):
    for key in (
        "WEBEX_ALLOWED_USERS", "WEBEX_ALLOW_ALL_USERS",
        "GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)
    extra = {"require_mention": False}
    if allowlist_source == "config":
        key = "allow_from" if room_type == "direct" else "group_allow_from"
        extra[key] = [allowed_user]
    else:
        monkeypatch.setenv(allowlist_source, allowed_user)
    config = PlatformConfig(enabled=True, token="webex-token", extra=extra)
    adapter = WebexAdapter(config)

    async def transport_get(path, params=None):
        if path == "rooms/room-1":
            return {"id": "room-1", "title": "Room", "type": room_type}
        if path == "people/person-1":
            return {"displayName": "Alice"}
        raise AssertionError(f"Unexpected API request: {path}")

    adapter._api_get_json = AsyncMock(side_effect=transport_get)
    event = await adapter._build_event({
        "resource": "messages", "event": "created",
        "data": {
            "id": "message-1", "roomId": "room-1", "roomType": room_type,
            "personId": "person-1", "personEmail": "alice@example.com",
            "text": "hello", "files": [],
        },
    })
    assert event is not None
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.WEBEX: config})
    runner.adapters = {Platform.WEBEX: adapter}
    runner.pairing_store = None
    return runner, event


@pytest.mark.asyncio
@pytest.mark.parametrize("room_type", ["direct", "group"])
@pytest.mark.parametrize(
    "allowlist_source", ["WEBEX_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS", "config"],
)
@pytest.mark.parametrize("allowed_user", ["alice@example.com", "person-1"])
async def test_webex_inbound_email_and_person_id_authorize_same_sender(
    monkeypatch, allowlist_source, allowed_user, room_type,
):
    runner, event = await _inbound_runner(
        monkeypatch, allowlist_source, allowed_user, room_type,
    )

    assert event.source.user_id == "alice@example.com"
    assert event.source.user_id_alt == "person-1"
    assert runner._is_user_authorized(event.source) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowlist_source", ["WEBEX_ALLOWED_USERS", "GATEWAY_ALLOWED_USERS", "config"],
)
@pytest.mark.parametrize(
    "allowed_user", ["person-2", "alice@other.example", "alice", "Alice"],
)
async def test_webex_inbound_rejects_other_principals_and_display_names(
    monkeypatch, allowlist_source, allowed_user,
):
    runner, event = await _inbound_runner(
        monkeypatch, allowlist_source, allowed_user, "direct",
    )

    assert runner._is_user_authorized(event.source) is False
