import { useEffect, useMemo, useState } from 'react'
import { Activity, BookOpen, History, ShieldCheck } from 'lucide-react'
import ActivityFeed from './ActivityFeed'
import IssuePanel from './IssuePanel'
import VersionHistory from './VersionHistory'
import type { IntelligencePanelProps } from './types'

type Tab = 'activity' | 'refs' | 'qa' | 'history'

export default function IntelligencePanel({
  run,
  events,
  selectedSegmentId,
  selectedIssueId,
  editingIssueId,
  onSelectIssue,
  onEditIssue,
  onCancelEditIssue,
  onSaveIssue,
}: IntelligencePanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>('activity')
  const selectedSegment = run.segments.find((segment) => segment.id === selectedSegmentId) ?? null

  useEffect(() => {
    if (run.status === 'QUALITY_GATE_FAILED') setActiveTab('qa')
  }, [run.status])

  useEffect(() => {
    if (selectedIssueId || editingIssueId) setActiveTab('qa')
  }, [editingIssueId, selectedIssueId])

  const filteredEvents = events.filter((event) => {
    if (!selectedSegmentId || event.segment_ids.length === 0) return true
    return event.segment_ids.includes(selectedSegmentId)
  })

  const externalEvidence = useMemo(() => {
    const relevant = selectedSegmentId
      ? events.filter((event) => event.segment_ids.length === 0 || event.segment_ids.includes(selectedSegmentId))
      : events
    const seen = new Set<string>()
    return relevant.flatMap((event) => event.evidence.flatMap((item) => {
      if (typeof item.source === 'string' && typeof item.target === 'string') return []
      return [{
        title: typeof item.title === 'string' ? item.title : '官方参考资料',
        url: typeof item.url === 'string' ? item.url : '',
        snippet: typeof item.snippet === 'string' ? item.snippet
          : typeof item.content === 'string' ? item.content : '',
        authority: typeof item.authority === 'string' ? item.authority : '',
        eventTitle: event.title,
      }]
    })).filter((item) => {
      const key = item.url || `${item.title}:${item.snippet}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [events, selectedSegmentId])

  const knowledge = run.knowledge_usage
  const references = useMemo(() => {
    const rows = selectedSegmentId
      ? knowledge.references_by_segment[selectedSegmentId] || []
      : Object.values(knowledge.references_by_segment).flat()
    const seen = new Set<string>()
    return rows.filter((item) => {
      const key = item.id || `${item.source}:${item.target}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [knowledge.references_by_segment, selectedSegmentId])

  const filteredIssues = run.issues.filter((issue) => {
    if (!selectedSegmentId || !issue.segment_id) return true
    return issue.segment_id === selectedSegmentId
  })
  const openIssueCount = filteredIssues.filter((issue) => issue.status === 'open').length

  const tabs: Array<{ key: Tab; label: string; count?: number; icon: typeof Activity }> = [
    { key: 'activity', label: '进度', count: filteredEvents.length, icon: Activity },
    {
      key: 'refs',
      label: '知识作用',
      count: knowledge.style_skills.length + knowledge.terminology.length
        + references.length + externalEvidence.length,
      icon: BookOpen,
    },
    { key: 'qa', label: '质检', count: openIssueCount, icon: ShieldCheck },
    { key: 'history', label: '版本', icon: History },
  ]

  return (
    <aside className="intelligence-panel" aria-label="质量与依据面板">
      <div className="intel-header">
        <div>
          <span className="section-eyebrow"><ShieldCheck size={14} />质量控制</span>
          <h2>审校与依据</h2>
        </div>
        <button
          type="button"
          className={`quality-orb ${run.quality.gate === 'passed' ? 'approved' : ''}`}
          onClick={() => setActiveTab('qa')}
          title="查看自动质检分的计算方法"
        >
          <strong>{run.quality.gate === 'checking' ? '—' : run.quality.score}</strong>
          <span>{run.quality.gate === 'checking' ? '检查中' : '自动质检分'}</span>
        </button>
      </div>

      <div role="tablist" className="intel-tabs" aria-label="质量面板选项">
        {tabs.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              type="button"
              key={tab.key}
              role="tab"
              aria-selected={activeTab === tab.key}
              className={activeTab === tab.key ? 'active' : ''}
              onClick={() => setActiveTab(tab.key)}
            >
              <Icon size={14} />{tab.label}
              {tab.count !== undefined && <span>{tab.count}</span>}
            </button>
          )
        })}
      </div>

      {selectedSegment && (
        <div className="intel-scope-notice">
          <span>当前聚焦</span>
          <strong>第 {String(selectedSegment.idx + 1).padStart(2, '0')} 段</strong>
          <button type="button" onClick={() => setActiveTab('history')}>查看版本</button>
        </div>
      )}

      <div className="intel-tab-content" role="tabpanel">
        {activeTab === 'activity' && <ActivityFeed events={filteredEvents} />}
        {activeTab === 'refs' && (
          <div className="knowledge-usage">
            <section className="knowledge-usage-block">
              <div className="knowledge-usage-title"><strong>文风 Skills</strong><span>{knowledge.style_skills.length}</span></div>
              <p>以版本化成文规范提供给初译、文风审校、全文一致性审校和自动定稿。</p>
              <ul className="knowledge-chip-list">
                {knowledge.style_skills.map((skill) => (
                  <li key={skill.id}>
                    <strong>{skill.name}</strong>
                    <span>{skill.selection === 'always' ? '基础·始终启用' : skill.selection === 'automatic' ? '文种自动匹配' : '任务手动选择'}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="knowledge-usage-block">
              <div className="knowledge-usage-title"><strong>术语表</strong><span>{knowledge.terminology.length}</span></div>
              <p>“必须采用”是人工或术语库硬约束；其余是系统抽取并核验的文档级译法。</p>
              {knowledge.terminology.length === 0 ? (
                <div className="knowledge-empty">本文尚未形成术语条目。</div>
              ) : (
                <ul className="term-usage-list">
                  {knowledge.terminology.map((term, index) => (
                    <li key={`${term.source}-${index}`}>
                      <div><strong>{term.source}</strong><span>→</span><b>{term.target}</b></div>
                      <em className={term.mandatory ? 'binding' : ''}>{term.mandatory ? '必须采用·硬约束' : '文档术语·全文对齐'}</em>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="knowledge-usage-block">
              <div className="knowledge-usage-title">
                <strong>{selectedSegmentId ? '当前段落官方软参考' : '全文官方软参考'}</strong>
                <span>{references.length}</span>
              </div>
              <p>这些当前语言对的双语句对用于用词和语体参照，不是强制译法，也不表示模型照抄采用。</p>
              {references.length === 0 ? (
                <div className="knowledge-empty">当前范围没有词汇相似的官方句对。</div>
              ) : (
                <ul className="refs-list">
                  {references.map((item) => (
                    <li key={item.id} className="ref-item bilingual-ref">
                      <div className="ref-title-line">
                        <strong>{item.source_document || '可追溯双语文档'}</strong>
                        <span className="ref-authority">{item.kind === 'verified_memory' ? '人工核验' : '官方语料·自动'}</span>
                      </div>
                      <p className="ref-source">{item.source}</p>
                      <p className="ref-target">{item.target}</p>
                      <div className="ref-foot">
                        <span>相关度 {Math.round((item.score || 0) * 100) / 100}{item.alignment_score ? ` · 对齐 ${Math.round(item.alignment_score * 100)}%` : ''}</span>
                        {item.url && <a href={item.url} target="_blank" rel="noreferrer">查看官方原文</a>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {externalEvidence.length > 0 && (
              <section className="knowledge-usage-block">
                <div className="knowledge-usage-title"><strong>官方网页核验</strong><span>{externalEvidence.length}</span></div>
                <ul className="refs-list">
                  {externalEvidence.map((item, index) => (
                    <li key={`${item.url}-${index}`} className="ref-item">
                      <div className="ref-title-line">
                        <strong>{item.title}</strong>
                        {item.authority && <span className="ref-authority">已验证官方域</span>}
                      </div>
                      {item.snippet && <p className="ref-snippet">{item.snippet}</p>}
                      <div className="ref-foot">
                        <span>{item.eventTitle}</span>
                        {item.url && <a href={item.url} target="_blank" rel="noreferrer">查看原文</a>}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
        {activeTab === 'qa' && (
          <IssuePanel
            issues={filteredIssues}
            segments={run.segments}
            quality={run.quality}
            runStatus={run.status}
            pipelineSteps={run.pipeline_steps || []}
            selectedIssueId={selectedIssueId}
            editingIssueId={editingIssueId}
            onSelectIssue={onSelectIssue}
            onEditIssue={onEditIssue}
            onCancelEditIssue={onCancelEditIssue}
            onSaveIssue={onSaveIssue}
          />
        )}
        {activeTab === 'history' && <VersionHistory segment={selectedSegment} />}
      </div>
    </aside>
  )
}
