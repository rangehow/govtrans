import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelRun, createRun, getRun, openEventStream } from './api'
import TranslateInput from './components/TranslateInput'
import Workspace from './components/Workspace'
import TermsAdmin from './components/TermsAdmin'
import CorpusAdmin from './components/CorpusAdmin'
import type { Confidentiality, Run, RunEvent } from './types'

const TERMINAL: Run['status'][] = ['COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_HUMAN_REVIEW']

export default function App() {
  const [activeTab, setActiveTab] = useState<'translate' | 'terms' | 'corpus'>('translate')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)

  const lastSeqRef = useRef(0)
  const runRef = useRef<Run | null>(null)
  runRef.current = run

  const refreshRun = useCallback(async (runId: string) => {
    try {
      const updated = await getRun(runId)
      setRun(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Live SSE stream with 5s polling fallback
  useEffect(() => {
    if (!run?.run_id) return
    const runId = run.run_id
    const source = openEventStream(runId, lastSeqRef.current)
    source.onmessage = (msg) => {
      const ev = JSON.parse(msg.data) as RunEvent
      lastSeqRef.current = Math.max(lastSeqRef.current, ev.seq)
      setEvents((prev) => (prev.some((p) => p.id === ev.id) ? prev : [...prev, ev]))
      void refreshRun(runId)
    }
    source.onerror = () => void refreshRun(runId)
    const poll = window.setInterval(() => {
      const current = runRef.current
      if (current && !TERMINAL.includes(current.status)) void refreshRun(runId)
    }, 5000)
    return () => {
      source.close()
      window.clearInterval(poll)
    }
  }, [run?.run_id, refreshRun])

  const submit = useCallback(
    async (text: string, confidentiality: Confidentiality) => {
      setError(null)
      setEvents([])
      lastSeqRef.current = 0
      setBusy(true)
      setSelectedSegmentId(null)
      try {
        const { run_id } = await createRun(text, confidentiality)
        setRun(null)
        await refreshRun(run_id)
        setRun(await getRun(run_id))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setBusy(false)
      }
    },
    [refreshRun],
  )

  const handleCancel = useCallback(async (runId: string) => {
    try {
      await cancelRun(runId)
      await refreshRun(runId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [refreshRun])

  useEffect(() => {
    if (run && TERMINAL.includes(run.status)) setBusy(false)
  }, [run])

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-top-row">
          <h1>GovTrans</h1>
          <nav className="app-nav">
            <button
              className={`nav-btn ${activeTab === 'translate' ? 'active' : ''}`}
              onClick={() => setActiveTab('translate')}
            >
              翻译工作台
            </button>
            <button
              className={`nav-btn ${activeTab === 'terms' ? 'active' : ''}`}
              onClick={() => setActiveTab('terms')}
            >
              术语管理
            </button>
            <button
              className={`nav-btn ${activeTab === 'corpus' ? 'active' : ''}`}
              onClick={() => setActiveTab('corpus')}
            >
              语料管理
            </button>
          </nav>
        </div>
        <p>基于官方语料的政务翻译生产系统 — 三栏智能工作空间</p>
      </header>

      <main className="app-main">
        {activeTab === 'translate' && (
          <>
            <TranslateInput busy={busy} onSubmit={submit} />
            {error && (
              <div role="alert" className="error-banner">
                {error}
              </div>
            )}
            <Workspace
              run={run}
              events={events}
              busy={busy}
              selectedSegmentId={selectedSegmentId}
              onSelectSegment={setSelectedSegmentId}
              onCancelRun={handleCancel}
            />
          </>
        )}

        {activeTab === 'terms' && <TermsAdmin />}

        {activeTab === 'corpus' && <CorpusAdmin />}
      </main>
    </div>
  )
}
