import os
import ssl
from typing import Any

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    url: str = Field(pattern=r"^https://[^\s]+$")
    prefix: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=40)
    headers_env: dict[str, str] = Field(default_factory=dict, repr=False)
    tools: list[str] | None = None
    timeout: float = Field(default=60, gt=0, le=300)

    def build_server(self) -> Any:
        from pydantic_ai.mcp import MCPToolset

        headers = {}
        for header, variable in self.headers_env.items():
            value = os.environ.get(variable)
            if not value:
                raise ValueError(f"Missing MCP credential environment variable: {variable}")
            headers[header] = value
        return MCPToolset(
            self.url,
            id=self.prefix,
            headers=headers,
            verify=ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None),
            init_timeout=min(self.timeout, 30),
            read_timeout=self.timeout,
            tool_error_behavior="error",
            prefer_tasks=False,
        )

    def build_toolset(self) -> Any:
        toolset = self.build_server()
        if self.tools is not None:
            allowed = frozenset(self.tools)
            toolset = toolset.filtered(lambda _context, definition: definition.name in allowed)
        return toolset.prefixed(self.prefix)
