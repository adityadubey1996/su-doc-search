"""Search quality eval harness — run after a test ingest to check performance.

Usage:
    poetry run python -m backend.eval

What it tests:
  1. Per-engine comparison (BM25 alone, kNN alone, RRF merged)
  2. Synonym expansion (SU == SearchUnify, KCS == knowledge centered service)
  3. Semantic queries (no exact keyword match, rely on vector similarity)
  4. Precision@3 per query (manual relevance — you mark which results look right)
  5. Section deep-link accuracy (is the section_url pointing to a real heading?)
"""
import os
import sys
import logging

logging.basicConfig(level="WARNING")  # quiet the libs; we control our own output

from backend.search.embedder import embed_query
from backend.search.fusion import SearchResult, rrf_merge
from backend.search.opensearch_index import bm25_query
from backend.search.pinecone_index import knn_query


# ── Test queries ─────────────────────────────────────────────────────────────
# Format: (query_string, [keywords that SHOULD appear in top results])
# The keywords are for automated relevance hints — shown alongside results.
TEST_QUERIES = [
    # Exact BM25-friendly queries
    ("Jira connector setup",              ["jira", "connector", "content source"]),
    ("API token authentication",          ["api", "token", "authentication"]),
    ("A/B testing search",                ["a/b", "test", "search"]),
    ("admin user permissions",            ["admin", "user", "role", "permission"]),
    ("content source add",                ["content source", "add", "connector"]),

    # Synonym expansion tests (BM25 should expand via synonym_graph)
    ("SU connector",                      ["searchunify", "connector", "content source"]),
    ("virtual agent chatbot",             ["suva", "virtual agent", "chatbot"]),
    ("KCS knowledge articles",            ["knowledge centered", "kcs", "article"]),

    # Semantic queries (vector search should outperform BM25 here)
    ("how does search ranking work",      ["relevance", "ranking", "bm25", "score"]),
    ("improve search results quality",    ["tuning", "relevance", "boost", "nlp"]),
    ("automate customer support",         ["agent helper", "agentic", "case", "ticket"]),
    ("track search performance metrics",  ["analytics", "report", "query", "performance"]),

    # Section-level accuracy tests
    ("account subscription details",      ["account", "subscription", "license"]),
    ("alert notification setup",          ["alert", "notification", "subscribe"]),
    ("LLM integration configuration",     ["llm", "language model", "integration"]),
]


def _color(text: str, code: str) -> str:
    """ANSI color helper — disabled when not a TTY."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _relevance_hint(result: SearchResult, keywords: list[str]) -> str:
    """Check if any expected keyword appears in title/section/snippet."""
    haystack = (result.title + " " + result.section + " " + result.snippet).lower()
    hits = [kw for kw in keywords if kw.lower() in haystack]
    if hits:
        return _color(f"✓ {hits}", "32")  # green
    return _color("✗ no keyword match", "33")  # yellow


def _show_results(label: str, results: list[SearchResult], keywords: list[str], top: int = 3) -> None:
    print(f"\n  [{label}]")
    if not results:
        print(_color("    (no results)", "31"))
        return
    for i, r in enumerate(results[:top]):
        section_label = r.section.replace("#", "").strip() if r.section else "—"
        hint = _relevance_hint(r, keywords)
        print(f"    #{i+1}  [{r.category}] {r.title}")
        print(f"         § {section_label}")
        print(f"         {r.snippet[:90]}...")
        print(f"         score={r.score:.4f}  {hint}")
        print(f"         → {r.section_url}")


def _os_results(query: str) -> list[SearchResult]:
    try:
        hits = bm25_query(query, top_k=10)
        return [
            SearchResult(
                chunk_id=h["_source"]["chunk_id"],
                title=h["_source"]["title"],
                url=h["_source"]["url"],
                section_url=h["_source"].get("section_url", h["_source"]["url"]),
                section=h["_source"].get("section", ""),
                category=h["_source"].get("category", ""),
                snippet=" ".join(h["_source"].get("text", "").split()[:30]),
                score=h.get("_score", 0.0),
            )
            for h in hits
        ]
    except Exception as e:
        print(_color(f"    OpenSearch error: {e}", "31"))
        return []


def _pc_results(query_vector: list[float]) -> list[SearchResult]:
    try:
        matches = knn_query(query_vector, top_k=10)
        return [
            SearchResult(
                chunk_id=m["id"],
                title=m["metadata"].get("title", ""),
                url=m["metadata"].get("url", ""),
                section_url=m["metadata"].get("section_url", m["metadata"].get("url", "")),
                section=m["metadata"].get("section", ""),
                category=m["metadata"].get("category", ""),
                snippet=m["metadata"].get("snippet", ""),
                score=m.get("score", 0.0),
            )
            for m in matches
        ]
    except Exception as e:
        print(_color(f"    Pinecone error: {e}", "31"))
        return []


def run_eval() -> None:
    print(_color("=" * 70, "1"))
    print(_color("  su-docs-search  —  Search Quality Eval", "1;36"))
    print(_color("=" * 70, "1"))
    print()
    print("Legend:")
    print(f"  {_color('✓ keyword', '32')} = expected keyword found in result")
    print(f"  {_color('✗ no keyword match', '33')} = might be wrong result (check manually)")
    print()

    total_queries = 0
    rrf_keyword_hits = 0
    os_keyword_hits = 0
    pc_keyword_hits = 0

    for query, keywords in TEST_QUERIES:
        total_queries += 1
        print(_color("─" * 70, "90"))
        print(_color(f"Query: {query!r}", "1"))
        print(f"Expected keywords: {keywords}")

        # Embed once — used for both kNN and comparison
        try:
            qvec = embed_query(query)
        except Exception as e:
            print(_color(f"  embed_query failed: {e}", "31"))
            qvec = []

        os_res = _os_results(query)
        pc_res = _pc_results(qvec) if qvec else []
        rrf_res = rrf_merge(os_res, pc_res, k=60, top_n=5)

        _show_results("BM25 (OpenSearch)", os_res, keywords)
        _show_results("kNN  (Pinecone)", pc_res, keywords)
        _show_results("RRF  (merged)", rrf_res, keywords)

        # Count keyword hits in top-1 result for each engine
        def top1_hits(results: list[SearchResult]) -> bool:
            if not results:
                return False
            r = results[0]
            h = (r.title + r.section + r.snippet).lower()
            return any(kw.lower() in h for kw in keywords)

        if top1_hits(os_res):
            os_keyword_hits += 1
        if top1_hits(pc_res):
            pc_keyword_hits += 1
        if top1_hits(rrf_res):
            rrf_keyword_hits += 1

        print()

    # Summary
    print(_color("=" * 70, "1"))
    print(_color("  Summary  (keyword hit @ top-1)", "1;36"))
    print(_color("=" * 70, "1"))
    print(f"  BM25 alone : {os_keyword_hits}/{total_queries} = {os_keyword_hits/total_queries:.0%}")
    print(f"  kNN  alone : {pc_keyword_hits}/{total_queries} = {pc_keyword_hits/total_queries:.0%}")
    print(f"  RRF merged : {rrf_keyword_hits}/{total_queries} = {rrf_keyword_hits/total_queries:.0%}")
    print()
    print("Note: 'keyword hit' is a proxy metric — manually review results above")
    print("for true relevance. RRF should score ≥ max(BM25, kNN).")
    print()


if __name__ == "__main__":
    run_eval()
