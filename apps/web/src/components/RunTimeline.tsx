import type { RunEvent } from '../types'

interface StageDef {
  key: string
  label: string
  phases: string[]
}

// UI timeline buckets mapped onto pipeline phases (docs/TRANSLATION_PIPELINE.md).
export const STAGES: StageDef[] = [
  { key: 'analyze', label: '文档分析', phases: ['parse', 'analyze'] },
  { key: 'terminology', label: '术语研究', phases: ['terminology'] },
  { key: 'research', label: '参考准备', phases: ['retrieve', 'plan'] },
  { key: 'translate', label: '翻译', phases: ['translate'] },
  { key: 'semantic', label: '语义审校', phases: ['semantic_review'] },
  { key: 'style', label: '风格审校', phases: ['style_review'] },
  { key: 'consistency', label: '一致性', phases: ['consistency_review'] },
  { key: 'finalqa', label: '终审 QA', phases: ['deterministic_qa', 'term_review', 'finalize', 'final_qa'] },
]

export type StageState = 'pending' | 'active' | 'done' | 'failed'

export function deriveStageStates(events: RunEvent[]): Record<string, StageState> {
  const states: Record<string, StageState> = {}
  for (const s of STAGES) states[s.key] = 'pending'
  for (const ev of events) {
    for (const s of STAGES) {
      if (!s.phases.includes(ev.phase)) continue
      if (ev.status === 'started' || ev.status === 'progress') states[s.key] = 'active'
      else if (ev.status === 'completed' && states[s.key] !== 'failed') states[s.key] = 'done'
      else if (ev.status === 'failed') states[s.key] = 'failed'
    }
  }
  return states
}

export default function RunTimeline({ events }: { events: RunEvent[] }) {
  const states = deriveStageStates(events)
  return (
    <ol className="timeline" aria-label="翻译流程进度">
      {STAGES.map((s) => (
        <li key={s.key} className={`timeline-node ${states[s.key]}`} data-testid={`stage-${s.key}`}>
          <span className="timeline-dot" aria-hidden="true" />
          <span className="timeline-label">{s.label}</span>
        </li>
      ))}
    </ol>
  )
}
