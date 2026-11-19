import { useEffect, useMemo, useState } from 'react'
import { Sparkles } from 'lucide-react'
import './ContextPill.css'

const API = '/teach-api'

type Context = {
  active_kp: string
  node_id: string
  course_name: string
  course_id: string
  error_type: string
  strategy: string
  strategy_version: string
  solo_level?: number
  solo_name?: string
}
type Node = { id: string; label: string; conf: number; status: string; seen: number }

const STATUS_LABEL: Record<string, string> = {
  stable: '稳定',
  conflict: '冲突',
  pending: '待确认',
}

/**
 * Put the knowledge graph "inside" the Q&A stream:
 * a compact context pill + quick questions from weak/conflict nodes.
 * Pure frontend; the backend context is already passed with every chat turn.
 */
export function ContextPill({
  onAsk,
  onOpenCompare,
  onOpenChain,
  onOpenGraph,
}: {
  onAsk: (prompt: string) => void
  onOpenCompare: (courseId: string) => void
  onOpenChain: () => void
  onOpenGraph: (courseId: string) => void
}) {
  const [context, setContext] = useState<Context | null>(null)
  const [nodes, setNodes] = useState<Node[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const response = await fetch(`${API}/teach/context`)
        if (!response.ok) return
        const data: Context = await response.json()
        if (!alive) return
        setContext(data)
        if (data.course_id) {
          const graphRes = await fetch(`${API}/teach/graph/${encodeURIComponent(data.course_id)}`)
          if (graphRes.ok) {
            const graph = await graphRes.json()
            if (alive) setNodes(Array.isArray(graph.nodes) ? graph.nodes : [])
          }
        }
      } catch (cause) {
        if (alive) setError((cause as Error).message)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const activeNode = useMemo(() => {
    if (!context) return null
    return (
      nodes.find((node) => node.id === context.node_id) ||
      nodes.find((node) => node.label && context.active_kp && node.label.includes(context.active_kp)) ||
      null
    )
  }, [context, nodes])

  const suggestions = useMemo(() => {
    const weak = nodes.filter(
      (node) =>
        node.id !== 'root' &&
        (node.status === 'conflict' || node.status === 'pending' || (node.conf || 0) < 0.6),
    )
    const pool = weak.length ? weak : activeNode ? [activeNode] : []
    return pool.slice(0, 3)
  }, [nodes, activeNode])

  if (error || !context || !context.active_kp) return null

  function ask(node: Node) {
    const status =
      node.status === 'conflict' ? '在知识图谱里还是冲突状态' : node.status === 'pending' ? '还没确认' : '我还没弄懂'
    const prompt =
      `我卡在「${context?.active_kp}」这里，最近错因是「${context?.error_type || '概念混淆'}」。` +
      `知识图谱里「${node.label}」${status}。` +
      `请用「${context?.strategy || '拆成两步的变式题'}」帮我把它拆成两步讲，先别直接给答案。`
    onAsk(prompt)
  }

  function askTenStep() {
    onAsk(
      `请用十步闭环（采集、切分、纪要、事实、待办、每日、洞察、检索、问答、策略升版）` +
        `复盘我这次「${context?.active_kp} · ${context?.error_type || '概念混淆'}」的过程：` +
        `哪一步最可能出错？下一轮怎么验证，不要泛泛而谈。`,
    )
  }

  function askEvolution() {
    onAsk(
      `请解释当前课堂策略 ${context?.strategy_version || 'v9'}（${context?.strategy || '拆成两步的变式题'}）` +
        `是怎么从上一版切过来的：切换理由、切换前后正确率、样本量，以及如果下一轮还不行应该怎么回滚。`,
    )
  }

  function askVerify() {
    onAsk(
      `请给我一个下一轮验证计划：同一错因「${context?.error_type || '概念混淆'}」换 3 道新题，` +
        `答对几道算稳定？如果还不行，策略 ${context?.strategy_version || 'v9'} 应该怎么回滚？`,
    )
  }

  return (
    <section className="context-pill" aria-label="当前学习上下文">
      <div className="context-pill-row">
        <Sparkles size={13} />
        <span className="context-pill-label">当前卡点</span>
        <b>{context.active_kp}{context.error_type ? ` · ${context.error_type}` : ''}</b>
        <span className="context-pill-sep">|</span>
        <span className="context-pill-label">策略</span>
        <b>{context.strategy_version || '—'}{context.strategy ? ` · ${context.strategy}` : ''}</b>
        <span className="context-pill-sep">|</span>
        <span className="context-pill-label">子图</span>
        <b className={activeNode ? `context-pill-node ${activeNode.status}` : 'context-pill-node'}>
          {activeNode ? `${activeNode.label}（${STATUS_LABEL[activeNode.status] || activeNode.status}）` : '未连接'}
        </b>
      </div>
      {suggestions.length > 0 && (
        <div className="context-pill-asks">
          <small>试着问：</small>
          {suggestions.map((node) => (
            <button type="button" key={node.id} onClick={() => ask(node)}>
              {node.label}
            </button>
          ))}
        </div>
      )}
      <div className="context-pill-asks context-pill-more">
        <small>也可以问：</small>
        <button type="button" onClick={askTenStep}>十步怎么走</button>
        <button type="button" onClick={askEvolution}>{context.strategy_version || '策略'} 怎么来的</button>
        <button type="button" onClick={askVerify}>下一轮怎么验证</button>
        <button type="button" onClick={() => onOpenCompare(context.course_id)}>
          看两轮对比
        </button>
        <button type="button" onClick={onOpenChain}>
          看证据链
        </button>
        <button type="button" className="context-pill-liquid" onClick={() => onOpenGraph(context.course_id)}>
          ✦ 液态图谱
        </button>
      </div>
    </section>
  )
}
