import { useCallback, useEffect, useState } from 'react'
import {
  BookOpenCheck,
  CheckCircle2,
  Database,
  ExternalLink,
  FileUp,
  FlaskConical,
  Languages,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import {
  importScioPair,
  getLatestScioCorpusSync,
  getScioCorpusSync,
  listStyleRules,
  listStyleSkills,
  mineStyleRules,
  reviewStyleRule,
  startScioCorpusSync,
  type ScioSyncJob,
  type StyleRule,
  type StyleSkill,
} from '../api'

const MAX_SAVED_HTML_BYTES = 2_500_000
const ACTIVE_SYNC_STATUSES = new Set(['queued', 'discovering', 'running', 'distilling'])

function isActiveSync(job: ScioSyncJob | null): boolean {
  return Boolean(job && ACTIVE_SYNC_STATUSES.has(job.status))
}

function completedSyncNotice(job: ScioSyncJob): string {
  const activated = job.distillation.auto_activated ?? job.distillation.auto_published ?? 0
  const ruleAction = activated > 0
    ? `${activated} 条高置信文风规则已自动生效`
    : '文风统计已更新，没有新的高置信增量规则'
  const failed = job.failed_count > 0 ? `；${job.failed_count} 份暂时失败，可再次同步续传` : ''
  return `${job.since_year}–${job.through_year} 年官方双语白皮书同步完成：发现 ${job.discovered} 组，成功 ${job.succeeded} 组，保存/复用 ${job.sentence_pairs} 个句对；${ruleAction}${failed}。`
}

interface Props {
  onUseSkills: (skillIds: string[]) => void
}

export default function StyleSkillsAdmin({ onUseSkills }: Props) {
  const [skills, setSkills] = useState<StyleSkill[]>([])
  const [rules, setRules] = useState<StyleRule[]>([])
  const [zhUrl, setZhUrl] = useState('')
  const [enUrl, setEnUrl] = useState('')
  const [domain, setDomain] = useState('')
  const [zhHtmlFile, setZhHtmlFile] = useState<File | null>(null)
  const [enHtmlFile, setEnHtmlFile] = useState<File | null>(null)
  const [fileInputKey, setFileInputKey] = useState(0)
  const [busyAction, setBusyAction] = useState<'sync' | 'import' | 'mine' | null>(null)
  const [syncJob, setSyncJob] = useState<ScioSyncJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([])
  const syncActive = isActiveSync(syncJob)
  const busy = busyAction !== null || syncActive

  const refresh = useCallback(async () => {
    const [skillResponse, ruleResponse] = await Promise.all([
      listStyleSkills(), listStyleRules(),
    ])
    setSkills(skillResponse.skills)
    setRules(ruleResponse.rules)
  }, [])

  useEffect(() => {
    void refresh().catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
  }, [refresh])

  useEffect(() => {
    let cancelled = false
    void getLatestScioCorpusSync()
      .then(({ job }) => {
        if (cancelled || !job) return
        setSyncJob(job)
        if (isActiveSync(job)) setBusyAction('sync')
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const jobId = syncJob?.job_id
    if (!jobId || !isActiveSync(syncJob)) return undefined
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const next = await getScioCorpusSync(jobId)
        if (cancelled) return
        setSyncJob(next)
        if (isActiveSync(next)) {
          timer = window.setTimeout(() => void poll(), 1_500)
          return
        }
        setBusyAction(null)
        if (next.status === 'failed') setError(next.error || '近十年语料同步失败，可重新启动续传。')
        else setNotice(completedSyncNotice(next))
        await refresh()
      } catch (caught) {
        if (cancelled) return
        setError(caught instanceof Error ? caught.message : String(caught))
        timer = window.setTimeout(() => void poll(), 3_000)
      }
    }
    timer = window.setTimeout(() => void poll(), 800)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [syncJob?.job_id, refresh])

  const importPair = async () => {
    if (!zhUrl.trim() || !enUrl.trim()) return
    setBusyAction('import'); setError(null); setNotice(null)
    try {
      const [zhHtml, enHtml] = await Promise.all([
        zhHtmlFile?.text(), enHtmlFile?.text(),
      ])
      const result = await importScioPair(
        zhUrl.trim(),
        enUrl.trim(),
        domain.trim() || undefined,
        { zhHtml, enHtml },
      )
      const mined = result.distillation
      const pages = result.source_pages.zh.length + result.source_pages.en.length
      const evidenceAction = result.ingest.warnings.length > 0 ? '已复用既有证据' : `已读取 ${pages} 个正文页`
      const activated = mined.auto_activated ?? mined.auto_published ?? 0
      const ruleAction = activated > 0
        ? `${activated} 条高置信文风规则已自动生效`
        : '文风统计已更新；低一致性表达仅作观察，不影响翻译'
      setNotice(
        `${evidenceAction}，保存 ${result.ingest.sentence_pairs} 个句对；${ruleAction}。`,
      )
      setZhUrl(''); setEnUrl(''); setDomain('')
      setZhHtmlFile(null); setEnHtmlFile(null)
      setFileInputKey((current) => current + 1)
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally { setBusyAction(null) }
  }

  const syncLatest = async () => {
    setBusyAction('sync'); setError(null); setNotice(null)
    try {
      const job = await startScioCorpusSync(10, domain.trim() || undefined)
      setSyncJob(job)
      if (!isActiveSync(job)) {
        setBusyAction(null)
        setNotice(completedSyncNotice(job))
        await refresh()
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
      setBusyAction(null)
    }
  }

  const remine = async () => {
    setBusyAction('mine'); setError(null); setNotice(null)
    try {
      const result = await mineStyleRules()
      const activated = result.auto_activated ?? result.auto_published ?? 0
      const ruleAction = activated > 0
        ? `${activated} 条高置信新规则已自动生效`
        : '未发现达到强一致阈值的新规则；无需人工处理'
      setNotice(`已扫描 ${result.documents_scanned || 0} 份官方文档、${result.pairs_scanned || 0} 个句对；${ruleAction}。`)
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally { setBusyAction(null) }
  }

  const setRuleStatus = async (id: string, status: 'candidate' | 'approved' | 'rejected') => {
    setError(null)
    try {
      await reviewStyleRule(id, status)
      await refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
  }

  const approvedCount = rules.filter((rule) => rule.status === 'approved').length
  const activeRules = rules.filter((rule) => rule.status === 'approved')
  const governanceRules = rules.filter((rule) => rule.status !== 'approved')
  const scioSkill = skills.find((skill) => skill.id === 'scio-white-paper-distilled')
  const currentYear = new Date().getFullYear()
  const syncRange = syncJob
    ? `${syncJob.since_year}–${syncJob.through_year}`
    : `${currentYear - 9}–${currentYear}`
  const syncButtonLabel = syncActive
    ? syncJob?.status === 'discovering'
      ? `正在扫描 ${syncRange} 年目录…`
      : syncJob?.status === 'distilling'
        ? '正在蒸馏近十年文风…'
        : `已处理 ${syncJob?.processed || 0}/${syncJob?.discovered || '…'} 组`
    : syncJob?.succeeded
      ? `检查并增量更新（${syncRange}）`
      : `同步近十年（${syncRange}）`

  return (
    <div className="style-admin">
      <section className="knowledge-boundary">
        <div className="knowledge-card terms">
          <BookOpenCheck size={20} />
          <div><strong>术语</strong><span>按语言对隔离的硬约束，由人直接新增、修改或按任务覆盖。</span></div>
        </div>
        <div className="knowledge-arrow">→</div>
        <div className="knowledge-card corpus">
          <Database size={20} />
          <div><strong>双语语料</strong><span>保留官方原文、译文与出处，是可追溯证据，不直接变成强制译法。</span></div>
        </div>
        <div className="knowledge-arrow">→</div>
        <div className="knowledge-card skill">
          <FlaskConical size={20} />
          <div><strong>文风 Skill</strong><span>从高质量对齐语料归纳句法、语域与衔接规律，按任务选择启用。</span></div>
        </div>
      </section>

      <section className="style-panel">
        <div className="style-panel-head">
          <div>
            <span className="section-eyebrow"><ShieldCheck size={13} />运行时能力</span>
            <h2>可配置文风 Skills</h2>
            <p>点击可选卡片可组合下一个任务的文风。不手动选择时，系统会根据文种自动匹配；基础安全规则始终启用。</p>
          </div>
          <span className="published-count">
            SCIO Skill 完整可用 · {scioSkill?.base_rule_count || 0} 条内置规范 · {scioSkill?.candidate_rule_count || 0} 项语料观察
            {approvedCount > 0 ? ` · ${approvedCount} 条高置信增量` : ''}
          </span>
        </div>
        <div className="skill-catalog-grid">
          {skills.map((skill) => {
            const selected = selectedSkillIds.includes(skill.id)
            const cardBody = (
              <>
                <div className="skill-card-title">
                  <Languages size={17} /><strong>{skill.name}</strong>
                  <span>{skill.locked ? '基础·始终启用' : selected ? '已选择' : '可选'}</span>
                </div>
                <p>{skill.description || '政务文件的版本化成文与衔接规范。'}</p>
                <footer>
                  <code>v {skill.version}</code>
                  <b>{skill.base_rule_count} 条内置规范{skill.distilled_rule_count > 0 ? ` · ${skill.distilled_rule_count} 条增量` : ''}</b>
                </footer>
              </>
            )
            if (skill.locked) {
              return <article key={skill.id} className="skill-catalog-card locked">{cardBody}</article>
            }
            return (
              <button
                type="button"
                key={skill.id}
                aria-pressed={selected}
                className={`skill-catalog-card selectable ${selected ? 'selected' : ''}`}
                onClick={() => setSelectedSkillIds((current) => current.includes(skill.id)
                  ? current.filter((item) => item !== skill.id)
                  : [...current, skill.id])}
              >
                {cardBody}
              </button>
            )
          })}
        </div>
        <div className="skill-selection-action">
          <span>{selectedSkillIds.length > 0 ? `已选 ${selectedSkillIds.length} 个文种 Skill，将作为下一个任务的显式配置。` : '无需预先配置：新任务默认按文种自动匹配。'}</span>
          <button type="button" className="primary" disabled={selectedSkillIds.length === 0} onClick={() => onUseSkills(selectedSkillIds)}>
            用已选 Skill 新建翻译
          </button>
        </div>
      </section>

      <section className="style-panel scio-import-panel">
        <div className="style-panel-head">
          <div>
            <span className="section-eyebrow"><Database size={13} />官方双语蒸馏</span>
            <h2>导入国新办白皮书对照文档</h2>
            <p>
              系统逐年读取近十年白皮书归档，自动完成站点验证，并按年度页及国新办双语专题声明确定中英文对应关系；随后合并分页、对齐和蒸馏，全程不等待人工审批。
              {' '}<a href="http://www.scio.gov.cn/zfbps/" target="_blank" rel="noreferrer">中文白皮书目录 <ExternalLink size={11} /></a>
            </p>
          </div>
          <div className="style-panel-actions">
            <button type="button" className="primary" onClick={() => void syncLatest()} disabled={busy}>
              {busyAction === 'sync' || syncActive ? <span className="button-spinner" /> : <Database size={14} />}
              {syncButtonLabel}
            </button>
            <button type="button" className="secondary-button" onClick={() => void remine()} disabled={busy}>
              <RefreshCw size={14} />重新蒸馏已有语料
            </button>
          </div>
        </div>
        {syncJob && syncActive && (
          <div className="sync-progress" role="status">
            <div className="sync-progress-copy">
              <strong>{syncJob.status === 'discovering' ? '正在建立十年双语清单' : syncJob.status === 'distilling' ? '正在归纳跨文档文风规律' : '正在逐份抓取、对齐并持久化'}</strong>
              <span>{syncJob.current_title || `${syncRange} 年任务在服务端持续运行；刷新或离开页面不会中断。`}</span>
            </div>
            <div className="sync-progress-track"><i style={{ width: `${Math.max(2, Math.round(syncJob.progress * 100))}%` }} /></div>
            <small>成功 {syncJob.succeeded} · 暂时失败 {syncJob.failed_count} · 已保存 {syncJob.sentence_pairs} 个句对</small>
          </div>
        )}
        {syncJob && !syncActive && syncJob.succeeded > 0 && (
          <div className="corpus-coverage-summary">
            <CheckCircle2 size={17} />
            <div>
              <strong>{syncRange} 年官方双语白皮书：{syncJob.succeeded} 组</strong>
              <span>{syncJob.sentence_pairs.toLocaleString()} 个持久化句对 · 再次同步只检查新增或修正项</span>
            </div>
          </div>
        )}
        <div className="manual-import-label"><strong>指定一对正文（可选）</strong><span>需要补录历史材料时再填写；日常使用上方自动同步即可。</span></div>
        <div className="scio-import-form">
          <label><span>中文正文 URL</span><input value={zhUrl} onChange={(event) => setZhUrl(event.target.value)} placeholder="http://www.scio.gov.cn/zfbps/..." /></label>
          <label><span>英文正文 URL</span><input value={enUrl} onChange={(event) => setEnUrl(event.target.value)} placeholder="https://english.scio.gov.cn/whitepapers/..." /></label>
          <label><span>领域（可选）</span><input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="economy / governance" /></label>
          <button type="button" className="primary" onClick={() => void importPair()} disabled={busy || !zhUrl.trim() || !enUrl.trim()}>
            {busyAction === 'import' ? <span className="button-spinner" /> : <FlaskConical size={15} />}{busyAction === 'import' ? '正在处理…' : '导入并蒸馏'}
          </button>
        </div>
        <details className="scio-html-fallback">
          <summary><FileUp size={14} />自动浏览器仍遇到极端网络故障？使用官方 HTML 兜底</summary>
          <p>正常情况不需要上传。URL 仍用于官方域名校验和出处留痕；文件只替代失败的网络抓取，不绕过证据校验。</p>
          <div className="scio-file-grid">
            <label>
              <span className="saved-html-title">中文官方 HTML</span>
              <input
                key={`zh-${fileInputKey}`}
                className="saved-html-input"
                type="file"
                accept=".html,.htm,text/html"
                onChange={(event) => {
                  const file = event.target.files?.[0] || null
                  if (file && file.size > MAX_SAVED_HTML_BYTES) {
                    setError('中文 HTML 超过 2.5 MB，请保存正文页而不是完整网站归档。')
                    setZhHtmlFile(null)
                    event.target.value = ''
                    return
                  }
                  setZhHtmlFile(file)
                }}
              />
              <span className="saved-html-action"><FileUp size={12} />选择 HTML 文件</span>
              <small>{zhHtmlFile?.name || '未选择；网络可抓取时不需要'}</small>
            </label>
            <label>
              <span className="saved-html-title">英文官方 HTML（可选）</span>
              <input
                key={`en-${fileInputKey}`}
                className="saved-html-input"
                type="file"
                accept=".html,.htm,text/html"
                onChange={(event) => {
                  const file = event.target.files?.[0] || null
                  if (file && file.size > MAX_SAVED_HTML_BYTES) {
                    setError('英文 HTML 超过 2.5 MB，请保存正文页而不是完整网站归档。')
                    setEnHtmlFile(null)
                    event.target.value = ''
                    return
                  }
                  setEnHtmlFile(file)
                }}
              />
              <span className="saved-html-action"><FileUp size={12} />选择 HTML 文件</span>
              <small>{enHtmlFile?.name || '通常由系统自动抓取并合并分页'}</small>
            </label>
          </div>
        </details>
        {error && <div role="alert" className="error-banner">{error}</div>}
        {notice && <div role="status" className="success-banner"><CheckCircle2 size={16} />{notice}</div>}
      </section>

      <section className="style-panel">
        <div className="style-panel-head">
          <div><span className="section-eyebrow">证据增量</span><h2>从官方语料自动归纳的文风规则</h2></div>
          <p>置信度 ≥ 80% 且至少两份不同官方文档支持时，规则自动进入 SCIO Skill 并在运行时生效。其余只是观察项，不会生成审批待办。</p>
        </div>
        {activeRules.length === 0 ? (
          <div className="panel-empty compact">当前没有达到强一致阈值的增量规则。这不代表 Skill 为空：国新办白皮书文风已包含 {scioSkill?.base_rule_count || 0} 条内置篇章、衔接和语体规范。</div>
        ) : (
          <div className="style-rule-list">
            {activeRules.map((rule) => (
              <article className="style-rule-row" key={rule.id}>
                <div className="rule-confidence"><strong>{Math.round(rule.confidence * 100)}%</strong><span>{rule.source_count} 份文档</span></div>
                <div className="rule-copy">
                  <div><span className="status-tag status-approved">{rule.activation_source === 'human' ? '人工例外生效' : '自动生效'}</span><strong>{rule.rule}</strong></div>
                  {rule.examples[0] && <p>{rule.examples[0].zh}<br /><em>{rule.examples[0].en}</em></p>}
                </div>
              </article>
            ))}
          </div>
        )}
        {governanceRules.length > 0 && (
          <details className="rule-governance">
            <summary>可选治理：{governanceRules.filter((rule) => rule.status === 'candidate').length} 条观察项，{governanceRules.filter((rule) => rule.status === 'rejected').length} 条已排除（无待办）</summary>
            <p>只有在业务专家确认低置信表达确实应作为例外成文偏好，或数据明显异常时才需人工操作。</p>
            <div className="style-rule-list">
              {governanceRules.map((rule) => (
                <article className="style-rule-row" key={rule.id}>
                  <div className="rule-confidence"><strong>{Math.round(rule.confidence * 100)}%</strong><span>{rule.source_count} 份文档</span></div>
                  <div className="rule-copy">
                    <div><span className={`status-tag status-${rule.status}`}>{rule.status === 'candidate' ? '观察项·不生效' : '已排除'}</span><strong>{rule.rule}</strong></div>
                    {rule.examples[0] && <p>{rule.examples[0].zh}<br /><em>{rule.examples[0].en}</em></p>}
                  </div>
                  <div className="rule-actions">
                    {rule.status === 'candidate' ? (
                      <>
                        <button type="button" onClick={() => void setRuleStatus(rule.id, 'approved')}><CheckCircle2 size={14} />人工例外启用</button>
                        <button type="button" onClick={() => void setRuleStatus(rule.id, 'rejected')}><XCircle size={14} />排除异常</button>
                      </>
                    ) : (
                      <button type="button" onClick={() => void setRuleStatus(rule.id, 'candidate')}>恢复为观察项</button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </details>
        )}
      </section>
    </div>
  )
}
