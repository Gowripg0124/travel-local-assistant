import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()


# =========================================================
# CONFIGURATION
# =========================================================

# SQLite file holding users, login sessions,
# conversations and messages.
DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    "app.db"
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    -- JSON: attachments, route, sources, places, ...
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);

-- One row per user. Written only by the backend from
-- verified payment-provider events, never the frontend.
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY
        REFERENCES users(id) ON DELETE CASCADE,
    plan_name TEXT NOT NULL,
    -- free | active | canceled | expired | past_due
    status TEXT NOT NULL,
    provider TEXT,
    subscription_id TEXT,
    started_at TEXT,
    expires_at TEXT,
    updated_at TEXT NOT NULL
);

-- One row per counted interaction. Guests have no
-- user_id and are identified by guest_key instead.
CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER
        REFERENCES users(id) ON DELETE CASCADE,
    guest_key TEXT,
    conversation_id INTEGER,
    request_id TEXT NOT NULL,
    -- message | document_upload
    usage_type TEXT NOT NULL,
    -- Nullable: only set when Gemini reported them.
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_user
    ON usage_records(user_id, usage_type, created_at);

CREATE INDEX IF NOT EXISTS idx_usage_guest
    ON usage_records(guest_key, usage_type, created_at);

-- Processed payment-provider webhook events, so
-- duplicate and out-of-order deliveries are skipped.
CREATE TABLE IF NOT EXISTS payment_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    subscription_id TEXT,
    event_created_at TEXT,
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_events_subscription
    ON payment_events(subscription_id, event_created_at);
"""


def utc_now() -> str:

    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    """
    Open a connection, commit on success,
    roll back on error, always close.
    """

    connection = sqlite3.connect(DATABASE_PATH)

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def init_db():
    """
    Create tables if they don't exist.
    Safe to run on every startup.
    """

    with get_connection() as connection:
        connection.executescript(SCHEMA)


# =========================================================
# USERS
# =========================================================

def create_user(email: str, password_hash: str) -> dict:
    """
    Raises sqlite3.IntegrityError if the
    email is already registered.
    """

    with get_connection() as connection:

        cursor = connection.execute(
            "INSERT INTO users (email, password_hash, created_at) "
            "VALUES (?, ?, ?)",
            (email, password_hash, utc_now())
        )

        return get_user_by_id(cursor.lastrowid, connection)


def get_user_by_id(user_id: int, connection=None) -> dict | None:

    if connection is None:
        with get_connection() as connection:
            return get_user_by_id(user_id, connection)

    row = connection.execute(
        "SELECT id, email, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    return dict(row) if row else None


def get_user_credentials(email: str) -> dict | None:
    """
    Includes password_hash; only for login checks.
    """

    with get_connection() as connection:

        row = connection.execute(
            "SELECT id, email, password_hash FROM users "
            "WHERE email = ?",
            (email,)
        ).fetchone()

    return dict(row) if row else None


# =========================================================
# LOGIN SESSIONS
# =========================================================

def create_auth_session(
    token_hash: str,
    user_id: int,
    expires_at: str
):

    with get_connection() as connection:

        connection.execute(
            "INSERT INTO auth_sessions "
            "(token_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, user_id, utc_now(), expires_at)
        )


def get_user_by_token_hash(token_hash: str) -> dict | None:
    """
    Returns the user for a valid, unexpired session.
    """

    with get_connection() as connection:

        row = connection.execute(
            "SELECT users.id, users.email, users.created_at "
            "FROM auth_sessions "
            "JOIN users ON users.id = auth_sessions.user_id "
            "WHERE auth_sessions.token_hash = ? "
            "AND auth_sessions.expires_at > ?",
            (token_hash, utc_now())
        ).fetchone()

    return dict(row) if row else None


def delete_auth_session(token_hash: str):

    with get_connection() as connection:

        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (token_hash,)
        )


def delete_expired_auth_sessions():

    with get_connection() as connection:

        connection.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (utc_now(),)
        )


# =========================================================
# CONVERSATIONS
# =========================================================

def create_conversation(user_id: int, title: str) -> dict:

    now = utc_now()

    with get_connection() as connection:

        cursor = connection.execute(
            "INSERT INTO conversations "
            "(user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, title, now, now)
        )

        return dict(
            connection.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (cursor.lastrowid,)
            ).fetchone()
        )


def get_conversation(conversation_id: int) -> dict | None:

    with get_connection() as connection:

        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,)
        ).fetchone()

    return dict(row) if row else None


def list_conversations(user_id: int) -> list[dict]:

    with get_connection() as connection:

        rows = connection.execute(
            "SELECT * FROM conversations WHERE user_id = ? "
            "ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()

    return [dict(row) for row in rows]


def delete_conversation(conversation_id: int):
    """
    Messages are removed by ON DELETE CASCADE.
    """

    with get_connection() as connection:

        connection.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,)
        )


# =========================================================
# MESSAGES
# =========================================================

def add_message(
    conversation_id: int,
    role: str,
    content: str,
    metadata: dict | None = None
):

    now = utc_now()

    with get_connection() as connection:

        connection.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                conversation_id,
                role,
                content,
                json.dumps(metadata or {}),
                now
            )
        )

        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id)
        )


def list_messages(conversation_id: int) -> list[dict]:
    """
    Returns messages in the same shape the Streamlit
    UI keeps in session state: role, content and the
    stored metadata fields merged in.
    """

    with get_connection() as connection:

        rows = connection.execute(
            "SELECT id, role, content, metadata, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY id",
            (conversation_id,)
        ).fetchall()

    return [
        {
            **json.loads(row["metadata"]),
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        }
        for row in rows
    ]


# =========================================================
# SUBSCRIPTIONS
# =========================================================

def get_subscription(user_id: int) -> dict | None:

    with get_connection() as connection:

        row = connection.execute(
            "SELECT * FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    return dict(row) if row else None


def upsert_subscription(
    user_id: int,
    plan_name: str,
    status: str,
    provider: str | None = None,
    subscription_id: str | None = None,
    started_at: str | None = None,
    expires_at: str | None = None
):

    with get_connection() as connection:

        connection.execute(
            "INSERT INTO subscriptions "
            "(user_id, plan_name, status, provider, "
            "subscription_id, started_at, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "plan_name = excluded.plan_name, "
            "status = excluded.status, "
            "provider = excluded.provider, "
            "subscription_id = excluded.subscription_id, "
            "started_at = excluded.started_at, "
            "expires_at = excluded.expires_at, "
            "updated_at = excluded.updated_at",
            (
                user_id, plan_name, status, provider,
                subscription_id, started_at, expires_at,
                utc_now()
            )
        )


# =========================================================
# PAYMENT EVENTS (webhook idempotency)
# =========================================================

def payment_event_exists(event_id: str) -> bool:

    with get_connection() as connection:

        return connection.execute(
            "SELECT 1 FROM payment_events WHERE event_id = ?",
            (event_id,)
        ).fetchone() is not None


def latest_payment_event_at(subscription_id: str) -> str | None:

    with get_connection() as connection:

        return connection.execute(
            "SELECT MAX(event_created_at) FROM payment_events "
            "WHERE subscription_id = ?",
            (subscription_id,)
        ).fetchone()[0]


def add_payment_event(
    event_id: str,
    provider: str,
    subscription_id: str | None,
    event_created_at: str | None
):

    with get_connection() as connection:

        connection.execute(
            "INSERT OR IGNORE INTO payment_events "
            "(event_id, provider, subscription_id, "
            "event_created_at, received_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_id, provider, subscription_id,
                event_created_at, utc_now()
            )
        )


# =========================================================
# USAGE RECORDS
# =========================================================

def add_usage_record(
    request_id: str,
    usage_type: str,
    user_id: int | None = None,
    guest_key: str | None = None,
    conversation_id: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None
):

    with get_connection() as connection:

        connection.execute(
            "INSERT INTO usage_records "
            "(user_id, guest_key, conversation_id, request_id, "
            "usage_type, input_tokens, output_tokens, "
            "total_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, guest_key, conversation_id, request_id,
                usage_type, input_tokens, output_tokens,
                total_tokens, utc_now()
            )
        )


def summarize_usage(
    usage_type: str,
    since: str | None = None,
    user_id: int | None = None,
    guest_key: str | None = None
) -> dict:
    """
    Count and token totals for one user or guest.
    Token sums are None when no tokens were recorded.
    """

    if user_id is not None:
        owner_sql, owner = "user_id = ?", user_id
    else:
        owner_sql, owner = "guest_key = ?", guest_key

    sql = (
        "SELECT COUNT(*) AS count, "
        "SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "SUM(total_tokens) AS total_tokens "
        f"FROM usage_records WHERE {owner_sql} "
        "AND usage_type = ?"
    )

    params = [owner, usage_type]

    if since:
        sql += " AND created_at >= ?"
        params.append(since)

    with get_connection() as connection:
        row = connection.execute(sql, params).fetchone()

    return dict(row)


if __name__ == "__main__":

    init_db()

    print(f"Database initialized at {DATABASE_PATH}")
