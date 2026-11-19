import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowRight, Brain, GitBranch, RefreshCw, RotateCcw, ShieldCheck, Sparkles } from 'lucide-react'
import './LearnerModelPage.css'

const API = '/teach-api'
const WORKSPACE_API = '/opentutor-api/api/workspace'

type Profile = {
  baseline: boolean
  student?: { name?: string; stage?: string; days_left?: number }
  fingerprint: { type: string; count: number; trend: string; note: string }[]
  mastery: { kp: string; value: number }[]
  solo: {
    current: number
    name: string
    desc: string
    evidence: string
    target: number
    target_name: string
    target_desc: string
    per_kp: { kp: string; level: number; n: number; rate: number }[]
  }
}
type Graph = {
  course_id: string
  course_name: string
  nodes: { id: string; label: string; conf: number; status: string; seen: number }[]
  edges: { from: string; to: string }[]
}
type Version = {
  id: string
  title: string
  status: string
  kind: string
  error_type?: string
  strategy?: string
  metrics?: { old: number | null; new: number | null; n: number } | null
  diff?: { ctx?: string[]; del?: string[]; add?: string[] }
  time?: string
  reason?: string
}
type Session = {
  id: string
  course_id: string
  course_name: string
  kp: string
  error_type: string
  strategy: string
  correct: number
  total: number
  ts: number
}
type Course = { id: string; name: string; type: string; evidence: number }
type UnifiedItem = {
  source: string
  id: string
  name: string
  version: number | string
  status: string
  change_summary?: string
  strategy?: string
  error_type?: string
  metrics?: { old: number | null; new: number | null; n: number } | null
  created_at?: string
}
type Unified = {
  storage_merged: boolean
  note: string
  sources: { opentutor: number; teaching_5001: number }
  teaching_error?: string | null
  items: UnifiedItem[]
  evidence_chain: { step: string; run: string; id: string; detail: string; at?: string }[]
  honesty: {
    scores_tied: boolean
    prompt_injection_verified: boolean | null
    execution_state: string | null
    learning_gain_claimed: boolean
  }
}

const SOLO_NAMES = ['前结构', '单点结构', '多点结构', '关联结构', '抽象扩展']
const pct = (value: number | null | undefined) =>
  value == null ? '—' : Math.round(value * 100) + '%'

function graphLayout(graph: Graph) {
  const adjacency = new Map<string, string[]>()
  graph.nodes.forEach((node) => adjacency.set(node.id, []))
  graph.edges.forEach((edge) => {
    if (adjacency.has(edge.from) && adjacency.has(edge.to)) {
      adjacency.get(edge.from)!.push(edge.to)
      adjacency.get(edge.to)!.push(edge.from)
    }
  })
  const root = graph.nodes.find((node) => node.id === 'root') || graph.nodes[0]
  const depth = new Map<string, number>()
  const queue: string[] = []
  if (root) {
    depth.set(root.id, 0)
    queue.push(root.id)
  }
  while (queue.length) {
    const current = queue.shift()!
    const nextDepth = (depth.get(current) || 0) + 1
    ;(adjacency.get(current) || []).forEach((next) => {
      if (!depth.has(next)) {
        depth.set(next, nextDepth)
        queue.push(next)
      }
    })
  }
  const groups = new Map<number, string[]>()
  graph.nodes.forEach((node) => {
    const level = depth.get(node.id) ?? 3
    const list = groups.get(level) || []
    list.push(node.id)
    groups.set(level, list)
  })
  const width = 760
  const height = 430
  const center = { x: width / 2, y: height / 2 }
  const positions = new Map<string, { x: number; y: number }>()
  ;[...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .forEach(([level, ids]) => {
      if (level === 0) {
        ids.forEach((id) => positions.set(id, center))
        return
      }
      const radius = 92 + (level - 1) * 105
      ids.forEach((id, index) => {
        const angle = (Math.PI * 2 * index) / ids.length - Math.PI / 2 + level * 0.35
        positions.set(id, {
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
        })
      })
    })
  return { positions, width, height }
}

export function LearnerModelPage() {
  const [courses, setCourses] = useState<Course[]>([])
  const [courseId, setCourseId] = useState('math')
  const [profile, setProfile] = useState<Profile | null>(null)
  const [graph, setGraph] = useState<Graph | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [latest, setLatest] = useState<{ session_id?: string; kp?: string; error_type?: string } | null>(null)
  const [unified, setUnified] = useState<Unified | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [coursesRes, profileRes, evolutionRes, sessionsRes, latestRes, graphRes, unifiedRes] = await Promise.all([
        fetch(`${API}/teach/courses`),
        fetch(`${API}/teach/profile`),
        fetch(`${API}/teach/evolution`),
        fetch(`${API}/teach/sessions?limit=40`),
        fetch(`${API}/teach/session/latest`),
        fetch(`${API}/teach/graph/${encodeURIComponent(courseId)}`),
        fetch(`${WORKSPACE_API}/strategies/unified`),
      ])
      if (!coursesRes.ok || !profileRes.ok) throw new Error('5001 教学服务暂未启动')
      const courseData = await coursesRes.json()
      setCourses(Array.isArray(courseData.courses) ? courseData.courses : [])
      setProfile(await profileRes.json())
      if (evolutionRes.ok) {
        const evolution = await evolutionRes.json()
        setVersions(
          (Array.isArray(evolution.versions) ? evolution.versions : []).filter(
            (item: Version) => item.kind === 'strategy',
          ),
        )
      }
      if (sessionsRes.ok) {
        const data = await sessionsRes.json()
        setSessions(Array.isArray(data.items) ? data.items : [])
      }
      if (latestRes.ok) setLatest(await latestRes.json())
      if (graphRes.ok) setGraph(await graphRes.json())
      if (unifiedRes.ok) setUnified(await unifiedRes.json())
    } catch (cause) {
      setError((cause as Error).message || '5001 教学服务暂未启动')
    } finally {
      setLoading(false)
    }
  }, [courseId])

  useEffect(() => {
    void load()
  }, [load])

  const layout = useMemo(() => (graph ? graphLayout(graph) : null), [graph])
  const courseSessions = useMemo(
    () => sessions.filter((item) => item.course_id === courseId && item.total > 0).slice(0, 14).reverse(),
    [sessions, courseId],
  )
  const activeVersion = versions.find((item) => item.status === 'active')

  async function rollback(id: string) {
    if (!window.confirm(`把课堂策略回滚到 ${id}？历史记录不会删除。`)) return
    setBusy(id)
    setError('')
    setNotice('')
    try {
      const response = await fetch(`${API}/teach/evolution/${encodeURIComponent(id)}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      const data = await response.json()
      if (!response.ok || data.ok === false) throw new Error(data.error || '回滚失败')
      setNotice(`已回滚到 ${id}，下一轮教学会用这个版本。`)
      await load()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy('')
    }
  }

  async function retryQuiz() {
    if (!latest?.session_id) {
      setError('还没有可复测的课堂记录，先去教学 Agent 做一次分析。')
      return
    }
    setBusy('retry')
    setError('')
    setNotice('')
    try {
      const response = await fetch(`${API}/teach/quiz/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: latest.session_id }),
      })
      const data = await response.json()
      if (!response.ok || data.ok === false) throw new Error(data.error || '复测创建失败')
      setNotice('已生成一套同错因的新题。回教学页做完这 3 题，答对才算稳定，避免只是记住原题答案。')
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy('')
    }
  }

  return (
    <>
      <section className="panel model-hero">
        <div>
          <span className="eyebrow">LEARNER MODEL · SOLO + 错因 + 知识图谱</span>
          <h1>学习画像</h1>
          <p className="muted">
            这里全部来自真实作答与课堂证据：SOLO 理解结构、六类错因、知识点掌握度、
            课程子图和策略版本。没有作答的知识点会明确显示“未评估”。
          </p>
        </div>
        <div className="model-actions">
          <select
            aria-label="选择课程"
            value={courseId}
            onChange={(event) => setCourseId(event.target.value)}
          >
            {courses.map((course) => (
              <option key={course.id} value={course.id}>
                {course.name}（证据 {course.evidence}）
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={15} /> {loading ? '读取中…' : '刷新'}
          </button>
        </div>
      </section>

      {error && <section className="panel"><p className="error">{error}</p></section>}
      {notice && <section className="panel"><p className="notice">{notice}</p></section>}

      {profile && (
        <>
          <section className="panel solo-card">
            <div className="solo-head">
              <div>
                <span className="eyebrow">SOLO 理解结构</span>
                <h2>
                  {profile.solo.current}/5 · {profile.solo.name}
                </h2>
                <p className="muted">{profile.solo.desc}</p>
              </div>
              <div className="solo-target">
                <small>下一步目标</small>
                <b>
                  {profile.solo.target}/5 · {profile.solo.target_name}
                </b>
                <span>{profile.solo.target_desc}</span>
              </div>
            </div>
            <div className="solo-meter" role="meter" aria-valuenow={profile.solo.current} aria-valuemin={1} aria-valuemax={5}>
              {SOLO_NAMES.map((name, index) => (
                <span key={name} className={index + 1 <= profile.solo.current ? 'on' : ''}>
                  {index + 1} {name}
                </span>
              ))}
            </div>
            <p className="solo-evidence">依据：{profile.solo.evidence}</p>
            <div className="solo-per-kp">
              {profile.solo.per_kp.map((item) => (
                <div key={item.kp}>
                  <span>{item.kp}</span>
                  <b>L{item.level}</b>
                  <small>
                    {item.n} 题 · 正确率 {pct(item.rate)}
                  </small>
                </div>
              ))}
            </div>
          </section>

          <div className="model-grid">
            <section className="panel">
              <h2>六类错因</h2>
              <p className="muted">错因是固定六类，不自由命名，才能跨课程聚合。</p>
              {profile.fingerprint.map((item) => {
                const max = Math.max(...profile.fingerprint.map((row) => row.count), 1)
                return (
                  <article className="model-bar" key={item.type}>
                    <div>
                      <b>{item.type}</b>
                      <small>{item.note}</small>
                    </div>
                    <div className="model-bar-track">
                      <span style={{ width: `${Math.round((item.count / max) * 100)}%` }} />
                    </div>
                    <span className="model-bar-value">
                      {item.count} 次 · {item.trend === 'down' ? '下降' : item.trend === 'up' ? '上升' : '持平'}
                    </span>
                  </article>
                )
              })}
            </section>

            <section className="panel">
              <h2>知识点掌握度</h2>
              <p className="muted">只有真实作答过的知识点才有数值。</p>
              {profile.mastery.length ? (
                profile.mastery.map((item) => (
                  <article className="model-bar" key={item.kp}>
                    <div>
                      <b>{item.kp}</b>
                    </div>
                    <div className="model-bar-track">
                      <span style={{ width: `${Math.round(item.value * 100)}%` }} />
                    </div>
                    <span className="model-bar-value">{pct(item.value)}</span>
                  </article>
                ))
              ) : (
                <p className="muted">还没有可计算的掌握度。</p>
              )}
            </section>
          </div>
        </>
      )}

      <section className="panel graph-panel">
        <div className="section-top">
          <h2>
            <GitBranch size={18} /> {graph?.course_name || '课程'}知识子图
          </h2>
          <span className="muted">节点大小=置信度，红色=冲突，黄色=待确认</span>
        </div>
        {graph && layout ? (
          <svg className="kg-svg" viewBox={`0 0 ${layout.width} ${layout.height}`} role="img" aria-label="课程知识图谱">
            {graph.edges.map((edge, index) => {
              const from = layout.positions.get(edge.from)
              const to = layout.positions.get(edge.to)
              if (!from || !to) return null
              return <line key={index} x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="kg-edge" />
            })}
            {graph.nodes.map((node) => {
              const position = layout.positions.get(node.id)
              if (!position) return null
              const radius = 7 + Math.max(0, Math.min(1, node.conf)) * 11
              const fill =
                node.status === 'conflict' ? '#e05a5a' : node.status === 'pending' ? '#e0b23a' : '#7b4fd8'
              return (
                <g key={node.id}>
                  <circle cx={position.x} cy={position.y} r={radius} fill={fill} opacity={0.9}>
                    <title>{`${node.label} · 置信度 ${Math.round(node.conf * 100)}% · 证据 ${node.seen || 0} 条`}</title>
                  </circle>
                  <text x={position.x} y={position.y + radius + 15} textAnchor="middle" className="kg-label">
                    {node.label}
                  </text>
                </g>
              )
            })}
          </svg>
        ) : (
          <p className="muted">正在读取子图…</p>
        )}
      </section>

      <div className="model-grid">
        <section className="panel">
          <div className="section-top">
            <h2>
              <ShieldCheck size={18} /> 课堂策略版本
            </h2>
            <span className="muted">当前 {activeVersion ? activeVersion.id : '无'}</span>
          </div>
          <p className="muted">
            这里是 5001 课堂策略库。OpenTutor 的技能版本与它一起汇入统一 strategy_registry 读模型；两个引擎仍各自写源表，新版本通过 sync 汇入。
          </p>
          {versions.map((version) => (
            <article className={`strategy-row ${version.status === 'active' ? 'active' : ''}`} key={version.id}>
              <div className="strategy-line">
                <b>{version.id}</b>
                <span>{version.title}</span>
                <small>{version.status === 'active' ? '使用中' : '已停用'}</small>
              </div>
              <p>{version.reason || '没有留下理由。'}</p>
              {version.metrics && (
                <p className="strategy-metrics">
                  切换前正确率 {pct(version.metrics.old)} · 切换后{' '}
                  {version.metrics.new == null ? '未复测' : pct(version.metrics.new)} · 样本 {version.metrics.n}
                </p>
              )}
              {version.diff?.add?.length ? (
                <p className="strategy-add">新增：{version.diff.add.join('；')}</p>
              ) : null}
              {version.status !== 'active' && (
                <button className="secondary" disabled={busy === version.id} onClick={() => void rollback(version.id)}>
                  <RotateCcw size={14} /> 回滚到这个版本
                </button>
              )}
            </article>
          ))}
          {!versions.length && <p className="muted">还没有策略版本记录。</p>}
        </section>

        <section className="panel">
          <h2>
            <Activity size={18} /> 最近自测曲线
          </h2>
          <p className="muted">每根柱子是一次真实交卷；没做的记录不画。</p>
          {courseSessions.length ? (
            <div className="session-bars">
              {courseSessions.map((session) => {
                const rate = session.total ? session.correct / session.total : 0
                return (
                  <div className="session-bar" key={session.id}>
                    <div className="session-bar-track">
                      <span style={{ height: `${Math.max(6, Math.round(rate * 100))}%` }} />
                    </div>
                    <b>
                      {session.correct}/{session.total}
                    </b>
                    <small>{session.kp}</small>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="muted">这门课还没有交过卷。</p>
          )}
          <div className="sandbox-box">
            <b>认知对抗检查（最小版）</b>
            <p>
              同一错因换一批新题复测。答对才算稳定，用来发现“记住原题答案但没真懂”的假性掌握。
              它不是完整的对抗沙盒，样本也小。
            </p>
            <button className="primary" disabled={busy === 'retry'} onClick={() => void retryQuiz()}>
              {busy === 'retry' ? '正在出题…' : '换题复测 3 题'}
            </button>
            <Link className="secondary" to="/capture-agent">
              去教学页做题 <ArrowRight size={14} />
            </Link>
          </div>
        </section>
      </div>

      {unified && (
        <section className="panel chain-panel">
          <div className="section-top">
            <h2>证据链与双轨策略</h2>
            <span className="muted">{unified.storage_merged ? '统一 registry · 读合并' : '只读归一化视图 · 未合并存储'}</span>
          </div>
          <p className="muted">{unified.note}</p>
          <div className="chain-steps">
            {unified.evidence_chain.map((step, index) => (
              <article className="chain-step" key={step.step + step.id}>
                <span className="chain-index">{index + 1}</span>
                <div>
                  <b>{step.step}</b>
                  <small>{step.run}{step.at ? ` · ${step.at.slice(0, 19).replace('T', ' ')}` : ''}</small>
                  <p>{step.detail}</p>
                  <code>{step.id.slice(0, 8)}…</code>
                </div>
              </article>
            ))}
          </div>
          <div className="chain-honesty">
            <span>两轮分数：{unified.honesty.scores_tied ? '持平' : '有差异'}</span>
            <span>注入验证：{String(unified.honesty.prompt_injection_verified)}</span>
            <span>执行状态：{unified.honesty.execution_state || '未记录'}</span>
            <span>学习提升：未宣称</span>
          </div>
          <div className="chain-sources">
            <div>
              <h3>OpenTutor 技能版本（{unified.sources.opentutor}）</h3>
              {unified.items.filter((item) => item.source === 'opentutor').map((item) => (
                <article className="chain-item" key={item.id}>
                  <div className="chain-line"><b>{item.name}</b><small>v{item.version} · {item.status}</small></div>
                  <p>{item.change_summary || '没有留下修改说明。'}</p>
                </article>
              ))}
            </div>
            <div>
              <h3>5001 课堂策略（{unified.sources.teaching_5001}）</h3>
              {unified.items.filter((item) => item.source === 'teaching_5001').map((item) => (
                <article className="chain-item" key={item.id}>
                  <div className="chain-line"><b>{item.name}</b><small>{String(item.version)} · {item.status}</small></div>
                  <p>{item.change_summary || '没有留下修改说明。'}</p>
                  {item.metrics && (
                    <small>切换前 {pct(item.metrics.old)} · 切换后 {item.metrics.new == null ? '未复测' : pct(item.metrics.new)} · n={item.metrics.n}</small>
                  )}
                </article>
              ))}
            </div>
          </div>
          {unified.teaching_error && <p className="muted">5001 未连接：{unified.teaching_error}</p>}
        </section>
      )}
    </>
  )
}
