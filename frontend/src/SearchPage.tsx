import React, { useState, useCallback } from 'react'
import type { SearchResult } from './types'
import { searchDocs } from './api'

const CATEGORY_COLORS: Record<string, string> = {
  'Content Sources': '#2563eb',
  'Agentic Suite': '#7c3aed',
  'Developer Guides': '#059669',
  'Release Notes': '#d97706',
  'FAQs': '#dc2626',
}

function categoryColor(cat: string): string {
  return CATEGORY_COLORS[cat] ?? '#64748b'
}

function sectionLabel(section: string): string {
  return section.replace(/^#+\s*/, '')
}

function ResultCard({ result }: { result: SearchResult }) {
  return (
    <div className="result-card">
      <div className="result-header">
        <a href={result.section_url} target="_blank" rel="noreferrer" className="result-title">
          {result.title}
        </a>
        <span
          className="category-badge"
          style={{ backgroundColor: categoryColor(result.category) }}
        >
          {result.category}
        </span>
      </div>
      {result.section && (
        <div className="result-section">{sectionLabel(result.section)}</div>
      )}
      <p className="result-snippet">{result.snippet}</p>
      <a href={result.section_url} target="_blank" rel="noreferrer" className="result-url">
        {result.section_url}
      </a>
    </div>
  )
}

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  const doSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    try {
      const resp = await searchDocs(q)
      setResults(resp.results)
      setSearched(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [query])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') doSearch()
  }

  return (
    <div className="page">
      <header className="header">
        <h1 className="logo">SearchUnify Docs</h1>
        <p className="tagline">Hybrid search — BM25 + semantic</p>
      </header>

      <div className="search-box">
        <input
          className="search-input"
          type="text"
          placeholder="Search docs..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button className="search-btn" onClick={doSearch} disabled={loading}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {searched && !loading && (
        <div className="results-meta">
          {results.length === 0
            ? 'No results found.'
            : `${results.length} result${results.length !== 1 ? 's' : ''}`}
        </div>
      )}

      <div className="results">
        {results.map(r => (
          <ResultCard key={r.chunk_id} result={r} />
        ))}
      </div>
    </div>
  )
}
