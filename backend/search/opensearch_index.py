# backend/search/opensearch_index.py
"""OpenSearch index management and BM25 search for su-docs-search.

Architecture note — how OpenSearch works here:
  Inverted index: analysis pipeline converts text → tokens → stored in an
  inverted index: {token: [(doc_id, positions)]}. Lookups are O(1) per token.

  BM25 score for term t in doc d:
    IDF(t) * TF(t,d) * (k1+1) / (TF(t,d) + k1*(1 - b + b*|d|/avgdl))
  k1=1.2 (TF saturation), b=0.75 (length normalization), both are OS defaults.

  Analysis pipeline (ORDER MATTERS):
    1. standard tokenizer — splits on whitespace/punctuation
    2. lowercase filter
    3. synonym_graph — expands e.g. "SU" → ["SU", "searchunify"] at BOTH
       index and query time (same analyzer used both ways via search_analyzer)
    4. english stemmer — "connectors" → "connector", "configuring" → "configur"
  Synonym BEFORE stemmer: expanded synonyms are also stemmed correctly.
"""
import logging
import pathlib
from typing import Any

from opensearchpy import OpenSearch, helpers

from backend.config import get_settings

logger = logging.getLogger(__name__)

INDEX_NAME = "su-docs"
SYNONYMS_FILE = pathlib.Path(__file__).parent / "synonyms.txt"


def _load_synonyms() -> list[str]:
    """Read synonyms.txt and return list of synonym rules."""
    if not SYNONYMS_FILE.exists():
        logger.warning("synonyms.txt not found at %s", SYNONYMS_FILE)
        return []
    return [line.strip() for line in SYNONYMS_FILE.read_text().splitlines() if line.strip()]


def _get_client() -> OpenSearch:
    """Get OpenSearch client connected to LocalStack endpoint."""
    url = get_settings().opensearch_url
    # LocalStack exposes the OpenSearch REST API at /opensearch/<region>/<domain>
    return OpenSearch(hosts=[url], use_ssl=False, verify_certs=False)


def create_index(force: bool = False) -> None:
    """Create the su-docs index with BM25 + synonym analyzer.

    The 'su_analyzer' pipeline: standard → lowercase → synonym_graph → english.
    Both index-time and search-time use the same analyzer so synonym expansion
    is consistent in both directions.

    Set force=True to delete and recreate (useful during development).
    """
    client = _get_client()

    if client.indices.exists(index=INDEX_NAME):
        if not force:
            logger.info("index '%s' already exists — skipping creation", INDEX_NAME)
            return
        logger.info("force=True — deleting existing index '%s'", INDEX_NAME)
        client.indices.delete(index=INDEX_NAME)

    synonyms = _load_synonyms()
    settings: dict[str, Any] = {
        "analysis": {
            "filter": {
                "su_synonyms": {
                    "type": "synonym_graph",
                    # expand=True means A,B → A,B (both terms indexed)
                    # vs. A=>B (only B indexed)
                    "synonyms": synonyms,
                    "expand": True,
                },
                "english_stemmer": {
                    "type": "stemmer",
                    "language": "english",
                },
            },
            "analyzer": {
                "su_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "su_synonyms", "english_stemmer"],
                }
            },
        }
    }

    # Mapping: keyword fields for exact filtering, text fields analyzed by su_analyzer.
    # Field boosts are applied at query time, not stored in the mapping.
    mappings: dict[str, Any] = {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "url": {"type": "keyword"},
            "section_url": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "su_analyzer"},
            "section": {"type": "text", "analyzer": "su_analyzer"},
            "category": {"type": "keyword"},
            "breadcrumb": {"type": "text", "analyzer": "su_analyzer"},
            "text": {"type": "text", "analyzer": "su_analyzer"},
        }
    }

    client.indices.create(index=INDEX_NAME, body={"settings": settings, "mappings": mappings})
    logger.info("created index '%s' with su_analyzer (synonym_graph + english stemmer)", INDEX_NAME)


def bulk_index(docs: list[dict[str, Any]]) -> int:
    """Bulk upsert documents by chunk_id. Returns count of indexed docs."""
    client = _get_client()
    actions = [
        {
            "_op_type": "index",
            "_index": INDEX_NAME,
            "_id": doc["chunk_id"],
            "_source": doc,
        }
        for doc in docs
    ]
    success, errors = helpers.bulk(client, actions, raise_on_error=False)
    if errors:
        logger.warning("bulk index: %d errors", len(errors))
    logger.info("bulk indexed %d docs to '%s'", success, INDEX_NAME)
    return success


def bm25_query(
    query: str,
    category: str | None = None,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """BM25 multi-match query across title/section/breadcrumb/text with field boosts.

    Field boosts (title^3, section^2) amplify BM25 scores for title matches,
    so a query term found in the title outranks the same term found in body text.

    synonym_graph runs at query time too (same su_analyzer) so 'SU' expands to
    'searchunify' before scoring, hitting docs that only contain the full name.
    """
    query_body: dict[str, Any] = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^3", "section^2", "breadcrumb", "text"],
                            "type": "best_fields",
                            "analyzer": "su_analyzer",
                        }
                    }
                ]
            }
        },
        "size": top_k,
        "_source": ["chunk_id", "url", "section_url", "title", "section", "category", "text"],
    }

    if category:
        query_body["query"]["bool"]["filter"] = [{"term": {"category": category}}]

    client = _get_client()
    resp = client.search(index=INDEX_NAME, body=query_body)
    return resp["hits"]["hits"]
