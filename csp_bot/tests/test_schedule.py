"""Tests for schedule command persistence integration."""

from datetime import UTC, datetime, timedelta

import pytest
from chatom import Message, User
from croniter import croniter

from csp_bot import Bot, BotConfig
from csp_bot.commands.schedule import ScheduleCommand
from csp_bot.persistence import InMemoryStateStore, ScheduleStore
from csp_bot.structs import BotCommand, CommandVariant


def _make_command(command: str = "echo", message_id: str = "msg1") -> BotCommand:
    return BotCommand(
        command=command,
        args=("hello",),
        source=User(id="U123", name="Test User"),
        targets=(),
        channel_id="C456",
        channel_name="general",
        backend="slack",
        variant=CommandVariant.REPLY,
        message=Message(id=message_id, content=f"/{command} hello"),
        delay=datetime.now(UTC) + timedelta(minutes=5),
        schedule="",
        times_run=0,
    )


def test_schedule_list_uses_stable_schedule_id():
    schedule_store = ScheduleStore(InMemoryStateStore())
    schedule_store.put(_make_command(), schedule_id="schedule-1")
    command = _make_command(command="schedule", message_id="list")
    command.args = ("list",)

    message = ScheduleCommand().execute(command, schedule_store)

    assert "schedule-1" in message.content
    assert "/echo" in message.content


def test_schedule_remove_uses_stable_schedule_id():
    schedule_store = ScheduleStore(InMemoryStateStore())
    schedule_store.put(_make_command(), schedule_id="schedule-1")
    bot = Bot(config=BotConfig())
    bot.set_schedule_store(schedule_store)
    command = _make_command(command="schedule", message_id="remove")
    command.args = ("remove", "schedule-1")

    result = ScheduleCommand().preexecute(command, schedule_store, bot)

    assert result is None
    assert schedule_store.get("schedule-1") is None


@pytest.mark.parametrize(
    ("cron_arg", "expression", "next_run"),
    [
        ('"0 9 * * MON-FRI"', "0 9 * * MON-FRI", datetime(2026, 11, 16, 9, tzinfo=UTC)),
        ('"0 9-17 * * *"', "0 9-17 * * *", datetime(2026, 11, 14, 9, tzinfo=UTC)),
        ('"0 9 * * *"', "0 9 * * *", datetime(2026, 11, 14, 9, tzinfo=UTC)),
        ("0-9-*-*-*", "0 9 * * *", datetime(2026, 11, 14, 9, tzinfo=UTC)),
    ],
)
def test_schedule_add_preserves_cron_ranges(cron_arg, expression, next_run):
    bot = Bot(config=BotConfig())
    bot._commands["schedule"] = ScheduleCommand()
    text = f"/schedule add /schedule {cron_arg} /echo hello"
    message = Message(id="schedule-add", content=text)

    command = bot._extract_commands(message, "slack", "C456", text, [])

    assert isinstance(command, BotCommand)
    assert command.schedule == expression
    assert command.command == "echo"
    assert command.args == ("hello",)
    assert croniter(command.schedule, datetime(2026, 11, 13, 17, 30, tzinfo=UTC)).get_next(datetime) == next_run


def test_bot_store_scheduled_command_allows_duplicate_command_names():
    bot = Bot(config=BotConfig())
    first = _make_command(command="echo", message_id="msg1")
    second = _make_command(command="echo", message_id="msg2")

    first_record = bot._store_scheduled_command(first, first.delay)
    second_record = bot._store_scheduled_command(second, second.delay)

    assert first_record.schedule_id != second_record.schedule_id
    assert {record.command.message.id for record in bot._schedule_store.records()} == {"msg1", "msg2"}


def test_bot_set_state_store_injects_existing_schedule_records():
    state_store = InMemoryStateStore()
    schedule_store = ScheduleStore(state_store)
    schedule_store.put(_make_command(), schedule_id="schedule-1")
    bot = Bot(config=BotConfig())

    bot.set_state_store(state_store)

    assert bot._schedule_store.get("schedule-1") is not None
    assert bot._schedule_store.get("schedule-1").command.message.id == "msg1"


def test_bot_restore_scheduled_commands_skips_past_records():
    bot = Bot(config=BotConfig())
    now = datetime.now(UTC)
    past = _make_command(message_id="past")
    future = _make_command(message_id="future")
    bot._schedule_store.put(past, schedule_id="past", next_run_at=now - timedelta(minutes=1))
    bot._schedule_store.put(future, schedule_id="future", next_run_at=now + timedelta(minutes=1))

    restored = bot._restore_scheduled_commands(now)

    assert [record.schedule_id for record in restored] == ["future"]


def test_bot_reschedules_recurring_command_with_same_schedule_id():
    bot = Bot(config=BotConfig())
    command = _make_command(command="echo", message_id="recurring")
    command.schedule = "*/5 * * * *"
    command.schedule_id = "schedule-1"
    first_time = datetime.now(UTC) + timedelta(minutes=5)
    second_time = first_time + timedelta(minutes=5)

    first_record = bot._store_scheduled_command(command, first_time)
    second_record = bot._store_scheduled_command(command, second_time)

    assert first_record.schedule_id == second_record.schedule_id == "schedule-1"
    assert second_record.created_at == first_record.created_at
    assert second_record.next_run_at == second_time
    assert [record.schedule_id for record in bot._schedule_store.records()] == ["schedule-1"]
