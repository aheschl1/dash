import os
import secrets

import psycopg
from psycopg.types.json import Jsonb
from datetime import datetime, timezone

import auth

# Connection string. Defaults to the in-compose Postgres service; override with
# DATABASE_URL on another machine (different host/credentials/db name).
DSN = os.environ.get("DATABASE_URL", "postgresql://app:hackme@postgress:5432/admindash")

RETENTION_SECONDS = 24 * 3600

# Cap on persisted agent chats; oldest (by last activity) are pruned on save.
CHAT_SESSIONS_MAX = int(os.environ.get("AGENT_MAX_CONVERSATIONS", "200"))

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id       BIGSERIAL PRIMARY KEY,
    ts       BIGINT NOT NULL,
    cpu_pct  REAL,
    ram_pct  REAL,
    gpu_util REAL,
    gpu_temp REAL,
    cpu_temp REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE TABLE IF NOT EXISTS docker_events (
    id        BIGSERIAL PRIMARY KEY,
    ts        BIGINT NOT NULL,
    action    TEXT,
    container TEXT,
    image     TEXT
);
CREATE INDEX IF NOT EXISTS idx_devents_ts ON docker_events(ts);
CREATE TABLE IF NOT EXISTS feedback (
    id              SERIAL PRIMARY KEY,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      BIGINT NOT NULL,
    resolved_at     BIGINT,
    resolution_note TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    created_at    BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    turns      INTEGER NOT NULL DEFAULT 0,
    messages   JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS reflected_at BIGINT;
CREATE TABLE IF NOT EXISTS agent_memories (
    id         SERIAL PRIMARY KEY,
    content    TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS board_cards (
    id           SERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,
    content      TEXT NOT NULL,
    col          TEXT NOT NULL DEFAULT 'todo',
    pos          DOUBLE PRECISION NOT NULL DEFAULT 0,
    source_conv  TEXT,
    source_title TEXT,
    created_at   BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_board_cards_col ON board_cards(col, pos);
CREATE TABLE IF NOT EXISTS conversation_notes (
    id         SERIAL PRIMARY KEY,
    conv_id    TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_notes_conv ON conversation_notes(conv_id);
CREATE TABLE IF NOT EXISTS reflection_runs (
    id            SERIAL PRIMARY KEY,
    started_at    BIGINT NOT NULL,
    finished_at   BIGINT NOT NULL,
    conversations INTEGER NOT NULL DEFAULT 0,
    added         JSONB NOT NULL DEFAULT '[]',
    deleted       JSONB NOT NULL DEFAULT '[]',
    errors        JSONB NOT NULL DEFAULT '[]'
);
DROP TABLE IF EXISTS feedback_runs;
"""


def init_db() -> None:
    with psycopg.connect(DSN) as con:
        con.execute(_INIT_SQL)
        con.commit()


def insert_snapshot(cpu_pct: float, ram_pct: float, gpu_util: float | None,
                    gpu_temp: float | None, cpu_temp: float | None) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    cutoff = now - RETENTION_SECONDS
    with psycopg.connect(DSN) as con:
        con.execute(
            "INSERT INTO metrics (ts, cpu_pct, ram_pct, gpu_util, gpu_temp, cpu_temp) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (now, cpu_pct, ram_pct, gpu_util, gpu_temp, cpu_temp),
        )
        con.execute("DELETE FROM metrics WHERE ts < %s", (cutoff,))
        con.commit()


def insert_event(ts: int, action: str, container: str, image: str) -> None:
    with psycopg.connect(DSN) as con:
        con.execute(
            "INSERT INTO docker_events (ts, action, container, image) VALUES (%s,%s,%s,%s)",
            (ts, action, container, image)
        )
        # keep only last 200 events
        con.execute("DELETE FROM docker_events WHERE id NOT IN (SELECT id FROM docker_events ORDER BY ts DESC LIMIT 200)")
        con.commit()


def query_events(limit: int = 30) -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT ts, action, container, image FROM docker_events ORDER BY ts DESC LIMIT %s",
            (limit,)
        ).fetchall()
    return [{"ts": r[0], "action": r[1], "container": r[2], "image": r[3]} for r in rows]


def insert_feedback(type: str, title: str, description: str) -> int:
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "INSERT INTO feedback (type, title, description, created_at) VALUES (%s,%s,%s,%s) RETURNING id",
            (type, title, description, now),
        ).fetchone()
        con.commit()
    return row[0]


def list_feedback(status: str | None = None) -> list[dict]:
    with psycopg.connect(DSN) as con:
        if status:
            rows = con.execute(
                "SELECT id, type, title, description, status, created_at, resolved_at, resolution_note "
                "FROM feedback WHERE status = %s ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, type, title, description, status, created_at, resolved_at, resolution_note "
                "FROM feedback ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
    return [
        {"id": r[0], "type": r[1], "title": r[2], "description": r[3],
         "status": r[4], "created_at": r[5], "resolved_at": r[6], "resolution_note": r[7]}
        for r in rows
    ]


def update_feedback_status(id: int, status: str, note: str | None = None) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    resolved = now if status in ("done", "needs-review", "failed") else None
    with psycopg.connect(DSN) as con:
        con.execute(
            "UPDATE feedback SET status=%s, resolved_at=%s, resolution_note=%s WHERE id=%s",
            (status, resolved, note, id),
        )
        con.commit()


# ── Auth ────────────────────────────────────────────────────────────────────

_secret_cache: str | None = None


def get_auth_secret() -> str:
    """Token-signing secret, generated once and persisted so tokens survive rebuilds."""
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    with psycopg.connect(DSN) as con:
        con.execute(
            "INSERT INTO auth_config (key, value) VALUES ('token_secret', %s) "
            "ON CONFLICT (key) DO NOTHING",
            (secrets.token_urlsafe(48),),
        )
        con.commit()
        row = con.execute("SELECT value FROM auth_config WHERE key='token_secret'").fetchone()
    _secret_cache = row[0]
    return _secret_cache


def get_app_config(keys: "list[str] | tuple[str, ...]") -> dict[str, str]:
    """Fetch runtime app-config values by key. Missing keys are simply absent
    from the result (callers fall back to env/defaults). Used for the agent's
    LLM endpoint/model, which are editable at runtime without a redeploy."""
    if not keys:
        return {}
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT key, value FROM app_config WHERE key = ANY(%s)",
            (list(keys),),
        ).fetchall()
    return {k: v for k, v in rows}


def set_app_config(values: dict[str, str]) -> None:
    """Upsert runtime app-config values. A None/empty value deletes the key so
    the resolver falls back to the env default."""
    with psycopg.connect(DSN) as con:
        for key, value in values.items():
            if value is None or value == "":
                con.execute("DELETE FROM app_config WHERE key=%s", (key,))
            else:
                con.execute(
                    "INSERT INTO app_config (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                    (key, value),
                )
        con.commit()


def create_user(username: str, password: str, role: str = "admin") -> None:
    salt, password_hash = auth.hash_password(password)
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        con.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (username) DO UPDATE SET "
            "password_hash=EXCLUDED.password_hash, salt=EXCLUDED.salt, role=EXCLUDED.role",
            (username, password_hash, salt, role, now),
        )
        con.commit()


def get_user(username: str) -> dict | None:
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "SELECT username, password_hash, salt, role FROM users WHERE username=%s",
            (username,),
        ).fetchone()
    if not row:
        return None
    return {"username": row[0], "password_hash": row[1], "salt": row[2], "role": row[3]}


def set_password(username: str, password: str) -> bool:
    salt, password_hash = auth.hash_password(password)
    with psycopg.connect(DSN) as con:
        n = con.execute(
            "UPDATE users SET password_hash=%s, salt=%s WHERE username=%s",
            (password_hash, salt, username),
        ).rowcount
        con.commit()
    return n > 0


# ── Agent chat sessions ───────────────────────────────────────────────────────


def _chat_title(messages: list[dict]) -> str:
    """First user turn, trimmed — mirrors the frontend/_render title logic."""
    for m in messages:
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"].strip()[:80]
    return ""


def _chat_turns(messages: list[dict]) -> int:
    """Count of renderable user/assistant bubbles (matches endpoint._render)."""
    return sum(
        1 for m in messages
        if (m.get("role") == "user")
        or (m.get("role") == "assistant" and (m.get("content") or "").strip())
    )


def chat_save(id: str, messages: list[dict]) -> None:
    """Upsert a chat's full message list, then prune to CHAT_SESSIONS_MAX newest.

    The first save for an id inserts the row (create() defers the write until a
    conversation has real content, so empty 'New chat' tabs never persist).
    """
    now = int(datetime.now(timezone.utc).timestamp())
    payload = Jsonb(messages)
    with psycopg.connect(DSN) as con:
        con.execute(
            "INSERT INTO chat_sessions (id, title, turns, messages, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "title=EXCLUDED.title, turns=EXCLUDED.turns, messages=EXCLUDED.messages, "
            "updated_at=EXCLUDED.updated_at",
            (id, _chat_title(messages), _chat_turns(messages), payload, now, now),
        )
        pruned = con.execute(
            "DELETE FROM chat_sessions WHERE id NOT IN "
            "(SELECT id FROM chat_sessions ORDER BY updated_at DESC LIMIT %s) "
            "RETURNING id",
            (CHAT_SESSIONS_MAX,),
        ).fetchall()
        if pruned:
            # Conversation notes are scoped to a chat; drop them with it. Safe to
            # key off the pruned ids (not "all ids missing from chat_sessions")
            # because a brand-new conversation may hold notes before its first
            # chat_save row exists, and we must not delete those.
            con.execute(
                "DELETE FROM conversation_notes WHERE conv_id = ANY(%s)",
                ([r[0] for r in pruned],),
            )
        con.commit()


def chat_get(id: str) -> list[dict] | None:
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "SELECT messages FROM chat_sessions WHERE id=%s", (id,)
        ).fetchone()
    return row[0] if row else None


def chat_list(limit: int = 100) -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT id, title, turns FROM chat_sessions ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "turns": r[2]} for r in rows]


def chat_unreflected(limit: int) -> list[str]:
    """Ids of conversations the nightly reflection still owes a pass: never
    reflected, or changed (a new turn bumped updated_at) since the last pass.
    Newest first, so a capped run reflects the freshest chats and leaves the
    rest for the next night."""
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT id FROM chat_sessions "
            "WHERE reflected_at IS NULL OR reflected_at < updated_at "
            "ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def chat_mark_reflected(id: str) -> None:
    """Stamp a conversation as reflected now. Reflection never calls chat_save, so
    updated_at stays put and this row won't resurface until a real new turn moves
    updated_at past this stamp again."""
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        con.execute("UPDATE chat_sessions SET reflected_at=%s WHERE id=%s", (now, id))
        con.commit()


def chat_delete(id: str) -> bool:
    with psycopg.connect(DSN) as con:
        n = con.execute("DELETE FROM chat_sessions WHERE id=%s", (id,)).rowcount
        con.execute("DELETE FROM conversation_notes WHERE conv_id=%s", (id,))
        con.commit()
    return n > 0


# ── Agent memories ────────────────────────────────────────────────────────────


def memory_save(content: str) -> int:
    """Persist a free-text memory the agent wants to recall in future chats."""
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "INSERT INTO agent_memories (content, created_at) VALUES (%s,%s) RETURNING id",
            (content, now),
        ).fetchone()
        con.commit()
    return row[0]


def memory_delete(id: int) -> bool:
    with psycopg.connect(DSN) as con:
        n = con.execute("DELETE FROM agent_memories WHERE id=%s", (id,)).rowcount
        con.commit()
    return n > 0


def memory_update(id: int, content: str) -> bool:
    """Replace a memory's text in place, keeping its id (so the agent's prompt
    references stay stable). False if no such id."""
    with psycopg.connect(DSN) as con:
        n = con.execute(
            "UPDATE agent_memories SET content=%s WHERE id=%s", (content, id)
        ).rowcount
        con.commit()
    return n > 0


def memory_list() -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT id, content, created_at FROM agent_memories ORDER BY id"
        ).fetchall()
    return [{"id": r[0], "content": r[1], "created_at": r[2]} for r in rows]


# ── Investigation board ───────────────────────────────────────────────────────

_BOARD_COLS = "id, kind, content, col, pos, source_conv, source_title, created_at"


def _board_row(r) -> dict:
    return {
        "id": r[0], "kind": r[1], "content": r[2], "col": r[3], "pos": r[4],
        "source_conv": r[5], "source_title": r[6], "created_at": r[7],
    }


def board_add(
    kind: str, content: str, source_conv: str | None, source_title: str | None,
    col: str = "todo",
) -> dict:
    """Pin a code/table block onto the board, appended to the bottom of `col`."""
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        nxt = con.execute(
            "SELECT COALESCE(MAX(pos), 0) + 1 FROM board_cards WHERE col=%s", (col,)
        ).fetchone()[0]
        row = con.execute(
            f"INSERT INTO board_cards (kind, content, col, pos, source_conv, source_title, created_at) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {_BOARD_COLS}",
            (kind, content, col, nxt, source_conv, source_title, now),
        ).fetchone()
        con.commit()
    return _board_row(row)


def board_move(id: int, col: str, pos: float) -> bool:
    """Reassign a card's column and fractional position. False if no such id."""
    with psycopg.connect(DSN) as con:
        n = con.execute(
            "UPDATE board_cards SET col=%s, pos=%s WHERE id=%s", (col, pos, id)
        ).rowcount
        con.commit()
    return n > 0


def board_delete(id: int) -> bool:
    with psycopg.connect(DSN) as con:
        n = con.execute("DELETE FROM board_cards WHERE id=%s", (id,)).rowcount
        con.commit()
    return n > 0


def board_list() -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            f"SELECT {_BOARD_COLS} FROM board_cards ORDER BY col, pos"
        ).fetchall()
    return [_board_row(r) for r in rows]


# ── Reflection runs ───────────────────────────────────────────────────────────
# An audit log of the nightly self-reflection job (see agent/reflect.py): what it
# added, what it pruned, how many conversations it mined, and any errors. Kept so
# the dashboard can show what the agent did to its own memory while unattended.

REFLECTION_RUNS_MAX = int(os.environ.get("AGENT_REFLECTION_RUNS_MAX", "50"))


def reflection_run_save(started_at: int, finished_at: int, conversations: int,
                        added: list, deleted: list, errors: list) -> int:
    """Record one reflection run, then prune to the most recent REFLECTION_RUNS_MAX."""
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "INSERT INTO reflection_runs "
            "(started_at, finished_at, conversations, added, deleted, errors) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (started_at, finished_at, conversations,
             Jsonb(added), Jsonb(deleted), Jsonb(errors)),
        ).fetchone()
        con.execute(
            "DELETE FROM reflection_runs WHERE id NOT IN "
            "(SELECT id FROM reflection_runs ORDER BY id DESC LIMIT %s)",
            (REFLECTION_RUNS_MAX,),
        )
        con.commit()
    return row[0]


def reflection_run_list(limit: int = 20) -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT id, started_at, finished_at, conversations, added, deleted, errors "
            "FROM reflection_runs ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "started_at": r[1], "finished_at": r[2], "conversations": r[3],
         "added": r[4], "deleted": r[5], "errors": r[6]}
        for r in rows
    ]


# ── Conversation notes ──────────────────────────────────────────────────────────
# Like memories, but scoped to a single conversation and injected fresh per run
# (never baked into the persisted system prompt) so they stay current across turns
# and reloads. Removed with the conversation (see chat_delete / chat_save prune).


def conv_note_save(conv_id: str, content: str) -> int:
    now = int(datetime.now(timezone.utc).timestamp())
    with psycopg.connect(DSN) as con:
        row = con.execute(
            "INSERT INTO conversation_notes (conv_id, content, created_at) "
            "VALUES (%s,%s,%s) RETURNING id",
            (conv_id, content, now),
        ).fetchone()
        con.commit()
    return row[0]


def conv_note_list(conv_id: str) -> list[dict]:
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT id, content FROM conversation_notes WHERE conv_id=%s ORDER BY id",
            (conv_id,),
        ).fetchall()
    return [{"id": r[0], "content": r[1]} for r in rows]


def query_history(minutes: int = 1440) -> list[dict]:
    cutoff = int(datetime.now(timezone.utc).timestamp()) - minutes * 60
    with psycopg.connect(DSN) as con:
        rows = con.execute(
            "SELECT ts, cpu_pct, ram_pct, gpu_util, gpu_temp, cpu_temp "
            "FROM metrics WHERE ts >= %s ORDER BY ts",
            (cutoff,),
        ).fetchall()
    return [
        {"ts": r[0], "cpu_pct": r[1], "ram_pct": r[2],
         "gpu_util": r[3], "gpu_temp": r[4], "cpu_temp": r[5]}
        for r in rows
    ]
