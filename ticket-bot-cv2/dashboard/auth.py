"""Session authentication for the Florida State Dashboard.

The dashboard is an administration surface, so access is gated by a password the
owner sets on first run (stored as a PBKDF2 hash, never in plain text). Sessions
live in memory: restarting the container signs everyone out, which is the safest
default for a self-hosted admin panel.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

PBKDF2_ITERATIONS = 240_000
SESSION_COOKIE = "fsd_session"
CSRF_COOKIE = "fsd_csrf"
SESSION_TTL_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 5
MAX_LOGIN_FAILURES = 6
FAILURE_WINDOW_SECONDS = 300
OAUTH_STATE_TTL_SECONDS = 600


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def check_password_strength(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if password.strip() != password:
        return "Do not pad the password with spaces."
    return None


def _now() -> float:
    return time.monotonic()


class Sessions:
    """In-memory session table with sliding expiry and CSRF tokens."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def create(self, user: dict | None = None, *, method: str = "password") -> dict:
        token = secrets.token_urlsafe(32)
        session = {
            "token": token,
            "csrf": secrets.token_urlsafe(24),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires_at": _now() + SESSION_TTL_SECONDS,
            "method": method,
            "user": user or {},
        }
        self._sessions[token] = session
        return session

    def resolve(self, token: str | None) -> dict | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if session["expires_at"] < _now():
            self._sessions.pop(token, None)
            return None
        session["expires_at"] = _now() + SESSION_TTL_SECONDS
        return session

    def destroy(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def prune(self) -> None:
        for token in [key for key, value in self._sessions.items() if value["expires_at"] < _now()]:
            self._sessions.pop(token, None)


class LoginThrottle:
    """Blocks password guessing from a single address."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    def _recent(self, address: str) -> list[float]:
        cutoff = _now() - FAILURE_WINDOW_SECONDS
        attempts = [stamp for stamp in self._failures.get(address, []) if stamp > cutoff]
        self._failures[address] = attempts
        return attempts

    def blocked(self, address: str) -> bool:
        return len(self._recent(address)) >= MAX_LOGIN_FAILURES

    def record_failure(self, address: str) -> None:
        self._failures.setdefault(address, []).append(_now())

    def reset(self, address: str) -> None:
        self._failures.pop(address, None)


class OAuthStates:
    """Short-lived CSRF state values for the optional Discord OAuth login."""

    def __init__(self) -> None:
        self._states: dict[str, float] = {}

    def create(self) -> str:
        state = secrets.token_urlsafe(24)
        self._states[state] = _now() + OAUTH_STATE_TTL_SECONDS
        return state

    def consume(self, state: str | None) -> bool:
        if not state:
            return False
        expires = self._states.pop(state, None)
        return expires is not None and expires > _now()
