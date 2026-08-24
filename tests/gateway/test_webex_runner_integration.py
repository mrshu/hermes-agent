"""Integration coverage for Webex adapter creation by the gateway runner."""

from unittest.mock import MagicMock

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
