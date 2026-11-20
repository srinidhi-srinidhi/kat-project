"""Embedding Config - the platform-wide 768-dimension embedding and pgvector settings.

Configuration module that fixes embedding size at 768 dimensions and holds the gemini-embedding-001 model and task type, the pgvector column and HNSW cosine index definitions, the mem0 embedding config, and default search and batch limits, with small helpers that build them.

Features:
    - Dimension Standardization: Enforced 768-dimension embeddings across all services
    - Model Configuration: Centralized Google Gemini embedding model settings
    - Task Type Management: Standardized embedding task types for different use cases
    - Database Integration: PostgreSQL PGVector compatibility configuration
    - Vector Column Types: Consistent database vector column definitions
    - Index Configuration: Optimized HNSW index settings for vector search
    - Mem0 Integration: Standardized configuration for vector memory systems
    - Search Parameters: Similarity thresholds and search optimization settings
    - Performance Optimization: Optimized embedding parameters for search quality
    - API Integration: Standardized configuration for Google Generative AI embedding APIs

Classes:
    No classes defined (module provides configuration constants and helper functions)

Functions:
    get_gemini_embedding_kwargs(): Return a copy of GEMINI_EMBEDDING_CONFIG for embedding-generation calls
    get_vector_column_definition(): Return the PostgreSQL vector column type string, e.g. "vector(768)"
    get_index_creation_sql(): Build the CREATE INDEX statement for an HNSW cosine-similarity vector index
    validate_dimensions(): Check whether a given embedding size matches EMBEDDING_DIMENSIONS

Constants:
    EMBEDDING_DIMENSIONS: Standard 768-dimension vector size for platform consistency
    EMBEDDING_MODEL: Google Gemini embedding model identifier
    EMBEDDING_TASK_TYPE: Task type for retrieval and document embedding
    GEMINI_EMBEDDING_CONFIG: Complete configuration dictionary for Gemini embeddings
    VECTOR_COLUMN_TYPE: PostgreSQL vector column type definition for database schema
    VECTOR_INDEX_TYPE: HNSW index type for optimal vector search performance
    VECTOR_INDEX_OPS: Cosine similarity operations for vector comparisons
    MEM0_EMBEDDING_CONFIG: Memory system embedding configuration
    MIN_SIMILARITY_THRESHOLD: Minimum similarity threshold for search quality
    DEFAULT_SEARCH_LIMIT: Default result limit for vector search operations
    DEFAULT_BATCH_SIZE: Default number of items embedded per batch
    MAX_BATCH_SIZE: Upper bound on items embedded per batch

Dependencies:
    - typing: Type hints for configuration data structures
    - app.core.config: Application settings and API key management

Updates:
    12-Mar-2026: Added comprehensive module docstring and configuration documentation

Notes:
    All embedding operations must use the 768-dimension standard defined here.
    Changes to embedding dimensions require database migration and reindexing.
    Gemini model configuration is optimized for multilingual semantic search.
"""

from typing import Dict, Any
from app.core.config import settings

EMBEDDING_DIMENSIONS = 768
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_TASK_TYPE = "retrieval_document"

GEMINI_EMBEDDING_CONFIG = {
    "model": EMBEDDING_MODEL,
    "task_type": EMBEDDING_TASK_TYPE,
    "output_dimensionality": EMBEDDING_DIMENSIONS
}

VECTOR_COLUMN_TYPE = f"vector({EMBEDDING_DIMENSIONS})"
VECTOR_INDEX_TYPE = "hnsw"
VECTOR_INDEX_OPS = "vector_cosine_ops"

MEM0_EMBEDDING_CONFIG = {
    "provider": "gemini",
    "config": {
        "model": EMBEDDING_MODEL,
        "api_key": settings.gemini_api_key,
        "output_dimensionality": EMBEDDING_DIMENSIONS
    }
}

MIN_SIMILARITY_THRESHOLD = 0.15
DEFAULT_SEARCH_LIMIT = 50

DEFAULT_BATCH_SIZE = 50
MAX_BATCH_SIZE = 100

def get_gemini_embedding_kwargs() -> Dict[str, Any]:
    """Return a copy of the Gemini embedding kwargs (model, task type, output dimensionality)."""
    return GEMINI_EMBEDDING_CONFIG.copy()


def get_vector_column_definition() -> str:
    """Return the PostgreSQL vector column type, e.g. vector(768)."""
    return VECTOR_COLUMN_TYPE


def get_index_creation_sql(table_name: str, column_name: str, index_name: str) -> str:
    """Return CREATE INDEX SQL for a vector index on the given table and column."""
    return f"""
    CREATE INDEX {index_name} ON {table_name} 
    USING {VECTOR_INDEX_TYPE} ({column_name} {VECTOR_INDEX_OPS})
    WITH (m = 16, ef_construction = 64);
    """

def validate_dimensions(embedding_size: int) -> bool:
    """Return True when an embedding size equals the configured EMBEDDING_DIMENSIONS."""
    return embedding_size == EMBEDDING_DIMENSIONS