import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Brain, Compass, Library, ListChecks, MessageCircle, Sparkles } from 'lucide-react'
import { useAppState } from '../context/AppState'
import './HomeDashboard.css'

const TEACH = '/teach-api'
const OT = '/opentutor-api/api/workspace'

type Dash = {
  facts: number
  insights: number
  moods: number
  latestMood: string
  openTodos: number
  firstTodo: string
  activeKp: string
  errorType: string
  strategy: string
  strategyVersion: string
  courseCount: number
  evidenceCount: number
  coverage: number
  balance: number
  verdict: string
  version: string
}

const EMPTY: Dash = {
  facts: 0,
  insights: 0,
  moods: 0,
  latestMood: '',
  openTodos: 0,
  firstTodo: '',
  activeKp: '',
  errorType: '',
  strategy: '',
  strategyVersion: '',
  courseCount: 0,
  evidenceCount: 0,
  coverage: 0,
  balance: 0,
  verdict: '',
  version: '',
}

async function safe(url: string) {
  try {
    const response = await fetch(url)
    return response.ok ? response.json() : null
  } catch {
    return null
  }
}

export function HomeDashboard() {
  const { conversations } = useAppState()
  const [data, setData] = useState<Dash>(EMPTY)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      const [facts, insights, todos, context, pivots, courses, moods, overview] = await Promise.all([
        safe(`${TEACH}/facts`),
        safe(`${TEACH}/insights`),
        safe(`${TEACH}/todos?status=open`),
        safe(`${TEACH}/teach/context`),
        safe(`${TEACH}/teach/pivots`),
        safe(`${TEACH}/teach/courses`),
        safe(`${TEACH}/teach/mood`),
        safe(`${OT}/overview`),
      ])
      if (!alive) return
      const evaluation = overview?.evaluations?.[0]
      const v2 = (overview?.versions || []).find(
        (row: any) => row.version === 2 && row.status === 'active',
      )
      const courseRows = Array.isArray(courses?.courses) ? courses.courses : []
      const moodRows = Array.isArray(moods?.items) ? moods.items : []
      setData({
        facts: facts?.count ?? 0,
        insights: insights?.count ?? 0,
        moods: moods?.count ?? moodRows.length,
        latestMood: moodRows[0]?.text || '',
        openTodos: todos?.counts?.open ?? 0,
        firstTodo: todos?.todos?.[0]?.text || '',
        activeKp: context?.active_kp || '',
        errorType: context?.error_type || '',
        strategy: context?.strategy || '',
        strategyVersion: context?.strategy_version || '',
        courseCount: courseRows.length,
        evidenceCount: courseRows.reduce((sum: number, row: any) => sum + (row.evidence || 0), 0),
        coverage: pivots?.coverage ?? 0,
        balance: pivots?.balance ?? 0,
        verdict: evaluation?.verdict || '',
        version: v2 ? `V${v2.version}` : '',
      })
      setLoaded(true)
    })()
    return () => {
      alive = false
    }
  }, [])

  if (!loaded) return null

  return (
    <section className="home-dashboard" aria-label="今日驾驶舱">
      <div className="section-top">
        <h2>今日驾驶舱</h2>
        <span className="muted">来自各页面的真实数据，点卡片进入</span>
      </div>
      <div className="dashboard-grid">
        <Link className="panel dash-card" to="/second-brain">
          <Brain size={18} />
          <b>{data.facts}</b>
          <span>条事实 · {data.insights} 条洞察</span>
          <p>{data.latestMood || '还没有记今天的状态'}</p>
        </Link>
        <Link className="panel dash-card" to="/list">
          <ListChecks size={18} />
          <b>{data.openTodos}</b>
          <span>项未完成待办</span>
          <p>{data.firstTodo || '暂时没有待办'}</p>
        </Link>
        <Link className="panel dash-card" to="/agent">
          <MessageCircle size={18} />
          <b>{data.activeKp || '—'}</b>
          <span>{data.errorType ? `错因：${data.errorType}` : '学习助手'}</span>
          <p>
            {data.strategyVersion
              ? `策略 ${data.strategyVersion} · ${data.strategy}`
              : '去问一个问题，或拍/录一段'}
          </p>
        </Link>
        <Link className="panel dash-card" to="/knowledge">
          <Library size={18} />
          <b>{data.courseCount}</b>
          <span>门课程 · {data.evidenceCount} 条课堂证据</span>
          <p>课程资料、知识子图与自测证据</p>
        </Link>
        <Link className="panel dash-card" to="/grow">
          <Compass size={18} />
          <b>{Math.round(data.coverage * 100)}%</b>
          <span>支点覆盖 · 均衡 {Math.round(data.balance * 100)}%</span>
          <p>学习、心理、个人、人生支点</p>
        </Link>
        <Link className="panel dash-card" to="/learner-model">
          <Activity size={18} />
          <b>{data.version || '—'}</b>
          <span>{data.verdict ? `最近评价：${data.verdict}` : '自进化证据'}</span>
          <p>策略版本、两轮对比与证据链</p>
        </Link>
        <Link className="panel dash-card" to="/summaries">
          <Sparkles size={18} />
          <b>{conversations.length}</b>
          <span>段个人记忆</span>
          <p>课堂、生活与项目记录</p>
        </Link>
      </div>
    </section>
  )
}
