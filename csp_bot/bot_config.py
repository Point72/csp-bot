"""Bot configuration using chatom backend configurations.

This module provides configuration classes that wrap chatom's
backend-specific configurations with bot-specific settings.
"""

from ccflow import BaseModel
from pydantic import Field

from .backends import (
    DiscordConfig as ChatomDiscordConfig,
    SlackConfig as ChatomSlackConfig,
    SymphonyConfig as ChatomSymphonyConfig,
    TelegramConfig as ChatomTelegramConfig,
)

__all__ = (
    "BackendConfig",
    "BotConfig",
    "DiscordConfig",
    "SlackConfig",
    "SymphonyConfig",
    "TelegramConfig",
)


class BackendConfig(BaseModel):
    """Base configuration for a bot backend.

    This wraps a chatom backend config with bot-specific settings.
    The bot name is auto-detected from the backend if not provided.
    """

    bot_name: str = Field(
        default="",
        description="Name of the bot. Auto-detected from backend if empty.",
    )

    channels: set[str] = Field(
        default_factory=set,
        description="Channels/rooms to subscribe to. Empty means all.",
    )

    user_access_channels: list[str] = Field(
        default_factory=list,
        description="If non-empty, only users from these channels can interact with the bot.",
    )

    query_user_access_seconds: int = Field(
        default=300,
        description="How frequently to query user access channels. 0 means only at startup.",
    )

    unauthorized_msg: str | None = Field(
        default="You are not authorized to interact with this bot.",
        description="Message to send when unauthorized user interacts. None means no message.",
    )


class DiscordConfig(BackendConfig):
    """Discord bot configuration."""

    config: ChatomDiscordConfig = Field(
        default_factory=ChatomDiscordConfig,
        description="Chatom Discord configuration.",
    )


class SlackConfig(BackendConfig):
    """Slack bot configuration."""

    config: ChatomSlackConfig = Field(
        default_factory=ChatomSlackConfig,
        description="Chatom Slack configuration.",
    )


class SymphonyConfig(BackendConfig):
    """Symphony bot configuration."""

    config: ChatomSymphonyConfig = Field(
        default_factory=ChatomSymphonyConfig,
        description="Chatom Symphony configuration.",
    )

    set_presence_seconds: int = Field(
        default=0,
        description="Seconds between presence updates. 0 means never.",
    )


class TelegramConfig(BackendConfig):
    """Telegram bot configuration."""

    config: ChatomTelegramConfig = Field(
        default_factory=ChatomTelegramConfig,
        description="Chatom Telegram configuration.",
    )


class BotConfig(BaseModel):
    """Main bot configuration.

    Configure which backends the bot should connect to and
    their respective settings.
    """

    discord: DiscordConfig | None = None
    slack: SlackConfig | None = None
    symphony: SymphonyConfig | None = None
    telegram: TelegramConfig | None = None

    ratelimit_seconds: float = Field(
        default=1.0,
        description="Minimum seconds between message outputs.",
    )

    allow_send_messages: bool = Field(
        default=False,
        description=(
            "Expose messages_out as a send channel, so an authenticated caller can POST a message "
            "to /api/v1/send/messages_out and have it delivered to the backend named on the message. "
            "Off by default: it turns the bot into a relay for anything that can reach the API, so it "
            "should be enabled deliberately and only with authentication configured."
        ),
    )
