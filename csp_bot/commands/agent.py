"""AgentCommand — base class for LLM-powered bot commands.

Provides the boilerplate for running a pydantic-ai Agent from within
a csp-bot command: background execution via a thread pool, automatic
rescheduling until the LLM call completes, and formatted response output.

Supports **stateful sessions**: when a user replies to a bot response (or
invokes the same command again within a time window), the conversation
history is resumed automatically.  Sessions expire after a configurable TTL.

Subclasses implement :meth:`build_agent` and :meth:`build_prompt`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from abc import abstractmethod
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from chatom import Channel, Message
from chatom.backend import BackendBase
from chatom.format import Format, convert_format

from csp_bot.commands.base import BaseCommand, BaseCommandModel, ReplyCommand
from csp_bot.persistence import InMemoryStateStore, StateStore
from csp_bot.structs import BotCommand

try:
    from chatom.agent import BackendToolset
    from chatom.agent.toolset import AccessPolicy
    from pydantic_ai.messages import ModelMessagesTypeAdapter
except ImportError as e:
    raise ImportError("AgentCommand requires the 'agent' extra. Install with: pip install csp-bot[agent]") from e

log = logging.getLogger(__name__)

__all__ = ("AgentCommand", "AgentCommandModel")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-cmd")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _AgentRunControl:
    """Thread-safe cancellation bridge to an active asyncio agent run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._cancel: Callable[[], Any] | None = None

    def bind(self, cancel: Callable[[], Any]) -> None:
        with self._lock:
            self._cancel = cancel
            cancel_requested = self._cancel_requested
        if cancel_requested:
            cancel()

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            cancel = self._cancel
        if cancel is not None:
            cancel()

    def clear(self) -> None:
        with self._lock:
            self._cancel = None


@dataclass
class AgentSession:
    """Tracks a multi-turn conversation between a user and an agent command."""

    #: Bumped whenever the serialized layout in :meth:`to_dict` changes.
    SCHEMA_VERSION: ClassVar[int] = 2

    user_id: str
    channel_id: str
    backend: str
    command_name: str
    message_history: list[Any] = field(default_factory=list)
    last_active: datetime = field(default_factory=_utc_now)
    bot_response_id: str | None = None  # ID of last bot message (for reply matching)

    @property
    def store_key(self) -> str:
        """Deterministic key for this session: command:backend:user:channel."""
        return f"{self.command_name}:{self.backend}:{self.user_id}:{self.channel_id}"

    def touch(self) -> None:
        self.last_active = _utc_now()

    def is_expired(self, ttl_seconds: float) -> bool:
        return (_utc_now() - self.last_active).total_seconds() > ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for durable storage.

        The pydantic-ai conversation history is serialized via
        :data:`ModelMessagesTypeAdapter` so it round-trips across processes.
        """
        opaque_history = all(isinstance(item, dict) and item.get("type") == "claude-session" for item in self.message_history)
        history_format = "opaque" if self.message_history and opaque_history else "pydantic-ai"
        history = self.message_history if history_format == "opaque" else ModelMessagesTypeAdapter.dump_python(self.message_history, mode="json")
        return {
            "schema_version": self.SCHEMA_VERSION,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "backend": self.backend,
            "command_name": self.command_name,
            "history_format": history_format,
            "message_history": history,
            "last_active": self.last_active.isoformat(),
            "bot_response_id": self.bot_response_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSession:
        """Reconstruct a session from :meth:`to_dict` output."""
        version = data.get("schema_version")
        if version not in (1, cls.SCHEMA_VERSION):
            raise ValueError(f"Unsupported AgentSession schema version: {version!r} (expected 1 or {cls.SCHEMA_VERSION})")
        last_active = data.get("last_active")
        history_format = data.get("history_format", "pydantic-ai")
        raw_history = data.get("message_history") or []
        if history_format == "opaque":
            message_history = list(raw_history)
        elif history_format == "pydantic-ai":
            message_history = list(ModelMessagesTypeAdapter.validate_python(raw_history))
        else:
            raise ValueError(f"Unsupported agent history format: {history_format!r}")
        return cls(
            user_id=data["user_id"],
            channel_id=data["channel_id"],
            backend=data.get("backend", ""),
            command_name=data["command_name"],
            message_history=message_history,
            last_active=datetime.fromisoformat(last_active) if last_active else _utc_now(),
            bot_response_id=data.get("bot_response_id"),
        )


class SessionStore:
    """Store for agent sessions, backed by a :class:`StateStore`.

    Sessions and the bot-response→session reply index live in two namespaces
    of the same :class:`StateStore`. The default :class:`InMemoryStateStore`
    keeps behavior simple (and preserves object identity for callers that
    mutate a returned session in place), while a durable backend can be
    injected to survive restarts without changing command implementations.

    Expiry is driven by each session's ``last_active`` timestamp and the
    configured TTL, independent of any TTL the underlying store applies.
    """

    namespace = "csp_bot.agent_sessions"
    response_namespace = "csp_bot.agent_sessions.responses"

    def __init__(self, ttl_seconds: float = 900.0, store: StateStore | None = None):
        self._ttl = ttl_seconds
        self.store: StateStore = store if store is not None else InMemoryStateStore()
        self._lock = threading.Lock()

    def get(self, key: str) -> AgentSession | None:
        with self._lock:
            session = self._load(key)
            if session and session.is_expired(self._ttl):
                self._remove_session(key, session)
                return None
            return session

    def get_by_response_id(
        self,
        response_id: str,
        *,
        user_id: str,
        channel_id: str,
        backend: str,
    ) -> AgentSession | None:
        """Look up a session by the bot's response message ID (for replies)."""
        with self._lock:
            key = self.store.get(self.response_namespace, response_id)
            if key is None:
                return None
            session = self._load(key)
            if session and session.is_expired(self._ttl):
                self._remove_session(key, session)
                return None
            if session and (session.user_id != user_id or session.channel_id != channel_id or session.backend != backend):
                return None
            return session

    def put(self, key: str, session: AgentSession) -> None:
        with self._lock:
            self.store.put(self.namespace, key, session)
            if session.bot_response_id:
                self.store.put(self.response_namespace, session.bot_response_id, key)

    def update_response_id(self, key: str, response_id: str) -> None:
        """Associate a bot response message ID with a session."""
        with self._lock:
            session = self._load(key)
            if session is None:
                return
            if session.bot_response_id:
                self.store.delete(self.response_namespace, session.bot_response_id)
            session.bot_response_id = response_id
            self.store.put(self.namespace, key, session)
            self.store.put(self.response_namespace, response_id, key)

    def _load(self, key: str) -> AgentSession | None:
        """Load a session, accepting both live objects and serialized dicts."""
        value = self.store.get(self.namespace, key)
        if value is None or isinstance(value, AgentSession):
            return value
        if isinstance(value, dict):
            return AgentSession.from_dict(value)
        raise TypeError(f"Unexpected session value for {key!r}: {type(value)!r}")

    def _remove_session(self, key: str, session: AgentSession | None = None) -> None:
        """Remove a session and its reply-index entry (caller holds lock)."""
        if session is None:
            session = self._load(key)
        self.store.delete(self.namespace, key)
        if session and session.bot_response_id:
            self.store.delete(self.response_namespace, session.bot_response_id)

    def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        with self._lock:
            removed = 0
            for record in list(self.store.records(self.namespace)):
                session = record.value if isinstance(record.value, AgentSession) else AgentSession.from_dict(record.value)
                if session.is_expired(self._ttl):
                    self._remove_session(record.key, session)
                    removed += 1
            return removed


def _run_agent(
    agent: Any,
    prompt: str | Sequence[Any],
    loop: asyncio.AbstractEventLoop | None = None,
    message_history: Sequence[Any] | None = None,
    control: _AgentRunControl | None = None,
) -> Any:
    """Run an agent on an event loop (for use in thread pool).

    If *loop* is provided (i.e. the backend's event loop), it is reused so
    that aiohttp sessions bound to it remain valid.  Otherwise a fresh loop
    is created.

    ``prompt`` may be a plain string or a sequence of pydantic-ai user
    content parts (e.g. text plus :class:`~pydantic_ai.BinaryContent`
    images), enabling multimodal input.
    """
    coro = agent.run(prompt, message_history=message_history)
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        if control is not None:
            control.bind(future.cancel)
        try:
            return future.result()
        finally:
            if control is not None:
                control.clear()

    owns_loop = loop is None
    if owns_loop:
        loop = asyncio.new_event_loop()
    task = loop.create_task(coro)
    if control is not None:
        control.bind(lambda: loop.call_soon_threadsafe(task.cancel))
    try:
        return loop.run_until_complete(task)
    finally:
        if control is not None:
            control.clear()
        if owns_loop:
            loop.close()


class AgentCommand(ReplyCommand):
    """Base class for commands that run a pydantic-ai agent with session support.

    Subclasses implement :meth:`build_agent` and :meth:`build_prompt`,
    and receive a :class:`~chatom.agent.BackendToolset` for free via
    :meth:`build_toolset`.

    **Session continuity**: conversation history is preserved per
    user+channel+command.  If the user replies to the bot's response
    (thread reply or message reference), the prior session is resumed.
    Sessions expire after :attr:`session_ttl_seconds`.

    Example::

        class AskCommand(AgentCommand):
            model_name = "github-copilot:claude-haiku-4.5"

            def command(self): return "ask"
            def name(self): return "Ask"
            def help(self): return "/ask <question> — Ask the AI (reply to continue)"

            def build_agent(self, command):
                toolset = self.build_toolset(command)
                return Agent(
                    self.get_model(),
                    toolsets=[toolset] if toolset else [],
                    instructions="You are a helpful assistant.",
                )

            def build_prompt(self, command):
                return " ".join(command.args)
    """

    _backends: ClassVar[dict[str, BackendBase]] = {}
    _backend_loops: ClassVar[dict[str, asyncio.AbstractEventLoop]] = {}
    _futures: ClassVar[dict[str, Future]] = {}
    _run_controls: ClassVar[dict[str, _AgentRunControl]] = {}
    _sessions: ClassVar[SessionStore] = SessionStore(ttl_seconds=900.0)

    model_name: str = "claude-sonnet-4-6"
    # Configurable delay between polling checks (seconds)
    poll_interval: int = 2
    # Maximum time to wait for agent completion (seconds)
    timeout: int = 120
    # Maximum total tool calls the agent may make in a single run. Guards
    # against runaway tool loops. 0 disables the cap.
    max_tool_calls: int = 25
    # Optional per-tool call caps for a single run (e.g. limit expensive
    # history reads / searches). None applies no per-tool limit.
    per_tool_limits: ClassVar[dict[str, int] | None] = None
    # Session time-to-live (seconds). 0 disables sessions.
    session_ttl_seconds: float = 900.0
    # Send a status message every N poll cycles (0 disables)
    status_every_n_polls: int = 15
    # When True, image attachments on the incoming message are downloaded
    # and passed to the model as multimodal input (so the agent can "see"
    # images the user posted).
    include_incoming_images: bool = True
    # Maximum size (bytes) of an incoming image to download for the model.
    max_incoming_image_bytes: int = 5_000_000
    # Base preamble prepended ahead of every prompt for this command.
    # Override per subclass (class attribute) or dynamically via
    # build_root_prompt(). Empty by default.
    root_prompt: str = ""
    # When True, prepend a note describing the invoking channel so the agent
    # can resolve references like "this channel" / "the current room".
    inject_channel: bool = True
    # Status messages shown to the user while processing
    status_messages: ClassVar[list[str]] = [
        "Thinking...",
        "Still working on it...",
        "Processing your request...",
        "Almost there...",
        "Crunching the data...",
    ]

    def __init__(self, *args, **kwargs):
        model_name = kwargs.pop("model_name", None)
        if model_name is not None:
            self.model_name = model_name

    @classmethod
    def set_backends(
        cls,
        backends: dict[str, BackendBase],
        loops: dict[str, asyncio.AbstractEventLoop] | None = None,
    ) -> None:
        """Inject backend instances. Called by Bot after adapter setup."""
        cls._backends = backends
        cls._backend_loops = loops or {}

    @classmethod
    def set_session_ttl(cls, ttl_seconds: float) -> None:
        """Reconfigure the session TTL, preserving the backing store."""
        cls._sessions = SessionStore(ttl_seconds=ttl_seconds, store=cls._sessions.store)

    @classmethod
    def set_session_store(cls, store: StateStore, ttl_seconds: float | None = None) -> None:
        """Back agent sessions with a (possibly durable) StateStore.

        Injecting an ``FsspecStateStore`` (or other durable backend) lets
        sessions survive bot restarts without changing command code.
        """
        ttl = ttl_seconds if ttl_seconds is not None else cls.session_ttl_seconds
        cls._sessions = SessionStore(ttl_seconds=ttl, store=store)

    @abstractmethod
    def build_agent(self, command: BotCommand) -> Any:
        """Return the agent or runner to use for this command."""
        ...

    @abstractmethod
    def build_prompt(self, command: BotCommand) -> str:
        """Return the user prompt string."""
        ...

    def build_root_prompt(self, command: BotCommand) -> str:
        """Return a base preamble prepended ahead of :meth:`build_prompt`.

        Defaults to the :attr:`root_prompt` class attribute. Override for a
        dynamic, per-invocation preamble. The final prompt is assembled as
        ``root_prompt`` + channel-context note (when :attr:`inject_channel`)
        + the command's own prompt.
        """
        return self.root_prompt

    def build_toolset(self, command: BotCommand) -> BackendToolset | None:
        """Return a BackendToolset for the command's backend, or None.

        The toolset is configured with an AccessPolicy that enforces:
        - The agent can only access the channel where the command was invoked
        - The requesting user must be a member of any target channel
        - DM reads are blocked by default
        - Message count is capped per request

        The toolset also enforces a per-run tool call budget
        (:attr:`max_tool_calls`) and optional per-tool caps
        (:attr:`per_tool_limits`) to guard against runaway tool loops.

        Subclasses can override :meth:`build_access_policy` to customize.
        """
        backend = self._backends.get(command.backend)
        if backend is None:
            return None
        policy = self.build_access_policy(command)
        return BackendToolset(
            backend=backend,
            access_policy=policy,
            max_tool_calls=self.max_tool_calls,
            per_tool_limits=self.per_tool_limits,
        )

    def build_access_policy(self, command: BotCommand) -> AccessPolicy:
        """Build the access policy for this command invocation.

        Override in subclasses to customize access rules. The default
        policy restricts access to the channel where the user typed the
        command (not the /room redirect target).
        """
        # Use the original channel where the user issued the command,
        # not the /room-redirected destination.
        origin_channel_id = command.message.channel_id if command.message and command.message.channel_id else command.channel_id
        return AccessPolicy(
            requesting_user=command.source,
            invoking_channel_id=origin_channel_id,
            restrict_to_invoking_channel=True,
            require_membership=True,
            block_dm_reads=True,
        )

    def get_model(self, model_name: str | None = None) -> Any:
        """Return a model instance configured from environment variables.

        Fully qualified model names are resolved by PydanticAI. Bare model names
        retain the Anthropic gateway configuration used by existing commands.
        """
        if model_name is None:
            model_name = self.model_name

        if ":" in model_name:
            from pydantic_ai.models import infer_model

            return infer_model(model_name)

        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        base_url = os.environ.get("ANTHROPIC_BASE_URL")

        provider = AnthropicProvider(api_key=api_key, base_url=base_url)
        return AnthropicModel(model_name, provider=provider)

    def wrap_symphony_output(self, messageml: str, command: BotCommand) -> str:
        """Hook to post-process Symphony MessageML before wrapping in <messageML>.

        Override in subclasses to wrap output in expandable cards, add
        headers, etc. Default is identity (no wrapping).
        """
        return messageml

    def _session_key(self, command: BotCommand) -> str:
        """Key for session lookup: command:backend:user:channel."""
        return f"{self.command()}:{command.backend}:{command.source.id}:{command.channel_id}"

    def _get_session(self, command: BotCommand) -> AgentSession | None:
        """Find an existing session — by reply reference or by user+channel."""
        # First: check if this is a reply to a bot message
        msg = command.message
        reply_to_id = getattr(msg, "reply_to_id", None) or (msg.reference.message_id if getattr(msg, "reference", None) else None)
        if reply_to_id:
            session = self._sessions.get_by_response_id(
                reply_to_id,
                user_id=command.source.id,
                channel_id=command.channel_id,
                backend=command.backend,
            )
            if session:
                session.touch()
                self._sessions.put(session.store_key, session)
                return session

        # Second: check by user+channel (same command re-invoked)
        session = self._sessions.get(self._session_key(command))
        if session:
            session.touch()
            self._sessions.put(session.store_key, session)
            return session

        return None

    def _create_session(self, command: BotCommand) -> AgentSession:
        """Create a new session for this command invocation."""
        session = AgentSession(
            user_id=command.source.id,
            channel_id=command.channel_id,
            command_name=self.command(),
            backend=command.backend,
        )
        self._sessions.put(self._session_key(command), session)
        return session

    def _command_key(self, command: BotCommand) -> str:
        """Unique key for tracking in-flight futures."""
        # Use the message ID for stability across reschedules
        msg_id = command.message.id if command.message else ""
        return f"{self.command()}:{command.source.id}:{msg_id}"

    def _status_channel(self, command: BotCommand) -> Channel:
        """Return the channel where status/progress messages should go.

        When /room redirect is used, status updates go to the origin channel
        (where the user typed the command) rather than the redirect destination.
        """
        if command.message and command.message.channel_id:
            origin_id = command.message.channel_id
            origin_name = command.message.channel.name if command.message.channel else ""
            if origin_id != command.channel_id:
                return Channel(id=origin_id, name=origin_name)
        return command.channel

    def _incoming_image_attachments(self, command: BotCommand) -> list[Any]:
        """Return image attachments on the incoming message, if any."""
        msg = command.message
        if not msg or not getattr(msg, "attachments", None):
            return []
        images = []
        for att in msg.attachments:
            content_type = getattr(att, "content_type", "") or ""
            att_type = getattr(getattr(att, "attachment_type", None), "value", "")
            if content_type.startswith("image/") or att_type == "image":
                images.append(att)
        return images

    def _prompt_prefix(self, command: BotCommand) -> str:
        """Assemble the root prompt and channel-context note that precede the
        command's own prompt. Returns "" when neither applies."""
        parts: list[str] = []
        root = self.build_root_prompt(command)
        if root:
            parts.append(root)
        if self.inject_channel:
            note = self._channel_context_note(command)
            if note:
                parts.append(note)
        return "\n\n".join(parts)

    def _channel_context_note(self, command: BotCommand) -> str:
        """Describe the invoking channel so the agent can resolve references.

        Without this, a command like ``/ask`` has no idea which channel it is
        running in, so phrases like "this channel" or "the current room" are
        unresolvable and history tools get called with a guessed channel.
        Uses the origin channel (where the user typed the command), not any
        ``/room`` redirect target.
        """
        channel_id = command.message.channel_id if command.message and command.message.channel_id else command.channel_id
        if not channel_id:
            return ""
        channel_name = ""
        if command.message and command.message.channel:
            channel_name = command.message.channel.name or ""
        if not channel_name:
            channel_name = command.channel_name or ""
        name_part = f' (name: "{channel_name}")' if channel_name else ""
        return (
            f"[Context: this conversation is taking place in the channel with "
            f'id="{channel_id}"{name_part}. When the user refers to "this channel", '
            '"this room", "here", or "the current channel/room", they mean this '
            "channel; pass this id to tools such as read_channel_history.]"
        )

    def _build_model_prompt(self, command: BotCommand, prompt: str) -> str | list[Any]:
        """Assemble the prompt for the model, attaching incoming images.

        Downloads any image attachments on the incoming message (via the
        backend, on its event loop) and returns a list of pydantic-ai
        content parts so the model can see them. Falls back to the plain
        text prompt when there are no images or download is unavailable.
        """
        if not self.include_incoming_images:
            return prompt

        images = self._incoming_image_attachments(command)
        if not images:
            return prompt

        backend = self._backends.get(command.backend)
        if backend is None:
            return prompt

        from pydantic_ai import BinaryContent

        backend_loop = self._backend_loops.get(command.backend)
        parts: list[Any] = [prompt]
        for att in images:
            if getattr(att, "size", None) and att.size > self.max_incoming_image_bytes:
                log.warning("Skipping incoming image %r: %s bytes exceeds limit", getattr(att, "filename", ""), att.size)
                continue
            try:
                data = self._download_on_loop(backend, att, command.message, backend_loop)
            except Exception:
                log.exception("Failed to download incoming image %r", getattr(att, "filename", ""))
                continue
            if not data or len(data) > self.max_incoming_image_bytes:
                continue
            media_type = getattr(att, "content_type", "") or "image/png"
            parts.append(BinaryContent(data=data, media_type=media_type))

        # Only return a multimodal list if we actually attached an image.
        return parts if len(parts) > 1 else prompt

    @staticmethod
    def _download_on_loop(
        backend: BackendBase,
        attachment: Any,
        message: Message | None,
        loop: asyncio.AbstractEventLoop | None,
    ) -> bytes:
        """Download an attachment on the backend's own event loop.

        The backend's HTTP client (e.g. Symphony's aiohttp session) is bound to
        the loop it was connected on, so the download must run there. That loop
        is usually idle rather than running, so drive it with
        ``run_until_complete``; only a running loop needs a threadsafe submit.
        Running the coroutine on a fresh loop raises aiohttp's "Timeout context
        manager should be used inside a task".
        """
        coro = backend.download_attachment(attachment, message=message)
        if loop is not None:
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
            return loop.run_until_complete(coro)
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()

    def preexecute(self, command: BotCommand) -> BotCommand:
        """Submit the LLM call to a thread pool and schedule a check."""
        removed_sessions = self._sessions.cleanup_expired()
        if removed_sessions:
            log.debug("Cleaned up %d expired agent sessions", removed_sessions)

        key = self._command_key(command)
        if key not in self._futures:
            try:
                agent = self.build_agent(command)
                prompt_text = self.build_prompt(command)
                prefix = self._prompt_prefix(command)
                if prefix:
                    prompt_text = f"{prefix}\n\n{prompt_text}"
                prompt = self._build_model_prompt(command, prompt_text)
            except Exception:
                log.exception("Error building agent/prompt for %s", self.command())
                command.args = ("ERROR: Failed to initialize the AI agent.",)
                return command

            # Resolve session history
            session = self._get_session(command) or self._create_session(command)
            history = session.message_history or None

            # Use the backend's event loop so aiohttp sessions stay valid
            backend_loop = self._backend_loops.get(command.backend)
            control = _AgentRunControl()
            future = _executor.submit(_run_agent, agent, prompt, backend_loop, history, control)
            self._futures[key] = future
            self._run_controls[key] = control
            log.info(
                "AgentCommand[%s] submitted for user %s (session history: %d msgs)",
                self.command(),
                command.source.name,
                len(session.message_history),
            )

        command.delay = _utc_now() + timedelta(seconds=self.poll_interval)
        return command

    def execute(self, command: BotCommand) -> Message | list[Message | BaseCommand] | BaseCommand | None:
        """Return result when ready; reschedule if still running."""
        # Handle errors from preexecute
        if command.args and len(command.args) == 1 and str(command.args[0]).startswith("ERROR:"):
            return Message(
                content=str(command.args[0]),
                channel=command.channel,
                metadata={"backend": command.backend},
            )

        key = self._command_key(command)
        future = self._futures.get(key)

        if future is None:
            log.warning("AgentCommand[%s] no future found for key %s", self.command(), key)
            return Message(
                content="Sorry, something went wrong processing your request.",
                channel=command.channel,
                metadata={"backend": command.backend},
            )

        if not future.done():
            # Check timeout
            elapsed = command.times_run * self.poll_interval
            if elapsed >= self.timeout:
                self._futures.pop(key, None)
                control = self._run_controls.pop(key, None)
                if control is not None:
                    control.cancel()
                future.cancel()
                return Message(
                    content="Sorry, the AI request timed out. Please try again.",
                    channel=command.channel,
                    metadata={"backend": command.backend},
                )
            # Reschedule — optionally with a status message
            command.delay = _utc_now() + timedelta(seconds=self.poll_interval)
            command.times_run += 1

            result: list[Any] = [command]
            if self.status_every_n_polls and self.status_messages:
                # Status messages go to the origin channel (where the user
                # typed the command), NOT the /room redirect destination.
                origin_channel = self._status_channel(command)
                if command.times_run == 1:
                    # First poll: always send initial status
                    status_text = self.status_messages[0]
                    result.append(
                        Message(
                            content=status_text,
                            channel=origin_channel,
                            metadata={"backend": command.backend},
                        )
                    )
                elif command.times_run % self.status_every_n_polls == 0:
                    idx = (command.times_run // self.status_every_n_polls) % len(self.status_messages)
                    status_text = self.status_messages[idx]
                    result.append(
                        Message(
                            content=status_text,
                            channel=origin_channel,
                            metadata={"backend": command.backend},
                        )
                    )
            return result

        # Future is done — get result
        self._futures.pop(key, None)
        self._run_controls.pop(key, None)
        try:
            result = future.result()
            output = str(result.output) if hasattr(result, "output") else str(result)

            # Persist conversation history in session
            session = self._get_session(command) or self._create_session(command)
            if hasattr(result, "all_messages"):
                session.message_history = list(result.all_messages())
            session.touch()
            self._sessions.put(session.store_key, session)

        except Exception:
            log.exception("AgentCommand[%s] agent execution failed", self.command())
            return Message(
                content="Sorry, there was an error processing your request.",
                channel=command.channel,
                metadata={"backend": command.backend},
            )

        # For Symphony, LLM output is markdown that must be converted to
        # MessageML.  Pre-wrapping here prevents the backend from treating
        # the content as pre-formatted MessageML.
        if command.backend == "symphony":
            messageml = convert_format(output, Format.MARKDOWN, Format.SYMPHONY_MESSAGEML)
            messageml = self.wrap_symphony_output(messageml, command)
            output = f"<messageML>{messageml}</messageML>"

        # Build response — include session key in metadata so the bot can
        # associate the response message ID back to the session later.
        response = Message(
            content=output,
            channel=command.channel,
            metadata={
                "backend": command.backend,
                "agent_session_key": self._session_key(command),
            },
        )
        return response

    def on_response_sent(self, session_key: str, response_message_id: str) -> None:
        """Associate a sent message ID with the session for reply tracking.

        Called by the Bot after the response message is published, so that
        future replies to that message can be routed back to this session.
        """
        self._sessions.update_response_id(session_key, response_message_id)


class AgentCommandModel(BaseCommandModel):
    """Model for registering an agent command with model configuration."""

    command: type[AgentCommand]
    model_name: str = "claude-sonnet-4-6"

    def create_command(self) -> AgentCommand:
        """Create the configured agent command."""
        return self.command(model_name=self.model_name)
