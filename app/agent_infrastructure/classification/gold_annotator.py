"""Classifier Gold Annotator - offline LLM labelling of user messages for classifier evaluation.

Offline helper, not on the request path, that scripts use to build the gold evaluation set and clean mined labels: an LLM (gpt-4.1 strong or gpt-4o-mini bulk) judges each message's single best canonical intent against a fixed rubric, in parallel batches.

Classes:
    No classes defined (module exposes the batch-annotation helper and annotate_queries())

Functions:
    _annotate_batch(): Label one batch of queries via a single LLM call.
    annotate_queries(): One canonical intent per query via parallel, batched LLM calls.

Dependencies:
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 (strong) / gpt4o_mini (bulk)
    - app.agent_infrastructure.classification.taxonomy: canonical intent mapping
    - app.core.logging: Application logger for per-batch failure warnings.

Updates:
    22-Jul-2026: Initial LLM-annotator (Phase A: gold set + no_tool cleaning).
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import List, Optional

from app.agent_infrastructure.infrastructure.llm_clients import gpt41, gpt4o_mini
from app.agent_infrastructure.classification import taxonomy
from app.core.logging import logger

_ALLOWED = {
    "search_provider", "recommend_products", "provide_etown_info",
    "booknow_provider", "request_provider", "greeting", "other",
}

_RUBRIC = (
    "You label a user's chat message for a local-services marketplace (\"Etown\") with its "
    "SINGLE best intent. Judge ONLY from the message's meaning — NOT from any tool or reply.\n\n"
    "Intents:\n"
    "- search_provider: wants to DISCOVER a business/shop/service/provider "
    "(\"find a bakery near me\", \"plumbers\", \"where can I buy an AC\", \"AC repair shop\").\n"
    "- recommend_products: wants PRODUCT suggestions/ideas to buy "
    "(\"suggest products for my kitchen\", \"recommend related products\", \"gift ideas\", "
    "\"what should I buy for X\").\n"
    "- provide_etown_info: a question ABOUT the Etown platform itself (features, how it works, "
    "policies, becoming a provider).\n"
    "- booknow_provider: wants to book/order from a specific, already-identified provider.\n"
    "- request_provider: placing or confirming a service request/order.\n"
    "- greeting: greeting, thanks, or smalltalk with no task.\n"
    "- other: anything else, unclear, or none of the above.\n\n"
    "Return STRICT JSON only: a list of objects {\"i\": <index>, \"intent\": <one intent>} for "
    "EVERY numbered message. No prose, no markdown."
)


async def _annotate_batch(client, batch: List[str]) -> List[Optional[str]]:
    """Label one batch of messages with a single LLM call, mapping unknown intents to other and failures to None."""
    payload = "\n".join(f"{i}. {q}" for i, q in enumerate(batch))
    prompt = f"{_RUBRIC}\n\nMessages:\n{payload}"
    out: List[Optional[str]] = [None] * len(batch)
    try:
        resp = await client.ainvoke(prompt)
        content = (resp.content or "").strip()
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if not match:
            return out
        for item in json.loads(match.group()):
            idx = item.get("i")
            intent = str(item.get("intent", "")).strip().lower()
            if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                continue
            if intent not in _ALLOWED:
                intent = "other"
            out[idx] = taxonomy.NO_TOOL if intent == "other" else intent
    except Exception as e:
        logger.warning(f"[gold_annotator] batch failed: {e}")
    return out


async def annotate_queries(
    queries: List[str],
    model: str = "strong",
    batch_size: int = 15,
    concurrency: int = 5,
) -> List[Optional[str]]:
    """Annotate each query with its single best canonical intent using batched concurrent LLM calls.

    Args:
        queries: User messages to label.
        model: strong selects gpt-4.1; anything else selects gpt-4o-mini.
        batch_size: Messages per LLM call.
        concurrency: Maximum concurrent LLM calls.

    Returns:
        Canonical intent per query, None where the batch failed.
    """
    client = gpt41 if model == "strong" else gpt4o_mini
    batches = [queries[i:i + batch_size] for i in range(0, len(queries), batch_size)]
    sem = asyncio.Semaphore(concurrency)

    async def _run(batch):
        async with sem:
            return await _annotate_batch(client, batch)

    results = await asyncio.gather(*[_run(b) for b in batches])
    labels: List[Optional[str]] = []
    for r in results:
        labels.extend(r)
    return labels
