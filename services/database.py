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


if __name__ == "__main__":

    init_db()

    print(f"Database initialized at {DATABASE_PATH}")
