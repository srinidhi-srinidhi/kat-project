"""Azure Translator Helpers - async client for Azure Translator single, detecting and batch translation.

Utility client that posts translation requests to the configured Azure Translator endpoint through the shared HTTP client with per-request timeouts; llm_clients instantiates it as the shared translator used across the platform.

Features:
    - Single text translation: Translate individual text strings
    - Batch translation: Translate multiple text items efficiently  
    - Language detection: Automatic language detection with translation
    - URL normalization: Prevents double slash issues in API endpoints
    - Timeouts: Per-request timeout on every translation call
    - Error handling: Comprehensive error logging and exception handling
    - HTTP client integration: Reuses connection pools for performance

Classes:
    AzureTranslatorUtils: Azure Translator client - translate(), translate_with_detection(), batch_translate()

Functions:
    No functions defined (every translation operation is a method on AzureTranslatorUtils)

Dependencies:
    - os: Environment variable access
    - uuid: Unique request tracing
    - typing: Type annotations
    - app.core.logging: Centralized logging
    - app.utils.http_client: Shared HTTP connection pool

Updates:
    23-Mar-2026: Fixed URL construction and region loading; added timeouts and validation.
"""
import os
import uuid
from typing import List, Dict, Any, Tuple
from app.core.config import settings
from app.core.logging import logger
from app.utils.http_client import get_http_client

class AzureTranslatorUtils:
    def __init__(self, subscription_key: str = None, region: str = None, endpoint: str = None):
        """Build request headers from arguments or azure_translator_* env vars and log config warnings."""
        self.subscription_key = subscription_key or settings.azure_translator_key
        self.region = region or os.getenv("azure_translator_location", "eastus")
        raw_endpoint = endpoint or os.getenv("azure_translator_endpoint")
        self.endpoint = raw_endpoint.rstrip('/') if raw_endpoint else None
        self.headers = {
            'Ocp-Apim-Subscription-Key': self.subscription_key,
            'Ocp-Apim-Subscription-Region': self.region,
            'Content-type': 'application/json',
            'X-ClientTraceId': str(uuid.uuid4())
        }
        self.translate_path = '/translate'
        self._validate_config()

    async def translate(self, input_text: str, language: str = "en") -> str:
        """Return input_text translated into the target language."""
        return await self._post_translation_request(input_text, language)

    async def translate_with_detection(self, input_text: str, language: str = "en") -> Tuple[str, str]:
        """Return the translation of input_text and its detected source language code."""
        translation, detected_language = await self._post_translation_request(input_text, language, detect_language=True)
        return translation, detected_language

    async def batch_translate(self, data: List[Dict[str, Any]], language: str = "en") -> List[Dict[str, Any]]:
        """Translate each item's content field in place and return the list, or [] for empty input."""
        if not data or not isinstance(data, list):
            return []
        
        translations = await self._post_batch_translation_request(data, language)
        return translations

    async def _post_translation_request(self, input_text: str, language: str, detect_language: bool = False) -> Any:
        """POST one text to the translate API, returning the translation or a (translation, language) tuple."""
        params = {
            'api-version': '3.0',
            'to': language
        }
        body = [{'text': input_text}]

        try:
            full_url = self._get_full_url()
            async with get_http_client() as client:
                response = await client.post(
                    full_url,
                    params=params,
                    headers=self.headers,
                    json=body,
                    timeout=10.0
                )
            response.raise_for_status()
            response_data = response.json()

            translation = response_data[0]["translations"][0]["text"]
            detected_language = response_data[0]["detectedLanguage"]["language"] if detect_language else None
            return (translation, detected_language) if detect_language else translation

        except Exception as e:
            await self._log_error(self._get_full_url(), e)
            raise

    async def _post_batch_translation_request(self, data: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
        """POST all items' content to the translate API in one call and overwrite each content with its translation."""
        params = {
            'api-version': '3.0',
            'to': language
        }
        body = [{'text': item['content']} for item in data]

        try:
            full_url = self._get_full_url()
            async with get_http_client() as client:
                response = await client.post(
                    full_url,
                    params=params,
                    headers=self.headers,
                    json=body,
                    timeout=15.0
                )
            response.raise_for_status()
            response_data = response.json()

            for i, item in enumerate(data):
                data[i]['content'] = response_data[i]["translations"][0]["text"]

            return data

        except Exception as e:
            await self._log_error(self._get_full_url(), e)
            raise

    def _validate_config(self):
        """Log warnings for a missing key, endpoint or region, or a non-HTTPS endpoint."""
        if not self.subscription_key:
            logger.warning("Azure Translator subscription key not provided")
        if not self.endpoint:
            logger.warning("Azure Translator endpoint not provided")
        if self.endpoint and not self.endpoint.startswith('https://'):
            logger.warning(f"Azure Translator endpoint should use HTTPS: {self.endpoint}")
        if not self.region:
            logger.warning("Azure Translator region not provided, using default 'eastus'")
        else:
            logger.info(f"Azure Translator configured for region: {self.region}")
        
        logger.debug(f"Azure Translator config - Endpoint: {self.endpoint}, Region: {self.region}, Key present: {bool(self.subscription_key)}")
    
    def _get_full_url(self) -> str:
        """Return the endpoint joined with the translate path."""
        return f"{self.endpoint}{self.translate_path}"

    async def _log_error(self, api_url: str, exception: Exception):
        """Log a translator request failure with its URL."""
        logger.error(f"Error accessing {api_url}: {exception}")
