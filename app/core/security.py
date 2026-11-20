"""Security Helpers - FastAPI Security() dependencies that authenticate API requests.

Core auth module: verify_token compares the HTTPBearer credential against the shared etown_bearer_token that almost every route declares, and verify_user_token decodes a per-user access JWT from auth/tokens for the /api/auth session model.

Features:
    - Static Bearer Token: verify_token() compares the header against `etown_bearer_token`
    - Per-User JWT Auth: verify_user_token() decodes the access JWT and returns its claims
    - FastAPI Integration: both dependencies use HTTPBearer via Security()

Classes:
    No classes defined (module uses function-based security dependencies)

Functions:
    verify_token(): Compares the bearer token against the etown_bearer_token env var
    verify_user_token(): Decodes a per-user access JWT and returns its claims

Dependencies:
    - fastapi: HTTPException for 401 responses, Security() for dependency injection
    - fastapi.security: HTTPBearer scheme and HTTPAuthorizationCredentials
    - os: Reads the etown_bearer_token environment variable
    - app.etown.auth.tokens: decode_access_token() and TokenError, used by verify_user_token()

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    02-Aug-2026: Added verify_user_token for the per-user access JWTs issued by /api/auth.
"""

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Return the bearer token if it matches etown_bearer_token, else raise a 401 HTTPException."""
    if credentials.credentials != os.getenv("etown_bearer_token"):
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token")
    return credentials.credentials


def verify_user_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    """Decode a per-user access JWT issued by /api/auth and return its contactid, userprofileid and role.

    Raises:
        HTTPException: 401 when the token is expired or fails validation.
    """
    from app.etown.auth.tokens import TokenError, decode_access_token

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc

    return {
        "contactid": claims.get("contactid"),
        "userprofileid": claims.get("userprofileid"),
        "role": claims.get("role"),
    }
