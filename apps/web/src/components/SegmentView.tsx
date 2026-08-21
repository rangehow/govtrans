import type { Segment } from '../types'

const STATUS_LABEL: Record<Segment['status'], string> = {
  pending: '待翻译', translated: '已翻译', reviewed: '已审校', final: '已定稿',
}

export default function SegmentView({ segments }: { segments: Segment[] }) {
  if (segments.length === 0) {
    return <div className="panel-empty">暂无句段</div>
  }
  return (
    <div className="segment-grid" aria-label="原文译文对照">
      <div className="segment-row segment-head">
        <span>原文</span><span>译文</span>
      </div>
      {segments.map((seg) => (
        <div key={seg.id} className={`segment-row seg-${seg.status}`}>
          <span className="segment-source">{seg.source}</span>
          <span className="segment-target">
            {seg.translation ?? <em className="segment-waiting">…</em>}
            <small className="segment-status">{STATUS_LABEL[seg.status]}</small>
          </span>
        </div>
      ))}
    </div>
  )
}
