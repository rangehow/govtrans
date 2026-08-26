import { Clock3, FileCheck2, Languages, Plus, ShieldCheck } from 'lucide-react'
import { formatRelativeTime, STATUS_LABEL } from '../status'
import type { RunSummary } from '../types'

interface Props {
  runs: RunSummary[]
  activeRunId: string | null
  loading: boolean
  onSelectRun: (runId: string) => void
  onNewRun: () => void
}

export default function HistorySidebar({
  runs,
  activeRunId,
  loading,
  onSelectRun,
  onNewRun,
}: Props) {
  return (
    <aside className="history-sidebar" aria-label="翻译任务历史">
      <div className="brand-block">
        <span className="brand-mark" aria-hidden="true"><Languages size={22} /></span>
        <div>
          <strong>GovTrans</strong>
          <span>政务翻译中心</span>
        </div>
      </div>

      <button type="button" className="new-run-button" onClick={onNewRun}>
        <Plus size={17} aria-hidden="true" />
        新建翻译
      </button>

      <div className="history-heading">
        <span>任务记录</span>
        <span>{runs.length}</span>
      </div>

      <div className="history-list">
        {loading && runs.length === 0 && (
          <div className="history-loading">正在恢复任务记录…</div>
        )}
        {!loading && runs.length === 0 && (
          <div className="history-empty">
            <FileCheck2 size={22} aria-hidden="true" />
            <span>尚无翻译任务</span>
          </div>
        )}
        {runs.map((item) => (
          <button
            type="button"
            key={item.run_id}
            className={`history-item ${activeRunId === item.run_id ? 'active' : ''}`}
            onClick={() => onSelectRun(item.run_id)}
            aria-current={activeRunId === item.run_id ? 'page' : undefined}
          >
            <span className="history-item-title">{item.title}</span>
            <span className="history-item-meta">
              <span className={`history-status-dot status-dot-${item.status}`} aria-hidden="true" />
              <span>{STATUS_LABEL[item.status]}</span>
              <span className="history-pair">{item.source_language} → {item.target_language}</span>
              <span className="history-time"><Clock3 size={11} />{formatRelativeTime(item.updated_at)}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="sidebar-footnote">
        <ShieldCheck size={15} aria-hidden="true" />
        <span>服务端持久化 · 断线自动恢复</span>
      </div>
    </aside>
  )
}
