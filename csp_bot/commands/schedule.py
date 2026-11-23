"""Explicit delayed and recurring command scheduling."""

import re
from datetime import UTC, datetime, timedelta
from shlex import split
from typing import TYPE_CHECKING

from chatom import Message
from chatom.format import Bold, FormattedMessage, Table, Text
from croniter import croniter

from csp_bot.structs import BotCommand

from .base import BaseCommand, BaseCommandModel, ReplyCommand

if TYPE_CHECKING:
    from csp_bot import Bot


class ScheduleCommand(ReplyCommand):
    raw_args = True
    _bot: "Bot"

    def command(self) -> str:
        return "schedule"

    def name(self) -> str:
        return "Schedule"

    def help(self) -> str:
        return "Schedule a command using five-field UTC cron, or list/remove your schedules. Separate the inner command with --."

    def usage(self) -> str:
        return '/schedule [list | remove <id> | add --cron "<cron>" -- /<command> [args]]'

    def execute(self, command: BotCommand, *, now: datetime | None = None) -> Message | list[BotCommand | Message]:
        schedule = self._bot._schedule_store
        now = now or datetime.now(UTC)
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        message = Message(channel=command.channel, backend=command.backend, metadata={"backend": command.backend})
        text = command.args[0] if command.args else "list"
        try:
            parts = re.split(r"\s+--(?:\s+|$)", text, maxsplit=1)
            header = split(parts[0])
            if command.command == "schedule" and header == ["list"] and len(parts) == 1:
                records = [
                    record
                    for record in schedule.records()
                    if record.command.backend == command.backend
                    and (record.command.source.id == command.source.id or self._bot.is_management_admin(command.backend, command.source.id))
                ]
                rows = [{"ID": record.schedule_id, "Command": f"/{record.command.command}"} for record in records]
                formatted = FormattedMessage(
                    content=[Bold(child=Text(content="Scheduled Commands")), Table.from_dict_list(rows, columns=["ID", "Command"])]
                )
                message.content = formatted.render_for(command.backend)
                return message
            if command.command == "schedule" and len(header) == 2 and header[0] == "remove" and len(parts) == 1:
                record = schedule.get(header[1])
                if (
                    record is None
                    or record.command.backend != command.backend
                    or (record.command.source.id != command.source.id and not self._bot.is_management_admin(command.backend, command.source.id))
                ):
                    raise ValueError("Unknown schedule or permission denied")
                self._bot._remove_scheduled_command(header[1])
                message.content = f"Removed schedule {header[1]}."
                return message
            if len(parts) != 2 or not parts[1]:
                raise ValueError("Use -- before the command to run")
            cron = ""
            if command.command == "delay":
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)", header[0]) if len(header) == 1 else None
                if match is None:
                    raise ValueError("Use a positive duration such as 20m, 30s, or 1h")
                duration = timedelta(seconds=float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]])
                if duration <= timedelta():
                    raise ValueError("Delay must be positive")
                due = now + duration
            else:
                if len(header) != 3 or header[:2] != ["add", "--cron"] or len(header[2].split()) != 5:
                    raise ValueError("Use add --cron followed by one quoted five-field cron expression")
                cron = header[2]
                due = croniter(cron, now).get_next(datetime)
            inner_name = parts[1].split(maxsplit=1)[0]
            if not inner_name.startswith(("/", "!")) or inner_name.lstrip("/!").lower() not in self._bot._commands:
                raise ValueError("Unknown inner command")
            runner = self._bot._commands[inner_name.lstrip("/!").lower()]
            if isinstance(runner, ScheduleCommand):
                raise TypeError("Nested scheduling commands are not supported")
            if getattr(runner, "admin", False) and not self._bot.is_management_admin(command.backend, command.source.id):
                raise ValueError("Admin access required")
            origin = command.message.channel_id if command.message and command.message.channel_id else command.channel_id
            mentions = list(command.message.mentions) if command.message else list(command.targets)
            inner = self._bot._extract_commands(command.message, command.backend, origin, parts[1], mentions, prepare=False)
            if not isinstance(inner, BotCommand):
                raise TypeError("The inner command is unavailable on this backend")
            if getattr(runner, "admin", False) and inner.channel_id != origin:
                raise ValueError("Management commands cannot redirect to another channel")
            inner.schedule = cron
            inner.delay = due if not cron else None
            inner.prepare_on_run = True
            record = schedule.put(inner, next_run_at=due)
            message.content = f"Scheduled /{inner.command}. ID: {record.schedule_id}. Next run: {due.isoformat()} (UTC)."
            return [inner, message]
        except (ValueError, TypeError, OverflowError) as exc:
            message.content = f"Cannot schedule: {exc}. Usage: {self.usage()}"
            return message


class DelayCommand(ScheduleCommand):
    def command(self) -> str:
        return "delay"

    def name(self) -> str:
        return "Delay"

    def help(self) -> str:
        return "Run a command once after a positive duration. Separate the inner command with --."

    def usage(self) -> str:
        return "/delay <duration: ms|s|m|h|d> -- /<command> [args]"


class ScheduleCommandModel(BaseCommandModel):
    command: type[BaseCommand] = ScheduleCommand


class DelayCommandModel(BaseCommandModel):
    command: type[BaseCommand] = DelayCommand
