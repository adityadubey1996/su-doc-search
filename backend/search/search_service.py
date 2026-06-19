"""Hybrid search orchestration for su-docs-search.

Coordinates BM25 (OpenSearch) + kNN (Pinecone) search in parallel.
Results are fused via Reciprocal Rank Fusion (RRF) to produce a single ranked list.

Both engines degrade independently — if one fails, results from the other are used alone.
If embedding fails, semantic search is disabled but BM25 still runs.
"""
import logging

from backend.search.embedder import embed_query
from backend.search.fusion import SearchResult, rrf_merge
from backend.search.opensearch_index import bm25_query
from backend.search.pinecone_index import knn_query

logger = logging.getLogger(__name__)

_SNIPPET_WORDS = 30


def _snippet(text: str) -> str:
    """Return first N words of text as a readable snippet."""
    words = text.split()
    return " ".join(words[:_SNIPPET_WORDS]) + ("..." if len(words) > _SNIPPET_WORDS else "")


def _os_hit_to_result(hit: dict) -> SearchResult:
    """Convert an OpenSearch hit dict to SearchResult.

    OpenSearch hit structure:
      {
        '_score': float,
        '_source': {
          'chunk_id': str,
          'title': str,
          'url': str,
          'section_url': str,  # optional, defaults to url
          'section': str,      # optional, heading text
          'category': str,     # optional
          'text': str,
        }
      }
    """
    src = hit["_source"]
    return SearchResult(
        chunk_id=src["chunk_id"],
        title=src["title"],
        url=src["url"],
        section_url=src.get("section_url", src["url"]),
        section=src.get("section", ""),
        category=src.get("category", ""),
        snippet=_snippet(src.get("text", "")),
        score=hit.get("_score", 0.0),
    )


def _pc_match_to_result(match: dict) -> SearchResult:
    """Convert a Pinecone match dict to SearchResult.

    Pinecone match structure:
      {
        'id': str (chunk_id),
        'score': float (cosine similarity in [0,1]),
        'metadata': {
          'url': str,
          'section_url': str,
          'title': str,
          'section': str,
          'category': str,
          'snippet': str,
        }
      }
    """
    meta = match.get("metadata", {})
    return SearchResult(
        chunk_id=match["id"],
        title=meta.get("title", ""),
        url=meta.get("url", ""),
        section_url=meta.get("section_url", meta.get("url", "")),
        section=meta.get("section", ""),
        category=meta.get("category", ""),
        snippet=meta.get("snippet", ""),
        score=match.get("score", 0.0),
    )


def _os_search(query: str, category: str | None, top_k: int) -> list[SearchResult]:
    """BM25 search via OpenSearch. Returns [] on any failure."""
    try:
        hits = bm25_query(query, category=category, top_k=top_k)
        return [_os_hit_to_result(h) for h in hits]
    except Exception as exc:
        logger.warning("opensearch degraded: %s", exc)
        return []


def _pc_search(query_vector: list[float], category: str | None, top_k: int) -> list[SearchResult]:
    """kNN search via Pinecone. Returns [] on any failure."""
    try:
        matches = knn_query(query_vector, top_k=top_k, category=category)
        return [_pc_match_to_result(m) for m in matches]
    except Exception as exc:
        logger.warning("pinecone degraded: %s", exc)
        return []


def search(
    query: str,
    category: str | None = None,
    top_n: int = 20,
) -> list[SearchResult]:
    """Hybrid search: BM25 + kNN → RRF merge.

    Fan-out to OpenSearch (BM25) and Pinecone (kNN) in parallel.
    Both engines degrade independently — if one fails, the other's results are used alone.
    Results are deduplicated by chunk_id and fused via Reciprocal Rank Fusion (k=60).

    Args:
        query: Search query string.
        category: Optional category filter (applied to both engines).
        top_n: Number of final results to return (default 20).

    Returns:
        List of top_n SearchResult objects ranked by RRF score.
    """
    logger.info("search query=%r category=%s top_n=%d", query, category, top_n)

    # Embed query for semantic search (also used as kNN input)
    try:
        query_vector = embed_query(query)
    except Exception as exc:
        logger.warning("embed_query failed — semantic search disabled: %s", exc)
        query_vector = []

    fetch_k = top_n * 2  # fetch more from each engine to give RRF room to rerank

    os_results = _os_search(query, category, fetch_k)
    pc_results = _pc_search(query_vector, category, fetch_k) if query_vector else []

    logger.info("os_results=%d pc_results=%d", len(os_results), len(pc_results))

    merged = rrf_merge(os_results, pc_results, k=60, top_n=top_n)
    logger.info("merged=%d results", len(merged))
    return merged
