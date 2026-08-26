import type {
  CostReport,
  Confidentiality,
  LanguageCatalog,
  LanguagePair,
  Run,
  RunEvent,
  RunSummary,
} from './types'

const BASE = './api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const body = await resp.text()
    let detail = ''
    try {
      const parsed = JSON.parse(body) as { detail?: string | Array<{ msg?: string }> }
      detail = typeof parsed.detail === 'string'
        ? parsed.detail
        : (parsed.detail || []).map((item) => item.msg).filter(Boolean).join('；')
    } catch { detail = body.slice(0, 200) }
    throw new Error(detail || `请求失败（HTTP ${resp.status}）`)
  }
  return resp.json() as Promise<T>
}

export interface ManualTermInput {
  source: string
  target: string
  proper_name?: boolean
  note?: string
}

export interface CreateRunOptions {
  sourceLanguage?: string
  targetLanguage?: string
  documentType?: string
  styleSkills?: string[]
  manualTerms?: ManualTermInput[]
  translationMode?: 'coherent' | 'balanced'
}

export function createRun(
  sourceText: string,
  confidentiality: Confidentiality,
  options: CreateRunOptions = {},
): Promise<{ run_id: string; status: string }> {
  return request('/runs', {
    method: 'POST',
    body: JSON.stringify({
      source_text: sourceText,
      source_language: options.sourceLanguage || 'zh',
      target_language: options.targetLanguage || 'en',
      confidentiality,
      document_type: options.documentType || null,
      style_skills: options.styleSkills,
      manual_terms: options.manualTerms || [],
      translation_mode: options.translationMode || 'coherent',
    }),
  })
}

export function listLanguages(): Promise<LanguageCatalog> {
  return request('/languages')
}

export function getLanguagePair(source: string, target: string): Promise<LanguagePair> {
  return request(`/languages/capabilities?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`)
}

export function getRun(runId: string): Promise<Run> {
  return request(`/runs/${runId}`)
}

export function listRuns(limit = 30): Promise<{ runs: RunSummary[]; has_more: boolean }> {
  return request(`/runs?limit=${limit}`)
}

export function getRunEventLog(runId: string, after = 0): Promise<{ events: RunEvent[]; last_cursor: number }> {
  const query = after > 0 ? `?after=${after}` : ''
  return request(`/runs/${runId}/event-log${query}`)
}

export function updateSegmentTranslation(
  runId: string,
  segmentId: string,
  translation: string,
  resolveIssueId?: string,
): Promise<Run> {
  return request(`/runs/${runId}/segments/${segmentId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      translation,
      resolve_issue_id: resolveIssueId || null,
    }),
  })
}

export function cancelRun(runId: string): Promise<{ status: string }> {
  return request(`/runs/${runId}/cancel`, { method: 'POST' })
}

export function continueRun(runId: string): Promise<{ run_id: string; status: string }> {
  return request(`/runs/${runId}/continue`, { method: 'POST' })
}

export function getRunCost(runId: string): Promise<CostReport> {
  return request(`/runs/${runId}/cost`)
}

export function openEventStream(runId: string, lastSeq: number): EventSource {
  const qs = lastSeq > 0 ? `?cursor=${lastSeq}` : ''
  return new EventSource(`${BASE}/runs/${runId}/events${qs}`)
}

export function exportRunUrl(runId: string, format: 'docx' | 'docx_bilingual' | 'txt' = 'docx'): string {
  return `${BASE}/runs/${runId}/export?format=${format}`
}


export interface Term {
  id: string
  source_term: string
  preferred_target: string
  source_language: string
  target_language: string
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

export function listTerms(q: string, sourceLanguage?: string, targetLanguage?: string): Promise<{ terms: Term[] }> {
  const pair = sourceLanguage && targetLanguage
    ? `&source_language=${encodeURIComponent(sourceLanguage)}&target_language=${encodeURIComponent(targetLanguage)}`
    : ''
  return request(`/terms?q=${encodeURIComponent(q)}&top_k=10${pair}`)
}

export function createTerm(data: { source_term: string; preferred_target: string; source_language: string; target_language: string; domain?: string; context?: string }): Promise<{ id: string }> {
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
export interface Alignment { id: string; level: string; idx: number; zh_text: string; en_text: string; score: number; status: string; tm_entry_id: string | null; reference_tier: 'automatic' | 'human_verified' | 'archive_only' | 'excluded' }

export function listDocuments(): Promise<{ documents: CorpusDocument[] }> { return request('/corpus/documents?limit=200') }
export function listPairs(): Promise<{ pairs: CorpusPair[] }> { return request('/corpus/pairs?limit=200') }
export function listAlignments(pairId: string): Promise<{ alignments: Alignment[] }> { return request(`/corpus/pairs/${pairId}/alignments?level=sentence`) }
export function updateAlignment(id: string, data: { status: 'auto' | 'approved' | 'rejected'; zh_text?: string; en_text?: string }): Promise<{ status: string; tm_entry_id: string | null }> {
  return request(`/corpus/alignments/${id}`, { method: 'PATCH', body: JSON.stringify(data) })
}

export interface StyleSkill {
  id: string
  name: string
  description: string
  version: string
  category: 'foundation' | 'style'
  locked: boolean
  supported_pairs: string[]
  default_for: string[]
  source: string | null
  base_rule_count: number
  candidate_rule_count: number
  distilled_rule_count: number
}

export interface StyleRule {
  id: string
  rule: string
  zh_pattern: string
  en_rendering: string
  source_count: number
  domains: string[]
  confidence: number
  status: 'candidate' | 'approved' | 'rejected'
  activation_source: 'automatic' | 'human' | null
  activated_at: string | null
  version: string
  examples: Array<{ zh: string; en: string; pair_id: string }>
}

export function listStyleSkills(): Promise<{ skills: StyleSkill[] }> {
  return request('/style-skills')
}

export function listStyleRules(): Promise<{ rules: StyleRule[] }> {
  return request('/style-rules')
}

export function mineStyleRules(minSupport = 2): Promise<Record<string, number>> {
  return request('/style-rules/mine', {
    method: 'POST', body: JSON.stringify({ min_support: minSupport }),
  })
}

export function reviewStyleRule(id: string, status: 'candidate' | 'approved' | 'rejected'): Promise<{ id: string; status: string }> {
  return request(`/style-rules/${id}/review`, {
    method: 'POST', body: JSON.stringify({ status }),
  })
}

export interface ScioImportResult {
  ingest: {
    pair_id: string
    paragraph_pairs: number
    sentence_pairs: number
    promoted_to_tm: number
    warnings: string[]
  }
  distillation: Record<string, number>
  source_pages: { zh: string[]; en: string[] }
}

export interface ScioSyncResult {
  discovered: number
  synced: Array<{
    title: string
    zh_url: string
    en_url: string
    pair_id: string
    sentence_pairs: number
    reused: boolean
    source_pages: { zh: string[]; en: string[] }
  }>
  failed: Array<{ title: string; error: string }>
  distillation: Record<string, number>
}

export type ScioSyncJobStatus =
  | 'queued'
  | 'discovering'
  | 'running'
  | 'distilling'
  | 'completed'
  | 'partial'
  | 'failed'

export interface ScioSyncJob {
  job_id: string
  source: string
  status: ScioSyncJobStatus
  stage: string
  since_year: number
  through_year: number
  discovered: number
  processed: number
  succeeded: number
  failed_count: number
  sentence_pairs: number
  current_title: string | null
  progress: number
  error: string | null
  synced: Array<{
    title: string
    publish_year: number | null
    zh_url: string
    en_url: string
    pair_id: string
    sentence_pairs: number
    reused: boolean
  }>
  failed: Array<{ title: string; publish_year: number | null; error: string }>
  distillation: Record<string, number>
  created_at: string | null
  updated_at: string | null
  created?: boolean
}

export function importScioPair(
  zhUrl: string,
  enUrl: string,
  domain?: string,
  savedHtml: { zhHtml?: string; enHtml?: string } = {},
): Promise<ScioImportResult> {
  return request('/corpus/scio/import-pair', {
    method: 'POST',
    body: JSON.stringify({
      zh_url: zhUrl,
      en_url: enUrl,
      domain: domain || null,
      zh_html: savedHtml.zhHtml || null,
      en_html: savedHtml.enHtml || null,
    }),
  })
}

export function syncScioCorpus(limit = 3, domain?: string): Promise<ScioSyncResult> {
  return request('/corpus/scio/sync', {
    method: 'POST',
    body: JSON.stringify({ limit, domain: domain || null }),
  })
}

export function startScioCorpusSync(years = 10, domain?: string): Promise<ScioSyncJob> {
  return request('/corpus/scio/sync-jobs', {
    method: 'POST',
    body: JSON.stringify({ years, domain: domain || null }),
  })
}

export function getScioCorpusSync(jobId: string): Promise<ScioSyncJob> {
  return request(`/corpus/scio/sync-jobs/${jobId}`)
}

export function getLatestScioCorpusSync(): Promise<{ job: ScioSyncJob | null }> {
  return request('/corpus/scio/sync-jobs/latest')
}
