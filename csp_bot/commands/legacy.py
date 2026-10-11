"""Legacy adapter for old BaseCommand subclasses.

Wraps existing BaseCommand instances so they can be used in the new
command execution pipeline alongside @command functions and Command
subclasses. This allows incremental migration — old commands keep
working without any changes.
"""

from __future__ import annotations

import logging
from typing import Any

from csp_bot.commands.base import BaseCommand
from csp_bot.commands.context import CommandContext
from csp_bot.structs import BotCommand

log = logging.getLogger(__name__)


class LegacyCommandAdapter:
    """Wraps a BaseCommand so it can be driven by the new framework.

    The adapter:
    - Accepts a CommandContext
    - Converts it to a BotCommand for the legacy execute() call
    - Delegates to the original command's execute() method
    - Passes through extra kwargs for HelpCommand/ScheduleCommand
    """

    __slots__ = ("_command",)

    def __init__(self, command: BaseCommand):
        self._command = command

    @property
    def wrapped(self) -> BaseCommand:
        """The underlying BaseCommand instance."""
        return self._command

    @property
    def name(self) -> str:
        return self._command.command()

    @property
    def help(self) -> str:
        return self._command.help()

    @property
    def usage(self) -> str:
        return self._command.usage()

    @property
    def backends(self) -> list[str]:
        return self._command.backends()

    def context_to_bot_command(self, ctx: CommandContext) -> BotCommand:
        """Convert a CommandContext back to a legacy BotCommand."""
        return BotCommand(
            command=ctx.command_name,
            args=tuple(ctx.args),
            source=ctx.source,
            targets=tuple(ctx.targets),
            channel_id=ctx.channel.id if ctx.channel else "",
            channel_name=ctx.channel.name if ctx.channel else "",
            backend=ctx.backend,
            variant=self._command.kind(),
            message=ctx.message,
            delay=None,
            schedule="",
            times_run=0,
        )

    def execute(self, ctx: CommandContext, **extra_kwargs) -> Any:
        """Execute the legacy command with a CommandContext.

        Converts ctx → BotCommand, calls preexecute, then execute.
        Returns whatever the legacy command returns (Message, BotMessage, etc.)
        """
        bot_cmd = self.context_to_bot_command(ctx)

        # Legacy preexecute
        bot_cmd = self._command.preexecute(bot_cmd)

        # Legacy execute — pass through extra kwargs
        return self._command.execute(bot_cmd, **extra_kwargs)
