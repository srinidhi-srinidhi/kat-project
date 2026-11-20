"""Etown Info Tool - answers platform questions from the etowninfo Q&A knowledge base.

Shared agent tool logic that embeds the user's question with Gemini, ranks etowninfo rows by pgvector L2 distance and returns the top two matches as Q&A text for the agent to relay.

Features:
    - Vector Search: Gemini query embedding ranked by `<->` L2 distance on etowninfo.content_vector
    - Q&A Formatting: Top rows are rendered as newline-separated Q:/A: pairs
    - Graceful Degradation: Empty embeddings, zero results and errors return a plain message
    - Logging: Query attempts, result counts, and failures are logged for tracing

Classes:
    No classes defined (module exposes two async helper functions)

Functions:
    handle_provide_etown_info(): Entry point; searches etowninfo for the top 2 matches
    etown_query_search(): Embeds the query, searches the given table, formats rows as Q&A text

Dependencies:
    - app.core.logging: Logger for search tracing and error reporting
    - app.db.client: Database.fetch for the pgvector similarity query
    - app.agent_infrastructure.infrastructure.llm_clients: gemini_embeddings for query embedding

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

from app.core.logging import logger
from app.db.client import Database
from app.agent_infrastructure.infrastructure.llm_clients import gemini_embeddings


async def handle_provide_etown_info(etown_query: str) -> str:
    """Return the two etowninfo Q&A pairs nearest to a question about the Etown platform."""
    return await etown_query_search(etown_query, "etowninfo", 2)


async def etown_query_search(search_query: str, table_name: str, limit: int) -> str:
    """Embed a query with Gemini and return the nearest Q&A pairs by L2 distance from a table.

    Args:
        search_query: Query text to embed and match.
        table_name: Table with prompt, response and content_vector columns.
        limit: Maximum number of rows to return.

    Returns:
        Blank-line-separated Q: / A: pairs, or a no-results or error message.
    """
    try:
        logger.info(f"Performing vector search on {table_name} with query: {search_query}")
        
        embedding_vector = await gemini_embeddings.aembed_query(search_query)
        
        if not embedding_vector:
            logger.warning("No embedding could be generated for the search query")
            return "No embedding could be generated for the search query."

        embedding_string = '[' + ','.join(map(str, embedding_vector)) + ']'

        query = f"""
            SELECT prompt, response
            FROM {table_name}
            WHERE content_vector IS NOT NULL
            ORDER BY content_vector <-> $1::vector
            LIMIT $2;
        """

        rows = await Database.fetch(query, embedding_string, limit)

        if not rows:
            logger.info(f"No results found for query: {search_query}")
            return "No relevant results found."

        text_results = "\n\n".join(
            f"Q: {row.get('prompt', '[No prompt]')}\nA: {row.get('response', '[No response]')}"
            for row in rows
        )

        logger.info(f"Found {len(rows)} relevant results for query")
        
        return text_results if text_results else "No relevant results found."

    except Exception as e:
        logger.error(f"Error during Etown info query: {str(e)}", exc_info=True)
        return "An error occurred during the query."
