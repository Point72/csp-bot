from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_mcp_config_resolves_environment_headers_and_filters_tools(monkeypatch):
    from csp_bot.commands.mcp import MCPServerConfig

    monkeypatch.setenv("TEST_MCP_KEY", "test-secret")
    factory = Mock()
    monkeypatch.setattr("pydantic_ai.mcp.MCPToolset", factory)
    config = MCPServerConfig(url="https://mcp.example/mcp", prefix="fetch", headers_env={"x-api-key": "TEST_MCP_KEY"}, tools=["fetch_image"])
    config.build_toolset()
    assert factory.call_args.kwargs["headers"] == {"x-api-key": "test-secret"}
    assert "test-secret" not in repr(config)
    predicate = factory.return_value.filtered.call_args.args[0]
    assert predicate(None, SimpleNamespace(name="fetch_image"))
    assert not predicate(None, SimpleNamespace(name="delete_file"))
    factory.return_value.filtered.return_value.prefixed.assert_called_once_with("fetch")


def test_missing_mcp_credential_fails_without_value(monkeypatch):
    from csp_bot.commands.mcp import MCPServerConfig

    monkeypatch.delenv("TEST_MCP_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_MCP_KEY"):
        MCPServerConfig(url="https://mcp.example/mcp", prefix="fetch", headers_env={"x-api-key": "TEST_MCP_KEY"}).build_toolset()


def test_mcp_requires_https():
    from csp_bot.commands.mcp import MCPServerConfig

    with pytest.raises(ValueError):
        MCPServerConfig(url="http://mcp.example/mcp", prefix="fetch")
