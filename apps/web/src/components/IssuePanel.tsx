import type { Issue } from '../types'

const ORDER: Issue['severity'][] = ['critical', 'major', 'minor']
const LABEL: Record<Issue['severity'], string> = {
  critical: '严重', major: '重要', minor: '轻微',
}

export default function IssuePanel({ issues }: { issues: Issue[] }) {
  const open = issues.filter((i) => i.status === 'open')
  if (open.length === 0) {
    return <div className="panel-empty">暂无待处理 QA 问题</div>
  }
  return (
    <div className="issue-panel" aria-label="QA 问题列表">
      {ORDER.map((sev) => {
        const group = open.filter((i) => i.severity === sev)
        if (group.length === 0) return null
        return (
          <section key={sev} className={`issue-group ${sev}`}>
            <h4>
              {LABEL[sev]} <span className="issue-count">{group.length}</span>
            </h4>
            <ul>
              {group.map((issue) => (
                <li key={issue.id}>
                  <span className="issue-category">[{issue.category}]</span> {issue.message}
                  {issue.suggested_fix && (
                    <div className="issue-fix">建议：{issue.suggested_fix}</div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
