import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, HeartPulse, Lightbulb, ListChecks, Sparkles, UserRound } from 'lucide-react'
import { Heading } from '../components/Workspace'
import './GrowthModules.css'

const API = '/teach-api'

type Mood = { id: number; text: string; time: string }
type Insight = {
  id: number
  insight_id?: string
  title: string
  description: string
  suggested_actions?: string[] | string
  date?: string
  kind?: string
  meta?: { category?: string }
}
type Todo = { id: number; text: string; due?: string; kp?: string }

function moodLabel(text: string) {
  const match = text.match(/^\[(.+?)\]/)
  return match ? match[1] : '状态'
}

function useGrowthData() {
  const [moods, setMoods] = useState<Mood[]>([])
  const [insights, setInsights] = useState<Insight[]>([])
  const [daily, setDaily] = useState<Insight[]>([])
  const [todos, setTodos] = useState<Todo[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [moodRes, insightRes, todoRes] = await Promise.all([
          fetch(`${API}/teach/mood`),
          fetch(`${API}/insights`),
          fetch(`${API}/todos?status=open`),
        ])
        if (moodRes.ok) {
          const data = await moodRes.json()
          if (alive) setMoods(Array.isArray(data.items) ? data.items : [])
        }
        if (insightRes.ok) {
          const data = await insightRes.json()
          if (alive) {
            setInsights(Array.isArray(data.insights) ? data.insights : [])
            setDaily(Array.isArray(data.daily) ? data.daily : [])
          }
        }
        if (todoRes.ok) {
          const data = await todoRes.json()
          if (alive) setTodos(Array.isArray(data.todos) ? data.todos : [])
        }
      } catch (cause) {
        if (alive) setError((cause as Error).message || '成长数据暂时无法读取')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  return { moods, insights, daily, todos, error, loading }
}

function InsightCard({ item }: { item: Insight }) {
  const actions = Array.isArray(item.suggested_actions)
    ? item.suggested_actions
    : item.suggested_actions
      ? [item.suggested_actions]
      : []
  return (
    <article className="growth-item">
      <div className="growth-item-title">
        <Sparkles size={15} />
        <b>{item.title || '一条成长线索'}</b>
        {item.date && <small>{item.date}</small>}
      </div>
      <p>{item.description}</p>
      {actions.length > 0 && <p className="growth-next">下一步：{actions.join('；')}</p>}
    </article>
  )
}

export function MindGrowthPage() {
  const { moods, insights, error, loading } = useGrowthData()
  const counts = moods.reduce<Record<string, number>>((acc, item) => {
    const label = moodLabel(item.text)
    acc[label] = (acc[label] || 0) + 1
    return acc
  }, {})
  const max = Math.max(...Object.values(counts), 1)
  const mindInsights = insights
    .filter((item) => /情绪|焦虑|疲惫|压力|状态|心态|负担|休息|累/.test(`${item.title} ${item.description}`))
    .slice(0, 3)

  return (
    <>
      <Heading title="心理成长" subtitle="看负荷怎么变化；一次心情不当作结论。" />
      {error && <section className="panel"><p className="error">{error}</p></section>}
      {loading && <section className="panel"><p className="muted">正在读取心境记录…</p></section>}
      <div className="growth-stat-row">
        <article className="panel growth-stat"><HeartPulse size={20} /><b>{moods.length}</b><span>条心境记录</span></article>
        <article className="panel growth-stat"><Lightbulb size={20} /><b>{mindInsights.length}</b><span>条相关线索</span></article>
        <article className="panel growth-stat"><Sparkles size={20} /><b>{moods[0]?.time || '—'}</b><span>最近一次记录</span></article>
      </div>
      <section className="panel">
        <div className="section-top"><h2>状态分布</h2><span className="muted">按你自己选的标签统计</span></div>
        {Object.keys(counts).length ? (
          <div className="mood-bars">
            {Object.entries(counts).map(([label, count]) => (
              <article key={label}>
                <span>{label}</span>
                <div><i style={{ width: `${Math.round((count / max) * 100)}%` }} /></div>
                <b>{count}</b>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">还没有心境记录。去第二大脑写一句“今天有点累/卡在哪”。</p>
        )}
        <Link className="secondary" to="/second-brain">去记一笔今天的状态 <ArrowRight size={14} /></Link>
      </section>
      <section className="panel">
        <h2>和负荷有关的线索</h2>
        <p className="muted">从真实记录里按关键词筛出来的，不是模型给你贴的心理标签。</p>
        {mindInsights.map((item) => <InsightCard key={item.insight_id || item.id} item={item} />)}
        {!mindInsights.length && <p className="muted">暂时没有相关线索。多记几次状态后，这里会出现模式。</p>}
      </section>
    </>
  )
}

export function PersonalGrowthPage() {
  const { insights, daily, todos, error, loading } = useGrowthData()
  const growInsights = insights
    .filter((item) => item.meta?.category === 'grow' || item.kind === 'insight')
    .slice(0, 4)

  return (
    <>
      <Heading title="个人成长" subtitle="从课堂、项目和日常记录里长出来的模式与下一步。" />
      {error && <section className="panel"><p className="error">{error}</p></section>}
      {loading && <section className="panel"><p className="muted">正在读取成长记录…</p></section>}
      <div className="growth-stat-row">
        <article className="panel growth-stat"><Sparkles size={20} /><b>{growInsights.length}</b><span>条成长洞察</span></article>
        <article className="panel growth-stat"><UserRound size={20} /><b>{daily.length}</b><span>天每日回顾</span></article>
        <article className="panel growth-stat"><ListChecks size={20} /><b>{todos.length}</b><span>条未完成待办</span></article>
      </div>
      <section className="panel">
        <h2>成长洞察</h2>
        <p className="muted">来自 Bee 的 Grow 输出和真实记录；建议只作为下一步的候选。</p>
        {growInsights.map((item) => <InsightCard key={item.insight_id || item.id} item={item} />)}
        {!growInsights.length && <p className="muted">还没有成长洞察。先积累几条课堂或项目记录。</p>}
      </section>
      <section className="panel">
        <div className="section-top"><h2>每日回顾</h2><Link className="secondary" to="/second-brain">看第二大脑 <ArrowRight size={14} /></Link></div>
        {daily.slice(0, 4).map((item) => (
          <article className="growth-item" key={item.insight_id || item.id}>
            <div className="growth-item-title"><b>{item.date || '每日回顾'}</b></div>
            <p>{item.description}</p>
          </article>
        ))}
        {!daily.length && <p className="muted">还没有每日回顾。</p>}
      </section>
      <section className="panel">
        <div className="section-top"><h2>下一步</h2><Link className="primary" to="/list">去看待办 <ArrowRight size={14} /></Link></div>
        {todos.slice(0, 5).map((todo) => (
          <article className="growth-todo" key={todo.id}>
            <span>{todo.text}</span>
            <small>{todo.due || '未设定时间'}</small>
          </article>
        ))}
        {!todos.length && <p className="muted">没有未完成待办。</p>}
      </section>
    </>
  )
}
