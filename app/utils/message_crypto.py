"""Message Crypto Helpers - AES-256-GCM at-rest encryption for etown_messages.message_text.

Utility helpers that encrypt message bodies into enc:v1: envelopes before they are stored and decrypt them on read, passing unprefixed legacy plaintext through, so API callers always send and receive plaintext.

Features:
    - Self-describing Envelope: an enc:v1: prefix marks encrypted rows; unprefixed values pass through.
    - Keyless Deployment: the AES-256 key is derived via PBKDF2-HMAC-SHA256 from configured values.
    - Deterministic Derivation: the same inputs always regenerate the same key.
    - Fingerprint Comparison: key_fingerprint() lets environments compare keys without revealing them.
    - Fail-closed Decryption: a tampered, wrong-key or corrupt row returns None instead of raising.
    - Guarded Key Rotation: changing the inputs strands existing rows until they are migrated.

Classes:
    No classes defined (module exposes derivation/envelope helpers and a row-decrypt convenience)

Functions:
    _formula_inputs(): Read passphrase/salt/iterations from settings, applying built-in defaults
    _get_key(): Return the cached AES key, deriving and logging it from settings on first use
    _fingerprint(): Compute a short non-reversible identifier for a derived key
    key_fingerprint(): Public wrapper around _fingerprint() for cross-environment key comparison
    looks_encrypted(): Cheap prefix check for the enc:v1: envelope marker
    _envelope_payload(): Decode and length-validate an envelope's base64 payload, or None if malformed
    is_envelope(): True when a value is a structurally valid enc:v1: envelope
    encrypt_text(): Plaintext -> enc:v1: envelope string for INSERT/UPDATE
    decrypt_text(): Stored value -> plaintext for API responses
    decrypt_fields(): In-place decrypt of named keys on a row dict

Dependencies:
    - cryptography.hazmat.primitives.ciphers.aead.AESGCM: authenticated encryption/decryption
    - cryptography.exceptions.InvalidTag: detects a tampered ciphertext or wrong key on decrypt
    - hashlib: PBKDF2-HMAC-SHA256 key derivation and the key-fingerprint hash
    - app.core.config.settings: MESSAGE_ENCRYPTION_PASSPHRASE / SALT / ITERATIONS
    - app.core.logging.logger: key-derivation and decrypt-failure logging

Updates:
    10-Aug-2026: Initial implementation for etown_messages.message_text with a PBKDF2-derived key.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.logging import logger

_ENVELOPE_PREFIX = "enc:v1:"
_NONCE_BYTES = 12
_KEY_BYTES = 32

_DEFAULT_PASSPHRASE = "etown:direct-message:message_text:v1"
_DEFAULT_SALT = "etown-messages-aesgcm-salt-v1"
_DEFAULT_ITERATIONS = 200_000

_key: Optional[bytes] = None
_key_resolved: bool = False


def _formula_inputs() -> tuple[str, str, int]:
    """Return the (passphrase, salt, iterations) key-derivation inputs from settings, defaulting blanks and invalid counts."""
    passphrase = (getattr(settings, "MESSAGE_ENCRYPTION_PASSPHRASE", None) or "").strip()
    salt = (getattr(settings, "MESSAGE_ENCRYPTION_SALT", None) or "").strip()
    try:
        iterations = int(getattr(settings, "MESSAGE_ENCRYPTION_ITERATIONS", 0) or 0)
    except (TypeError, ValueError):
        logger.warning("message crypto: MESSAGE_ENCRYPTION_ITERATIONS is not an integer; using the default")
        iterations = 0
    return (
        passphrase or _DEFAULT_PASSPHRASE,
        salt or _DEFAULT_SALT,
        iterations if iterations > 0 else _DEFAULT_ITERATIONS,
    )


def _get_key() -> bytes:
    """Return the cached 32-byte AES key, deriving it with PBKDF2-SHA256 on first use."""
    global _key, _key_resolved
    if not _key_resolved:
        passphrase, salt, iterations = _formula_inputs()
        _key = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
            dklen=_KEY_BYTES,
        )
        _key_resolved = True
        using_defaults = (passphrase == _DEFAULT_PASSPHRASE) and (salt == _DEFAULT_SALT)
        logger.info(
            "Message text encryption enabled (AES-256-GCM, PBKDF2 iters=%d, key=%s%s)",
            iterations,
            _fingerprint(_key),
            ", built-in defaults — set MESSAGE_ENCRYPTION_PASSPHRASE / "
            "MESSAGE_ENCRYPTION_SALT in production" if using_defaults else "",
        )
    return _key


def _fingerprint(key: bytes) -> str:
    """Return a short, non-reversible identifier for a derived key."""
    return hashlib.sha256(b"etown-message-key-fingerprint" + key).hexdigest()[:12]


def key_fingerprint() -> str:
    """Return a 12-hex-character fingerprint of the derived key, safe to log or compare across environments."""
    return _fingerprint(_get_key())


def looks_encrypted(value: Any) -> bool:
    """Return True when value is a string starting with the envelope prefix, without validating the body."""
    return isinstance(value, str) and value.startswith(_ENVELOPE_PREFIX)


def _envelope_payload(value: Any) -> Optional[bytes]:
    """Return the base64-decoded envelope payload, or None if value is not valid base64 long enough for nonce and tag."""
    if not looks_encrypted(value):
        return None
    try:
        blob = base64.b64decode(value[len(_ENVELOPE_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        logger.debug("message crypto: envelope prefix present but the body is not valid base64; treating it as plaintext")
        return None
    return blob if len(blob) >= _NONCE_BYTES + 16 else None


def is_envelope(value: Any) -> bool:
    """Return True when ``value`` is a structurally valid ``enc:v1:`` envelope."""
    return _envelope_payload(value) is not None


def encrypt_text(plaintext: Optional[str]) -> Optional[str]:
    """Return message text as an AES-GCM enc:v1 envelope, passing empty or already-encrypted input through unchanged."""
    if not plaintext:
        return plaintext
    if is_envelope(plaintext):
        return plaintext

    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_get_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _ENVELOPE_PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_text(stored: Optional[str]) -> Optional[str]:
    """Return the plaintext of a stored message body, passing non-envelopes through and returning None when decryption fails."""
    blob = _envelope_payload(stored)
    if blob is None:
        return stored

    try:
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(_get_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except (InvalidTag, binascii.Error, ValueError, UnicodeDecodeError) as exc:
        logger.error(
            "Message text decryption failed (%s: %s) — returning empty body",
            type(exc).__name__, exc,
         exc_info=True)
        return None


def decrypt_fields(row: Optional[dict[str, Any]], *fields: str) -> Optional[dict[str, Any]]:
    """Decrypt the named keys of a row dict in place, skipping missing keys, and return the row."""
    if not row:
        return row
    for field in fields:
        if field in row:
            row[field] = decrypt_text(row[field])
    return row
