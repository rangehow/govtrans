import { useEffect, useMemo, useState } from 'react'
import {
  Check,
  Clipboard,
  Cpu,
  Download,
  FileText,
  Hourglass,
  LoaderCircle,
  Plus,
  Radio,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Square,
} from 'lucide-react'
import { exportRunUrl } from '../api'
import { DOCUMENT_TYPE_LABEL, isTerminal, STATUS_LABEL } from '../status'
import BilingualDocument from './BilingualDocument'
import IntelligencePanel from './IntelligencePanel'
import RunTimeline, { deriveStageStates, STAGES } from './RunTimeline'
import type { WorkspaceProps } from './types'
import type { Issue } from '../types'

const CONFIDENTIALITY_LABEL = {
  PUBLIC: '公开',
  INTERNAL: '内部',
  CONFIDENTIAL: '机密',
}

const CONNECTION_LABEL = {
  idle: '已同步',
  connecting: '正在连接',
  live: '实时同步',
  reconnecting: '正在重连',
}

const STYLE_LABEL: Record<string, string> = {
  'scio-white-paper-distilled': '国新办白皮书文风',
  'gov-white-paper': '通用白皮书文风',
  'gov-policy-document': '政策文件文风',
  'gov-leader-speech': '领导人讲话文风',
  'gov-press-conference': '新闻发布会文风',
}

const STAGE_COPY: Record<string, { title: string; detail: string }> = {
  parse: { title: '正在整理文档结构', detail: '识别标题、段落和列表，过长段落才会拆分。' },
  analyze: { title: '正在理解全文', detail: '建立文种、实体、指代和篇章衔接台账。' },
  terminology: { title: '正在核对术语', detail: '区分必须采用的译法与可参考的建议译法。' },
  retrieve: { title: '正在准备官方参考', detail: '检索相关双语句对，作为用词和语体的软参考。' },
  plan: { title: '正在统一翻译策略', detail: '固定全文术语、实体和上下文约束。' },
  translate: { title: '正在连贯翻译', detail: '按连续章节联合推理，已完成的译法会传给后文。' },
  deterministic_qa: { title: '正在核验数字与完整性', detail: '自动对齐数字、日期、标点和强制术语。' },
  term_review: { title: '正在审校术语', detail: '确认全文必选译法和名称一致。' },
  semantic_review: { title: '正在审校语义', detail: '独立检查遗漏、添加、逻辑关系和政策含义。' },
  style_review: { title: '正在审校文风', detail: '核对目标语言的政务语域、句法和成文规范。' },
  consistency_review: { title: '正在检查全文一致性', detail: '联合检查实体、缩写、指代、时态和列表平行性。' },
  finalize: { title: '正在自动修订', detail: '根据审校结果定向修复阻断项，同时保留版本记录。' },
  final_qa: { title: '正在做交付前终审', detail: '若仍有严重或重要问题，系统会自动回到修订。' },
  complete: { title: '正在归档成稿', detail: '生成可阅读、可导出的最终版本。' },
}

function activityAge(value: string | undefined, now: number): string {
  if (!value) return '等待首个进度事件'
  const seconds = Math.max(0, Math.floor((now - new Date(value).getTime()) / 1000))
  if (seconds < 5) return '刚刚确认仍在运行'
  if (seconds < 30) return `${seconds} 秒前确认运行`
  if (seconds < 60) return `响应较慢 · ${seconds} 秒无新事件`
  return `正在自动核对 · ${Math.floor(seconds / 60)} 分钟无新事件`
}

export default function Workspace({
  run,
  events,
  selectedSegmentId,
  connection,
  continuing,
  onSelectSegment,
  onCancelRun,
  onContinueRun,
  onUpdateSegment,
  onNewRun,
}: WorkspaceProps) {
  const [copied, setCopied] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null)
  const [editingIssueId, setEditingIssueId] = useState<string | null>(null)
  const running = !isTerminal(run.status)
  const finalCount = run.segments.filter((segment) => segment.status === 'final').length
  const translatedCount = run.segments.filter((segment) => segment.status !== 'pending').length
  const missingCount = run.segments.length - translatedCount
  const openIssues = Object.values(run.quality.open).reduce((sum, count) => sum + count, 0)
  const sourceTitle = run.segments[0]?.source.trim()
    || run.summary
    || '政务翻译任务'
  const latestEvent = events[events.length - 1]
  const latestWait = [...events].reverse().find((event) => event.type === 'run.resource_wait')
  const waitAttempt = Number(latestWait?.metrics.attempt || 0)
  const waitMax = Number(latestWait?.metrics.max_attempts || 0)
  const activeCopy = STAGE_COPY[run.current_stage || ''] || {
    title: latestEvent?.title || '正在处理任务',
    detail: '任务进度已保存，页面可安全刷新或离开后再回来。',
  }
  const activeRuntime = run.pipeline_steps?.find((step) => step.id === run.current_stage)
  const activeRuntimeLabel = activeRuntime?.models.length
    ? activeRuntime.models.join(' + ')
    : activeRuntime?.engine
  const timelineStates = deriveStageStates(events, run.current_stage, run.status)
  const activeStageLabels = STAGES
    .filter((stage) => timelineStates[stage.key] === 'active')
    .map((stage) => stage.label)
  const activeSignalTitle = activeStageLabels.length > 1
    ? `${activeStageLabels.length} 项并行处理中`
    : latestEvent?.metrics.heartbeat === true
      ? latestEvent.title
      : activeCopy.title

  useEffect(() => {
    if (!running) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  useEffect(() => {
    if (
      selectedIssueId
      && !run.issues.some((issue) => issue.id === selectedIssueId && issue.status === 'open')
    ) {
      setSelectedIssueId(null)
    }
  }, [run.issues, selectedIssueId])

  const selectIssue = (issue: Issue) => {
    setSelectedIssueId(issue.id)
    if (issue.segment_id) onSelectSegment(issue.segment_id)
  }

  const editIssue = (issue: Issue) => {
    selectIssue(issue)
    setEditingIssueId(issue.id)
  }

  const saveIssue = async (issue: Issue, translation: string) => {
    if (!issue.segment_id) throw new Error('该质检项没有关联段落，无法就地修改')
    await onUpdateSegment(run.run_id, issue.segment_id, translation, issue.id)
    setEditingIssueId(null)
    setSelectedIssueId(null)
  }

  const activitySummary = useMemo(() => {
    if (run.status === 'WAITING_RESOURCES') return latestWait?.summary || activeCopy.detail
    if (latestEvent?.status === 'progress' && latestEvent.summary) return latestEvent.summary
    return activeCopy.detail
  }, [activeCopy.detail, latestEvent, latestWait?.summary, run.status])

  const copyTranslation = async () => {
    const text = run.segments.map((segment) => segment.translation ?? '').join('\n\n')
    await navigator.clipboard.writeText(text)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  const coverageCount = running ? translatedCount : finalCount
  const qaValue = running
    ? '检查中'
    : run.quality.gate === 'passed' ? '已通过' : run.quality.blocking > 0 ? `${run.quality.blocking} 项` : '未完成'

  return (
    <div className="workspace-container">
      <section className={`run-overview overview-${run.status}`} aria-label="翻译任务状态">
        <div className="overview-main">
          <div className={`status-emblem status-emblem-${run.status}`} aria-hidden="true">
            {run.status === 'COMPLETED' ? <ShieldCheck size={24} /> : ['QUALITY_GATE_FAILED', 'FAILED'].includes(run.status)
              ? <ShieldAlert size={24} /> : <FileText size={24} />}
          </div>
          <div className="overview-copy">
            <div className="overview-title-line">
              <h2 title={sourceTitle}>{sourceTitle}</h2>
              <span className={`status-pill status-${run.status}`}>{STATUS_LABEL[run.status]}</span>
            </div>
            <div className="run-metadata">
              <span>{DOCUMENT_TYPE_LABEL[run.document_type || ''] || run.document_type || '自动识别文种'}</span>
              <span>{CONFIDENTIALITY_LABEL[run.confidentiality]}</span>
              <span>{run.language_pair.source.name_zh} → {run.language_pair.target.name_zh}</span>
              <span>{run.segments.length} 个段落单元</span>
              <span>{run.translation_mode === 'coherent' ? '全文连贯' : '均衡提速'}</span>
              {run.style_skills.length > 0 && <span>{run.style_skills.map((id) => STYLE_LABEL[id] || id).join(' + ')}</span>}
              <span className={`connection-state connection-${connection}`}>
                <Radio size={12} />{CONNECTION_LABEL[connection]}
              </span>
              <span className="run-id">ID {run.run_id.slice(0, 8)}</span>
            </div>
          </div>
        </div>

        <div className="overview-actions">
          {run.status === 'COMPLETED' && (
            <>
              <button type="button" className="secondary-button" onClick={() => void copyTranslation()}>
                {copied ? <Check size={15} /> : <Clipboard size={15} />}{copied ? '已复制' : '复制译文'}
              </button>
              <a className="secondary-button action-link" href={exportRunUrl(run.run_id, 'docx_bilingual')}>
                <Download size={15} />双语对照
              </a>
              <a className="primary action-link" href={exportRunUrl(run.run_id, 'docx')}>
                <Download size={15} />导出成稿 Word
              </a>
            </>
          )}
          {running && (
            <button type="button" className="danger-quiet-button" onClick={() => onCancelRun(run.run_id)}>
              <Square size={12} />取消任务
            </button>
          )}
          {!running && !['QUALITY_GATE_FAILED', 'FAILED'].includes(run.status) && (
            <button type="button" className="secondary-button" onClick={onNewRun}>
              <Plus size={15} />新建翻译
            </button>
          )}
        </div>

        <div className="overview-progress">
          <div className="progress-track" role="progressbar" aria-valuenow={Math.round(run.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${Math.max(2, run.progress * 100)}%` }} />
          </div>
          <span>{Math.round(run.progress * 100)}%</span>
        </div>

        {running && (
          <div className="live-work-signal" role="status" aria-live="polite">
            <span className="work-pulse" aria-hidden="true"><i /></span>
            <div className="work-signal-copy">
              <strong>{run.status === 'WAITING_RESOURCES' ? '已保存，等待资源后自动续跑' : activeSignalTitle}</strong>
              <span>{activitySummary}</span>
              {activeRuntimeLabel && <em><Cpu size={11} />{activeRuntimeLabel}</em>}
            </div>
            <div className="work-signal-meta">
              <strong>{translatedCount}/{run.segments.length}</strong>
              <span>{activityAge(latestEvent?.created_at || run.updated_at, now)}</span>
            </div>
          </div>
        )}

        <div className="run-stat-strip">
          <div><strong>{coverageCount}/{run.segments.length}</strong><span>{running ? '已有译文' : '定稿段落'}</span></div>
          <div><strong>{qaValue}</strong><span>交付检查</span></div>
          <div><strong>{run.quality.blocking}</strong><span>发布阻断</span></div>
          <div><strong>{run.quality.revision_rounds}</strong><span>自动修订轮次</span></div>
        </div>

        {run.status === 'WAITING_RESOURCES' && (
          <div role="status" className="resource-wait">
            <Hourglass size={16} />共享推理资源繁忙，任务已安全保存。
            {waitAttempt > 0 && waitMax > 0 ? ` 正在进行第 ${waitAttempt}/${waitMax} 轮自动重试；不会无限卡住。` : ' 系统将在有界时间内自动重试。'}
          </div>
        )}

        {run.status === 'QUALITY_GATE_FAILED' && (
          <div className="quality-stop-card" role="alert">
            <ShieldAlert size={19} />
            <div>
              <strong>{missingCount > 0
                ? `已有译文已保存，仍有 ${missingCount} 个段落待补译、${run.quality.blocking} 个发布阻断项`
                : `全部段落译文已保存，但还有 ${run.quality.blocking} 个发布阻断项`}</strong>
              <span>系统已自动修订 {run.quality.revision_rounds} 轮。为避免无限重写，本轮暂停；可以保留现有译文和审校依据继续定向优化。</span>
            </div>
            <button type="button" className="primary" disabled={continuing} onClick={() => onContinueRun(run.run_id)}>
              {continuing ? <LoaderCircle className="spinning" size={15} /> : <RefreshCw size={15} />}
              {continuing ? '正在续跑…' : '继续自动优化'}
            </button>
          </div>
        )}

        {run.status === 'FAILED' && (
          <div className="quality-stop-card" role="alert">
            <ShieldAlert size={19} />
            <div>
              <strong>任务在“{activeCopy.title}”中断，已完成的译文和依据均已保存</strong>
              <span>可以直接从中断步骤重试；系统不会重复已经完成的阶段，也不会清空当前译文。</span>
            </div>
            <button type="button" className="primary" disabled={continuing} onClick={() => onContinueRun(run.run_id)}>
              {continuing ? <LoaderCircle className="spinning" size={15} /> : <RefreshCw size={15} />}
              {continuing ? '正在重试…' : '从中断处重试'}
            </button>
          </div>
        )}

        {run.error && !['QUALITY_GATE_FAILED', 'FAILED'].includes(run.status) && (
          <div role="alert" className="run-error"><ShieldAlert size={16} />{run.error}</div>
        )}
        {openIssues > 0 && run.status === 'COMPLETED' && run.quality.blocking === 0 && (
          <div className="advisory-note">已通过交付检查；仍保留 {run.quality.advisory} 条非阻断润色建议，可在右侧“质检”中查看评分依据。</div>
        )}
      </section>

      <RunTimeline
        events={events}
        runStatus={run.status}
        currentStage={run.current_stage}
        pipelineSteps={run.pipeline_steps || []}
      />

      <div className="workspace-grid">
        <BilingualDocument
          segments={run.segments}
          issues={run.issues}
          runStatus={run.status}
          selectedSegmentId={selectedSegmentId}
          selectedIssueId={selectedIssueId}
          onSelectSegment={onSelectSegment}
          onSelectIssue={selectIssue}
          onEditIssue={editIssue}
          sourceLanguage={run.language_pair.source}
          targetLanguage={run.language_pair.target}
        />
        <IntelligencePanel
          run={run}
          events={events}
          selectedSegmentId={selectedSegmentId}
          selectedIssueId={selectedIssueId}
          editingIssueId={editingIssueId}
          onSelectIssue={selectIssue}
          onEditIssue={editIssue}
          onCancelEditIssue={() => setEditingIssueId(null)}
          onSaveIssue={saveIssue}
        />
      </div>
    </div>
  )
}
