import { Cpu } from 'lucide-react'
import type { RunEvent } from '../types'

const PHASE_LABEL: Record<string, string> = {
  run: '运行', parse: '解析', analyze: '分析', terminology: '术语', retrieve: '检索',
  plan: '规划', translate: '翻译', deterministic_qa: '数字·格式',
  term_review: '术语审校', semantic_review: '语义审校', style_review: '风格审校',
  consistency_review: '一致性审校', finalize: '定稿', final_qa: '终审 QA', complete: '完成',
  manual_edit: '人工修改',
}

function executionLabel(event: RunEvent): string | null {
  const models = Array.isArray(event.metrics.models)
    ? event.metrics.models.filter((item): item is string => typeof item === 'string')
    : []
  if (models.length > 0) return models.join(' + ')
  return typeof event.metrics.engine === 'string' ? event.metrics.engine : null
}

export default function ActivityFeed({ events }: { events: RunEvent[] }) {
  // Liveness heartbeats drive the timeline and live-status card, but repeating
  // them in the audit feed would bury actual stage results.
  const visibleEvents = events.filter((event) => event.metrics.heartbeat !== true)
  if (visibleEvents.length === 0) {
    return <div className="panel-empty">正在等待第一条处理进展…</div>
  }
  return (
    <ul className="activity-feed" aria-label="运行活动流">
      {[...visibleEvents].reverse().map((ev) => {
        const execution = executionLabel(ev)
        return <li key={`${ev.seq}-${ev.id}`} className={`activity-item ${ev.status}`}>
          <div className="activity-head">
            <span className="activity-phase">{PHASE_LABEL[ev.phase] ?? ev.phase}</span>
            <span className="activity-title">{ev.title}</span>
            <time className="activity-time">{new Date(ev.created_at).toLocaleTimeString()}</time>
          </div>
          {ev.summary && <p className="activity-summary">{ev.summary}</p>}
          {execution && <span className="activity-execution"><Cpu size={11} />{execution}</span>}
        </li>
      })}
    </ul>
  )
}
