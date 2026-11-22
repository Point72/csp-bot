"""Recent successful bot sends, indexed independently of platform history."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from chatom import Message

from csp_bot.persistence import InMemoryStateStore, StateStore


@dataclass(frozen=True)
class SentMessage:
    id: str
    backend: str
    channel_id: str
    channel_name: str
    native_id: str
    posted_at: datetime
    preview: str
    deleted: bool = False


class SentMessageStore:
    namespace = "csp_bot.sent_messages"

    def __init__(self, store: StateStore | None = None):
        self._store = store if store is not None else InMemoryStateStore()
        self._lock = Lock()

    def record(self, backend: str, original: Message, sent: Message) -> SentMessage | None:
        channel_id = sent.channel_id or original.channel_id
        if not sent.id or not channel_id:
            return None
        key = uuid5(NAMESPACE_URL, json.dumps([backend, channel_id, sent.id])).hex
        with self._lock:
            existing = self.get(key, backend)
            if existing is not None:
                return existing
            content = sent.content or original.content
            if not content:
                filenames = [attachment.filename for attachment in sent.attachments or original.attachments if attachment.filename]
                content = ", ".join(filenames) or original.metadata.get("filename") or "[attachment]"
            record = SentMessage(
                id=key,
                backend=backend,
                channel_id=channel_id,
                channel_name=" ".join((sent.channel_name or original.channel_name).split())[:50],
                native_id=sent.id,
                posted_at=datetime.now(UTC),
                preview=" ".join(content.split())[:80],
            )
            self._store.put(self.namespace, key, record)
            records = sorted(self._store.records(self.namespace), key=lambda item: (item.value.posted_at, item.key), reverse=True)
            for expired in records[1000:]:
                self._store.delete(self.namespace, expired.key)
            return record

    def get(self, message_id: str, backend: str) -> SentMessage | None:
        record = self._store.get(self.namespace, message_id)
        return record if isinstance(record, SentMessage) and record.backend == backend else None

    def page(self, backend: str, before: str = "") -> tuple[list[SentMessage], str]:
        records = [item.value for item in self._store.records(self.namespace) if item.value.backend == backend]
        records.sort(key=lambda item: (item.posted_at, item.id), reverse=True)
        if before:
            cursor = self.get(before, backend)
            if cursor is None:
                raise ValueError("Unknown or expired page cursor. Start again with /messages list.")
            records = [item for item in records if (item.posted_at, item.id) < (cursor.posted_at, cursor.id)]
        page = records[:5]
        return page, page[-1].id if len(records) > 5 else ""

    def update(self, record: SentMessage, *, content: str | None = None, native_id: str = "", deleted: bool = False) -> None:
        updated = replace(
            record,
            preview=" ".join(content.split())[:80] if content is not None else record.preview,
            native_id=native_id or record.native_id,
            deleted=deleted,
        )
        self._store.put(self.namespace, record.id, updated)
