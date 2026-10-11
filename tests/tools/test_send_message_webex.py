"""Webex delivery through the migrated routing and HTTP sender modules."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web
from aiohttp.test_utils import TestServer

from agent.secret_scope import reset_secret_scope, set_secret_scope
from gateway.config import Platform
from tools.send_message_tool import _send_to_platform, send_message_tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "message", "target_field"),
    [("alice@example.com", "hello", "toPersonEmail"),
     ("Y2lzY29zcGFyazovL3VzL1JPT00vYWJjMTIz", "", "roomId")],
)
async def test_webex_route_uploads_media_and_preserves_thread(
    tmp_path, monkeypatch, target, message, target_field,
):
    attachment = tmp_path / "report.txt"
    attachment.write_bytes(b"report contents")
    parent_id = "Y2lzY29zcGFyazovL3VzL01FU1NBR0Uva2xv"
    resolved_room = "Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ"
    requests = []

    async def receive(request):
        assert request.headers["Authorization"] == "Bearer served-token"
        if request.content_type == "application/json":
            payload = await request.json()
        else:
            fields = await request.post()
            uploaded = fields["files"]
            payload = {
                target_field: fields[target_field], "parentId": fields["parentId"],
                "filename": uploaded.filename, "content": uploaded.file.read(),
                "content_type": uploaded.content_type,
            }
        requests.append(payload)
        return web.json_response(
            {"id": f"message-{len(requests)}", "roomId": resolved_room, "parentId": parent_id},
            status=201,
        )

    app = web.Application()
    app.router.add_post("/v1/messages", receive)
    real_client_session = aiohttp.ClientSession
    async with TestServer(app) as server:
        class LocalSession:
            def __init__(self, **kwargs):
                self.session = real_client_session(**kwargs)

            async def __aenter__(self):
                await self.session.__aenter__()
                return self

            async def __aexit__(self, *args):
                return await self.session.__aexit__(*args)

            def post(self, url, **kwargs):
                assert url == "https://webexapis.com/v1/messages"
                return self.session.post(server.make_url("/v1/messages"), **kwargs)

        monkeypatch.setattr(aiohttp, "ClientSession", LocalSession)
        monkeypatch.setenv("WEBEX_BOT_TOKEN", "launch-token")
        scope = set_secret_scope({"WEBEX_BOT_TOKEN": "served-token"})
        try:
            result = await _send_to_platform(
                Platform.WEBEX, SimpleNamespace(token=None, extra={}), target, message,
                thread_id=parent_id, media_files=[(str(attachment), False)],
            )
        finally:
            reset_secret_scope(scope)

    expected = []
    if message:
        expected.append({"markdown": message, target_field: target, "parentId": parent_id})
    expected.append({
        target_field: target, "parentId": parent_id, "filename": "report.txt",
        "content": b"report contents", "content_type": "text/plain",
    })
    assert requests == expected
    assert result["success"] is True
    assert result["resolved_chat_id"] == resolved_room
    assert result["resolved_thread_id"] == parent_id
    assert result["message_id"] == f"message-{len(requests)}"


def _run_async_immediately(coro):
    return asyncio.run(coro)


def test_send_to_webex_uses_resolved_chat_id_for_mirroring():
    webex_cfg = SimpleNamespace(enabled=True, token="webex-token", extra={})
    config = SimpleNamespace(
        platforms={Platform.WEBEX: webex_cfg},
        get_home_channel=lambda _platform: None,
    )

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch(
             "tools.send_message_tool._send_to_platform",
             new=AsyncMock(
                 return_value={
                     "success": True,
                     "resolved_chat_id": "Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ",
                 }
             ),
         ) as send_mock, \
         patch("gateway.mirror.mirror_to_session", return_value=True) as mirror_mock:
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "webex:alice@example.com",
                    "message": "hello",
                }
            )
        )

    assert result["success"] is True
    send_mock.assert_awaited_once_with(
        Platform.WEBEX,
        webex_cfg,
        "alice@example.com",
        "hello",
        thread_id=None,
        media_files=[],
        force_document=False,
    )
    mirror_mock.assert_called_once_with(
        "webex",
        "Y2lzY29zcGFyazovL3VzL1JPT00vcm9vbQ",
        "hello",
        source_label="cli",
        thread_id=None,
        user_id=None,
    )
