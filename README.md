# su-docs-search

Hybrid search engine over SearchUnify's public documentation (~450 pages).
Combines BM25 keyword search (OpenSearch) with dense vector semantic search (Pinecone + Gemini embeddings), fused with Reciprocal Rank Fusion.

Built as an interview project to demonstrate OpenSearch, vector search, and hybrid retrieval from scratch.

---

## How it works

```
User query
  ├── OpenSearch BM25 (synonym expansion + stemming) → ranked list A
  └── Gemini embed(query) → Pinecone kNN → ranked list B
  └── RRF merge A + B → top 20 results → React UI
```

### BM25 in OpenSearch

OpenSearch maintains an **inverted index**: `token → [(doc_id, positions)]` so lookup is O(1) per token.

**BM25 score** for term `t` in document `d`:
```
IDF(t) × TF(t,d) × (k1+1) / (TF(t,d) + k1 × (1 - b + b × |d|/avgdl))
```
- IDF: penalises common terms (occurs in many docs → low IDF)
- TF: frequency of `t` in `d`, saturated by `k1=1.2`
- Length normalization via `b=0.75` and `avgdl` (average doc length)

**Analysis pipeline** (order matters):
1. `standard` tokenizer — split on whitespace/punctuation
2. `lowercase`
3. `synonym_graph` — expand SearchUnify-specific terms (e.g. "SU" → "searchunify", "KCS" → "knowledge centered service") at both index and query time
4. `english` stemmer — "connectors" → "connector"

Synonyms run **before** stemming so expanded tokens are also stemmed correctly.

**Field boosts**: `title^3`, `section^2`, `breadcrumb^1`, `text^1` — title matches outscore body matches.

### Semantic search (Pinecone)

Each chunk is embedded with Gemini `text-embedding-004` (768-dim) using `RETRIEVAL_DOCUMENT` at ingest. At query time, the query is embedded with `RETRIEVAL_QUERY`. Pinecone uses **HNSW** (approximate nearest-neighbour) and **cosine similarity** to find semantically related chunks in O(log N).

### Reciprocal Rank Fusion (RRF)

BM25 scores and cosine similarity scores are on incompatible scales. RRF converts ranks to scores:
```
score = Σ 1 / (k + rank)   for each ranked list,  k = 60
```
A chunk ranked #1 in both lists: `2/(61) ≈ 0.033`. Ranked #1 in one list only: `1/61 ≈ 0.016`. The constant `k=60` prevents top-ranked results from dominating too strongly.

### Content extraction

Each page is split at H1/H2/H3 boundaries into sections. Each section carries:
- `anchor_id` → used to build `section_url = page_url#anchor_id` for deep-linking
- Images: `alt` text extracted as `[Image: description]`
- Tables: converted to `Header: value` lines
- Code blocks: preserved in backtick fences

Sections larger than 500 words are sub-split with 50-word overlap, heading prepended to each sub-chunk.

---

## Stack

| Layer | Technology |
|---|---|
| Lexical search | OpenSearch via LocalStack (emulates Amazon OpenSearch Service) |
| Vector search | Pinecone (pinecone-local for dev, Pinecone cloud free tier for prod) |
| Embeddings | Gemini `text-embedding-004` (768-dim) |
| Fusion | Reciprocal Rank Fusion (app layer) |
| Backend | FastAPI + Python 3.11 |
| Frontend | React 18 + TypeScript + Vite |

---

## Quick start

### Prerequisites
- Docker + Docker Compose
- Poetry (Python package manager)
- Node.js 20+
- A Gemini API key (free at [aistudio.google.com](https://aistudio.google.com))

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your_key_here
```

### 2. Start infrastructure (LocalStack + Pinecone-local)

```bash
docker compose up localstack pinecone-local -d
# Wait for health checks to pass (~30s)
```

### 3. Install Python deps

```bash
poetry install
```

### 4. Run ingest (scrapes ~450 pages, embeds, indexes — takes ~15-30 min)

```bash
# Test with 10 pages first
poetry run python -m backend.ingest --limit 10

# Full ingest
poetry run python -m backend.ingest
```

Add `--force` to re-create the OpenSearch index (clears all data).

### 5. Start backend

```bash
poetry run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### 6. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

---

## Full Docker Compose (all services)

```bash
cp .env.example .env
# Edit .env — set GEMINI_API_KEY

docker compose up
# Then run ingest separately (needs Gemini key in host env)
poetry run python -m backend.ingest
```

---

## Project structure

```
su-docs-search/
├── backend/
│   ├── config.py              # Pydantic settings from .env
│   ├── ingest.py              # CLI: scrape → chunk → embed → index
│   ├── main.py                # FastAPI: GET /api/search, /api/health
│   ├── Dockerfile
│   └── search/
│       ├── scraper.py         # Sitemap → PageDoc (sections, alt text, tables)
│       ├── chunker.py         # Section-based chunker → Chunk (with section_url)
│       ├── embedder.py        # Gemini text-embedding-004 batched client
│       ├── opensearch_index.py  # BM25 index: synonym_graph + english stemmer
│       ├── pinecone_index.py    # Pinecone: create, upsert, kNN query
│       ├── search_service.py    # Fan-out + RRF + degradation
│       ├── fusion.py            # RRF merge (pure)
│       └── synonyms.txt         # SearchUnify-specific synonym pairs
├── frontend/
│   └── src/
│       ├── SearchPage.tsx     # Search box + result cards with section deep-links
│       ├── api.ts             # fetch /api/search
│       └── types.ts           # SearchResult, SearchResponse
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## API

### `GET /api/search`

| Param | Type | Required | Description |
|---|---|---|---|
| `q` | string | yes | Search query |
| `category` | string | no | Filter by doc category (e.g. `Content Sources`) |
| `top_n` | int | no | Max results (default 20, max 100) |

**Response:**
```json
{
  "query": "jira setup",
  "total": 8,
  "results": [
    {
      "chunk_id": "abc123",
      "title": "Jira Connector",
      "url": "https://docs.searchunify.com/Content/Jira.htm",
      "section_url": "https://docs.searchunify.com/Content/Jira.htm#prerequisites",
      "section": "## Prerequisites",
      "category": "Content Sources",
      "snippet": "You need an API token to connect Jira...",
      "score": 0.031
    }
  ]
}
```

Result cards link to `section_url` — a deep-link that takes you directly to the matching section in the original doc.

### `GET /api/health`
Returns `{"status": "ok"}`.

---

## Degradation

- If **OpenSearch fails** at query time: returns Pinecone-only results (logged warning)
- If **Pinecone fails** at query time: returns OpenSearch-only results (logged warning)
- If **Gemini embed fails** at ingest: chunk still lands in OpenSearch (no vector)
- If **a page fetch fails** during ingest: skipped and logged, rest continues

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required.** Gemini API key |
| `OPENSEARCH_URL` | `http://localhost:4566/opensearch/us-east-1/su-docs` | LocalStack OpenSearch endpoint |
| `PINECONE_HOST` | `http://localhost:5081` | pinecone-local endpoint |
| `PINECONE_INDEX` | `su-docs` | Pinecone index name |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose ingest logging |
