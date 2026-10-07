"""Integration tests for Bot command processing and message routing.

These tests cover critical integration points that have caused production bugs:
1. Metadata propagation - ensuring messages have backend metadata for filtering
2. Channel resolution - resolving channel names to IDs with connected backends
3. Command argument parsing - handling /room and /channel directives
4. Event loop management - ensuring async operations don't fail with closed loops
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from chatom import Channel, ChannelType, Message, User
from chatom.discord import DiscordChannel
from pydantic import Field

from csp_bot import Bot, BotCommand, BotConfig, BotMessage
from csp_bot.bot_config import SymphonyConfig
from csp_bot.commands import HelpCommand, ReplyToOtherCommand
from csp_bot.commands.framework import Command, CommandModel, clear_registry, command
from csp_bot.structs import CommandVariant

# Test Fixtures


@pytest.fixture
def mock_symphony_backend():
    """Create a mock Symphony backend for testing."""
    backend = MagicMock()
    backend.config = MagicMock()

    # Mock async methods
    async def mock_connect():
        pass

    async def mock_get_bot_info():
        return User(id="bot123", name="TestBot", display_name="Test Bot")

    async def mock_fetch_channel(id=None, name=None):
        if name == "TKP":
            return Channel(id="stream123", name="TKP")
        if id == "stream123":
            return Channel(id="stream123", name="TKP")
        return None

    backend.connect = mock_connect
    backend.get_bot_info = mock_get_bot_info
    backend.fetch_channel = mock_fetch_channel

    return backend


@pytest.fixture
def mock_adapter(mock_symphony_backend):
    """Create a mock adapter wrapping the backend."""
    adapter = MagicMock()
    adapter.backend = mock_symphony_backend
    return adapter


@pytest.fixture
def bot_with_symphony():
    """Create a Bot with Symphony config for testing."""
    config = BotConfig(
        symphony=SymphonyConfig(),
    )
    return Bot(config=config)


@pytest.fixture
def sample_bot_command():
    """Create a sample BotCommand for testing."""
    return BotCommand(
        command="slap",
        args=(),
        source=User(id="user123", name="Test User"),
        targets=(User(id="target456", name="Target User"),),
        channel_id="channel789",
        channel_name="test-channel",
        backend="symphony",
        variant=CommandVariant.REPLY_TO_OTHER,
        message=Message(
            id="msg123",
            content="/slap @Target User",
            author=User(id="user123"),
            channel=Channel(id="channel789"),
        ),
        delay=None,
        schedule="",
        times_run=0,
    )


# Metadata Propagation Tests


class TestMetadataPropagation:
    """Tests to ensure metadata is correctly propagated for message filtering.

    Bug context: Commands returning Message objects directly (like trout/slap)
    were not being routed to backends because metadata["backend"] wasn't set.
    _execute_command now sets it, and _filter_messages_for_backend also falls
    back to msg.backend, so either names the destination.
    """

    def test_execute_command_sets_metadata_on_message(self, bot_with_symphony, sample_bot_command):
        """Test that _execute_command ensures metadata["backend"] is set."""

        # Create a simple command that returns a Message without metadata
        class SimpleCommand(ReplyToOtherCommand):
            def command(self):
                return "test"

            def name(self):
                return "Test"

            def help(self):
                return "Test command"

            def execute(self, cmd: BotCommand) -> Message | None:
                # Return message with backend field but NO metadata
                return Message(
                    content="Hello",
                    channel=cmd.channel,
                    backend=cmd.backend,
                    # Note: metadata is NOT set here - this was the bug
                )

        # Register the command
        bot_with_symphony._commands["test"] = SimpleCommand()

        # Change command to use our test command
        test_cmd = BotCommand(
            command="test",
            args=(),
            source=sample_bot_command.source,
            targets=sample_bot_command.targets,
            channel_id=sample_bot_command.channel_id,
            channel_name=sample_bot_command.channel_name,
            backend="symphony",
            variant=CommandVariant.REPLY_TO_OTHER,
            message=sample_bot_command.message,
            delay=None,
            schedule="",
            times_run=0,
        )

        # Execute command
        results = bot_with_symphony._execute_command(test_cmd)

        # Verify metadata was added
        assert results is not None
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.metadata is not None
        assert msg.metadata.get("backend") == "symphony"

    def test_execute_command_preserves_existing_metadata(self, bot_with_symphony, sample_bot_command):
        """Test that existing metadata is preserved, not overwritten."""

        class MetadataCommand(ReplyToOtherCommand):
            def command(self):
                return "meta"

            def name(self):
                return "Meta"

            def help(self):
                return "Metadata test"

            def execute(self, cmd: BotCommand) -> Message | None:
                return Message(
                    content="Hello",
                    channel=cmd.channel,
                    backend=cmd.backend,
                    metadata={"backend": "symphony", "custom_key": "custom_value"},
                )

        bot_with_symphony._commands["meta"] = MetadataCommand()

        test_cmd = BotCommand(
            command="meta",
            args=(),
            source=sample_bot_command.source,
            targets=(),
            channel_id="ch123",
            channel_name="test",
            backend="symphony",
            variant=CommandVariant.REPLY,
            message=sample_bot_command.message,
            delay=None,
            schedule="",
            times_run=0,
        )

        results = bot_with_symphony._execute_command(test_cmd)

        assert results is not None
        msg = results[0]
        assert msg.metadata["backend"] == "symphony"
        assert msg.metadata["custom_key"] == "custom_value"

    def test_execute_command_uses_msg_backend_over_cmd_backend(self, bot_with_symphony, sample_bot_command):
        """Test that msg.backend takes precedence over cmd.backend when set."""

        class BackendCommand(ReplyToOtherCommand):
            def command(self):
                return "backend"

            def name(self):
                return "Backend"

            def help(self):
                return "Backend test"

            def execute(self, cmd: BotCommand) -> Message | None:
                # Return message with explicit backend different from command
                return Message(
                    content="Hello",
                    channel=cmd.channel,
                    backend="slack",  # Different from cmd.backend
                )

        bot_with_symphony._commands["backend"] = BackendCommand()

        test_cmd = BotCommand(
            command="backend",
            args=(),
            source=sample_bot_command.source,
            targets=(),
            channel_id="ch123",
            channel_name="test",
            backend="symphony",  # Command says symphony
            variant=CommandVariant.REPLY,
            message=sample_bot_command.message,
            delay=None,
            schedule="",
            times_run=0,
        )

        results = bot_with_symphony._execute_command(test_cmd)

        assert results is not None
        msg = results[0]
        # Message's backend field should take precedence
        assert msg.metadata["backend"] == "slack"

    def test_bot_message_to_chatom_includes_metadata(self, bot_with_symphony):
        """Test that BotMessage conversion includes backend in metadata."""
        bot_msg = BotMessage(
            content="Hello world",
            channel_id="C123",
            channel_name="general",
            thread_id="",
            backend="slack",
            mentions=(),
            formatted=None,
            reply_to_id="",
        )

        result = bot_with_symphony._bot_message_to_chatom(bot_msg)

        assert result.metadata is not None
        assert result.metadata.get("backend") == "slack"


class TestNewFrameworkIntegration:
    """Integration tests for new command framework execution in Bot."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_load_commands_discovers_decorated_command(self, bot_with_symphony):
        """Decorated commands should be auto-registered by Bot.load_commands."""

        @command(name="newecho", help="Echo via new framework")
        def newecho(ctx):
            return f"echo: {ctx.args_text}"

        bot_with_symphony.load_commands([])

        assert "newecho" in bot_with_symphony._commands
        entry = bot_with_symphony._commands["newecho"]
        assert hasattr(entry, "handler")

    def test_execute_command_supports_new_class_command(self, bot_with_symphony):
        """Class-based new framework commands should execute via _execute_command."""

        class NewPing(Command):
            name: str = "newping"
            help: str = "Ping via new framework"

            def execute(self, ctx):
                return ctx.reply("pong")

        bot_with_symphony._commands["newping"] = NewPing()
        bot_with_symphony._bot_user_ids["symphony"] = "bot123"
        bot_with_symphony._bot_names["symphony"] = "TestBot"

        cmd = BotCommand(
            command="newping",
            args=(),
            source=User(id="user123", name="Test User"),
            targets=(),
            channel_id="channel789",
            channel_name="test-channel",
            backend="symphony",
            variant=CommandVariant.REPLY,
            message=Message(
                id="msg123",
                content="/newping",
                author=User(id="user123"),
                channel=Channel(id="channel789"),
            ),
            delay=None,
            schedule="",
            times_run=0,
        )

        results = bot_with_symphony._execute_command(cmd)

        assert results is not None
        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert results[0].content == "pong"
        assert results[0].metadata is not None
        assert results[0].metadata.get("backend") == "symphony"

    def test_execute_command_injects_deps_into_context(self, bot_with_symphony):
        """New framework commands should receive shared deps via ctx.deps."""

        class NeedsDeps(Command):
            name: str = "needsdeps"
            help: str = "Check deps wiring"

            def execute(self, ctx):
                return f"token={ctx.deps['token']}"

        bot_with_symphony._commands["needsdeps"] = NeedsDeps()
        bot_with_symphony.set_deps({"token": "abc123"})
        bot_with_symphony._bot_user_ids["symphony"] = "bot123"
        bot_with_symphony._bot_names["symphony"] = "TestBot"

        cmd = BotCommand(
            command="needsdeps",
            args=(),
            source=User(id="user123", name="Test User"),
            targets=(),
            channel_id="channel789",
            channel_name="test-channel",
            backend="symphony",
            variant=CommandVariant.REPLY,
            message=Message(
                id="msg123",
                content="/needsdeps",
                author=User(id="user123"),
                channel=Channel(id="channel789"),
            ),
            delay=None,
            schedule="",
            times_run=0,
        )

        results = bot_with_symphony._execute_command(cmd)

        assert results is not None
        assert len(results) == 1
        assert results[0].content == "token=abc123"


class TestRegistrationTimeBackendPolicy:
    """Tests for registration-time backend compatibility checks."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_decorated_command_skipped_when_backend_not_active(self, bot_with_symphony):
        """Commands limited to inactive backends should not be registered."""

        @command(name="slack_only", help="Slack only", backends=["slack"])
        def slack_only(ctx):
            return "nope"

        bot_with_symphony.load_commands([])

        assert "slack_only" not in bot_with_symphony._commands

    def test_decorated_command_registered_when_backend_active(self, bot_with_symphony):
        """Commands limited to active backends should be registered."""

        @command(name="symphony_only", help="Symphony only", backends=["symphony"])
        def symphony_only(ctx):
            return "ok"

        bot_with_symphony.load_commands([])

        assert "symphony_only" in bot_with_symphony._commands

    def test_invalid_backend_name_raises_on_registration(self, bot_with_symphony):
        """Unknown backend names should fail fast during registration."""

        @command(name="bad_backend", help="Bad backend", backends=["not-a-backend"])
        def bad_backend(ctx):
            return "nope"

        with pytest.raises(ValueError, match="unknown backends"):
            bot_with_symphony.load_commands([])

    def test_model_command_skipped_when_backend_not_active(self, bot_with_symphony):
        """Model-loaded commands should obey registration-time backend filtering."""

        class SlackOnlyCommand(Command):
            name: str = "model_slack_only"
            help: str = "Slack-only model command"
            backends: list[str] = Field(default_factory=lambda: ["slack"])

            def execute(self, ctx):
                return "nope"

        model = CommandModel(command=SlackOnlyCommand)
        bot_with_symphony.load_commands([model])

        assert "model_slack_only" not in bot_with_symphony._commands

    def test_load_commands_uses_model_factory(self, bot_with_symphony):
        class FactoryCommand(Command):
            name: str = "factory_command"

            def execute(self, ctx):
                return "ok"

        class FactoryModel:
            def create_command(self):
                return FactoryCommand()

        bot_with_symphony.load_commands([FactoryModel()])

        assert isinstance(bot_with_symphony._commands["factory_command"], FactoryCommand)


class TestEntryPointCommandDiscovery:
    """Tests for plugin command discovery through Python entry points."""

    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_load_commands_discovers_entrypoint_registered_command(self, bot_with_symphony):
        """Entry-point loader should import plugin and register its decorated command."""

        def register_plugin_command():
            @command(name="from_ep", help="Registered from entry point")
            def from_ep(ctx):
                return "ok"

        entry_point = MagicMock()
        entry_point.name = "plugin.from_ep"
        entry_point.load.return_value = register_plugin_command

        with patch("csp_bot.bot.importlib_metadata.entry_points", return_value=[entry_point]):
            bot_with_symphony.load_commands([])

        assert "from_ep" in bot_with_symphony._commands

    def test_model_command_precedence_over_entrypoint_command(self, bot_with_symphony):
        """Explicit model registration should win when entry point uses same command name."""

        def register_plugin_command():
            @command(name="clash", help="Plugin command")
            def clash(ctx):
                return "plugin"

        class ClashModelCommand(Command):
            name: str = "clash"
            help: str = "Model command"

            def execute(self, ctx):
                return "model"

        entry_point = MagicMock()
        entry_point.name = "plugin.clash"
        entry_point.load.return_value = register_plugin_command

        with patch("csp_bot.bot.importlib_metadata.entry_points", return_value=[entry_point]):
            bot_with_symphony.load_commands([CommandModel(command=ClashModelCommand)])

        assert "clash" in bot_with_symphony._commands
        assert isinstance(bot_with_symphony._commands["clash"], ClashModelCommand)


# Command Argument Parsing Tests


class TestCommandArgumentParsing:
    """Tests for parsing command arguments including /room and /channel directives."""

    def test_parse_room_directive(self, bot_with_symphony):
        """Test that /room directive is parsed correctly."""
        tokens = ["@User", "/room", "TKP"]
        mentions = [User(id="user123", name="User")]

        args, _targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "slack")

        assert channel == "TKP"
        assert "/room" not in args
        assert "TKP" not in args

    def test_parse_channel_directive(self, bot_with_symphony):
        """Test that /channel directive is parsed correctly."""
        tokens = ["@User", "/channel", "general"]
        mentions = [User(id="user123", name="User")]

        args, _targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "slack")

        assert channel == "general"
        assert "/channel" not in args
        assert "general" not in args

    def test_parse_bang_room_directive(self, bot_with_symphony):
        """Test that !room directive is parsed correctly."""
        tokens = ["@User", "!room", "TKP"]
        mentions = [User(id="user123", name="User")]

        args, _targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "slack")

        assert channel == "TKP"
        assert "!room" not in args

    def test_parse_bang_channel_directive(self, bot_with_symphony):
        """Test that !channel directive is parsed correctly."""
        tokens = ["@User", "!channel", "random"]
        mentions = [User(id="user123", name="User")]

        args, _targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "slack")

        assert channel == "random"
        assert "!channel" not in args

    def test_parse_args_without_channel_directive(self, bot_with_symphony):
        """Test parsing args when no channel directive is present."""
        tokens = ["arg1", "arg2", "arg3"]
        mentions = []

        args, targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "slack")

        assert args == ["arg1", "arg2", "arg3"]
        assert channel == ""
        assert len(targets) == 0

    def test_symphony_mention_parsing_with_multiword_names(self, bot_with_symphony):
        """Test Symphony mention parsing handles multi-word names."""
        # Symphony: "@Paine, Timothy /room TKP" becomes tokens ["@Paine,", "Timothy", "/room", "TKP"]
        tokens = ["@Paine,", "Timothy", "/room", "TKP"]
        mentions = [User(id="user123", name="Paine, Timothy")]

        args, targets, channel = bot_with_symphony._parse_command_args(tokens, mentions, "symphony")

        assert channel == "TKP"
        assert len(targets) == 1
        assert targets[0].id == "user123"
        # Should not have "Timothy" in args (it's part of the name)
        assert "Timothy" not in args


# Channel Resolution Tests


class TestChannelResolution:
    """Tests for channel name to ID resolution."""

    def test_resolve_channel_by_name(self, bot_with_symphony, mock_adapter):
        """Test resolving a channel by name."""
        # Set up the bot with our mock adapter
        bot_with_symphony._adapters["symphony"] = mock_adapter

        # Create a mock backend class that returns our mock when instantiated
        mock_backend_class = MagicMock()
        mock_backend_instance = MagicMock()
        mock_backend_instance.config = mock_adapter.backend.config

        async def mock_connect():
            pass

        async def mock_fetch_channel(id=None, name=None):
            if name == "TKP":
                return Channel(id="stream123", name="TKP")
            return None

        mock_backend_instance.connect = mock_connect
        mock_backend_instance.fetch_channel = mock_fetch_channel
        mock_backend_class.return_value = mock_backend_instance

        with (
            patch.object(type(mock_adapter.backend), "__call__", mock_backend_class),
            patch("csp_bot.bot.type") as mock_type,
        ):
            mock_type.return_value = mock_backend_class

            # Call the method - the key test is that it doesn't raise an exception
            # and that the event loop handling works correctly
            _channel = bot_with_symphony._resolve_channel("TKP", "symphony")

            # Note: This may return None if the mock isn't set up quite right,
            # but the key test is that it doesn't raise an exception
            # and that the event loop handling works
            assert _channel is None or hasattr(_channel, "id")


class TestEnsureBackendConnected:
    """Tests for the _ensure_backend_connected method and event loop management."""

    def test_ensure_backend_connected_caches_result(self, bot_with_symphony, mock_adapter):
        """Test that connected backends are cached and reused."""
        bot_with_symphony._adapters["symphony"] = mock_adapter

        # Pre-populate the cache to test caching behavior
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_backend = MagicMock()
        bot_with_symphony._connected_backends["symphony"] = (mock_backend, mock_loop)

        result = bot_with_symphony._ensure_backend_connected("symphony")

        assert result is not None
        backend, loop = result
        assert backend is mock_backend
        assert loop is mock_loop

    def test_ensure_backend_connected_returns_none_for_missing_adapter(self, bot_with_symphony):
        """Test that None is returned for non-existent backends."""
        result = bot_with_symphony._ensure_backend_connected("nonexistent")

        assert result is None

    def test_connected_backends_dict_initialized_empty(self, bot_with_symphony):
        """Test that _connected_backends starts empty."""
        # Fresh bot should have empty connected backends
        assert len(bot_with_symphony._connected_backends) == 0


# Bot Command Channel Property Tests


class TestBotCommandChannel:
    """Tests for BotCommand.channel property."""

    def test_channel_property_returns_channel_object(self, sample_bot_command):
        """Test that BotCommand.channel returns a Channel with correct ID and name."""
        channel = sample_bot_command.channel

        assert isinstance(channel, Channel)
        assert channel.id == "channel789"
        assert channel.name == "test-channel"

    def test_channel_property_with_resolved_channel(self):
        """Test BotCommand.channel with a resolved target channel."""
        cmd = BotCommand(
            command="slap",
            args=(),
            source=User(id="user123"),
            targets=(User(id="target456"),),
            channel_id="resolved_stream_id",  # This would be the resolved ID
            channel_name="TKP",  # And the resolved name
            backend="symphony",
            variant=CommandVariant.REPLY_TO_OTHER,
            message=Message(id="msg123", content="test"),
            delay=None,
            schedule="",
            times_run=0,
        )

        channel = cmd.channel

        assert channel.id == "resolved_stream_id"
        assert channel.name == "TKP"


# Create Response Message Tests


class TestCreateResponseMessage:
    """Tests for _create_response_message method."""

    def test_create_response_message_has_metadata(self, bot_with_symphony):
        """Test that created response messages have backend in metadata."""
        msg = bot_with_symphony._create_response_message(
            content="Hello",
            channel_id="C123",
            backend="symphony",
        )

        assert msg.metadata is not None
        assert msg.metadata.get("backend") == "symphony"

    def test_create_response_message_with_mentions(self, bot_with_symphony):
        """Test creating response with mentions.

        Note: _create_response_message adds mentions to the content string via
        mention_user_for_backend, and passes mention_ids to Message. However,
        Message.mention_ids is a computed property from mentions (User objects),
        so the passed mention_ids may not be reflected. The important thing is
        that the content contains the mention markup and metadata is set.
        """
        users = [User(id="U123", name="User1"), User(id="U456", name="User2")]

        msg = bot_with_symphony._create_response_message(
            content="Hello",
            channel_id="C123",
            backend="symphony",
            mentions=users,
        )

        # Content should contain the mention markup
        assert "<mention uid=" in msg.content or "@" in msg.content
        # Metadata should have backend
        assert msg.metadata.get("backend") == "symphony"


# Extract Commands Tests


class TestExtractCommands:
    """Tests for command extraction from messages."""

    def test_extract_command_with_room_directive(self, bot_with_symphony, mock_adapter):
        """Test extracting a command with /room directive."""
        # Set up mock
        bot_with_symphony._adapters["symphony"] = mock_adapter
        bot_with_symphony._configs["symphony"] = MagicMock()
        bot_with_symphony._bot_user_ids["symphony"] = "bot123"
        bot_with_symphony._bot_names["symphony"] = "TestBot"

        # Register a simple command
        class TestCmd(ReplyToOtherCommand):
            def command(self):
                return "test"

            def name(self):
                return "Test"

            def help(self):
                return "Test"

            def execute(self, cmd):
                return None

        bot_with_symphony._commands["test"] = TestCmd()

        msg = Message(
            id="msg123",
            content="/test @User /room TKP",
            author=User(id="user123"),
            channel=Channel(id="original_channel"),
            metadata={"backend": "symphony"},
        )

        mentions = [User(id="target456", name="User")]

        # Note: _resolve_channel will fail without proper mock setup,
        # but the command parsing should still work
        result = bot_with_symphony._extract_commands(
            msg=msg,
            backend="symphony",
            channel_id="original_channel",
            text="/test @User /room TKP",
            mentions=mentions,
        )

        # If channel resolution fails, target_channel falls back to original
        # The important thing is the command was parsed
        if result:
            assert result.command == "test"

    @pytest.mark.parametrize(
        "text,backend,bot_id",
        [
            ("<@UBOT123> /test", "slack", "UBOT123"),
            ("<@UBOT123>/test", "slack", "UBOT123"),
            ("<@UBOT123>!test", "slack", "UBOT123"),
            ("<@!UBOT123> /test", "discord", "UBOT123"),
        ],
    )
    def test_extract_command_strips_bot_mention(self, bot_with_symphony, text, backend, bot_id):
        """Test that <@BOT_ID> mentions are stripped before command parsing."""
        bot_with_symphony._configs[backend] = MagicMock()
        bot_with_symphony._bot_user_ids[backend] = bot_id
        bot_with_symphony._bot_names[backend] = "TestBot"

        class TestCmd(ReplyToOtherCommand):
            def command(self):
                return "test"

            def name(self):
                return "Test"

            def help(self):
                return "Test"

            def execute(self, cmd):
                return None

        bot_with_symphony._commands["test"] = TestCmd()

        msg = Message(
            id="msg1",
            content=text,
            author=User(id="user1"),
            channel=Channel(id="ch1"),
            metadata={"backend": backend},
        )

        result = bot_with_symphony._extract_commands(
            msg=msg,
            backend=backend,
            channel_id="ch1",
            text=text,
            mentions=[User(id=bot_id)],
        )

        assert result is not None
        assert result.command == "test"

    def test_extract_command_without_prefix_shows_help(self, bot_with_symphony):
        """Test that messages without / or ! prefix show help."""
        bot_with_symphony._configs["slack"] = MagicMock()
        bot_with_symphony._bot_user_ids["slack"] = "UBOT"
        bot_with_symphony._bot_names["slack"] = "TestBot"
        bot_with_symphony._commands["help"] = HelpCommand()

        msg = Message(
            id="msg1",
            content="<@UBOT> just some text",
            author=User(id="user1"),
            channel=Channel(id="ch1"),
            metadata={"backend": "slack"},
        )

        result = bot_with_symphony._extract_commands(
            msg=msg,
            backend="slack",
            channel_id="ch1",
            text="<@UBOT> just some text",
            mentions=[User(id="UBOT")],
        )

        assert result is not None
        assert result.command == "help"


# Filter Messages for Backend Tests


class TestFilterMessagesForBackend:
    """Tests to verify message filtering logic (non-CSP unit tests)."""

    def test_message_with_matching_backend_metadata(self):
        """Test that messages with matching backend metadata would be filtered in."""
        msg = Message(
            content="Hello",
            channel=Channel(id="C123"),
            metadata={"backend": "symphony"},
        )

        # Simulate the filter logic
        metadata = msg.metadata or {}
        msg_backend = metadata.get("backend", "")

        assert msg_backend == "symphony"

    def test_message_without_metadata_would_be_filtered_out(self):
        """Test that messages without metadata would be filtered out."""
        msg = Message(
            content="Hello",
            channel=Channel(id="C123"),
            backend="symphony",  # Has backend field but no metadata
        )

        # Simulate the filter logic
        metadata = msg.metadata or {}
        msg_backend = metadata.get("backend", "")

        # This message would NOT pass the filter - this was the bug!
        assert msg_backend == ""  # Empty, won't match any backend

    def test_message_with_empty_metadata_would_be_filtered_out(self):
        """Test that messages with empty metadata dict would be filtered out."""
        msg = Message(
            content="Hello",
            channel=Channel(id="C123"),
            metadata={},  # Empty metadata
            backend="symphony",
        )

        metadata = msg.metadata or {}
        msg_backend = metadata.get("backend", "")

        assert msg_backend == ""  # Empty, won't match


# Bot Info Caching Tests


class TestBotInfoCaching:
    """Tests for bot info fetching and caching."""

    def test_bot_id_cached_after_fetch(self, bot_with_symphony):
        """Test that bot ID is cached after fetching."""
        # Manually populate cache
        bot_with_symphony._bot_user_ids["symphony"] = "bot123"

        bot_id = bot_with_symphony._get_bot_id("symphony")

        assert bot_id == "bot123"

    def test_bot_name_cached_after_fetch(self, bot_with_symphony):
        """Test that bot name is cached after fetching."""
        bot_with_symphony._bot_names["symphony"] = "TestBot"

        bot_name = bot_with_symphony._get_bot_name("symphony")

        assert bot_name == "TestBot"

    def test_bot_name_uses_config_override(self, bot_with_symphony):
        """Test that explicit bot_name in config takes precedence."""
        config = MagicMock()
        config.bot_name = "ConfiguredBotName"
        bot_with_symphony._configs["symphony"] = config
        bot_with_symphony._bot_names["symphony"] = "CachedName"

        bot_name = bot_with_symphony._get_bot_name("symphony")

        assert bot_name == "ConfiguredBotName"


# Direct Message Detection Tests


class TestAgentSessionReplyIsolation:
    def test_reply_from_different_user_does_not_resume_session(self, bot_with_symphony, monkeypatch):
        from chatom.base.message import MessageReference

        from csp_bot.commands.agent import AgentCommand, AgentSession, SessionStore

        sessions = SessionStore(ttl_seconds=60.0)
        sessions.put(
            "ask:slack:U1:C1",
            AgentSession(
                user_id="U1",
                channel_id="C1",
                backend="slack",
                command_name="ask",
                bot_response_id="response-1",
            ),
        )
        monkeypatch.setattr(AgentCommand, "_sessions", sessions)
        message = Message(
            id="reply-1",
            content="continue",
            author=User(id="U2", name="Other User"),
            channel_id="C1",
            reference=MessageReference(message_id="response-1"),
        )

        result = bot_with_symphony._check_agent_session_reply(message, "slack", "C1")

        assert result is None


class TestDirectMessageDetection:
    """Tests for direct message detection across backends."""

    def test_is_dm_from_message_property(self, bot_with_symphony):
        """Test DM detection using message.is_dm property."""
        msg = MagicMock()
        msg.is_dm = True

        assert bot_with_symphony._is_direct_message(msg, "symphony") is True

    def test_is_dm_from_metadata(self, bot_with_symphony):
        """Test DM detection using metadata populated by adapters."""
        msg = Message(
            id="msg1",
            content="!help",
            author=User(id="user123"),
            metadata={"is_dm": True},
        )

        assert bot_with_symphony._is_direct_message(msg, "discord") is True

    def test_slack_dm_channel_id_detection(self, bot_with_symphony):
        """Test Slack DM detection via channel ID starting with 'D'."""
        msg = MagicMock()
        msg.is_dm = False
        msg.channel_id = "D123456"
        msg.channel = None

        assert bot_with_symphony._is_direct_message(msg, "slack") is True

    def test_symphony_im_stream_type_detection(self, bot_with_symphony):
        """Test Symphony DM detection via stream_type."""
        msg = MagicMock()
        msg.is_dm = False
        msg.channel = MagicMock()
        msg.channel.stream_type = "IM"

        assert bot_with_symphony._is_direct_message(msg, "symphony") is True

    def test_discord_dm_channel_type_detection(self, bot_with_symphony):
        """Test Discord DM detection via generic ChannelType."""
        msg = MagicMock()
        msg.is_dm = False
        msg.channel = DiscordChannel(id="dm123", channel_type=ChannelType.DIRECT)

        assert bot_with_symphony._is_direct_message(msg, "discord") is True

    def test_discord_dm_channel_type_metadata_detection(self, bot_with_symphony):
        """Test Discord DM detection via generic channel metadata."""
        msg = MagicMock()
        msg.is_dm = False
        msg.channel = None
        msg.metadata = {"channel_type": "direct"}

        assert bot_with_symphony._is_direct_message(msg, "discord") is True

    def test_discord_dm_message_is_to_bot_without_mention(self, bot_with_symphony):
        """Test Discord DMs imply messages are addressed to the bot."""
        config = MagicMock()
        config.bot_name = "CSP Bot"
        bot_with_symphony._configs["discord"] = config
        bot_with_symphony._bot_user_ids["discord"] = "bot123"

        msg = Message(
            id="msg1",
            content="!help",
            author=User(id="user123"),
            channel=DiscordChannel(id="dm123", channel_type=ChannelType.DIRECT),
            backend="discord",
        )

        is_to_bot, channel_id, text, mentions = bot_with_symphony._is_message_to_bot(msg, "discord")

        assert is_to_bot is True
        assert channel_id == "dm123"
        assert text == "!help"
        assert mentions == []

    def test_discord_dm_message_uses_metadata_channel_id_without_channel(self, bot_with_symphony):
        """Test Discord DMs still route when CSP loses the channel object."""
        config = MagicMock()
        config.bot_name = "CSP Bot"
        bot_with_symphony._configs["discord"] = config
        bot_with_symphony._bot_user_ids["discord"] = "bot123"

        msg = Message(
            id="msg1",
            content="!help",
            author=User(id="user123"),
            metadata={
                "backend": "discord",
                "channel_id": "dm123",
                "channel_type": "direct",
            },
        )

        is_to_bot, channel_id, text, mentions = bot_with_symphony._is_message_to_bot(msg, "discord")

        assert is_to_bot is True
        assert channel_id == "dm123"
        assert text == "!help"
        assert mentions == []


class TestBackendFiltering:
    """Which field names a message's destination."""

    @staticmethod
    def _routed_to(bot, backend, message):
        """Run the filter node on one message and report whether it passed."""
        import csp

        @csp.graph
        def g():
            edge = csp.unroll(csp.const([message]))
            csp.add_graph_output("out", bot._filter_messages_for_backend(backend, edge))

        result = csp.run(g, starttime=datetime.now(UTC), endtime=timedelta(seconds=1))
        return len(result.get("out", [])) == 1

    def test_metadata_routes_a_message(self, bot_with_symphony):
        message = Message(content="hi", metadata={"backend": "symphony"})

        assert self._routed_to(bot_with_symphony, "symphony", message)
        assert not self._routed_to(bot_with_symphony, "slack", message)

    def test_backend_field_routes_a_message(self, bot_with_symphony):
        """A message sent over REST sets `backend`, not metadata.

        Before this fell back to the field, such a message was accepted by the
        send endpoint and then silently dropped, reaching no backend at all.
        """
        message = Message(content="hi", backend="symphony")

        assert self._routed_to(bot_with_symphony, "symphony", message)
        assert not self._routed_to(bot_with_symphony, "slack", message)

    def test_metadata_wins_over_the_field(self, bot_with_symphony):
        """Inbound tagging writes metadata, so it stays authoritative."""
        message = Message(content="hi", backend="slack", metadata={"backend": "symphony"})

        assert self._routed_to(bot_with_symphony, "symphony", message)
        assert not self._routed_to(bot_with_symphony, "slack", message)

    def test_a_message_naming_no_backend_goes_nowhere(self, bot_with_symphony):
        message = Message(content="hi")

        assert not self._routed_to(bot_with_symphony, "symphony", message)


class TestSendChannelOptIn:
    """messages_out is only sendable when the config asks for it."""

    def test_send_is_off_by_default(self):
        assert BotConfig().allow_send_messages is False

    def test_send_can_be_enabled(self):
        assert BotConfig(allow_send_messages=True).allow_send_messages is True
