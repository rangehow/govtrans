import type { VersionHistoryProps } from './types'

export default function VersionHistory({ segment }: VersionHistoryProps) {
  if (!segment) {
    return <div className="panel-empty">请在左侧或中栏点击任意句段以查看多版本对照 (AI Draft / Reviewed / Final)</div>
  }

  // Segment might or might not have versions object depending on backend schema
  const versions = (segment as any).versions || {}
  const aiDraft = versions.ai_draft || segment.translation || '（暂无初稿）'
  const reviewed = versions.reviewed || segment.translation || '（暂无审校版）'
  const final = versions.final || segment.translation || '（暂无定稿）'

  return (
    <div className="version-history" aria-label="版本历史对照">
      <div className="version-header-info">
        <h3>句段 #{segment.idx + 1} 版本演进对照</h3>
        <p className="version-source-text">原文：{segment.source}</p>
      </div>

      <div className="version-columns">
        <div className="version-card">
          <div className="version-badge ai">AI Draft</div>
          <div className="version-content">{aiDraft}</div>
        </div>

        <div className="version-card">
          <div className="version-badge reviewed">Reviewed</div>
          <div className="version-content">{reviewed}</div>
        </div>

        <div className="version-card">
          <div className="version-badge final">Final</div>
          <div className="version-content">{final}</div>
        </div>
      </div>
    </div>
  )
}
