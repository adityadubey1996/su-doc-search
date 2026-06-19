from dataclasses import dataclass


@dataclass
class SearchResult:
    chunk_id: str
    title: str
    url: str           # page URL
    section_url: str   # page URL + #anchor — what the result card links to
    section: str       # heading text of the matching section
    category: str
    snippet: str
    score: float


def rrf_merge(
    list_a: list[SearchResult],
    list_b: list[SearchResult],
    k: int = 60,
    top_n: int = 20,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion: score = sum(1 / (k + rank)) across both lists.

    A result ranked #1 in both lists: 2/(60+1) ≈ 0.033
    A result ranked #1 in one list only: 1/61 ≈ 0.016
    Deduplication by chunk_id — first occurrence wins for metadata.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, SearchResult] = {}

    for rank, result in enumerate(list_a):
        scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        by_id[result.chunk_id] = result

    for rank, result in enumerate(list_b):
        scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + rank + 1)
        if result.chunk_id not in by_id:
            by_id[result.chunk_id] = result

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [
        SearchResult(**{**vars(by_id[cid]), "score": rrf_score})
        for cid, rrf_score in ranked
    ]
