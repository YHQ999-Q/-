import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen, Camera, Database, RefreshCw } from 'lucide-react'
import { CourseLibrary } from './LearningPanels'
import '../pages/KnowledgePage.css'

const API = '/teach-api'

type Course = { id: string; name: string; type: string; evidence: number }
type Evidence = { text: string; kind_label?: string; t_label?: string; verify_status?: string }
type Node = {
  id: string
  label: string
  conf: number
  status: string
  seen: number
  evidence?: Evidence[]
}
type Graph = {
  course_id: string
  course_name: string
  nodes: Node[]
  edges: { from: string; to: string }[]
  evidence_count: number
  mode: string
}
type LearningEvidence = {
  id: number
  node_id: string
  kp: string
  source: string
  correct: number
  total: number
  error_type: string
  strategy_version: string
  note: string
  time: string
}

/**
 * 课程知识库：课程子图 + 课堂证据 + 自测反哺。
 * 从独立页面搬进「第二大脑」的一个标签里，路由 /knowledge 仍可用。
 */
export function KnowledgePanel({ embedded = false }: { embedded?: boolean }) {
  const [courses, setCourses] = useState<Course[]>([])
  const [courseId, setCourseId] = useState('')
  const [graph, setGraph] = useState<Graph | null>(null)
  const [evidence, setEvidence] = useState<LearningEvidence[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        // 先读“当前正在上的课”，再读课程列表：
        // 学习助手/课堂采集刚切到「模拟电子技术」，知识库就自动落在这一门。
        const [response, contextResponse] = await Promise.all([
          fetch(`${API}/teach/courses`),
          fetch(`${API}/teach/context`).catch(() => null),
        ])
        if (!response.ok) throw new Error('知识库暂时无法读取')
        const data = await response.json()
        const context = contextResponse && contextResponse.ok ? await contextResponse.json() : null
        const rows: Course[] = Array.isArray(data.courses) ? data.courses : []
        if (!alive) return
        setCourses(rows)
        const preferred =
          rows.find((row) => row.id === context?.course_id) ||
          rows.find((row) => row.evidence > 0) ||
          rows[0]
        setCourseId((current) => current || preferred?.id || '')
      } catch (cause) {
        if (alive) setError((cause as Error).message)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  async function loadGraph(id: string) {
    if (!id) return
    setLoading(true)
    setError('')
    try {
      const [graphRes, evidenceRes] = await Promise.all([
        fetch(`${API}/teach/graph/${encodeURIComponent(id)}`),
        fetch(`${API}/teach/evidence?course_id=${encodeURIComponent(id)}`),
      ])
      if (!graphRes.ok) throw new Error('这门课的知识子图暂时无法读取')
      setGraph(await graphRes.json())
      if (evidenceRes.ok) {
        const data = await evidenceRes.json()
        setEvidence(Array.isArray(data.items) ? data.items : [])
      }
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (courseId) void loadGraph(courseId)
  }, [courseId])

  const nodeStats = useMemo(() => {
    if (!graph) return { stable: 0, conflict: 0, pending: 0 }
    return graph.nodes.reduce(
      (acc, node) => {
        if (node.status === 'conflict') acc.conflict += 1
        else if (node.status === 'pending') acc.pending += 1
        else acc.stable += 1
        return acc
      },
      { stable: 0, conflict: 0, pending: 0 },
    )
  }, [graph])

  const evidenceByNode = useMemo(() => {
    const map: Record<string, number> = {}
    evidence.forEach((item) => {
      if (item.node_id) map[item.node_id] = (map[item.node_id] || 0) + 1
    })
    return map
  }, [evidence])

  return (
    <>
      <div className="section-top knowledge-panel-head">
        <div>
          <h2>{embedded ? '课程知识库' : '知识库'}</h2>
          <p className="muted">课程子图 + 课堂沉淀；生活记忆在下面，这里只放知识与证据。</p>
        </div>
        <button className="secondary" onClick={() => void loadGraph(courseId)} disabled={loading}>
          <RefreshCw size={15} /> {loading ? '读取中…' : '刷新'}
        </button>
      </div>

      {error && <section className="panel"><p className="error">{error}</p></section>}

      <CourseLibrary />

      <section className="panel">
        <div className="section-top">
          <h2><Database size={18} /> 课程</h2>
          <Link className="secondary" to="/capture-agent">去课堂采集 <Camera size={14} /></Link>
        </div>
        <div className="knowledge-courses">
          {courses.map((course) => (
            <button
              key={course.id}
              className={course.id === courseId ? 'active' : ''}
              onClick={() => setCourseId(course.id)}
            >
              <b>{course.name}</b>
              <small>{course.type} · {course.evidence} 条证据</small>
            </button>
          ))}
          {!courses.length && <p className="muted">还没有课程。第一次课堂采集后会自动出现。</p>}
        </div>
      </section>

      {graph && (
        <>
          <div className="knowledge-stats">
            <article className="panel knowledge-stat"><b>{graph.nodes.length}</b><span>知识点</span></article>
            <article className="panel knowledge-stat"><b>{graph.edges.length}</b><span>前置/关联</span></article>
            <article className="panel knowledge-stat"><b>{graph.evidence_count}</b><span>课堂证据</span></article>
            <article className="panel knowledge-stat"><b>{nodeStats.conflict + nodeStats.pending}</b><span>冲突/待确认</span></article>
          </div>

          <section className="panel">
            <div className="section-top">
              <h2><BookOpen size={18} /> {graph.course_name} · 知识子图</h2>
              <span className="muted">
                {graph.mode === 'live' ? '实时' : '只读'} · 稳定 {nodeStats.stable} / 冲突 {nodeStats.conflict} / 待确认 {nodeStats.pending}
              </span>
            </div>
            <div className="knowledge-nodes">
              {graph.nodes.map((node) => (
                <article className={`knowledge-node ${node.status}`} key={node.id}>
                  <div className="knowledge-node-head">
                    <b>{node.label}</b>
                    <span>{Math.round((node.conf || 0) * 100)}%</span>
                  </div>
                  <div className="knowledge-node-bar"><span style={{ width: `${Math.round((node.conf || 0) * 100)}%` }} /></div>
                  <small>
                    {node.seen || 0} 条课堂证据
                    {evidenceByNode[node.id] ? ` · ${evidenceByNode[node.id]} 条自测证据` : ''}
                    {node.status === 'conflict' ? ' · 有冲突，待确认' : node.status === 'pending' ? ' · 待确认' : ''}
                  </small>
                  {!!node.evidence?.length && (
                    <details>
                      <summary>看证据</summary>
                      <ul>
                        {node.evidence.slice(0, 3).map((item, index) => (
                          <li key={`${node.id}-${index}`}>
                            <span>{item.text}</span>
                            <small>
                              {item.kind_label || '证据'}
                              {item.t_label ? ` · ${item.t_label}` : ''}
                              {item.verify_status ? ` · ${item.verify_status}` : ''}
                            </small>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </article>
              ))}
              {!graph.nodes.length && <p className="muted">这门课还没有课堂证据。</p>}
            </div>
          </section>

          <section className="panel">
            <div className="section-top">
              <h2>自测证据（反哺）</h2>
              <span className="muted">交卷后自动写入</span>
            </div>
            <p className="muted">
              每次 3 题自测会把对错、错因、当时使用的策略写成一条证据，挂到对应知识点，并同步进变更记录。
              它不会直接改节点置信度——置信度仍由课堂证据和校验决定。
            </p>
            <div className="knowledge-evidence">
              {evidence.map((item) => (
                <article key={item.id}>
                  <div>
                    <b>{item.kp || '未命名知识点'}</b>
                    <small>{item.time} · {item.source}</small>
                  </div>
                  <span className={item.correct === item.total && item.total > 0 ? 'evidence-score good' : 'evidence-score'}>
                    {item.correct}/{item.total}
                  </span>
                  <small>
                    {item.error_type || '未标注错因'} · 策略 {item.strategy_version || '未记录'}
                    {item.node_id ? ` · 节点 ${item.node_id}` : ''}
                  </small>
                </article>
              ))}
              {!evidence.length && (
                <p className="muted">还没有自测证据。去课堂采集做一次 3 题自测。</p>
              )}
            </div>
          </section>

          <section className="panel knowledge-links">
            <div>
              <h2>知识库和第二大脑怎么分</h2>
              <p className="muted">
                课程知识库：课程知识点、前置关系、课堂证据（本标签）。<br />
                第二大脑：生活、项目、课堂摘要与事实洞察，不参与课程子图。<br />
                学习画像：你在这个知识库上的掌握度、错因和策略版本。
              </p>
            </div>
            <div className="actions">
              <Link className="secondary" to="/learner-model">去学习画像</Link>
            </div>
          </section>
        </>
      )}
    </>
  )
}
