import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Mic } from 'lucide-react'
import './ServerMemory.css'

const API = '/teach-api'

type Session = {
  id: string
  kp: string
  course_name: string
  asr: string
  conclusion: string
  ts: number
}

export function ServerMemory() {
  const [items, setItems] = useState<Session[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const response = await fetch(`${API}/teach/sessions?limit=20`)
        if (!response.ok) return
        const data = await response.json()
        if (alive) setItems(Array.isArray(data.items) ? data.items : [])
      } catch (cause) {
        if (alive) setError((cause as Error).message)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const rows = items
    .filter(
      (item) =>
        item.asr &&
        !/没听清|没识别/.test(item.asr) &&
        !/^[\s?？!！。.,，]+$/.test(item.asr) &&
        item.asr.replace(/[^一-龥A-Za-z0-9]/g, '').length >= 2,
    )
    .slice(0, 5)

  return (
    <section className="panel server-memory" aria-label="课堂语音记忆">
      <div className="section-top">
        <h2><Mic size={17} /> 课堂语音记忆</h2>
        <Link to="/second-brain">第二大脑 <ArrowRight size={13} /></Link>
      </div>
      <p className="muted">
        来自硬件或课堂采集的语音转写；只显示听清楚的记录，没听清的不进这里，也不进知识库。
      </p>
      {error && <p className="error">{error}</p>}
      <div className="server-memory-list">
        {rows.map((item) => (
          <article key={item.id}>
            <div>
              <b>{item.kp || item.course_name || '学习记录'}</b>
              <small>
                {item.ts ? new Date(item.ts * 1000).toLocaleString('zh-CN') : ''}
                {item.course_name ? ` · ${item.course_name}` : ''}
              </small>
            </div>
            <p>{item.asr}</p>
            {item.conclusion && <p className="muted">{item.conclusion}</p>}
          </article>
        ))}
        {!rows.length && (
          <p className="muted">
            还没有有效语音记录。按板子上的 BOOT 说一句，或在问答页点数据库图标「存为记忆」。
          </p>
        )}
      </div>
    </section>
  )
}
