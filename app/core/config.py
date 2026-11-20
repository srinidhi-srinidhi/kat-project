"""Application Config - environment detection, .env loading and the shared settings singleton.

Core configuration module that loads .env.local, .env, .env.<APP_ENV>.local and .env.<APP_ENV> at import without overriding variables already set, then builds the module-level settings object for every service, resolving secrets from Azure Key Vault when configured and from environment variables otherwise.

Features:
    - Environment Detection: Automatic environment identification (development/staging/production/test)
    - Cascading Configuration: Hierarchical .env file loading with precedence rules
    - Service Configuration: Centralized settings for Azure OpenAI, Google Gemini, PostgreSQL, and external APIs
    - CORS Management: Cross-origin resource sharing policy configuration
    - Database Settings: PostgreSQL connection parameters and schema configuration
    - External API Integration: PowerApps, Mem0, and other third-party service settings
    - Security Configuration: API keys, bearer tokens, and authentication settings
    - Performance Settings: Connection pooling, timeout, and caching parameters
    - Voice & Video Calling: 100ms.live configuration for voice and video calling with quality controls
    - Notification Centre: Per-channel kill switches, shadow mode, scheduler, and LLM digest copy pool settings
    - Passwordless Email Auth: AUTH_* provider selection, JWT/pepper secrets, TTLs, throttles, magic-link URLs
    - Key Vault Secrets: Azure Key Vault lookup per environment with env-var fallback
    - Classifier Flags: CLASSIFIER_MODE (off/shadow/live) and related bridge settings
    - Data Query Flags: DATA_QUERY_MODE (off/shadow/live) and the read-only login for query_my_data

Classes:
    Environment: Enum defining application environment types (development/staging/production/test)
    Settings: Main configuration class containing all application settings and external service parameters

Functions:
    get_environment(): Determines current application environment from environment variables
    load_env_file(): Implements cascading .env file loading with precedence rules
    parse_list_from_env(): Utility for parsing comma-separated lists from environment variables
    parse_dict_of_lists_from_env(): Utility for parsing dictionary structures from environment variables
    parse_int_from_env(): Parse an integer environment variable, falling back to a default on absence/error
    parse_float_from_env(): Parse a float environment variable, falling back to a default on absence/error
    Settings._env_secret_name(): Selects the dev vs prod Key Vault secret name for the running environment
    Settings._env_vault_credentials(): Selects the dev vs prod Key Vault URL and service-principal credentials
    Settings._fetch_secret_from_keyvault(): Fetch a Key Vault secret, falling back to an env var

Dependencies:
    - logging: Module logger for env-parse and Key Vault fallbacks
    - os: Environment variable access and system interaction
    - dotenv: .env file loading and environment variable management
    - enum: Type-safe environment enumeration
    - pathlib: File system path manipulation for .env file discovery
    - azure.identity / azure.keyvault.secrets: Lazily imported inside _fetch_secret_from_keyvault to pull secrets from Azure Key Vault

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    11-Mar-2026: Added dedicated EtownDB pool sizing settings for user endpoint performance tuning
    19-Mar-2026: Added video call configuration support with separate templates and quality controls
    05-Jun-2026: Added notification centre settings - kill switches, shadow mode, scheduler, digest pool.
    08-Jul-2026: Added invite SMS template IDs for English and Tamil to support new invite flow
    09-Jul-2026: Added TERMINAL_LOG_RETENTION_DAYS and log-retention sweep settings (enable flag + interval)
    16-Jul-2026: Removed unused duplicate settings and all legacy PowerApps flow URLs.
    02-Aug-2026: Added the AUTH_* block backing passwordless email login.
    03-Aug-2026: Added AISEARCH_* settings - admission limit, wait and query-embedding cache.
    19-Aug-2026: Added AUTH_REGISTRATION_TTL_SECONDS for pre-registration tokens.
    24-Aug-2026: Added SUPPORT_EMAIL, the inbox contact-support tickets are routed to.
    26-Aug-2026: Added a module logger; env-parse and Key Vault fallbacks now log.
    31-Aug-2026: Added PLAN_PAYMENT_EMAIL, the inbox plan payment requests are routed to.
    01-Sep-2026: Removed PLAN_PAYMENT_EMAIL; plan payments now go to SUPPORT_EMAIL.
    03-Sep-2026: Added TOWN_MAPPING_ENABLED and PLAN_PAYMENT_EXTRA_EMAILS.
    04-Sep-2026: DB password reads Key Vault with the env-matched SP; added TOWN_MAPPING_LINK_MAX_KM.
    15-Sep-2026: Added UNINSTALL_DETECTION_ENABLED and UNINSTALL_PROBE_ENABLED; both off by default.
    16-Sep-2026: Added WHATSAPP_FOS_BUSINESS_ONBOARDING_TEMPLATE_NAME and SUPPORT_PHONE.
    18-Sep-2026: Added FOS_ONBOARD_MAX_DISTANCE_KM and FOS_ONBOARD_TOO_FAR_MESSAGE.
    21-Sep-2026: Added FOS_ONBOARD_PROXIMITY_CHECK_ENABLED and FOS_ONBOARD_OWN_NUMBER_MESSAGE.
    23-Sep-2026: FOS proximity flag is a master switch; added FOS onboarding SMS template settings.
    24-Sep-2026: Added BOOKING_ALERT_SMS_TEMPLATE_ID_EN and BOOKING_ALERT_SMS_MESSAGE_EN.
    25-Sep-2026: Added FOS_ONBOARDING_WHATSAPP_STATUS_WAIT_SECONDS (default 120) for the onboarding SMS fallback.
    28-Sep-2026: Added IMAGE_WATERMARK_ENABLED (default true) for the generated-image watermark.
    05-Oct-2026: Added the event/update submitted-for-approval WhatsApp templates, review URL paths, COMMUNITY_REVIEW_BASE_URL and the COMMUNITY_REVIEW_WHATSAPP/EMAIL_RECIPIENTS lists.
    06-Oct-2026: Replaced the review URL paths with COMMUNITY_REVIEW_APP_ID; COMMUNITY_REVIEW_BASE_URL falls back to DATAVERSE_URL.
    08-Oct-2026: Added WHATSAPP_TOWN_IMAGE_REQUEST_TEMPLATE_NAME and TOWN_IMAGE_REVIEW_PATH; Azure OpenAI, Translator, Gemini and Google Maps keys now read from the dev Key Vault under .env-named secrets; dev Graph secret renamed; removed Sentry and LangSmith settings.
    09-Oct-2026: RapidAPI key now read from the dev Key Vault; TOWN_IMAGE_REVIEW_PATH now opens the ets_townreview record; Azure OpenAI, Translator, Gemini, Google Maps, RapidAPI and Graph Key Vault secret names are now required from .env, with no in-code default or env-var fallback; Azure OpenAI secret names are now one per model, and those keys plus Translator, Gemini, Google Maps and RapidAPI read the environment's Key Vault instead of always the dev one; removed the unused gpt-4o-audio-preview settings.
    12-Oct-2026: Added COMMUNITY_VIEW_RETENTION_ENABLED (default true), COMMUNITY_VIEW_RETENTION_DAYS (default 30) and TOWN_PHOTO_MIN_RELEVANCE (default 80).
    13-Oct-2026: Added GUEST_CONTACT_ID and GUEST_USER_PROFILE_ID, read from .env, for the shared guest account.
    16-Oct-2026: Added LUCIDE_STATIC_URL, the Lucide icon CDN base used for category icons.
    20-Oct-2026: Added EVENT_STATUS_REFRESH_ENABLED (default true) and EVENT_OPEN_ENDED_HOURS (default 48).
    21-Oct-2026: Added PROVIDER_SEO_SYNC_ENABLED (default false).
    22-Oct-2026: Added WHATSAPP_SUBSCRIPTION_INVOICE_TEMPLATE_NAME (default etown_silver_subscription_invoice).
    26-Oct-2026: Added AUTH_STATIC_OTP_NUMBERS, number:otp pairs for test accounts that get a fixed OTP and no SMS.
    26-Oct-2026: Added PROVIDER_IMAGE_AUTOGENERATE (default true).
    27-Oct-2026: Added DATA_QUERY_MODE (default off), the read-only data-query settings and CHAATIE_PROCESSING_TEXTS_LLM (default true); PROVIDER_IMAGE_AUTOGENERATE now defaults to false.
"""

import logging
import os
import re
from dotenv import load_dotenv
from enum import Enum
from pathlib import Path

logger = logging.getLogger("app.core.config")

class Environment(str, Enum):
    """Application environment types."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"

def get_environment() -> Environment:
    """Map APP_ENV to an Environment, defaulting to development."""
    env =os.getenv("APP_ENV", "development").lower()
    if env in ["production", "prod"]:
        return Environment.PRODUCTION
    if env in ["staging", "stage"]:
        return Environment.STAGING
    if env == "test":
        return Environment.TEST
    return Environment.DEVELOPMENT

def load_env_file():
    """Load .env.local and .env, then the APP_ENV-specific files, and return the resolved Environment."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    base_env_files = [
        base_dir / ".env.local",
        base_dir / ".env"
    ]
    for env_file in base_env_files:
        if env_file.is_file():
            load_dotenv(dotenv_path=env_file)
            print(f"Pre-loaded base environment from {env_file}")
    env = get_environment()
    print(f"Loading environment: {env}")
    env_specific_files = [
        base_dir / f".env.{env.value}.local",
        base_dir / f".env.{env.value}",
    ]
    
    loaded_files = []
    for env_file in env_specific_files:
        if env_file.is_file():
            load_dotenv(dotenv_path=env_file)
            loaded_files.append(env_file)
            print(f"Loaded environment-specific config from {env_file}")
    if not loaded_files:
        print(f"No environment-specific files found for {env}")
    return env

ENV_FILE = load_env_file()

def parse_list_from_env(env_key, default=None):
    """Return an env var's comma-separated values as a list, or the default when unset."""
    value = os.getenv(env_key)
    if not value:
        return default or []
    value = value.strip("\"'")
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]

def parse_dict_of_lists_from_env(prefix, default_dict=None):
    """Build a dict of lowercased key suffix to comma-separated values from env vars sharing a prefix."""
    result = default_dict or {}
    for key, value in os.environ.items():
        if key.startswith(prefix):
            endpoint = key[len(prefix):].lower()
            if value:
                value = value.strip("\"'")
                result[endpoint] = [item.strip() for item in value.split(",") if item.strip()]
    return result


def parse_int_from_env(env_key: str, default: int) -> int:
    """Return an env var as an int, or the default when unset or not an integer."""
    value = os.getenv(env_key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("[config] %s is not an integer; using the default %s", env_key, default)
        return default


def parse_float_from_env(env_key: str, default: float) -> float:
    """Return an env var as a float, or the default when unset or not a number."""
    value = os.getenv(env_key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("[config] %s is not a number; using the default %s", env_key, default)
        return default

class Settings:
    """Application settings class."""

    def __init__(self):
        """Populate every setting from environment variables and Key Vault secrets."""
        self.ENVIRONMENT = get_environment()

        self.PROJECT_NAME = os.getenv("PROJECT_NAME", "Etown Chaatie")
        self.PROJECT_VERSION = os.getenv("VERSION", "2.0.1")
        self.PROJECT_DESCRIPTION = os.getenv(
            "DESCRIPTION",
            "The ultimate platform-based marketplace🏪 community for services and products."
        )
        self.PROJECT_SUMMARY = os.getenv(
            "PROJECT_SUMMARY",
            "EtownChaatie is a powerful platform-based marketplace community designed to connect people with local businesses, service providers, and product sellers. Whether you're looking for skilled professionals 🛠️, unique products 🎁, or local deals 💸, EtownChaatie brings everything under one roof for a seamless experience."
        )

        self.AZURE_KV_URL = os.getenv("AZURE_KV_URL")
        self.AZURE_KV_CLIENT_ID = os.getenv("AZURE_KV_CLIENT_ID")
        self.AZURE_KV_CLIENT_SECRET = os.getenv("AZURE_KV_CLIENT_SECRET")
        self.AZURE_KV_TENANT_ID = os.getenv("AZURE_KV_TENANT_ID")

        self.AZURE_KV_DEV_URL = os.getenv("AZURE_KV_DEV_URL")
        self.AZURE_KV_DEV_CLIENT_ID = os.getenv("AZURE_KV_DEV_CLIENT_ID")
        self.AZURE_KV_DEV_CLIENT_SECRET = os.getenv("AZURE_KV_DEV_CLIENT_SECRET")
        self.AZURE_KV_DEV_TENANT_ID = os.getenv("AZURE_KV_DEV_TENANT_ID")

        _vault = self._env_vault_credentials()
        _vault_secrets: dict[str, str | None] = {}

        def _vault_secret(name_env_var: str) -> str | None:
            """Fetch the Key Vault secret named by name_env_var, once per distinct secret name."""
            secret_name = os.environ[name_env_var]
            if secret_name not in _vault_secrets:
                _vault_secrets[secret_name] = self._fetch_secret_from_keyvault(secret_name=secret_name, **_vault)
            return _vault_secrets[secret_name]

        self.azure_openai_4o_endpoint = os.getenv("azure_openai_4o_endpoint")
        self.azure_openai_4o_api_key = _vault_secret("AZURE_OPENAI_4O_KV_SECRET_NAME")
        self.azure_openai_4o_api_version = os.getenv("azure_openai_4o_api_version")

        self.azure_openai_4o_mini_endpoint = os.getenv("azure_openai_4o_mini_endpoint")
        self.azure_openai_4o_mini_api_key = _vault_secret("AZURE_OPENAI_4O_MINI_KV_SECRET_NAME")
        self.azure_openai_4o_mini_api_version = os.getenv("azure_openai_4o_mini_api_version")

        self.azure_openai_41_endpoint = os.getenv("azure_openai_4.1_endpoint")
        self.azure_openai_41_api_key = _vault_secret("AZURE_OPENAI_41_KV_SECRET_NAME")
        self.azure_openai_41_api_version = os.getenv("azure_openai_4.1_api_version")

        self.azure_openai_4_turbo_endpoint = os.getenv("azure_openai_4_turbo_endpoint")
        self.azure_openai_4_turbo_api_key = _vault_secret("AZURE_OPENAI_4_TURBO_KV_SECRET_NAME")
        self.azure_openai_4_turbo_api_version = os.getenv("azure_openai_4_turbo_api_version")

        self.azure_openai_txtemb_3_small_endpoint = os.getenv("azure_openai_txtemb_3_small_endpoint")
        self.azure_openai_txtemb_3_small_api_key = _vault_secret("AZURE_OPENAI_TXTEMB_3_SMALL_KV_SECRET_NAME")
        self.azure_openai_txtemb_3_small_api_version = os.getenv("azure_openai_txtemb_3_small_api_version")

        self.azure_translator_key = _vault_secret("AZURE_TRANSLATOR_KV_SECRET_NAME")
        self.azure_translator_endpoint = os.getenv("azure_translator_endpoint")
        self.azure_translator_location = os.getenv("azure_translator_location")

        self.madlad_translate_url = os.getenv("madlad_translate_url", "https://aimodel.etown.com/v1/translate")

        self.image_generate_url = os.getenv("image_generate_url", "https://aimodel.etown.com/v1/images/generations")
        self.image_generate_timeout = parse_float_from_env("image_generate_timeout", 120.0)
        self.IMAGE_WATERMARK_ENABLED = os.getenv("IMAGE_WATERMARK_ENABLED", "true").lower() == "true"
        self.PROVIDER_IMAGE_AUTOGENERATE = os.getenv("PROVIDER_IMAGE_AUTOGENERATE", "false").lower() == "true"
        self.TOWN_PHOTO_MIN_RELEVANCE = min(100, max(0, int(os.getenv("TOWN_PHOTO_MIN_RELEVANCE", "85"))))

        self.gemini_api_key = _vault_secret("GEMINI_KV_SECRET_NAME")

        self.MEM0_VECTOR_STORE_PROVIDER = os.getenv("MEM0_VECTOR_STORE_PROVIDER", "pgvector")

        self.chaatie_access_key = os.getenv("chaatie_access_key")
        self.powerapps_accesskey = os.getenv("powerapps_accesskey")
        self.external_providers_data_insertion_url = os.getenv("external_providers_data_insertion_url")

        self.etown_bearer_token = os.getenv("etown_bearer_token")

        self.google_maps_api_key = _vault_secret("GOOGLE_MAPS_KV_SECRET_NAME")
        self.rapidapi_key = _vault_secret("RAPIDAPI_KV_SECRET_NAME")

        self.HMS_APP_ACCESS_KEY = os.getenv("HMS_APP_ACCESS_KEY")
        self.HMS_APP_SECRET = os.getenv("HMS_APP_SECRET")
        self.HMS_BASE_URL = os.getenv("HMS_BASE_URL", "https://api.100ms.live/v2/")
        
        self.HMS_TEMPLATE_ID = os.getenv("HMS_TEMPLATE_ID")
        self.HMS_VIDEO_TEMPLATE_ID = os.getenv("HMS_VIDEO_TEMPLATE_ID")
        
        self.HMS_REGION = os.getenv("HMS_REGION", "auto")
        self.HMS_CALL_TIMEOUT_HOURS = parse_int_from_env("HMS_CALL_TIMEOUT_HOURS", 2)
        self.HMS_MAX_CALLS_PER_HOUR_PER_USER = parse_int_from_env("HMS_MAX_CALLS_PER_HOUR_PER_USER", 10)
        
        self.HMS_DEFAULT_VIDEO_QUALITY = os.getenv("HMS_DEFAULT_VIDEO_QUALITY", "medium")
        self.HMS_MAX_VIDEO_BITRATE = parse_int_from_env("HMS_MAX_VIDEO_BITRATE", 1000)
        self.HMS_ENABLE_SCREEN_SHARE = os.getenv("HMS_ENABLE_SCREEN_SHARE", "true").lower() == "true"

        self.FCM_ENDPOINT = os.getenv("FCM_ENDPOINT", "https://fcm.googleapis.com/v1/projects/etowndev/messages:send")
        self.FCM_PROJECT_ID = os.getenv("FCM_PROJECT_ID", "etowndev")

        self.NOTIFICATIONS_SHADOW_MODE = os.getenv(
            "NOTIFICATIONS_SHADOW_MODE", "false"
        ).lower() == "true"
        self.NOTIFICATIONS_DISABLED_TYPES = os.getenv("NOTIFICATIONS_DISABLED_TYPES")
        self.PUSH_ENABLED = os.getenv("PUSH_ENABLED", "true").lower() == "true"
        self.WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "true").lower() == "true"
        self.SMS_ENABLED = os.getenv("SMS_ENABLED", "true").lower() == "true"
        self.INAPP_ENABLED = os.getenv("INAPP_ENABLED", "true").lower() == "true"
        self.FCM_SERVER_MINT_ENABLED = os.getenv("FCM_SERVER_MINT_ENABLED", "true").lower() == "true"
        self.NOTIFICATIONS_GOVERNANCE_ENABLED = os.getenv(
            "NOTIFICATIONS_GOVERNANCE_ENABLED", "false"
        ).lower() == "true"
        self.NOTIFICATIONS_SCHEDULER_ENABLED = os.getenv(
            "NOTIFICATIONS_SCHEDULER_ENABLED", "false"
        ).lower() == "true"
        self.NOTIFICATIONS_QUEUE_ENABLED = os.getenv(
            "NOTIFICATIONS_QUEUE_ENABLED", "false"
        ).lower() == "true"
        self.NOTIFICATIONS_QUEUE_TICK_SECONDS = parse_int_from_env("NOTIFICATIONS_QUEUE_TICK_SECONDS", 60)
        self.NOTIFICATIONS_QUEUE_SAFETY_POLL_SECONDS = parse_int_from_env("NOTIFICATIONS_QUEUE_SAFETY_POLL_SECONDS", 60)
        self.NOTIFICATIONS_QUEUE_LISTENER_KEEPALIVE_SECONDS = parse_int_from_env(
            "NOTIFICATIONS_QUEUE_LISTENER_KEEPALIVE_SECONDS", 300
        )
        self.NOTIFICATIONS_QUEUE_ALERT_EVERY_N_REAPS = parse_int_from_env(
            "NOTIFICATIONS_QUEUE_ALERT_EVERY_N_REAPS", 5
        )
        self.NOTIFICATIONS_QUEUE_CONCURRENCY = parse_int_from_env("NOTIFICATIONS_QUEUE_CONCURRENCY", 20)
        self.NOTIFICATIONS_QUEUE_MARKETING_RATE = parse_int_from_env("NOTIFICATIONS_QUEUE_MARKETING_RATE", 50)
        self.NOTIFICATIONS_QUEUE_VISIBILITY_TIMEOUT = parse_int_from_env("NOTIFICATIONS_QUEUE_VISIBILITY_TIMEOUT", 120)
        self.NOTIFICATIONS_QUEUE_ALERT_DEPTH = parse_int_from_env("NOTIFICATIONS_QUEUE_ALERT_DEPTH", 1000)
        self.NOTIFICATIONS_QUEUE_ALERT_AGE_S = parse_int_from_env("NOTIFICATIONS_QUEUE_ALERT_AGE_S", 600)
        self.NOTIFICATIONS_DIGEST_ENABLED = os.getenv(
            "NOTIFICATIONS_DIGEST_ENABLED", "false"
        ).lower() == "true"
        self.NOTIFICATIONS_MORNING_DIGEST_ENABLED = (
            self.NOTIFICATIONS_DIGEST_ENABLED
            and os.getenv("NOTIFICATIONS_MORNING_DIGEST_ENABLED", "false").lower() == "true"
        )
        self.NOTIFICATIONS_EVENING_DIGEST_ENABLED = (
            self.NOTIFICATIONS_DIGEST_ENABLED
            and os.getenv("NOTIFICATIONS_EVENING_DIGEST_ENABLED", "false").lower() == "true"
        )
        self.MORNING_DIGEST_HOUR = parse_int_from_env("MORNING_DIGEST_HOUR", 8)
        self.MORNING_DIGEST_MINUTE = parse_int_from_env("MORNING_DIGEST_MINUTE", 0)
        self.EVENING_DIGEST_HOUR = parse_int_from_env("EVENING_DIGEST_HOUR", 19)
        self.EVENING_DIGEST_MINUTE = parse_int_from_env("EVENING_DIGEST_MINUTE", 0)
        self.MORNING_DIGEST_MIN_OFFERS = parse_int_from_env("MORNING_DIGEST_MIN_OFFERS", 1)
        self.MORNING_DIGEST_MAX_RECIPIENTS = parse_int_from_env("MORNING_DIGEST_MAX_RECIPIENTS", 500)
        self.NOTIFICATIONS_LLM_DIGEST_ENABLED = os.getenv(
            "NOTIFICATIONS_LLM_DIGEST_ENABLED", "false"
        ).lower() == "true"
        self.MORNING_DIGEST_COPY_POOL_SIZE = parse_int_from_env("MORNING_DIGEST_COPY_POOL_SIZE", 30)
        self.MORNING_DIGEST_COPY_REFRESH_WEEKDAY = parse_int_from_env(
            "MORNING_DIGEST_COPY_REFRESH_WEEKDAY", 0
        )

        self.SEARCH_METADATA_REFRESH_ENABLED = os.getenv(
            "SEARCH_METADATA_REFRESH_ENABLED", "true"
        ).lower() == "true"
        self.SEARCH_METADATA_REFRESH_SECONDS = parse_int_from_env(
            "SEARCH_METADATA_REFRESH_SECONDS", 300
        )
        self.MORNING_DIGEST_COPY_RETENTION_DAYS = parse_int_from_env(
            "MORNING_DIGEST_COPY_RETENTION_DAYS", 30
        )
        self.NOTIFICATIONS_RATING_REMINDER_ENABLED = (
            self.NOTIFICATIONS_DIGEST_ENABLED
            and os.getenv("NOTIFICATIONS_RATING_REMINDER_ENABLED", "false").lower() == "true"
        )
        self.RATING_REMINDER_HOUR = parse_int_from_env("RATING_REMINDER_HOUR", 12)
        self.RATING_REMINDER_MINUTE = parse_int_from_env("RATING_REMINDER_MINUTE", 0)
        self.RATING_REMINDER_MAX_RECIPIENTS = parse_int_from_env("RATING_REMINDER_MAX_RECIPIENTS", 1000)
        self.RATING_REMINDER_COPY_POOL_SIZE = parse_int_from_env("RATING_REMINDER_COPY_POOL_SIZE", 20)
        self.RATING_REMINDER_COPY_REFRESH_WEEKDAY = parse_int_from_env(
            "RATING_REMINDER_COPY_REFRESH_WEEKDAY", 0
        )

        _classifier_mode = os.getenv("CLASSIFIER_MODE", "off").strip().lower()
        self.CLASSIFIER_MODE = _classifier_mode if _classifier_mode in ("off", "shadow", "live") else "off"
        _prompt_logging = os.getenv("CLASSIFIER_PROMPT_LOGGING", "full").strip().lower()
        self.CLASSIFIER_PROMPT_LOGGING = (
            _prompt_logging if _prompt_logging in ("full", "truncate", "hash", "off") else "full"
        )

        _data_query_mode = os.getenv("DATA_QUERY_MODE", "off").strip().lower()
        self.DATA_QUERY_MODE = _data_query_mode if _data_query_mode in ("off", "shadow", "live") else "off"
        self.DATA_QUERY_DB_USER = os.getenv("DATA_QUERY_DB_USER", "").strip()
        self.DATA_QUERY_MAX_ROWS = parse_int_from_env("DATA_QUERY_MAX_ROWS", 50)
        self.DATA_QUERY_STATEMENT_TIMEOUT_MS = parse_int_from_env("DATA_QUERY_STATEMENT_TIMEOUT_MS", 3000)
        self.DATA_QUERY_MAX_PLAN_COST = float(parse_int_from_env("DATA_QUERY_MAX_PLAN_COST", 200000))
        self.DATA_QUERY_POOL_MAX_SIZE = parse_int_from_env("DATA_QUERY_POOL_MAX_SIZE", 4)
        self.CHAATIE_PROCESSING_TEXTS_LLM = (
            os.getenv("CHAATIE_PROCESSING_TEXTS_LLM", "true").strip().lower() == "true"
        )

        self.AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
        self.AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER")
        self.LUCIDE_STATIC_URL = (os.getenv("LUCIDE_STATIC_URL") or "").strip().rstrip("/")

        self.SMS_API_URL = os.getenv("SMS_API_URL")
        self.SMS_ACCESS_TOKEN = os.getenv("SMS_ACCESS_TOKEN")
        self.SMS_AUTH_SIGNATURE = os.getenv("SMS_AUTH_SIGNATURE")
        self.SMS_EXPIRE = os.getenv("SMS_EXPIRE")
        self.SMS_ROUTE = os.getenv("SMS_ROUTE")
        self.SMS_HEADER = os.getenv("SMS_HEADER")
        self.SMS_CONTENT_TYPE = os.getenv("SMS_CONTENT_TYPE")
        self.SMS_ENTITY_ID = os.getenv("SMS_ENTITY_ID")
        self.SMS_TEMPLATE_ID_EN = os.getenv("SMS_TEMPLATE_ID_EN")
        self.SMS_TEMPLATE_ID_TA = os.getenv("SMS_TEMPLATE_ID_TA")
        self.SMS_MESSAGE_TEMPLATE_EN = os.getenv("SMS_MESSAGE_TEMPLATE_EN")
        self.SMS_MESSAGE_TEMPLATE_TA = os.getenv("SMS_MESSAGE_TEMPLATE_TA")
        self.SMS_REMOVE_DUPLICATE_NUMBERS = os.getenv("SMS_REMOVE_DUPLICATE_NUMBERS")

        self.OTP_TEMPLATE_ID_EN = os.getenv("OTP_TEMPLATE_ID_EN")
        self.OTP_TEMPLATE_ID_TA = os.getenv("OTP_TEMPLATE_ID_TA")
        self.OTP_CONTENT_TYPE_EN = os.getenv("OTP_CONTENT_TYPE_EN")
        self.OTP_CONTENT_TYPE_TA = os.getenv("OTP_CONTENT_TYPE_TA")
        self.OTP_ACCESS_TOKEN = os.getenv("OTP_ACCESS_TOKEN")
        self.OTP_AUTH_SIGNATURE = os.getenv("OTP_AUTH_SIGNATURE")
        self.OTP_EXPIRE = os.getenv("OTP_EXPIRE")
        self.OTP_ROUTE = os.getenv("OTP_ROUTE")
        self.OTP_HEADER = os.getenv("OTP_HEADER")
        self.OTP_ENTITY_ID = os.getenv("OTP_ENTITY_ID")
        self.OTP_REMOVE_DUPLICATE_NUMBERS = os.getenv("OTP_REMOVE_DUPLICATE_NUMBERS")
        self.OTP_API_URL = os.getenv("OTP_API_URL")

        self.BOOK_SMS_TEMPLATE_ID_EN = os.getenv("BOOK_SMS_TEMPLATE_ID_EN")
        self.BOOK_SMS_TEMPLATE_ID_TA = os.getenv("BOOK_SMS_TEMPLATE_ID_TA")
        self.BOOK_SMS_CONTENT_TYPE_EN = os.getenv("BOOK_SMS_CONTENT_TYPE_EN")
        self.BOOK_SMS_CONTENT_TYPE_TA = os.getenv("BOOK_SMS_CONTENT_TYPE_TA")
        self.BOOK_SMS_ACCESS_TOKEN = os.getenv("BOOK_SMS_ACCESS_TOKEN")
        self.BOOK_SMS_AUTH_SIGNATURE = os.getenv("BOOK_SMS_AUTH_SIGNATURE")
        self.BOOK_SMS_EXPIRE = os.getenv("BOOK_SMS_EXPIRE")
        self.BOOK_SMS_ROUTE = os.getenv("BOOK_SMS_ROUTE")
        self.BOOK_SMS_HEADER = os.getenv("BOOK_SMS_HEADER")
        self.BOOK_SMS_ENTITY_ID = os.getenv("BOOK_SMS_ENTITY_ID")
        self.BOOK_SMS_REMOVE_DUPLICATE_NUMBERS = os.getenv("BOOK_SMS_REMOVE_DUPLICATE_NUMBERS")
        self.BOOK_SMS_API_URL = os.getenv("BOOK_SMS_API_URL")

        self.INVITE_SMS_TEMPLATE_ID_EN = os.getenv("INVITE_SMS_TEMPLATE_ID_EN")
        self.INVITE_SMS_TEMPLATE_ID_TA = os.getenv("INVITE_SMS_TEMPLATE_ID_TA")

        self.MYCIRCLE_SMS_TEMPLATE_ID_EN = os.getenv("MYCIRCLE_SMS_TEMPLATE_ID_EN")
        self.MYCIRCLE_SMS_TEMPLATE_ID_TA = os.getenv("MYCIRCLE_SMS_TEMPLATE_ID_TA")
        self.MYCIRCLE_SMS_MESSAGE_EN = os.getenv("MYCIRCLE_SMS_MESSAGE_EN")
        self.MYCIRCLE_SMS_MESSAGE_TA = os.getenv("MYCIRCLE_SMS_MESSAGE_TA")

        self.FOS_ONBOARDING_SMS_TEMPLATE_ID_EN = os.getenv("FOS_ONBOARDING_SMS_TEMPLATE_ID_EN")
        self.FOS_ONBOARDING_SMS_MESSAGE_EN = os.getenv("FOS_ONBOARDING_SMS_MESSAGE_EN")
        self.FOS_ONBOARDING_WHATSAPP_STATUS_WAIT_SECONDS = parse_float_from_env(
            "FOS_ONBOARDING_WHATSAPP_STATUS_WAIT_SECONDS", 120.0
        )

        self.BOOKING_ALERT_SMS_TEMPLATE_ID_EN = os.getenv("BOOKING_ALERT_SMS_TEMPLATE_ID_EN")
        self.BOOKING_ALERT_SMS_MESSAGE_EN = os.getenv("BOOKING_ALERT_SMS_MESSAGE_EN")

        self.WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
        self.WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        self.WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
        self.WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
        self.WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID")
        self.WHATSAPP_API_BASE_URL = os.getenv(
            "WHATSAPP_API_BASE_URL", "https://graph.facebook.com"
        )
        self.WHATSAPP_OFFER_TEMPLATE_NAME = os.getenv("WHATSAPP_OFFER_TEMPLATE_NAME")
        self.WHATSAPP_OTP_TEMPLATE_NAME = os.getenv("WHATSAPP_OTP_TEMPLATE_NAME")
        self.WHATSAPP_WELCOME_TEMPLATE_NAME = os.getenv("WHATSAPP_WELCOME_TEMPLATE_NAME")
        self.WHATSAPP_BOOKING_REQUEST_TEMPLATE_NAME = os.getenv("WHATSAPP_BOOKING_REQUEST_TEMPLATE_NAME")
        self.WHATSAPP_BOOKING_ACCEPTED_TEMPLATE_NAME = os.getenv("WHATSAPP_BOOKING_ACCEPTED_TEMPLATE_NAME")
        self.WHATSAPP_BOOKING_COMPLETED_TEMPLATE_NAME = os.getenv("WHATSAPP_BOOKING_COMPLETED_TEMPLATE_NAME")
        self.WHATSAPP_UNVERIFIED_PROVIDER_TEMPLATE_NAME = os.getenv("WHATSAPP_UNVERIFIED_PROVIDER_TEMPLATE_NAME")
        self.WHATSAPP_UNVERIFIED_PROVIDER_ENQUIRY_TEMPLATE_NAME = os.getenv("WHATSAPP_UNVERIFIED_PROVIDER_ENQUIRY_TEMPLATE_NAME")
        self.WHATSAPP_UNVERIFIED_PROVIDER_FALLBACK_TEMPLATE_NAME = os.getenv("WHATSAPP_UNVERIFIED_PROVIDER_FALLBACK_TEMPLATE_NAME")
        self.WHATSAPP_FOS_BUSINESS_ONBOARDING_TEMPLATE_NAME = os.getenv("WHATSAPP_FOS_BUSINESS_ONBOARDING_TEMPLATE_NAME")
        self.WHATSAPP_EVENT_SUBMITTED_TEMPLATE_NAME = os.getenv(
            "WHATSAPP_EVENT_SUBMITTED_TEMPLATE_NAME", "event_submitted_for_approval"
        )
        self.WHATSAPP_UPDATE_SUBMITTED_TEMPLATE_NAME = os.getenv(
            "WHATSAPP_UPDATE_SUBMITTED_TEMPLATE_NAME", "update_submitted_for_approval"
        )
        self.WHATSAPP_TOWN_IMAGE_REQUEST_TEMPLATE_NAME = os.getenv(
            "WHATSAPP_TOWN_IMAGE_REQUEST_TEMPLATE_NAME", "town_image_change_request"
        )
        self.WHATSAPP_SUBSCRIPTION_INVOICE_TEMPLATE_NAME = os.getenv(
            "WHATSAPP_SUBSCRIPTION_INVOICE_TEMPLATE_NAME", "etown_silver_subscription_invoice"
        )
        self.TOWN_IMAGE_REVIEW_PATH = os.getenv(
            "TOWN_IMAGE_REVIEW_PATH",
            "main.aspx?appid={appid}&pagetype=entityrecord&etn=ets_townreview&id={townreviewid}",
        )
        self.COMMUNITY_REVIEW_APP_ID = os.getenv("COMMUNITY_REVIEW_APP_ID", "")
        self.COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS = parse_list_from_env(
            "COMMUNITY_REVIEW_WHATSAPP_RECIPIENTS", []
        )
        self.COMMUNITY_REVIEW_EMAIL_RECIPIENTS = parse_list_from_env(
            "COMMUNITY_REVIEW_EMAIL_RECIPIENTS", []
        )
        self.COMMUNITY_REVIEW_BASE_URL = (
            os.getenv("COMMUNITY_REVIEW_BASE_URL")
            or os.getenv("DATAVERSE_URL")
            or "https://etownprod.crm5.dynamics.com"
        )
        self.WHATSAPP_DEFAULT_LANGUAGE = os.getenv("WHATSAPP_DEFAULT_LANGUAGE", "en")
        self.WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
        self.WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
        self.WHATSAPP_REQUEST_TIMEOUT_SECONDS = parse_float_from_env(
            "WHATSAPP_REQUEST_TIMEOUT_SECONDS", 15.0
        )

        self.APNS_CERTIFICATE_PATH = os.getenv("APNS_CERTIFICATE_PATH")
        self.APNS_CERTIFICATE_PASSPHRASE = os.getenv("APNS_CERTIFICATE_PASSPHRASE")
        self.APNS_BUNDLE_ID = os.getenv("APNS_BUNDLE_ID")
        self.APNS_ENVIRONMENT = os.getenv("APNS_ENVIRONMENT", "sandbox")
        self.APNS_SANDBOX_URL = "https://api.sandbox.push.apple.com"
        self.APNS_PRODUCTION_URL = "https://api.push.apple.com"

        self.INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID")
        self.INSTAGRAM_REDIRECT_URI = os.getenv("INSTAGRAM_REDIRECT_URI")

        _redirect = self.INSTAGRAM_REDIRECT_URI or ""
        if _redirect:
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(_redirect)
            _derived = f"{_parsed.scheme}://{_parsed.netloc}"
        else:
            _derived = "https://api.etown.com"
        self.API_BASE_URL = os.getenv("API_BASE_URL", _derived)
        self.INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "EtownSecIG2026")

        self.APP_OPEN_URL = os.getenv("APP_OPEN_URL", "https://www.etown.com/openapp").rstrip("/")
        self.APP_LINK_CAMPAIGN_CODE = os.getenv("APP_LINK_CAMPAIGN_CODE", "ETCM")

        self.INSTAGRAM_APP_SECRET = self._fetch_secret_from_keyvault(
            secret_name=os.getenv("INSTAGRAM_KV_SECRET_NAME", "Instagram-API-Secret"),
            fallback_env_var="INSTAGRAM_APP_SECRET",
            **_vault,
        )

        self.INSIGHTS_RECENT_WINDOW_HOURS = parse_int_from_env("INSIGHTS_RECENT_WINDOW_HOURS", 24)
        self.INSIGHTS_RECENT_INTERVAL_HOURS = parse_int_from_env("INSIGHTS_RECENT_INTERVAL_HOURS", 4)
        self.INSIGHTS_MATURE_INTERVAL_HOURS = parse_int_from_env("INSIGHTS_MATURE_INTERVAL_HOURS", 24)
        self.INSIGHTS_MAX_POST_AGE_DAYS = parse_int_from_env("INSIGHTS_MAX_POST_AGE_DAYS", 30)
        self.INSIGHTS_BATCH_CONCURRENCY = parse_int_from_env("INSIGHTS_BATCH_CONCURRENCY", 10)

        _snapshot_windows = parse_list_from_env("INSIGHTS_SNAPSHOT_WINDOWS_DAYS", ["7", "15", "30"])
        _windows = sorted({int(w) for w in _snapshot_windows if str(w).strip().isdigit()})
        self.INSIGHTS_SNAPSHOT_WINDOWS_DAYS = _windows or [7, 15, 30]
        self.INSIGHTS_CANONICAL_WINDOW_DAYS = max(self.INSIGHTS_SNAPSHOT_WINDOWS_DAYS)

        self.FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
        self.FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")
        self.FACEBOOK_REDIRECT_URI = os.getenv("FACEBOOK_REDIRECT_URI")
        self.FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v24.0")
        self.FACEBOOK_WEBHOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_WEBHOOK_VERIFY_TOKEN", "EtownSecFB2026")

        self.FACEBOOK_APP_SECRET = self._fetch_secret_from_keyvault(
            secret_name=os.getenv("FACEBOOK_KV_SECRET_NAME", "Facebook-App-Secret"),
            fallback_env_var="FACEBOOK_APP_SECRET",
            **_vault,
        )

        self.FACEBOOK_PAGE_TOKEN = self._fetch_secret_from_keyvault(
            secret_name=os.getenv("FACEBOOK_PAGE_TOKEN_KV_SECRET_NAME", "Facebook-Page-Token"),
            fallback_env_var="FACEBOOK_PAGE_TOKEN",
            **_vault,
        )

        self.madlad_api_key = self._fetch_secret_from_keyvault(
            secret_name="localai-madlad-api-key",
            fallback_env_var="madlad_api_key",
        )

        self.DATAVERSE_CLIENT_ID = os.getenv("DATAVERSE_CLIENT_ID")
        self.DATAVERSE_CLIENT_SECRET = os.getenv("DATAVERSE_CLIENT_SECRET")
        self.DATAVERSE_TENANT_ID = os.getenv("DATAVERSE_TENANT_ID")
        self.DATAVERSE_URL = os.getenv("DATAVERSE_URL")

        self.ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL")
        self.ACCOUNT_DELETION_EMAIL_ENABLED = os.getenv("ACCOUNT_DELETION_EMAIL_ENABLED", "true").lower() == "true"

        self.ACCOUNT_DELETION_ENABLED = os.getenv("ACCOUNT_DELETION_ENABLED", "true").lower() == "true"
        self.ACCOUNT_DEACTIVATION_ENABLED = os.getenv("ACCOUNT_DEACTIVATION_ENABLED", "true").lower() == "true"

        self.ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED = os.getenv(
            "ACCOUNT_DATAVERSE_LIFECYCLE_SYNC_ENABLED", "true"
        ).lower() == "true"

        self.PROVIDER_REVIEW_DATAVERSE_SYNC_ENABLED = os.getenv(
            "PROVIDER_REVIEW_DATAVERSE_SYNC_ENABLED", "true"
        ).lower() == "true"

        self.PROVIDER_SEO_SYNC_ENABLED = os.getenv("PROVIDER_SEO_SYNC_ENABLED", "false").lower() == "true"

        self.ACCOUNT_PURGE_ENABLED = os.getenv("ACCOUNT_PURGE_ENABLED", "true").lower() == "true"
        self.PLAN_EXPIRY_ENABLED = os.getenv("PLAN_EXPIRY_ENABLED", "false").lower() == "true"
        self.PLAN_RETENTION_ENABLED = os.getenv("PLAN_RETENTION_ENABLED", "false").lower() == "true"
        self.COMMUNITY_VIEW_RETENTION_ENABLED = (
            os.getenv("COMMUNITY_VIEW_RETENTION_ENABLED", "true").lower() == "true"
        )
        self.COMMUNITY_VIEW_RETENTION_DAYS = parse_int_from_env("COMMUNITY_VIEW_RETENTION_DAYS", 30)
        self.EVENT_STATUS_REFRESH_ENABLED = (
            os.getenv("EVENT_STATUS_REFRESH_ENABLED", "true").lower() == "true"
        )
        self.EVENT_OPEN_ENDED_HOURS = parse_int_from_env("EVENT_OPEN_ENDED_HOURS", 48)
        self.TOWN_MAPPING_ENABLED = os.getenv("TOWN_MAPPING_ENABLED", "false").lower() == "true"
        _town_link_reach = (os.getenv("TOWN_MAPPING_LINK_MAX_KM") or "").strip()
        try:
            self.TOWN_MAPPING_LINK_MAX_KM = float(_town_link_reach) if _town_link_reach else None
        except ValueError:
            print(f"[config] WARNING: TOWN_MAPPING_LINK_MAX_KM={_town_link_reach!r} is not a "
                  f"number; falling back to the absorb radius")
            self.TOWN_MAPPING_LINK_MAX_KM = None
        self.UNINSTALL_DETECTION_ENABLED = os.getenv("UNINSTALL_DETECTION_ENABLED", "false").lower() == "true"
        self.UNINSTALL_PROBE_ENABLED = os.getenv("UNINSTALL_PROBE_ENABLED", "false").lower() == "true"
        self.PLAN_USAGE_NUDGE_ENABLED = os.getenv("PLAN_USAGE_NUDGE_ENABLED", "false").lower() == "true"
        self.ACCOUNT_PURGE_AFTER_DAYS = parse_int_from_env("ACCOUNT_PURGE_AFTER_DAYS", 90)

        self.FOS_ONBOARD_PROXIMITY_CHECK_ENABLED = (
            os.getenv("FOS_ONBOARD_PROXIMITY_CHECK_ENABLED", "false").lower() == "true"
        )
        self.FOS_ONBOARD_MAX_DISTANCE_KM = parse_float_from_env("FOS_ONBOARD_MAX_DISTANCE_KM", 1.0)
        self.FOS_ONBOARD_TOO_FAR_MESSAGE = os.getenv(
            "FOS_ONBOARD_TOO_FAR_MESSAGE", "Please go to provider's location to onboard"
        )
        self.FOS_ONBOARD_OWN_NUMBER_MESSAGE = os.getenv(
            "FOS_ONBOARD_OWN_NUMBER_MESSAGE",
            "You cannot onboard a business on your own mobile number - please register it "
            "from your own account.",
        )

        self.GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID")
        self.GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID")
        self.GRAPH_CLIENT_SECRET = self._fetch_secret_from_keyvault(
            secret_name=self._env_secret_name(
                "GRAPH_CLIENT_SECRET_KV_SECRET_NAME",
                dev="spmailetowndevpython-secret",
                prod="EtownProd-Mail-Python-AppSecret",
            ),
            fallback_env_var="GRAPH_CLIENT_SECRET",
            **self._env_vault_credentials("GRAPH_CLIENT_SECRET_KV_URL"),
        )
        self.GRAPH_SENDER_EMAIL = os.getenv("GRAPH_SENDER_EMAIL")
        self.SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@etown.com")
        self.SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "")
        self.PLAN_PAYMENT_EXTRA_EMAILS = parse_list_from_env("PLAN_PAYMENT_EXTRA_EMAILS", [])
        self.BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS = parse_list_from_env(
            "BOOKING_EMAIL_EXCLUDED_MOBILE_NUMBERS", []
        )
        self.BOOKING_ALERT_SMS_RECIPIENTS = parse_list_from_env(
            "BOOKING_ALERT_SMS_RECIPIENTS", []
        )

        self.MESSAGE_ENCRYPTION_PASSPHRASE = os.getenv(
            "MESSAGE_ENCRYPTION_PASSPHRASE", "etown:direct-message:message_text:v1"
        )
        self.MESSAGE_ENCRYPTION_SALT = os.getenv(
            "MESSAGE_ENCRYPTION_SALT", "etown-messages-aesgcm-salt-v1"
        )
        self.MESSAGE_ENCRYPTION_ITERATIONS = parse_int_from_env(
            "MESSAGE_ENCRYPTION_ITERATIONS", 200_000
        )

        self.AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local").lower()
        self.AUTH_JWT_SECRET = self._fetch_secret_from_keyvault(
            secret_name=self._env_secret_name(
                "AUTH_JWT_SECRET_KV_SECRET_NAME",
                dev="EtownDev-Auth-JWT-Secret",
                prod="EtownProd-Auth-JWT-Secret",
            ),
            fallback_env_var="AUTH_JWT_SECRET",
            **self._env_vault_credentials("AUTH_KV_URL"),
        )
        self.AUTH_OTP_PEPPER = self._fetch_secret_from_keyvault(
            secret_name=self._env_secret_name(
                "AUTH_OTP_PEPPER_KV_SECRET_NAME",
                dev="EtownDev-Auth-OTP-Pepper",
                prod="EtownProd-Auth-OTP-Pepper",
            ),
            fallback_env_var="AUTH_OTP_PEPPER",
            **self._env_vault_credentials("AUTH_KV_URL"),
        )
        self.AUTH_JWT_ALGORITHM = os.getenv("AUTH_JWT_ALGORITHM", "HS256")
        self.AUTH_JWT_ISSUER = os.getenv("AUTH_JWT_ISSUER", "etown-agents-api")
        self.AUTH_JWT_AUDIENCE = os.getenv("AUTH_JWT_AUDIENCE", "etown-app")
        self.AUTH_ACCESS_TTL_SECONDS = parse_int_from_env("AUTH_ACCESS_TTL_SECONDS", 900)
        self.AUTH_REGISTRATION_TTL_SECONDS = parse_int_from_env("AUTH_REGISTRATION_TTL_SECONDS", 900)
        self.AUTH_REFRESH_TTL_DAYS = parse_int_from_env("AUTH_REFRESH_TTL_DAYS", 30)
        self.AUTH_OTP_TTL_SECONDS = parse_int_from_env("AUTH_OTP_TTL_SECONDS", 300)
        self.AUTH_OTP_MAX_ATTEMPTS = parse_int_from_env("AUTH_OTP_MAX_ATTEMPTS", 5)
        self.AUTH_MAGICLINK_TTL_SECONDS = parse_int_from_env("AUTH_MAGICLINK_TTL_SECONDS", 600)
        self.AUTH_EXCHANGE_TTL_SECONDS = parse_int_from_env("AUTH_EXCHANGE_TTL_SECONDS", 120)
        self.AUTH_RESEND_COOLDOWN_SECONDS = parse_int_from_env("AUTH_RESEND_COOLDOWN_SECONDS", 60)
        self.AUTH_MAX_SENDS_PER_HOUR = parse_int_from_env("AUTH_MAX_SENDS_PER_HOUR", 10)
        self.AUTH_MAGICLINK_API_BASE = os.getenv("AUTH_MAGICLINK_API_BASE", "")
        self.AUTH_MAGICLINK_REDIRECT_BASE = os.getenv("AUTH_MAGICLINK_REDIRECT_BASE", "")
        self.AUTH_RETURN_OTP_IN_RESPONSE = (
            os.getenv("AUTH_RETURN_OTP_IN_RESPONSE", "false").lower() == "true"
        )
        self.AUTH_STATIC_OTP_NUMBERS = {
            re.sub(r"\D", "", number)[-10:]: otp.strip()
            for number, _, otp in (
                entry.partition(":") for entry in parse_list_from_env("AUTH_STATIC_OTP_NUMBERS", [])
            )
            if otp.strip()
        }

        self.AUTH_IOS_TEAM_ID = os.getenv("AUTH_IOS_TEAM_ID", "2KHZ7CU3JA")
        self.AUTH_IOS_BUNDLE_ID = os.getenv("AUTH_IOS_BUNDLE_ID", "com.etown.etownapp")
        self.AUTH_ANDROID_PACKAGE_NAME = os.getenv("AUTH_ANDROID_PACKAGE_NAME", "com.etown.etownapp")
        self.AUTH_ANDROID_SHA256_FINGERPRINTS = parse_list_from_env(
            "AUTH_ANDROID_SHA256_FINGERPRINTS", []
        )
        self.AUTH_APPLINK_PATHS = parse_list_from_env("AUTH_APPLINK_PATHS", ["/auth/*"])

        self.DEBUG = os.getenv("DEBUG", "false").lower() == "true"

        self.DBHOST = os.getenv("DB_HOST")
        self.DBPORT = os.getenv("DB_PORT") or "5432"
        self.DATABASENAME = os.getenv("DB_NAME")
        self.DBUSERNAME = os.getenv("DB_USER")
        self.DB_PASSWORD_KV_SECRET_NAME = os.getenv(
            "DB_PASSWORD_KV_SECRET_NAME", "Etown-Dev-DB-PostgresSQL-Server-Password"
        )
        self.DBPASSWORD = self._fetch_secret_from_keyvault(
            secret_name=self.DB_PASSWORD_KV_SECRET_NAME,
            fallback_env_var="DB_PASSWORD",
            **self._env_vault_credentials("DB_PASSWORD_KV_URL"),
        )
        self.DATA_QUERY_DB_PASSWORD = (
            self._fetch_secret_from_keyvault(
                secret_name=os.getenv(
                    "DATA_QUERY_DB_PASSWORD_KV_SECRET_NAME", "Etown-Chaatie-ReadOnly-DB-Password"
                ),
                fallback_env_var="DATA_QUERY_DB_PASSWORD",
                **self._env_vault_credentials("DB_PASSWORD_KV_URL"),
            )
            if self.DATA_QUERY_MODE != "off" and self.DATA_QUERY_DB_USER
            else None
        )
        self.DEFAULT_SCHEMA = os.getenv("DEFAULT_SCHEMA", "public")

        self.LANGGRAPH_RECURSION_LIMIT = int(
            os.getenv("LANGGRAPH_RECURSION_LIMIT")
            or os.getenv("LANGGRAPGH_RECURSION_LIMIT")
            or 25
        )
        self.GUEST_CONTACT_ID = os.getenv("GUEST_CONTACT_ID", "").strip()
        self.GUEST_USER_PROFILE_ID = os.getenv("GUEST_USER_PROFILE_ID", "").strip()
        
        self.LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FORMAT = os.getenv("LOG_FORMAT", "console")
        self.ENABLE_DATABASE_LOGGING = os.getenv("ENABLE_DATABASE_LOGGING", "true").lower() == "true"        
        self.DB_LOG_QUEUE_SIZE = int(os.getenv("DB_LOG_QUEUE_SIZE", "10000"))
        self.DB_LOG_FLUSH_INTERVAL = float(os.getenv("DB_LOG_FLUSH_INTERVAL", "2.0"))
        self.DB_LOG_BATCH_SIZE = int(os.getenv("DB_LOG_BATCH_SIZE", "100"))
        self.DB_LOG_FALLBACK_TO_CONSOLE = os.getenv("DB_LOG_FALLBACK_TO_CONSOLE", "true").lower() == "true"
        self.DB_LOG_RETENTION_DAYS = int(os.getenv("DB_LOG_RETENTION_DAYS", "30"))
        self.TERMINAL_LOG_RETENTION_DAYS = int(os.getenv("TERMINAL_LOG_RETENTION_DAYS", "30"))
        self.SEARCH_APPEARANCE_RETENTION_DAYS = int(os.getenv("SEARCH_APPEARANCE_RETENTION_DAYS", "180"))
        self.LOG_RETENTION_SWEEP_ENABLED = os.getenv("LOG_RETENTION_SWEEP_ENABLED", "true").lower() == "true"
        self.LOG_RETENTION_SWEEP_INTERVAL_HOURS = int(os.getenv("LOG_RETENTION_SWEEP_INTERVAL_HOURS", "168"))
        self.CORS_ORIGINS = parse_list_from_env("CORS_ORIGINS", ["*"])
        self.CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"
        self.CORS_ALLOW_METHODS = parse_list_from_env("CORS_ALLOW_METHODS", ["*"])
        self.CORS_ALLOW_HEADERS = parse_list_from_env("CORS_ALLOW_HEADERS", ["*"])

        self.WEB_CONCURRENCY = max(1, parse_int_from_env("WEB_CONCURRENCY", 2))

        self.DB_POOL_TOTAL_MAX_CONN = max(10, parse_int_from_env("DB_POOL_TOTAL_MAX_CONN", 40))
        default_db_pool_max = max(10, self.DB_POOL_TOTAL_MAX_CONN // self.WEB_CONCURRENCY)
        self.DB_POOL_MAX_SIZE = max(1, parse_int_from_env("DB_POOL_MAX_SIZE", default_db_pool_max))
        self.DB_POOL_MIN_SIZE = max(1, parse_int_from_env("DB_POOL_MIN_SIZE", min(10, self.DB_POOL_MAX_SIZE)))
        if self.DB_POOL_MIN_SIZE > self.DB_POOL_MAX_SIZE:
            self.DB_POOL_MIN_SIZE = self.DB_POOL_MAX_SIZE

        self.ETOWN_DB_POOL_MAX_SIZE = max(1, parse_int_from_env("ETOWN_DB_POOL_MAX_SIZE", self.DB_POOL_MAX_SIZE))
        self.ETOWN_DB_POOL_MIN_SIZE = max(1, parse_int_from_env("ETOWN_DB_POOL_MIN_SIZE", self.DB_POOL_MIN_SIZE))
        if self.ETOWN_DB_POOL_MIN_SIZE > self.ETOWN_DB_POOL_MAX_SIZE:
            self.ETOWN_DB_POOL_MIN_SIZE = self.ETOWN_DB_POOL_MAX_SIZE

        self.DB_POOL_CONNECT_TIMEOUT_SECONDS = parse_float_from_env("DB_POOL_CONNECT_TIMEOUT_SECONDS", 30.0)
        self.DB_POOL_ACQUIRE_TIMEOUT_SECONDS = parse_float_from_env("DB_POOL_ACQUIRE_TIMEOUT_SECONDS", 10.0)
        self.DB_POOL_COMMAND_TIMEOUT_SECONDS = parse_float_from_env("DB_POOL_COMMAND_TIMEOUT_SECONDS", 30.0)
        self.DB_POOL_IDLE_LIFETIME_SECONDS = parse_float_from_env("DB_POOL_IDLE_LIFETIME_SECONDS", 120.0)
        self.DB_POOL_MAX_QUERIES = max(1000, parse_int_from_env("DB_POOL_MAX_QUERIES", 50000))
        self.DB_POOL_STATEMENT_CACHE_SIZE = max(0, parse_int_from_env("DB_POOL_STATEMENT_CACHE_SIZE", 1024))
        self.DB_STATEMENT_TIMEOUT_MS = max(0, parse_int_from_env("DB_STATEMENT_TIMEOUT_MS", 15000))
        self.DB_IVFFLAT_PROBES = max(0, parse_int_from_env("DB_IVFFLAT_PROBES", 0))

        self.AISEARCH_MAX_CONCURRENT_SEARCHES = max(
            1,
            parse_int_from_env("AISEARCH_MAX_CONCURRENT_SEARCHES", max(8, self.DB_POOL_MAX_SIZE * 2)),
        )
        self.AISEARCH_ADMISSION_WAIT_SECONDS = parse_float_from_env("AISEARCH_ADMISSION_WAIT_SECONDS", 2.0)
        self.AISEARCH_EMBEDDING_CACHE_SIZE = max(0, parse_int_from_env("AISEARCH_EMBEDDING_CACHE_SIZE", 1024))
        self.AISEARCH_EMBEDDING_CACHE_TTL_SECONDS = parse_float_from_env(
            "AISEARCH_EMBEDDING_CACHE_TTL_SECONDS", 900.0
        )
        self.AISEARCH_RESULT_CACHE_SIZE = max(0, parse_int_from_env("AISEARCH_RESULT_CACHE_SIZE", 512))
        self.AISEARCH_RESULT_CACHE_TTL_SECONDS = parse_float_from_env(
            "AISEARCH_RESULT_CACHE_TTL_SECONDS", 120.0
        )


    def _env_secret_name(self, override_env_var: str, *, dev: str, prod: str) -> str:
        """Return the override env var's secret name, else the prod or dev name for the loaded environment."""
        return os.getenv(override_env_var) or (prod if ENV_FILE == Environment.PRODUCTION else dev)

    def _env_vault_credentials(self, url_override_env_var: str | None = None) -> dict:
        """Return the environment's complete Key Vault credential set, else the other one, with an optional URL override."""
        prod_set = (
            self.AZURE_KV_URL, self.AZURE_KV_CLIENT_ID,
            self.AZURE_KV_CLIENT_SECRET, self.AZURE_KV_TENANT_ID,
        )
        dev_set = (
            self.AZURE_KV_DEV_URL, self.AZURE_KV_DEV_CLIENT_ID,
            self.AZURE_KV_DEV_CLIENT_SECRET, self.AZURE_KV_DEV_TENANT_ID,
        )
        preferred, alternate = (
            (prod_set, dev_set) if ENV_FILE == Environment.PRODUCTION else (dev_set, prod_set)
        )
        url, client_id, client_secret, tenant_id = preferred if all(preferred) else alternate
        return {
            "vault_url": (os.getenv(url_override_env_var) if url_override_env_var else None) or url,
            "client_id": client_id,
            "client_secret": client_secret,
            "tenant_id": tenant_id,
        }

    def _fetch_secret_from_keyvault(
        self,
        secret_name: str,
        fallback_env_var: str | None = None,
        vault_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
    ) -> str | None:
        """Fetch a secret from Azure Key Vault, falling back to an env var when unconfigured or the fetch fails.

        Args:
            secret_name: Name of the secret in Key Vault.
            fallback_env_var: Env var read when Key Vault is unconfigured or the fetch fails; None for vault-only secrets.
            vault_url: Optional vault URL override; defaults to AZURE_KV_URL.
            client_id: Optional client ID override; defaults to AZURE_KV_CLIENT_ID.
            client_secret: Optional client secret override; defaults to AZURE_KV_CLIENT_SECRET.
            tenant_id: Optional tenant ID override; defaults to AZURE_KV_TENANT_ID.

        Returns:
            The secret value, or None when neither source has it.
        """
        fallback = os.getenv(fallback_env_var) if fallback_env_var else None

        kv_url = vault_url or self.AZURE_KV_URL
        kv_client_id = client_id or self.AZURE_KV_CLIENT_ID
        kv_client_secret = client_secret or self.AZURE_KV_CLIENT_SECRET
        kv_tenant_id = tenant_id or self.AZURE_KV_TENANT_ID

        if not all([kv_url, kv_client_id, kv_client_secret, kv_tenant_id]):
            if fallback:
                print(f"[config] '{secret_name}' loaded from env var '{fallback_env_var}' (Key Vault not configured)")
            elif not fallback_env_var:
                print(f"[config] WARNING: '{secret_name}' not available — Key Vault not configured")
            else:
                print(f"[config] WARNING: '{secret_name}' not available — Key Vault not configured and '{fallback_env_var}' env var is unset")
            return fallback

        try:
            from azure.identity import ClientSecretCredential
            from azure.keyvault.secrets import SecretClient

            credential = ClientSecretCredential(
                tenant_id=kv_tenant_id,
                client_id=kv_client_id,
                client_secret=kv_client_secret,
            )
            client = SecretClient(vault_url=kv_url, credential=credential)
            secret = client.get_secret(secret_name)
            print(f"[config] '{secret_name}' fetched from Azure Key Vault: {kv_url}")
            return secret.value
        except Exception as exc:
            if not fallback_env_var:
                logger.warning("[config] failed to fetch %r from Key Vault", secret_name, exc_info=True)
                print(f"[config] WARNING: Failed to fetch '{secret_name}' from Key Vault ({exc}).")
                return None
            logger.warning("[config] failed to fetch %r from Key Vault; falling back to the env var", secret_name, exc_info=True)
            print(f"[config] WARNING: Failed to fetch '{secret_name}' from Key Vault ({exc}). Falling back to env var.")
            return fallback


settings = Settings()
