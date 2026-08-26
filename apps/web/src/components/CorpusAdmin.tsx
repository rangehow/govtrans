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
  const [editZh, setEditZh] = useState('')
  const [editEnVal, setEditEnVal] = useState('')

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

  const handleStatusUpdate = async (id: string, status: 'auto' | 'approved' | 'rejected', zh?: string, en?: string) => {
    setError(null)
    try {
      const res = await updateAlignment(id, { status, zh_text: zh, en_text: en })
      setAlignments(prev => prev.map(a => {
        if (a.id !== id) return a
        const referenceTier = status === 'rejected'
          ? 'excluded'
          : status === 'approved'
            ? 'human_verified'
            : a.score >= 0.85 ? 'automatic' : 'archive_only'
        return {
          ...a,
          status: res.status,
          reference_tier: referenceTier,
          tm_entry_id: res.tm_entry_id,
          zh_text: zh ?? a.zh_text,
          en_text: en ?? a.en_text,
        }
      }))
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
      <div className="corpus-purpose-note">
        <strong>语料是可追溯的官方参考，不是第二个术语库，也没有发布步骤。</strong>
        对齐分 ≥ 85% 的官方句对自动成为翻译软参考；低置信句对只存档。人只在发现错误、需要排除，或必须例外启用低置信句对时介入，翻译永不等待人工审批。
      </div>
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
            <h3>官方参考状态</h3>
            {loadingAlign ? <div className="loading-state">正在加载对齐句段...</div> : alignments.length === 0 ? <div className="panel-empty">请选择左侧关联对以展示句对</div> : (
              <div className="alignment-list-container">
                {alignments.map(a => {
                  const isEditing = editingId === a.id
                  const isApproved = a.reference_tier === 'human_verified'
                  const isRejected = a.reference_tier === 'excluded'
                  const statusLabel = a.reference_tier === 'automatic'
                    ? '自动参考'
                    : a.reference_tier === 'human_verified'
                      ? '人工核验'
                      : a.reference_tier === 'archive_only' ? '仅存档' : '已排除'
                  return (
                    <div key={a.id} className={`alignment-item-row status-${a.reference_tier}`}>
                      <div className="align-meta-tag">
                        <span className="idx-tag">#{a.idx}</span>
                        <span className={`score-badge ${a.score > 0.8 ? 'high' : 'low'}`}>Score: {a.score.toFixed(2)}</span>
                        <span className={`status-tag status-${a.reference_tier}`}>{statusLabel}</span>
                      </div>

                      {isEditing ? (
                        <div className="align-edit-fields">
                          <textarea className="align-edit-textarea" value={editZh} onChange={e => setEditZh(e.target.value)} />
                          <textarea className="align-edit-textarea" value={editEnVal} onChange={e => setEditEnVal(e.target.value)} />
                          <div className="align-edit-actions">
                            <button onClick={() => handleStatusUpdate(a.id, 'approved', editZh, editEnVal)} className="btn-save primary">保存修正并标记人工核验</button>
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
                        <details className="alignment-governance">
                          <summary>发现问题或需要例外处理</summary>
                          <div className="align-row-actions">
                            {a.reference_tier === 'archive_only' && (
                              <button onClick={() => handleStatusUpdate(a.id, 'approved')} className="action-btn approve-btn">例外启用为高可信参考</button>
                            )}
                            {a.reference_tier === 'automatic' && (
                              <button onClick={() => handleStatusUpdate(a.id, 'approved')} className="action-btn approve-btn">标记已人工核验</button>
                            )}
                            {(isApproved || isRejected) && (
                              <button onClick={() => handleStatusUpdate(a.id, 'auto')} className="action-btn">恢复自动判断</button>
                            )}
                            {!isRejected && (
                              <button onClick={() => handleStatusUpdate(a.id, 'rejected')} className="action-btn reject-btn">排除错误句对</button>
                            )}
                            <button onClick={() => startEdit(a)} className="action-btn edit-btn">修正对齐或译文</button>
                          </div>
                        </details>
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
