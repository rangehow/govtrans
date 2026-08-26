import { useEffect, useRef, useState } from 'react'
import {
  ArrowLeftRight,
  ArrowRight,
  BookMarked,
  CircleAlert,
  ClipboardPaste,
  FileText,
  FolderOpen,
  Globe2,
  LockKeyhole,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import {
  getLanguagePair,
  listLanguages,
  listStyleSkills,
  type CreateRunOptions,
  type StyleSkill,
} from '../api'
import {
  formatFileSize,
  MAX_SOURCE_CHARS,
  scanFolder,
  type ImportedTextFile,
  type RejectedFolderFile,
} from '../fileImport'
import type { Confidentiality } from '../types'
import type { LanguagePair, LanguageSpec } from '../types'
import { FALLBACK_LANGUAGES, languageInfo } from '../languages'

export interface BatchSource {
  path: string
  text: string
}

export interface BatchProgress {
  completed: number
  total: number
  current: string | null
  created: number
  failed: number
}

interface Props {
  busy: boolean
  batchProgress: BatchProgress | null
  onSubmit: (text: string, confidentiality: Confidentiality, options: CreateRunOptions) => void
  onSubmitBatch: (
    sources: BatchSource[],
    confidentiality: Confidentiality,
    options: CreateRunOptions,
  ) => void
  onClose?: () => void
  initialStyleSkills?: string[] | null
}

interface DraftTerm { id: string; source: string; target: string }

const DIRECTORY_INPUT_ATTRIBUTES = {
  directory: '',
  webkitdirectory: '',
}

export default function TranslateInput({
  busy,
  batchProgress,
  onSubmit,
  onSubmitBatch,
  onClose,
  initialStyleSkills,
}: Props) {
  const [text, setText] = useState('')
  const [sourceMode, setSourceMode] = useState<'paste' | 'folder'>('paste')
  const [folderName, setFolderName] = useState('')
  const [folderFiles, setFolderFiles] = useState<ImportedTextFile[]>([])
  const [rejectedFiles, setRejectedFiles] = useState<RejectedFolderFile[]>([])
  const [folderError, setFolderError] = useState<string | null>(null)
  const [scanningFolder, setScanningFolder] = useState(false)
  const [confidentiality, setConfidentiality] = useState<Confidentiality>('PUBLIC')
  const [documentType, setDocumentType] = useState('')
  const [translationMode, setTranslationMode] = useState<'coherent' | 'balanced'>('coherent')
  const [languages, setLanguages] = useState<LanguageSpec[]>(FALLBACK_LANGUAGES)
  const [sourceLanguage, setSourceLanguage] = useState('zh')
  const [targetLanguage, setTargetLanguage] = useState('en')
  const [languagePair, setLanguagePair] = useState<LanguagePair | null>(null)
  const [skills, setSkills] = useState<StyleSkill[]>([])
  const [autoStyle, setAutoStyle] = useState(initialStyleSkills == null)
  const [selectedSkills, setSelectedSkills] = useState<string[]>(initialStyleSkills || [])
  const [manualTerms, setManualTerms] = useState<DraftTerm[]>([])
  const folderInputRef = useRef<HTMLInputElement>(null)
  const normalized = text.trim()
  const tooLong = text.length > MAX_SOURCE_CHARS
  const hasSource = sourceMode === 'paste' ? Boolean(normalized) && !tooLong : folderFiles.length > 0

  useEffect(() => {
    let active = true
    void Promise.allSettled([listStyleSkills(), listLanguages()]).then((results) => {
      if (!active) return
      const [styleResult, languageResult] = results
      if (styleResult.status === 'fulfilled') setSkills(styleResult.value.skills)
      if (languageResult.status === 'fulfilled') setLanguages(languageResult.value.languages)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    void getLanguagePair(sourceLanguage, targetLanguage)
      .then((pair) => { if (active) setLanguagePair(pair) })
      .catch(() => { if (active) setLanguagePair(null) })
    return () => { active = false }
  }, [sourceLanguage, targetLanguage])

  const pairKey = `${sourceLanguage}-${targetLanguage}`
  const supportsPair = (skill: StyleSkill) => (
    !skill.supported_pairs
    || skill.supported_pairs.includes('*')
    || skill.supported_pairs.includes(pairKey)
  )
  const selectableSkills = skills.filter((skill) => !skill.locked && supportsPair(skill))
  const foundationSkills = skills.filter((skill) => skill.locked && supportsPair(skill))
  const sourceInfo = languageInfo(sourceLanguage, languages)
  const targetInfo = languageInfo(targetLanguage, languages)

  useEffect(() => {
    setSelectedSkills((current) => current.filter((id) => (
      skills.some((skill) => skill.id === id && supportsPair(skill))
    )))
  // pairKey is the stable dependency; supportsPair is intentionally derived inline.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairKey, skills])

  const chooseManualStyle = () => {
    setAutoStyle(false)
    const defaults = selectableSkills
      .filter((skill) => skill.default_for.includes(documentType))
      .map((skill) => skill.id)
    setSelectedSkills((current) => current.length > 0 ? current : defaults)
  }

  const toggleSkill = (id: string) => {
    const defaults = selectableSkills
      .filter((skill) => skill.default_for.includes(documentType))
      .map((skill) => skill.id)
    const baseSelection = autoStyle ? defaults : selectedSkills
    setAutoStyle(false)
    setSelectedSkills(baseSelection.includes(id)
      ? baseSelection.filter((item) => item !== id)
      : [...baseSelection, id])
  }

  const updateTerm = (id: string, field: 'source' | 'target', value: string) => {
    setManualTerms((current) => current.map((term) => (
      term.id === id ? { ...term, [field]: value } : term
    )))
  }

  const handleFolderSelection = async (selected: FileList | null) => {
    if (!selected?.length) return
    const files = Array.from(selected)
    setScanningFolder(true)
    setFolderError(null)
    setFolderFiles([])
    setRejectedFiles([])
    try {
      const result = await scanFolder(files)
      setFolderName(result.folderName)
      setFolderFiles(result.files)
      setRejectedFiles(result.rejected)
      if (result.files.length === 0) {
        setFolderError('该文件夹中没有可翻译的文本文件。')
      }
    } catch (caught) {
      setFolderError(caught instanceof Error ? caught.message : '读取文件夹失败，请重新选择。')
    } finally {
      setScanningFolder(false)
      if (folderInputRef.current) folderInputRef.current.value = ''
    }
  }

  const runOptions = (): CreateRunOptions => ({
    sourceLanguage,
    targetLanguage,
    documentType: documentType || undefined,
    styleSkills: autoStyle ? undefined : selectedSkills,
    translationMode,
    manualTerms: manualTerms
      .filter((term) => term.source.trim() && term.target.trim())
      .map((term) => ({ source: term.source.trim(), target: term.target.trim() })),
  })

  const submit = () => {
    if (busy || scanningFolder || !hasSource) return
    if (sourceMode === 'folder') {
      onSubmitBatch(
        folderFiles.map(({ path, text: sourceText }) => ({ path, text: sourceText })),
        confidentiality,
        runOptions(),
      )
      return
    }
    onSubmit(normalized, confidentiality, runOptions())
  }

  return (
    <section className="composer-card" aria-label="创建翻译任务">
      <div className="composer-heading">
        <div className="composer-icon" aria-hidden="true"><Sparkles size={20} /></div>
        <div>
          <h2>创建多语种政务翻译</h2>
          <p>选择任意语言方向；语料是可选的证据增强，不会限制基础翻译能力</p>
        </div>
        {onClose && (
          <button type="button" className="icon-button composer-close" onClick={onClose} aria-label="收起新建面板">
            <X size={18} />
          </button>
        )}
      </div>

      <div className="language-pair-panel">
        <label>
          <span>源语言</span>
          <select value={sourceLanguage} onChange={(event) => setSourceLanguage(event.target.value)} disabled={busy}>
            {languages.map((language) => (
              <option key={language.code} value={language.code} disabled={language.code === targetLanguage}>
                {language.name_zh} · {language.name_en}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="language-swap"
          aria-label="交换源语言和目标语言"
          title="交换语言方向"
          disabled={busy}
          onClick={() => {
            setSourceLanguage(targetLanguage)
            setTargetLanguage(sourceLanguage)
          }}
        >
          <ArrowLeftRight size={17} />
        </button>
        <label>
          <span>目标语言</span>
          <select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)} disabled={busy}>
            {languages.map((language) => (
              <option key={language.code} value={language.code} disabled={language.code === sourceLanguage}>
                {language.name_zh} · {language.name_en}
              </option>
            ))}
          </select>
        </label>
        <div className={`pair-capability ${languagePair?.capabilities.official_corpus ? 'enhanced' : 'model-native'}`}>
          <Globe2 size={16} />
          <div>
            <strong>{sourceInfo.name_zh} → {targetInfo.name_zh}·已支持</strong>
            <span>{languagePair?.capabilities.description || '模型原生多语种翻译 + 通用审校'}</span>
          </div>
        </div>
      </div>

      <div className="source-mode-header">
        <span className="field-label">{sourceInfo.name_zh}原文</span>
        <div className="source-mode-switch" role="tablist" aria-label="原文输入方式">
          <button
            type="button"
            role="tab"
            aria-selected={sourceMode === 'paste'}
            className={sourceMode === 'paste' ? 'active' : ''}
            onClick={() => setSourceMode('paste')}
            disabled={busy || scanningFolder}
          >
            <ClipboardPaste size={15} />粘贴文本
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={sourceMode === 'folder'}
            className={sourceMode === 'folder' ? 'active' : ''}
            onClick={() => setSourceMode('folder')}
            disabled={busy || scanningFolder}
          >
            <FolderOpen size={15} />选择文件夹
            {folderFiles.length > 0 && <span className="mode-count">{folderFiles.length}</span>}
          </button>
        </div>
      </div>

      {sourceMode === 'paste' ? (
        <div className={`textarea-shell ${tooLong ? 'invalid' : ''}`} role="tabpanel">
          <textarea
            id="source-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') submit()
            }}
            placeholder={`在此粘贴完整${sourceInfo.name_zh}文档…`}
            rows={9}
            disabled={busy}
            aria-describedby="source-help"
          />
          <span className="character-count">{text.length.toLocaleString()} / {MAX_SOURCE_CHARS.toLocaleString()}</span>
        </div>
      ) : (
        <div className="folder-source-panel" role="tabpanel">
          <input
            {...DIRECTORY_INPUT_ATTRIBUTES}
            ref={folderInputRef}
            className="directory-input"
            type="file"
            multiple
            disabled={busy || scanningFolder}
            onChange={(event) => void handleFolderSelection(event.target.files)}
            aria-label="选择包含文本文件的文件夹"
          />
          <div className={`folder-picker ${folderFiles.length > 0 ? 'has-files' : ''}`}>
            <span className="folder-picker-icon" aria-hidden="true"><FolderOpen size={25} /></span>
            <div>
              <strong>{folderFiles.length > 0 ? '更换文件夹' : '选择要批量翻译的文件夹'}</strong>
              <span>自动识别 TXT、Markdown、CSV、JSON、XML、字幕及常见配置文本</span>
            </div>
            <button
              type="button"
              className="secondary-button folder-select-button"
              onClick={() => folderInputRef.current?.click()}
              disabled={busy || scanningFolder}
            >
              {scanningFolder ? <span className="button-spinner dark" aria-hidden="true" /> : <FolderOpen size={16} />}
              {scanningFolder ? '正在检测…' : '选择文件夹'}
            </button>
          </div>

          {folderFiles.length > 0 && (
            <>
              <div className="folder-scan-summary" role="status">
                <FileText size={17} aria-hidden="true" />
                <strong>{folderName}</strong>
                <span>已识别 {folderFiles.length} 个文本文件</span>
                {rejectedFiles.length > 0 && <span>· 跳过 {rejectedFiles.length} 个</span>}
              </div>
              <div className="folder-file-list" aria-label="待翻译文本文件">
                {folderFiles.map((file) => (
                  <div className="folder-file-row" key={file.path}>
                    <FileText size={16} aria-hidden="true" />
                    <span className="folder-file-name" title={file.path}>{file.path}</span>
                    <span className="folder-file-meta">
                      {file.text.length.toLocaleString()} 字符 · {formatFileSize(file.size)}
                    </span>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`移除 ${file.path}`}
                      title="不翻译此文件"
                      onClick={() => setFolderFiles((current) => current.filter((item) => item.path !== file.path))}
                      disabled={busy}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}

          {rejectedFiles.length > 0 && (
            <details className="folder-rejections">
              <summary><CircleAlert size={15} />查看已跳过的 {rejectedFiles.length} 个文件</summary>
              <ul>
                {rejectedFiles.slice(0, 50).map((file) => (
                  <li key={file.path}><span title={file.path}>{file.path}</span><em>{file.reason}</em></li>
                ))}
                {rejectedFiles.length > 50 && <li>另有 {rejectedFiles.length - 50} 个文件未展开</li>}
              </ul>
            </details>
          )}
          {folderError && <p className="field-error">{folderError}</p>}
          {batchProgress && (
            <div className="batch-submit-progress" role="status" aria-live="polite">
              <span>{batchProgress.completed}/{batchProgress.total}</span>
              <div>
                <strong>{batchProgress.current ? '正在创建翻译任务' : '批量任务创建完成'}</strong>
                <small title={batchProgress.current || undefined}>
                  {batchProgress.current || `成功 ${batchProgress.created} 个，失败 ${batchProgress.failed} 个`}
                </small>
              </div>
            </div>
          )}
        </div>
      )}
      <div id="source-help" className="source-help">
        <span><ShieldCheck size={14} />全文实体与指代台账</span>
        <span><Sparkles size={14} />连续上下文联合推理</span>
        <span><LockKeyhole size={14} />任务与译文持久化</span>
      </div>

      <div className="composer-controls">
        <label>
          <span>文种类型</span>
          <select value={documentType} onChange={(event) => setDocumentType(event.target.value)} disabled={busy}>
            <option value="">自动识别</option>
            <option value="policy_document">政策文件</option>
            <option value="white_paper">白皮书</option>
            <option value="leader_speech">领导人讲话</option>
            <option value="press_conference">新闻发布会</option>
            <option value="report">工作报告</option>
            <option value="notice">通知公告</option>
          </select>
        </label>
        <label>
          <span>保密级别</span>
          <select
            value={confidentiality}
            onChange={(event) => setConfidentiality(event.target.value as Confidentiality)}
            disabled={busy}
          >
            <option value="PUBLIC">公开 · 允许官方网站核验</option>
            <option value="INTERNAL">内部 · 仅官方白名单</option>
            <option value="CONFIDENTIAL">机密 · 完全禁用外网</option>
          </select>
        </label>
        <button
          type="button"
          className="primary submit-translation"
          disabled={busy || scanningFolder || !hasSource}
          onClick={submit}
        >
          {busy ? <span className="button-spinner" aria-hidden="true" /> : <Sparkles size={17} />}
          {busy
            ? batchProgress
              ? `正在创建 ${Math.min(batchProgress.completed + 1, batchProgress.total)}/${batchProgress.total}`
              : '正在创建任务…'
            : sourceMode === 'folder'
              ? `批量创建 ${folderFiles.length} 个任务`
              : '开始自动翻译'}
          {!busy && <ArrowRight size={17} />}
        </button>
      </div>

      <div className="translation-config-grid">
        <section className="config-section">
          <div className="config-section-head">
            <div><span className="config-kicker">01 文风 Skill</span><h3>选择成文规范</h3></div>
            <label className="switch-label">
              <input
                type="checkbox"
                checked={autoStyle}
                onChange={(event) => event.target.checked ? setAutoStyle(true) : chooseManualStyle()}
              />自动匹配文种
            </label>
          </div>
          <div className="skill-choice-grid">
            {selectableSkills.length === 0 && (
              <div className="pair-style-empty">该语言对自动使用多语种政务基础规则，当前没有另外的专项文风包。</div>
            )}
            {selectableSkills.map((skill) => {
              const autoSelected = autoStyle && skill.default_for.includes(documentType)
              const selected = autoSelected || (!autoStyle && selectedSkills.includes(skill.id))
              return (
                <button
                  type="button"
                  key={skill.id}
                  className={`skill-choice ${selected ? 'selected' : ''}`}
                  onClick={() => toggleSkill(skill.id)}
                  disabled={busy}
                >
                  <strong>{skill.name}</strong>
                  <span>{skill.description || '版本化的文体、句法与衔接规范'}</span>
                  <small>
                    {autoSelected ? '已按文种自动启用 · ' : selected ? '已手动选择 · ' : ''}
                    {skill.base_rule_count} 条内置规范{skill.distilled_rule_count > 0 ? ` · ${skill.distilled_rule_count} 条增量` : ''}
                  </small>
                </button>
              )
            })}
          </div>
          <p className="config-note">
            {autoStyle && !documentType
              ? languagePair?.capabilities.specialized_style
                ? '当前为自动模式：文档分析确定文种后选择对应 Skill。'
                : '当前语言对使用通用多语种政务成文规则。'
              : `${foundationSkills.map((skill) => skill.name).join('、') || '基础政务规范'}始终启用；Skill 只管文风，不夹带固定术语。`}
          </p>
        </section>

        <section className="config-section">
          <div className="config-section-head">
            <div><span className="config-kicker">02 翻译策略</span><h3>上下文与速度</h3></div>
          </div>
          <div className="mode-choice-grid">
            <button type="button" className={translationMode === 'coherent' ? 'selected' : ''} onClick={() => setTranslationMode('coherent')}>
              <strong>全文连贯（推荐）</strong>
              <span>短文一次完成；长文顺序翻译连续章节，后文可见前文译法。</span>
            </button>
            <button type="button" className={translationMode === 'balanced' ? 'selected' : ''} onClick={() => setTranslationMode('balanced')}>
              <strong>均衡提速</strong>
              <span>长文并发处理章节，共享实体台账；适合时效优先场景。</span>
            </button>
          </div>
        </section>
      </div>

      <section className="task-terms-section">
        <div className="config-section-head">
          <div><span className="config-kicker">03 本次任务术语</span><h3>人工指定译法（可选）</h3></div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setManualTerms((current) => [
              ...current, { id: crypto.randomUUID(), source: '', target: '' },
            ])}
          >
            <Plus size={14} />添加术语
          </button>
        </div>
        {manualTerms.length === 0 ? (
          <button type="button" className="term-empty-add" onClick={() => setManualTerms([{ id: crypto.randomUUID(), source: '', target: '' }])}>
            <BookMarked size={16} />如有项目专用译法，可在这里直接指定；它将覆盖全局术语库。
          </button>
        ) : (
          <div className="task-term-list">
            {manualTerms.map((term) => (
              <div className="task-term-row" key={term.id}>
                <input aria-label={`${sourceInfo.name_zh}术语`} placeholder={`${sourceInfo.name_zh}术语`} value={term.source} onChange={(event) => updateTerm(term.id, 'source', event.target.value)} />
                <span>→</span>
                <input aria-label={`${targetInfo.name_zh}规定译法`} placeholder={`${targetInfo.name_zh}规定译法`} value={term.target} onChange={(event) => updateTerm(term.id, 'target', event.target.value)} />
                <button type="button" className="icon-button" aria-label="删除术语" onClick={() => setManualTerms((current) => current.filter((item) => item.id !== term.id))}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {sourceMode === 'paste' && tooLong && <p className="field-error">原文超过 100,000 字符，请分成多个文件处理。</p>}
    </section>
  )
}
