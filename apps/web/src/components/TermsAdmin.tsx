import React, { useState, useEffect } from 'react'
import { listTerms, createTerm, updateTerm, deprecateTerm, getTermHistory, type Term, type TermHistory } from '../api'

export default function TermsAdmin() {
  const [query, setQuery] = useState(''), [debouncedQuery, setDebouncedQuery] = useState('')
  const [terms, setTerms] = useState<Term[]>([]), [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null)
  const [newSource, setNewSource] = useState(''), [newTarget, setNewTarget] = useState(''), [newDomain, setNewDomain] = useState(''), [newContext, setNewContext] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null), [editTarget, setEditTarget] = useState(''), [editDomain, setEditDomain] = useState('')
  const [historyMap, setHistoryMap] = useState<Record<string, TermHistory[]>>({}), [historyLoading, setHistoryLoading] = useState<Record<string, boolean>>({}), [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(timer)
  }, [query])

  const fetchTerms = async (q: string) => {
    setLoading(true); setError(null)
    try { setTerms((await listTerms(q)).terms || []) } catch (err) { setError(err instanceof Error ? err.message : '加载术语失败') }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchTerms(debouncedQuery) }, [debouncedQuery])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newSource.trim() || !newTarget.trim()) return
    setError(null)
    try {
      await createTerm({ source_term: newSource, preferred_target: newTarget, domain: newDomain || undefined, context: newContext || undefined })
      setNewSource(''); setNewTarget(''); setNewDomain(''); setNewContext('')
      fetchTerms(debouncedQuery)
    } catch (err) { setError(err instanceof Error ? err.message : '创建失败') }
  }

  const startEdit = (t: Term) => { setEditingId(t.id); setEditTarget(t.preferred_target); setEditDomain(t.domain || '') }

  const handleSaveEdit = async (id: string) => {
    setError(null)
    try { await updateTerm(id, { preferred_target: editTarget, domain: editDomain }); setEditingId(null); fetchTerms(debouncedQuery) }
    catch (err) { setError(err instanceof Error ? err.message : '更新失败') }
  }

  const handleDeprecate = async (id: string) => {
    if (!window.confirm('确定要弃用该术语吗？')) return
    setError(null)
    try { await deprecateTerm(id); fetchTerms(debouncedQuery) } catch (err) { setError(err instanceof Error ? err.message : '弃用失败') }
  }

  const toggleHistory = async (id: string) => {
    if (expandedId === id) { setExpandedId(null); return }
    setExpandedId(id)
    if (!historyMap[id]) {
      setHistoryLoading(prev => ({ ...prev, [id]: true }))
      try {
        const res = await getTermHistory(id)
        setHistoryMap(prev => ({ ...prev, [id]: res.history || [] }))
      } catch (err) { setError(err instanceof Error ? err.message : '加载历史失败') }
      finally { setHistoryLoading(prev => ({ ...prev, [id]: false })) }
    }
  }

  return (
    <div className="terms-admin">
      <form onSubmit={handleCreate} className="admin-form-panel">
        <h3>新增术语</h3>
        <div className="form-row">
          <input placeholder="源术语 (必填)" value={newSource} onChange={e => setNewSource(e.target.value)} required />
          <input placeholder="规定译法 (必填)" value={newTarget} onChange={e => setNewTarget(e.target.value)} required />
          <input placeholder="领域 (选填)" value={newDomain} onChange={e => setNewDomain(e.target.value)} />
          <input placeholder="上下文 (选填)" value={newContext} onChange={e => setNewContext(e.target.value)} />
          <button type="submit" className="primary btn-sm">添加</button>
        </div>
      </form>

      <div className="search-bar-wrap">
        <input type="text" className="search-input" placeholder="输入术语名称搜索..." value={query} onChange={e => setQuery(e.target.value)} />
      </div>

      {error && <div role="alert" className="error-banner">{error}</div>}

      <div className="admin-table-wrap">
        {loading ? <div className="loading-state">正在加载术语...</div> : terms.length === 0 ? <div className="panel-empty">暂无术语数据</div> : (
          <table className="admin-table">
            <thead>
              <tr><th>源术语</th><th>规定译法</th><th>领域</th><th>状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              {terms.map(t => (
                <React.Fragment key={t.id}>
                  <tr>
                    <td className="font-semibold">{t.source_term}</td>
                    <td>{editingId === t.id ? <input className="table-input" value={editTarget} onChange={e => setEditTarget(e.target.value)} /> : t.preferred_target}</td>
                    <td>{editingId === t.id ? <input className="table-input" value={editDomain} onChange={e => setEditDomain(e.target.value)} /> : (t.domain || '-')}</td>
                    <td><span className={`status-tag status-${t.status}`}>{t.status === 'deprecated' ? '已弃用' : '启用中'}</span></td>
                    <td>
                      <div className="action-buttons">
                        {editingId === t.id ? (
                          <>
                            <button onClick={() => handleSaveEdit(t.id)} className="btn-link text-ok">保存</button>
                            <button onClick={() => setEditingId(null)} className="btn-link">取消</button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => startEdit(t)} disabled={t.status === 'deprecated'} className="btn-link">编辑</button>
                            <button onClick={() => handleDeprecate(t.id)} disabled={t.status === 'deprecated'} className="btn-link text-danger">弃用</button>
                          </>
                        )}
                        <button onClick={() => toggleHistory(t.id)} className="btn-link">历史</button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === t.id && (
                    <tr className="history-row">
                      <td colSpan={5}>
                        <div className="history-panel">
                          <h4>修改历史纪录</h4>
                          {historyLoading[t.id] ? <p className="loading-state">载入中...</p> : !historyMap[t.id]?.length ? <p className="panel-empty">尚无历史变更记录</p> : (
                            <ul className="history-list">
                              {historyMap[t.id].map((h, i) => (
                                <li key={i}>
                                  <span className="hist-action">{h.action}</span>
                                  {h.before && <span className="hist-val"> ({h.before} → {h.after})</span>}
                                  <span className="hist-actor">操作人: {h.actor}</span>
                                  <span className="hist-time">{new Date(h.created_at).toLocaleString('zh-CN')}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
