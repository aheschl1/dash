"""Conversation titling sidecar.

After a conversation's first user turn, propose a short human title for it with a
SEPARATE, fresh-context LLM call (not the agent's tool-calling loop and not its
system prompt — just "pick a title"). The proposed name is persisted via
`db.chat_set_auto_title` and preferred by `db.chat_list`, so it replaces the
first-message-derived fallback in the conversation tabs.

This is deliberately **non-blocking**: `maybe_propose_title` snapshots the message
list and hands the LLM call off to a daemon thread, so the turn it was triggered
from returns its answer immediately. A failed or empty proposal is simply dropped
(the first-message fallback title remains); there is no retry.
"""
import os
import threading

import db

from .main import _client_for, resolve_llm_config

# Fire once the conversation has at least this many user turns. The first message
# already states the topic, so we name the tab right after the opening turn.
TITLE_AFTER_USER_TURNS = int(os.environ.get("AGENT_TITLE_AFTER_TURNS", "1"))

# Cap how much of the conversation we feed the titler — the opening exchange is
# plenty to name it, and a small prompt keeps the call cheap.
_MAX_TURNS = 4
_MAX_CHARS = 600
_MAX_TITLE_LEN = 60

_TITLE_SYSTEM = (
    "You pick a title for a chat conversation. Given the user's opening message to a "
    "server-admin assistant, pick a concise title of at most six words describing what "
    "it is about. Reply with ONLY the title — no quotes, no trailing punctuation, no "
    "preamble or explanation."
)

# conv_ids whose titling is done or in flight, so a later save doesn't re-spawn a
# worker. Best-effort de-dupe; the worker also re-checks the DB before writing.
_lock = threading.Lock()
_seen: "set[str]" = set()


def _count_user_turns(messages: list[dict]) -> int:
    return sum(
        1 for m in messages
        if m.get("role") == "user" and (m.get("content") or "").strip()
    )


def _transcript(messages: list[dict]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        speaker = "User" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content[:_MAX_CHARS]}")
        if len(lines) >= _MAX_TURNS:
            break
    return "\n".join(lines)


def _clean(raw: str | None) -> str:
    title = (raw or "").strip().strip('"“”\'')
    title = " ".join(title.split())
    return title[:_MAX_TITLE_LEN].strip()


def _worker(conv_id: str, transcript: str) -> None:
    try:
        if db.chat_has_auto_title(conv_id):
            return
        cfg = resolve_llm_config()
        resp = _client_for(cfg["base_url"], cfg["api_key"]).chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": _TITLE_SYSTEM},
                {"role": "user", "content": transcript},
            ],
        )
        title = _clean(resp.choices[0].message.content)
        if title:
            db.chat_set_auto_title(conv_id, title)
    except Exception:
        # Best-effort: leave the first-message fallback title in place and allow a
        # later turn to retry by clearing the in-flight guard.
        with _lock:
            _seen.discard(conv_id)


def maybe_propose_title(conv_id: str, messages: list[dict]) -> None:
    """Spawn the titling sidecar if this conversation just crossed the threshold.

    Non-blocking: returns immediately, doing the LLM call on a daemon thread."""
    if _count_user_turns(messages) < TITLE_AFTER_USER_TURNS:
        return
    with _lock:
        if conv_id in _seen:
            return
        _seen.add(conv_id)
    transcript = _transcript(messages)
    if not transcript:
        with _lock:
            _seen.discard(conv_id)
        return
    threading.Thread(
        target=_worker, args=(conv_id, transcript), daemon=True
    ).start()
