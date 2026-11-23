from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import csp
import pytest
from chatom import Channel, Message, User

from csp_bot import BaseCommandModel, Bot, BotCommand, BotConfig, Command, CommandModel, ReplyCommand
from csp_bot.commands import DelayCommandModel, EchoCommandModel, HelpCommandModel, ScheduleCommandModel
from csp_bot.persistence import FsspecStateStore

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _bot():
    bot = Bot(config=BotConfig(ratelimit_seconds=0.01))
    bot.load_commands([ScheduleCommandModel(), DelayCommandModel(), EchoCommandModel(), HelpCommandModel()])
    return bot


def _parse(bot, text, user="user"):
    message = Message(content=text, author=User(id=user), channel=Channel(id="room", name="general"))
    with patch.object(Bot, "_get_bot_id", return_value="bot"), patch.object(Bot, "_get_bot_name", return_value="helper"):
        return bot._extract_commands(message, "slack", "room", text, [])


def _create(bot, text):
    parsed = _parse(bot, text)
    assert isinstance(parsed, BotCommand)
    result = bot._execute_command(parsed, now=NOW)
    commands = [item for item in result if isinstance(item, BotCommand)]
    messages = [item for item in result if isinstance(item, Message)]
    return commands, messages


def _run(bot, events, duration):
    @csp.graph
    def graph():
        source = csp.curve(BotCommand, events) if events else csp.null_ts(BotCommand)
        feedback = csp.feedback(BotCommand)
        output = bot._handle_commands(csp.merge(source, feedback.out()))
        feedback.bind(csp.unroll(output.commands))
        csp.add_graph_output("messages", output.messages)

    output = csp.run(graph, starttime=NOW, endtime=duration, realtime=False)
    return [(at.replace(tzinfo=UTC), message) for at, messages in output["messages"] for message in messages]


@pytest.mark.parametrize("text", ['/schedule add --cron "* * * * *" -- /help /schedule', "/schedule add --cron '* * * * *' -- /help /schedule"])
def test_schedule_boundary_preserves_inner_schedule_argument(text):
    bot = _bot()
    commands, messages = _create(bot, text)
    assert len(commands) == len(messages) == 1
    inner = commands[0]
    assert inner.command == "help" and inner.args == ("/schedule",)
    assert inner.schedule == "* * * * *"
    assert inner.schedule_id and inner.schedule_id in messages[0].content
    assert "2026-11-22" in messages[0].content


@pytest.mark.parametrize(
    "text",
    [
        "/schedule /help /schedule '\\**-\\*-\\*-\\*-\\**'",
        '/schedule add --cron "*-*-*-*-*" -- /echo wrong',
        '/schedule add --cron "* * * * * *" -- /echo wrong',
        '/schedule add --cron "bad" -- /echo wrong',
        '/schedule add --cron "0 0 30 2 *" -- /echo wrong',
        '/schedule add --cron "* * * * *" /echo wrong',
        '/schedule add --cron "* * * * *" --',
        '/schedule add --cron "* * * * *" -- unknown',
        '/schedule add --cron "* * * * *" -- /missing',
        '/schedule add --cron "* * * * *" -- /schedule list',
        '/schedule add --cron "* * * * *" --delay 1s -- /echo wrong',
    ],
)
def test_invalid_schedule_returns_error_without_execution_or_storage(text):
    bot = _bot()
    commands, messages = _create(bot, text)
    assert not commands
    assert len(messages) == 1 and "Cannot schedule" in messages[0].content
    assert bot._schedule_store.records() == []


def test_cron_ranges_are_preserved():
    commands, _ = _create(_bot(), '/schedule add --cron "0 9 * * 1-5" -- /echo workday')
    assert commands[0].schedule == "0 9 * * 1-5"


def test_recurring_job_runs_once_per_due_time_and_ignores_duplicate_alarms():
    bot = _bot()
    commands, _ = _create(bot, '/schedule add --cron "* * * * *" -- /echo scheduled')
    results = _run(bot, [(timedelta(), commands[0])], timedelta(seconds=121))
    calls = [(at, msg) for at, msg in results if msg.content == "scheduled"]
    assert len(calls) == 2
    for (at, _), seconds in zip(calls, [60, 120], strict=True):
        assert NOW + timedelta(seconds=seconds) <= at <= NOW + timedelta(seconds=seconds + 0.02)
    assert len(bot._schedule_store.records()) == 1


def test_command_preparation_is_deferred_until_due():
    calls = []

    class PreparedCommand(ReplyCommand):
        def command(self):
            return "prepared"

        def name(self):
            return "Prepared"

        def help(self):
            return "Prepared"

        def preexecute(self, command):
            calls.append(command.id)
            return command

        def execute(self, command):
            return Message(content="prepared result", channel=command.channel)

    bot = _bot()
    bot.load_commands([BaseCommandModel(command=PreparedCommand)])
    commands, _ = _create(bot, '/schedule add --cron "* * * * *" -- /prepared')
    assert calls == []
    _run(bot, [(timedelta(), commands[0])], timedelta(seconds=121))
    assert len(calls) == 2


def test_cancelled_armed_schedule_never_executes():
    bot = _bot()
    commands, _ = _create(bot, '/schedule add --cron "* * * * *" -- /echo scheduled')
    cancel = _parse(bot, f"/schedule remove {commands[0].schedule_id}")
    results = _run(bot, [(timedelta(), commands[0]), (timedelta(seconds=30), cancel)], timedelta(seconds=121))
    assert not any(msg.content == "scheduled" for _, msg in results)
    assert bot._schedule_store.records() == []


def test_restart_restores_future_delays_and_advances_missed_recurrence(tmp_path):
    first = _bot()
    first.set_state_store(FsspecStateStore(str(tmp_path / "state")))
    commands, _ = _create(first, '/schedule add --cron "* * * * *" -- /echo resumed')
    command = commands[0]
    first._store_scheduled_command(command, NOW - timedelta(minutes=1))
    restored = _bot()
    restored.set_state_store(FsspecStateStore(str(tmp_path / "state")))
    results = _run(restored, [], timedelta(seconds=61))
    assert [msg.content for _, msg in results] == ["resumed"]


@pytest.mark.parametrize("duration,seconds", [("20m", 1200), ("30s", 30), ("1h", 3600), ("2d", 172800), ("100ms", 0.1)])
def test_delay_is_an_explicit_one_off(duration, seconds):
    bot = _bot()
    commands, messages = _create(bot, f"/delay {duration} -- /echo /schedule /delay")
    assert len(commands) == len(messages) == 1
    assert commands[0].delay == NOW + timedelta(seconds=seconds)
    assert commands[0].schedule == ""
    assert commands[0].args == ("/schedule", "/delay")


@pytest.mark.parametrize("duration", ["0s", "-1s", "yesterday", "nan", "1s --cron x", "1", "1s 2s"])
def test_invalid_delay_cannot_run_immediately(duration):
    bot = _bot()
    commands, messages = _create(bot, f"/delay {duration} -- /echo wrong")
    assert not commands and "Cannot schedule" in messages[0].content
    assert bot._schedule_store.records() == []


def test_delay_acknowledges_once_and_executes_only_when_due():
    bot = _bot()
    parsed = _parse(bot, "/delay 100ms -- /echo delayed")
    results = _run(bot, [(timedelta(), parsed)], timedelta(seconds=1))
    assert sum(msg.content.startswith("Scheduled /echo") for _, msg in results) == 1
    calls = [(at, msg) for at, msg in results if msg.content == "delayed"]
    assert len(calls) == 1 and calls[0][0] >= NOW + timedelta(milliseconds=100)
    assert bot._schedule_store.records() == []


def test_future_delay_survives_restart_but_overdue_delay_is_removed(tmp_path):
    path = str(tmp_path / "state")
    bot = _bot()
    bot.set_state_store(FsspecStateStore(path))
    commands, _ = _create(bot, "/delay 100ms -- /echo future")
    past = commands[0].copy()
    past.schedule_id = "past"
    past.args = ("overdue",)
    bot._store_scheduled_command(past, NOW - timedelta(seconds=1))
    restarted = _bot()
    restarted.set_state_store(FsspecStateStore(path))
    results = _run(restarted, [], timedelta(seconds=1))
    assert [msg.content for _, msg in results] == ["future"]
    assert restarted._schedule_store.records() == []


def test_recurring_async_preparation_keeps_parent_schedule_separate():
    preparations = []

    class AsyncCommand(ReplyCommand):
        def command(self):
            return "async"

        def name(self):
            return "Async"

        def help(self):
            return "Async"

        def preexecute(self, command):
            preparations.append(command)
            command.delay = NOW + timedelta(minutes=len(preparations), milliseconds=20)
            return command

        def execute(self, command):
            return Message(content="async result", channel=command.channel)

    bot = _bot()
    bot.load_commands([BaseCommandModel(command=AsyncCommand)])
    commands, _ = _create(bot, '/schedule add --cron "* * * * *" -- /async')
    schedule_id = commands[0].schedule_id
    results = _run(bot, [(timedelta(), commands[0])], timedelta(seconds=121))
    assert len(preparations) == 2
    assert len({command.id for command in preparations}) == 2
    assert [msg.content for _, msg in results] == ["async result", "async result"]
    assert [record.schedule_id for record in bot._schedule_store.records()] == [schedule_id]
    assert bot._schedule_store.get(schedule_id).command.prepare_on_run


def test_scheduling_supports_modern_commands_without_legacy_hooks():
    class ModernCommand(Command):
        name: str = "modern"

        def execute(self, ctx):
            return "modern result"

    bot = _bot()
    bot.load_commands([CommandModel(command=ModernCommand)])
    commands, _ = _create(bot, "/delay 100ms -- /modern")
    assert [msg.content for _, msg in _run(bot, [(timedelta(), commands[0])], timedelta(seconds=1))] == ["modern result"]


def test_inner_raw_arguments_remain_intact():
    from .test_raw_arguments import RawTextCommand

    bot = _bot()
    bot.load_commands([BaseCommandModel(command=RawTextCommand)])
    payload = '{"literal": "/schedule /delay --",\n  "text": "with  spaces"}'
    commands, _ = _create(bot, f"/delay 100ms -- /raw {payload}")
    assert commands[0].args == (payload,)
    assert [msg.content for _, msg in _run(bot, [(timedelta(), commands[0])], timedelta(seconds=1))] == [payload]


def test_only_owner_or_backend_admin_can_remove_schedule():
    bot = _bot()
    commands, _ = _create(bot, "/delay 1h -- /echo private")
    schedule_id = commands[0].schedule_id
    remove = _parse(bot, f"/schedule remove {schedule_id}", user="other")
    assert "permission denied" in bot._execute_command(remove, now=NOW)[0].content
    assert bot._schedule_store.get(schedule_id) is not None
    bot.config.management.admin_users = {"slack": {"other"}}
    assert "Removed schedule" in bot._execute_command(remove, now=NOW)[0].content
    assert bot._schedule_store.get(schedule_id) is None
