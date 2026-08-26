import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpenCheck, Database, Languages, Palette, ShieldCheck } from 'lucide-react'
import {
  cancelRun,
  continueRun,
  createRun,
  getRun,
  getRunEventLog,
  listRuns,
  openEventStream,
  updateSegmentTranslation,
} from './api'
import type { CreateRunOptions } from './api'
import CorpusAdmin from './components/CorpusAdmin'
import HistorySidebar from './components/HistorySidebar'
import TermsAdmin from './components/TermsAdmin'
import StyleSkillsAdmin from './components/StyleSkillsAdmin'
import TranslateInput, {
  type BatchProgress,
  type BatchSource,
} from './components/TranslateInput'
import Workspace from './components/Workspace'
import { isTerminal } from './status'
import type { Confidentiality, Run, RunEvent, RunSummary } from './types'

type ActiveTab = 'translate' | 'style' | 'terms' | 'corpus'
type ConnectionState = 'idle' | 'connecting' | 'live' | 'reconnecting'

const LAST_RUN_KEY = 'govtrans:last-run-id'

function rememberRun(runId: string) {
  try {
    window.localStorage.setItem(LAST_RUN_KEY, runId)
  } catch {
    // Private browsing can disable storage. The URL remains a durable locator.
  }
}

function recalledRun(): string | null {
  try {
    return window.localStorage.getItem(LAST_RUN_KEY)
  } catch {
    return null
  }
}

function updateRunUrl(runId: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('run', runId)
  window.history.replaceState({}, '', url)
}

function mergeRunEvents(previous: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  if (incoming.length === 0) return previous
  const byId = new Map(previous.map((event) => [event.id, event]))
  for (const event of incoming) byId.set(event.id, event)
  return [...byId.values()].sort((left, right) => left.seq - right.seq)
}

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('translate')
  const [run, setRun] = useState<Run | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [events, setEvents] = useState<RunEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [continuing, setContinuing] = useState(false)
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [runLoading, setRunLoading] = useState(false)
  const [composerOpen, setComposerOpen] = useState(true)
  const [presetStyleSkills, setPresetStyleSkills] = useState<string[] | null>(null)
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('idle')

  const lastSeqRef = useRef(0)
  const loadTokenRef = useRef(0)
  const refreshTimerRef = useRef<number | null>(null)
  const restoreGenerationRef = useRef(0)
  const activeRunIdRef = useRef<string | null>(null)
  activeRunIdRef.current = run?.run_id ?? null

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const response = await listRuns(50)
      setRuns(response.runs)
      return response.runs
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const loadRun = useCallback(async (runId: string, persistLocation = true) => {
    const token = ++loadTokenRef.current
    setRunLoading(true)
    setError(null)
    try {
      const [detail, eventLog] = await Promise.all([getRun(runId), getRunEventLog(runId)])
      if (token !== loadTokenRef.current) return detail
      lastSeqRef.current = eventLog.last_cursor
      setEvents(eventLog.events)
      setRun(detail)
      setSelectedSegmentId(detail.segments[0]?.id ?? null)
      setComposerOpen(false)
      if (persistLocation) updateRunUrl(runId)
      rememberRun(runId)
      return detail
    } finally {
      if (token === loadTokenRef.current) setRunLoading(false)
    }
  }, [])

  const refreshRun = useCallback(async (runId: string) => {
    try {
      const cursor = lastSeqRef.current
      const [updated, eventLog] = await Promise.all([
        getRun(runId),
        getRunEventLog(runId, cursor),
      ])
      if (activeRunIdRef.current !== runId) return
      if (eventLog.events.length > 0) {
        lastSeqRef.current = Math.max(lastSeqRef.current, eventLog.last_cursor)
        setEvents((previous) => mergeRunEvents(previous, eventLog.events))
      }
      setRun(updated)
      if (isTerminal(updated.status)) void loadHistory()
    } catch (caught) {
      if (activeRunIdRef.current === runId) {
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    }
  }, [loadHistory])

  useEffect(() => {
    const generation = ++restoreGenerationRef.current
    let disposed = false

    const restore = async () => {
      try {
        const history = await loadHistory()
        if (disposed || generation !== restoreGenerationRef.current) return
        const urlRun = new URL(window.location.href).searchParams.get('run')
        const preferred = urlRun || recalledRun() || history[0]?.run_id
        if (preferred) {
          try {
            await loadRun(preferred, true)
          } catch {
            const fallback = history.find((item) => item.run_id !== preferred)
            if (fallback) await loadRun(fallback.run_id, true)
          }
        }
      } catch (caught) {
        if (!disposed && generation === restoreGenerationRef.current) {
          setError(caught instanceof Error ? caught.message : String(caught))
        }
      } finally {
        if (!disposed && generation === restoreGenerationRef.current) setHistoryLoading(false)
      }
    }
    void restore()
    return () => { disposed = true }
  }, [loadHistory, loadRun])

  // Persisted SSE is the live acceleration path; periodic detail reads are the
  // fallback. A burst of segment events is coalesced into one detail refresh.
  const runIsTerminal = run ? isTerminal(run.status) : true

  useEffect(() => {
    if (!run?.run_id || runIsTerminal) {
      setConnection('idle')
      return
    }
    const runId = run.run_id
    setConnection('connecting')
    const source = openEventStream(runId, lastSeqRef.current)

    const scheduleRefresh = (delay = 180) => {
      if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = window.setTimeout(() => void refreshRun(runId), delay)
    }

    source.onopen = () => {
      setConnection('live')
      void refreshRun(runId)
    }
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as RunEvent
        lastSeqRef.current = Math.max(lastSeqRef.current, event.seq)
        setEvents((previous) => mergeRunEvents(previous, [event]))
        if (event.progress !== null) {
          // The event is already persisted and authoritative. Reflect its
          // percentage immediately instead of waiting for the coalesced detail
          // read, which can be noticeably slower for a large document.
          setRun((previous) => previous?.run_id === runId
            ? { ...previous, progress: Math.max(previous.progress, event.progress ?? 0) }
            : previous)
        }
        scheduleRefresh(event.type.startsWith('run.') ? 0 : 180)
      } catch {
        // A malformed frame should trigger reconciliation, but it does not
        // mean the EventSource transport itself disconnected.
        scheduleRefresh(0)
      }
    }
    source.onerror = () => {
      setConnection('reconnecting')
      scheduleRefresh(0)
    }
    // SSE is the low-latency path; this incremental event-log reconciliation
    // is the correctness path. A proxy that buffers or drops SSE can no longer
    // leave the timeline frozen while the run itself keeps moving.
    const poll = window.setInterval(() => void refreshRun(runId), 2500)
    return () => {
      source.close()
      window.clearInterval(poll)
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = null
      }
    }
  }, [run?.run_id, runIsTerminal, refreshRun])

  const submit = useCallback(async (
    text: string,
    confidentiality: Confidentiality,
    options: CreateRunOptions,
  ) => {
    setError(null)
    setSubmitting(true)
    setBatchProgress(null)
    setSelectedSegmentId(null)
    try {
      const created = await createRun(text, confidentiality, options)
      lastSeqRef.current = 0
      setEvents([])
      await loadRun(created.run_id)
      await loadHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmitting(false)
    }
  }, [loadHistory, loadRun])

  const submitBatch = useCallback(async (
    sources: BatchSource[],
    confidentiality: Confidentiality,
    options: CreateRunOptions,
  ) => {
    if (sources.length === 0) return
    setError(null)
    setSubmitting(true)
    setSelectedSegmentId(null)

    let createdCount = 0
    const createdRunIds: string[] = []
    const failures: Array<{ path: string; message: string }> = []
    setBatchProgress({
      completed: 0,
      total: sources.length,
      current: sources[0].path,
      created: 0,
      failed: 0,
    })

    try {
      // Submission is sequential so large folders cannot create a burst of
      // database writes. Each accepted run starts immediately on the server,
      // where the shared model-client semaphore controls actual inference.
      for (let index = 0; index < sources.length; index += 1) {
        const source = sources[index]
        setBatchProgress({
          completed: index,
          total: sources.length,
          current: source.path,
          created: createdCount,
          failed: failures.length,
        })
        try {
          const created = await createRun(source.text, confidentiality, options)
          createdRunIds.push(created.run_id)
          createdCount += 1
        } catch (caught) {
          failures.push({
            path: source.path,
            message: caught instanceof Error ? caught.message : String(caught),
          })
        }
        setBatchProgress({
          completed: index + 1,
          total: sources.length,
          current: index + 1 < sources.length ? sources[index + 1].path : null,
          created: createdCount,
          failed: failures.length,
        })
      }

      if (createdRunIds.length > 0) {
        lastSeqRef.current = 0
        setEvents([])
        await loadRun(createdRunIds[createdRunIds.length - 1])
        await loadHistory()
      }

      if (failures.length > 0) {
        const examples = failures
          .slice(0, 3)
          .map((item) => `${item.path}：${item.message}`)
          .join('；')
        const remainder = failures.length > 3 ? `；另有 ${failures.length - 3} 个失败` : ''
        setError(`批量任务已创建 ${createdCount}/${sources.length} 个。${examples}${remainder}`)
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmitting(false)
      setBatchProgress(null)
    }
  }, [loadHistory, loadRun])

  const handleCancel = useCallback(async (runId: string) => {
    try {
      await cancelRun(runId)
      await refreshRun(runId)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [refreshRun])

  const handleContinue = useCallback(async (runId: string) => {
    setContinuing(true)
    setError(null)
    try {
      await continueRun(runId)
      await refreshRun(runId)
      await loadHistory()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setContinuing(false)
    }
  }, [loadHistory, refreshRun])

  const handleUpdateSegment = useCallback(async (
    runId: string,
    segmentId: string,
    translation: string,
    issueId?: string,
  ) => {
    setError(null)
    try {
      const updated = await updateSegmentTranslation(
        runId, segmentId, translation, issueId,
      )
      if (activeRunIdRef.current === runId) setRun(updated)
      await refreshRun(runId)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught)
      setError(message)
      throw caught
    }
  }, [refreshRun])

  const handleSelectRun = useCallback(async (runId: string) => {
    setActiveTab('translate')
    try {
      await loadRun(runId)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [loadRun])

  const pageTitle = activeTab === 'translate'
    ? '智能翻译工作台'
    : activeTab === 'style' ? '文风 Skills'
      : activeTab === 'terms' ? '术语知识库' : '官方平行语料'

  return (
    <div className="app-shell">
      <HistorySidebar
        runs={runs}
        activeRunId={run?.run_id ?? null}
        loading={historyLoading}
        onSelectRun={(runId) => void handleSelectRun(runId)}
        onNewRun={() => {
          setPresetStyleSkills(null)
          setActiveTab('translate')
          setComposerOpen(true)
        }}
      />

      <div className="main-shell">
        <header className="topbar">
          <div>
            <span className="topbar-kicker">
              <ShieldCheck size={14} aria-hidden="true" />
              自动质量闭环
            </span>
            <h1>{pageTitle}</h1>
          </div>
          <nav className="app-nav" aria-label="主导航">
            <button type="button" className={activeTab === 'translate' ? 'active' : ''} onClick={() => setActiveTab('translate')}>
              <Languages size={16} />翻译
            </button>
            <button type="button" className={activeTab === 'style' ? 'active' : ''} onClick={() => setActiveTab('style')}>
              <Palette size={16} />文风
            </button>
            <button type="button" className={activeTab === 'terms' ? 'active' : ''} onClick={() => setActiveTab('terms')}>
              <BookOpenCheck size={16} />术语
            </button>
            <button type="button" className={activeTab === 'corpus' ? 'active' : ''} onClick={() => setActiveTab('corpus')}>
              <Database size={16} />语料
            </button>
          </nav>
        </header>

        <main className="app-main">
          {activeTab === 'translate' && (
            <>
              {composerOpen && (
                <TranslateInput
                  busy={submitting}
                  batchProgress={batchProgress}
                  onSubmit={submit}
                  onSubmitBatch={submitBatch}
                  initialStyleSkills={presetStyleSkills}
                  onClose={run ? () => setComposerOpen(false) : undefined}
                />
              )}
              {error && <div role="alert" className="error-banner">{error}</div>}
              {runLoading && !run && <div className="page-loading">正在恢复翻译任务…</div>}
              {run && (
                <Workspace
                  run={run}
                  events={events}
                  selectedSegmentId={selectedSegmentId}
                  connection={connection}
                  continuing={continuing}
                  onSelectSegment={setSelectedSegmentId}
                  onCancelRun={handleCancel}
                  onContinueRun={handleContinue}
                  onUpdateSegment={handleUpdateSegment}
                  onNewRun={() => { setPresetStyleSkills(null); setComposerOpen(true) }}
                />
              )}
              {!run && !composerOpen && !runLoading && (
                <button type="button" className="empty-start" onClick={() => setComposerOpen(true)}>
                  开始第一个翻译任务
                </button>
              )}
            </>
          )}
          {activeTab === 'style' && (
            <StyleSkillsAdmin onUseSkills={(skillIds) => {
              setPresetStyleSkills(skillIds)
              setActiveTab('translate')
              setComposerOpen(true)
            }} />
          )}
          {activeTab === 'terms' && <TermsAdmin />}
          {activeTab === 'corpus' && <CorpusAdmin />}
        </main>
      </div>
    </div>
  )
}
