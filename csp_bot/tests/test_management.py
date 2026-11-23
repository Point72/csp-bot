import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chatom import Channel, Message, User
from chatom.base.capabilities import BackendCapabilities, Capability

from csp_bot import Bot, BotCommand, BotConfig
from csp_bot.bot_config import ManagementConfig
from csp_bot.commands.echo import EchoCommandModel
from csp_bot.commands.framework import Command, CommandModel
from csp_bot.commands.help import HelpCommandModel
from csp_bot.commands.management import LogsCommandModel, MessagesCommandModel, WhoamiCommandModel


def _command(name, args=(), *, user="admin", backend="slack", channel="control", origin=None, reply_to_id=""):
    return BotCommand.model_validate(
        {
            "command": name,
            "args": args,
            "backend": backend,
            "channel_id": channel,
            "channel_name": channel,
            "source": User(id=user),
            "targets": (),
            "message": Message(
                content=f"/{name}",
                channel=Channel(id=origin or channel),
                author=User(id=user),
                reply_to=Message(id=reply_to_id) if reply_to_id else None,
            ),
        }
    )


@pytest.fixture
def bot(tmp_path):
    log_file = tmp_path / "bot.log"
    log_file.write_text("first\nsecond\nthird\n")
    config = BotConfig(
        management=ManagementConfig(
            admin_users={"slack": {"admin"}},
            log_channels={"slack": {"control"}},
            message_channels={"slack": {"control"}},
            log_file=str(log_file),
        )
    )
    instance = Bot(config=config)
    instance.load_commands([WhoamiCommandModel(), LogsCommandModel(), MessagesCommandModel()])
    return instance


def _content(bot, command):
    return bot._execute_command(command)[0].content


@pytest.mark.parametrize("user,backend", [("member", "slack"), ("admin", "discord"), ("", "slack")])
def test_admin_identity_is_scoped_to_backend(bot, user, backend):
    with patch.object(Bot, "_ensure_backend_connected") as connect:
        assert "Admin access required" in _content(bot, _command("messages", ("delete", "target"), user=user, backend=backend))
    connect.assert_not_called()


def test_empty_configuration_denies_management(bot):
    bot.config.management = ManagementConfig()
    assert "Admin access required" in _content(bot, _command("logs"))


def test_display_name_cannot_grant_admin(bot):
    command = _command("logs", user="member")
    command.source = User(id="member", name="admin", handle="admin")
    assert "Admin access required" in _content(bot, command)


def test_unauthorized_preexecute_is_not_called(bot):
    command = bot._commands["logs"]
    with (
        patch.object(command, "preexecute") as preexecute,
        patch.object(Bot, "_get_bot_name", return_value="helper"),
        patch.object(Bot, "_get_bot_id", return_value="bot"),
    ):
        msg = Message(content="/logs", channel=Channel(id="control"), author=User(id="member"))
        parsed = bot._extract_commands(msg, "slack", "control", msg.content, [])
    preexecute.assert_not_called()
    assert "Admin access required" in _content(bot, parsed)


def test_privilege_is_rechecked_at_execution(bot):
    command = _command("logs")
    bot.config.management.admin_users.clear()
    assert "Admin access required" in _content(bot, command)


@pytest.mark.parametrize("name,args", [("logs", ()), ("messages", ("delete", "target"))])
def test_management_rejects_channel_redirects(bot, name, args):
    with patch.object(Bot, "_ensure_backend_connected") as connect:
        response = bot._execute_command(_command(name, args, origin="control", channel="public"))[0]
    assert "redirect" in response.content
    assert response.channel_id == "control"
    connect.assert_not_called()


def test_denial_replies_to_origin(bot):
    response = bot._execute_command(_command("logs", user="member", origin="control", channel="public"))[0]
    assert response.channel_id == "control"
    assert response.metadata["backend"] == "slack"


@pytest.mark.parametrize("user,role", [("admin", "admin"), ("member", "user")])
def test_whoami_shows_stable_identity(bot, user, role):
    content = _content(bot, _command("whoami", user=user))
    assert f"User ID: {user}" in content
    assert "Backend: slack" in content
    assert "Channel ID: control" in content
    assert f"Role: {role}" in content


def test_logs_return_requested_tail(bot):
    content = _content(bot, _command("logs", ("2",)))
    assert "second" in content and "third" in content
    assert "first" not in content


def test_logs_require_allowed_destination(bot):
    assert "configured log channel" in _content(bot, _command("logs", channel="public"))


@pytest.mark.parametrize("args", [("0",), ("201",), ("-1",), ("/etc/passwd",), ("10", "anything")])
def test_logs_validate_arguments(bot, args):
    assert "Usage: /logs" in _content(bot, _command("logs", args))


def test_logs_are_bounded(bot):
    from pathlib import Path

    Path(bot.config.management.log_file).write_text("old\n" + "x" * 100_000 + "\nlast\n")
    content = _content(bot, _command("logs", ("200",)))
    assert len(content) < 4000
    assert "last" in content and "old" not in content


def test_logs_handle_unconfigured_or_missing_file(bot):
    bot.config.management.log_file = None
    assert "not configured" in _content(bot, _command("logs"))
    bot.config.management.log_file = "/nonexistent/csp_bot.log"
    assert "unavailable" in _content(bot, _command("logs"))


@pytest.fixture
def backend(bot):
    backend = MagicMock()
    backend.capabilities = BackendCapabilities(capabilities=frozenset({Capability.DELETING, Capability.EDITING}))
    backend.normalize_channel_id.side_effect = lambda value: value
    backend.get_bot_info = AsyncMock(return_value=User(id="bot"))
    backend.fetch_messages = AsyncMock(return_value=[Message(id="target", channel=Channel(id="control"), author=User(id="bot"))])
    backend.delete_message = AsyncMock()
    backend.edit_message = AsyncMock(return_value=Message(id="edited", content="replacement"))
    loop = asyncio.new_event_loop()
    bot._connected_backends["slack"] = (backend, loop)
    room = Channel(id="target-room", name="general")
    bot._record_sent_message("slack", Message(channel=room, content="original"), Message(id="target", channel=room, content="original"))
    backend.catalog_id = bot._sent_messages.page("slack")[0][0].id
    yield backend
    loop.close()


def test_messages_list_includes_room_native_and_catalog_ids(bot, backend):
    content = _content(bot, _command("messages"))
    assert backend.catalog_id in content
    assert "Native ID: target" in content
    assert "general (target-room)" in content
    assert "original" in content


def test_messages_require_private_management_destination(bot, backend):
    assert "configured message management channel" in _content(bot, _command("messages", channel="public"))
    assert "original" not in _content(bot, _command("messages", channel="public"))


def test_messages_reject_unknown_cursor(bot):
    assert "Unknown or expired page cursor" in _content(bot, _command("messages", ("list", "unknown")))


@pytest.mark.parametrize("platform", ["slack", "discord", "symphony", "telegram"])
def test_catalog_delete_does_not_require_history(bot, backend, platform):
    bot.config.management.admin_users[platform] = {"admin"}
    bot.config.management.message_channels[platform] = {"control"}
    bot._connected_backends[platform] = bot._connected_backends["slack"]
    channel = Channel(id="native-room", name="room")
    bot._record_sent_message(platform, Message(channel=channel), Message(id="native-id", channel=channel))
    record = next(record for record in bot._sent_messages.page(platform)[0] if record.native_id == "native-id")
    assert "Deleted" in _content(bot, _command("messages", ("delete", record.id), backend=platform))
    backend.delete_message.assert_awaited_once_with(message="native-id", channel=Channel(id="native-room"))
    backend.fetch_messages.assert_not_awaited()
    assert bot._sent_messages.get(record.id, platform).deleted


@pytest.mark.parametrize("platform", ["slack", "discord", "symphony", "telegram"])
def test_catalog_edit_routes_to_native_room_and_updates_preview(bot, backend, platform):
    from chatom.format import FormattedMessage, Text

    bot.config.management.admin_users[platform] = {"admin"}
    bot.config.management.message_channels[platform] = {"control"}
    bot._connected_backends[platform] = bot._connected_backends["slack"]
    room = Channel(id="native-room")
    bot._record_sent_message(platform, Message(channel=room), Message(id="native-id", channel=room))
    record = next(record for record in bot._sent_messages.page(platform)[0] if record.native_id == "native-id")
    args = ("edit", record.id, "new", "<text>")
    assert "Edited" in _content(bot, _command("messages", args, backend=platform))
    rendered = FormattedMessage(content=[Text(content="new <text>")]).render_for(platform)
    backend.edit_message.assert_awaited_once_with(message="native-id", content=rendered, channel=room)
    updated = bot._sent_messages.get(record.id, platform)
    assert updated.preview == "new <text>" and updated.native_id == "edited"
    assert updated.posted_at == record.posted_at


@pytest.mark.parametrize("action,args", [("delete", ()), ("edit", ("replacement",))])
def test_messages_check_backend_capability(bot, backend, action, args):
    backend.capabilities = BackendCapabilities(capabilities=frozenset())
    assert "not support" in _content(bot, _command("messages", (action, backend.catalog_id, *args)))
    backend.delete_message.assert_not_awaited()
    backend.edit_message.assert_not_awaited()


def test_delete_reports_backend_failure_without_marking_deleted(bot, backend):
    backend.delete_message.side_effect = PermissionError("not allowed")
    assert "action failed" in _content(bot, _command("messages", ("delete", backend.catalog_id)))
    assert not bot._sent_messages.get(backend.catalog_id, "slack").deleted


@pytest.mark.parametrize("args", [("delete",), ("delete", "one", "two"), ("edit", "one"), ("list", "one", "two"), ("unknown",)])
def test_messages_reject_invalid_syntax(bot, backend, args):
    assert "Usage: /messages" in _content(bot, _command("messages", args))
    backend.delete_message.assert_not_awaited()
    backend.edit_message.assert_not_awaited()


def test_messages_refuse_native_ids_and_other_backend_records(bot, backend):
    channel = Channel(id="elsewhere")
    bot._record_sent_message("discord", Message(channel=channel), Message(id="other", channel=channel))
    other_id = bot._sent_messages.page("discord")[0][0].id
    for message_id in ["target", other_id]:
        assert "Unknown message ID" in _content(bot, _command("messages", ("delete", message_id)))
    backend.delete_message.assert_not_awaited()


def test_deleted_records_cannot_be_edited_or_deleted_again(bot, backend):
    assert "Deleted" in _content(bot, _command("messages", ("delete", backend.catalog_id)))
    assert "already marked deleted" in _content(bot, _command("messages", ("delete", backend.catalog_id)))
    assert "already marked deleted" in _content(bot, _command("messages", ("edit", backend.catalog_id, "text")))
    backend.delete_message.assert_awaited_once()
    backend.edit_message.assert_not_awaited()


def test_messages_report_success_if_only_catalog_update_fails(bot, backend):
    with patch.object(bot._sent_messages, "update", side_effect=OSError("storage offline")):
        content = _content(bot, _command("messages", ("delete", backend.catalog_id)))
    assert "succeeded" in content and "could not be updated" in content
    backend.delete_message.assert_awaited_once()


@pytest.mark.parametrize("user,available", [("admin", True), ("member", False)])
def test_agent_deletion_uses_management_admins(bot, backend, user, available):
    from csp_bot.commands.agent import AgentCommand, AgentCommandModel

    class TestAgent(AgentCommand):
        def command(self):
            return "test-agent"

        def name(self):
            return "Test"

        def help(self):
            return "Test"

        def build_agent(self, command):
            raise AssertionError("No model call expected")

        def build_prompt(self, command):
            return ""

    bot.load_commands([AgentCommandModel(command=TestAgent)])
    runner = bot._commands["test-agent"]
    with patch.object(AgentCommand, "_backends", {"slack": backend}):
        toolset = runner.build_toolset(_command("test-agent", user=user))
    assert ("delete_message" in toolset.tool_definitions()) is available
    if available:
        backend.fetch_channel = AsyncMock(return_value=Channel(id="control"))
        backend.fetch_channel_members = AsyncMock(return_value=[User(id="admin")])
        backend.send_message = AsyncMock(return_value=Message(id="agent-sent", channel=Channel(id="control"), content="agent text"))
        asyncio.run(toolset.call("send_message", {"channel": {"id": "control"}, "content": "agent text"}))
        assert any(record.native_id == "agent-sent" for record in bot._sent_messages.page("slack")[0])
        bot.config.management = ManagementConfig()
        with patch.object(AgentCommand, "_backends", {"slack": backend}):
            assert "delete_message" not in runner.build_toolset(_command("test-agent", user=user)).tool_definitions()
    if not available:
        with pytest.raises(ValueError, match="not available"):
            asyncio.run(toolset.call("delete_message", {"channel": {"id": "control"}, "message_id": "target"}))
        backend.delete_message.assert_not_awaited()


def _bot_with(bot, models):
    instance = Bot(config=bot.config)
    instance.load_commands(models)
    return instance


def test_config_can_make_any_command_admin_only(bot):
    flagged = _bot_with(bot, [EchoCommandModel(admin=True)])
    assert "Admin access required" in _content(flagged, _command("echo", ("hi",), user="member"))
    assert "Admin access required" not in _content(flagged, _command("echo", ("hi",)))


def test_config_can_open_an_admin_command(bot):
    opened = _bot_with(bot, [LogsCommandModel(admin=False)])
    assert "Admin access required" not in _content(opened, _command("logs", user="member"))


def test_help_lists_admin_only_commands_to_admins(bot):
    flagged = _bot_with(bot, [HelpCommandModel(), EchoCommandModel(admin=True)])
    assert "/echo" not in _content(flagged, _command("help", user="member"))
    assert "/echo" in _content(flagged, _command("help"))


def test_config_can_make_a_framework_command_admin_only(bot):
    class Greet(Command):
        name: str = "greet"

        def execute(self, ctx):
            return "hi"

    flagged = _bot_with(bot, [CommandModel(command=Greet, admin=True)])
    assert "Admin access required" in _content(flagged, _command("greet", user="member"))
