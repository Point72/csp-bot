"""Tests for help command rendering."""

from types import SimpleNamespace

import pytest
from chatom import Message, User

from csp_bot.commands.echo import EchoCommand
from csp_bot.commands.framework import Command, clear_registry, command, get_registered_commands
from csp_bot.commands.help import HelpCommand
from csp_bot.commands.legacy import LegacyCommandAdapter
from csp_bot.structs import BotCommand, CommandVariant


def _command(backend: str) -> BotCommand:
    return BotCommand(
        command="help",
        args=(),
        source=User(id="user1"),
        targets=(),
        channel_id="channel1",
        channel_name="general",
        backend=backend,
        variant=CommandVariant.REPLY,
        message=Message(id="msg1", content="!help"),
        delay=None,
        schedule="",
        times_run=0,
    )


def _commands():
    return {
        "ask": SimpleNamespace(name="Ask", help="Ask <anything> & get an answer", backends=[]),
        "help": SimpleNamespace(name="Help", help="Get help with bot commands", backends=[]),
    }


def test_help_discord_uses_list_not_markdown_table() -> None:
    result = HelpCommand().execute(_command("discord"), _commands())

    assert result.content.startswith("**Bot Commands Help**")
    assert "- `/ask` **Ask**: Ask <anything> & get an answer" in result.content
    assert "| Command" not in result.content


def test_help_slack_uses_list_not_code_block_table() -> None:
    result = HelpCommand().execute(_command("slack"), _commands())

    assert result.content.startswith("*Bot Commands Help*")
    assert "- `/ask` *Ask*: Ask <anything> & get an answer" in result.content
    assert "```" not in result.content


def test_help_telegram_uses_supported_html_list() -> None:
    result = HelpCommand().execute(_command("telegram"), _commands())

    assert result.content.startswith("<b>Bot Commands Help</b>")
    assert "- <code>/ask</code> <b>Ask</b>: Ask &lt;anything&gt; &amp; get an answer" in result.content
    assert "<table>" not in result.content


def test_help_symphony_keeps_table_rendering() -> None:
    result = HelpCommand().execute(_command("symphony"), _commands())

    assert "<h3>Bot Commands Help</h3>" in result.content
    assert "<table>" in result.content


@pytest.mark.parametrize("backend", ["discord", "slack", "telegram", "symphony"])
@pytest.mark.parametrize("runner_kind", ["base", "adapter", "metadata", "decorated", "stateful"])
def test_help_renders_optional_usage_after_description(backend, runner_kind):
    class UsageCommand(EchoCommand):
        def help(self) -> str:
            return "Echo a message."

        def usage(self) -> str:
            return "/echo message"

    if runner_kind == "base":
        runner = UsageCommand()
    elif runner_kind == "adapter":
        runner = LegacyCommandAdapter(UsageCommand())
    elif runner_kind == "metadata":
        runner = SimpleNamespace(name="Echo", help="Echo a message.", usage="/echo message", backends=[])
    elif runner_kind == "stateful":
        runner = Command(name="Echo", help="Echo a message.", usage="/echo message")
    else:
        clear_registry()
        try:

            @command(name="echo", help="Echo a message.", usage="/echo message")
            def echo(context):
                return context.args_text

            runner = get_registered_commands()["echo"]
        finally:
            clear_registry()

    result = HelpCommand().execute(_command(backend), {"echo": runner})
    assert "Echo a message. Syntax: /echo message" in result.content
    assert result.content.count("Syntax:") == 1


def test_help_only_base_command_keeps_existing_output():
    runner = EchoCommand()
    assert runner.usage() == ""
    result = HelpCommand().execute(_command("discord"), {"echo": runner})
    assert result.content.endswith(f"- `/echo` **{runner.name()}**: {runner.help()}")


def test_help_renders_usage_without_description():
    runner = Command(name="Echo", usage="/echo message")
    result = HelpCommand().execute(_command("discord"), {"echo": runner})
    assert result.content.endswith("- `/echo` **Echo**: Syntax: /echo message")
