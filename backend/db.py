import os
import secrets

import psycopg
from psycopg.types.json import Jsonb
from datetime import datetime, timezone

import auth

DSN = "postgresql://app:hackme@postgress:5432/admindash"

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
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    turns      INTEGER NOT NULL DEFAULT 0,
    messages   JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);
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
        con.execute(
            "DELETE FROM chat_sessions WHERE id NOT IN "
            "(SELECT id FROM chat_sessions ORDER BY updated_at DESC LIMIT %s)",
            (CHAT_SESSIONS_MAX,),
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


def chat_delete(id: str) -> bool:
    with psycopg.connect(DSN) as con:
        n = con.execute("DELETE FROM chat_sessions WHERE id=%s", (id,)).rowcount
        con.commit()
    return n > 0


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
