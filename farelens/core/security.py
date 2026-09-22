"""Password hashing, API-key hashing, session tokens and secret encryption.

All of these derive from a single application secret (APP_SECRET_KEY). If the
operator did not set one, we generate it once and persist it under
APP_DATA_DIR so sessions and encrypted credentials survive restarts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "fl_"
_ENC_PREFIX = "enc:v1:"


def load_or_create_secret(configured: str, data_dir: Path) -> str:
    """Return the app secret, generating and persisting one if not configured."""
    if configured and configured.strip():
        return configured.strip()

    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / "secret.key"
    if key_file.exists():
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    generated = secrets.token_urlsafe(48)
    key_file.write_text(generated, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:  # Windows / non-POSIX filesystems
        pass
    logger.warning(
        "APP_SECRET_KEY not set; generated one at %s. Set APP_SECRET_KEY explicitly in production.",
        key_file,
    )
    return generated


class SecretBox:
    """Symmetric encryption for credentials stored at rest (Fernet/AES-128-CBC+HMAC)."""

    def __init__(self, app_secret: str):
        digest = hashlib.sha256(f"farelens:fernet:{app_secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None or plaintext == "":
            return ""
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{_ENC_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith(_ENC_PREFIX):
            # Tolerate plaintext (e.g. hand-edited config) so a bad migration
            # never locks the operator out.
            return value
        try:
            return self._fernet.decrypt(value[len(_ENC_PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Stored secret cannot be decrypted. APP_SECRET_KEY has changed since it was saved; "
                "re-enter the credential in the admin UI."
            ) from exc

    @staticmethod
    def is_encrypted(value: str) -> bool:
        return bool(value) and value.startswith(_ENC_PREFIX)


# --- Passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --- API keys ----------------------------------------------------------------


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext_key, prefix_for_display, sha256_hash)."""
    raw = secrets.token_urlsafe(32)
    key = f"{API_KEY_PREFIX}{raw}"
    return key, key[:11], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- Admin sessions (JWT) ---------------------------------------------------

_JWT_ALG = "HS256"


def create_session_token(app_secret: str, *, user_id: int, username: str, hours: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=max(1, hours))).timestamp()),
        "jti": secrets.token_hex(8),
        "scope": "admin",
    }
    return jwt.encode(payload, app_secret, algorithm=_JWT_ALG)


def decode_session_token(app_secret: str, token: str) -> dict | None:
    try:
        payload = jwt.decode(token, app_secret, algorithms=[_JWT_ALG])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != "admin":
        return None
    return payload


def mask_secret(value: str | None) -> str:
    """Show only enough of a secret to recognise it (never enough to use it)."""
    if not value:
        return ""
    raw = str(value)
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}…{raw[-4:]}"
