import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import './EmbedPage.css'

type Props = {
  title: string
  subtitle: string
  src: string
  hint?: string
  crossLink?: { label: string; to: string }
}

/**
 * Embeds one of the two existing web versions without rewriting it.
 *  - 5001 教学 Agent（拍照 / 录音 / 3 道自测 / 知识图谱 / 策略升版）
 *  - 5000 记忆站（Bee 风格）
 * The iframe keeps each version's own HTML/CSS/JS untouched; only the outer
 * navigation is unified.
 */
export function EmbedPage({ title, subtitle, src, hint, crossLink }: Props) {
  const [nonce, setNonce] = useState(0)
  return (
    <section className="panel embed-panel">
      <div className="embed-head">
        <div>
          <span className="eyebrow">整合运行 · 原页面未改写</span>
          <h1>{title}</h1>
          <p className="muted">{subtitle}</p>
        </div>
        <div className="embed-actions">
          {crossLink && (
            <Link className="primary" to={crossLink.to}>
              {crossLink.label}
            </Link>
          )}
          <button className="secondary" onClick={() => setNonce((n) => n + 1)}>
            <RefreshCw size={15} /> 重新载入
          </button>
          <a className="secondary" href={src} target="_blank" rel="noreferrer">
            新窗口打开 <ArrowUpRight size={15} />
          </a>
        </div>
      </div>
      {hint && <p className="embed-hint">{hint}</p>}
      <iframe
        key={nonce}
        className="embed-frame"
        src={src}
        title={title}
        allow="microphone; camera; clipboard-read; clipboard-write"
      />
    </section>
  )
}
