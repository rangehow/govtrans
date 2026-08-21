import type { Run, RunEvent, Segment, Issue } from '../types'

export interface WorkspaceProps {
  run: Run | null
  events: RunEvent[]
  busy: boolean
  selectedSegmentId: string | null
  onSelectSegment: (id: string | null) => void
  onCancelRun: (runId: string) => void
}

export interface SourceColumnProps {
  segments: Segment[]
  selectedSegmentId: string | null
  onSelectSegment: (id: string) => void
}

export interface TranslationColumnProps {
  segments: Segment[]
  selectedSegmentId: string | null
  onSelectSegment: (id: string) => void
}

export interface IntelligencePanelProps {
  run: Run
  events: RunEvent[]
  selectedSegmentId: string | null
}

export interface VersionHistoryProps {
  segment: Segment | null
}
