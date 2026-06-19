export interface SearchResult {
  chunk_id: string
  title: string
  url: string
  section_url: string
  section: string
  category: string
  snippet: string
  score: number
}

export interface SearchResponse {
  results: SearchResult[]
  total: number
  query: string
}
