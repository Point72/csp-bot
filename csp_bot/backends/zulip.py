"""Zulip backend integration using chatom.

This module provides Zulip-specific types and adapters through chatom.
"""

__all__ = (
    "ZulipAdapter",
    "ZulipConfig",
    "ZulipMessage",
    "ZulipUser",
)

try:
    from chatom.zulip import (
        ZulipConfig,
        ZulipMessage,
        ZulipUser,
    )
    from csp_adapter_zulip import ZulipAdapter
except ImportError:
    from chatom import Message as ChatomMessage, User as ChatomUser
    from pydantic import BaseModel

    class ZulipConfig(BaseModel):
        """Placeholder when chatom.zulip is not available."""

    ZulipAdapter = None
    ZulipMessage = ChatomMessage
    ZulipUser = ChatomUser
