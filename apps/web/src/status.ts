import type { RunStatus } from './types'

export const TERMINAL_STATUSES: RunStatus[] = [
  'COMPLETED',
  'FAILED',
  'QUALITY_GATE_FAILED',
  'CANCELLED',
  'WAITING_HUMAN_REVIEW',
]

export const STATUS_LABEL: Record<RunStatus, string> = {
  CREATED: '已创建',
  PARSING: '解析中',
  ANALYZING: '分析中',
  RESEARCHING: '术语核验中',
  TRANSLATING: '翻译中',
  REVIEWING: '多路审校中',
  FINALIZING: '自动修订中',
  QA: '质量闸门中',
  WAITING_RESOURCES: '等待推理资源',
  COMPLETED: '已通过·可交付',
  FAILED: '任务中断',
  QUALITY_GATE_FAILED: '已暂停·可继续优化',
  CANCELLED: '已取消',
  WAITING_HUMAN_REVIEW: '旧版质量拦截',
}

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status)
}

export function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime()
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000))
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`
  return new Date(value).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

export const DOCUMENT_TYPE_LABEL: Record<string, string> = {
  white_paper: '白皮书',
  policy_document: '政策文件',
  press_conference: '新闻发布会',
  leader_speech: '领导人讲话',
  report: '工作报告',
  notice: '通知公告',
  other: '政务文本',
}
