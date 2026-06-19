"""Pinecone index management and kNN search for su-docs-search.

Architecture note — how Pinecone HNSW works here:
  Each chunk is stored as a vector record: {id, values, metadata}.
  id = chunk_id (same key used in OpenSearch for dedup/join).
  values = 768-dim Gemini embedding (RETRIEVAL_DOCUMENT task type at ingest).

  Cosine similarity: measures angle between vectors, range [-1,1] but for
  unit-norm embeddings effectively [0,1]. 1 = identical direction (semantically
  close), 0 = orthogonal (unrelated). Independent of vector magnitude.

  HNSW graph: approximate nearest-neighbour — does NOT scan all vectors.
  Trades tiny recall loss for O(log N) query time instead of O(N).

  At query time: embed(query, RETRIEVAL_QUERY) → 768-dim vector →
  Pinecone returns top-k by cosine sim → metadata carries url, section_url,
  title, category, snippet for building SearchResult.
"""
import logging
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from backend.config import get_settings

logger = logging.getLogger(__name__)

DIMENSION = 768
METRIC = "cosine"


def _get_client() -> Pinecone:
    """Return a Pinecone client pointing at pinecone-local (dev) or cloud (prod)."""
    settings = get_settings()
    return Pinecone(host=settings.pinecone_host, api_key="local-key")


def _get_index():
    """Return the Pinecone Index object using the data-plane host from the index description.

    pinecone-local exposes control plane on port 5081 but data plane on port 5082.
    Describing the index gives us the actual host so we don't hard-code ports.
    """
    client = _get_client()
    index_name = get_settings().pinecone_index
    desc = client.describe_index(index_name)
    host = desc.host if str(desc.host).startswith("http") else f"http://{desc.host}"
    return client.Index(index_name, host=host)


def create_index_if_missing() -> None:
    """Create the Pinecone index if it doesn't already exist.

    Uses serverless spec for local emulator compatibility.
    768-dim cosine metric matches Gemini text-embedding-004 output.
    """
    client = _get_client()
    index_name = get_settings().pinecone_index
    existing = [idx.name for idx in client.list_indexes()]

    if index_name in existing:
        logger.info("pinecone index '%s' already exists — skipping creation", index_name)
        return

    client.create_index(
        name=index_name,
        dimension=DIMENSION,
        metric=METRIC,
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    logger.info("created pinecone index '%s' dim=%d metric=%s", index_name, DIMENSION, METRIC)


def upsert_vectors(
    records: list[dict[str, Any]],
    batch_size: int = 100,
) -> int:
    """Upsert vector records into Pinecone. Each record: {id, values, metadata}.

    Pinecone upsert is idempotent — re-uploading a chunk_id overwrites the old vector.
    Returns total count upserted.
    """
    index = _get_index()
    total = 0

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        index.upsert(vectors=batch)
        total += len(batch)
        logger.debug("upserted vectors %d-%d", i, i + len(batch))

    logger.info("upserted %d vectors to pinecone index '%s'", total, get_settings().pinecone_index)
    return total


def knn_query(
    vector: list[float],
    top_k: int = 20,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """kNN query: returns top_k closest vectors by cosine similarity.

    Returns list of dicts with: id, score, and metadata fields
    (url, section_url, title, section, category, snippet).

    Cosine scores from Pinecone are in [0,1] — NOT the same scale as BM25 scores.
    RRF fusion handles this cross-scale mismatch by using rank, not raw score.
    """
    index = _get_index()
    filter_expr = {"category": {"$eq": category}} if category else None

    resp = index.query(
        vector=vector,
        top_k=top_k,
        include_metadata=True,
        filter=filter_expr,
    )
    return resp.get("matches", [])
