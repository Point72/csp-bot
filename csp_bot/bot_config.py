"""Bot configuration using chatom backend configurations.

This module provides configuration classes that wrap chatom's
backend-specific configurations with bot-specific settings.
"""

from ccflow import BaseModel
from pydantic import Field

from .backends import (
    DiscordConfig as ChatomDiscordConfig,
    IRCConfig as ChatomIRCConfig,
    MatrixConfig as ChatomMatrixConfig,
    SlackConfig as ChatomSlackConfig,
    SymphonyConfig as ChatomSymphonyConfig,
    TelegramConfig as ChatomTelegramConfig,
    ZulipConfig as ChatomZulipConfig,
)

__all__ = (
    "BackendConfig",
    "BotConfig",
    "DiscordConfig",
    "IRCConfig",
    "ManagementConfig",
    "MatrixConfig",
    "SlackConfig",
    "SymphonyConfig",
    "TelegramConfig",
    "ZulipConfig",
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


class ZulipConfig(BackendConfig):
    """Zulip bot configuration."""

    config: ChatomZulipConfig = Field(
        default_factory=ChatomZulipConfig,
        description="Chatom Zulip configuration.",
    )


class IRCConfig(BackendConfig):
    """IRC bot configuration."""

    config: ChatomIRCConfig = Field(
        default_factory=ChatomIRCConfig,
        description="Chatom IRC configuration.",
    )


class MatrixConfig(BackendConfig):
    """Matrix bot configuration."""

    config: ChatomMatrixConfig = Field(
        default_factory=ChatomMatrixConfig,
        description="Chatom Matrix configuration.",
    )


class ManagementConfig(BaseModel):
    """Explicit backend-scoped administrators and log access."""

    admin_users: dict[str, set[str]] = Field(default_factory=dict, description="Backend name to administrator user IDs. Empty denies access.")
    log_channels: dict[str, set[str]] = Field(default_factory=dict, description="Backend name to channel IDs permitted to receive logs.")
    message_channels: dict[str, set[str]] = Field(
        default_factory=dict, description="Backend name to channels permitted for cross-room message management."
    )
    log_file: str | None = Field(default=None, description="Operator-configured bot log file. None disables log retrieval.")

    def is_admin(self, backend: str, user_id: str) -> bool:
        return bool(user_id) and user_id in self.admin_users.get(backend, set())


class BotConfig(BaseModel):
    """Main bot configuration.

    Configure which backends the bot should connect to and
    their respective settings.
    """

    discord: DiscordConfig | None = None
    slack: SlackConfig | None = None
    symphony: SymphonyConfig | None = None
    telegram: TelegramConfig | None = None
    zulip: ZulipConfig | None = None
    irc: IRCConfig | None = None
    matrix: MatrixConfig | None = None
    management: ManagementConfig = Field(default_factory=ManagementConfig)

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
