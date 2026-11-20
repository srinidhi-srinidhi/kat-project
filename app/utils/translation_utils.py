"""Translation Helpers - language detection, translation and localized errors for English, Tamil, Hindi and Telugu.

Utility layer over the shared Azure translator from llm_clients: detects language from Unicode ranges, translates search terms to English and responses into the user's language, re-translates wrong-language replies, and serves localized error messages through a TranslationManager singleton, returning the original text on failure.

Features:
    - Multi-language Support: Translation across English, Tamil, Hindi, and Telugu
    - Language Detection: Unicode-range based language identification from raw text
    - Search Term Translation: non-English terms translated to English for invite and booking flows
    - Response Translation: agent responses translated into the user's language when needed
    - Tamil Catalog: fully covered fixed replies get authored Tamil, shadow-logged beside the translator or served live
    - Language Validation & Correction: re-translates a response in the wrong language
    - Localized Error Messages: static per-language catalog with English fallback
    - Translation Toggles: Module-level flags to switch search-term/output/debug-logging translation on or off
    - Fail-soft Behavior: every translation path returns the original text on failure

Classes:
    SupportedLanguage(Enum): Supported language codes - ENGLISH, TAMIL, HINDI, TELUGU
    TranslationConfig(): Module-wide toggles for search/output/error-message translation and debug logging
    SearchTermTranslator(): Translates a search term to English for internal-invite and booking flows
    OutputTranslator(): Translates response text and database result fields into a target language
    ErrorMessageTranslator(): Static catalog of localized error messages with English fallback
    TranslationManager(): Facade combining the three translators into one singleton (translation_manager)

Functions:
    get_translator(): Import and return the shared Azure translator instance from llm_clients
    translate_text(): Core text translation via the Azure translator, with same-language short-circuit
    get_translate_text_function(): Return the translate_text function itself (indirection helper)
    normalize_language_code(): Map a raw language string/code to a SupportedLanguage enum member
    detect_language_in_text(): Detect language of text via Unicode character ranges (Optional result)
    detect_language(): Detect language of text via Unicode character ranges (defaults to English)
    translate_search_term_for_invite(): Convenience wrapper for the internal-invite search translator
    translate_search_term_for_booking(): Convenience wrapper for the booking search translator
    translate_response_to_user_language(): Convenience wrapper for the output translator
    get_error_message(): Convenience wrapper for the error-message translator
    configure_translation_debug(): Toggle TranslationConfig.DEBUG_TRANSLATION_LOGGING
    configure_search_translation(): Toggle TranslationConfig.SEARCH_TERM_TRANSLATION_ENABLED
    configure_output_translation(): Toggle TranslationConfig.OUTPUT_TRANSLATION_ENABLED
    validate_and_correct_language(): Re-translate a response whose detected language doesn't match the target

Dependencies:
    - app.agent_infrastructure.infrastructure.llm_clients.translator: Azure Translator client
    - app.utils.tamil_catalog: authored Tamil renderings and the TAMIL_CATALOG_MODE flag
    - enum.Enum: SupportedLanguage language-code enumeration
    - typing: Dict/List/Optional/Tuple/Any type hints for translation payloads

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
    21-Oct-2026: OutputTranslator.translate_response shadows or serves the Tamil catalog for fully covered replies.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

from app.agent_infrastructure.infrastructure.llm_clients import translator
from app.utils.tamil_catalog import render_tamil, tamil_catalog_mode
logger = logging.getLogger(__name__)


class SupportedLanguage(Enum):
    ENGLISH = "en"
    TAMIL = "ta"
    HINDI = "hi"
    TELUGU = "te"

LANGUAGE_MAPPINGS = {
    "en": SupportedLanguage.ENGLISH,
    "english": SupportedLanguage.ENGLISH,
    "ta": SupportedLanguage.TAMIL,
    "tamil": SupportedLanguage.TAMIL,
    "hi": SupportedLanguage.HINDI,
    "hindi": SupportedLanguage.HINDI,
    "te": SupportedLanguage.TELUGU,
    "telugu": SupportedLanguage.TELUGU,
}

LANGUAGE_UNICODE_RANGES = {
    SupportedLanguage.TAMIL: ('\u0B80', '\u0BFF'),
    SupportedLanguage.HINDI: ('\u0900', '\u097F'),
    SupportedLanguage.TELUGU: ('\u0C00', '\u0C7F'),
}


class TranslationConfig:
    """Configuration for translation settings"""
    
    SEARCH_TERM_TRANSLATION_ENABLED = True
    SEARCH_TERM_CONFIDENCE_THRESHOLD = 0.7
    
    OUTPUT_TRANSLATION_ENABLED = True
    OUTPUT_FALLBACK_TO_ENGLISH = True
    
    ERROR_MESSAGE_TRANSLATION_ENABLED = True
    
    DEBUG_TRANSLATION_LOGGING = True


async def get_translator():
    """Return the translator from llm_clients, or None when it cannot be imported."""
    try:
        from app.agent_infrastructure.infrastructure.llm_clients import translator
        return translator
    except ImportError:
        logging.error("Failed to import translator from llm_clients")
        return None

async def translate_text(text: str, target_language: str, source_language: str = None):
    """Translate text with the configured translator and return a success/translated_text dict, echoing the input on failure."""
    try:
        translator_instance = await get_translator()
        if not translator_instance:
            return {"success": False, "error": "Translator not available"}
        
        if source_language and source_language == target_language:
            return {"success": True, "translated_text": text}
        
        translated_text = await translator_instance.translate(text, target_language)
        return {
            "success": True,
            "translated_text": translated_text,
            "original_text": text
        }
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        return {
            "success": False, 
            "error": str(e),
            "translated_text": text,
            "original_text": text
        }

async def get_translate_text_function():
    """Return the translate_text coroutine function."""
    return translate_text

def normalize_language_code(language: str) -> SupportedLanguage:
    """Map a language code or name to a SupportedLanguage, defaulting to English."""
    if not language:
        return SupportedLanguage.ENGLISH
    
    normalized = language.lower().strip()
    return LANGUAGE_MAPPINGS.get(normalized, SupportedLanguage.ENGLISH)

def detect_language_in_text(text: str) -> Optional[SupportedLanguage]:
    """Return the first language whose Unicode range appears in the text, English otherwise, or None for empty text."""
    if not text:
        return None
    
    for language, (start, end) in LANGUAGE_UNICODE_RANGES.items():
        if any(start <= char <= end for char in text):
            return language
    
    return SupportedLanguage.ENGLISH


class SearchTermTranslator:
    """Handles translation of search terms for internal invite and booking"""
    
    @staticmethod
    async def translate_search_term(
        search_term: str, 
        source_language: str, 
        target_language: str = "en"
    ) -> Dict[str, Any]:
        """Translate a search term between languages, returning success, original and translated text, and any error."""
        if not search_term or not search_term.strip():
            return {
                "success": False,
                "error": "Empty search term",
                "original_text": search_term,
                "translated_text": search_term
            }
        
        source_lang = normalize_language_code(source_language)
        target_lang = normalize_language_code(target_language)
        
        if source_lang == target_lang:
            return {
                "success": True,
                "original_text": search_term,
                "translated_text": search_term,
                "translation_needed": False
            }
        
        try:
            if TranslationConfig.DEBUG_TRANSLATION_LOGGING:
                logging.info(f"[TRANSLATION] Translating search term: '{search_term}' from {source_lang.value} to {target_lang.value}")
            
            translation_result = await translate_text(
                text=search_term,
                target_language=target_lang.value,
                source_language=source_lang.value
            )
            
            if translation_result.get('success'):
                translated = translation_result['translated_text']
                if TranslationConfig.DEBUG_TRANSLATION_LOGGING:
                    logging.info(f"[TRANSLATION] Success: '{search_term}' -> '{translated}'")
                
                return {
                    "success": True,
                    "original_text": search_term,
                    "translated_text": translated,
                    "translation_needed": True
                }
            else:
                if TranslationConfig.DEBUG_TRANSLATION_LOGGING:
                    logging.warning(f"[TRANSLATION] Failed: {translation_result.get('error')}")
                
                return {
                    "success": False,
                    "error": translation_result.get('error', 'Translation failed'),
                    "original_text": search_term,
                    "translated_text": search_term
                }
                
        except Exception as e:
            logging.error(f"[TRANSLATION] Exception during search term translation: {e}")
            return {
                "success": False,
                "error": str(e),
                "original_text": search_term,
                "translated_text": search_term
            }

    @staticmethod
    async def translate_search_term_for_internal_invite(
        search_term: str, 
        user_language: str
    ) -> Tuple[str, str]:
        """Return (original, English) search terms, translating only when enabled and the user language is not English."""
        if not TranslationConfig.SEARCH_TERM_TRANSLATION_ENABLED:
            return search_term, search_term
        
        user_lang = normalize_language_code(user_language)
        
        if user_lang == SupportedLanguage.ENGLISH:
            return search_term, search_term
        
        translation_result = await SearchTermTranslator.translate_search_term(
            search_term=search_term,
            source_language=user_language,
            target_language="en"
        )
        
        if translation_result["success"]:
            return search_term, translation_result["translated_text"]
        else:
            return search_term, search_term

    @staticmethod
    async def translate_search_term_for_booking(
        search_term: str, 
        user_language: str
    ) -> Tuple[str, str]:
        """Return (original, English) search terms using the internal-invite translation."""
        return await SearchTermTranslator.translate_search_term_for_internal_invite(
            search_term, user_language
        )


class OutputTranslator:
    """Handles translation of system outputs and responses"""
    
    @staticmethod
    async def translate_response(
        response_text: str, 
        target_language: str
    ) -> str:
        """Translate a response into the target language, serving or shadowing the Tamil catalog, returning the original on failure."""
        if not response_text or not TranslationConfig.OUTPUT_TRANSLATION_ENABLED:
            return response_text
        target_lang = normalize_language_code(target_language)
        catalog_mode = tamil_catalog_mode() if target_lang == SupportedLanguage.TAMIL else "off"
        catalog_text = None
        if catalog_mode != "off":
            try:
                catalog_text = render_tamil(response_text)
            except Exception:
                logger.warning("[TAMIL_CATALOG] render failed; using the translator", exc_info=True)
            if catalog_mode == "live" and catalog_text is not None:
                logger.info("[TAMIL_CATALOG] live hit: served the authored Tamil reply")
                return catalog_text
        if target_lang == SupportedLanguage.ENGLISH:
            detected_lang = detect_language_in_text(response_text)
            if detected_lang == SupportedLanguage.ENGLISH or detected_lang is None:
                return response_text
        translated_reply = response_text
        try:
            if TranslationConfig.DEBUG_TRANSLATION_LOGGING:
                logging.info(f"[OUTPUT_TRANSLATION] Translating to {target_lang.value}: '{response_text[:50]}...'")
            translation_result = await translate_text(response_text, target_lang.value)
            if translation_result and translation_result.get("success") and translation_result.get("translated_text"):
                translated = translation_result["translated_text"]
                if translated and translated != response_text:
                    if TranslationConfig.DEBUG_TRANSLATION_LOGGING:
                        logging.info(f"[OUTPUT_TRANSLATION] Success: '{response_text[:30]}...' -> '{translated[:30]}...'")
                    translated_reply = translated
        except Exception as e:
            logging.error(f"[OUTPUT_TRANSLATION] Exception: {e}")
        if catalog_mode == "shadow":
            if catalog_text is not None:
                logger.info("[TAMIL_CATALOG] shadow hit: source=%r catalog=%r translator=%r",response_text, catalog_text, translated_reply,)
            else:
                logger.info("[TAMIL_CATALOG] shadow miss: chars=%d head=%r",len(response_text), response_text[:60],)
        return translated_reply
    @staticmethod
    async def translate_database_results(
        results: List[Dict[str, Any]], 
        target_language: str,
        content_fields: List[str]
    ) -> List[Dict[str, Any]]:
        """Translate the named fields of each result row in place when output translation is enabled."""
        if not results or not TranslationConfig.OUTPUT_TRANSLATION_ENABLED:
            return results
        
        try:
            for result in results:
                for field in content_fields:
                    if field in result and result[field]:
                        translation_result = await translate_text(
                            text=str(result[field]),
                            target_language=target_language
                        )
                        if translation_result.get('success'):
                            result[field] = translation_result['translated_text']
            
            return results
        except Exception as e:
            logging.error(f"[DATABASE_TRANSLATION] Exception: {e}")
            return results


class ErrorMessageTranslator:
    """Handles translation of error messages"""
    
    ERROR_MESSAGES = {
        SupportedLanguage.ENGLISH: {
            "general_error": "Something went wrong, please try again later",
            "missing_info": "Missing required information",
            "translation_failed": "Translation service is temporarily unavailable",
            "no_results": "No results found",
            "invalid_input": "Invalid input provided",
            "pending_rating_search": "Before proceeding with provider search, please complete your rating for the previous booking.",
            "pending_rating_booking": "Before proceeding with booking, please complete your rating for the previous booking.",
            "max_iterations": "Maximum iterations reached without obtaining a final response.",
            "validation_error": "The response could not be validated, please try again",
            "language_mismatch": "Response language doesn't match requested language",
            "tool_error": "Tool execution failed, please retry your request",
            "database_error": "Database operation failed, please try again",
            "authentication_error": "Authentication failed, please check your credentials"
        },
        SupportedLanguage.TAMIL: {
            "general_error": "ஏதோ தவறு நடந்துள்ளது, தயவுசெய்து பின்னர் மீண்டும் முயற்சிக்கவும்",
            "missing_info": "தேவையான தகவல் இல்லை",
            "translation_failed": "மொழிபெயர்ப்பு சேவை தற்காலிகமாக கிடைக்கவில்லை",
            "no_results": "முடிவுகள் எதுவும் கிடைக்கவில்லை",
            "invalid_input": "தவறான உள்ளீடு வழங்கப்பட்டது",
            "pending_rating_search": "வழங்குநர் தேடலைத் தொடரும் முன், முந்தைய பதிவின் மதிப்பீட்டை முடிக்கவும்",
            "pending_rating_booking": "பதிவைத் தொடரும் முன், முந்தைய பதிவின் மதிப்பீட்டை முடிக்கவும்",
            "max_iterations": "அதிகபட்ச முயற்சிகள் எட்டப்பட்டன, இறுதி பதில் கிடைக்கவில்லை",
            "validation_error": "பதில் சரிபார்க்க முடியவில்லை, தயவுசெய்து மீண்டும் முயற்சிக்கவும்",
            "language_mismatch": "பதில் மொழி கோரப்பட்ட மொழியுடன் பொருந்தவில்லை",
            "tool_error": "கருவி செயல்பாடு தோல்வியடைந்தது, தயவுசெய்து உங்கள் கோரிக்கையை மீண்டும் முயற்சிக்கவும்",
            "database_error": "தரவுத்தள செயல்பாடு தோல்வியடைந்தது, தயவுசெய்து மீண்டும் முயற்சிக்கவும்",
            "authentication_error": "அங்கீகாரம் தோல்வியடைந்தது, தயவுசெய்து உங்கள் சான்றுகளை சரிபார்க்கவும்"
        },
        SupportedLanguage.HINDI: {
            "general_error": "कुछ गलत हुआ है, कृपया बाद में फिर से कोशिश करें",
            "missing_info": "आवश्यक जानकारी गुम है",
            "translation_failed": "अनुवाद सेवा अस्थायी रूप से अनुपलब्ध है",
            "no_results": "कोई परिणाम नहीं मिला",
            "invalid_input": "अमान्य इनपुट प्रदान किया गया",
            "pending_rating_search": "प्रदाता खोज जारी रखने से पहले, कृपया पिछली बुकिंग के लिए अपनी रेटिंग पूरी करें",
            "pending_rating_booking": "बुकिंग जारी रखने से पहले, कृपया पिछली बुकिंग के लिए अपनी रेटिंग पूरी करें",
            "max_iterations": "अधिकतम प्रयास पूरे हो गए, अंतिम उत्तर नहीं मिला",
            "validation_error": "प्रतिक्रिया को सत्यापित नहीं किया जा सका, कृपया फिर से कोशिश करें",
            "language_mismatch": "प्रतिक्रिया भाषा अनुरोधित भाषा से मेल नहीं खाती",
            "tool_error": "टूल निष्पादन विफल हो गया, कृपया अपना अनुरोध फिर से प्रयास करें",
            "database_error": "डेटाबेस ऑपरेशन विफल हो गया, कृपया फिर से कोशिश करें",
            "authentication_error": "प्रमाणीकरण विफल हो गया, कृपया अपनी साख जांचें"
        },
        SupportedLanguage.TELUGU: {
            "general_error": "ఏదో తప్పు జరిగింది, దయచేసి తర్వాత మళ్లీ ప్రయత్నించండి",
            "missing_info": "అవసరమైన సమాచారం లేదు",
            "translation_failed": "అనువాద సేవ తాత్కాలికంగా అందుబాటులో లేదు",
            "no_results": "ఫలితాలు ఏవీ కనుగొనబడలేదు",
            "invalid_input": "చెల్లని ఇన్‌పుట్ అందించబడింది",
            "pending_rating_search": "ప్రొవైడర్ శోధనను కొనసాగించే ముందు, దయచేసి మునుపటి బుకింగ్ కోసం మీ రేటింగ్‌ను పూర్తి చేయండి",
            "pending_rating_booking": "బుకింగ్‌ను కొనసాగించే ముందు, దయచేసి మునుపటి బుకింగ్ కోసం మీ రేటింగ్‌ను పూర్తి చేయండి",
            "max_iterations": "గరిష్ట ప్రయత్నాలు పూర్తయ్యాయి, తుది సమాధానం లభించలేదు",
            "validation_error": "ప్రతిస్పందనను ధృవీకరించడం సాధ్యపడలేదు, దయచేసి మళ్లీ ప్రయత్నించండి",
            "language_mismatch": "ప్రతిస్పందన భాష అభ్యర్థించిన భాషతో సరిపోలలేదు",
            "tool_error": "టూల్ అమలు విఫలమైంది, దయచేసి మీ అభ్యర్థనను మళ్లీ ప్రయత్నించండి",
            "database_error": "డేటాబేస్ ఆపరేషన్ విఫలమైంది, దయచేసి మళ్లీ ప్రయత్నించండి",
            "authentication_error": "ప్రమాణీకరణ విఫలమైంది, దయచేసి మీ ఆధారాలను తనిఖీ చేయండి"
        }
    }
    
    @staticmethod
    def get_error_message(error_type: str, language: str) -> str:
        """Return the error message for a type in the given language, falling back to general_error and English."""
        if not TranslationConfig.ERROR_MESSAGE_TRANSLATION_ENABLED:
            return ErrorMessageTranslator.ERROR_MESSAGES[SupportedLanguage.ENGLISH].get(
                error_type, "An error occurred"
            )
        
        target_lang = normalize_language_code(language)
        
        if target_lang in ErrorMessageTranslator.ERROR_MESSAGES:
            messages = ErrorMessageTranslator.ERROR_MESSAGES[target_lang]
            return messages.get(error_type, messages.get("general_error", "An error occurred"))
        
        english_messages = ErrorMessageTranslator.ERROR_MESSAGES[SupportedLanguage.ENGLISH]
        return english_messages.get(error_type, english_messages.get("general_error", "An error occurred"))


class TranslationManager:
    """Main class for managing all translation operations"""
    
    def __init__(self):
        """Create the search, output and error-message translators."""
        self.search_translator = SearchTermTranslator()
        self.output_translator = OutputTranslator()
        self.error_translator = ErrorMessageTranslator()
    
    async def process_internal_invite_translation(
        self, 
        search_term: str, 
        user_language: str,
        response: str
    ) -> Dict[str, Any]:
        """Translate an invite search term to English and the response to the user's language."""
        original_term, translated_term = await self.search_translator.translate_search_term_for_internal_invite(
            search_term, user_language
        )
        
        translated_response = await self.output_translator.translate_response(
            response, user_language
        )
        
        return {
            "original_search_term": original_term,
            "translated_search_term": translated_term,
            "translated_response": translated_response
        }
    
    async def process_booking_translation(
        self, 
        search_term: str, 
        user_language: str,
        response: str
    ) -> Dict[str, Any]:
        """Translate a booking search term to English and the response to the user's language."""
        original_term, translated_term = await self.search_translator.translate_search_term_for_booking(
            search_term, user_language
        )
        
        translated_response = await self.output_translator.translate_response(
            response, user_language
        )
        
        return {
            "original_search_term": original_term,
            "translated_search_term": translated_term,
            "translated_response": translated_response
        }
    
    def get_localized_error(self, error_type: str, language: str) -> str:
        """Return the localized error message for a type and language."""
        return self.error_translator.get_error_message(error_type, language)

translation_manager = TranslationManager()


async def translate_search_term_for_invite(search_term: str, user_language: str) -> Tuple[str, str]:
    """Return (original, English) search terms for an internal invite."""
    return await translation_manager.search_translator.translate_search_term_for_internal_invite(
        search_term, user_language
    )

async def translate_search_term_for_booking(search_term: str, user_language: str) -> Tuple[str, str]:
    """Return (original, English) search terms for a booking."""
    return await translation_manager.search_translator.translate_search_term_for_booking(
        search_term, user_language
    )

async def translate_response_to_user_language(response: str, target_language: str) -> str:
    """Translate a response into the target language via the shared translation manager."""
    return await translation_manager.output_translator.translate_response(
        response, target_language
    )

def get_error_message(error_type: str, language: str) -> str:
    """Return the localized error message for a type and language."""
    return translation_manager.error_translator.get_error_message(error_type, language)


def configure_translation_debug(enabled: bool):
    """Enable or disable translation debug logging."""
    TranslationConfig.DEBUG_TRANSLATION_LOGGING = enabled

def configure_search_translation(enabled: bool):
    """Enable or disable search term translation."""
    TranslationConfig.SEARCH_TERM_TRANSLATION_ENABLED = enabled

def configure_output_translation(enabled: bool):
    """Enable or disable output translation."""
    TranslationConfig.OUTPUT_TRANSLATION_ENABLED = enabled


def detect_language(text: str) -> SupportedLanguage:
    """Return the first language whose Unicode range appears in the text, defaulting to English."""
    for language, (start_char, end_char) in LANGUAGE_UNICODE_RANGES.items():
        if any(start_char <= char <= end_char for char in text):
            return language
    
    return SupportedLanguage.ENGLISH

async def validate_and_correct_language(response: str, target_language: str) -> str:
    """Translate the response into the target language when its detected language differs."""
    if not target_language:
        return response
    
    detected_lang = detect_language(response)
    target_lang = normalize_language_code(target_language)
    
    if detected_lang != target_lang:
        try:
            corrected_response = await translate_response_to_user_language(response, target_language)
            return corrected_response if corrected_response else response
        except Exception:
            logger.warning("translation: could not correct the response language to %s", target_language, exc_info=True)
            if target_lang == SupportedLanguage.ENGLISH:
                return f"{response}\n\n[Note: Response should be in English as requested]"
            else:
                return response
    
    return response