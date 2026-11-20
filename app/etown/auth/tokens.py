"""Auth Token Helpers - secret generation, peppered hashing and JWT mint and verify.

Auth-layer primitives: generates OTPs, link tokens, exchange codes and refresh tokens with secrets, stores them only as HMAC-SHA256 digests keyed by AUTH_OTP_PEPPER, and mints and verifies HS256 access and registration JWTs.

Features:
    - CSPRNG Secrets: 6-digit OTPs and URL-safe tokens from the ``secrets`` module
    - Peppered Hashing: HMAC-SHA256 digests; the plaintext secret is never stored
    - Constant-Time Compare: hmac.compare_digest on every verification path
    - Access Tokens: HS256 JWTs carrying contactid, userprofileid and role
    - Registration Tokens: short-lived purpose='registration' JWTs, refused by the access-token path
    - Refresh Tokens: opaque high-entropy strings, stored hashed and rotated on use

Classes:
    TokenError(): Raised when an access/registration token fails decoding or validation

Functions:
    _pepper(): Return the configured AUTH_OTP_PEPPER as bytes, or raise if unset
    _jwt_secret(): Return the configured AUTH_JWT_SECRET, or raise if unset
    generate_otp_secret(): Cryptographically secure 6-digit numeric OTP
    generate_url_secret(): URL-safe high-entropy token for links, codes and refresh tokens
    hash_secret(): HMAC-SHA256 digest of a secret under the configured pepper
    secrets_match(): Constant-time comparison of a candidate secret against a stored digest
    mint_access_token(): Encode a signed, expiring access JWT for a resolved identity
    _decode(): Verify a JWT's signature, algorithm, issuer, audience and expiry
    decode_access_token(): Validate and decode an access JWT
    mint_registration_token(): Encode a short-lived JWT for a proven-but-unregistered identifier
    decode_registration_token(): Validate and decode a registration JWT

Dependencies:
    - PyJWT: JWT encode/decode (already a project dependency)
    - secrets/hmac/hashlib: CSPRNG and constant-time digest comparison
    - app.core.config: AUTH_* settings

Updates:
    02-Aug-2026: Initial creation
    19-Aug-2026: Added registration tokens for verified identifiers that own no account.
"""

import datetime
import hashlib
import hmac
import secrets

import jwt

from app.core.config import settings


class TokenError(Exception):
    """Raised when an access token is malformed, expired, or fails signature checks."""


def _pepper() -> bytes:
    """Return AUTH_OTP_PEPPER encoded as UTF-8 bytes.

    Raises:
        RuntimeError: When AUTH_OTP_PEPPER is not configured.
    """
    pepper = settings.AUTH_OTP_PEPPER
    if not pepper:
        raise RuntimeError(
            "AUTH_OTP_PEPPER is not configured. Set it in Key Vault "
            "(Etown-Auth-OTP-Pepper) or as the AUTH_OTP_PEPPER env var."
        )
    return pepper.encode("utf-8")


def _jwt_secret() -> str:
    """Return the AUTH_JWT_SECRET signing secret.

    Raises:
        RuntimeError: When AUTH_JWT_SECRET is not configured.
    """
    secret = settings.AUTH_JWT_SECRET
    if not secret:
        raise RuntimeError(
            "AUTH_JWT_SECRET is not configured. Set it in Key Vault "
            "(Etown-Auth-JWT-Secret) or as the AUTH_JWT_SECRET env var."
        )
    return secret


def generate_otp_secret() -> str:
    """Return a cryptographically secure zero-padded 6-digit OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_url_secret(nbytes: int = 32) -> str:
    """Return a URL-safe base64 secret with nbytes of entropy."""
    return secrets.token_urlsafe(nbytes)


def hash_secret(secret: str) -> str:
    """Return the hex HMAC-SHA256 digest of a secret under the configured pepper.

    Raises:
        RuntimeError: When AUTH_OTP_PEPPER is not configured.
    """
    return hmac.new(_pepper(), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def secrets_match(candidate: str, stored_hash: str) -> bool:
    """Return True when a candidate secret hashes to the stored digest, compared in constant time."""
    return hmac.compare_digest(hash_secret(candidate), stored_hash)


def mint_access_token(contactid: str, userprofileid: str | None, role: int | None) -> tuple[str, int]:
    """Return a signed access JWT for a contact, profile and role, plus its lifetime in seconds.

    Raises:
        RuntimeError: When AUTH_JWT_SECRET is not configured.
    """
    ttl = settings.AUTH_ACCESS_TTL_SECONDS
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": contactid,
        "contactid": contactid,
        "userprofileid": userprofileid,
        "role": role,
        "iss": settings.AUTH_JWT_ISSUER,
        "aud": settings.AUTH_JWT_AUDIENCE,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl),
    }
    token = jwt.encode(payload, _jwt_secret(), algorithm=settings.AUTH_JWT_ALGORITHM)
    return token, ttl


def _decode(token: str, *, allow_expired: bool = False) -> dict:
    """Verify a JWT's signature, algorithm, issuer, audience and optionally expiry, and return its claims.

    Raises:
        TokenError: When the token is malformed or fails any enforced check.
    """
    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[settings.AUTH_JWT_ALGORITHM],
            issuer=settings.AUTH_JWT_ISSUER,
            audience=settings.AUTH_JWT_AUDIENCE,
            options={"verify_exp": not allow_expired},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def decode_access_token(token: str, *, allow_expired: bool = False) -> dict:
    """Validate an access JWT and return its claims; allow_expired skips only the expiry check.

    Raises:
        TokenError: When the token fails validation or is not an access token.
    """
    claims = _decode(token, allow_expired=allow_expired)
    if claims.get("purpose", "access") != "access":
        raise TokenError("Token is not an access token")
    return claims


def mint_registration_token(identifier: str, channel: str) -> tuple[str, int]:
    """Return a short-lived registration JWT proving an identifier over a channel, plus its lifetime in seconds.

    Raises:
        RuntimeError: When AUTH_JWT_SECRET is not configured.
    """
    ttl = settings.AUTH_REGISTRATION_TTL_SECONDS
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": f"{channel}:{identifier}",
        "purpose": "registration",
        "identifier": identifier,
        "channel": channel,
        "iss": settings.AUTH_JWT_ISSUER,
        "aud": settings.AUTH_JWT_AUDIENCE,
        "iat": now,
        "exp": now + datetime.timedelta(seconds=ttl),
    }
    token = jwt.encode(payload, _jwt_secret(), algorithm=settings.AUTH_JWT_ALGORITHM)
    return token, ttl


def decode_registration_token(token: str) -> dict:
    """Validate a registration JWT and return its claims, including identifier and channel.

    Raises:
        TokenError: When the token fails validation or is not a registration token.
    """
    claims = _decode(token)
    if claims.get("purpose") != "registration":
        raise TokenError("Token is not a registration token")
    return claims
