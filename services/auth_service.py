import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from services import database


# =========================================================
# CONFIGURATION
# =========================================================

# PBKDF2-SHA256 work factor (OWASP recommendation).
PASSWORD_ITERATIONS = 600_000

MIN_PASSWORD_LENGTH = 8

SESSION_DAYS = 7

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """
    Raised for signup/login problems. The message
    is safe to show to the user.
    """

    def __init__(self, message: str, status_code: int = 400):

        super().__init__(message)

        self.status_code = status_code


# =========================================================
# PASSWORD HASHING
# =========================================================

def hash_password(password: str) -> str:
    """
    Returns "pbkdf2_sha256$iterations$salt$hash".
    """

    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS
    )

    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii")
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:

    try:

        algorithm, iterations, salt, expected = (
            stored_hash.split("$")
        )

    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        base64.b64decode(salt),
        int(iterations)
    )

    return hmac.compare_digest(
        digest,
        base64.b64decode(expected)
    )


# =========================================================
# LOGIN SESSION TOKENS
# =========================================================

def _hash_token(token: str) -> str:

    # Only the token's hash is stored, so a leaked
    # database can't be used to log in.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_session(user_id: int) -> str:

    token = secrets.token_urlsafe(32)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(days=SESSION_DAYS)
    ).isoformat()

    database.create_auth_session(
        _hash_token(token),
        user_id,
        expires_at
    )

    return token


# =========================================================
# SIGNUP / LOGIN / LOGOUT
# =========================================================

def _normalize_email(email: str) -> str:

    return (email or "").strip().lower()


def signup(email: str, password: str) -> dict:

    email = _normalize_email(email)

    if not email or not password:
        raise AuthError("Email and password are required.")

    if not EMAIL_PATTERN.match(email):
        raise AuthError("Please enter a valid email address.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least "
            f"{MIN_PASSWORD_LENGTH} characters."
        )

    try:

        user = database.create_user(
            email,
            hash_password(password)
        )

    except sqlite3.IntegrityError:

        raise AuthError(
            "An account with this email already exists.",
            status_code=409
        )

    return {
        "token": _create_session(user["id"]),
        "user": user
    }


def login(email: str, password: str) -> dict:

    email = _normalize_email(email)

    if not email or not password:
        raise AuthError("Email and password are required.")

    credentials = database.get_user_credentials(email)

    # Same message for unknown email and wrong password.
    if (
        credentials is None
        or not verify_password(
            password,
            credentials["password_hash"]
        )
    ):
        raise AuthError(
            "Invalid email or password.",
            status_code=401
        )

    database.delete_expired_auth_sessions()

    return {
        "token": _create_session(credentials["id"]),
        "user": database.get_user_by_id(credentials["id"])
    }


def logout(token: str):

    database.delete_auth_session(_hash_token(token))


def get_user_for_token(token: str | None) -> dict | None:

    if not token:
        return None

    return database.get_user_by_token_hash(
        _hash_token(token)
    )
