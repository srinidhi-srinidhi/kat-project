"""LLM Clients Config - the single place every chat, embedding and translation client is created.

Agent infrastructure module that instantiates the Azure OpenAI gpt-4.1 and gpt-4o-mini clients, the Gemini flash chat clients, the 768-dimension gemini-embedding-001 embedder and an Azure Translator instance once at import, from credentials in app.core.config.

Features:
    - Multi-provider Clients: Azure OpenAI GPT-4.1/GPT-4o-mini and Gemini embeddings plus three chat clients
    - Deterministic vs. Creative Variants: gpt41_precise (temperature 0) and gpt4o_mini_creative (0.9)
    - Shared Gemini Model ID: all three Gemini chat clients use GEMINI_FLASH_MODEL
    - Vision-sized Client: gemini_2_5_flash_vision has a larger max_tokens and timeout budget
    - 768-dim Embeddings: gemini_embeddings uses gemini-embedding-001 at output_dimensionality 768
    - Translation Client: A ready-to-use AzureTranslatorUtils instance for multilingual support.
    - Singleton Instances: Every client is created once at import and imported by name

Classes:
    No classes defined (module provides configured client instances)

Functions:
    No functions defined (module exposes configured client instances only)

Dependencies:
    - langchain_openai: LangChain Azure OpenAI integration for chat completions
    - langchain_google_genai: LangChain Google Generative AI integration for embeddings and chat
    - pydantic: Data validation and secure credential handling
    - app.core.config: Application configuration and API credentials
    - app.utils.azure_translator_package: Azure Translator utility integration

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    12-Mar-2026: Added GPT-4o-mini client for fast web search operations
    27-Mar-2026: Increased Gemini 2.5 Flash timeout for improved live-search reliability
    12-Aug-2026: Both Gemini chat clients use GEMINI_FLASH_MODEL; the pinned 2.5-flash id 404s.
"""
from langchain_openai import AzureChatOpenAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from pydantic import SecretStr
from app.core.config import settings
from app.utils.azure_translator_package import AzureTranslatorUtils


gpt41 = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_41_endpoint,
    api_key=settings.azure_openai_41_api_key,
    api_version=settings.azure_openai_41_api_version,
    deployment_name='gpt-4-1',
    temperature=0.7,
    max_tokens=1000,
)

gpt41_precise = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_41_endpoint,
    api_key=settings.azure_openai_41_api_key,
    api_version=settings.azure_openai_41_api_version,
    deployment_name='gpt-4-1',
    temperature=0,
    max_tokens=1000,
)

gpt4o_mini = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_4o_mini_endpoint,
    api_key=settings.azure_openai_4o_mini_api_key,
    api_version=settings.azure_openai_4o_mini_api_version,
    deployment_name='gpt-4o-mini',
    temperature=0.1,
    max_tokens=1500,
    request_timeout=30,
)

gpt4o_mini_creative = AzureChatOpenAI(
    azure_endpoint=settings.azure_openai_4o_mini_endpoint,
    api_key=settings.azure_openai_4o_mini_api_key,
    api_version=settings.azure_openai_4o_mini_api_version,
    deployment_name='gpt-4o-mini',
    temperature=0.9,
    max_tokens=2000,
    request_timeout=45,
)

gemini_embeddings = GoogleGenerativeAIEmbeddings(
    model="models/gemini-embedding-001",
    google_api_key=SecretStr(settings.gemini_api_key) if settings.gemini_api_key else None,
    output_dimensionality=768,
)

GEMINI_FLASH_MODEL = "gemini-flash-latest"

gemini_2_5_flash = ChatGoogleGenerativeAI(
    model=GEMINI_FLASH_MODEL,
    google_api_key=settings.gemini_api_key,
    temperature=0.1,
    max_tokens=1024,
    request_timeout=45,
)

gemini_2_5_flash_vision = ChatGoogleGenerativeAI(
    model=GEMINI_FLASH_MODEL,
    google_api_key=settings.gemini_api_key,
    temperature=0.1,
    max_tokens=8192,
    request_timeout=60,
)

gemini_flash_latest = ChatGoogleGenerativeAI(
    model=GEMINI_FLASH_MODEL,
    google_api_key=settings.gemini_api_key,
    temperature=0.1,
    max_tokens=2000,
    request_timeout=45,
)

translator = AzureTranslatorUtils(
            subscription_key=settings.azure_translator_key,
            endpoint=settings.azure_translator_endpoint
        )