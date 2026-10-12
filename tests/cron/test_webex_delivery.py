"""Webex cron delivery resolves DM email targets to their reply-facing rooms."""

import asyncio
from unittest.mock import Mock

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent.secret_scope import reset_secret_scope, set_secret_scope
from cron.scheduler_delivery import _deliver_result, _resolve_delivery_targets
from gateway.config import Platform, PlatformConfig
from gateway.platforms.webex import WebexAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("live_adapter", [False, True])
@pytest.mark.parametrize("media_only", [False, True])
@pytest.mark.parametrize("media_status", [201, 503])
@pytest.mark.parametrize(
    ("target", "target_field"),
    [("alice@example.com", "toPersonEmail"),
     ("Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ", "roomId")],
)
async def test_cron_webex_mirrors_the_delivered_room(
    tmp_path, monkeypatch, target, target_field, live_adapter, media_only, media_status,
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  webex:\n    enabled: true\n"
        "cron:\n  wrap_response: false\n",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("WEBEX_BOT_TOKEN", "launch-token")
    parent_id = "Y2lzY29zcGFyazovL3VzL01FU1NBR0UvcGFyZW50"
    room_id = "Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ"
    attachment = tmp_path / "report.txt"
    attachment.write_bytes(b"report contents")
    requests = []

    async def receive(request):
        assert request.headers["Authorization"] == "Bearer served-token"
        if request.content_type == "application/json":
            payload = await request.json()
        else:
            fields = await request.post()
            uploaded = fields["files"]
            media_target_field = "roomId" if live_adapter and not media_only else target_field
            payload = {
                media_target_field: fields[media_target_field], "parentId": fields["parentId"],
                "filename": uploaded.filename, "content": uploaded.file.read(),
            }
        requests.append(payload)
        if request.content_type != "application/json" and media_status != 201:
            return web.json_response({"message": "upload rejected"}, status=media_status)
        return web.json_response(
            {"id": f"message-{len(requests)}", "roomId": room_id, "parentId": parent_id},
            status=201,
        )

    app = web.Application()
    app.router.add_post("/v1/messages", receive)
    real_session = aiohttp.ClientSession
    mirror = Mock(return_value=True)
    monkeypatch.setattr("gateway.mirror.mirror_to_session", mirror)
    async with TestServer(app) as server:
        class LocalSession:
            def __init__(self, **kwargs):
                self.session = real_session(**kwargs)

            async def __aenter__(self):
                await self.session.__aenter__()
                return self

            async def __aexit__(self, *args):
                return await self.session.__aexit__(*args)

            def post(self, url, **kwargs):
                assert url == "https://webexapis.com/v1/messages"
                return self.session.post(server.make_url("/v1/messages"), **kwargs)

        monkeypatch.setattr(aiohttp, "ClientSession", LocalSession)
        scope = set_secret_scope({"WEBEX_BOT_TOKEN": "served-token"})
        try:
            job = {
                "id": "webex-report", "name": "Webex report", "attach_to_session": True,
                "deliver": f"webex:{target}:{parent_id}",
            }
            async with real_session() as session:
                adapters = None
                if live_adapter:
                    adapter = WebexAdapter(PlatformConfig(enabled=True))
                    adapter._session = session
                    adapters = {Platform.WEBEX: adapter}
                    monkeypatch.setattr(
                        "gateway.platforms.webex.API_BASE", str(server.make_url("/v1")),
                    )
                error = await asyncio.to_thread(
                    _deliver_result, job,
                    f"MEDIA:{attachment}" if media_only else f"Daily report\nMEDIA:{attachment}",
                    adapters=adapters, loop=asyncio.get_running_loop() if live_adapter else None,
                )
        finally:
            reset_secret_scope(scope)

    if media_status == 503:
        assert "webex" in error.lower()
        if media_only or not live_adapter:
            mirror.assert_not_called()
        return
    assert error is None
    media_target = {"roomId": room_id} if live_adapter and not media_only else {target_field: target}
    expected = [] if media_only else [
        {"markdown": "Daily report", target_field: target, "parentId": parent_id},
    ]
    assert requests == [
        *expected,
        {**media_target, "parentId": parent_id, "filename": "report.txt",
         "content": b"report contents"},
    ]
    mirror.assert_called_once()
    assert mirror.call_args.args[:2] == ("webex", room_id)
    assert mirror.call_args.kwargs["thread_id"] == parent_id
    assert mirror.call_args.kwargs["role"] == "user"
    assert ("document attachment" if media_only else "Daily report") in mirror.call_args.args[2]


def test_webex_cron_home_targets_follow_the_served_profile(monkeypatch):
    monkeypatch.setenv("WEBEX_HOME_CHANNEL", "launch-room")
    monkeypatch.setenv("WEBEX_HOME_CHANNEL_THREAD_ID", "launch-thread")
    scope = set_secret_scope({
        "WEBEX_HOME_CHANNEL": "served-room",
        "WEBEX_HOME_CHANNEL_THREAD_ID": "served-thread",
    })
    try:
        home_targets = _resolve_delivery_targets({"id": "home", "deliver": "webex"})
        broadcast_targets = _resolve_delivery_targets({"id": "all", "deliver": "all"})
    finally:
        reset_secret_scope(scope)

    assert home_targets == [{
        "platform": "webex", "chat_id": "served-room", "thread_id": "served-thread",
        "_resolved_from": "home",
    }]
    assert [target for target in broadcast_targets if target["platform"] == "webex"] == [{
        "platform": "webex", "chat_id": "served-room", "thread_id": "served-thread",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_first", [False, True])
async def test_media_only_partial_delivery_mirrors_the_successful_attachment(
    tmp_path, monkeypatch, failed_first,
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "platforms:\n  webex:\n    enabled: true\n"
        "cron:\n  wrap_response: false\n",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    room_id = "Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ"
    parent_id = "Y2lzY29zcGFyazovL3VzL01FU1NBR0UvcGFyZW50"
    attachments = [tmp_path / name for name in ("delivered.txt", "rejected.txt")]
    for attachment in attachments:
        attachment.write_bytes(b"report contents")
    if failed_first:
        attachments.reverse()
    received = []

    async def receive(request):
        fields = await request.post()
        filename = fields["files"].filename
        received.append(filename)
        assert fields["toPersonEmail"] == "alice@example.com"
        assert fields["parentId"] == parent_id
        if filename == "rejected.txt":
            return web.json_response({"message": "upload rejected"}, status=503)
        return web.json_response(
            {"id": "delivered-message", "roomId": room_id, "parentId": parent_id},
            status=201,
        )

    app = web.Application()
    app.router.add_post("/v1/messages", receive)
    mirror = Mock(return_value=True)
    monkeypatch.setattr("gateway.mirror.mirror_to_session", mirror)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        monkeypatch.setattr("gateway.platforms.webex.API_BASE", str(server.make_url("/v1")))
        scope = set_secret_scope({"WEBEX_BOT_TOKEN": "served-token"})
        try:
            adapter = WebexAdapter(PlatformConfig(enabled=True))
            adapter._session = session
            error = await asyncio.to_thread(
                _deliver_result,
                {"id": "partial-report", "deliver": f"webex:alice@example.com:{parent_id}",
                 "attach_to_session": True},
                "\n".join(f"MEDIA:{attachment}" for attachment in attachments),
                adapters={Platform.WEBEX: adapter}, loop=asyncio.get_running_loop(),
            )
        finally:
            reset_secret_scope(scope)

    assert received == [attachment.name for attachment in attachments]
    assert "rejected.txt" in error
    mirror.assert_called_once()
    assert mirror.call_args.args[:2] == ("webex", room_id)
    assert mirror.call_args.kwargs["thread_id"] == parent_id
    assert mirror.call_args.kwargs["role"] == "user"
    assert "[Sent document attachment]" in mirror.call_args.args[2]
