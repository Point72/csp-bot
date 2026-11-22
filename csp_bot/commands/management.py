"""Identity, logs, and management of recent bot messages."""

import asyncio
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from chatom import Channel, Message
from chatom.base.capabilities import Capability
from chatom.format import CodeBlock, FormattedMessage, Text

from csp_bot.structs import BotCommand

from .base import BaseCommand, BaseCommandModel, ReplyCommand

if TYPE_CHECKING:
    from chatom.backend import BackendBase

    from csp_bot.bot import Bot
    from csp_bot.sent_messages import SentMessage

log = getLogger(__name__)


class ManagementCommand(ReplyCommand):
    """Management commands receive their owning bot during registration."""

    _bot: "Bot"


class AdminCommand(ManagementCommand):
    """Marker for commands requiring a configured management administrator."""


class WhoamiCommand(ManagementCommand):
    def command(self) -> str:
        return "whoami"

    def name(self) -> str:
        return "Who am I"

    def help(self) -> str:
        return "Show your backend, user ID, channel ID, and management role."

    def usage(self) -> str:
        return "/whoami"

    def execute(self, command: BotCommand) -> Message:
        role = "admin" if self._bot.config.management.is_admin(command.backend, command.source.id) else "user"
        origin = command.message.channel_id if command.message and command.message.channel_id else command.channel_id
        content = f"Backend: {command.backend}\nUser ID: {command.source.id}\nChannel ID: {origin}\nRole: {role}"
        return Message(content=content, channel=command.channel, backend=command.backend)


class LogsCommand(AdminCommand):
    def command(self) -> str:
        return "logs"

    def name(self) -> str:
        return "Logs"

    def help(self) -> str:
        return "Show recent bot logs in a configured log channel. Admin only."

    def usage(self) -> str:
        return "/logs [lines: 1-200]"

    def execute(self, command: BotCommand) -> Message:
        config = self._bot.config.management
        if command.channel_id not in config.log_channels.get(command.backend, set()):
            content = "Use a configured log channel to retrieve logs."
        elif not config.log_file:
            content = "Bot log retrieval is not configured."
        else:
            try:
                lines = int(command.args[0]) if command.args else 50
                if len(command.args) > 1 or not 1 <= lines <= 200:
                    raise ValueError
            except ValueError:
                content = f"Usage: {self.usage()}"
            else:
                try:
                    with Path(config.log_file).open("rb") as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell() - 65_536))
                        tail = "\n".join(stream.read(65_536).decode("utf-8", errors="replace").splitlines()[-lines:])
                    if len(tail) > 3000:
                        tail = "[truncated]\n" + tail[-3000:]
                    content = FormattedMessage(content=[CodeBlock(content=tail or "Log file is empty.")]).render_for(command.backend)
                except OSError:
                    log.exception("Cannot read configured bot log file")
                    content = "Bot log file is unavailable."
        return Message(content=content, channel=command.channel, backend=command.backend)


class MessagesCommand(AdminCommand):
    def command(self) -> str:
        return "messages"

    def name(self) -> str:
        return "Messages"

    def help(self) -> str:
        return "Browse recent bot messages across rooms, then edit or delete by catalog ID. Admin only."

    def usage(self) -> str:
        return "/messages [list [before-id] | edit <id> <text> | delete <id>]"

    def execute(self, command: BotCommand) -> Message:
        content = f"Usage: {self.usage()}"
        args = command.args or ("list",)
        if command.channel_id not in self._bot.config.management.message_channels.get(command.backend, set()):
            content = "Use a configured message management channel."
        elif args[0] == "list" and len(args) <= 2:
            try:
                records, cursor = self._bot._sent_messages.page(command.backend, args[1] if len(args) == 2 else "")
                lines = [f"Recent bot messages ({command.backend}), newest first:"]
                for record in records:
                    room = f"{record.channel_name} ({record.channel_id})" if record.channel_name else record.channel_id
                    status = "deleted" if record.deleted else "sent"
                    lines.extend(
                        [
                            f"ID: {record.id} | {record.posted_at.isoformat(timespec='seconds')} | {status}",
                            f"Room: {room} | Native ID: {record.native_id}",
                            record.preview,
                            "",
                        ]
                    )
                if not records:
                    lines.append("No recorded messages on this page.")
                if cursor:
                    lines.append(f"Next: /messages list {cursor}")
                lines.append("Actions: /messages edit <ID> <text> | /messages delete <ID>")
                content = FormattedMessage(content=[CodeBlock(content="\n".join(lines))]).render_for(command.backend)
            except ValueError as exc:
                content = str(exc)
        elif (args[0] == "delete" and len(args) == 2) or (args[0] == "edit" and len(args) >= 3 and " ".join(args[2:]).strip()):
            record = self._bot._sent_messages.get(args[1], command.backend)
            if record is None:
                return Message(content="Unknown message ID for this backend. Use /messages list.", channel=command.channel, backend=command.backend)
            if record.deleted:
                return Message(content="That message is already marked deleted.", channel=command.channel, backend=command.backend)
            connection = self._bot._ensure_backend_connected(command.backend)
            if connection is None:
                content = "Backend is unavailable."
            else:
                backend, loop = connection
                text = " ".join(args[2:])
                log.info(
                    "Message action=%s backend=%s user=%s channel=%s message=%s",
                    args[0],
                    command.backend,
                    command.source.id,
                    record.channel_id,
                    record.native_id,
                )
                operation = asyncio.wait_for(self._change(backend, record, args[0], text), timeout=30)
                try:
                    if loop.is_running():
                        content = asyncio.run_coroutine_threadsafe(operation, loop).result()
                    else:
                        content = loop.run_until_complete(operation)
                except Exception:
                    log.exception("Message action failed backend=%s channel=%s message=%s", command.backend, record.channel_id, record.native_id)
                    content = "Message action failed. Check bot logs for the backend error."
        return Message(content=content, channel=command.channel, backend=command.backend)

    async def _change(self, backend: "BackendBase", record: "SentMessage", action: str, text: str) -> str:
        capability = Capability.DELETING if action == "delete" else Capability.EDITING
        if not backend.capabilities or not backend.capabilities.supports(capability):
            return f"This backend does not support {action} for messages."
        channel = Channel(id=record.channel_id)
        if action == "delete":
            await backend.delete_message(message=record.native_id, channel=channel)
            native_id = record.native_id
        else:
            rendered = FormattedMessage(content=[Text(content=text)]).render_for(record.backend)
            edited = await backend.edit_message(message=record.native_id, content=rendered, channel=channel)
            native_id = edited.id or record.native_id
        try:
            self._bot._sent_messages.update(record, content=text if action == "edit" else None, native_id=native_id, deleted=action == "delete")
        except Exception:
            log.exception("Backend action succeeded but catalog update failed for %s", record.id)
            return f"Message {action} succeeded, but the local catalog could not be updated."
        return f"{'Deleted' if action == 'delete' else 'Edited'} message {record.id} in {record.channel_id}."


class WhoamiCommandModel(BaseCommandModel):
    command: type[BaseCommand] = WhoamiCommand


class LogsCommandModel(BaseCommandModel):
    command: type[BaseCommand] = LogsCommand


class MessagesCommandModel(BaseCommandModel):
    command: type[BaseCommand] = MessagesCommand
