import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Info,
  LocateFixed,
  Pencil,
  Save,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react'
import type { Issue, PipelineStep, Run, Segment } from '../types'

const ORDER: Issue['severity'][] = ['critical', 'major', 'minor']
const LABEL: Record<Issue['severity'], string> = {
  critical: '严重阻断', major: '重要阻断', minor: '轻微润色建议',
}

interface Props {
  issues: Issue[]
  segments: Segment[]
  quality: Run['quality']
  runStatus: Run['status']
  pipelineSteps: PipelineStep[]
  selectedIssueId: string | null
  editingIssueId: string | null
  onSelectIssue: (issue: Issue) => void
  onEditIssue: (issue: Issue) => void
  onCancelEditIssue: () => void
  onSaveIssue: (issue: Issue, translation: string) => Promise<void>
}

function locateIssueText(text: string, issue: Issue): { start: number; end: number } | null {
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

function suggestionReplacement(issue: Issue): string | null {
  const suggestion = issue.suggested_fix?.trim()
  if (!suggestion) return null
  const prefixes = [
    '按正常句式大小写改为 ', '改用规定译法 ', '统一为 ', '替换为 ',
  ]
  const prefix = prefixes.find((item) => suggestion.startsWith(item))
  const replacement = prefix ? suggestion.slice(prefix.length).trim() : suggestion
  if (!replacement || /^(补译|检查|确认|重写|调整)/.test(replacement)) return null
  return replacement
}

function IssueExcerpt({ text, issue }: { text: string; issue: Issue }) {
  const span = locateIssueText(text, issue)
  if (!span) {
    return (
      <div className="issue-context no-exact-span">
        <span>关联段落</span>
        <p>{text.length > 150 ? `${text.slice(0, 150)}…` : text}</p>
      </div>
    )
  }
  const contextStart = Math.max(0, span.start - 52)
  const contextEnd = Math.min(text.length, span.end + 52)
  return (
    <div className="issue-context">
      <span><LocateFixed size={11} />译文命中</span>
      <p>
        {contextStart > 0 && '…'}{text.slice(contextStart, span.start)}
        <mark>{text.slice(span.start, span.end)}</mark>
        {text.slice(span.end, contextEnd)}{contextEnd < text.length && '…'}
      </p>
    </div>
  )
}

const REVIEWER_STAGE: Record<string, string> = {
  deterministic_qa: 'deterministic_qa',
  term_reviewer: 'term_review',
  semantic_reviewer: 'semantic_review',
  style_reviewer: 'style_review',
  consistency_reviewer: 'consistency_review',
  final_qa: 'final_qa',
  final_release_reviewer: 'final_qa',
}

function reviewerLabel(reviewer: string, pipelineSteps: PipelineStep[]): string {
  const runtime = pipelineSteps.find((step) => step.id === REVIEWER_STAGE[reviewer])
  if (reviewer === 'deterministic_qa' || reviewer === 'final_qa' || reviewer === 'term_reviewer') {
    return '规则检查'
  }
  const model = runtime?.models.join(' + ')
  if (reviewer === 'consistency_reviewer') return model ? `规则 + 模型 · ${model}` : '规则 + 模型'
  return model ? `模型审校 · ${model}` : '模型审校'
}

export default function IssuePanel({
  issues,
  segments,
  quality,
  runStatus,
  pipelineSteps,
  selectedIssueId,
  editingIssueId,
  onSelectIssue,
  onEditIssue,
  onCancelEditIssue,
  onSaveIssue,
}: Props) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [savedNotice, setSavedNotice] = useState<string | null>(null)
  const editorRef = useRef<HTMLTextAreaElement | null>(null)
  const open = issues.filter((issue) => issue.status === 'open')
  const terminal = ['COMPLETED', 'QUALITY_GATE_FAILED', 'FAILED', 'CANCELLED', 'WAITING_HUMAN_REVIEW'].includes(runStatus)
  const segmentsById = useMemo(
    () => new Map(segments.map((segment) => [segment.id, segment])),
    [segments],
  )
  const editingIssue = open.find((issue) => issue.id === editingIssueId) ?? null

  useEffect(() => {
    if (!editingIssue?.segment_id) return
    const translation = segmentsById.get(editingIssue.segment_id)?.translation || ''
    setDraft(translation)
    setEditError(null)
    window.setTimeout(() => {
      const editor = editorRef.current
      if (!editor) return
      editor.focus()
      const span = locateIssueText(translation, editingIssue)
      if (span) {
        editor.setSelectionRange(span.start, span.end)
        editor.scrollTop = Math.max(
          0,
          (span.start / Math.max(translation.length, 1)) * editor.scrollHeight
            - editor.clientHeight / 3,
        )
      }
      editor.closest('.issue-editor')?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    }, 0)
  }, [editingIssue, segmentsById])

  const save = async (issue: Issue) => {
    if (!draft.trim()) {
      setEditError('译文不能为空')
      return
    }
    setSaving(true)
    setEditError(null)
    try {
      await onSaveIssue(issue, draft)
      setSavedNotice('修改已保存，对应质检项已解决。')
      window.setTimeout(() => setSavedNotice(null), 2600)
    } catch (caught) {
      setEditError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSaving(false)
    }
  }

  const applySuggestion = (issue: Issue) => {
    const replacement = suggestionReplacement(issue)
    const span = locateIssueText(draft, issue)
    if (!replacement || !span) return
    const updated = `${draft.slice(0, span.start)}${replacement}${draft.slice(span.end)}`
    setDraft(updated)
    window.setTimeout(() => {
      editorRef.current?.focus()
      editorRef.current?.setSelectionRange(span.start, span.start + replacement.length)
    }, 0)
  }

  return (
    <div className="issue-panel" aria-label="自动质检结果">
      <section className={`quality-explanation gate-${quality.gate}`}>
        <div className="quality-explanation-head">
          <span className="quality-gate-icon" aria-hidden="true">
            {quality.gate === 'passed' ? <CheckCircle2 size={17} /> : <ShieldAlert size={17} />}
          </span>
          <div><strong>{quality.label}</strong><span>{terminal ? '全文本次检查结果' : '全文分数会随审校实时变化'}</span></div>
          <b>{terminal ? quality.score : '—'}<small>/100</small></b>
        </div>

        {quality.gate === 'passed' && quality.advisory > 0 && (
          <p className="quality-decision-copy">
            严重和重要问题已清零，因此可交付。仍有 {quality.advisory} 条主观性较强的润色建议；可直接定位并修改，不会触发无休止的自动重写。
          </p>
        )}
        {quality.gate === 'needs_optimization' && (
          <p className="quality-decision-copy">
            系统已自动修订 {quality.revision_rounds} 轮，仍有 {quality.blocking} 个会影响发布的问题。你可以逐条修改后再继续终审。
          </p>
        )}

        <div className="quality-deductions" aria-label="质检扣分明细">
          <span className={quality.open.critical ? 'has-items' : ''}>严重 {quality.open.critical} · -{quality.deductions.critical}</span>
          <span className={quality.open.major ? 'has-items' : ''}>重要 {quality.open.major} · -{quality.deductions.major}</span>
          <span className={quality.open.minor ? 'has-items' : ''}>建议 {quality.open.minor} · -{quality.deductions.minor}</span>
        </div>

        <details className="score-method">
          <summary><Info size={13} />这个分数是什么，为什么可能不到 100？</summary>
          <p>{quality.score_basis}</p>
          <p>{quality.release_rule}</p>
        </details>
      </section>

      {savedNotice && <div className="issue-save-notice" role="status"><CheckCircle2 size={14} />{savedNotice}</div>}

      {open.length === 0 ? (
        <div className="panel-empty compact">当前范围未检出待处理项</div>
      ) : (
        <>
          <div className="issue-interaction-hint">
            <LocateFixed size={13} />单击定位原文，双击直接修改译文
          </div>
          {ORDER.map((severity) => {
            const group = open.filter((issue) => issue.severity === severity)
            if (group.length === 0) return null
            return (
              <section key={severity} className={`issue-group ${severity}`}>
                <h4>
                  {LABEL[severity]} <span className="issue-count">{group.length}</span>
                </h4>
                <ul>
                  {group.map((issue) => {
                    const segment = issue.segment_id ? segmentsById.get(issue.segment_id) : null
                    const isEditing = issue.id === editingIssueId
                    const isSelected = issue.id === selectedIssueId
                    const replacement = suggestionReplacement(issue)
                    const canEdit = terminal && Boolean(segment?.translation)
                    return (
                      <li key={issue.id} className={`issue-card ${isSelected ? 'selected' : ''} ${isEditing ? 'editing' : ''}`}>
                        <div
                          className="issue-card-hitbox"
                          role="button"
                          tabIndex={0}
                          aria-label={`${LABEL[issue.severity]}：${issue.message}。双击修改`}
                          onClick={() => onSelectIssue(issue)}
                          onDoubleClick={() => { if (canEdit) onEditIssue(issue) }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') onSelectIssue(issue)
                            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && canEdit) {
                              event.preventDefault()
                              onEditIssue(issue)
                            }
                          }}
                        >
                          <div className="issue-card-meta">
                            <span className="issue-category">{issue.category}</span>
                            <span title={`检查来源：${reviewerLabel(issue.reviewer, pipelineSteps)}`}>
                              {reviewerLabel(issue.reviewer, pipelineSteps)}
                            </span>
                          </div>
                          <p className="issue-message">{issue.message}</p>
                          {segment?.translation && <IssueExcerpt text={segment.translation} issue={issue} />}
                          {issue.suggested_fix && (
                            <div className="issue-fix"><span>建议修改</span><strong>{issue.suggested_fix}</strong></div>
                          )}
                        </div>

                        {!isEditing && (
                          <button
                            type="button"
                            className="issue-edit-button"
                            disabled={!canEdit}
                            onClick={() => onEditIssue(issue)}
                            title={canEdit ? '编辑译文（也可双击问题卡）' : '自动处理结束后可手动修改'}
                          >
                            <Pencil size={12} />修改
                          </button>
                        )}

                        {isEditing && segment && (
                          <div className="issue-editor" onClick={(event) => event.stopPropagation()}>
                            <div className="issue-editor-head">
                              <div><strong>直接修改第 {segment.idx + 1} 段译文</strong><span>保存后自动解决本条质检项</span></div>
                              <button type="button" onClick={onCancelEditIssue} aria-label="取消编辑"><X size={14} /></button>
                            </div>
                            <textarea
                              ref={editorRef}
                              value={draft}
                              onChange={(event) => setDraft(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === 'Escape') onCancelEditIssue()
                                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                                  event.preventDefault()
                                  void save(issue)
                                }
                              }}
                              aria-label="修改译文"
                            />
                            {editError && <div className="issue-edit-error" role="alert">{editError}</div>}
                            <div className="issue-editor-actions">
                              {replacement && locateIssueText(draft, issue) && (
                                <button type="button" className="suggestion-button" onClick={() => applySuggestion(issue)}>
                                  <Sparkles size={13} />套用建议
                                </button>
                              )}
                              <span>Esc 取消 · {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'} + Enter 保存</span>
                              <button type="button" className="save-edit-button" disabled={saving || !draft.trim()} onClick={() => void save(issue)}>
                                <Save size={13} />{saving ? '保存中…' : '保存并解决'}
                              </button>
                            </div>
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )
          })}
        </>
      )}
    </div>
  )
}
