import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelRun, createRun, getRun, openEventStream } from './api'
import ActivityFeed from './components/ActivityFeed'
import IssuePanel from './components/IssuePanel'
import RunTimeline from './components/RunTimeline'
import SegmentView from './components/SegmentView'
import TranslateInput from './components/TranslateInput'
import type { Confidentiality, Run, RunEvent } from './types'

const TERMINAL: Run['status'][] = ['COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_HUMAN_REVIEW']

export default function App() {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const lastSeqRef = useRef(0)
  const runRef = useRef<Run | null>(null)
  runRef.current = run

  const refreshRun = useCallback(async (runId: string) => {
    try {
      setRun(await getRun(runId))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // Live SSE stream: all timeline/activity state derives from real backend
  // events. A slow poll backs up EventSource reconnects.
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

  useEffect(() => {
    if (run && TERMINAL.includes(run.status)) setBusy(false)
  }, [run])

  const running = run && !TERMINAL.includes(run.status)

  return (
    <div className="app">
      <header className="app-header">
        <h1>GovTrans</h1>
        <p>基于官方语料的政务翻译生产系统</p>
      </header>

      <main className="layout">
        <div className="col-left">
          <TranslateInput busy={busy} onSubmit={submit} />
          {error && <div role="alert" className="error-banner">{error}</div>}
          {run && (
            <section aria-label="句段对照">
              <h2>句段对照</h2>
              <SegmentView segments={run.segments} />
            </section>
          )}
        </div>

        <div className="col-right">
          {run ? (
            <>
              <section className="run-status" aria-label="运行状态">
                <h2>
                  运行状态：<span className={`status status-${run.status}`}>{run.status}</span>
                  {running && (
                    <button className="link" onClick={() => void cancelRun(run.run_id)}>
                      取消
                    </button>
                  )}
                </h2>
                <progress value={run.progress} max={1} />
                {run.error && <div role="alert" className="error-banner">{run.error}</div>}
              </section>
              <RunTimeline events={events} />
              <section aria-label="QA 问题"><h2>QA 问题</h2><IssuePanel issues={run.issues} /></section>
              <section aria-label="活动流"><h2>实时活动</h2><ActivityFeed events={events} /></section>
            </>
          ) : (
            !busy && <div className="panel-empty hero">提交原文开始一次真实翻译运行 — 所有进度由后端事件驱动</div>
          )}
          {busy && !run && <div className="panel-empty hero">正在创建运行…</div>}
        </div>
      </main>
    </div>
  )
}
