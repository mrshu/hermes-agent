"""Current base-adapter contracts exercised by the Webex adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _make_adapter(*, token: str = "webex-token", **extra):
    from gateway.platforms.webex import WebexAdapter

    return WebexAdapter(
        PlatformConfig(enabled=True, token=token, extra=extra),
    )


class _ChunkStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.yield_count = 0

    async def iter_chunked(self, chunk_size):
        assert chunk_size > 0
        for chunk in self._chunks:
            self.yield_count += 1
            yield chunk


class _Response:
    def __init__(self, *, headers, chunks, status=200, text=""):
        self.headers = headers
        self.status = status
        self.content = _ChunkStream(chunks)
        self.read_called = False
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def read(self):
        self.read_called = True
        raise AssertionError("Webex attachment downloads must stream bounded chunks")

    async def text(self):
        return self._text


class _Session:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def get(self, *_args, **_kwargs):
        return self.response

    async def close(self):
        self.closed = True


class TestWebexEditContract:
    @pytest.mark.asyncio
    async def test_stream_consumer_can_finalize_webex_edit(self):
        from gateway.stream_consumer import GatewayStreamConsumer

        adapter = _make_adapter()
        adapter._api_put_json = AsyncMock(
            return_value={"id": "message-1", "roomId": "room-1"},
        )
        consumer = GatewayStreamConsumer(adapter, "room-1")

        result = await consumer._edit_message(
            message_id="message-1",
            content="complete response",
            finalize=True,
        )

        assert result.success is True
        adapter._api_put_json.assert_awaited_once_with(
            "messages/message-1",
            {"roomId": "room-1", "markdown": "complete response"},
        )


class TestWebexAttachmentBounds:
    @pytest.mark.asyncio
    async def test_document_rejects_oversized_content_length_before_reading(
        self, monkeypatch
    ):
        from gateway.platforms import webex

        response = _Response(
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "5",
                "Content-Disposition": 'attachment; filename="report.pdf"',
            },
            chunks=[b"abcde"],
        )
        adapter = _make_adapter()
        adapter._session = _Session(response)
        monkeypatch.setattr(webex, "get_inbound_media_max_bytes", lambda: 4)
        cache_document = MagicMock()
        monkeypatch.setattr(webex, "cache_document_from_bytes", cache_document)

        with pytest.raises(ValueError, match="Inbound document payload is too large"):
            await adapter._download_attachment("https://webex.example/report.pdf")

        assert response.content.yield_count == 0
        assert response.read_called is False
        cache_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_document_stops_when_stream_exceeds_declared_and_actual_limit(
        self, monkeypatch
    ):
        from gateway.platforms import webex

        response = _Response(
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "2",
                "Content-Disposition": 'attachment; filename="report.pdf"',
            },
            chunks=[b"abc", b"de", b"unreachable"],
        )
        adapter = _make_adapter()
        adapter._session = _Session(response)
        monkeypatch.setattr(webex, "get_inbound_media_max_bytes", lambda: 4)
        cache_document = MagicMock()
        monkeypatch.setattr(webex, "cache_document_from_bytes", cache_document)

        with pytest.raises(ValueError, match="Inbound document payload is too large"):
            await adapter._download_attachment("https://webex.example/report.pdf")

        assert response.content.yield_count == 2
        assert response.read_called is False
        cache_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_document_stream_within_limit_reaches_document_cache(self, monkeypatch):
        from gateway.platforms import webex

        response = _Response(
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "5",
                "Content-Disposition": 'attachment; filename="report.pdf"',
            },
            chunks=[b"ab", b"cde"],
        )
        adapter = _make_adapter()
        adapter._session = _Session(response)
        monkeypatch.setattr(webex, "get_inbound_media_max_bytes", lambda: 5)
        cache_document = MagicMock(return_value="/cache/report.pdf")
        monkeypatch.setattr(webex, "cache_document_from_bytes", cache_document)

        path, mime = await adapter._download_attachment(
            "https://webex.example/report.pdf"
        )

        assert (path, mime) == ("/cache/report.pdf", "application/pdf")
        assert response.read_called is False
        cache_document.assert_called_once_with(b"abcde", "report.pdf")


class TestWebexStartupClassification:
    @pytest.mark.asyncio
    async def test_missing_token_is_nonretryable(self, monkeypatch):
        monkeypatch.delenv("WEBEX_BOT_TOKEN", raising=False)
        adapter = _make_adapter(token="")

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "webex_missing_token"
        assert adapter.fatal_error_retryable is False

    @pytest.mark.asyncio
    async def test_explicit_auth_status_is_nonretryable(self, monkeypatch):
        from gateway.platforms import webex

        session = _Session(
            _Response(headers={}, chunks=[], status=401, text="unauthorized"),
        )
        adapter = _make_adapter()
        adapter._acquire_platform_lock = MagicMock(return_value=True)
        adapter._release_platform_lock = MagicMock()
        monkeypatch.setattr(
            webex.aiohttp,
            "ClientSession",
            MagicMock(return_value=session),
        )

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "webex_auth_error"
        assert adapter.fatal_error_retryable is False
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_transport_failure_remains_retryable(self, monkeypatch):
        from gateway.platforms import webex

        session = _Session(
            _Response(headers={}, chunks=[], status=503, text="unavailable"),
        )
        adapter = _make_adapter()
        adapter._acquire_platform_lock = MagicMock(return_value=True)
        adapter._release_platform_lock = MagicMock()
        monkeypatch.setattr(
            webex.aiohttp,
            "ClientSession",
            MagicMock(return_value=session),
        )

        assert await adapter.connect() is False
        assert adapter.fatal_error_code == "webex_api_unavailable"
        assert adapter.fatal_error_retryable is True
        assert session.closed is True

    def test_listener_classification_is_narrow(self):
        adapter = _make_adapter()

        missing_sdk = adapter._classify_listener_failure(
            "The Webex JS SDK packages are not installed. Run npm install."
        )
        ambiguous_crash = adapter._classify_listener_failure(
            "Webex listener exited before startup completed (code 1)"
        )

        assert missing_sdk.code == "webex_missing_dependency"
        assert missing_sdk.retryable is False
        assert ambiguous_crash.code == "webex_listener_fatal"
        assert ambiguous_crash.retryable is True


def test_webex_adapter_uses_profile_secrets_without_process_fallback(monkeypatch):
    from agent import secret_scope

    monkeypatch.setenv("WEBEX_BOT_TOKEN", "process-token")
    monkeypatch.setenv("WEBEX_WEBHOOK_PUBLIC_URL", "https://process.example.com")
    monkeypatch.setenv("WEBEX_WEBHOOK_SECRET", "process-secret")
    context = secret_scope.set_multiplex_context(True)
    scope = secret_scope.set_secret_scope({"WEBEX_BOT_TOKEN": "profile-token"})
    try:
        adapter = _make_adapter(token="")
    finally:
        secret_scope.reset_secret_scope(scope)
        secret_scope.reset_multiplex_context(context)

    assert adapter._token == "profile-token"
    assert adapter._public_url == ""
    assert adapter._secret == ""


def test_webex_thread_history_uses_profile_session_key(tmp_path, monkeypatch):
    from pathlib import Path

    from gateway.config import GatewayConfig
    from gateway.session import SessionStore

    hermes_home = tmp_path / ".hermes"
    (hermes_home / "profiles" / "secondary").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    adapter = _make_adapter()
    store = SessionStore(hermes_home / "sessions", GatewayConfig(multiplex_profiles=True))
    adapter.set_session_store(store)
    secondary_source = adapter.build_source(
        chat_id="room-1", chat_type="group", user_id="alice@example.com", thread_id="parent-1",
    )
    secondary_source.profile = "secondary"
    default_source = adapter.build_source(
        chat_id="room-1", chat_type="group", user_id="alice@example.com", thread_id="parent-1",
    )
    try:
        store.get_or_create_session(secondary_source)
        assert adapter._has_active_session_for_thread(source=secondary_source) is True
        assert adapter._has_active_session_for_thread(source=default_source) is False
    finally:
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_webex_listener_monitor_survives_wait_timeout_until_exit():
    adapter = _make_adapter()
    adapter._running = True
    adapter._notify_fatal_error = AsyncMock()
    process = SimpleNamespace(wait=AsyncMock(side_effect=[TimeoutError, 0]))

    await adapter._listener_exit_loop(process)

    assert process.wait.await_count == 2
    assert adapter.fatal_error_code == "webex_listener_exited"
    adapter._notify_fatal_error.assert_awaited_once()
