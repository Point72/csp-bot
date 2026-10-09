"""CSP Bot - Multi-platform chat bot using chatom.

This package provides a CSP-based bot framework that leverages
chatom for unified cross-platform chat support.

Key features:
- Unified Message, User, Channel models via chatom
- Cross-platform mention generation
- Backend-specific message formatting
- Entity recognition and parsing
- Support for Slack, Symphony, and Discord
"""

__version__ = "2.0.3"

# Re-export chatom types for convenience
from chatom import Channel, Message, User

from .bot import Bot
from .bot_config import BotConfig, DiscordConfig, SlackConfig, SymphonyConfig, TelegramConfig
from .commands import (
    BaseCommand,
    BaseCommandModel,
    BotInfo,
    Command,
    CommandContext,
    CommandModel,
    EchoCommand,
    HelpCommand,
    LegacyCommandAdapter,
    NoResponseCommand,
    ReplyCommand,
    ReplyToAllCommand,
    ReplyToAuthorCommand,
    ReplyToOtherCommand,
    ScheduleCommand,
    StatusCommand,
    command,
    mention_user,
)
from .gateway import CspBotGateway, Gateway, GatewayChannels, GatewayModule, GatewaySettings
from .persistence import FsspecStateStore, InMemoryStateStore, ScheduledCommandRecord, ScheduleStore, StateStore, StoredRecord
from .structs import Backend, BotCommand, BotMessage, CommandVariant
from .utils import format_message, get_backend_format, is_valid_url, mention_users

# Alias for backwards compatibility with tests
Channels = GatewayChannels

__all__ = (
    "Backend",
    "BaseCommand",
    "BaseCommandModel",
    "Bot",
    "BotCommand",
    "BotConfig",
    "BotInfo",
    "BotMessage",
    "Channel",
    "Channels",
    "Command",
    "CommandContext",
    "CommandModel",
    "CommandVariant",
    "CspBotGateway",
    "DiscordConfig",
    "EchoCommand",
    "FsspecStateStore",
    "Gateway",
    "GatewayChannels",
    "GatewayModule",
    "GatewaySettings",
    "HelpCommand",
    "InMemoryStateStore",
    "LegacyCommandAdapter",
    "Message",
    "NoResponseCommand",
    "ReplyCommand",
    "ReplyToAllCommand",
    "ReplyToAuthorCommand",
    "ReplyToOtherCommand",
    "ScheduleCommand",
    "ScheduleStore",
    "ScheduledCommandRecord",
    "SlackConfig",
    "StateStore",
    "StatusCommand",
    "StoredRecord",
    "SymphonyConfig",
    "TelegramConfig",
    "User",
    "__version__",
    "command",
    "format_message",
    "get_backend_format",
    "is_valid_url",
    "mention_user",
    "mention_users",
)
