import RunTimeline from './RunTimeline'
import SourceColumn from './SourceColumn'
import TranslationColumn from './TranslationColumn'
import IntelligencePanel from './IntelligencePanel'
import type { WorkspaceProps } from './types'
import { cancelRun } from '../api'

const TERMINAL = ['COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_HUMAN_REVIEW']

export default function Workspace({
  run,
  events,
  busy,
  selectedSegmentId,
  onSelectSegment,
  onCancelRun,
}: WorkspaceProps) {
  if (!run && busy) {
    return (
      <div className="workspace-hero-state">
        <div className="panel-empty hero">正在创建运行，初始化翻译工作空间…</div>
      </div>
    )
  }

  if (!run) {
    return (
      <div className="workspace-hero-state">
        <div className="panel-empty hero">
          请在上方输入框粘贴原文并提交，开启 GovTrans 智能政务翻译工作空间
        </div>
      </div>
    )
  }

  const running = !TERMINAL.includes(run.status)

  return (
    <div className="workspace-container">
      {/* Run status & timeline top bar */}
      <section className="run-status-bar" aria-label="运行状态与进度">
        <div className="run-status-row">
          <div className="run-status-info">
            <h2>
              运行状态：<span className={`status status-${run.status}`}>{run.status}</span>
              <span className="run-id-tag">ID: {run.run_id.slice(0, 8)}</span>
            </h2>
            {running && (
              <button className="link cancel-btn" onClick={() => onCancelRun(run.run_id)}>
                取消运行
              </button>
            )}
          </div>
          <div className="progress-wrap">
            <progress value={run.progress} max={1} />
            <span className="progress-pct">{Math.round((run.progress || 0) * 100)}%</span>
          </div>
        </div>
        {run.error && (
          <div role="alert" className="error-banner">
            {run.error}
          </div>
        )}
      </section>

      <RunTimeline events={events} />

      {/* 3-Column Workspace Grid */}
      <div className="workspace-three-col">
        <SourceColumn
          segments={run.segments}
          selectedSegmentId={selectedSegmentId}
          onSelectSegment={onSelectSegment}
        />
        <TranslationColumn
          segments={run.segments}
          selectedSegmentId={selectedSegmentId}
          onSelectSegment={onSelectSegment}
        />
        <IntelligencePanel
          run={run}
          events={events}
          selectedSegmentId={selectedSegmentId}
        />
      </div>
    </div>
  )
}
