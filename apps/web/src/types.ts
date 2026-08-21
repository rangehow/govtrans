export type RunStatus =
  | 'CREATED' | 'PARSING' | 'ANALYZING' | 'RESEARCHING' | 'TRANSLATING'
  | 'REVIEWING' | 'FINALIZING' | 'QA' | 'COMPLETED' | 'FAILED'
  | 'CANCELLED' | 'WAITING_HUMAN_REVIEW'

export type Confidentiality = 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL'

export interface Segment {
  id: string
  idx: number
  source: string
  translation: string | null
  status: 'pending' | 'translated' | 'reviewed' | 'final'
}

export interface Issue {
  id: string
  segment_id: string | null
  reviewer: string
  severity: 'critical' | 'major' | 'minor'
  category: string
  message: string
  suggested_fix: string | null
  status: 'open' | 'resolved' | 'dismissed'
}

export interface Run {
  run_id: string
  status: RunStatus
  progress: number
  error: string | null
  segments: Segment[]
  issues: Issue[]
  created_at: string
}

export interface RunEvent {
  id: string
  run_id: string
  seq: number
  type: string
  phase: string
  status: 'started' | 'progress' | 'completed' | 'failed'
  title: string
  summary: string | null
  progress: number | null
  segment_ids: string[]
  evidence: Array<Record<string, unknown>>
  metrics: Record<string, unknown>
  created_at: string
}

export interface CostReport {
  total: { calls: number; input_tokens: number; output_tokens: number }
  by_role: Record<string, { calls: number; input_tokens: number; output_tokens: number }>
}
