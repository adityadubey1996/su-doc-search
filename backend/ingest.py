"""Ingest pipeline CLI: scrape → chunk → embed → index to OpenSearch + Pinecone.

Run:
    poetry run python -m backend.ingest [--limit N] [--force] [--urls-file FILE]

Flags:
    --limit N        Only ingest first N pages (for dev/testing)
    --force          Re-create OpenSearch index before ingesting (clears old data)
    --urls-file FILE Ingest only the URLs listed in FILE (one URL per line).
                     Useful for targeted test runs before full ingest.
"""
import argparse
import asyncio
import logging
import os
import pathlib
import sys

import httpx

from backend.config import get_settings
from backend.search.chunker import Chunk, chunk_sections
from backend.search.embedder import embed_texts
from backend.search.opensearch_index import bulk_index, create_index
from backend.search.pinecone_index import create_index_if_missing, upsert_vectors
from backend.search.scraper import PageDoc, fetch_page, scrape_sitemap

# Increase recursion limit to handle deeply nested HTML
sys.setrecursionlimit(5000)

logger = logging.getLogger(__name__)

settings = get_settings()
SITEMAP_URL = settings.sitemap_url
EMBED_BATCH = 50  # chunks per embedding call (stay well under Gemini's 100 limit)
INDEX_BATCH = 100  # chunks per OpenSearch bulk call (reduced from 200 for Pinecone compatibility)


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


def _chunks_from_pages(pages: list[PageDoc]) -> list[Chunk]:
    """Convert all pages to chunks. Skips pages with no sections."""
    all_chunks: list[Chunk] = []
    for page in pages:
        if not page.sections:
            logger.debug("skip page with no sections: %s", page.url)
            continue
        chunks = chunk_sections(
            url=page.url,
            title=page.title,
            category=page.category,
            breadcrumb=page.breadcrumb,
            sections=page.sections,
        )
        all_chunks.extend(chunks)
    logger.info("chunked %d pages → %d chunks", len(pages), len(all_chunks))
    return all_chunks


def _index_chunks(chunks: list[Chunk]) -> None:
    """Embed chunks and index to both OpenSearch and Pinecone in batches.

    Process EMBED_BATCH chunks at a time to avoid holding all embeddings in memory.
    """
    total_os = 0
    total_pc = 0

    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        texts = [c.text for c in batch]

        # Embed batch — falls back to zero vectors on hard failure (chunk still lands in OS)
        vectors = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        # Build OS documents and Pinecone records for this batch
        os_docs = [
            {
                "chunk_id": c.chunk_id,
                "url": c.url,
                "section_url": c.section_url,
                "title": c.title,
                "section": c.section,
                "category": c.category,
                "breadcrumb": c.breadcrumb,
                "text": c.text,
            }
            for c in batch
        ]
        pc_records = [
            {
                "id": c.chunk_id,
                "values": vec,
                "metadata": {
                    "url": c.url,
                    "section_url": c.section_url,
                    "title": c.title,
                    "section": c.section,
                    "category": c.category,
                    "snippet": " ".join(c.text.split()[:30]),
                },
            }
            for c, vec in zip(batch, vectors)
        ]

        os_count = bulk_index(os_docs)
        pc_count = upsert_vectors(pc_records, batch_size=len(pc_records))
        total_os += os_count
        total_pc += pc_count

        logger.info(
            "progress: %d/%d chunks — os_indexed=%d pc_upserted=%d",
            min(i + EMBED_BATCH, len(chunks)),
            len(chunks),
            total_os,
            total_pc,
        )

    logger.info("ingest complete: %d OS docs, %d Pinecone vectors", total_os, total_pc)


async def _scrape_url_list(urls: list[str]) -> list[PageDoc]:
    """Fetch a specific list of URLs concurrently in batches of 10."""
    docs: list[PageDoc] = []
    async with httpx.AsyncClient(headers={"User-Agent": "su-docs-search/0.1"}) as client:
        for i in range(0, len(urls), 10):
            batch = urls[i : i + 10]
            results = await asyncio.gather(*[fetch_page(client, u) for u in batch])
            docs.extend(r for r in results if r is not None)
            logger.info("scraped %d/%d pages", len(docs), len(urls))
    return docs


async def run_ingest(limit: int | None, force: bool, urls_file: str | None = None) -> None:
    """Full ingest pipeline: scrape → chunk → embed → index."""
    logger.info("=== su-docs ingest START limit=%s force=%s urls_file=%s ===", limit, force, urls_file)

    # Phase 1: ensure indices exist
    logger.info("phase 1: creating indices")
    create_index(force=force)
    create_index_if_missing()

    # Phase 2: scrape
    if urls_file:
        url_list = [u.strip() for u in pathlib.Path(urls_file).read_text().splitlines() if u.strip()]
        if limit:
            url_list = url_list[:limit]
        logger.info("phase 2: scraping %d URLs from %s", len(url_list), urls_file)
        pages = await _scrape_url_list(url_list)
    else:
        logger.info("phase 2: scraping sitemap %s", SITEMAP_URL)
        pages = await scrape_sitemap(SITEMAP_URL, limit=limit)
    logger.info("scraped %d pages", len(pages))

    # Phase 3: chunk
    logger.info("phase 3: chunking")
    chunks = _chunks_from_pages(pages)
    if not chunks:
        logger.warning("no chunks produced — ingest aborted")
        return

    # Phase 4: embed + index
    logger.info("phase 4: embedding and indexing %d chunks", len(chunks))
    _index_chunks(chunks)

    logger.info("=== su-docs ingest DONE ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SearchUnify docs into search engines")
    parser.add_argument("--limit", type=int, default=None, help="Max pages to ingest")
    parser.add_argument("--force", action="store_true", help="Re-create OpenSearch index")
    parser.add_argument("--urls-file", default=None, help="File with URLs to ingest (one per line)")
    args = parser.parse_args()

    setup_logging()
    asyncio.run(run_ingest(limit=args.limit, force=args.force, urls_file=args.urls_file))


if __name__ == "__main__":
    main()
