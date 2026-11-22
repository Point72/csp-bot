from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from chatom import Channel, Message

from csp_bot import Bot, BotConfig
from csp_bot.persistence import FsspecStateStore
from csp_bot.sent_messages import SentMessageStore


def _record(store, number, *, backend="slack", room="room"):
    message = Message(id=str(number), content=f"message {number}", channel=Channel(id=room, name=f"Room {room}"))
    return store.record(backend, message, message)


def test_native_ids_are_scoped_by_backend_and_room():
    store = SentMessageStore()
    a = _record(store, "same", backend="slack", room="one")
    b = _record(store, "same", backend="slack", room="two")
    c = _record(store, "same", backend="telegram", room="one")
    assert len({a.id, b.id, c.id}) == 3
    assert store.get(c.id, "slack") is None
    assert len(store.page("slack")[0]) == 2


def test_duplicate_callbacks_do_not_add_another_entry():
    store = SentMessageStore()
    first = _record(store, 1)
    assert _record(store, 1) == first
    assert len(store.page("slack")[0]) == 1


def test_cursor_pages_remain_stable_with_new_sends_and_deletion():
    store = SentMessageStore()
    now = datetime(2026, 11, 22, tzinfo=UTC)
    with patch("csp_bot.sent_messages.datetime") as clock:
        for index in range(12):
            clock.now.return_value = now + timedelta(seconds=index)
            _record(store, index)
    first, cursor = store.page("slack")
    assert [item.native_id for item in first] == ["11", "10", "9", "8", "7"]
    store.update(first[-1], deleted=True)
    with patch("csp_bot.sent_messages.datetime") as clock:
        clock.now.return_value = now + timedelta(hours=1)
        _record(store, "new")
    second, cursor = store.page("slack", cursor)
    assert [item.native_id for item in second] == ["6", "5", "4", "3", "2"]
    last, cursor = store.page("slack", cursor)
    assert [item.native_id for item in last] == ["1", "0"]
    assert cursor == ""


def test_catalog_is_bounded():
    store = SentMessageStore()
    for index in range(1001):
        _record(store, index)
    assert len(list(store._store.records(store.namespace))) == 1000
    first = Message(id="0", channel=Channel(id="room"))
    other = SentMessageStore().record("slack", first, first)
    assert other is not None
    assert store.get(other.id, "slack") is None


def test_catalog_restores_with_existing_state_store(tmp_path):
    path = str(tmp_path / "state")
    first = SentMessageStore(FsspecStateStore(path))
    record = _record(first, 1)
    restored = SentMessageStore(FsspecStateStore(path))
    assert restored.get(record.id, "slack") == record
    bot = Bot(config=BotConfig())
    bot.set_state_store(FsspecStateStore(path))
    assert bot._sent_messages.get(record.id, "slack") == record


def test_posted_callback_uses_actual_id_and_falls_back_to_original_room():
    bot = Bot(config=BotConfig())
    original = Message(id="incoming", content="text", channel=Channel(id="room", name="General"))
    bot._record_sent_message("slack", original, Message(id="actual"))
    record = bot._sent_messages.page("slack")[0][0]
    assert record.native_id == "actual" and record.channel_id == "room"
    assert record.channel_name == "General" and record.preview == "text"
    bot._record_sent_message("slack", original, Message())
    assert len(bot._sent_messages.page("slack")[0]) == 1
