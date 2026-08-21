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
