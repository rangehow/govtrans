import type { Run, RunEvent, Segment, Issue } from '../types'

export interface WorkspaceProps {
  run: Run
  events: RunEvent[]
  selectedSegmentId: string | null
  connection: 'idle' | 'connecting' | 'live' | 'reconnecting'
  continuing: boolean
  onSelectSegment: (id: string | null) => void
  onCancelRun: (runId: string) => void
  onContinueRun: (runId: string) => void
  onUpdateSegment: (
    runId: string,
    segmentId: string,
    translation: string,
    issueId?: string,
  ) => Promise<void>
  onNewRun: () => void
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
  selectedIssueId: string | null
  editingIssueId: string | null
  onSelectIssue: (issue: Issue) => void
  onEditIssue: (issue: Issue) => void
  onCancelEditIssue: () => void
  onSaveIssue: (issue: Issue, translation: string) => Promise<void>
}

export interface VersionHistoryProps {
  segment: Segment | null
}
