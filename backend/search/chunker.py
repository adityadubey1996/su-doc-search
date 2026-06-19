import hashlib
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str      # stable hash of url + chunk_index
    url: str           # page URL
    section_url: str   # page URL + #anchor — for deep-linking to exact section
    title: str         # page H1 title
    section: str       # heading text of this section
    category: str      # top-level doc category (from URL path)
    breadcrumb: str    # full path breadcrumb
    text: str          # chunk body (heading prepended)
    chunk_index: int


MAX_WORDS = 500
OVERLAP_WORDS = 50


def _make_chunk_id(url: str, index: int) -> str:
    return hashlib.sha256(f"{url}::{index}".encode()).hexdigest()[:16]


def _sub_split(heading: str, body: str) -> list[str]:
    """Split body into <=MAX_WORDS windows with OVERLAP_WORDS overlap.
    Heading is prepended to every window so each chunk is self-contained."""
    words = body.split()
    if len(words) <= MAX_WORDS:
        return [f"{heading}\n{body}".strip() if heading else body]

    windows: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + MAX_WORDS, len(words))
        window = " ".join(words[start:end])
        windows.append(f"{heading}\n{window}".strip() if heading else window)
        if end == len(words):
            break
        start = end - OVERLAP_WORDS
    return windows


def chunk_sections(
    url: str,
    title: str,
    category: str,
    breadcrumb: str,
    sections: list[tuple[str, str, str]],  # (anchor_id, heading_text, body_text)
) -> list[Chunk]:
    """Convert pre-split sections into Chunks.

    Each chunk deep-links via section_url = url#anchor_id.
    Oversized sections are sub-split with overlap; heading is repeated in each sub-chunk.
    """
    chunks: list[Chunk] = []
    global_index = 0

    for anchor_id, heading, body in sections:
        if not body.strip():
            continue
        section_url = f"{url}#{anchor_id}" if anchor_id else url
        for window_text in _sub_split(heading, body):
            chunks.append(Chunk(
                chunk_id=_make_chunk_id(url, global_index),
                url=url,
                section_url=section_url,
                title=title,
                section=heading,
                category=category,
                breadcrumb=breadcrumb,
                text=window_text,
                chunk_index=global_index,
            ))
            global_index += 1

    return chunks
