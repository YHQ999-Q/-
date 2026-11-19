import { useEffect, useState } from 'react'
import { Compass, RefreshCw } from 'lucide-react'
import { Heading } from '../components/Workspace'
import './PivotGrowthPage.css'

const API = '/teach-api'

type PivotItem = { text: string; source: string; at: string; done?: boolean }
type Pivot = {
  id: string
  name: string
  count: number
  intensity: number
  latest: string
  last_at: string
  items: PivotItem[]
}
type Data = {
  pivots: Pivot[]
  coverage: number
  balance: number
  uncategorized: number
  formula: string
  note: string
}

export function PivotGrowthPage() {
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API}/teach/pivots`)
      if (!response.ok) throw new Error('支点数据暂时无法读取')
      setData(await response.json())
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <>
      <Heading title="人生支点" subtitle="不是你有多强，而是生活有多少个能接住你的地方。">
        <button className="secondary" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={15} /> {loading ? '读取中…' : '刷新'}
        </button>
      </Heading>

      {error && <section className="panel"><p className="error">{error}</p></section>}

      {data && (
        <>
          <div className="pivot-stats">
            <article className="panel pivot-stat">
              <Compass size={20} />
              <b>{Math.round(data.coverage * 100)}%</b>
              <span>支点覆盖度</span>
            </article>
            <article className="panel pivot-stat">
              <b>{Math.round(data.balance * 100)}%</b>
              <span>支点均衡度</span>
            </article>
            <article className="panel pivot-stat">
              <b>{data.uncategorized}</b>
              <span>未分类记录</span>
            </article>
          </div>

          <section className="pivot-grid">
            {data.pivots.map((pivot) => (
              <article className={`panel pivot-card ${pivot.count ? '' : 'empty'}`} key={pivot.id}>
                <div className="pivot-head">
                  <h2>{pivot.name}</h2>
                  <b>{pivot.count}</b>
                </div>
                <div className="pivot-bar" role="meter" aria-label={`${pivot.name}强度`} aria-valuenow={Math.round(pivot.intensity * 100)} aria-valuemin={0} aria-valuemax={100}>
                  <span style={{ width: `${Math.round(pivot.intensity * 100)}%` }} />
                </div>
                {pivot.latest ? (
                  <>
                    <p className="pivot-latest">{pivot.latest}</p>
                    <small>{pivot.last_at}</small>
                    {pivot.items.length > 1 && (
                      <details>
                        <summary>看这个支点的 {pivot.items.length} 条记录</summary>
                        <ul>
                          {pivot.items.map((item, index) => (
                            <li key={`${pivot.id}-${index}`}>
                              <span>{item.text}</span>
                              <small>{item.at} · {item.source}</small>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </>
                ) : (
                  <p className="muted">还没有记录。下一次随手记可能会落在这里。</p>
                )}
              </article>
            ))}
          </section>

          <section className="panel pivot-note">
            <h2>怎么算的</h2>
            <p>{data.note}</p>
            <p className="muted">{data.formula}</p>
            <p className="muted">
              它只反映“记录有没有覆盖到这些方面”，不代表心理健康、能力高低或人生质量。
              未分类记录不会硬塞进某个支点。
            </p>
          </section>
        </>
      )}
    </>
  )
}
