"""Help command for csp-bot."""

from collections.abc import Mapping
from logging import getLogger
from typing import Any

from chatom import Message
from chatom.format import Bold, Code, FormattedMessage, Heading, LineBreak, ListItem, Span, Table, Text, UnorderedList

from csp_bot.structs import BotCommand

from .base import BaseCommand, BaseCommandModel, ReplyCommand

log = getLogger(__name__)


def _command_backends(runner: Any) -> list:
    """Get backends list from either legacy or new command types."""
    if isinstance(runner, BaseCommand):
        return runner.backends()
    return getattr(runner, "backends", []) or []


def _command_name(runner: Any) -> str:
    if isinstance(runner, BaseCommand):
        return runner.name()
    return getattr(runner, "name", "") or ""


def _command_help(runner: Any) -> str:
    if isinstance(runner, BaseCommand):
        description = runner.help()
        usage = runner.usage()
    else:
        description = getattr(runner, "help", "") or ""
        usage = getattr(runner, "usage", "") or ""
    if not usage:
        return description
    return f"{description} Syntax: {usage}" if description else f"Syntax: {usage}"


def _render_table_help(rows: list, backend: str) -> str:
    msg = FormattedMessage(metadata={"backend": backend})
    msg.content.append(Heading(child=Text(content="Bot Commands Help"), level=3))
    msg.content.append(Table.from_dict_list(rows, columns=["Command", "Name", "Info"]))
    return msg.render_for(backend)


def _render_list_help(rows: list, backend: str) -> str:
    msg = FormattedMessage(metadata={"backend": backend})
    msg.content.append(Bold(child=Text(content="Bot Commands Help")))
    msg.content.append(LineBreak())
    msg.content.append(
        UnorderedList(
            items=[
                ListItem(
                    child=Span(
                        children=[
                            Code(content=row["Command"]),
                            Text(content=" "),
                            Bold(child=Text(content=row["Name"])),
                            Text(content=": "),
                            Text(content=row["Info"]),
                        ]
                    )
                )
                for row in rows
            ]
        )
    )
    return msg.render_for(backend)


def _render_help(rows: list, backend: str) -> str:
    if backend.lower() == "symphony":
        return _render_table_help(rows, backend)
    return _render_list_help(rows, backend)


class HelpCommand(ReplyCommand):
    """Display help for available commands."""

    def command(self) -> str:
        return "help"

    def name(self) -> str:
        return "Help"

    def help(self) -> str:
        return "Get help with bot commands. Syntax: /help [command]"

    def execute(
        self,
        command: BotCommand,
        commands: Mapping[str, Any] | None = None,
    ) -> Message | None:
        log.info(f"Help command: {command.command}")

        # Collect help for each command
        rows = []
        for cmd_key, cmd_inst in commands.items():
            if cmd_key.startswith("_"):
                continue

            backends = _command_backends(cmd_inst)
            if backends and command.backend not in backends:
                continue

            if command.args and cmd_key not in command.args:
                continue

            rows.append(
                {
                    "Command": f"/{cmd_key}",
                    "Name": _command_name(cmd_inst),
                    "Info": _command_help(cmd_inst),
                }
            )

        rows.sort(key=lambda r: r["Command"])

        return Message(
            content=_render_help(rows, command.backend),
            channel=command.channel,
            metadata={"backend": command.backend},
        )


class HelpCommandModel(BaseCommandModel):
    command: type[BaseCommand] = HelpCommand
