# backend/search/embedder.py
import logging
import time
from typing import Literal

from google import genai
from google.genai import types as genai_types

from backend.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "gemini-embedding-001"
OUTPUT_DIM = 768   # MRL truncation — gemini-embedding-001 defaults to 3072; we keep 768 to match existing index
BATCH_SIZE = 100
# Gemini free tier: 1500 requests/min → sleep briefly between batches
BATCH_SLEEP_S = 0.5
MAX_RETRIES = 3
RETRY_BASE_S = 2.0

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Return a Gemini client (singleton, created on first call)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_settings().gemini_api_key)
    return _client


def embed_texts(
    texts: list[str],
    task_type: TaskType = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Embed a list of texts in batches.

    Uses RETRIEVAL_DOCUMENT for indexing, RETRIEVAL_QUERY for query-time.
    Returns a list of 768-dim float vectors in the same order as input.
    On batch failure: logs warning and fills failed positions with zero vectors.
    """
    vectors: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        vector_batch = _embed_batch_with_retry(batch, task_type)
        vectors.extend(vector_batch)
        if i + BATCH_SIZE < len(texts):
            time.sleep(BATCH_SLEEP_S)

    return vectors


def embed_query(query: str) -> list[float]:
    """Embed a single search query (RETRIEVAL_QUERY task type)."""
    results = embed_texts([query], task_type="RETRIEVAL_QUERY")
    return results[0]


def _embed_batch_with_retry(
    texts: list[str],
    task_type: TaskType,
) -> list[list[float]]:
    """Embed one batch, retrying up to MAX_RETRIES times on transient errors."""
    zero_vector: list[float] = [0.0] * 768
    client = _get_client()

    for attempt in range(MAX_RETRIES):
        try:
            result = client.models.embed_content(
                model=MODEL,
                contents=texts,
                config=genai_types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=OUTPUT_DIM,
                ),
            )
            return [list(e.values) for e in result.embeddings]
        except Exception as exc:
            wait = RETRY_BASE_S * (2 ** attempt)
            logger.warning(
                "embed batch failed attempt=%d/%d err=%s wait=%.1fs",
                attempt + 1, MAX_RETRIES, exc, wait,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    logger.error("embed batch hard failure after %d attempts — returning zero vectors", MAX_RETRIES)
    return [zero_vector] * len(texts)
