import type { SearchResponse } from './types'

export async function searchDocs(
  query: string,
  category?: string,
  topN = 20,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, top_n: String(topN) })
  if (category) params.set('category', category)
  const resp = await fetch(`/api/search?${params}`)
  if (!resp.ok) throw new Error(`Search failed: ${resp.status}`)
  return resp.json()
}
