import React, { useState, useEffect } from 'react'
import { listPairs, listDocuments, listAlignments, updateAlignment, type CorpusPair, type CorpusDocument, type Alignment } from '../api'

export default function CorpusAdmin() {
  const [pairs, setPairs] = useState<CorpusPair[]>([])
  const [docs, setDocs] = useState<CorpusDocument[]>([])
  const [alignments, setAlignments] = useState<Alignment[]>([])
  const [selectedPairId, setSelectedPairId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false), [loadingAlign, setLoadingAlign] = useState(false)
  const [error, setError] = useState<string | null>(null)
  
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editZh, setEditZh] = useState(''), [editEn] = useState('')
  const [editEnVal, setEditEnVal] = useState('') // renamed to avoid shadowing or collision

  useEffect(() => {
    const initData = async () => {
      setLoading(true); setError(null)
      try {
        const [pRes, dRes] = await Promise.all([listPairs(), listDocuments()])
        setPairs(pRes.pairs || []); setDocs(dRes.documents || [])
        if (pRes.pairs?.length > 0) setSelectedPairId(pRes.pairs[0].id)
      } catch (err) { setError(err instanceof Error ? err.message : '加载语料失败') }
      finally { setLoading(false) }
    }
    initData()
  }, [])

  useEffect(() => {
    if (!selectedPairId) { setAlignments([]); return }
    const fetchAligns = async () => {
      setLoadingAlign(true); setError(null)
      try { setAlignments((await listAlignments(selectedPairId)).alignments || []) }
      catch (err) { setError(err instanceof Error ? err.message : '加载句对对齐失败') }
      finally { setLoadingAlign(false) }
    }
    fetchAligns()
  }, [selectedPairId])

  const docMap = new Map(docs.map(d => [d.id, d]))

  const handleStatusUpdate = async (id: string, status: 'approved' | 'rejected', zh?: string, en?: string) => {
    setError(null)
    try {
      const res = await updateAlignment(id, { status, zh_text: zh, en_text: en })
      setAlignments(prev => prev.map(a => a.id === id ? { ...a, status: res.status, zh_text: zh ?? a.zh_text, en_text: en ?? a.en_text } : a))
      setEditingId(null)
    } catch (err) { setError(err instanceof Error ? err.message : '更新句对状态失败') }
  }

  const startEdit = (a: Alignment) => {
    setEditingId(a.id)
    setEditZh(a.zh_text)
    setEditEnVal(a.en_text)
  }

  return (
    <div className="corpus-admin-layout">
      {error && <div role="alert" className="error-banner">{error}</div>}
      {loading ? <div className="loading-state">载入中...</div> : (
        <div className="corpus-columns">
          {/* 左侧 Pair 列表 */}
          <div className="corpus-left-col">
            <h3>双语文档关联对 ({pairs.length})</h3>
            {pairs.length === 0 ? <div className="panel-empty">暂无关联文档对</div> : (
              <div className="pair-list-scroll">
                {pairs.map(p => {
                  const zhTitle = docMap.get(p.zh_doc_id)?.title || p.zh_doc_id
                  const enTitle = docMap.get(p.en_doc_id)?.title || p.en_doc_id
                  return (
                    <button key={p.id} onClick={() => setSelectedPairId(p.id)} className={`pair-item-card ${selectedPairId === p.id ? 'active' : ''}`}>
                      <div className="pair-titles">
                        <div className="p-title zh">🇨🇳 {zhTitle}</div>
                        <div className="p-title en">🇺🇸 {enTitle}</div>
                      </div>
                      <div className="pair-meta">
                        <span>匹配方法: {p.match_method}</span>
                        <span className="conf-badge">置信度: {(p.match_confidence * 100).toFixed(0)}%</span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* 右侧 Alignments 列表 */}
          <div className="corpus-right-col">
            <h3>对齐句段校对</h3>
            {loadingAlign ? <div className="loading-state">正在加载对齐句段...</div> : alignments.length === 0 ? <div className="panel-empty">请选择左侧关联对以展示句对</div> : (
              <div className="alignment-list-container">
                {alignments.map(a => {
                  const isEditing = editingId === a.id
                  const isApproved = a.status === 'approved'
                  const isRejected = a.status === 'rejected'
                  return (
                    <div key={a.id} className={`alignment-item-row status-${a.status}`}>
                      <div className="align-meta-tag">
                        <span className="idx-tag">#{a.idx}</span>
                        <span className={`score-badge ${a.score > 0.8 ? 'high' : 'low'}`}>Score: {a.score.toFixed(2)}</span>
                        {a.status && <span className={`status-tag status-${a.status}`}>{a.status === 'approved' ? '已批准' : a.status === 'rejected' ? '已拒绝' : '待审核'}</span>}
                      </div>

                      {isEditing ? (
                        <div className="align-edit-fields">
                          <textarea className="align-edit-textarea" value={editZh} onChange={e => setEditZh(e.target.value)} />
                          <textarea className="align-edit-textarea" value={editEnVal} onChange={e => setEditEnVal(e.target.value)} />
                          <div className="align-edit-actions">
                            <button onClick={() => handleStatusUpdate(a.id, 'approved', editZh, editEnVal)} className="btn-save primary">提交修正并批准</button>
                            <button onClick={() => setEditingId(null)} className="btn-cancel">取消</button>
                          </div>
                        </div>
                      ) : (
                        <div className="align-text-compare">
                          <div className={`align-lang-box zh ${isRejected ? 'text-rejected' : ''} ${isApproved ? 'text-approved' : ''}`}>{a.zh_text}</div>
                          <div className={`align-lang-box en ${isRejected ? 'text-rejected' : ''} ${isApproved ? 'text-approved' : ''}`}>{a.en_text}</div>
                        </div>
                      )}

                      {!isEditing && (
                        <div className="align-row-actions">
                          <button onClick={() => handleStatusUpdate(a.id, 'approved')} disabled={isApproved} className="action-btn approve-btn">批准</button>
                          <button onClick={() => handleStatusUpdate(a.id, 'rejected')} disabled={isRejected} className="action-btn reject-btn">拒绝</button>
                          <button onClick={() => startEdit(a)} className="action-btn edit-btn">修正</button>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
