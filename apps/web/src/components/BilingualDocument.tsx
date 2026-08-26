import { Fragment, useEffect, useRef, useState } from 'react'
import { AlertCircle, BookOpenText, CheckCircle2, Columns2, Languages } from 'lucide-react'
import type { Issue, LanguageSpec, RunStatus, Segment } from '../types'

interface Props {
  segments: Segment[]
  issues: Issue[]
  runStatus: RunStatus
  selectedSegmentId: string | null
  selectedIssueId: string | null
  onSelectSegment: (id: string) => void
  onSelectIssue: (issue: Issue) => void
  onEditIssue: (issue: Issue) => void
  sourceLanguage: LanguageSpec
  targetLanguage: LanguageSpec
}

const SEGMENT_STATUS: Record<Segment['status'], string> = {
  pending: '等待翻译',
  translated: '初译完成',
  reviewed: '已审校',
  final: '已定稿',
}

function pendingCopy(status: RunStatus): string {
  if (status === 'PARSING' || status === 'ANALYZING' || status === 'RESEARCHING') {
    return '正在理解全文，译文将按连续章节出现…'
  }
  if (status === 'WAITING_RESOURCES') return '已安全排队，资源可用后自动续译…'
  return '已进入连贯翻译队列…'
}

function issueSpan(text: string, issue: Issue): { start: number; end: number } | null {
  const quoted = [...issue.message.matchAll(/[“”"'‘’]([^“”"'‘’]{2,})[“”"'‘’]/g)]
    .map((match) => match[1])
  const candidates = [issue.target_span || '', ...quoted]
    .map((candidate) => candidate.trim())
    .filter(Boolean)
  const folded = text.toLocaleLowerCase()
  for (const candidate of candidates) {
    const start = folded.indexOf(candidate.toLocaleLowerCase())
    if (start >= 0) return { start, end: start + candidate.length }
  }
  return null
}

function HighlightedTranslation({
  text,
  issues,
  selectedIssueId,
  onSelectIssue,
  onEditIssue,
}: {
  text: string
  issues: Issue[]
  selectedIssueId: string | null
  onSelectIssue: (issue: Issue) => void
  onEditIssue: (issue: Issue) => void
}) {
  const spans = issues
    .map((issue) => ({ issue, span: issueSpan(text, issue) }))
    .filter((item): item is { issue: Issue; span: { start: number; end: number } } => Boolean(item.span))
    .sort((left, right) => (
      left.span.start - right.span.start
      || Number(right.issue.id === selectedIssueId) - Number(left.issue.id === selectedIssueId)
      || (right.span.end - right.span.start) - (left.span.end - left.span.start)
    ))

  const nonOverlapping: typeof spans = []
  for (const item of spans) {
    const previous = nonOverlapping[nonOverlapping.length - 1]
    if (!previous || item.span.start >= previous.span.end) nonOverlapping.push(item)
  }
  if (nonOverlapping.length === 0) return <>{text}</>

  let cursor = 0
  return <>{nonOverlapping.map(({ issue, span }) => {
    const before = text.slice(cursor, span.start)
    const matched = text.slice(span.start, span.end)
    cursor = span.end
    return (
      <Fragment key={`${issue.id}-${span.start}`}>
        {before}
        <mark
          className={`issue-highlight ${issue.severity} ${issue.id === selectedIssueId ? 'active' : ''}`}
          role="button"
          tabIndex={0}
          title="质检命中位置；双击直接修改"
          onClick={(event) => {
            event.stopPropagation()
            onSelectIssue(issue)
          }}
          onDoubleClick={(event) => {
            event.stopPropagation()
            onEditIssue(issue)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              onSelectIssue(issue)
            }
          }}
        >{matched}</mark>
      </Fragment>
    )
  })}{text.slice(cursor)}</>
}

export default function BilingualDocument({
  segments,
  issues,
  runStatus,
  selectedSegmentId,
  selectedIssueId,
  onSelectSegment,
  onSelectIssue,
  onEditIssue,
  sourceLanguage,
  targetLanguage,
}: Props) {
  const [view, setView] = useState<'reading' | 'bilingual'>('reading')
  const segmentRefs = useRef(new Map<string, HTMLElement>())
  const openBySegment = new Map<string, Issue[]>()
  for (const issue of issues) {
    if (!issue.segment_id || issue.status !== 'open') continue
    openBySegment.set(issue.segment_id, [...(openBySegment.get(issue.segment_id) ?? []), issue])
  }

  useEffect(() => {
    if (!selectedSegmentId) return
    segmentRefs.current.get(selectedSegmentId)?.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
    })
  }, [selectedSegmentId, view])

  const renderFoot = (segment: Segment, openIssues: Issue[]) => {
    const hasBlocker = openIssues.some((issue) => issue.severity !== 'minor')
    return (
      <div className="segment-foot">
        <span className={`segment-state state-${segment.status}`}>
          {segment.status === 'final' && <CheckCircle2 size={12} />}
          {SEGMENT_STATUS[segment.status]}
        </span>
        {openIssues.length > 0 && (
          <button
            type="button"
            className={`segment-issues ${hasBlocker ? 'blocking' : ''}`}
            onClick={(event) => {
              event.stopPropagation()
              onSelectIssue(openIssues[0])
            }}
            title="查看该段质检问题"
          >
            <AlertCircle size={12} />{hasBlocker ? `${openIssues.length} 项需修复` : `${openIssues.length} 条建议`}
          </button>
        )}
      </div>
    )
  }

  return (
    <section className="document-panel" aria-label="译文文档">
      <div className="document-panel-heading">
        <div>
          <span className="section-eyebrow"><Languages size={14} />文档视图</span>
          <h2>{view === 'reading' ? `${targetLanguage.name_zh}成稿` : '双语对照'}</h2>
        </div>
        <div className="document-heading-actions">
          <span className="segment-total">{segments.length} 个段落</span>
          <div className="document-view-switch" role="tablist" aria-label="文档阅读方式">
            <button type="button" role="tab" aria-selected={view === 'reading'} className={view === 'reading' ? 'active' : ''} onClick={() => setView('reading')}>
              <BookOpenText size={14} />成稿
            </button>
            <button type="button" role="tab" aria-selected={view === 'bilingual'} className={view === 'bilingual' ? 'active' : ''} onClick={() => setView('bilingual')}>
              <Columns2 size={14} />对照
            </button>
          </div>
        </div>
      </div>

      {view === 'reading' ? (
        <div className="reading-document" role="document" aria-label={`${targetLanguage.name_zh}译文`} lang={targetLanguage.bcp47} dir={targetLanguage.rtl ? 'rtl' : 'ltr'}>
          {segments.map((segment) => {
            const selected = segment.id === selectedSegmentId
            const openIssues = openBySegment.get(segment.id) ?? []
            const content = segment.translation
            const issueFocused = openIssues.some((issue) => issue.id === selectedIssueId)
            const className = `reading-block kind-${segment.kind || 'paragraph'} ${selected ? 'selected' : ''} ${issueFocused ? 'issue-focused' : ''}`
            const highlighted = content ? (
              <HighlightedTranslation
                text={content}
                issues={openIssues}
                selectedIssueId={selectedIssueId}
                onSelectIssue={onSelectIssue}
                onEditIssue={onEditIssue}
              />
            ) : null
            return (
              <article
                key={segment.id}
                className={className}
                ref={(node) => {
                  if (node) segmentRefs.current.set(segment.id, node)
                  else segmentRefs.current.delete(segment.id)
                }}
                tabIndex={0}
                aria-label={`第 ${segment.idx + 1} 段，${SEGMENT_STATUS[segment.status]}`}
                onClick={() => onSelectSegment(segment.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelectSegment(segment.id)
                  }
                }}
              >
                <span className="reading-index">{String(segment.idx + 1).padStart(2, '0')}</span>
                {content ? (
                  segment.kind === 'title' ? <h1>{highlighted}</h1>
                    : segment.kind === 'heading' ? <h3>{highlighted}</h3>
                      : <p>{highlighted}</p>
                ) : (
                  <div className="reading-placeholder">
                    <span className="reading-skeleton"><i /><i /><i /></span>
                    <small>{pendingCopy(runStatus)}</small>
                  </div>
                )}
                {(selected || openIssues.length > 0) && renderFoot(segment, openIssues)}
              </article>
            )
          })}
        </div>
      ) : (
        <>
          <div className="bilingual-column-head" aria-hidden="true">
            <span>{sourceLanguage.name_zh}原文</span>
            <span>{targetLanguage.name_zh}译文</span>
          </div>

          <div className="bilingual-rows">
            {segments.map((segment) => {
              const selected = segment.id === selectedSegmentId
              const openIssues = openBySegment.get(segment.id) ?? []
              const issueFocused = openIssues.some((issue) => issue.id === selectedIssueId)
              return (
                <div
                  key={segment.id}
                  className={`bilingual-row ${selected ? 'selected' : ''} ${issueFocused ? 'issue-focused' : ''}`}
                  ref={(node) => {
                    if (node) segmentRefs.current.set(segment.id, node)
                    else segmentRefs.current.delete(segment.id)
                  }}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected}
                  onClick={() => onSelectSegment(segment.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelectSegment(segment.id)
                    }
                  }}
                >
                  <div className="row-index">{String(segment.idx + 1).padStart(2, '0')}</div>
                  <div className="source-cell" data-label={`${sourceLanguage.name_zh}原文`} lang={sourceLanguage.bcp47} dir={sourceLanguage.rtl ? 'rtl' : 'ltr'}><p>{segment.source}</p></div>
                  <div className="translation-cell" data-label={`${targetLanguage.name_zh}译文`} lang={targetLanguage.bcp47} dir={targetLanguage.rtl ? 'rtl' : 'ltr'}>
                    {segment.translation
                      ? <p><HighlightedTranslation
                        text={segment.translation}
                        issues={openIssues}
                        selectedIssueId={selectedIssueId}
                        onSelectIssue={onSelectIssue}
                        onEditIssue={onEditIssue}
                      /></p>
                      : <div className="translation-placeholder"><span />{pendingCopy(runStatus)}</div>}
                    {renderFoot(segment, openIssues)}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
