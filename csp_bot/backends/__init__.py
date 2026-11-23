"""Backend adapters for csp-bot using chatom.

This module provides access to backend adapters through chatom's
unified interface. Each backend exposes:
- Config: Configuration class from chatom
- Adapter: CSP adapter from csp-adapter-*
- Message: Backend-specific message type from chatom
"""

from .discord import *
from .irc import *
from .matrix import *
from .slack import *
from .symphony import *
from .telegram import *
from .zulip import *
