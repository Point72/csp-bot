"""IRC backend integration using chatom.

This module provides IRC-specific types and adapters through chatom.
"""

__all__ = (
    "IRCAdapter",
    "IRCConfig",
    "IRCMessage",
    "IRCUser",
)

try:
    from chatom.irc import (
        IRCConfig,
        IRCMessage,
        IRCUser,
    )
    from csp_adapter_irc import IRCAdapter
except ImportError:
    from chatom import Message as ChatomMessage, User as ChatomUser
    from pydantic import BaseModel

    class IRCConfig(BaseModel):
        """Placeholder when chatom.irc is not available."""

    IRCAdapter = None
    IRCMessage = ChatomMessage
    IRCUser = ChatomUser
