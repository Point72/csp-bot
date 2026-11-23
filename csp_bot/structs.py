"""Data structures for csp-bot using chatom.

This module provides the core data structures for csp-bot,
leveraging chatom's unified message and user models.
"""

from datetime import datetime
from enum import Enum

from chatom import Channel, Message as ChatomMessage, User
from csp_gateway.utils.struct import GatewayStruct

__all__ = (
    "Backend",
    "BotCommand",
    "BotMessage",
    "CommandVariant",
)


# Type alias for supported backends
Backend = str  # "discord", "slack", "symphony"


class CommandVariant(Enum):
    """Variants of bot command responses."""

    NO_RESPONSE = 0
    """No response needed - used for triggering actions without reply."""

    REPLY = 1
    """Bot replies in the channel where the command was issued."""

    REPLY_TO_AUTHOR = 2
    """Bot replies mentioning the command author."""

    REPLY_TO_OTHER = 3
    """Bot replies mentioning a tagged user."""

    REPLY_TO_ALL = 4
    """Bot replies mentioning all tagged users."""


class BotMessage(GatewayStruct):
    """Message representation for bot responses.

    This provides a simple structure for bot responses that can
    be converted to backend-specific message types.
    """

    content: str
    """Plain text or formatted message content."""

    channel_id: str
    """Channel/room ID to send to."""

    channel_name: str
    """Channel/room name (for resolution)."""

    thread_id: str
    """Thread ID for threaded replies."""

    backend: str
    """Target backend platform."""

    mentions: tuple[str, ...]
    """User IDs to mention in the message."""

    formatted: object  # FormattedMessage, but can't use pydantic in Struct
    """Optional FormattedMessage for rich content."""

    reply_to_id: str
    """Message ID this is replying to."""

    @classmethod
    def from_chatom_message(cls, msg: ChatomMessage, backend: str) -> "BotMessage":
        """Create a BotMessage from a chatom Message.

        Args:
            msg: The chatom Message object.
            backend: The backend platform name.

        Returns:
            A new BotMessage instance.
        """
        return cls(
            content=msg.content or "",
            channel_id=msg.channel_id or "",
            channel_name=msg.channel.name if msg.channel else "",
            thread_id=msg.thread_id or "",
            backend=backend,
            mentions=tuple(msg.mention_ids) if msg.mention_ids else (),
            formatted=None,
            reply_to_id=msg.reply_to_id or "",
        )

    def to_chatom_message(self) -> ChatomMessage:
        """Convert to a chatom Message.

        Returns:
            A chatom Message instance.
        """
        return ChatomMessage(
            content=self.content,
            channel_id=self.channel_id,
            channel=Channel(id=self.channel_id, name=self.channel_name) if self.channel_id else None,
            thread_id=self.thread_id,
            mention_ids=list(self.mentions) if self.mentions else [],
            reply_to_id=self.reply_to_id,
        )


class BotCommand(GatewayStruct):
    """A bot command parsed from an incoming message.

    This structure captures all the information needed to
    execute a bot command and generate a response.
    """

    command: str
    """The command name (without leading /)."""

    args: tuple[str, ...]
    """Command arguments as parsed tokens."""

    source: User
    """The user who issued the command."""

    targets: tuple[User, ...]
    """Users mentioned/tagged in the command."""

    channel_id: str
    """Channel ID where the command was issued."""

    channel_name: str
    """Channel name where the command was issued."""

    backend: str
    """Backend platform (slack, symphony, discord)."""

    variant: CommandVariant
    """The command response variant."""

    message: ChatomMessage
    """The original chatom Message."""

    delay: datetime | None
    """Scheduled execution time (for delayed commands)."""

    schedule: str
    """Cron expression for recurring commands."""

    schedule_id: str = ""
    """Stable ID for delayed or scheduled command records."""

    prepare_on_run: bool = False
    """Defer the inner command's preparation until its scheduled occurrence."""

    burst_checked: bool = False
    """Preparation already reserved this invocation's burst-limit slot."""

    execution_error: str | None = None
    """A blocked invocation's error; an empty string suppresses repeated reports."""

    times_run: int
    """Number of times this command has run."""

    @property
    def channel(self) -> Channel:
        """Get the channel as a chatom Channel object."""
        return Channel(id=self.channel_id, name=self.channel_name)

    @property
    def original_message(self) -> ChatomMessage:
        """Get the original chatom Message."""
        return self.message


# Exclude from gateway struct lookup to avoid conflicts
BotCommand.omit_from_lookup()
BotMessage.omit_from_lookup()
