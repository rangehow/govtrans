import { useState } from 'react'
import ActivityFeed from './ActivityFeed'
import IssuePanel from './IssuePanel'
import VersionHistory from './VersionHistory'
import type { IntelligencePanelProps } from './types'
import type { Issue, Segment, RunEvent } from '../types'

export default function IntelligencePanel({
  run,
  events,
  selectedSegmentId,
}: IntelligencePanelProps) {
  const [activeTab, setActiveTab] = useState<'activity' | 'refs' | 'qa' | 'history'>('activity')

  // Find selected segment object
  const selectedSegment = run.segments.find((s: Segment) => s.id === selectedSegmentId) || null

  // Filter events for Activity tab:
  const filteredEvents = events.filter((ev: RunEvent) => {
    if (!selectedSegmentId) return true
    if (!ev.segment_ids || ev.segment_ids.length === 0) return true
    return ev.segment_ids.includes(selectedSegmentId)
  })

  // Filter evidence for References tab:
  const relevantEventsForRefs = selectedSegmentId
    ? events.filter((ev: RunEvent) => ev.segment_ids && ev.segment_ids.includes(selectedSegmentId))
    : events

  const allEvidence: Array<{
    title?: string
    url?: string
    snippet?: string
    authority?: string
    eventTitle?: string
  }> = []

  for (const ev of relevantEventsForRefs) {
    if (Array.isArray(ev.evidence)) {
      for (const item of ev.evidence) {
        allEvidence.push({
          title: typeof item.title === 'string' ? item.title : undefined,
          url: typeof item.url === 'string' ? item.url : undefined,
          snippet: typeof item.snippet === 'string' ? item.snippet : (typeof item.content === 'string' ? item.content : undefined),
          authority: typeof item.authority === 'string' ? item.authority : undefined,
          eventTitle: ev.title,
        })
      }
    }
  }

  // Filter issues for QA tab:
  const filteredIssues = run.issues.filter((issue: Issue) => {
    if (!selectedSegmentId) return true
    if (!issue.segment_id) return true
    return issue.segment_id === selectedSegmentId
  })

  return (
    <div className="intelligence-panel" aria-label="右栏情报与 QA 面板">
      <div role="tablist" className="intel-tabs" aria-label="情报面板选项卡">
        <button
          role="tab"
          aria-selected={activeTab === 'activity'}
          className={`intel-tab ${activeTab === 'activity' ? 'active' : ''}`}
          onClick={() => setActiveTab('activity')}
        >
          Activity ({filteredEvents.length})
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'refs'}
          className={`intel-tab ${activeTab === 'refs' ? 'active' : ''}`}
          onClick={() => setActiveTab('refs')}
        >
          References ({allEvidence.length})
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'qa'}
          className={`intel-tab ${activeTab === 'qa' ? 'active' : ''}`}
          onClick={() => setActiveTab('qa')}
        >
          QA ({filteredIssues.filter((i: Issue) => i.status === 'open').length})
        </button>
        <button
          role="tab"
          aria-selected={activeTab === 'history'}
          className={`intel-tab ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          History
        </button>
      </div>

      {selectedSegmentId && selectedSegment && (
        <div className="intel-scope-notice">
          正在聚焦句段 #{selectedSegment.idx + 1}
        </div>
      )}

      <div className="intel-tab-content">
        {activeTab === 'activity' && <ActivityFeed events={filteredEvents} />}

        {activeTab === 'refs' && (
          <div className="refs-container" aria-label="参考资料列表">
            {allEvidence.length === 0 ? (
              <div className="panel-empty">暂无相关参考证据 / 术语匹配</div>
            ) : (
              <ul className="refs-list">
                {allEvidence.map((ev, idx) => (
                  <li key={idx} className="ref-item">
                    <div className="ref-title-line">
                      <strong>{ev.title || '官方参考资料'}</strong>
                      {ev.authority && <span className="ref-authority">{ev.authority}</span>}
                    </div>
                    {ev.snippet && <p className="ref-snippet">{ev.snippet}</p>}
                    {ev.url && (
                      <a href={ev.url} target="_blank" rel="noreferrer" className="ref-url">
                        {ev.url}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {activeTab === 'qa' && (
          <div className="qa-container">
            <IssuePanel issues={filteredIssues} />
          </div>
        )}

        {activeTab === 'history' && (
          <div className="history-container">
            <VersionHistory segment={selectedSegment} />
          </div>
        )}
      </div>
    </div>
  )
}
