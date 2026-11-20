"""Blob Storage Helpers - shared Azure Blob upload, decoding and URL helpers for all media.

Utility module that every feature imports for media storage: decodes base64 and data URLs, sniffs MIME types, uploads bytes through a cached BlobServiceClient returning a relative blob path, and converts between relative paths and public HTTPS URLs.

Features:
    - Container Name Normalization: Strips leading slashes from Azure container names
    - Base64/Data-URL Decoding: Decodes base64 or data-URL strings to raw bytes for upload
    - Singleton Client: Cached BlobServiceClient to avoid repeated connection setup
    - Async Upload: Non-blocking blob upload returning the /{container}/{blob_name} path
    - Async Download: Non-blocking read of a stored blob's bytes by path or URL
    - Timestamp Prefix: UTC timestamp string for time-ordered blob name generation
    - Content Settings: Configurable MIME type and cache settings for uploaded blobs
    - MIME Sniffing: detect_mime_type() reads a file's magic bytes
    - URL Helpers: Convert between relative blob paths and public HTTPS URLs
    - Error Handling: Graceful handling of missing Azure SDK or invalid input

Classes:
    No classes defined (module uses function-based utilities with module-level singleton)

Functions:
    _strip_data_url_prefix(): Drop the 'data:...;base64,' prefix from a data-URL string
    detect_mime_type(): Sniff a MIME type from a file's leading magic bytes
    get_blob_base_url(): Return the account base URL (no trailing slash)
    normalize_container_name(): Strip leading slashes from a container name
    to_bytes(): Decode a base64 or data-URL string to raw bytes
    get_blob_service_client(): Return cached BlobServiceClient singleton
    upload_blob_bytes(): Upload bytes to Azure Blob and return the blob path
    download_blob_bytes(): Download a blob's bytes by its relative path or public URL
    current_timestamp_prefix(): UTC timestamp string for blob name prefixes
    is_stored_media_url(): True if a value is a stored blob path/URL vs base64 to upload
    to_public_blob_url(): Build a public HTTPS URL from a relative blob path
    to_relative_blob_url(): Strip scheme and host from an absolute blob URL, returning the relative path

Dependencies:
    - asyncio: asyncio.to_thread() runs the blocking Azure SDK upload_blob() call off the event loop
    - base64: Base64 decoding for image and file data
    - binascii: Error handling for invalid base64 input
    - datetime: UTC timestamp generation
    - typing: Type hints for optional parameters
    - app.core.config: Azure Blob Storage connection string and container settings
    - app.core.logging: Centralized logging for upload operations
    - azure.storage.blob: Azure Blob Storage SDK (optional import with fallback)

Updates:
    26-May-2026: Added Features, Classes, Functions, Dependencies, Updates sections to complete module docstring
    10-Jul-2026: Added to_public_blob_url function to build public HTTPS URLs from relative blob paths
    26-Aug-2026: Logged the azure-storage-blob import guard and base-URL resolution failures.
    29-Sep-2026: Added download_blob_bytes().
"""

import asyncio
import base64
import binascii
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import logger

try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
except ImportError:
    logger.warning("azure-storage-blob is not installed; blob uploads are disabled", exc_info=True)
    BlobServiceClient = None
    ContentSettings = None

_blob_service_client: Optional[Any] = None

_BASE64_ALPHABET = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789+/=_-"
)


def _strip_data_url_prefix(value: str) -> str:
    """Return the text after a data-URL "base64," marker, or the value unchanged when absent."""
    if not value:
        return ""
    if "base64," in value:
        return value.split("base64,", 1)[1]
    return value


def detect_mime_type(data: bytes) -> str:
    """Return the MIME type sniffed from a file's magic bytes, or application/octet-stream when unrecognised."""
    if len(data) >= 3 and data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if len(data) >= 8 and data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if len(data) >= 6 and data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if len(data) >= 12 and data[4:8] == b'ftyp':
        return 'video/mp4'
    if len(data) >= 4 and data[:4] == b'\x1aE\xdf\xa3':
        return 'video/webm'
    if len(data) >= 5 and data[:5] == b'%PDF-':
        return 'application/pdf'
    return 'application/octet-stream'


def get_blob_base_url() -> str:
    """Return the Azure Blob account base URL without a trailing slash, or "" if the client cannot initialise."""
    try:
        client = get_blob_service_client()
        return client.url.rstrip('/')
    except Exception:
        logger.warning("blob storage: could not resolve the account base URL", exc_info=True)
        return ''


def normalize_container_name(container_name: Optional[str]) -> str:
    """Return an Azure Blob container name trimmed of whitespace and leading slashes, or "" when empty."""
    if not container_name:
        return ""
    return container_name.strip().lstrip("/")


def to_bytes(value: Optional[str]) -> bytes:
    """Decode base64 or data-URL text to bytes, returning b"" for empty or "null" and raw latin-1 bytes otherwise.

    Raises:
        ValueError: When a data-URL carries invalid base64.
    """
    if value is None:
        return b""
    stripped_value = value.strip()
    if not stripped_value or stripped_value.lower() == "null":
        return b""
    looks_like_data_url = "base64," in value[:64].lower()
    stripped = "".join(value.split())
    is_b64_shape = (
        len(stripped) >= 8
        and len(stripped) % 4 in (0, 2, 3)
        and all(c in _BASE64_ALPHABET for c in stripped)
    )
    if looks_like_data_url or is_b64_shape:
        candidate = _strip_data_url_prefix(value.strip())
        candidate = "".join(candidate.split())
        pad = len(candidate) % 4
        if pad:
            candidate += "=" * (4 - pad)
        try:
            return base64.b64decode(candidate, validate=True)
        except binascii.Error:
            if looks_like_data_url:
                raise ValueError("Invalid base64 input")
    return value.encode("latin-1", errors="surrogateescape")


def get_blob_service_client() -> Any:
    """Return the cached BlobServiceClient, creating it from AZURE_BLOB_CONNECTION_STRING on first use.

    Raises:
        RuntimeError: When azure-storage-blob is missing or the connection string is unset.
    """
    global _blob_service_client
    if BlobServiceClient is None:
        raise RuntimeError("azure-storage-blob is not installed")
    if _blob_service_client is None:
        if not settings.AZURE_BLOB_CONNECTION_STRING:
            raise RuntimeError("AZURE_BLOB_CONNECTION_STRING is not configured")
        _blob_service_client = BlobServiceClient.from_connection_string(
            settings.AZURE_BLOB_CONNECTION_STRING
        )
    return _blob_service_client


async def upload_blob_bytes(
    container_name: str,
    blob_name: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload content to Azure Blob, overwriting, and return /{container_name}/{blob_name}, or "" when content is empty."""
    if not content:
        return ""

    def _upload() -> None:
        service_client = get_blob_service_client()
        container_client = service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob=blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    await asyncio.to_thread(_upload)
    return f"/{container_name}/{blob_name}"


async def download_blob_bytes(blob_path: str) -> bytes:
    """Download a blob by its /{container}/{blob_name} path or public URL and return its bytes.

    Raises:
        ValueError: When the path does not name both a container and a blob.
    """
    container_name, _, blob_name = (to_relative_blob_url(blob_path) or "").lstrip("/").partition("/")
    if not container_name or not blob_name:
        raise ValueError(f"Not a blob path: {blob_path!r}")

    def _download() -> bytes:
        service_client = get_blob_service_client()
        blob_client = service_client.get_container_client(container_name).get_blob_client(blob=blob_name)
        return blob_client.download_blob().readall()

    return await asyncio.to_thread(_download)


def current_timestamp_prefix() -> str:
    """Return a UTC timestamp string (YYYYMMDDHHmmss) for blob name prefixes."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def is_stored_media_url(value: Optional[str]) -> bool:
    """Return True if the value is an http(s) URL or short relative blob path rather than media to upload."""
    if not value:
        return False
    v = value.strip()
    if v.lower().startswith(("http://", "https://")):
        return True
    return v.startswith("/") and len(v) <= 500 and "." in v


def to_public_blob_url(path_or_url: Optional[str]) -> Optional[str]:
    """Prefix a relative blob path with the account base URL, returning empty, absolute or unresolvable input unchanged."""
    if not path_or_url:
        return path_or_url
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    base = get_blob_base_url()
    if not base:
        logger.warning("BLOB_URL_BUILD | Azure blob base URL unavailable — path=%s", path_or_url)
        return path_or_url
    sep = "" if path_or_url.startswith("/") else "/"
    return f"{base}{sep}{path_or_url}"


def to_relative_blob_url(path_or_url: Optional[str]) -> Optional[str]:
    """Strip the scheme and host from an absolute blob URL, returning empty or relative input unchanged."""
    if not path_or_url:
        return path_or_url
    if path_or_url.startswith(("http://", "https://")):
        scheme_end = path_or_url.find("://") + 3
        path_start = path_or_url.find("/", scheme_end)
        return path_or_url[path_start:] if path_start != -1 else "/"
    return path_or_url
