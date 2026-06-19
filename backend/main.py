import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.search.search_service import SearchResult, search

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("su-docs-search backend started")
    yield


app = FastAPI(title="su-docs-search", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    query: str


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/search", response_model=SearchResponse)
async def search_endpoint(
    q: str = Query(..., min_length=1, description="Search query"),
    category: str | None = Query(None, description="Optional category filter"),
    top_n: int = Query(20, ge=1, le=100, description="Max results to return"),
) -> SearchResponse:
    """Hybrid search: BM25 + semantic kNN + RRF fusion."""
    logger.info("GET /api/search q=%r category=%s top_n=%d", q, category, top_n)
    results = search(query=q, category=category, top_n=top_n)
    return SearchResponse(results=results, total=len(results), query=q)
