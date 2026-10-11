"""Base command classes for csp-bot.

This module provides the abstract base classes for bot commands.
Commands can leverage chatom's cross-platform features.
"""

from abc import ABC, abstractmethod
from typing import Union

from ccflow import BaseModel
from chatom import Message

from csp_bot.structs import Backend, BotCommand, BotMessage, CommandVariant


class BaseCommand(ABC):
    """Abstract base class for bot commands.

    Commands should implement the abstract methods to define
    their behavior and response type.
    """

    @staticmethod
    @abstractmethod
    def kind() -> CommandVariant:
        """Return the command's response variant."""
        ...

    @staticmethod
    def backends() -> list[Backend]:
        """Return supported backends. Empty means all backends."""
        return []

    @abstractmethod
    def command(self) -> str:
        """Return the command signature (e.g., 'help' for /help)."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Return the human-readable command name."""
        ...

    @abstractmethod
    def help(self) -> str:
        """Return the help text for the command."""
        ...

    def usage(self) -> str:
        """Return optional command syntax, without a label."""
        return ""

    @abstractmethod
    def num_recipients(self) -> int:
        """Return how many users this command can tag.

        Returns:
            0: No tagging
            1: Single target
            -1: Any number of tags
        """
        ...

    def preexecute(self, command: BotCommand) -> BotCommand:
        """Pre-execution hook for command modification."""
        return command

    @abstractmethod
    def execute(
        self,
        command: BotCommand,
    ) -> Union[Message, BotMessage, list[Message], list[BotMessage], "BaseCommand", list["BaseCommand"], None]:
        """Execute the command and return response(s).

        Commands can return:
        - Message or BotMessage: Sent to the user
        - BaseCommand: Queued for next cycle
        - List of above: Multiple items
        - None: No response
        """
        return None


class NoResponseCommand(BaseCommand):
    """Command that produces no response."""

    @staticmethod
    def kind() -> CommandVariant:
        return CommandVariant.NO_RESPONSE

    def num_recipients(self) -> int:
        return 0


class ReplyCommand(BaseCommand):
    """Command that replies in the channel."""

    @staticmethod
    def kind() -> CommandVariant:
        return CommandVariant.REPLY

    def num_recipients(self) -> int:
        return 0


class ReplyToAuthorCommand(BaseCommand):
    """Command that replies mentioning the author."""

    @staticmethod
    def kind() -> CommandVariant:
        return CommandVariant.REPLY_TO_AUTHOR

    def num_recipients(self) -> int:
        return 1


class ReplyToOtherCommand(BaseCommand):
    """Command that replies mentioning a tagged user."""

    @staticmethod
    def kind() -> CommandVariant:
        return CommandVariant.REPLY_TO_OTHER

    def num_recipients(self) -> int:
        return 1


class ReplyToAllCommand(BaseCommand):
    """Command that replies mentioning all tagged users."""

    @staticmethod
    def kind() -> CommandVariant:
        return CommandVariant.REPLY_TO_ALL

    def num_recipients(self) -> int:
        return -1


class BaseCommandModel(BaseModel):
    """Model for registering commands via configuration."""

    command: type[BaseCommand]

    def create_command(self) -> BaseCommand:
        """Create the configured command."""
        return self.command()
