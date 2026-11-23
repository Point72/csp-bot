from concurrent.futures import Future
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from chatom import Message, User

from csp_bot import BaseCommandModel, BotCommand, ReplyCommand

from .test_schedule_execution import NOW, _bot, _parse, _run


def _echo(*, user="user", backend="slack", room="room", text="same"):
    return BotCommand.model_validate(
        {
            "command": "echo",
            "args": (text,),
            "source": User(id=user),
            "targets": (),
            "backend": backend,
            "channel_id": room,
            "channel_name": room,
            "message": Message(content="/echo same"),
            "times_run": 0,
            "schedule": "",
        }
    )


def test_fourth_execution_is_blocked_and_reports_once():
    bot = _bot()
    results = [bot._execute_command(_echo(), now=NOW + timedelta(milliseconds=100 * i)) for i in range(8)]
    assert [result[0].content for result in results[:3]] == ["same"] * 3
    assert "more than 3" in results[3][0].content
    assert all(result is None for result in results[4:])


def test_rolling_window_allows_execution_when_oldest_entry_expires():
    bot = _bot()
    for seconds in [0, 1, 2]:
        assert bot._execute_command(_echo(), now=NOW + timedelta(seconds=seconds))[0].content == "same"
    assert "more than 3" in bot._execute_command(_echo(), now=NOW + timedelta(seconds=4.999))[0].content
    assert bot._execute_command(_echo(), now=NOW + timedelta(seconds=5))[0].content == "same"
    assert len(bot._recent_commands) == 1
    bot._execute_command(_echo(text="other"), now=NOW + timedelta(seconds=20))
    assert len(bot._recent_commands) == 1


@pytest.mark.parametrize("change", [{"user": "other"}, {"backend": "discord"}, {"room": "elsewhere"}, {"text": "different"}])
def test_burst_identity_includes_user_backend_room_and_arguments(change):
    bot = _bot()
    for _ in range(3):
        bot._execute_command(_echo(), now=NOW)
    assert bot._execute_command(_echo(**change), now=NOW)[0].content in {"same", "different"}


def test_gate_checks_before_preparation_and_does_not_count_execution_twice():
    prepared = []

    class PreparedCommand(ReplyCommand):
        def command(self):
            return "prepared"

        def name(self):
            return "Prepared"

        def help(self):
            return "Prepared"

        def preexecute(self, command):
            prepared.append(command.id)
            return command.copy()

        def execute(self, command):
            return Message(content="ran", channel=command.channel)

    bot = _bot()
    bot.load_commands([BaseCommandModel(command=PreparedCommand)])
    with patch("csp_bot.bot.datetime", wraps=datetime) as clock:
        clock.now.return_value = NOW
        results = [bot._execute_command(_parse(bot, "/prepared"), now=NOW) for _ in range(4)]
    assert len(prepared) == 3
    assert [result[0].content for result in results[:3]] == ["ran"] * 3
    assert "more than 3" in results[3][0].content


def test_gate_drops_recurring_schedule_and_pending_alarm():
    bot = _bot()
    command = _echo()
    command.schedule = "* * * * * *"
    command.prepare_on_run = True
    record = bot._store_scheduled_command(command, NOW + timedelta(seconds=1))
    results = _run(bot, [(timedelta(), command)], timedelta(seconds=8))
    assert sum(message.content == "same" for _, message in results) == 3
    errors = [message for _, message in results if "more than 3" in message.content]
    assert len(errors) == 1
    assert record.schedule_id in errors[0].content
    assert bot._schedule_store.records() == []


def test_gate_removes_matching_delays_but_preserves_other_users_jobs():
    bot = _bot()
    for index in range(6):
        command = _echo()
        command.delay = NOW + timedelta(milliseconds=100 * (index + 1))
        command.prepare_on_run = True
        bot._store_scheduled_command(command, command.delay)
    other = _echo(user="other", text="other user")
    other.delay = NOW + timedelta(seconds=1)
    bot._store_scheduled_command(other, other.delay)
    results = _run(bot, [], timedelta(seconds=2))
    assert sum(message.content == "same" for _, message in results) == 3
    assert sum("more than 3" in message.content for _, message in results) == 1
    assert sum(message.content == "other user" for _, message in results) == 1
    assert bot._schedule_store.records() == []


def test_gate_stops_self_rescheduling_commands():
    calls = []

    class LoopCommand(ReplyCommand):
        def command(self):
            return "loop"

        def name(self):
            return "Loop"

        def help(self):
            return "Loop"

        def execute(self, command):
            calls.append(command.id)
            command.delay = NOW + timedelta(milliseconds=50 * len(calls))
            return command

    bot = _bot()
    bot.load_commands([BaseCommandModel(command=LoopCommand)])
    command = _echo()
    command.command = "loop"
    results = _run(bot, [(timedelta(), command)], timedelta(seconds=1))
    assert len(calls) == 3
    assert sum("more than 3" in message.content for _, message in results) == 1
    assert bot._schedule_store.records() == []


def test_gate_cancels_in_flight_agent_work(monkeypatch):
    from csp_bot.commands.agent import AgentCommand, AgentCommandModel

    class CancellableAgent(AgentCommand):
        def command(self):
            return "agent"

        def name(self):
            return "Agent"

        def help(self):
            return "Agent"

        def build_agent(self, command):
            raise AssertionError("No model calls")

        def build_prompt(self, command):
            return ""

    bot = _bot()
    bot.load_commands([AgentCommandModel(command=CancellableAgent)])
    runner = bot._commands["agent"]
    command = _echo()
    command.command = "agent"
    other_room = command.copy()
    other_room.channel_id = "another-room"
    assert runner._command_key(other_room) != runner._command_key(command)
    other_backend = command.copy()
    other_backend.backend = "discord"
    assert runner._command_key(other_backend) != runner._command_key(command)
    key = runner._command_key(command)
    future = Future()
    control = Mock()
    monkeypatch.setattr(AgentCommand, "_futures", {key: future})
    monkeypatch.setattr(AgentCommand, "_run_controls", {key: control})
    for index in range(4):
        result = bot._execute_command(command.copy(), now=NOW + timedelta(milliseconds=index * 100))
    assert "more than 3" in result[0].content
    assert future.cancelled()
    control.cancel.assert_called_once()
    assert runner._futures == {} and runner._run_controls == {}


def test_command_names_and_mention_targets_have_separate_budgets():
    bot = _bot()
    for _ in range(3):
        bot._execute_command(_echo(), now=NOW)
    other_command = _echo()
    other_command.command = "help"
    assert "more than 3" not in bot._execute_command(other_command, now=NOW)[0].content
    other_targets = _echo()
    other_targets.targets = (User(id="recipient"),)
    assert "more than 3" not in bot._execute_command(other_targets, now=NOW)[0].content
