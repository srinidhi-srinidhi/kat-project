"""Auth Provider Contract - the Protocol and value types every authentication backend implements.

Domain contract used by auth_service: defines the AuthProvider Protocol selected through AUTH_PROVIDER, the resolved, challenge and verified identity records, and the structured AuthChallengeError, so a new backend is one class and a setting change.

Features:
    - Identity Outside the Provider: auth_service resolves the Etown contact; a provider receives it and never invents one
    - Structured Errors: AuthChallengeError carries a machine-readable code the service maps to HTTP
    - Transport-Agnostic: no FastAPI or HTTP types cross this boundary

Classes:
    AuthChallengeError: Raised when a challenge cannot be issued, found, or verified
    ResolvedIdentity: An Etown account resolved from an identifier, before verification
    ChallengeHandle: The issued challenge as returned to the caller
    VerifiedIdentity: An identity whose ownership of the identifier has been proven
    AuthProvider: Protocol every authentication backend must satisfy

Functions:
    No functions defined (module exposes only the classes above)

Dependencies:
    - dataclasses/typing: value objects and the structural Protocol

Updates:
    02-Aug-2026: Initial creation
"""

from dataclasses import dataclass
from typing import Optional, Protocol


class AuthChallengeError(Exception):
    """Raised when a challenge cannot be issued, located, or verified.

    Attributes:
        code: Machine-readable reason ('not_found', 'expired', 'consumed',
            'invalid_secret', 'too_many_attempts', 'throttled', 'send_failed').
        message: Human-readable detail safe to return to the caller.
        retry_after: Seconds until the caller may retry, for throttling errors.
    """

    def __init__(self, code: str, message: str, retry_after: Optional[int] = None):
        """Store the reason code, caller-safe message and optional retry delay in seconds."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class ResolvedIdentity:
    """An Etown account resolved from an identifier, before ownership is proven.

    ``contactid`` is None for a 'verify' challenge against an identifier that is not
    registered yet — onboarding proves ownership before the account exists.
    """

    identifier: str
    channel: str
    contactid: Optional[str]
    userprofileid: Optional[str]
    role: Optional[int]


@dataclass(frozen=True)
class ChallengeHandle:
    """An issued challenge, as handed back to the client."""

    request_id: str
    method: str
    expires_in: int
    debug_secret: Optional[str] = None


@dataclass(frozen=True)
class VerifiedIdentity:
    """An identity whose ownership of the identifier has been proven.

    ``contactid`` is None when the proven identifier has no account yet.
    """

    identifier: str
    channel: str
    contactid: Optional[str]
    userprofileid: Optional[str]
    role: Optional[int]


class AuthProvider(Protocol):
    """Structural contract for an authentication backend."""

    name: str

    async def start_challenge(
        self,
        *,
        identity: ResolvedIdentity,
        method: str,
        purpose: str,
        language_code: str,
        device_id: Optional[str],
        request_ip: Optional[str],
        user_agent: Optional[str],
    ) -> ChallengeHandle:
        """Issue and deliver a challenge for a resolved identity.

        Args:
            identity: Account the challenge is for; its channel selects the delivery route.
            method: 'otp', or 'magiclink' for email only.
            purpose: 'login' or 'verify'.
            language_code: Delivery language ('en' or 'ta').
            device_id: Optional client device identifier.
            request_ip: Caller IP, for audit.
            user_agent: Caller user agent, for audit.

        Returns:
            The issued ChallengeHandle.

        Raises:
            AuthChallengeError: On throttling or delivery failure.
        """
        ...

    async def verify_challenge(self, *, request_id: str, secret: str) -> VerifiedIdentity:
        """Verify an OTP against an outstanding challenge, consume it and return the proven identity.

        Raises:
            AuthChallengeError: When the challenge is missing, expired, consumed, exhausted or mismatched.
        """
        ...

    async def consume_magic_link(self, *, link_secret: str) -> str:
        """Consume a magic-link token and return a short-lived one-time exchange code.

        Raises:
            AuthChallengeError: When the link is unknown, expired, or already used.
        """
        ...

    async def exchange_code(self, *, code: str) -> VerifiedIdentity:
        """Redeem a one-time exchange code and return the verified identity it represents.

        Raises:
            AuthChallengeError: When the code is unknown, expired, or already used.
        """
        ...
