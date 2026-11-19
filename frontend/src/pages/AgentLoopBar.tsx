import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import './AgentLoopBar.css'

const TEACH = '/teach-api'
const OT = '/opentutor-api/api/workspace'

type Step = { label: string; value: string; detail: string; tone?: 'ok' | 'warn' | 'muted' }

function short(value: unknown, limit = 70) {
  const text = typeof value === 'string' ? value : ''
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

/**
 * Two separate teaching loops. The bar is mode-aware on purpose:
 * - 问答助手 shows the OpenTutor loop (evaluation → reflection → skill v2 → next invocation)
 * - 课堂采集 shows the 5001 loop (knowledge graph → classroom strategy → 3-question quiz → version)
 * They share memory, but the storage is not merged; never mix their numbers.
 */
export function AgentLoopBar({ mode }: { mode: 'chat' | 'capture' }) {
  const [chatSteps, setChatSteps] = useState<Step[]>([])
  const [captureSteps, setCaptureSteps] = useState<Step[]>([])
  const [error, setError] = useState('')
  // 手机上默认收起，先让聊天区露出来；想看回路再点“展开回路”。
  const [open, setOpen] = useState(() => !window.matchMedia('(max-width: 760px)').matches)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [latestRes, evoRes, overviewRes] = await Promise.all([
          fetch(`${TEACH}/teach/session/latest`),
          fetch(`${TEACH}/teach/evolution`),
          fetch(`${OT}/overview`),
        ])
        const latest = latestRes.ok ? await latestRes.json() : {}
        const evo = evoRes.ok ? await evoRes.json() : {}
        const overview = overviewRes.ok ? await overviewRes.json() : {}
        if (!alive) return

        // ── 问答助手：OpenTutor 闭环 ──
        const course = (overview.courses || [])[0]
        const evaluation = (overview.evaluations || [])[0]
        const reflection = (overview.reflections || [])[0]
        const v2 = (overview.versions || []).find(
          (row: any) => row.version === 2 && row.status === 'active',
        )
        const inv2 = (overview.invocations || []).find((row: any) => row.version === 2)
        const weak = (course?.weak_concepts || [])[0]
        setChatSteps([
          {
            label: '课程 / 教材',
            value: course?.course_name || '—',
            detail: course?.assessment_summary
              ? `已评估 ${course.assessment_summary.concepts_assessed}/${course.assessment_summary.concepts_total} 个知识点`
              : '还没有学习者状态',
            tone: course ? 'ok' : 'muted',
          },
          {
            label: '学习者状态',
            value: weak?.name || '—',
            detail: weak
              ? `薄弱点 · 掌握度 ${weak.mastery_score == null ? '未评估' : Math.round(weak.mastery_score * 100) + '%'}`
              : '暂未识别薄弱点',
            tone: weak ? 'warn' : 'muted',
          },
          {
            label: '学习评价',
            value: evaluation?.verdict || '—',
            detail: evaluation
              ? `理解 ${evaluation.scores?.understanding ?? '未测'} / 应用 ${evaluation.scores?.application ?? '未测'} / 迁移 ${evaluation.scores?.transfer ?? '未测'}`
              : '还没有评价记录',
            tone: evaluation
              ? evaluation.verdict === 'achieved'
                ? 'ok'
                : 'warn'
              : 'muted',
          },
          {
            label: '反思',
            value: reflection ? '已生成' : '—',
            detail: reflection?.problem?.statement || '还没有反思记录',
            tone: reflection ? 'ok' : 'muted',
          },
          {
            label: '技能版本',
            value: v2 ? `V${v2.version}` : '—',
            detail: v2?.change_summary || '还没有新版本',
            tone: v2 ? 'ok' : 'muted',
          },
          {
            label: '下一轮调用',
            value: inv2 ? `V${inv2.version}` : '—',
            detail: inv2
              ? `${inv2.status} · 注入验证 ${String(inv2.prompt_injection_verified)}`
              : '还没有下一轮调用记录',
            tone: inv2 ? 'ok' : 'muted',
          },
        ])

        // ── 课堂采集：5001 闭环 ──
        const strategies = (evo.versions || []).filter((row: any) => row.kind === 'strategy')
        const active = strategies.find((row: any) => row.status === 'active')
        const metrics = active?.metrics
        setCaptureSteps([
          {
            label: '知识图谱',
            value: latest.kp || '—',
            detail: latest.course_name ? `${latest.course_name} · 课堂证据` : '还没有课堂采集',
            tone: latest.kp ? 'ok' : 'muted',
          },
          {
            label: '课堂策略',
            value: active?.id || '—',
            detail: active?.strategy || '还没有课堂策略',
            tone: active ? 'ok' : 'muted',
          },
          {
            label: '3 题自测',
            value: latest.total ? `${latest.correct}/${latest.total}` : '—',
            detail: latest.error_type ? `错因：${latest.error_type}` : '还没有交卷',
            tone: latest.total ? 'ok' : 'muted',
          },
          {
            label: '策略升版',
            value: active?.id || '—',
            detail: metrics
              ? `切换前 ${metrics.old == null ? '—' : Math.round(metrics.old * 100) + '%'} · 切换后 ${metrics.new == null ? '未复测' : Math.round(metrics.new * 100) + '%'} · n=${metrics.n}`
              : '还没有足够样本决定升版',
            tone: active ? 'ok' : 'muted',
          },
          {
            label: '下一轮',
            value: active?.id || '—',
            detail: '同一错因换题复测；答对才算稳定，可在学习画像发起',
            tone: active ? 'ok' : 'muted',
          },
        ])
      } catch (cause) {
        if (alive) setError((cause as Error).message || '本轮教学数据暂时无法读取')
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  if (error) return null
  const steps = mode === 'chat' ? chatSteps : captureSteps
  if (!steps.length) return null

  return (
    <section className={`agent-loop ${mode}`} aria-label={mode === 'chat' ? '问答助手教学回路' : '课堂采集教学回路'}>
      <div className="agent-loop-head">
        <b>{mode === 'chat' ? '问答助手 · OpenTutor 教学回合' : '课堂采集 · 5001 教学回合'}</b>
        <em className="agent-loop-badge">自进化</em>
        <span>
          {mode === 'chat'
            ? '教材 → 学习者状态 → 评价 → 反思 → 技能版本 → 下一轮'
            : '知识图谱 → 课堂策略 → 3 题自测 → 策略升版 → 下一轮'}
        </span>
        <button
          type="button"
          className="agent-loop-toggle"
          onClick={() => window.dispatchEvent(new Event('metamind-open-chain'))}
        >
          查看完整证据链 <ArrowRight size={13} />
        </button>
        <button
          type="button"
          className="agent-loop-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? '收起回路' : '展开回路'}
        </button>
      </div>
      {open && <div className="agent-loop-steps">
        {steps.map((step, index) => (
          <article className={`agent-loop-step ${step.tone || ''}`} key={step.label}>
            <span className="agent-loop-index">{index + 1}</span>
            <div>
              <small>{step.label}</small>
              <b>{step.value}</b>
              <p>{short(step.detail)}</p>
            </div>
          </article>
        ))}
      </div>}
      {open && <p className="agent-loop-note">
        {mode === 'chat'
          ? '自进化 = 每轮回答完都会被评价，评价写成反思，反思改出新技能版本（V2），下一轮回答自动注入新版本。数字来自 OpenTutor 的评价/反思/技能库，不是前端编的。'
          : '这是课堂采集的闭环，数字来自 5001 的知识图谱/自测/课堂策略库。统一 strategy_registry 已合并读出，两个引擎仍各自写源表。'}
      </p>}
    </section>
  )
}
