export type RunStatus =
  | 'CREATED' | 'PARSING' | 'ANALYZING' | 'RESEARCHING' | 'TRANSLATING'
  | 'REVIEWING' | 'FINALIZING' | 'QA' | 'COMPLETED' | 'FAILED'
  | 'QUALITY_GATE_FAILED' | 'CANCELLED' | 'WAITING_RESOURCES' | 'WAITING_HUMAN_REVIEW'

export type Confidentiality = 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL'

export interface LanguageSpec {
  code: string
  name_zh: string
  name_en: string
  bcp47: string
  rtl: boolean
}

export interface LanguagePairCapabilities {
  model_translation: boolean
  pair_scoped_terminology: boolean
  official_corpus: boolean
  official_corpus_direction: 'direct' | 'reverse' | 'none'
  specialized_style: boolean
  qa_tier: 'zh_en_enhanced' | 'multilingual_universal'
  description: string
}

export interface LanguagePair {
  source: LanguageSpec
  target: LanguageSpec
  direction: string
  capabilities: LanguagePairCapabilities
}

export interface LanguageCatalog {
  languages: LanguageSpec[]
  defaults: { source_language: string; target_language: string }
  enhanced_pairs: LanguagePair[]
  policy: string
}

export interface Segment {
  id: string
  idx: number
  source: string
  translation: string | null
  status: 'pending' | 'translated' | 'reviewed' | 'final'
  versions: Partial<Record<'ai_draft' | 'reviewed' | 'final' | 'manual_previous' | 'manual', string>>
  kind: 'title' | 'heading' | 'list' | 'paragraph'
}

export interface Issue {
  id: string
  segment_id: string | null
  reviewer: string
  severity: 'critical' | 'major' | 'minor'
  category: string
  message: string
  source_span: string | null
  target_span: string | null
  suggested_fix: string | null
  status: 'open' | 'resolved' | 'dismissed'
}

export interface RuntimeStyleSkill {
  id: string
  name: string
  kind: 'foundation' | 'style'
  selection: 'always' | 'automatic' | 'manual'
}

export interface GlossaryEntry {
  source: string
  target: string
  origin?: string
  mandatory?: boolean
  proper_name?: boolean
  evidence?: Array<Record<string, unknown>>
}

export interface TranslationReference {
  id: string
  source: string
  target: string
  source_document?: string | null
  url?: string | null
  authority?: string
  kind?: 'official_corpus' | 'verified_memory'
  usage?: 'advisory'
  score?: number
  alignment_score?: number | null
}

export interface KnowledgeUsage {
  style_skills: RuntimeStyleSkill[]
  terminology: GlossaryEntry[]
  references_by_segment: Record<string, TranslationReference[]>
  reference_count: number
  automatic_reference_count: number
  verified_reference_count: number
}

export interface PipelineCall {
  role: string
  model: string
  latency_ms: number
  retries: number
  status: string
  created_at: string | null
}

export interface PipelineStep {
  id: string
  title: string
  kind: 'rules' | 'model' | 'hybrid'
  engine: string
  roles: string[]
  models: string[]
  calls: number
  latency_ms: number
  retries: number
  last_status: string | null
  call_details: PipelineCall[]
}

export interface Run {
  run_id: string
  status: RunStatus
  direction: string
  source_language: string
  target_language: string
  language_pair: LanguagePair
  progress: number
  error: string | null
  summary: string | null
  source_text: string
  document_type: string | null
  confidentiality: Confidentiality
  style_skills: string[]
  manual_terms: Array<{ source: string; target: string; proper_name?: boolean; note?: string }>
  translation_mode: 'coherent' | 'balanced'
  current_stage: string | null
  loop_count: number
  pipeline_version: string
  segments: Segment[]
  issues: Issue[]
  created_at: string
  updated_at: string
  quality: {
    score: number
    open: Record<Issue['severity'], number>
    deductions: Record<Issue['severity'], number>
    blocking: number
    advisory: number
    gate: 'checking' | 'passed' | 'needs_optimization' | 'interrupted'
    label: string
    revision_rounds: number
    max_auto_rounds: number
    continue_count: number
    score_basis: string
    release_rule: string
  }
  pipeline_steps: PipelineStep[]
  knowledge_usage: KnowledgeUsage
}

export interface RunSummary {
  run_id: string
  title: string
  source_preview: string
  status: RunStatus
  direction: string
  source_language: string
  target_language: string
  progress: number
  document_type: string | null
  confidentiality: Confidentiality
  segment_count: number
  final_segment_count: number
  open_issues: Record<Issue['severity'], number>
  quality_score: number
  created_at: string
  updated_at: string
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
