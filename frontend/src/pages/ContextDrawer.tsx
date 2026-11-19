import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { LiquidGraph } from './LiquidGraph'
import './ContextDrawer.css'

type DrawerType = 'compare' | 'chain' | 'graph'

type TeachingContext = {
  active_kp?: string
  node_id?: string
  error_type?: string
  strategy?: string
  strategy_version?: string
  course_id?: string
}

type Reference = { title: string; excerpt?: string; url?: string }

const EVOLUTION_NOTES = [
  {
    title: '比较什么指标',
    body:
      '本项目属于长周期教育应用，不以单轮问答正确率或即时满意度作为核心指标。' +
      '重点比较两次运行中：学习者状态识别、教学策略适配、引导式提问质量、学习反馈利用及策略更新情况。' +
      '长期效果需通过连续学习后的前后测、延迟测验、知识迁移和独立解决问题能力进行验证。' +
      '本次演示主要验证自进化机制，不夸大学习成效。',
  },
  {
    title: '学到了什么',
    body:
      'Agent 不只是记住对话内容，而是逐步积累学习者的知识状态、理解困难和教学偏好。' +
      '通过 Evaluator 评价学习表现，Reflection 反思教学策略，并将有效调整沉淀为可复用的 Teaching Skill，' +
      '使后续教学能够根据学习者的实际反馈持续调整。',
  },
  {
    title: '坏了怎么回退',
    body:
      '采用版本化管理机制，保留原有稳定策略和历史版本。新策略经过评价后再更新；' +
      '如果出现教学效果下降、策略异常或错误更新，可以回退至此前稳定版本，' +
      '避免错误策略持续影响后续教学，并保留历史学习记录。',
  },
]

function EvolutionNotes() {
  return (
    <section className="drawer-evo" aria-label="进化说明">
      <h3>进化说明</h3>
      {EVOLUTION_NOTES.map((note, index) => (
        <article key={note.title}>
          <b>{index + 1}. {note.title}</b>
          <p>{note.body}</p>
        </article>
      ))}
    </section>
  )
}

function short(value: string, limit: number) {
  return value.length > limit ? `${value.slice(0, limit)}…` : value
}

/**
 * In-place drawer for the Q&A page. No route change, no new page:
 *  - compare: the 10-step r1/r2 classroom comparison, scoped to the topic the
 *    student is asking about right now
 *  - chain: the evidence for *this* question (what was asked, which strategy
 *    version answered it, which sources it cited), followed by the system-wide
 *    V1 → reflection → V2 version history
 */
export function ContextDrawer({
  type,
  courseId,
  question,
  answer,
  references,
  onClose,
}: {
  type: DrawerType
  courseId?: string
  question?: string
  answer?: string
  references?: Reference[]
  onClose: () => void
}) {
  const [data, setData] = useState<any>(null)
  const [teaching, setTeaching] = useState<TeachingContext | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    void (async () => {
      setLoading(true)
      setError('')
      try {
        // The compact classroom context is what makes both drawers answer
        // "this question", not "some course in the demo library".
        const contextResponse = await fetch('/teach-api/teach/context')
        const contextPayload: TeachingContext | null = contextResponse.ok
          ? await contextResponse.json()
          : null
        if (alive) setTeaching(contextPayload)

        const targetCourse = courseId || contextPayload?.course_id || ''
        const url =
          type === 'compare'
            ? `/teach-api/teach/compare?course_id=${encodeURIComponent(courseId || '')}` +
              `&kp=${encodeURIComponent(contextPayload?.node_id || contextPayload?.active_kp || '')}`
            : type === 'graph'
              ? `/teach-api/teach/graph/${encodeURIComponent(targetCourse)}`
              : '/opentutor-api/api/workspace/strategies/unified'
        const response = await fetch(url)
        if (!response.ok) throw new Error('暂时无法读取，请稍后重试')
        const payload = await response.json()
        if (alive) setData(payload)
      } catch (cause) {
        if (alive) setError((cause as Error).message)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [type, courseId])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const currentRefs = (references || []).filter((ref) => ref.title).slice(0, 4)
  const topic = teaching?.active_kp || ''

  return (
    <div className="context-drawer-backdrop" onClick={onClose}>
      <aside
        className="context-drawer"
        onClick={(event) => event.stopPropagation()}
        aria-label={type === 'compare' ? '两轮对比' : type === 'graph' ? '液态知识图谱' : '证据链'}
      >
        <header className="context-drawer-head">
          <div>
            <span className="eyebrow">
              {type === 'compare' ? 'TWO-ROUND COMPARISON' : type === 'graph' ? 'LIQUID KNOWLEDGE GRAPH' : 'EVIDENCE CHAIN'}
            </span>
            <h2>{type === 'compare' ? '两轮对比 · 10 步' : type === 'graph' ? '液态知识图谱' : '证据链'}</h2>
            <p className="muted">
              {type === 'compare'
                ? `围绕当前卡点${topic ? `「${topic}」` : ''}：同一个闭环，r1 只有静态图谱；r2 带课堂记忆。`
                : type === 'graph'
                  ? '节点会呼吸、连线会流动。黄色圈是当前卡点，把鼠标/手指放到节点上看证据。'
                  : '先是这道题本身的证据，后面才是版本是怎么来的。'}
            </p>
          </div>
          <button className="icon" onClick={onClose} aria-label="关闭">
            <X size={19} />
          </button>
        </header>

        {loading && <p className="muted">正在读取…</p>}
        {error && <p className="error">{error}</p>}

        {data && type === 'compare' && (
          <>
            <div className="drawer-meta">
              <b>{data.course?.name || '课程'}</b>
              {data.focus?.kp_label && <span>重点：{data.focus.kp_label}</span>}
              {data.next_action && <span>{data.next_action}</span>}
            </div>
            <p className="muted">
              {teaching?.strategy_version
                ? `当前策略 ${teaching.strategy_version}${teaching.strategy ? ` · ${teaching.strategy}` : ''}；下面两轮都用它来对比。`
                : '下面按这张子图谱的重点知识点对比两轮。'}
            </p>
            <div className="compare-steps">
              {(data.steps || []).map((step: any) => (
                <article className="compare-step" key={`${step.no}-${step.name}`}>
                  <header>
                    <b>{step.no} {step.name}</b>
                    {step.changed ? <span className="compare-changed">有变化</span> : <span className="compare-same">不变</span>}
                  </header>
                  <div className="compare-cols">
                    <div>
                      <small>第一轮 · {step.r1?.src || '—'}</small>
                      <p>{step.r1?.text || '—'}</p>
                    </div>
                    <div>
                      <small>第二轮 · {step.r2?.src || '—'}</small>
                      <p>{step.r2?.text || '—'}</p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
            <EvolutionNotes />
          </>
        )}

        {data && type === 'graph' && (
          <>
            <div className="drawer-meta">
              <b>{data.course_name || '课程知识图谱'}</b>
              <span>节点 {data.nodes?.length || 0}</span>
              <span>关系 {data.edges?.length || 0}</span>
              {data.evidence_count != null && <span>课堂证据 {data.evidence_count}</span>}
            </div>
            <LiquidGraph graph={data} currentId={teaching?.node_id} />
            <div className="liquid-legend">
              <span><i style={{ background: '#a78bfa' }} />稳定</span>
              <span><i style={{ background: '#f2bd57' }} />待确认</span>
              <span><i style={{ background: '#ff7b8a' }} />冲突</span>
              <span><i style={{ background: '#ffd900' }} />当前卡点</span>
            </div>
            <p className="muted">
              图谱来自 5001 的课堂采集：节点大小 = 置信度，节点上的数字 = 课堂证据条数。
              这张图只读，不会因为拖拽而改动学习记录。
            </p>
          </>
        )}

        {data && type === 'chain' && (
          <>
            <section className="drawer-current" aria-label="这道题的证据">
              <h3>这道题</h3>
              <div className="drawer-current-row">
                <small>你问的</small>
                <p>{question ? short(question, 220) : '还没有提问。先问一道题，这里会显示它的证据。'}</p>
              </div>
              <div className="drawer-current-tags">
                {topic && <span>知识点：{topic}</span>}
                {teaching?.error_type && <span>错因：{teaching.error_type}</span>}
                {teaching?.strategy_version && (
                  <span>
                    策略：{teaching.strategy_version}
                    {teaching.strategy ? ` · ${teaching.strategy}` : ''}
                  </span>
                )}
              </div>
              {answer && (
                <div className="drawer-current-row">
                  <small>助手回答（节选）</small>
                  <p>{short(answer, 260)}</p>
                </div>
              )}
              <div className="drawer-current-row">
                <small>引用来源</small>
                {currentRefs.length ? (
                  <ul className="drawer-current-refs">
                    {currentRefs.map((ref, index) => (
                      <li key={`${ref.title}-${index}`}>
                        {ref.url ? (
                          <a href={ref.url} target="_blank" rel="noopener noreferrer">{ref.title}</a>
                        ) : (
                          <span>{ref.title}</span>
                        )}
                        {ref.excerpt && <em>{short(ref.excerpt, 90)}</em>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">这轮回答没有返回来源；如果是断流，重新生成一次就会出现。</p>
                )}
              </div>
            </section>

            <h3 className="drawer-subhead">系统策略进化（跨题）</h3>
            <p className="muted">{data.note}</p>
            <div className="drawer-chain">
              {(data.evidence_chain || []).map((step: any, index: number) => (
                <article className="drawer-chain-step" key={`${step.step}-${step.id}`}>
                  <span>{index + 1}</span>
                  <div>
                    <b>{step.step}</b>
                    <small>{step.run}{step.at ? ` · ${String(step.at).slice(0, 19).replace('T', ' ')}` : ''}</small>
                    <p>{step.detail}</p>
                    <code>{step.id}</code>
                  </div>
                </article>
              ))}
            </div>
            <div className="drawer-honesty">
              <span>两轮分数：{data.honesty?.scores_tied ? '持平' : '有差异'}</span>
              <span>注入验证：{String(data.honesty?.prompt_injection_verified)}</span>
              <span>执行状态：{data.honesty?.execution_state || '未记录'}</span>
              <span>学习提升：未宣称</span>
            </div>
            <p className="muted">
              OpenTutor 技能版本与 5001 课堂策略已汇入统一 strategy_registry 读模型；两个引擎仍各自写源表，新版本通过 sync 汇入。
            </p>
            <EvolutionNotes />
          </>
        )}
      </aside>
    </div>
  )
}
