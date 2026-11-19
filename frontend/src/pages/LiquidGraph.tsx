import { useEffect, useMemo, useRef, useState } from 'react'
import './LiquidGraph.css'

export type LiquidNode = {
  id: string
  label: string
  conf: number
  status: string
  seen?: number
  evidence?: unknown[]
}
export type LiquidEdge = { from: string; to: string }
export type LiquidGraphData = {
  course_name?: string
  nodes?: LiquidNode[]
  edges?: LiquidEdge[]
  evidence_count?: number
}

type Point = { x: number; y: number; vx: number; vy: number; depth: number }

const STATUS_LABEL: Record<string, string> = {
  stable: '稳定',
  pending: '待确认',
  conflict: '冲突',
}

function statusColor(status: string) {
  if (status === 'conflict') return '#ff7b8a'
  if (status === 'pending') return '#f2bd57'
  return '#a78bfa'
}

/** 按知识图的父子关系排一个初始径向位置，根节点在中心。 */
function seedNodes(nodes: LiquidNode[], edges: LiquidEdge[], width: number, height: number) {
  const adjacency = new Map<string, string[]>()
  nodes.forEach((node) => adjacency.set(node.id, []))
  edges.forEach((edge) => {
    if (adjacency.has(edge.from) && adjacency.has(edge.to)) {
      adjacency.get(edge.from)!.push(edge.to)
      adjacency.get(edge.to)!.push(edge.from)
    }
  })
  const root = nodes.find((node) => node.id === 'root') || nodes[0]
  const depth = new Map<string, number>()
  const queue: string[] = []
  if (root) {
    depth.set(root.id, 0)
    queue.push(root.id)
  }
  while (queue.length) {
    const current = queue.shift()!
    const next = (depth.get(current) || 0) + 1
    ;(adjacency.get(current) || []).forEach((id) => {
      if (!depth.has(id)) {
        depth.set(id, next)
        queue.push(id)
      }
    })
  }
  const maxDepth = Math.max(1, ...Array.from(depth.values()))
  const groups = new Map<number, string[]>()
  nodes.forEach((node) => {
    const level = depth.get(node.id) ?? maxDepth
    groups.set(level, [...(groups.get(level) || []), node.id])
  })
  const positions = new Map<string, Point>()
  const center = { x: width / 2, y: height / 2 }
  groups.forEach((ids, level) => {
    if (level === 0) {
      ids.forEach((id) => positions.set(id, { ...center, vx: 0, vy: 0, depth: 0 }))
      return
    }
    const radius = Math.min(width, height) * (0.18 + (level / maxDepth) * 0.3)
    ids.forEach((id, index) => {
      const angle = (Math.PI * 2 * index) / ids.length - Math.PI / 2 + level * 0.4
      positions.set(id, {
        x: center.x + Math.cos(angle) * radius,
        y: center.y + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        depth: level,
      })
    })
  })
  return positions
}

/**
 * 液态知识图谱：节点像液滴一样缓慢漂移，连线带流动的光点。
 * 用 canvas 画，节点数在几十个以内都够用。
 */
export function LiquidGraph({
  graph,
  currentId,
}: {
  graph: LiquidGraphData
  currentId?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [hovered, setHovered] = useState<LiquidNode | null>(null)
  const nodes = useMemo(() => graph.nodes || [], [graph])
  const edges = useMemo(() => graph.edges || [], [graph])

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let width = wrap.clientWidth
    let height = wrap.clientHeight
    let points = seedNodes(nodes, edges, width, height)
    let raf = 0
    let disposed = false
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const pointer = { x: -999, y: -999 }

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    function resize() {
      width = wrap!.clientWidth
      height = wrap!.clientHeight
      canvas!.width = Math.max(1, Math.round(width * dpr))
      canvas!.height = Math.max(1, Math.round(height * dpr))
      canvas!.style.width = `${width}px`
      canvas!.style.height = `${height}px`
      const previous = points
      points = seedNodes(nodes, edges, width, height)
      // 保留已经稳定下来的位置，避免旋转屏幕时节点全部跳回原点。
      points.forEach((point, id) => {
        const old = previous.get(id)
        if (old) {
          point.x = Math.max(8, Math.min(width - 8, old.x))
          point.y = Math.max(20, Math.min(height - 26, old.y))
        }
      })
      draw(performance.now())
    }

    function step(dt: number, now: number) {
      const ids = Array.from(points.keys())
      const centerX = width / 2
      const centerY = height / 2
      const span = Math.min(width, height)
      const ideal = span * 0.24
      const array = ids.map((id) => ({ id, point: points.get(id)! }))

      // 节点之间的排斥，保证不叠在一起
      for (let i = 0; i < array.length; i += 1) {
        for (let j = i + 1; j < array.length; j += 1) {
          const a = array[i].point
          const b = array[j].point
          let dx = a.x - b.x
          let dy = a.y - b.y
          let dist = Math.hypot(dx, dy)
          if (dist < 0.001) {
            dx = 0.01
            dy = 0.01
            dist = 0.01
          }
          const minDist = span * 0.19
          if (dist < minDist) {
            const force = ((minDist - dist) / minDist) * 0.9
            a.vx += (dx / dist) * force
            a.vy += (dy / dist) * force
            b.vx -= (dx / dist) * force
            b.vy -= (dy / dist) * force
          }
        }
      }

      // 连线像弹簧，把有关系的知识点拉近
      edges.forEach((edge) => {
        const a = points.get(edge.from)
        const b = points.get(edge.to)
        if (!a || !b) return
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.max(0.001, Math.hypot(dx, dy))
        const force = (dist - ideal) * 0.0009
        a.vx += (dx / dist) * force * dt
        a.vy += (dy / dist) * force * dt
        b.vx -= (dx / dist) * force * dt
        b.vy -= (dy / dist) * force * dt
      })

      // 轻微向中心收拢 + 缓慢的液态漂移
      const time = now * 0.00035
      array.forEach(({ id, point }, index) => {
        point.vx += (centerX - point.x) * 0.00035 * dt
        point.vy += (centerY - point.y) * 0.00035 * dt
        point.vx += Math.sin(time + index * 1.7) * 0.0022
        point.vy += Math.cos(time * 0.9 + index * 1.3) * 0.0022

        // 鼠标靠近时轻轻躲开，增强“液体”感
        const dx = point.x - pointer.x
        const dy = point.y - pointer.y
        const dist = Math.hypot(dx, dy)
        if (dist < 70 && dist > 0.001) {
          const push = (70 - dist) / 70
          point.vx += (dx / dist) * push * 0.35
          point.vy += (dy / dist) * push * 0.35
        }

        point.vx *= 0.9
        point.vy *= 0.9
        point.x += point.vx * dt
        point.y += point.vy * dt
        const pad = span * 0.1
        point.x = Math.max(pad, Math.min(width - pad, point.x))
        point.y = Math.max(pad + 14, Math.min(height - pad - 18, point.y))
        void id
      })
    }

    function draw(now: number) {
      if (disposed) return
      const t = now || 0
      if (!reduceMotion) step(1, t)

      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx!.clearRect(0, 0, width, height)

      // 液体背景：几团缓慢移动的紫色/金色光晕
      const bg = ctx!.createLinearGradient(0, 0, width, height)
      bg.addColorStop(0, '#1b0b31')
      bg.addColorStop(0.55, '#251047')
      bg.addColorStop(1, '#160a2a')
      ctx!.fillStyle = bg
      ctx!.fillRect(0, 0, width, height)
      const blobs = [
        { x: width * 0.28, y: height * 0.35, r: spanOf(width, height) * 0.55, c: 'rgba(124,77,255,0.32)' },
        { x: width * 0.78, y: height * 0.68, r: spanOf(width, height) * 0.5, c: 'rgba(80,190,255,0.14)' },
        { x: width * 0.6, y: height * 0.2, r: spanOf(width, height) * 0.4, c: 'rgba(255,217,0,0.07)' },
      ]
      blobs.forEach((blob, index) => {
        const dx = Math.sin(t * 0.00018 + index * 2.1) * 26
        const dy = Math.cos(t * 0.00015 + index * 1.4) * 20
        const gradient = ctx!.createRadialGradient(blob.x + dx, blob.y + dy, 0, blob.x + dx, blob.y + dy, blob.r)
        gradient.addColorStop(0, blob.c)
        gradient.addColorStop(1, 'rgba(0,0,0,0)')
        ctx!.fillStyle = gradient
        ctx!.fillRect(0, 0, width, height)
      })

      // 连线：贝塞尔曲线 + 流动光点
      edges.forEach((edge, index) => {
        const a = points.get(edge.from)
        const b = points.get(edge.to)
        if (!a || !b) return
        const mx = (a.x + b.x) / 2
        const my = (a.y + b.y) / 2
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.max(0.001, Math.hypot(dx, dy))
        const bend = Math.sin(t * 0.0006 + index * 1.8) * Math.min(26, dist * 0.16)
        const cx = mx + (-dy / dist) * bend
        const cy = my + (dx / dist) * bend
        ctx!.beginPath()
        ctx!.moveTo(a.x, a.y)
        ctx!.quadraticCurveTo(cx, cy, b.x, b.y)
        ctx!.strokeStyle = 'rgba(167,139,250,0.32)'
        ctx!.lineWidth = 1.1
        ctx!.stroke()

        const flow = (t * 0.00018 + index * 0.21) % 1
        const fx = (1 - flow) * (1 - flow) * a.x + 2 * (1 - flow) * flow * cx + flow * flow * b.x
        const fy = (1 - flow) * (1 - flow) * a.y + 2 * (1 - flow) * flow * cy + flow * flow * b.y
        ctx!.beginPath()
        ctx!.arc(fx, fy, 1.7, 0, Math.PI * 2)
        ctx!.fillStyle = 'rgba(255,255,255,0.75)'
        ctx!.fill()
      })

      // 节点：外发光 + 液体核心
      nodes.forEach((node, index) => {
        const point = points.get(node.id)
        if (!point) return
        const radius = 9 + Math.max(0, Math.min(1, node.conf)) * 12
        const color = statusColor(node.status)
        const current = node.id === currentId
        const pulse = current ? 1 + Math.sin(t * 0.003) * 0.12 : 1

        const glow = ctx!.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius * 2.8 * pulse)
        glow.addColorStop(0, `${hexToRgba(color, 0.55)}`)
        glow.addColorStop(1, 'rgba(0,0,0,0)')
        ctx!.fillStyle = glow
        ctx!.beginPath()
        ctx!.arc(point.x, point.y, radius * 2.8 * pulse, 0, Math.PI * 2)
        ctx!.fill()

        const core = ctx!.createRadialGradient(
          point.x - radius * 0.3,
          point.y - radius * 0.35,
          radius * 0.1,
          point.x,
          point.y,
          radius,
        )
        core.addColorStop(0, '#ffffff')
        core.addColorStop(0.25, color)
        core.addColorStop(1, hexToRgba(color, 0.55))
        ctx!.fillStyle = core
        ctx!.beginPath()
        ctx!.arc(point.x, point.y, radius * pulse, 0, Math.PI * 2)
        ctx!.fill()

        if (current) {
          ctx!.beginPath()
          ctx!.arc(point.x, point.y, radius + 6 + Math.sin(t * 0.003) * 2, 0, Math.PI * 2)
          ctx!.strokeStyle = 'rgba(255,217,0,0.9)'
          ctx!.lineWidth = 2
          ctx!.stroke()
        }
        void index
      })

      // 标签单独一遍画：先保证当前卡点，再给其他节点找不重叠的位置。
      // 放不下的标签先不画，悬停时底部提示会显示完整名字。
      const placed: { x1: number; y1: number; x2: number; y2: number }[] = []
      const labelOrder = nodes
        .map((node, index) => ({ node, index }))
        .sort((a, b) => {
          const priority = (n: LiquidNode) => (n.id === currentId ? 1 : 0)
          return priority(b.node) - priority(a.node) || a.index - b.index
        })
      labelOrder.forEach(({ node }) => {
        const point = points.get(node.id)
        if (!point) return
        const radius = 9 + Math.max(0, Math.min(1, node.conf)) * 12
        const rawLabel = node.label || node.id
        const label = rawLabel.length > 7 ? `${rawLabel.slice(0, 7)}…` : rawLabel
        const current = node.id === currentId
        ctx!.font = `${current ? '700 ' : ''}12px Inter, "PingFang SC", "Microsoft YaHei", sans-serif`
        ctx!.textAlign = 'left'
        const textWidth = ctx!.measureText(label).width
        const textHeight = 14
        const gap = radius + 7
        const candidates = [
          { x: point.x - textWidth / 2, y: point.y + gap, baseline: 'top' as CanvasTextBaseline, boxY: point.y + gap },
          { x: point.x - textWidth / 2, y: point.y - gap - textHeight, baseline: 'top' as CanvasTextBaseline, boxY: point.y - gap - textHeight },
          { x: point.x + gap, y: point.y - textHeight / 2, baseline: 'top' as CanvasTextBaseline, boxY: point.y - textHeight / 2 },
          { x: point.x - gap - textWidth, y: point.y - textHeight / 2, baseline: 'top' as CanvasTextBaseline, boxY: point.y - textHeight / 2 },
        ]
        for (const candidate of candidates) {
          const box = {
            x1: candidate.x - 2,
            y1: candidate.boxY - 1,
            x2: candidate.x + textWidth + 2,
            y2: candidate.boxY + textHeight + 1,
          }
          const inside =
            box.x1 >= 3 && box.x2 <= width - 3 && box.y1 >= 3 && box.y2 <= height - 3
          const overlaps = placed.some(
            (other) => !(box.x2 < other.x1 || box.x1 > other.x2 || box.y2 < other.y1 || box.y1 > other.y2),
          )
          if (inside && !overlaps) {
            ctx!.textBaseline = candidate.baseline
            ctx!.fillStyle = current ? '#ffe88a' : 'rgba(240,234,255,0.82)'
            ctx!.shadowColor = 'rgba(15,5,35,0.85)'
            ctx!.shadowBlur = 4
            ctx!.fillText(label, candidate.x, candidate.y)
            ctx!.shadowBlur = 0
            placed.push(box)
            break
          }
        }
      })

      if (!reduceMotion) raf = requestAnimationFrame(draw)
    }

    function spanOf(w: number, h: number) {
      return Math.min(w, h)
    }
    function hexToRgba(hex: string, alpha: number) {
      const clean = hex.replace('#', '')
      const value = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean
      const r = parseInt(value.slice(0, 2), 16)
      const g = parseInt(value.slice(2, 4), 16)
      const b = parseInt(value.slice(4, 6), 16)
      return `rgba(${r},${g},${b},${alpha})`
    }

    function handleMove(event: PointerEvent) {
      const rect = canvas!.getBoundingClientRect()
      pointer.x = event.clientX - rect.left
      pointer.y = event.clientY - rect.top
      let nearest: LiquidNode | null = null
      let best = 34
      nodes.forEach((node) => {
        const point = points.get(node.id)
        if (!point) return
        const dist = Math.hypot(point.x - pointer.x, point.y - pointer.y)
        if (dist < best) {
          best = dist
          nearest = node
        }
      })
      setHovered(nearest)
    }
    function handleLeave() {
      pointer.x = -999
      pointer.y = -999
      setHovered(null)
    }

    const observer = new ResizeObserver(() => resize())
    observer.observe(wrap)
    canvas.addEventListener('pointermove', handleMove)
    canvas.addEventListener('pointerleave', handleLeave)
    resize()

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      observer.disconnect()
      canvas.removeEventListener('pointermove', handleMove)
      canvas.removeEventListener('pointerleave', handleLeave)
    }
  }, [nodes, edges, currentId])

  return (
    <div className="liquid-graph" ref={wrapRef}>
      <canvas ref={canvasRef} aria-label="液态知识图谱" role="img" />
      <div className="liquid-graph-hint">
        {hovered ? (
          <>
            <b style={{ color: statusColor(hovered.status) }}>{hovered.label}</b>
            <span>
              置信度 {Math.round((hovered.conf || 0) * 100)}% · 证据 {hovered.seen || 0} 条 ·{' '}
              {STATUS_LABEL[hovered.status] || hovered.status}
            </span>
          </>
        ) : (
          <span>把手指/鼠标放到节点上，看这个知识点的置信度和证据数</span>
        )}
      </div>
    </div>
  )
}
