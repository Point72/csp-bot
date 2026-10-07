"""CSP Bot using chatom for unified chat platform support.

This module provides the Bot class that leverages chatom's unified
interface for working with multiple chat platforms (Slack, Symphony, Discord).

Key features enabled by chatom:
- Unified Message, User, and Channel models
- Cross-platform mention generation
- Backend-specific message formatting
- Entity recognition and parsing
"""

import asyncio
import html
import importlib.metadata as importlib_metadata
import re
import threading
import time
from csv import reader
from datetime import datetime, timedelta
from io import StringIO
from logging import getLogger
from types import MappingProxyType
from typing import Any, ClassVar

import csp
from chatom import Channel, Message, User, mention_user_for_backend
from chatom.base import parse_mentions
from croniter import croniter
from csp import Outputs, ts
from pydantic import PrivateAttr

from .backends import (
    DiscordAdapter,
    SlackAdapter,
    SymphonyAdapter,
    SymphonyPresenceStatus,
    TelegramAdapter,
)
from .bot_config import BotConfig
from .commands import (
    BaseCommand,
    BotInfo,
    Command,
    CommandContext,
    HelpCommand,
    ScheduleCommand,
    StatusCommand,
    execute_command_func,
    get_registered_commands,
)
from .gateway import GatewayChannels, GatewayModule
from .persistence import InMemoryStateStore, ScheduledCommandRecord, ScheduleStore, StateStore
from .structs import (
    Backend,
    BotCommand,
    BotMessage,
    CommandVariant,
)

log = getLogger(__name__)

__all__ = ("Bot",)


class Bot(GatewayModule):
    """CSP Bot module using chatom for multi-platform support.

    This bot leverages chatom's unified interface to work seamlessly
    across Slack, Symphony, and Discord.

    Features:
        - Unified message handling via chatom Message type
        - Cross-platform user mentions via chatom
        - Entity recognition using chatom's mention parsing
        - Backend-specific message formatting
    """

    config: BotConfig

    _command_models: list[Any] = PrivateAttr(default_factory=list)
    _commands: dict[str, Any] = PrivateAttr(default_factory=dict)
    _configs: dict[Backend, Any] = PrivateAttr(default_factory=dict)
    _adapters: dict[Backend, Any] = PrivateAttr(default_factory=dict)
    _connected_backends: dict[Backend, tuple[Any, asyncio.AbstractEventLoop]] = PrivateAttr(default_factory=dict)
    _schedule_store: ScheduleStore = PrivateAttr(default_factory=lambda: ScheduleStore(InMemoryStateStore()))
    _authorized_users: dict[Backend, set[str]] = PrivateAttr(default_factory=dict)
    _bot_user_ids: dict[Backend, str] = PrivateAttr(default_factory=dict)
    _bot_names: dict[Backend, str] = PrivateAttr(default_factory=dict)
    _deps: Any = PrivateAttr(default=None)
    _thread: threading.Thread | None = PrivateAttr(None)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    _KNOWN_BACKENDS: ClassVar[set[str]] = {"discord", "slack", "symphony", "telegram"}

    @staticmethod
    def _datetime_for_now(value: datetime | None, now: datetime) -> datetime | None:
        # Persistence records use aware UTC datetimes. csp.now() is naive in
        # current runtime tests, so normalize only at the csp scheduling edge.
        if value is None:
            return None
        if value.tzinfo is not None and now.tzinfo is None:
            return value.replace(tzinfo=None)
        if value.tzinfo is None and now.tzinfo is not None:
            return value.replace(tzinfo=now.tzinfo)
        return value

    def set_state_store(self, state_store: StateStore) -> None:
        """Inject the state store used for bot runtime persistence."""
        self.set_schedule_store(ScheduleStore(state_store))

    def set_schedule_store(self, schedule_store: ScheduleStore) -> None:
        """Inject a schedule store for delayed and recurring commands."""
        self._schedule_store = schedule_store

    def _restore_scheduled_commands(self, now: datetime) -> list[ScheduledCommandRecord]:
        """Return future scheduled commands that should be re-armed."""
        restored = []
        for record in self._schedule_store.records():
            next_run_at = self._datetime_for_now(record.next_run_at, now)
            if next_run_at and next_run_at >= now:
                restored.append(record)
        return restored

    def _store_scheduled_command(self, cmd: BotCommand, next_run_at: datetime) -> ScheduledCommandRecord:
        return self._schedule_store.put(cmd, schedule_id=cmd.schedule_id or None, next_run_at=next_run_at)

    def _remove_scheduled_command(self, schedule_id: str) -> bool:
        return self._schedule_store.remove(schedule_id)

    def set_deps(self, deps: Any) -> None:
        """Set shared dependency object for new command framework contexts."""
        self._deps = deps

    def connect(self, channels: GatewayChannels) -> None:
        """Connect to configured backends and set up message processing.

        Uses chatom's unified adapters for each backend.
        """
        # Initialize Discord
        if self.config.discord:
            if DiscordAdapter is None:
                raise ImportError("Discord adapter not installed. Please install csp-adapter-discord.")
            self._configs["discord"] = self.config.discord
            self._adapters["discord"] = DiscordAdapter(self.config.discord.config)

        # Initialize Slack
        if self.config.slack:
            if SlackAdapter is None:
                raise ImportError("Slack adapter not installed. Please install csp-adapter-slack.")
            self._configs["slack"] = self.config.slack
            self._adapters["slack"] = SlackAdapter(self.config.slack.config)

        # Initialize Symphony
        if self.config.symphony:
            if SymphonyAdapter is None:
                raise ImportError("Symphony adapter not installed. Please install csp-adapter-symphony.")
            self._configs["symphony"] = self.config.symphony
            self._adapters["symphony"] = SymphonyAdapter(self.config.symphony.config)

        # Initialize Telegram
        if self.config.telegram:
            if TelegramAdapter is None:
                raise ImportError("Telegram adapter not installed. Please install csp-adapter-telegram.")
            self._configs["telegram"] = self.config.telegram
            self._adapters["telegram"] = TelegramAdapter(self.config.telegram.config)

        # Fetch bot info for all backends at startup
        for backend in self._adapters:
            log.info(f"Fetching bot info for {backend}...")
            self._fetch_bot_info(backend)

        # Inject backends into AgentCommand subclasses
        self._inject_backends_into_agent_commands()

        # Subscribe to messages from all adapters
        # chatom provides unified Message type across all backends
        messages_in = csp.null_ts(Message)
        for backend, adapter in self._adapters.items():
            config = self._configs[backend]
            raw_msgs = adapter.subscribe(
                channels=config.channels if config.channels else None,
                skip_own=True,
                skip_history=True,
            )
            # Unroll the list and convert to individual messages
            unrolled = csp.unroll(raw_msgs)
            # Tag messages with their backend
            tagged = self._tag_message_backend(backend, unrolled)
            messages_in = csp.flatten([messages_in, tagged])

        # Set input channel
        channels.set_channel(GatewayChannels.messages_in, messages_in)

        # Process messages to extract commands
        command_outputs = self._process_incoming_messages(messages_in)
        bot_commands = csp.unroll(command_outputs.bot_commands)
        channels.set_channel(GatewayChannels.commands, bot_commands)

        # Handle commands and generate responses
        response_outputs = self._handle_commands(channels.get_channel(GatewayChannels.commands))
        messages_out = csp.flatten(
            [
                csp.unroll(response_outputs.messages),
                command_outputs.unauthorized_message,
            ]
        )

        channels.set_channel(GatewayChannels.messages_out, messages_out)

        # Loop back secondary commands
        secondary_commands = csp.unroll(response_outputs.commands)
        channels.set_channel(GatewayChannels.commands, secondary_commands)

        if self.config.allow_send_messages:
            # Lets a caller POST to /api/v1/send/messages_out. The gateway wires
            # the send adapter into the channel after every module has
            # connected, so the merged edge below picks those messages up too.
            channels.add_send_channel(GatewayChannels.messages_out)

        # Publish responses to adapters
        # chatom handles conversion to backend-specific formats
        #
        # Read the channel rather than the edge built above, so anything else
        # feeding messages_out, including the send adapter, reaches a backend.
        # This is not a cycle: subscribe() is a source and publish() a sink, so
        # nothing here flows back into what composes the channel.
        outbound = channels.get_channel(GatewayChannels.messages_out)
        for backend, adapter in self._adapters.items():
            backend_messages = self._filter_messages_for_backend(backend, outbound)
            adapter.publish(backend_messages)

        # Set up presence updates for Symphony
        if self.config.symphony and self.config.symphony.set_presence_seconds:
            presence = csp.timer(
                timedelta(seconds=self.config.symphony.set_presence_seconds),
                SymphonyPresenceStatus.AVAILABLE,
            )
            self._adapters["symphony"].publish_presence(presence)

        # Set up user access queries
        for backend in ["symphony", "slack", "discord", "telegram"]:
            config = self._configs.get(backend)
            if config and config.user_access_channels:
                self._update_user_access(backend)
                if config.query_user_access_seconds:
                    self._thread = threading.Thread(
                        target=self._update_user_access_loop,
                        args=(backend,),
                        daemon=True,
                    )
                    self._thread.start()

    @csp.node
    def _tag_message_backend(self, backend: str, msg: ts[Message]) -> ts[Message]:
        """Tag a message with its backend."""
        if csp.ticked(msg):
            # Ensure metadata exists
            if msg.metadata is None:
                msg.metadata = {}
            # Set backend in metadata if not already set
            if not msg.metadata.get("backend"):
                msg.metadata["backend"] = backend
            return msg

    @csp.node
    def _filter_messages_for_backend(self, backend: str, msg: ts[Message]) -> ts[Message]:
        """Filter messages for a specific backend.

        Messages the bot routes internally are tagged in metadata, but
        chatom's Message carries a `backend` field of its own, and that is what
        a caller sending one over REST naturally sets. Either names the
        destination, with metadata taking precedence since that is what the
        inbound tagging writes.
        """
        if csp.ticked(msg):
            metadata = msg.metadata or {}
            msg_backend = metadata.get("backend") or msg.backend or ""
            if msg_backend == backend:
                return msg

    def _update_user_access(self, backend: str) -> None:
        """Update authorized users from access channels."""
        config = self._configs.get(backend)
        if not config or not config.user_access_channels:
            return

        adapter = self._adapters.get(backend)
        if not adapter:
            return

        users: set[str] = set()
        for channel_name in config.user_access_channels:
            try:
                # Use chatom's backend to fetch channel members
                members = adapter.backend.sync.fetch_channel_members(channel_name)
                users.update(m.id for m in members if m.id)
            except Exception:
                log.exception(f"Error fetching members from {channel_name}")

        with self._lock:
            self._authorized_users[backend] = users

    def _update_user_access_loop(self, backend: str) -> None:
        """Background loop to update user access."""
        config = self._configs.get(backend)
        if not config:
            return

        while True:
            time.sleep(config.query_user_access_seconds)
            try:
                self._update_user_access(backend)
            except Exception:
                log.exception(f"Error updating user access for {backend}")

    def _inject_backends_into_agent_commands(self) -> None:
        """Inject connected BackendBase instances into AgentCommand subclasses."""
        try:
            from csp_bot.commands.agent import AgentCommand
        except ImportError:
            # pydantic-ai / chatom[agent] not installed — skip silently
            return

        backends = {}
        loops = {}
        for name in self._adapters:
            result = self._ensure_backend_connected(name)
            if result:
                connected_backend, loop = result
                backends[name] = connected_backend
                loops[name] = loop
        if backends:
            AgentCommand.set_backends(backends, loops=loops)
            log.info(f"Injected {len(backends)} connected backends into AgentCommand: {list(backends.keys())}")

    def _track_agent_session_response(self, response: Message, command: BotCommand) -> None:
        """Associate a sent response with an agent session for reply tracking.

        Uses the original command's message ID as the key that future replies
        will reference (e.g., thread_ts in Slack, or message reference in Discord).
        """
        metadata = response.metadata or {}
        session_key = metadata.get("agent_session_key")
        if not session_key:
            return

        try:
            from csp_bot.commands.agent import AgentCommand
        except ImportError:
            return

        # Use the original command's message ID — in Slack threads, replies
        # reference this as thread_ts; in Discord, as message_reference.
        orig_msg_id = command.message.id if command.message else None
        if orig_msg_id:
            AgentCommand._sessions.update_response_id(session_key, orig_msg_id)
            log.debug(f"Tracked agent session response: session={session_key}, msg_id={orig_msg_id}")

        # Also track by the response message ID if it has one
        if response.id and response.id != orig_msg_id:
            AgentCommand._sessions.update_response_id(session_key, response.id)

    def _ensure_backend_connected(self, backend: str) -> tuple[Any, asyncio.AbstractEventLoop] | None:
        """Ensure a connected backend exists for the given platform.

        Lazily creates and connects a backend instance that can be reused
        for API calls like fetching bot info or resolving channels.

        Args:
            backend: The backend platform name (e.g., "symphony", "slack").

        Returns:
            Tuple of (connected backend instance, event loop), or None if unavailable.
        """
        # Return cached connected backend if available
        if backend in self._connected_backends:
            return self._connected_backends[backend]

        adapter = self._adapters.get(backend)
        if not adapter:
            log.warning(f"No adapter for backend: {backend}")
            return None

        # Create a persistent event loop for this backend
        # Using a dedicated loop avoids "Event loop is closed" errors
        loop = asyncio.new_event_loop()

        # Create and connect a new backend instance
        backend_class = type(adapter.backend)
        new_backend = backend_class(config=adapter.backend.config)

        async def _connect():
            await new_backend.connect()
            return new_backend

        try:
            loop.run_until_complete(_connect())
            self._connected_backends[backend] = (new_backend, loop)
            log.info(f"Created connected backend for {backend}")
            return (new_backend, loop)
        except Exception:
            log.exception(f"Failed to create connected backend for {backend}")
            loop.close()
            return None

    def _resolve_channel(self, channel_identifier: str, backend: str) -> Channel | None:
        """Resolve a channel name or ID to a Channel object.

        Uses the shared connected backend for the platform.

        Args:
            channel_identifier: Channel name or ID to resolve.
            backend: The backend platform.

        Returns:
            The resolved Channel object, or None if not found.
        """
        result = self._ensure_backend_connected(backend)
        if not result:
            return None

        connected_backend, loop = result

        async def _fetch() -> Channel | None:
            log.info(f"Resolving channel '{channel_identifier}' for {backend}")

            # First try to fetch by name
            channel = await connected_backend.fetch_channel(name=channel_identifier)
            if channel:
                log.info(f"Resolved channel by name: {channel.id} ({channel.name})")
                return channel

            # If not found by name, try by ID
            log.info(f"Channel not found by name, trying by ID: {channel_identifier}")
            channel = await connected_backend.fetch_channel(id=channel_identifier)
            if channel:
                log.info(f"Resolved channel by ID: {channel.id} ({channel.name})")
            return channel

        try:
            return loop.run_until_complete(_fetch())
        except Exception:
            log.exception(f"Error resolving channel: {channel_identifier}")
            return None

    def load_commands(self, command_models: list[Any]) -> None:
        """Load command handlers from command models and decorator registry.

        Supports both legacy BaseCommandModel and the new CommandModel.
        """
        log.info(f"Loading {len(command_models)} commands...")
        self._load_entrypoint_commands()
        active_backends = self._active_backends()
        for model in command_models:
            try:
                command = model.create_command()
            except TypeError:
                log.critical(f"Incomplete command type - implement all abstract methods: {model.command}")
                raise

            if isinstance(command, BaseCommand):
                command_str = command.command()
                runner: Any = command
            elif isinstance(command, Command):
                command_str = command.name
                runner = command
            else:
                raise TypeError(f"Unsupported command type from model {type(model).__name__}: {type(command).__name__}")

            if not self._is_command_backend_compatible(command_str, runner, active_backends):
                continue

            log.info(f"Registered command: /{command_str}")
            if command_str in self._commands:
                raise ValueError(f"Command already registered: {command_str}\n\t{command}\n\t{self._commands[command_str]}")

            self._commands[command_str] = runner
            self._command_models.append(model)

        # Decorator-registered commands are available globally and can be mixed
        # with model-based commands. Explicit model definitions win on conflicts.
        for command_name, entry in get_registered_commands().items():
            if command_name in self._commands:
                continue
            if not self._is_command_backend_compatible(command_name, entry, active_backends):
                continue
            log.info(f"Registered decorated command: /{command_name}")
            self._commands[command_name] = entry

    def _load_entrypoint_commands(self) -> None:
        """Load command plugins from Python entry points.

        Entry points in the ``csp_bot.commands`` group are imported so they can
        register commands through decorators or module import side effects.
        If the loaded object is callable, it is invoked with no arguments.
        """
        try:
            try:
                entry_points = importlib_metadata.entry_points(group="csp_bot.commands")
            except TypeError:
                all_entry_points = importlib_metadata.entry_points()
                entry_points = all_entry_points.get("csp_bot.commands", [])
        except Exception:
            log.exception("Failed to discover csp_bot.commands entry points")
            return

        for entry_point in entry_points:
            try:
                loaded = entry_point.load()
            except Exception:
                log.exception("Failed to load command entry point: %s", getattr(entry_point, "name", "<unknown>"))
                continue

            if callable(loaded):
                try:
                    loaded()
                except Exception:
                    log.exception("Failed to initialize command entry point: %s", getattr(entry_point, "name", "<unknown>"))
                    continue

            log.info("Loaded command entry point: %s", getattr(entry_point, "name", "<unknown>"))

    def _active_backends(self) -> set[str]:
        """Return configured backends for this bot instance."""
        active: set[str] = set()
        if self.config.discord:
            active.add("discord")
        if self.config.slack:
            active.add("slack")
        if self.config.symphony:
            active.add("symphony")
        return active

    def _normalize_command_backends(self, command_name: str, backends: list[str]) -> list[str]:
        """Normalize and validate declared command backends."""
        normalized = [b.lower() for b in backends]
        unknown = sorted({b for b in normalized if b not in self._KNOWN_BACKENDS})
        if unknown:
            raise ValueError(f"Command '{command_name}' declared unknown backends: {', '.join(unknown)}")
        return normalized

    def _is_command_backend_compatible(self, command_name: str, command_runner: Any, active_backends: set[str]) -> bool:
        """Check registration-time backend compatibility for a command."""
        declared_backends = self._command_backends(command_runner)
        if not declared_backends:
            return True

        normalized = self._normalize_command_backends(command_name, declared_backends)

        # If no backends are configured yet, keep command registration permissive.
        if not active_backends:
            return True

        if active_backends.intersection(normalized):
            return True

        log.info(
            "Skipping command /%s: declared backends %s do not match active backends %s",
            command_name,
            normalized,
            sorted(active_backends),
        )
        return False

    def _command_backends(self, command_runner: Any) -> list[str]:
        """Return supported backends for either legacy or new command types."""
        if isinstance(command_runner, BaseCommand):
            return command_runner.backends()
        if isinstance(command_runner, Command):
            return command_runner.backends
        return list(getattr(command_runner, "backends", []) or [])

    def _build_command_context(self, cmd: BotCommand) -> CommandContext:
        """Build CommandContext from legacy BotCommand for new framework execution."""
        bot_info = BotInfo(
            id=self._get_bot_id(cmd.backend) or "",
            name=self._get_bot_name(cmd.backend) or "",
            version="",
        )
        channel = Channel(id=cmd.channel_id, name=cmd.channel_name)
        return CommandContext(
            command_name=cmd.command,
            source=cmd.source,
            targets=list(cmd.targets),
            channel=channel,
            message=cmd.message,
            args=list(cmd.args),
            args_text=" ".join(cmd.args),
            backend=cmd.backend,
            bot=bot_info,
            deps=self._deps,
        )

    @csp.node
    def _process_incoming_messages(self, msg: ts[Message]) -> Outputs(bot_commands=ts[[BotCommand]], unauthorized_message=ts[Message]):
        """Process incoming messages to extract bot commands.

        Uses chatom's unified Message type and mention parsing.
        """
        if csp.ticked(msg):
            try:
                backend = msg.metadata.get("backend", "")
                log.info(f"Processing incoming message from {backend}: content={msg.content[:100] if msg.content else ''!r}")
                is_to_bot, channel_id, text, mentions = self._is_message_to_bot(msg, backend)
                log.info(f"is_to_bot={is_to_bot}, channel_id={channel_id}")

                if not is_to_bot:
                    log.info("Ignoring message (not to bot)")
                    return

                if not self._is_authorized(msg, backend):
                    config = self._configs.get(backend)
                    if config and config.unauthorized_msg:
                        response = self._create_response_message(
                            content=config.unauthorized_msg,
                            channel_id=channel_id,
                            backend=backend,
                        )
                        csp.output(unauthorized_message=response)
                    return

                commands = self._extract_commands(msg, backend, channel_id, text, mentions)
                if commands:
                    csp.output(bot_commands=commands if isinstance(commands, list) else [commands])

            except Exception:
                log.exception("Error processing message")

    @csp.node
    def _handle_commands(self, cmd: ts[BotCommand]) -> Outputs(messages=ts[[Message]], commands=ts[[BotCommand]]):
        """Handle bot commands and generate responses.

        Supports delayed and scheduled commands via alarms.
        """
        with csp.alarms():
            a_scheduled: ts[BotCommand] = csp.alarm(BotCommand)
            a_ratelimit: ts[bool] = csp.alarm(bool)

        with csp.state():
            s_buffer: list[Message] = []
            s_buffer_last: list[Message] = []
            s_to_process: list[BotCommand] = []

        with csp.start():
            csp.schedule_alarm(a_ratelimit, timedelta(seconds=self.config.ratelimit_seconds), True)
            now = csp.now()
            for record in self._restore_scheduled_commands(now):
                next_run_at = self._datetime_for_now(record.next_run_at, now)
                if next_run_at:
                    csp.schedule_alarm(a_scheduled, next_run_at, record.command)

        # Handle scheduled command triggers
        if csp.ticked(a_scheduled) and self._schedule_store.get(a_scheduled.schedule_id) is not None:
            # Removed schedules may still have an outstanding CSP alarm; the
            # store is the source of truth and acts as the tombstone check.
            s_to_process.append(a_scheduled)

            # Reschedule recurring commands
            if a_scheduled.schedule:
                now = csp.now()
                next_time = croniter(a_scheduled.schedule, now).get_next(datetime)
                if next_time >= now:
                    self._store_scheduled_command(a_scheduled, next_time)
                    csp.schedule_alarm(a_scheduled, next_time, a_scheduled)
            else:
                self._remove_scheduled_command(a_scheduled.schedule_id)

        # Handle new commands
        if csp.ticked(cmd):
            now = csp.now()
            delay = self._datetime_for_now(cmd.delay, now)

            # Check for delayed execution
            if delay and delay >= now:
                self._store_scheduled_command(cmd, delay)
                csp.schedule_alarm(a_scheduled, delay, cmd)
            # Check for scheduled execution
            elif cmd.schedule:
                next_time = croniter(cmd.schedule, now).get_next(datetime)
                if next_time >= now:
                    self._store_scheduled_command(cmd, next_time)
                    csp.schedule_alarm(a_scheduled, next_time, cmd)
            else:
                s_to_process.append(cmd)

        # Process commands
        if csp.ticked(cmd) or csp.ticked(a_scheduled):
            next_cycle_commands = []

            for command in s_to_process:
                log.debug(f"Executing command: {command.command}")
                result = self._execute_command(command)

                log.debug(f"Command {command.command} execution returned: {result}")
                if result:
                    results = result if isinstance(result, list) else [result]
                    for item in results:
                        log.debug(f"Processing result item type: {type(item).__name__}, isinstance(Message): {isinstance(item, Message)}")
                        if isinstance(item, Message):
                            log.debug(f"Adding message to buffer: {item.content[:100] if item.content else 'empty'}...")
                            s_buffer.append(item)
                            # Track agent session responses for reply continuity
                            self._track_agent_session_response(item, command)
                        elif isinstance(item, BotCommand):
                            next_cycle_commands.append(item)
                else:
                    log.debug(f"Command {command.command} returned no result")

            if next_cycle_commands:
                csp.output(commands=next_cycle_commands)

            s_to_process = []

        # Rate-limited output
        if csp.ticked(a_ratelimit):
            if s_buffer:
                # Deduplicate by message ID (Message is not hashable)
                seen_ids = {m.id for m in s_buffer_last if m.id}
                output = [m for m in s_buffer if m.id not in seen_ids]
                if output:
                    csp.output(messages=output)
                s_buffer_last = s_buffer.copy()
                s_buffer = []

            csp.schedule_alarm(a_ratelimit, timedelta(seconds=self.config.ratelimit_seconds), True)

    def _is_message_to_bot(self, msg: Message, backend: str) -> tuple[bool, str, str, list[User]]:
        """Check if a message is directed at the bot.

        Uses chatom's mention parsing to detect bot mentions.

        Returns:
            Tuple of (is_to_bot, channel_id, text_content, mentioned_users)
        """
        config = self._configs.get(backend)
        if not config:
            return False, "", "", []

        # Get content and channel - extract plain text for Symphony
        raw_content = msg.content or ""
        if backend == "symphony":
            # Symphony sends HTML/MessageML - extract plain text
            from chatom.symphony import SymphonyMessage

            if isinstance(msg, SymphonyMessage):
                content = msg._parse_symphony_content(raw_content)
            else:
                # Manual HTML stripping for non-SymphonyMessage
                content = raw_content
                content = re.sub(r"<br\s*/?>\s*", "\n", content, flags=re.IGNORECASE)
                content = re.sub(r"</p>\s*", "\n", content, flags=re.IGNORECASE)
                content = re.sub(r"</div>\s*", "\n", content, flags=re.IGNORECASE)
                content = re.sub(r"<[^>]+>", "", content)
                content = html.unescape(content).strip()
        else:
            content = raw_content
        metadata = msg.metadata or {}
        channel_id = msg.channel_id or (msg.channel.id if msg.channel else "") or str(metadata.get("channel_id") or "")

        # Get mentioned users from the message directly (already parsed by chatom)
        # This is more reliable than re-parsing from stripped content
        mentioned_users = list(msg.mentions) if msg.mentions else []

        # If no mentions on message, try extracting from Symphony data field
        if not mentioned_users and backend == "symphony":
            from chatom.symphony import SymphonyMessage

            if isinstance(msg, SymphonyMessage) and msg.data:
                mention_ids = SymphonyMessage.extract_mentions_from_data(msg.data)
                for uid in mention_ids:
                    mentioned_users.append(User(id=str(uid), name=""))

        # If still no mentions, try parsing from raw content (before stripping)
        if not mentioned_users:
            mention_matches = parse_mentions(raw_content, backend)
            for match in mention_matches:
                user = User(id=match.user_id, name="")
                mentioned_users.append(user)

        # Get bot ID and name from backend (auto-detected)
        bot_id = self._get_bot_id(backend)
        bot_name = self._get_bot_name(backend)

        # Debug logging
        log.info(
            f"[{backend}] _is_message_to_bot: bot_id={bot_id}, mentions={[u.id for u in mentioned_users]}, msg.data={getattr(msg, 'data', None)}"
        )

        # Check if this is a DM (always to bot)
        is_dm = self._is_direct_message(msg, backend)
        log.debug(f"[{backend}] is_dm={is_dm}")

        if is_dm:
            # In DMs, accept if author is not the bot
            author_id = msg.author.id if msg.author else msg.author_id
            if author_id and author_id != bot_id:
                log.debug(f"[{backend}] Accepting DM from {author_id}")
                return True, channel_id, content, mentioned_users

        # Check if bot is mentioned by ID
        if bot_id and any(u.id == bot_id for u in mentioned_users):
            log.debug(f"[{backend}] Bot mentioned by ID")
            return True, channel_id, content, mentioned_users

        # Check for bot name mention in text
        if bot_name and f"@{bot_name}" in content:
            log.debug(f"[{backend}] Bot mentioned by name")
            return True, channel_id, content, mentioned_users

        log.debug(f"[{backend}] Message not to bot")
        return False, "", content, mentioned_users

    def _is_direct_message(self, msg: Message, backend: str) -> bool:
        """Check if message is a direct message.

        Uses the message's is_dm property which checks channel.is_dm and metadata.
        """
        # Use the base class is_dm property which handles all the logic
        if msg.is_dm:
            return True

        # Additional backend-specific fallbacks for edge cases
        if backend == "slack":
            # Slack DM channel IDs start with 'D'
            channel_id = msg.channel_id or (msg.channel.id if msg.channel else "")
            if channel_id and channel_id.startswith("D"):
                return True

        elif backend == "symphony":
            # Check channel object for stream_type
            if msg.channel:
                stream_type = getattr(msg.channel, "stream_type", None)
                if stream_type is not None:
                    # SymphonyStreamType.IM or string "IM"
                    return str(stream_type) == "IM" or stream_type == "IM"

        elif backend == "discord":
            if msg.channel:
                channel_type = getattr(msg.channel, "channel_type", None)
                type_str = str(getattr(channel_type, "value", channel_type)).lower()
                if type_str in {"direct", "group"}:
                    return True

            metadata = msg.metadata or {}
            channel_type = metadata.get("channel_type")
            if channel_type is not None:
                type_str = str(getattr(channel_type, "value", channel_type)).lower()
                if type_str in {"direct", "group"}:
                    return True

        return False

    def _fetch_bot_info(self, backend: str) -> None:
        """Fetch bot info from the backend API and cache it.

        Uses the shared connected backend for the platform.
        """
        if backend in self._bot_user_ids and backend in self._bot_names:
            return  # Already cached

        result = self._ensure_backend_connected(backend)
        if not result:
            return

        connected_backend, loop = result

        async def _fetch():
            bot_info = await connected_backend.get_bot_info()
            if bot_info:
                self._bot_user_ids[backend] = bot_info.id
                self._bot_names[backend] = bot_info.name or bot_info.display_name or ""
                log.info(f"Bot info for {backend}: id={bot_info.id}, name={self._bot_names[backend]}")

        try:
            loop.run_until_complete(_fetch())
        except Exception:
            log.exception("Error fetching bot info for %s", backend)

    def _get_bot_id(self, backend: str) -> str | None:
        """Get the bot's user ID for a backend."""
        if backend in self._bot_user_ids:
            return self._bot_user_ids[backend]

        # Try to fetch from API
        self._fetch_bot_info(backend)
        return self._bot_user_ids.get(backend)

    def _get_bot_name(self, backend: str) -> str | None:
        """Get the bot's username for a backend.

        First checks config for explicit bot_name, then checks cache,
        then tries to fetch from backend API.
        """
        # Check config first (explicit override)
        config = self._configs.get(backend)
        if config and config.bot_name:
            return config.bot_name

        # Check cache
        if backend in self._bot_names:
            return self._bot_names[backend]

        # Try to fetch from API
        self._fetch_bot_info(backend)
        return self._bot_names.get(backend)

    def _is_authorized(self, msg: Message, backend: str) -> bool:
        """Check if the message author is authorized."""
        config = self._configs.get(backend)
        if not config or not config.user_access_channels:
            return True

        author_id = msg.author.id if msg.author else msg.author_id
        if not author_id:
            return False

        with self._lock:
            authorized = self._authorized_users.get(backend, set())
            return author_id in authorized

    def _extract_commands(
        self,
        msg: Message,
        backend: str,
        channel_id: str,
        text: str,
        mentions: list[User],
    ) -> BotCommand | list[BotCommand] | None:
        """Extract bot commands from a message.

        Uses chatom's entity recognition to identify mentioned users.
        """
        try:
            content = text.strip()

            # Strip bot mention from beginning if present
            bot_name = self._get_bot_name(backend)
            bot_id = self._get_bot_id(backend)

            # Strip <@BOT_ID> format (Slack/Discord)
            if bot_id:
                content = re.sub(rf"<@!?{re.escape(bot_id)}>", "", content).strip()

            # Strip @bot_name format (Symphony/generic)
            if bot_name and content.startswith(f"@{bot_name}"):
                content = content[len(f"@{bot_name}") :].strip()

            log.info(f"Extracting command from: {content!r}")

            # Check for command syntax (supports both / and ! prefixes)
            if not content.startswith(("/", "!")):
                # Check if this is a reply to an active agent session
                session_cmd = self._check_agent_session_reply(msg, backend, channel_id)
                if session_cmd:
                    return session_cmd

                # If tagged but no command, show help
                log.info("No command prefix, showing help")
                return self._create_help_command(msg, backend, channel_id)

            # Tokenize the command
            tokens = next(reader(StringIO(content), delimiter=" ", quotechar='"', skipinitialspace=True))
            if not tokens:
                return None

            # Parse command and arguments (strip both / and ! prefixes)
            command_name = tokens[0].lstrip("/!").lower()
            log.info(f"Parsed command_name: {command_name!r}, registered commands: {list(self._commands.keys())}")
            if command_name not in self._commands:
                log.warning(f"Unknown command: {command_name}")
                return self._create_help_command(msg, backend, channel_id)

            # Filter out the bot from mentions before parsing args
            bot_id = self._get_bot_id(backend)
            filtered_mentions = [u for u in mentions if u.id != bot_id]

            # Parse arguments and tagged users
            args, target_users, target_channel = self._parse_command_args(tokens[1:], filtered_mentions, backend)

            # Resolve channel name to ID if a target channel was specified
            target_channel_name = ""
            if target_channel:
                resolved_channel = self._resolve_channel(target_channel, backend)
                if resolved_channel:
                    target_channel = resolved_channel.id
                    target_channel_name = resolved_channel.name or target_channel
                else:
                    log.warning(f"Could not resolve channel: {target_channel}")
                    # Keep the original value - it might already be an ID
                    target_channel_name = target_channel

            # Use original channel if not specified
            if not target_channel:
                target_channel = channel_id

            # Create command
            command_runner = self._commands[command_name]

            # Check backend support
            command_backends = self._command_backends(command_runner)
            if command_backends and backend not in command_backends:
                log.warning(f"Command {command_name} not supported on {backend}")
                return None

            # Build source user from chatom Message
            source = User(
                id=msg.author.id if msg.author else msg.author_id or "",
                name=msg.author.name if msg.author else "",
                email=getattr(msg.author, "email", "") if msg.author else "",
                handle=getattr(msg.author, "handle", "") if msg.author else "",
            )

            # Build target users - they are already chatom Users
            targets = tuple(target_users)

            # Get channel name - use resolved name if we resolved a target channel
            channel_name = target_channel_name
            if not channel_name and msg.channel:
                if hasattr(msg.channel, "name"):
                    channel_name = msg.channel.name or ""
                elif isinstance(msg.channel, str):
                    channel_name = ""  # Channel is just an ID string

            bot_cmd = BotCommand(
                command=command_name,
                args=tuple(args),
                source=source,
                targets=targets,
                channel_id=target_channel,
                channel_name=channel_name,
                backend=backend,
                variant=command_runner.kind() if isinstance(command_runner, BaseCommand) else CommandVariant.REPLY,
                message=msg,
                delay=None,
                schedule="",
                times_run=0,
            )

            # Pre-execute hooks
            if isinstance(command_runner, ScheduleCommand):
                return command_runner.preexecute(bot_cmd, self._schedule_store, self)
            elif isinstance(command_runner, StatusCommand):
                return command_runner.preexecute(bot_cmd, self)
            return command_runner.preexecute(bot_cmd)

        except Exception:
            log.exception("Error extracting command")
            return None

    def _check_agent_session_reply(self, msg: Message, backend: str, channel_id: str) -> BotCommand | None:
        """Check if the message is a reply to a bot response with an active agent session.

        If so, constructs a BotCommand to continue the conversation.
        """
        try:
            from csp_bot.commands.agent import AgentCommand
        except ImportError:
            return None

        # Get the referenced message ID
        ref_id = None
        if msg.reference and msg.reference.message_id:
            ref_id = msg.reference.message_id
        elif msg.reply_to and msg.reply_to.id:
            ref_id = msg.reply_to.id
        # Check thread metadata (Slack thread_ts)
        if not ref_id and msg.thread and msg.thread.id:
            ref_id = msg.thread.id

        if not ref_id:
            return None

        # Look up session by the bot response ID
        source_id = msg.author.id if msg.author else msg.author_id or ""
        session = AgentCommand._sessions.get_by_response_id(
            ref_id,
            user_id=source_id,
            channel_id=channel_id,
            backend=backend,
        )
        if session is None:
            return None

        # Found an active session — route the reply to the same command
        command_name = session.command_name
        if command_name not in self._commands:
            log.warning(f"Agent session references unknown command: {command_name}")
            return None

        command_runner = self._commands[command_name]
        content = msg.content or ""
        # Strip bot mention if present
        bot_name = self._get_bot_name(backend)
        bot_id = self._get_bot_id(backend)
        if bot_id:
            content = re.sub(rf"<@!?{re.escape(bot_id)}>", "", content).strip()
        if bot_name and content.startswith(f"@{bot_name}"):
            content = content[len(f"@{bot_name}") :].strip()

        source = User(
            id=msg.author.id if msg.author else msg.author_id or "",
            name=msg.author.name if msg.author else "",
            email=getattr(msg.author, "email", "") if msg.author else "",
            handle=getattr(msg.author, "handle", "") if msg.author else "",
        )

        channel_name = ""
        if msg.channel and hasattr(msg.channel, "name"):
            channel_name = msg.channel.name or ""

        bot_cmd = BotCommand(
            command=command_name,
            args=(content,),
            source=source,
            targets=(),
            channel_id=channel_id,
            channel_name=channel_name,
            backend=backend,
            variant=command_runner.kind() if isinstance(command_runner, BaseCommand) else CommandVariant.REPLY,
            message=msg,
            delay=None,
            schedule="",
            times_run=0,
        )

        log.info(f"Routing reply to active agent session: command={command_name}, user={source.id}")
        return command_runner.preexecute(bot_cmd)

    def _parse_command_args(
        self,
        tokens: list[str],
        mentions: list[User],
        backend: str,
    ) -> tuple[list[str], list[User], str]:
        """Parse command arguments, extracting tagged users and channels."""
        args = []
        target_users = []
        target_channel = ""
        skip_indices = set()

        # For Symphony, we need special handling since mentions in the stripped
        # text are names like "@Paine," but our mentions list has user IDs.
        # We'll match @ tokens with mentions in order, and skip following name tokens.
        symphony_mention_iter = iter(mentions) if backend == "symphony" else None

        for i, token in enumerate(tokens):
            if i in skip_indices:
                continue

            # Handle /channel, /room, !channel, or !room directive
            if token in ("/channel", "/room", "!channel", "!room"):
                if i + 1 < len(tokens):
                    target_channel = tokens[i + 1]
                    skip_indices.add(i + 1)
                continue

            # Check if token is a mention placeholder
            is_mention = False

            # For Symphony: if token starts with @, it's a mention - match with next mention in list
            # Also skip following tokens that look like they're part of the name (no @ prefix, no /)
            if backend == "symphony" and token.startswith("@") and symphony_mention_iter:
                try:
                    user = next(symphony_mention_iter)
                    target_users.append(user)
                    is_mention = True
                    # Skip following tokens that are likely part of the multi-word name
                    # (they don't start with @ or / and aren't commands)
                    j = i + 1
                    while j < len(tokens):
                        next_token = tokens[j]
                        if next_token.startswith(("@", "/", "!")):
                            break
                        skip_indices.add(j)
                        j += 1
                except StopIteration:
                    pass  # No more mentions to match

            # For other backends: match by user ID
            if not is_mention:
                for user in mentions:
                    if user.id in token or f"@{user.id}" == token:
                        target_users.append(user)
                        is_mention = True
                        break

            if not is_mention:
                args.append(token)

        return args, target_users, target_channel

    def _create_help_command(self, msg: Message, backend: str, channel_id: str) -> BotCommand | None:
        """Create a help command when no specific command is given."""
        command_runner = self._commands.get("help")
        if not command_runner:
            return None

        source = User(
            id=msg.author.id if msg.author else msg.author_id or "",
            name=msg.author.name if msg.author else "",
            email=getattr(msg.author, "email", "") if msg.author else "",
            handle=getattr(msg.author, "handle", "") if msg.author else "",
        )

        # Handle channel name - SlackMessage.channel is a string, not a Channel object
        channel_name = ""
        if msg.channel:
            if hasattr(msg.channel, "name"):
                channel_name = msg.channel.name or ""
            elif isinstance(msg.channel, str):
                channel_name = ""  # Can't get name from channel ID string

        return BotCommand(
            command="help",
            args=(),
            source=source,
            targets=(),
            channel_id=channel_id,
            channel_name=channel_name,
            backend=backend,
            variant=command_runner.kind() if isinstance(command_runner, BaseCommand) else CommandVariant.REPLY,
            message=msg,
            delay=None,
            schedule="",
            times_run=0,
        )

    def _execute_command(self, cmd: BotCommand) -> Message | list[Message] | BotCommand | list[BotCommand] | None:
        """Execute a bot command and return responses."""
        command_runner = self._commands.get(cmd.command)
        if not command_runner:
            return None

        try:
            if isinstance(command_runner, BaseCommand):
                if isinstance(command_runner, HelpCommand):
                    responses = command_runner.execute(cmd, MappingProxyType(self._commands))
                elif isinstance(command_runner, ScheduleCommand):
                    responses = command_runner.execute(cmd, self._schedule_store)
                else:
                    responses = command_runner.execute(cmd)
            elif isinstance(command_runner, Command):
                ctx = self._build_command_context(cmd)
                responses = [r for r in execute_command_func(command_runner.execute, ctx) if r is not None]
            elif hasattr(command_runner, "handler"):
                ctx = self._build_command_context(cmd)
                responses = [r for r in execute_command_func(command_runner.handler, ctx) if r is not None]
            else:
                log.error(f"Unsupported command runner type for {cmd.command}: {type(command_runner).__name__}")
                return None
        except Exception:
            log.exception(f"Error executing command: {cmd.command}")
            return None

        log.debug(f"Command {cmd.command} returned: {type(responses)}: {responses}")

        if not responses:
            log.debug(f"Command {cmd.command} returned empty response, returning None")
            return None

        results = responses if isinstance(responses, list) else [responses]

        # Convert BotMessage to chatom Message if needed, and ensure metadata is set
        processed = []
        for r in results:
            log.debug(f"Processing response item: {type(r)}")
            if isinstance(r, BotMessage):
                processed.append(self._bot_message_to_chatom(r))
            elif isinstance(r, Message):
                # Ensure metadata has backend set for filtering
                if r.metadata is None:
                    r.metadata = {}
                if not r.metadata.get("backend"):
                    # Use the message's backend field if set, otherwise use command's backend
                    r.metadata["backend"] = r.backend or cmd.backend
                processed.append(r)
            else:
                processed.append(r)

        log.debug(f"Returning {len(processed)} processed messages for {cmd.command}")
        return processed

    def _bot_message_to_chatom(self, bot_msg: BotMessage) -> Message:
        """Convert a BotMessage to a chatom Message."""
        return Message(
            content=bot_msg.content,
            channel=Channel(id=bot_msg.channel_id, name=bot_msg.channel_name),
            thread_id=bot_msg.thread_id,
            mention_ids=list(bot_msg.mentions) if bot_msg.mentions else [],
            metadata={"backend": bot_msg.backend},
        )

    def _create_response_message(
        self,
        content: str,
        channel_id: str,
        backend: str,
        thread_id: str = "",
        mentions: list[User] | None = None,
    ) -> Message:
        """Create a response message with chatom.

        Uses chatom's mention_user_for_backend for cross-platform mentions.
        """
        # Build mention string if users provided
        mention_str = ""
        if mentions:
            mention_parts = [mention_user_for_backend(u, backend) for u in mentions]
            mention_str = " ".join(mention_parts) + " "

        return Message(
            content=mention_str + content,
            channel=Channel(id=channel_id),
            thread_id=thread_id,
            mention_ids=[u.id for u in (mentions or [])],
            metadata={"backend": backend},
        )
