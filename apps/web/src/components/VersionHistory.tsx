import type { VersionHistoryProps } from './types'

export default function VersionHistory({ segment }: VersionHistoryProps) {
  if (!segment) {
    return <div className="panel-empty">请在文档中点击任意段落，查看初译、审校版与定稿的演进</div>
  }

  const versions = segment.versions || {}
  const aiDraft = versions.ai_draft || segment.translation || '（暂无初稿）'
  const reviewed = versions.reviewed || segment.translation || '（暂无审校版）'
  const final = versions.final || segment.translation || '（暂无定稿）'
  const manual = versions.manual

  return (
    <div className="version-history" aria-label="版本历史对照">
      <div className="version-header-info">
        <h3>第 {String(segment.idx + 1).padStart(2, '0')} 段版本演进</h3>
        <p className="version-source-text">原文：{segment.source}</p>
      </div>

      <div className="version-columns">
        <div className="version-card">
          <div className="version-badge ai">初译</div>
          <div className="version-content">{aiDraft}</div>
        </div>

        <div className="version-card">
          <div className="version-badge reviewed">审校版</div>
          <div className="version-content">{reviewed}</div>
        </div>

        <div className="version-card">
          <div className="version-badge final">定稿</div>
          <div className="version-content">{final}</div>
        </div>

        {manual && (
          <div className="version-card manual-version">
            <div className="version-badge manual">人工修改</div>
            <div className="version-content">{manual}</div>
          </div>
        )}
      </div>
    </div>
  )
}
