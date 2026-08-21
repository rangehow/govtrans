import { useState } from 'react'
import type { Confidentiality } from '../types'

interface Props {
  busy: boolean
  onSubmit: (text: string, confidentiality: Confidentiality) => void
}

export default function TranslateInput({ busy, onSubmit }: Props) {
  const [text, setText] = useState('')
  const [confidentiality, setConfidentiality] = useState<Confidentiality>('PUBLIC')

  return (
    <section className="translate-input" aria-label="原文输入">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="粘贴中文政务公文原文，例如：推动高质量发展，加快构建新发展格局。"
        rows={6}
        disabled={busy}
      />
      <div className="input-toolbar">
        <label>
          机密分级：
          <select
            value={confidentiality}
            onChange={(e) => setConfidentiality(e.target.value as Confidentiality)}
            disabled={busy}
          >
            <option value="PUBLIC">公开</option>
            <option value="INTERNAL">内部</option>
            <option value="CONFIDENTIAL">机密（禁用外网检索）</option>
          </select>
        </label>
        <button
          className="primary"
          disabled={busy || text.trim().length === 0}
          onClick={() => onSubmit(text.trim(), confidentiality)}
        >
          {busy ? '翻译中…' : 'Translate'}
        </button>
      </div>
    </section>
  )
}
