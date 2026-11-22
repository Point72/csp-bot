"""Bot commands for csp-bot.

Commands can leverage chatom's cross-platform features for
mentions, formatting, and entity recognition.

Two command APIs are available:

1. ``@command`` decorator for stateless function-based commands
2. ``Command`` BaseModel subclass for stateful class-based commands

The legacy ``BaseCommand`` hierarchy is still supported via
``LegacyCommandAdapter``.
"""

from csp_bot.utils import mention_user

from .base import (
    BaseCommand,
    BaseCommandModel,
    NoResponseCommand,
    ReplyCommand,
    ReplyToAllCommand,
    ReplyToAuthorCommand,
    ReplyToOtherCommand,
)
from .context import BotInfo, CommandContext
from .echo import EchoCommand, EchoCommandModel
from .executor import execute_command_func
from .framework import Command, CommandEntry, CommandModel, clear_registry, command, get_registered_commands
from .help import HelpCommand, HelpCommandModel
from .legacy import LegacyCommandAdapter
from .management import LogsCommand, LogsCommandModel, MessagesCommand, MessagesCommandModel, WhoamiCommand, WhoamiCommandModel
from .schedule import ScheduleCommand, ScheduleCommandModel
from .status import StatusCommand, StatusCommandModel

__all__ = (
    "BaseCommand",
    "BaseCommandModel",
    "BotInfo",
    "Command",
    "CommandContext",
    "CommandEntry",
    "CommandModel",
    "EchoCommand",
    "EchoCommandModel",
    "HelpCommand",
    "HelpCommandModel",
    "LegacyCommandAdapter",
    "LogsCommand",
    "LogsCommandModel",
    "MessagesCommand",
    "MessagesCommandModel",
    "NoResponseCommand",
    "ReplyCommand",
    "ReplyToAllCommand",
    "ReplyToAuthorCommand",
    "ReplyToOtherCommand",
    "ScheduleCommand",
    "ScheduleCommandModel",
    "StatusCommand",
    "StatusCommandModel",
    "WhoamiCommand",
    "WhoamiCommandModel",
    "clear_registry",
    "command",
    "execute_command_func",
    "get_registered_commands",
    "mention_user",
)

try:
    from .agent import AgentCommand, AgentCommandModel
    from .claude_agent import ClaudeAgentCommand

    __all__ += ("AgentCommand", "AgentCommandModel", "ClaudeAgentCommand")
except ImportError:
    pass
