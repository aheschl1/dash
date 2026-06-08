"""Agent conversation store: Postgres-backed, with an in-memory hot cache.

The `chat_sessions` table is the source of truth, so conversations survive a
backend restart and reloads. The in-memory dict is only a cache to avoid a DB
round-trip on every turn; evicting from it never loses data (it reloads from the
DB on the next access). There is no TTL and no eviction-as-deletion — a chat goes
away only when explicitly ended (DELETE /api/end/{id}) or pruned by the DB's
most-recent-N cap (see db.chat_save / CHAT_SESSIONS_MAX).

A freshly created conversation is cached in memory but NOT written to the DB until
its first turn is saved, so empty 'New chat' tabs that never get used don't persist.

Sync FastAPI handlers run in a threadpool, so the cache dict is guarded by a lock.
The per-conversation message list is mutated outside the lock during the (slow)
LLM call; concurrent sends to the *same* id are a frontend bug, not defended here.
"""
import os
import threading
import uuid
from collections import OrderedDict

import db

from .main import new_messages

# In-memory cache bound. Pure memory hygiene — data lives in the DB, so dropping
# a cache entry just forces a reload on next access.
CACHE_MAX = int(os.environ.get("AGENT_CACHE_MAX", "200"))

_lock = threading.Lock()
_cache: "OrderedDict[str, list[dict]]" = OrderedDict()

# Conversations with a turn currently running. A conversation's message list is
# mutated in place during the (slow) LLM call, so two concurrent turns on the
# same id would corrupt it — this serializes them across both the POST and WS
# paths. A turn that started before a WS dropped keeps this flag until it finishes
# and persists, so a reconnecting client can tell the conversation is still busy.
_busy: "set[str]" = set()


def try_begin(conv_id: str) -> bool:
    """Claim exclusive run rights for a turn. False if one is already running."""
    with _lock:
        if conv_id in _busy:
            return False
        _busy.add(conv_id)
        return True


def finish(conv_id: str) -> None:
    """Release the run claim taken by try_begin()."""
    with _lock:
        _busy.discard(conv_id)


def is_busy(conv_id: str) -> bool:
    with _lock:
        return conv_id in _busy


def _cache_put(conv_id: str, messages: list[dict]) -> None:
    _cache[conv_id] = messages
    _cache.move_to_end(conv_id)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def create() -> str:
    """Mint a new conversation seeded with the system prompt.

    Cached only — the DB row is written lazily on the first save() so unused
    empty chats never persist.
    """
    conv_id = uuid.uuid4().hex
    with _lock:
        _cache_put(conv_id, new_messages())
    return conv_id


def get(conv_id: str) -> list[dict] | None:
    """Return a conversation's live message list (cache, then DB on miss)."""
    with _lock:
        messages = _cache.get(conv_id)
        if messages is not None:
            _cache.move_to_end(conv_id)
            return messages
    loaded = db.chat_get(conv_id)
    if loaded is None:
        return None
    with _lock:
        # Re-check: another thread may have cached/created it while we hit the DB.
        existing = _cache.get(conv_id)
        if existing is not None:
            _cache.move_to_end(conv_id)
            return existing
        _cache_put(conv_id, loaded)
        return loaded


def get_copy(conv_id: str) -> list[dict] | None:
    """A shallow copy of a conversation for safe read-only rendering."""
    messages = get(conv_id)
    return list(messages) if messages is not None else None


def save(conv_id: str, messages: list[dict]) -> None:
    """Persist a conversation after a completed turn (upsert + prune)."""
    db.chat_save(conv_id, messages)


def end(conv_id: str) -> bool:
    """Retire a conversation everywhere. False only if it existed nowhere."""
    with _lock:
        in_cache = _cache.pop(conv_id, None) is not None
    deleted = db.chat_delete(conv_id)
    return in_cache or deleted
