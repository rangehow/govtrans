import type { RunEvent } from '../types'

const PHASE_LABEL: Record<string, string> = {
  run: '运行', parse: '解析', analyze: '分析', terminology: '术语', retrieve: '检索',
  plan: '规划', translate: '翻译', deterministic_qa: '确定 QA',
  term_review: '术语审校', semantic_review: '语义审校', style_review: '风格审校',
  consistency_review: '一致性审校', finalize: '定稿', final_qa: '终审 QA', complete: '完成',
}

export default function ActivityFeed({ events }: { events: RunEvent[] }) {
  if (events.length === 0) {
    return <div className="panel-empty">暂无活动 — 提交原文后这里会实时显示后端事件流</div>
  }
  return (
    <ul className="activity-feed" aria-label="运行活动流">
      {[...events].reverse().map((ev) => (
        <li key={`${ev.seq}-${ev.id}`} className={`activity-item ${ev.status}`}>
          <div className="activity-head">
            <span className="activity-phase">{PHASE_LABEL[ev.phase] ?? ev.phase}</span>
            <span className="activity-title">{ev.title}</span>
            <time className="activity-time">{new Date(ev.created_at).toLocaleTimeString()}</time>
          </div>
          {ev.summary && <p className="activity-summary">{ev.summary}</p>}
        </li>
      ))}
    </ul>
  )
}
