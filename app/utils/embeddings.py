"""Embedding Helpers - 768-dimension Gemini text embeddings and pgvector formatting.

Shared utility that generates 768-dimension gemini-embedding-001 vectors with retry on transient errors and a LangChain fallback, plus helpers to normalise, validate and render vectors as pgvector literals.

Features:
    - 768-Dimension Enforcement: output_dimensionality pinned to 768 on every Gemini call
    - Transient-Failure Retry: generate_embedding() retries transient Gemini errors with exponential backoff
    - LangChain Fallback: generate_embedding() falls back to the gemini_embeddings wrapper
    - Vector Normalization: normalize_vector() scales to unit length, tolerating a zero-norm vector
    - Dimension Validation: validate_embedding_dimensions() checks the dimension count
    - PGVector Formatting: vector_to_postgres_string() renders a PostgreSQL array literal
    - Call Monitoring: generate_embedding() is wrapped with llm_call_monitor

Classes:
    No classes defined (module uses function-based embedding utilities)

Functions:
    generate_embedding(): 768-dim Gemini embedding with retry, then LangChain fallback
    generate_embedding_langchain(): Embedding via the LangChain wrapper, no retry or fallback
    normalize_vector(): Normalize a vector to unit length
    validate_embedding_dimensions(): Check that an embedding has the expected dimension count
    vector_to_postgres_string(): Convert a vector to a PostgreSQL array-literal string

Dependencies:
    - asyncio: to_thread() for the blocking Gemini call; sleep() for retry backoff
    - numpy: Vector-norm math for normalize_vector()
    - google.genai: Primary embedding provider — Gemini embed_content() with 768-dim output
    - app.logs.enhanced_logging: llm_call_monitor decorator on generate_embedding()
    - app.agent_infrastructure.infrastructure.llm_clients: gemini_embeddings LangChain fallback wrapper
    - app.core.config: Gemini API key configuration
    - app.core.logging: Centralized logging for embedding operations

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description and enhanced feature coverage
    12-Aug-2026: generate_embedding() retries the primary Gemini call on transient failures with backoff.
"""

import asyncio
import numpy as np
from typing import List
import google.genai as genai
from openai import AsyncAzureOpenAI
from app.logs.enhanced_logging import llm_call_monitor
from app.core.config import settings
from app.core.logging import logger
from app.agent_infrastructure.infrastructure.llm_clients import gemini_embeddings

genai_client = genai.Client(api_key=settings.gemini_api_key)

EMBEDDING_DIMENSIONS = 768

EMBEDDING_TRANSIENT_RETRIES = 3
EMBEDDING_RETRY_BASE_DELAY_SECONDS = 1.0
EMBEDDING_TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "504", "DEADLINE_EXCEEDED")


@llm_call_monitor('gemini-embedding', 'embedding_generation')
async def generate_embedding(text: str, model: str = "models/gemini-embedding-001") -> List[float]:
    """Embed text with Gemini at EMBEDDING_DIMENSIONS, retrying transient errors and falling back to LangChain."""
    try:
        def _embed_sync() -> any:
            return genai_client.models.embed_content(
                model=model,
                contents=text,
                config=genai.types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBEDDING_DIMENSIONS
                ),
            )

        for attempt in range(EMBEDDING_TRANSIENT_RETRIES):
            try:
                result = await asyncio.to_thread(_embed_sync)
                break
            except Exception as transient:
                if attempt == EMBEDDING_TRANSIENT_RETRIES - 1 or not any(
                    marker in str(transient) for marker in EMBEDDING_TRANSIENT_MARKERS
                ):
                    raise
                delay = EMBEDDING_RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                logger.warning(
                    "Transient embedding failure (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, EMBEDDING_TRANSIENT_RETRIES, delay, transient,
                )
                await asyncio.sleep(delay)

        embedding = result.embeddings
        if not result.embeddings:
            raise ValueError("No embeddings returned from API")

        embedding = result.embeddings[0].values

        if len(embedding) != EMBEDDING_DIMENSIONS:
            logger.warning(f"Unexpected embedding dimension: {len(embedding)}, expected {EMBEDDING_DIMENSIONS}")

        return embedding

    except Exception as e:
        logger.error(f"Error generating embedding: {str(e)}", exc_info=True)
        
        try:
            logger.info("Falling back to LangChain gemini_embeddings")
            embedding = await gemini_embeddings.aembed_query(text)
            return embedding
        except Exception as fallback_error:
            logger.error(f"Fallback embedding generation failed: {str(fallback_error)}", exc_info=True)
            raise


async def generate_embedding_langchain(text: str) -> List[float]:
    """Embed text with the LangChain Gemini embeddings client."""
    try:
        embedding = await gemini_embeddings.aembed_query(text)
        return embedding
    except Exception as e:
        logger.error(f"Error generating LangChain embedding: {str(e)}", exc_info=True)
        raise


def normalize_vector(vector: List[float]) -> List[float]:
    """Return the vector scaled to unit length, or unchanged when its norm is zero."""
    vec_array = np.array(vector)
    norm = np.linalg.norm(vec_array)
    if norm == 0:
        return vector
    normalized = vec_array / norm
    return normalized.tolist()


def validate_embedding_dimensions(embedding: List[float], expected_dim: int = EMBEDDING_DIMENSIONS) -> bool:
    """Return whether the embedding has the expected dimension count, logging a warning when it does not."""
    actual_dim = len(embedding)
    if actual_dim != expected_dim:
        logger.warning(f"Embedding dimension mismatch: got {actual_dim}, expected {expected_dim}")
        return False
    return True


def vector_to_postgres_string(vector: List[float]) -> str:
    """Format a vector as a pgvector literal such as '[1.0,2.0,3.0]'."""
    return '[' + ','.join(map(str, vector)) + ']'
