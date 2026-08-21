import type { CostReport, Confidentiality, Run } from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(`API ${resp.status}: ${body.slice(0, 200)}`)
  }
  return resp.json() as Promise<T>
}

export function createRun(
  sourceText: string,
  confidentiality: Confidentiality,
  documentType?: string,
): Promise<{ run_id: string; status: string }> {
  return request('/runs', {
    method: 'POST',
    body: JSON.stringify({
      source_text: sourceText,
      direction: 'zh-en',
      confidentiality,
      document_type: documentType || null,
    }),
  })
}

export function getRun(runId: string): Promise<Run> {
  return request(`/runs/${runId}`)
}

export function cancelRun(runId: string): Promise<{ status: string }> {
  return request(`/runs/${runId}/cancel`, { method: 'POST' })
}

export function getRunCost(runId: string): Promise<CostReport> {
  return request(`/runs/${runId}/cost`)
}

export function openEventStream(runId: string, lastSeq: number): EventSource {
  const qs = lastSeq > 0 ? `?cursor=${lastSeq}` : ''
  return new EventSource(`${BASE}/runs/${runId}/events${qs}`)
}


export interface Term {
  id: string
  source_term: string
  preferred_target: string
  domain: string | null
  status: string
}

export interface TermHistory {
  action: string
  before: string | null
  after: string | null
  actor: string
  created_at: string
}

export function listTerms(q: string): Promise<{ terms: Term[] }> {
  return request(`/terms?q=${encodeURIComponent(q)}&top_k=10`)
}

export function createTerm(data: { source_term: string; preferred_target: string; domain?: string; context?: string }): Promise<{ id: string }> {
  return request('/terms', { method: 'POST', body: JSON.stringify(data) })
}

export function updateTerm(id: string, data: { preferred_target?: string; domain?: string; context?: string }): Promise<{ status: string }> {
  return request(`/terms/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
}

export function deprecateTerm(id: string): Promise<{ status: string }> {
  return request(`/terms/${id}/deprecate`, { method: 'POST' })
}

export function getTermHistory(id: string): Promise<{ history: TermHistory[] }> {
  return request(`/terms/${id}/history`)
}

export interface CorpusDocument { id: string; url: string; title: string; lang: string; document_type: string; domain: string; metadata: Record<string, unknown>; fetched_at: string }
export interface CorpusPair { id: string; zh_doc_id: string; en_doc_id: string; match_method: string; match_confidence: number; status: string }
export interface Alignment { id: string; level: string; idx: number; zh_text: string; en_text: string; score: number; status: string; tm_entry_id: string | null }

export function listDocuments(): Promise<{ documents: CorpusDocument[] }> { return request('/corpus/documents?limit=50') }
export function listPairs(): Promise<{ pairs: CorpusPair[] }> { return request('/corpus/pairs') }
export function listAlignments(pairId: string): Promise<{ alignments: Alignment[] }> { return request(`/corpus/pairs/${pairId}/alignments?level=sentence`) }
export function updateAlignment(id: string, data: { status: 'approved' | 'rejected'; zh_text?: string; en_text?: string }): Promise<{ status: string; tm_entry_id: string | null }> {
  return request(`/corpus/alignments/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
}
