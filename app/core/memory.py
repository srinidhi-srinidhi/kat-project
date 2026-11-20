"""Chaatie Memory Config - lazily built mem0 client for long-term conversational memory.

Core configuration that assembles mem0 with a pgvector store in Etown Postgres, a cached embedder and an Azure OpenAI gpt-4.1 fact extractor, and exposes one lazily constructed Memory client that chat.py and memory_utils import.

Features:
    - Lazy Singleton: the mem0 client and its Postgres tables are created on first use
    - PGVector Backend: memories live in the `etown_mem0_memories` collection in Etown Postgres
    - Azure OpenAI Fact Extraction: a custom gpt-4.1 prompt distills turns into a facts list
    - Durable-Fact Taxonomy: only lasting facts are stored; transient ones are rejected
    - Multilingual Normalization: facts are stored in English with the original language noted
    - Retrieval Tuning Constants: the similarity floor and result caps chat.py applies
    - Embedding Cache: an LRU around the client's embedder skips repeat Gemini calls

Classes:
    No classes defined (module exposes a lazy singleton loader and the MEM0_CONFIG dict)

Constants:
    MEMORY_SEARCH_LIMIT: Rows pulled from pgvector before the similarity floor is applied
    MEMORY_INJECT_LIMIT: Maximum memories placed in the prompt after filtering
    MEMORY_SIMILARITY_FLOOR: Minimum cosine similarity for a memory to reach the prompt
    MEMORY_SEARCH_TIMEOUT_SECONDS: Budget for a retrieval before the turn proceeds without memory
    MEMORY_EMBEDDING_CACHE_SIZE: Entries held in the embedder's LRU cache

Functions:
    _install_embedding_cache(): Wrap a client's embedder in a size-bounded LRU, in place
    get_memory_client(): Lazily build (once) and return the shared mem0 Memory instance
    memory_embedding_cache_info(): Hit/miss counters for the embedding cache

Dependencies:
    - mem0: Memory class and Memory.from_config() used to build the client
    - app.core.config: DB credentials and Azure OpenAI endpoint/key/version settings
    - app.core.embedding_config: EMBEDDING_DIMENSIONS and MEM0_EMBEDDING_CONFIG
    - urllib.parse: percent-encodes the DB password for the connection string

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    07-Sep-2026: Rewrote extraction prompt; added retrieval tuning constants and similarity floor; cached the embedder.
    24-Sep-2026: mem0 LLM and embedding calls traced to LangSmith under the calling turn.
    08-Oct-2026: Removed LangSmith tracing.
"""

import os
from functools import lru_cache

from mem0 import Memory
from app.core.config import settings
from app.core.embedding_config import EMBEDDING_DIMENSIONS, MEM0_EMBEDDING_CONFIG
import urllib.parse

encoded_password = urllib.parse.quote_plus(settings.DBPASSWORD)

CUSTOM_FACT_EXTRACTION_PROMPT = """You extract DURABLE facts about a user of Etown, a local
services marketplace. A durable fact is one that will still be true, and still useful, weeks from
now. Return JSON only: {"facts": ["fact1", "fact2"]}. Return {"facts": []} when nothing qualifies —
that is the correct answer for most turns.

STORE (durable):
- Identity and household: where they live, who they live with, children, pets, languages spoken.
- Stable addresses and what each one is: home, office, a parent's house. Name the label.
- Possessions that generate service needs: car, two-wheeler, AC, fridge, washing machine, home type.
- Standing preferences and constraints: dietary rules, allergies, accessibility needs, budget
  sensitivity, preferred time of day, "always call before arriving".
- Provider affinity: a business they say they HAVE USED, liked, disliked, or want again, with
  what they used it for. Format: "Uses <BUSINESS NAME> for <service>". Only when the user wrote
  the business name themselves AND is talking about having used it -- not when they are merely
  naming it while searching, asking about it, or correcting a search term.
- Recurring needs: a service they need on a cadence ("AC serviced every summer").
- Anything the user explicitly asks to be remembered.

NEVER STORE (transient — this is the most important rule):
- Today's date, the current time, or any statement of the form "today is X" / "the date is X".
- A specific booking's date, time, pickup or drop for one job. The provider is durable; the job
  is not. "Uses <business> for cab service" — yes. "Booked a cab Sunday 9 AM to the airport" — no.
- A one-off search or question: what they are looking for right now, a weather question, a price
  check, "show me my requests".
- Anything the assistant said. Errors, apologies, confirmations, prompts for more detail and
  provider lists are the assistant's words, not the user's facts. If a line reads like the
  assistant talking, ignore it entirely.
- Anything already on their profile that the assistant merely read back to them.
- Temporary states: what they are craving, what they want right now, an in-progress booking.
- A business name the user is only searching for, correcting, or asking about, and any name whose
  spelling looks like a typo or a voice-input mangling. A wrong business name is worse than none;
  the platform resolves imprecise names from the user's booking history, not from memory.

RULES:
- Write each fact as a short third-person statement about the user. No dates inside it.
- State what is true of them, never that they asked to be remembered. "Is vegetarian" -- yes.
  "Wants to be remembered as vegetarian" -- no: the wrapper buries the fact and stops it being
  findable when it matters.
- One fact per idea. Never merge two.
- Prefer the specific over the vague: name the locality the user named, not "a town".
- ALWAYS write the fact in English. If the user wrote in another language, append that language
  name in brackets: "English translation (OriginalLanguage)".

Examples (the business names below are placeholders -- never reuse them in an answer):
Input: user: Hi
Output: {"facts": []}

Input: user: nice weather today
Output: {"facts": []}

Input: user: Looking for a restaurant nearby
Output: {"facts": []}

Input: user: is Example Motors open today?
Output: {"facts": []}

Input: user: I meant Exampl Moters
Output: {"facts": []}

Input: assistant: It seems the service date has already passed. Could you confirm a future time?
Output: {"facts": []}

Input: user: book Example Movers for Sunday at 9 AM from my flat to the new house
Output: {"facts": ["Uses Example Movers for packers and movers service"]}

Input: user: I always go to Example Clinic for my check-ups
Output: {"facts": ["Uses Example Clinic for medical check-ups"]}

Input: user: pick it up from my home address, 12 Second Street, Anytown
Output: {"facts": ["Home address is 12 Second Street, Anytown"]}

Input: user: I am vegetarian, please only suggest veg restaurants
Output: {"facts": ["Is vegetarian", "Wants only vegetarian food suggestions"]}

Input: user: remember I am vegetarian and I never eat onion
Output: {"facts": ["Is vegetarian", "Never eats onion"]}

Input: user: my AC needs servicing every summer, it is a 1.5 ton split
Output: {"facts": ["Has a 1.5 ton split AC", "Needs AC servicing every summer"]}

Input: user: நான் சென்னையில் வசிக்கிறேன்
Output: {"facts": ["Lives in Chennai (Tamil)"]}
"""

MEMORY_SEARCH_LIMIT = 12
MEMORY_INJECT_LIMIT = 4
MEMORY_SIMILARITY_FLOOR = 0.55
MEMORY_SEARCH_TIMEOUT_SECONDS = 4.0

MEM0_CONFIG = {
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "connection_string": (
                f"postgresql://{settings.DBUSERNAME}:{encoded_password}"
                f"@{settings.DBHOST}:{int(settings.DBPORT or 5432)}/"
                f"{settings.DATABASENAME}"
                "?sslmode=require"
            ),
            "collection_name": "etown_mem0_memories",
            "embedding_model_dims": EMBEDDING_DIMENSIONS,
        }
    },
    "embedder": MEM0_EMBEDDING_CONFIG,
    "llm":{
        "provider":"azure_openai",
        "config":{
            "model":"gpt-4o",
            "temperature":0.1,
            "max_tokens":1500,
            "azure_kwargs":{
                "azure_endpoint": settings.azure_openai_41_endpoint,
                "api_key":settings.azure_openai_41_api_key,
                "api_version":settings.azure_openai_41_api_version,
                "azure_deployment":"gpt-4.1"
            }
        }
    },
    "custom_fact_extraction_prompt": CUSTOM_FACT_EXTRACTION_PROMPT,
    "version": "v1.1"
}

MEMORY_EMBEDDING_CACHE_SIZE = 512

_memory_client_instance = None
_memory_embed_cache = None


def _install_embedding_cache(client):
    """Replace the mem0 client's embed method with a size-bounded LRU-cached version, in place.

    Args:
        client: A constructed mem0 Memory instance.

    Returns:
        The cache_info callable of the LRU cache.
    """
    original_embed = client.embedding_model.embed

    @lru_cache(maxsize=MEMORY_EMBEDDING_CACHE_SIZE)
    def _cached(text: str, memory_action):
        return tuple(original_embed(text, memory_action))

    def embed(text, memory_action=None):
        return list(_cached(text, memory_action))

    client.embedding_model.embed = embed
    return _cached.cache_info


def get_memory_client():
    """Build the mem0 Memory client on first call, with cached embeddings."""
    global _memory_client_instance, _memory_embed_cache
    if _memory_client_instance is None:
        _memory_client_instance = Memory.from_config(MEM0_CONFIG)
        _memory_embed_cache = _install_embedding_cache(_memory_client_instance)
    return _memory_client_instance


def memory_embedding_cache_info():
    """Return the embedding cache's hit/miss counters, or None before the client is built."""
    return _memory_embed_cache() if _memory_embed_cache else None