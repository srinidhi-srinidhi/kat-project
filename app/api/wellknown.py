"""Well-Known Endpoints - HTTP routes serving the iOS Universal Link and Android App Link claims.

Root-mounted, unauthenticated HTTP layer outside /api that serves apple-app-site-association and assetlinks.json from configured app identifiers, letting the magic-link redirect from /api/auth open the Etown app instead of a browser.

Features:
    - AASA: apple-app-site-association served from /.well-known/ and the legacy root path
    - Asset Links: assetlinks.json declaring the Android package and signing fingerprints
    - Fingerprint Normalisation: accepts colon-delimited or bare hex, emits uppercase colon form
    - Fail Loud: 503 rather than a malformed document when identifiers are unconfigured
    - Cacheable: both documents are served with Cache-Control max-age=3600
    - Domain Match: the serving domain must be the one AUTH_MAGICLINK_REDIRECT_BASE points at

Endpoints:
    GET /.well-known/apple-app-site-association: iOS Universal Link claim
    GET /apple-app-site-association: legacy path, same document
    GET /.well-known/assetlinks.json: Android App Link claim

Classes:
    No classes defined (module exposes two FastAPI route handlers and a JSON-response helper)

Functions:
    _json_response(): Serialise a document with the exact content type and cache header both platforms expect
    apple_app_site_association(): Build and serve the AASA document
    android_asset_links(): Build and serve assetlinks.json

Dependencies:
    - fastapi: Routing and Response
    - app.core.config: AUTH_IOS_* / AUTH_ANDROID_* / AUTH_APPLINK_PATHS settings
    - re: Normalises a signing fingerprint (colon-delimited or bare hex) into Google's required form

Updates:
    02-Aug-2026: Initial creation
"""

import json
import logging
import re

from fastapi import APIRouter, Response

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

_JSON = "application/json"


def _json_response(payload, status_code: int = 200) -> Response:
    """Return a document as a cacheable application/json Response with the given status."""
    return Response(
        content=json.dumps(payload, indent=2),
        media_type=_JSON,
        status_code=status_code,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/.well-known/apple-app-site-association")
@router.get("/apple-app-site-association")
async def apple_app_site_association() -> Response:
    """Handle GET /.well-known/apple-app-site-association: serve the iOS AASA document, or 503 when unconfigured."""
    team_id = (settings.AUTH_IOS_TEAM_ID or "").strip()
    bundle_id = (settings.AUTH_IOS_BUNDLE_ID or "").strip()
    if not team_id or not bundle_id:
        logger.error("[wellknown] AASA requested but AUTH_IOS_TEAM_ID/BUNDLE_ID are unset")
        return _json_response(
            {"error": "Universal Link association is not configured on this host"}, 503
        )

    app_id = f"{team_id}.{bundle_id}"
    paths = [p.strip() for p in settings.AUTH_APPLINK_PATHS if p.strip()]

    return _json_response({
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appID": app_id,
                    "appIDs": [app_id],
                    "paths": paths,
                    "components": [
                        {"/": path, "comment": "Etown magic-link sign-in"} for path in paths
                    ],
                }
            ],
        },
        "webcredentials": {"apps": [app_id]},
    })


@router.get("/.well-known/assetlinks.json")
async def android_asset_links() -> Response:
    """Handle GET /.well-known/assetlinks.json: serve the Android App Link document, or 503 when unconfigured."""
    package = (settings.AUTH_ANDROID_PACKAGE_NAME or "").strip()
    raw_prints = settings.AUTH_ANDROID_SHA256_FINGERPRINTS or []

    fingerprints = []
    for raw in raw_prints:
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
        if len(cleaned) != 64:
            logger.error("[wellknown] Ignoring malformed SHA-256 fingerprint (%d hex chars)", len(cleaned))
            continue
        fingerprints.append(":".join(cleaned[i:i + 2] for i in range(0, 64, 2)))

    if not package or not fingerprints:
        logger.error(
            "[wellknown] assetlinks requested but package/fingerprints are unset "
            "(package=%r, usable_fingerprints=%d)", package, len(fingerprints)
        )
        return _json_response(
            {"error": "App Link association is not configured on this host"}, 503
        )

    return _json_response([
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
    ])
