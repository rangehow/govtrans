import { useEffect, useRef, useState } from 'react'
import { Check, Cpu, LoaderCircle, ShieldCheck, X } from 'lucide-react'
import type { PipelineStep, RunEvent, RunStatus } from '../types'

interface StageDef {
  key: string
  label: string
  phases: string[]
}

export const STAGES: StageDef[] = [
  { key: 'analyze', label: '文档分析', phases: ['parse', 'analyze'] },
  { key: 'terminology', label: '术语与参考', phases: ['terminology', 'retrieve', 'plan'] },
  { key: 'translate', label: '全文翻译', phases: ['translate'] },
  { key: 'review', label: '语义·风格审校', phases: ['deterministic_qa', 'term_review', 'semantic_review', 'style_review'] },
  { key: 'consistency', label: '全文一致性', phases: ['consistency_review'] },
  { key: 'finalize', label: '自动修订', phases: ['finalize'] },
  { key: 'finalqa', label: '交付前校验', phases: ['final_qa', 'complete'] },
]

export type StageState = 'pending' | 'active' | 'done' | 'failed'

function groupIndexForPhase(phase: string | null): number {
  if (!phase) return -1
  return STAGES.findIndex((stage) => stage.phases.includes(phase))
}

export function deriveStageStates(
  events: RunEvent[],
  currentStage: string | null = null,
  runStatus?: RunStatus,
): Record<string, StageState> {
  const states: Record<string, StageState> = Object.fromEntries(
    STAGES.map((stage) => [stage.key, 'pending' as StageState]),
  )

  for (const event of [...events].sort((left, right) => left.seq - right.seq)) {
    const index = groupIndexForPhase(event.phase)
    if (index < 0) continue
    const key = STAGES[index].key
    if (event.status === 'started') {
      states[key] = 'active'
      // A retry/final-QA loop can revisit an earlier stage. Clear stale
      // terminal states ahead of it, but preserve later stages that are
      // genuinely active in parallel (early translation alongside analysis
      // and terminology is the important case).
      for (let later = index + 1; later < STAGES.length; later += 1) {
        if (states[STAGES[later].key] === 'done' || states[STAGES[later].key] === 'failed') {
          states[STAGES[later].key] = 'pending'
        }
      }
    } else if (event.status === 'progress') {
      states[key] = 'active'
    } else if (event.status === 'completed') {
      states[key] = 'done'
    } else if (event.status === 'failed') {
      states[key] = 'failed'
    }
  }

  if (runStatus === 'COMPLETED') {
    for (const stage of STAGES) states[stage.key] = 'done'
    return states
  }

  const currentIndex = groupIndexForPhase(currentStage)
  if (currentIndex >= 0 && runStatus && ![
    'QUALITY_GATE_FAILED', 'FAILED', 'CANCELLED', 'WAITING_HUMAN_REVIEW',
  ].includes(runStatus)) {
    for (let index = 0; index < currentIndex; index += 1) {
      if (states[STAGES[index].key] === 'pending') states[STAGES[index].key] = 'done'
    }
    if (states[STAGES[currentIndex].key] === 'pending') {
      states[STAGES[currentIndex].key] = 'active'
    }
    // current_stage is the durable resume cursor, not the complete picture of
    // parallel work. Event-derived later active stages (for example terminology
    // and short-document translation running alongside analysis) must remain
    // visible instead of being reset to "waiting" here.
  }

  if (runStatus === 'QUALITY_GATE_FAILED' || runStatus === 'WAITING_HUMAN_REVIEW') {
    states.finalqa = 'failed'
  } else if ((runStatus === 'FAILED' || runStatus === 'CANCELLED') && currentIndex >= 0) {
    states[STAGES[currentIndex].key] = 'failed'
  }
  return states
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} s`
  const minutes = Math.floor(milliseconds / 60_000)
  const seconds = Math.round((milliseconds % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

function activeDuration(startedAt: string | null, now: number): string {
  if (!startedAt) return '刚刚启动'
  const seconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000))
  if (seconds < 60) return `已运行 ${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `已运行 ${minutes} 分 ${remainder} 秒`
}

function stageActivity(stage: StageDef, events: RunEvent[]) {
  const scoped = [...events]
    .filter((event) => stage.phases.includes(event.phase))
    .sort((left, right) => left.seq - right.seq)
  let startedAt: string | null = null
  for (const event of scoped) {
    if (event.status === 'started') startedAt = event.created_at
    if (event.status === 'progress' && !startedAt) startedAt = event.created_at
    if (event.status === 'completed' || event.status === 'failed') startedAt = null
  }
  return { latest: scoped[scoped.length - 1], startedAt }
}

function activeDetail(event: RunEvent | undefined): string {
  if (!event) return '后台处理中'
  const batch = Number(event.metrics.batch || 0)
  const batchCount = Number(event.metrics.batch_count || 0)
  if (batch > 0 && batchCount > 0) return `已完成 ${batch}/${batchCount} 个连续章节`
  const attempt = Number(event.metrics.attempt || 0)
  const maxAttempts = Number(event.metrics.max_attempts || 0)
  if (attempt > 0 && maxAttempts > 0) return `资源重试 ${attempt}/${maxAttempts}`
  if (event.metrics.heartbeat === true) return '后台刚刚确认仍在处理'
  return event.summary || event.title || '后台处理中'
}

function executionSummary(stage: StageDef, pipelineSteps: PipelineStep[]) {
  const steps = pipelineSteps.filter((item) => stage.phases.includes(item.id))
  if (steps.length === 0) return { label: '运行信息同步中', calls: 0, latency: 0 }
  const models = [...new Set(steps.flatMap((item) => item.models))]
  const hasRules = steps.some((item) => item.kind === 'rules' || item.kind === 'hybrid')
  const engines = [...new Set(steps.map((item) => item.engine).filter(Boolean))]
  const calls = steps.reduce((sum, item) => sum + item.calls, 0)
  const latency = steps.reduce((sum, item) => sum + item.latency_ms, 0)

  const label = models.length > 0
    ? `${models.join(' + ')}${hasRules ? ' · 规则协同' : ''}`
    : engines.join(' + ') || '确定性规则'
  return { label, calls, latency }
}

const STATE_LABEL: Record<StageState, string> = {
  pending: '等待', active: '运行中', done: '已完成', failed: '需处理',
}

interface Props {
  events: RunEvent[]
  runStatus: RunStatus
  currentStage: string | null
  pipelineSteps: PipelineStep[]
}

export default function RunTimeline({
  events,
  runStatus,
  currentStage,
  pipelineSteps,
}: Props) {
  const states = deriveStageStates(events, currentStage, runStatus)
  const [now, setNow] = useState(() => Date.now())
  const timelineRef = useRef<HTMLOListElement>(null)
  const activeKey = [...STAGES].reverse().find((stage) => states[stage.key] === 'active')?.key

  useEffect(() => {
    if (!Object.values(states).includes('active')) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [runStatus, currentStage, events.length])

  useEffect(() => {
    if (!activeKey) return
    timelineRef.current
      ?.querySelector<HTMLElement>(`[data-testid="stage-${activeKey}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [activeKey])

  return (
    <section className="timeline-shell" aria-label="自动翻译流程与模型运行状态">
      <div className="timeline-heading">
        <div>
          <span>PROCESS</span>
          <strong>全流程实时进展</strong>
        </div>
        <p><Cpu size={13} />每一步均显示实际配置的模型或规则引擎</p>
      </div>
      <ol className="timeline" ref={timelineRef}>
        {STAGES.map((stage, index) => {
          const state = states[stage.key]
          const execution = executionSummary(stage, pipelineSteps)
          const activity = stageActivity(stage, events)
          return (
            <li
              key={stage.key}
              className={`timeline-node ${state}`}
              data-testid={`stage-${stage.key}`}
              aria-current={state === 'active' ? 'step' : undefined}
            >
              <div className="timeline-marker" aria-hidden="true">
                <span className="timeline-step">{String(index + 1).padStart(2, '0')}</span>
                <span className="timeline-dot">
                  {state === 'done' && <Check size={13} />}
                  {state === 'active' && <LoaderCircle size={13} />}
                  {state === 'failed' && <X size={13} />}
                  {state === 'pending' && <span />}
                </span>
              </div>
              <div className="timeline-copy">
                <div className="timeline-title-row">
                  <span className="timeline-label">{stage.label}</span>
                  <span className="timeline-state-label">{STATE_LABEL[state]}</span>
                </div>
                <span className="timeline-model" title={execution.label}>
                  {stage.key === 'finalqa' && state === 'done'
                    ? <ShieldCheck size={12} /> : <Cpu size={12} />}
                  {execution.label}
                </span>
                {state === 'active' && (
                  <span className="timeline-live-detail">
                    <span><i aria-hidden="true" />{activeDetail(activity.latest)}</span>
                    <time>{activeDuration(activity.startedAt, now)}</time>
                  </span>
                )}
                {execution.calls > 0 && (
                  <span className="timeline-metrics" title={`${execution.calls} 次模型调用，累计耗时 ${formatDuration(execution.latency)}`}>
                    {execution.calls} 次调用 · 累计 {formatDuration(execution.latency)}
                  </span>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
