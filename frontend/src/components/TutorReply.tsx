import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, ExternalLink, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
// Same math rendering pipeline as OpenTutor components/shared/markdown-renderer.tsx.
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { cleanReply, prepareReply, sourceLabel, safeSourceUrl, type CourseReference } from '../lib/tutorReply'

function Citation({ reference, number }: { reference: CourseReference; number: number }) {
  const [position, setPosition] = useState<{ top: number; left: number; width: number } | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const suppressFocus = useRef(false)
  const id = useId()
  function closeAndFocus() {
    cancelClose(); setPosition(null)
    suppressFocus.current = true
    trigger.current?.focus()
    suppressFocus.current = false
  }
  const cancelClose = () => { clearTimeout(timer.current) }
  function open() {
    cancelClose()
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(360, window.innerWidth - 24)
    const height = Math.min(330, window.innerHeight - 24)
    const top = rect.bottom + 8 + height <= window.innerHeight ? rect.bottom + 8 : Math.max(12, rect.top - height - 8)
    setPosition({ top, left: Math.max(12, Math.min(rect.left - 20, window.innerWidth - width - 12)), width })
  }
  function closeSoon() { cancelClose(); timer.current = setTimeout(() => setPosition(null), 180) }
  useEffect(() => {
    if (!position) return
    function outside(event: PointerEvent) {
      if (!popup.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setPosition(null)
    }
    function escape(event: KeyboardEvent) { if (event.key === 'Escape') { closeAndFocus() } }
    function reposition(event: Event) { if (!popup.current?.contains(event.target as Node)) setPosition(null) }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [position])
  useEffect(() => () => clearTimeout(timer.current), [])
  return <><button ref={trigger} type="button" className="tutor-citation" aria-label={`引用 ${number}：${reference.title}`} aria-expanded={!!position} aria-controls={position ? id : undefined} onMouseEnter={open} onMouseLeave={closeSoon} onFocus={() => { if (!suppressFocus.current) open() }} onBlur={(e) => { if (!popup.current?.contains(e.relatedTarget as Node)) closeSoon() }} onClick={open}>{number}</button>{position && createPortal(<div ref={popup} id={id} role="dialog" aria-label={`资料来源：${reference.title}`} className="tutor-source-popup" style={position} onMouseEnter={cancelClose} onMouseLeave={closeSoon} onFocus={cancelClose} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) closeSoon() }}><header><span><BookOpen size={13}/>课程资料</span><button type="button" aria-label="关闭引用" onClick={() => { closeAndFocus() }}><X size={15}/></button></header><h3>{reference.title}</h3>{reference.excerpt ? <div className="tutor-source-excerpt"><ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]}>{cleanReply(reference.excerpt)}</ReactMarkdown></div> : <p className="tutor-source-unavailable">这条引用未附原文片段，暂时无法在此预览。</p>}{safeSourceUrl(reference.url) && <a className="tutor-source-original" href={safeSourceUrl(reference.url)} target="_blank" rel="noopener noreferrer">查看官方原文<ExternalLink size={12}/></a>}</div>, document.body)}</>
}

export function TutorReply({ content, references = [] }: { content: string; references?: CourseReference[] }) {
  const { markdown, citations } = prepareReply(content, references)
  return <><div className="tutor-markdown"><ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]} components={{ a: ({ href, children, ...props }) => {
    const match = href?.match(/^#tutor-citation-(\d+)$/)
    const index = match ? Number(match[1]) - 1 : -1
    return index >= 0 && citations[index] ? <Citation reference={citations[index]} number={index + 1}/> : <a {...props} href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  } }}>{markdown}</ReactMarkdown></div>{!citations.length && !!references.length && <div className="tutor-related-sources"><span>相关课程资料</span>{references.map((ref, index) => <Citation key={`${index}-${ref.title}`} reference={{ ...ref, title: sourceLabel(ref.title) }} number={index + 1}/>)}</div>}</>
}
