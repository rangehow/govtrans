import { useEffect, useRef } from 'react'
import type { TranslationColumnProps } from './types'

const STATUS_LABEL: Record<string, string> = {
  pending: '待翻译',
  translated: '已翻译',
  reviewed: '已审校',
  final: '已定稿',
}

export default function TranslationColumn({
  segments,
  selectedSegmentId,
  onSelectSegment,
}: TranslationColumnProps) {
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({})

  useEffect(() => {
    if (selectedSegmentId && rowRefs.current[selectedSegmentId]) {
      rowRefs.current[selectedSegmentId]?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    }
  }, [selectedSegmentId])

  if (segments.length === 0) {
    return <div className="panel-empty">暂无译文句段</div>
  }

  return (
    <div className="col-card translation-column" aria-label="译文中栏">
      <div className="col-card-header">
        <h2>译文 (Translation)</h2>
        <span className="col-count">双向对照</span>
      </div>
      <div className="segment-list">
        {segments.map((seg) => {
          const isSelected = seg.id === selectedSegmentId
          return (
            <div
              key={seg.id}
              ref={(el) => {
                rowRefs.current[seg.id] = el
              }}
              tabIndex={0}
              role="button"
              aria-pressed={isSelected}
              className={`segment-item ${isSelected ? 'selected' : ''}`}
              onClick={() => onSelectSegment(seg.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelectSegment(seg.id)
                }
              }}
            >
              <div className="segment-meta-line">
                <span className="seg-idx">#{seg.idx + 1}</span>
                <span className={`segment-status-badge ${seg.status}`}>
                  {STATUS_LABEL[seg.status] || seg.status}
                </span>
              </div>
              <div className="segment-text">
                {seg.translation ?? <em className="segment-waiting">等待翻译引擎生成…</em>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
