import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, BookOpen, Camera, Check, ChevronDown, Copy, Database, History, MessageSquare, Mic, PanelLeftClose, Plus, RotateCcw, Sparkles, Square, Trash2, Video, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { readPracticeAnswer } from '../lib/practiceAnswers'
import { refreshLearning } from '../lib/useLearning'
import { TutorReply } from '../components/TutorReply'
import { ContextPill } from './ContextPill'
import { ContextDrawer } from './ContextDrawer'
import './VisionTools.css'
import { copyableReply, cleanReply, safeSourceUrl, type CourseReference } from '../lib/tutorReply'
import './TeachingAgentPage.css'

const API = '/opentutor-api/api'
const STORAGE_KEY = 'metamind-tutor-conversations-v1'
const ACTIVE_KEY = 'metamind-tutor-active-v1'
type Course = { id: string; name: string; metadata?: { teaching_default?: boolean; data_kind?: string; starter_questions?: string[]; practice_topics?: string[]; source_label?: string; sources?: { url?: string }[] } }
type Exercise = { id:string; question:string; question_type:string; options?:Record<string,string>; topic?:string; submission_id:string; answered?:boolean }
type Message = { id: string; role: 'user' | 'assistant'; content: string; error?: string; references?: CourseReference[]; exercise?:Exercise }
type Conversation = { id: string; courseId: string; title: string; messages: Message[]; sessionId?: string; updatedAt: number }

function readHistory(): Conversation[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (!Array.isArray(value)) return []
    return value.filter((item): item is Conversation => !!item && typeof item.id === 'string' && typeof item.courseId === 'string' && typeof item.title === 'string' && Array.isArray(item.messages) && item.messages.every((m: Message) => m && typeof m.id === 'string' && ['user', 'assistant'].includes(m.role) && typeof m.content === 'string'))
  } catch { return [] }
}
function friendlyError(error: unknown) {
  const text = error instanceof Error ? error.message : ''
  if (/session.*not found/i.test(text)) return '这次对话已过期，请新建对话后继续。'
  if (/fetch|network|连接|502|503|504/i.test(text)) return '暂时连接不上，请稍后重试。'
  if (/provider|api.key|LLM_REQUIRED|401|403/i.test(text)) return 'AI 服务暂时不可用，请稍后再试。'
  return '这次没能完成回复，请重试。'
}

export function TeachingAgentPage() {
  const [params]=useSearchParams()
  const [practiceReasons,setPracticeReasons]=useState<Record<string,string>>({})
  const [practiceChoices,setPracticeChoices]=useState<Record<string,string[]>>({})
  const [courses, setCourses] = useState<Course[]>([])
  const [courseId, setCourseId] = useState('')
  const [conversations, setConversations] = useState<Conversation[]>(readHistory)
  const [activeId, setActiveId] = useState<string | null>(() => {
    try { return localStorage.getItem(ACTIVE_KEY) } catch { return null }
  })
  const [draft, setDraft] = useState('')
  const [welcomeMode, setWelcomeMode] = useState<'ask' | 'practice'>('ask')
  const [practiceTopic, setPracticeTopic] = useState('')
  const [generating, setGenerating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [storageError, setStorageError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [copiedId, setCopiedId] = useState('')
  const [drawer, setDrawer] = useState<{ type: 'compare' | 'chain' | 'graph'; courseId?: string } | null>(null)
  const [vision, setVision] = useState<{ image: string; question: string; answer: string; busy: boolean } | null>(null)
  const [voiceRecording, setVoiceRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [routeBusy, setRouteBusy] = useState(false)
  const imageInputRef = useRef<HTMLInputElement>(null)
  const [cameraOpen, setCameraOpen] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const cameraStreamRef = useRef<MediaStream | null>(null)
  const voiceRecorderRef = useRef<MediaRecorder | null>(null)
  const voiceChunks = useRef<Blob[]>([])
  const [showScroll, setShowScroll] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const stickToBottom = useRef(true)
  const active = conversations.find((item) => item.id === activeId)
  const messages = active?.messages || []
  // 证据链/两轮对比要围绕“现在这道题”，而不是课程库里的通用演示。
  const lastUserMessage = [...messages].reverse().find((item) => item.role === 'user')
  const lastAnswerMessage = [...messages].reverse().find((item) => item.role === 'assistant' && !item.error && item.content.trim())
  const course = courses.find((row) => row.id === courseId)
  const starterQuestions = (course?.metadata?.starter_questions || []).filter((item) => typeof item === 'string').slice(0, 3)
  const practiceTopics = (course?.metadata?.practice_topics || []).filter((item) => typeof item === 'string')
  const selectedPracticeTopic = practiceTopics.includes(practiceTopic) ? practiceTopic : practiceTopics[0] || ''
  const officialSource = safeSourceUrl(course?.metadata?.sources?.[0]?.url)

  async function loadCourses() {
    setLoading(true); setLoadError('')
    try {
      const response = await fetch(`${API}/courses/`)
      if (!response.ok) throw new Error('load')
      const rows: Course[] = await response.json()
      setCourses(rows)
      const responseHistory=await fetch(`${API}/workspace/chat-history`)
      if(responseHistory.ok){const history:Conversation[]=await responseHistory.json();setConversations((local)=>{
        const sessions=new Set(history.map((item)=>item.sessionId))
        return [...local.filter((item)=>!item.sessionId || !sessions.has(item.sessionId)),...history.map((item)=>{
          const localItem=local.find((l)=>l.sessionId===item.sessionId)
          return localItem && localItem.messages.length>item.messages.length?{...item,messages:[...item.messages,...localItem.messages.slice(item.messages.length)]}:item
        })]
      })}

      setCourseId((current) => rows.some((row) => row.id === current) ? current : rows.find((row) => row.metadata?.teaching_default)?.id || rows[0]?.id || '')
      if (!rows.length) setLoadError('还没有可学习的课程，请先添加课程内容。')
    } catch { setLoadError('课程加载失败，请检查连接后重试。') }
    finally { setLoading(false) }
  }
  useEffect(()=>{const sessionId=params.get('session');const session=conversations.find((c)=>c.sessionId===sessionId);if(session){setActiveId(session.id);setCourseId(session.courseId)}else if(params.get('course')&&courses.some(c=>c.id===params.get('course'))){setCourseId(params.get('course')!)}},[params,conversations.length,courses.length])
  useEffect(()=>{if(params.get('practice')){setWelcomeMode('practice');setPracticeTopic(params.get('practice')||'')}},[params])
  useEffect(() => { void loadCourses(); return () => abortRef.current?.abort() }, [])
  useEffect(() => {
    const openChain = () => setDrawer({ type: 'chain' })
    window.addEventListener('metamind-open-chain', openChain)
    return () => window.removeEventListener('metamind-open-chain', openChain)
  }, [])
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations)); setStorageError('') }
    catch { setStorageError('浏览器暂时无法保存对话记录，离开页面前请复制需要保留的内容。') }
  }, [conversations])
  useEffect(() => {
    try {
      if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
      else localStorage.removeItem(ACTIVE_KEY)
    } catch { /* storage may be unavailable; the page still works this session */ }
  }, [activeId])
  useEffect(() => {
    if (!conversations.length) return
    if (activeId && conversations.some((item) => item.id === activeId)) return
    const latest = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)[0]
    if (latest) {
      setActiveId(latest.id)
      setCourseId(latest.courseId)
    }
  }, [conversations.length, activeId])
  useEffect(() => {
    if (stickToBottom.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages, generating, activeId])
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 180)}px`
    }
  }, [draft])

  function newChat(nextCourseId = courseId) {
    if (generating) return
    setActiveId(null); setCourseId(nextCourseId); setDraft(''); setHistoryOpen(false); setWelcomeMode('ask'); setPracticeTopic('')
    stickToBottom.current = true
    inputRef.current?.focus()
  }
  function patchConversation(id: string, update: (item: Conversation) => Conversation) {
    setConversations((items) => items.map((item) => item.id === id ? update(item) : item))
  }
  async function sendImage(file: File) {
    if (!file.type.startsWith('image/')) {
      setStorageError('请选择图片文件。')
      return
    }
    if (file.size > 7 * 1024 * 1024) {
      setStorageError('图片请控制在 7 MB 内。')
      return
    }
    const question = draft.trim() || '这张图里我卡在哪？请结合当前知识点和策略讲。'
    const image = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(new Error('图片读取失败'))
      reader.readAsDataURL(file)
    })
    setVision({ image, question, answer: '', busy: true })
    setDraft('')
    try {
      const response = await fetch('/teach-api/teach/ask-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image, question }),
      })
      const result = await response.json()
      if (!response.ok || result.ok === false) throw new Error(result.error || '图片问答失败')
      setVision({ image, question, answer: result.answer, busy: false })
    } catch (cause) {
      setVision({ image, question, answer: `图片问答失败：${(cause as Error).message}`, busy: false })
    }
  }

  async function saveToMemory() {
    const text = draft.trim()
    if (!text) {
      setStorageError('先写一句或说一句，再存为记忆。')
      return
    }
    setRouteBusy(true)
    try {
      const response = await fetch('/teach-api/teach/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, source: 'qa' }),
      })
      const result = await response.json()
      if (!response.ok || result.ok === false) throw new Error(result.error || '保存失败')
      setStorageError(result.message || '已存入记忆')
      setDraft('')
    } catch (cause) {
      setStorageError((cause as Error).message)
    } finally {
      setRouteBusy(false)
    }
  }

  async function openWebcam() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      })
      cameraStreamRef.current = stream
      setCameraOpen(true)
    } catch {
      setStorageError('电脑摄像头不可用，请检查权限或改用上传图片。')
    }
  }

  function closeWebcam() {
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop())
    cameraStreamRef.current = null
    setCameraOpen(false)
  }

  async function captureWebcam() {
    const video = videoRef.current
    if (!video) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth || 1280
    canvas.height = video.videoHeight || 720
    canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
    canvas.toBlob((blob) => {
      if (!blob) return
      const file = new File([blob], 'camera.jpg', { type: 'image/jpeg' })
      closeWebcam()
      void sendImage(file)
    }, 'image/jpeg', 0.9)
  }

  useEffect(() => {
    if (cameraOpen && videoRef.current && cameraStreamRef.current) {
      videoRef.current.srcObject = cameraStreamRef.current
      void videoRef.current.play().catch(() => {})
    }
  }, [cameraOpen])

  async function toggleVoice() {
    if (voiceRecording) {
      voiceRecorderRef.current?.stop()
      setVoiceRecording(false)
      return
    }
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices) {
      setStorageError('这台设备不支持录音，可以直接输入。')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      voiceRecorderRef.current = recorder
      voiceChunks.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) voiceChunks.current.push(event.data)
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        setVoiceBusy(true)
        try {
          const blob = new Blob(voiceChunks.current, { type: recorder.mimeType || 'audio/webm' })
          const response = await fetch('/teach-api/teach/asr', {
            method: 'POST',
            headers: { 'Content-Type': blob.type || 'audio/webm' },
            body: blob,
          })
          const result = await response.json()
          if (!response.ok || result.ok === false) throw new Error(result.error || '语音转写失败')
          setDraft((current) => (current ? `${current}\n${result.text}` : result.text))
        } catch (cause) {
          setStorageError((cause as Error).message)
        } finally {
          setVoiceBusy(false)
        }
      }
      recorder.start()
      setVoiceRecording(true)
    } catch {
      setStorageError('麦克风不可用，可以直接输入。')
    }
  }

  async function sendMessage(text = draft, retry = false, practiceSubmission?:{exercise:Exercise;answer:string;reason?:string}) {
    const content = text.trim()
    if (!courseId || generating || (!content && !retry)) return
    const pending=[...messages].reverse().find((m)=>m.exercise && !m.exercise.answered)?.exercise
    const typedChoice=pending?readPracticeAnswer(content,pending):null
    if(!practiceSubmission && typedChoice && pending)practiceSubmission={exercise:pending,answer:typedChoice,reason:/因为|理由|原因|所以/.test(content)?content:undefined}
    const prior = retry ? messages.slice(0, -1) : messages
    const lastUser = [...prior].reverse().find((item) => item.role === 'user')
    const message = retry ? lastUser?.content : content
    if (!message) return
    const history = retry ? prior.slice(0, -1) : prior
    const id = activeId || crypto.randomUUID()
    const assistantId = crypto.randomUUID()
    const nextMessages: Message[] = [...prior, ...(!retry ? [{ id: crypto.randomUUID(), role: 'user' as const, content: message }] : []), { id: assistantId, role: 'assistant', content: '' }]
    if (!active) {
      setConversations((items) => [{ id, courseId, title: message.slice(0, 30), messages: nextMessages, updatedAt: Date.now() }, ...items])
      setActiveId(id)
    } else patchConversation(id, (item) => ({ ...item, messages: nextMessages, updatedAt: Date.now() }))
    setDraft(''); setGenerating(true); stickToBottom.current = true
    const controller = new AbortController()
    abortRef.current = controller
    let assistantText = ''
    const updateMessage = (error?: string) => patchConversation(id, (item) => ({ ...item, messages: item.messages.map((m) => m.id === assistantId ? { ...m, content: assistantText, error } : m) }))
    try {
      if(practiceSubmission){
        const {exercise,answer:submitted}=practiceSubmission
        const response=await fetch(`${API}/workspace/practice/answer`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({problem_id:exercise.id,answer:submitted,session_id:active?.sessionId,request_id:exercise.submission_id,response_text:message,reason:practiceSubmission.reason})})
        const result=await response.json();if(!response.ok)throw new Error(result.detail||'提交失败')
        assistantText=(result.is_correct?'答对了。':'这次还没答对。'+(exercise.options?'正确选项是 '+String(result.correct_answer||'').toUpperCase():'参考答案是 '+String(result.correct_answer||''))+'。')+'\n\n'+(result.explanation||'')
        patchConversation(id,(item)=>({...item,messages:item.messages.map((m)=>m.exercise?.submission_id===exercise.submission_id?{...m,exercise:{...m.exercise,answered:true}}:m)}))
        updateMessage();refreshLearning();return
      }
      const [classroom, overview] = await Promise.all([
        fetch('/teach-api/teach/context', { signal: controller.signal })
          .then((row) => (row.ok ? row.json() : null))
          .catch(() => null),
        fetch(`${API}/workspace/overview`, { signal: controller.signal })
          .then((row) => (row.ok ? row.json() : null))
          .catch(() => null),
      ])
      const v2 = (overview?.versions || []).find((row: any) => row.version === 2 && row.status === 'active')
      const classroomContext = classroom
        ? { ...classroom, opentutor_version: v2 ? `V${v2.version}` : '', opentutor_skill: v2?.name || '' }
        : null
      const response = await fetch(`${API}/chat/`, {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ course_id: courseId, message, session_id: active?.sessionId, scene: 'tutoring', learning_mode: 'self_paced', tab_context:{...(welcomeMode==='practice'?{topic:selectedPracticeTopic}:{}),...(classroomContext?{metamind:classroomContext}:{})}, history: history.filter((m) => !m.error).map(({ role, content: value }) => ({ role, content: role === 'assistant' ? cleanReply(value) : value })) }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || `请求失败 ${response.status}`)
      }
      if (!response.body) throw new Error('empty')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let completed = false
      function processFrame(frame: string) {
        const event = frame.match(/^event:\s*(.+)$/m)?.[1]?.trim()
        const data = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
        if (!data) return
        let payload: Record<string, unknown>
        try { payload = JSON.parse(data) } catch { return }
        if (event === 'session' && typeof payload.session_id === 'string') {
          const sessionId = payload.session_id
          patchConversation(id, (item) => ({ ...item, sessionId }))
        }
        if(event==='exercise')patchConversation(id,(item)=>({...item,messages:item.messages.map((m)=>m.id===assistantId?{...m,exercise:payload as unknown as Exercise}:m)}))
        if (event === 'done') {
          completed = true
          const provenance = payload.provenance as { content_refs?: { title?: string; excerpt?: string; preview?: string; url?: string }[] } | undefined
          const references = (provenance?.content_refs || []).filter((ref) => typeof ref.title === 'string' && !!(ref.excerpt || ref.preview)).map((ref) => ({ title: ref.title!, excerpt: ref.excerpt || ref.preview || '', url: safeSourceUrl(ref.url) }))
          patchConversation(id, (item) => ({ ...item, messages: item.messages.map((m) => m.id === assistantId ? { ...m, references } : m) }))
        }
        if (event === 'error') throw new Error(String(payload.error || 'error'))
        if (event === 'message' && typeof payload.content === 'string') assistantText += payload.content
        if (event === 'replace' && typeof payload.content === 'string') assistantText = payload.content
        if (event === 'message' || event === 'replace') updateMessage()
      }
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        frames.forEach(processFrame)
        if (done) { if (buffer.trim()) processFrame(buffer); break }
      }
      // 有些后端（比如 5001 记录执行状态时出错）会在正文流完之后才异常退出，
      // 浏览器拿不到 done 事件。只要已经收到正文，就当这轮回复成功，不再弹黄色警告。
      if (!assistantText.trim()) {
        updateMessage('还没有收到回复，请重试。')
      } else if (!completed) {
        console.warn('SSE 流在 done 之前结束，但已收到正文，按成功处理。')
      }
    } catch (cause) {
      if (controller.signal.aborted) updateMessage('已停止生成。')
      else updateMessage(friendlyError(cause))
    } finally { abortRef.current = null; setGenerating(false); inputRef.current?.focus() }
  }
  async function copyMessage(message: Message) {
    try { await navigator.clipboard.writeText(copyableReply(message.content)); setCopiedId(message.id); window.setTimeout(() => setCopiedId(''), 1800) }
    catch { setStorageError('复制失败，请选中文字后手动复制。') }
  }

  return <div className="tutor-page">
    <header className="tutor-header">
      <div className="tutor-header-left"><button className={`tutor-icon ${historyOpen ? 'is-active' : ''}`} title="历史对话" aria-label="历史对话" aria-expanded={historyOpen} onClick={() => setHistoryOpen(!historyOpen)}><History size={19}/></button><span className="tutor-header-divider"/><div><h1>问答助手 <span>AI</span></h1><p>一起把问题弄明白</p></div></div>
      <div className="tutor-header-actions"><label className="tutor-course"><BookOpen size={15}/><select aria-label="选择学习课程" value={courseId} disabled={generating || loading} onChange={(e) => newChat(e.target.value)}><option value="" disabled>{loading ? '正在加载课程…' : '选择课程'}</option>{courses.filter((course) => course.metadata?.data_kind !== 'synthetic_demo' || course.id === courseId).map((course) => <option key={course.id} value={course.id}>{course.name.replace('METAMIND 演示', '').replace(/^[ ·—\-|｜]+/, '') || course.name}</option>)}</select><ChevronDown size={13}/></label><button className="tutor-new" disabled={generating} onClick={() => newChat()}><Plus size={17}/><span>新对话</span></button></div>
    </header>
    <ContextPill
      onAsk={(prompt) => void sendMessage(prompt)}
      onOpenCompare={(courseId) => setDrawer({ type: 'compare', courseId })}
      onOpenChain={() => setDrawer({ type: 'chain' })}
      onOpenGraph={(courseId) => setDrawer({ type: 'graph', courseId })}
    />
    <div className="tutor-body">
      {historyOpen && <><button className="tutor-history-backdrop" aria-label="关闭历史对话" onClick={() => setHistoryOpen(false)}/><aside className="tutor-history"><div className="tutor-history-heading"><h2>历史对话</h2><button className="tutor-icon" aria-label="收起历史对话" onClick={() => setHistoryOpen(false)}><PanelLeftClose size={17}/></button></div><button className="tutor-history-new" disabled={generating} onClick={() => newChat()}><Plus size={16}/>开始新对话</button><p className="tutor-history-caption">保存在当前浏览器</p><div className="tutor-history-list">{!conversations.length && <p className="tutor-history-empty">你的对话会出现在这里。</p>}{[...conversations].sort((a,b) => b.updatedAt - a.updatedAt).map((item) => <div className={`tutor-history-item ${item.id === activeId ? 'active' : ''}`} key={item.id}><button disabled={generating} onClick={() => { setActiveId(item.id); setCourseId(item.courseId); setDraft(''); setHistoryOpen(false); stickToBottom.current = true }}><MessageSquare size={14}/><span>{item.title}</span></button><button disabled={generating} className="tutor-delete" aria-label={`删除对话：${item.title}`} onClick={async() => { try{if(item.sessionId){const response=await fetch(`${API}/workspace/chat-history/${item.sessionId}`,{method:'DELETE'});if(!response.ok&&response.status!==404)throw new Error('删除未完成，请重试。')}setConversations((rows) => rows.filter((row) => row.id !== item.id)); if (activeId === item.id) newChat()}catch(cause){setStorageError((cause as Error).message)} }}><Trash2 size={14}/></button></div>)}</div></aside></>}
      <section className="tutor-chat" aria-label="与问答助手对话">
        <div className="tutor-scroll" ref={scrollRef} onScroll={(e) => { const el = e.currentTarget; stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; setShowScroll(!stickToBottom.current) }}>
          {!messages.length ? <div className="tutor-welcome">
            <div className="tutor-emblem"><BookOpen size={29} strokeWidth={1.5}/></div>
            <span className="tutor-kicker">{loading ? '正在加载课程' : '当前课程'}</span>
            <h2>{course?.name.split('｜')[0] || '开始你的学习对话'}</h2>
            {officialSource && course?.metadata?.source_label && <a className="tutor-course-source" href={officialSource} target="_blank" rel="noopener noreferrer"><BookOpen size={12}/>{course.metadata.source_label}</a>}
            <div className="tutor-entry-tabs" role="tablist" aria-label="选择学习方式">
              <button id="tutor-ask-tab" type="button" role="tab" aria-controls="tutor-entry-panel" aria-selected={welcomeMode === 'ask'} onClick={() => setWelcomeMode('ask')}>问问题</button>
              <button id="tutor-practice-tab" type="button" role="tab" aria-controls="tutor-entry-panel" aria-selected={welcomeMode === 'practice'} onClick={() => setWelcomeMode('practice')}>做练习</button>
            </div>
            <div id="tutor-entry-panel" role="tabpanel" aria-labelledby={welcomeMode === 'ask' ? 'tutor-ask-tab' : 'tutor-practice-tab'} className="tutor-entry-panel">
              {welcomeMode === 'ask' ? <><p>哪里没弄懂？直接问，或把题目、代码贴过来。</p>{!!starterQuestions.length && <><span className="tutor-entry-caption">比如，你可以这样问</span><div className="tutor-starter-questions">{starterQuestions.map((question) => <button type="button" key={question} disabled={!courseId || generating} onClick={() => void sendMessage(question)}>{question}<ArrowUp size={14}/></button>)}</div></>}</> : <><p>{practiceTopics.length ? '选个知识点，做一道题。' : '从这门课的基础知识开始，做一道题。'}</p><div className="tutor-practice-topics" aria-label="练习知识点">{practiceTopics.map((topic) => <button type="button" key={topic} aria-pressed={selectedPracticeTopic === topic} onClick={() => setPracticeTopic(topic)}>{topic}</button>)}</div><button type="button" className="tutor-start-practice" disabled={!courseId || generating || loading} onClick={() => void sendMessage(selectedPracticeTopic ? `给我一道${selectedPracticeTopic}的基础练习题。` : '给我一道这门课的基础练习题。')}>开始练习<ArrowUp size={15}/></button></>}
            </div>
          </div> : <div className="tutor-messages">{messages.map((message, index) => <article key={message.id} className={`tutor-message ${message.role}`} aria-label={message.role === 'user' ? '你的消息' : '问答助手的回复'}>{message.role === 'assistant' && <div className="tutor-message-avatar"><Sparkles size={17}/></div>}<div className="tutor-message-content">{message.role === 'user' ? <div className="tutor-user-text">{message.content}</div> : <><div className="tutor-assistant-name">问答助手</div>{message.content && <TutorReply content={message.content} references={message.references}/>}{!message.content && !message.error && generating && <div className="tutor-thinking" role="status"><span/><span/><span/><span className="sr-only">正在思考</span></div>}{generating && index === messages.length - 1 && !!message.content && !message.error && <p className="tutor-generating" role="status">正在回复…</p>}{message.exercise && <div className="tutor-exercise">
                  {!!message.exercise.options && <div className="tutor-answer-options">{Object.entries(message.exercise.options).map(([key,value])=><button type="button" key={key} aria-pressed={(practiceChoices[message.id]||[]).includes(key)} disabled={generating||message.exercise?.answered} onClick={()=>setPracticeChoices((old)=>({...old,[message.id]:message.exercise?.question_type==='select_all'?(old[message.id]||[]).includes(key)?old[message.id].filter((v)=>v!==key):[...(old[message.id]||[]),key]:[key]}))}><b>{key.toUpperCase()}</b>{value}</button>)}</div>}
                  {!message.exercise.answered&&message.exercise.options&&<textarea aria-label="解题思路（选填）" className="tutor-answer-reason" placeholder="也可以写下你的思路（选填）" maxLength={4000} rows={2} value={practiceReasons[message.id]||''} onChange={e=>setPracticeReasons(old=>({...old,[message.id]:e.target.value}))}/>}
                  {message.exercise.answered?<span className="tutor-answer-saved"><Check size={13}/>作答已保存到学习记录</span>:message.exercise.options?<button type="button" className="tutor-start-practice" disabled={generating||!practiceChoices[message.id]?.length} onClick={()=>void sendMessage(practiceChoices[message.id].map((key)=>`${key.toUpperCase()}. ${message.exercise?.options?.[key]}`).join('；')+(practiceReasons[message.id]?'\n'+practiceReasons[message.id]:''),false,{exercise:message.exercise!,answer:practiceChoices[message.id].join(','),reason:practiceReasons[message.id]||undefined})}>提交答案</button>:<span className="muted">在下方输入答案后提交。</span>}
                </div>}{message.error && <p className="tutor-message-error" role="alert">{message.error}</p>}{!generating && <div className="tutor-message-tools">{message.content && <button className="tutor-icon" title={copiedId === message.id ? '已复制' : '复制回复'} aria-label="复制回复" onClick={() => void copyMessage(message)}>{copiedId === message.id ? <Check size={15}/> : <Copy size={15}/>}</button>}{index === messages.length - 1 && <button className="tutor-retry" onClick={() => void sendMessage('', true)}><RotateCcw size={14}/>{message.error ? '重试' : '重新生成'}</button>}</div>}</>}</div></article>)}</div>}
        </div>
        {vision && (
          <div className="tutor-vision-panel">
            <button className="tutor-vision-close" aria-label="关闭图片问答" onClick={() => setVision(null)}>
              <X size={15} />
            </button>
            <div className="tutor-vision-head">
              <img src={vision.image} alt="上传的题目" />
              <div>
                <small>图片问答 · 视觉模型</small>
                <p>{vision.question}</p>
              </div>
            </div>
            {vision.busy ? (
              <p className="muted">正在看图…</p>
            ) : (
              <div className="tutor-vision-answer">
                <TutorReply content={vision.answer} />
              </div>
            )}
          </div>
        )}
        <div className="tutor-compose-area">{showScroll && <button className="tutor-scroll-bottom" aria-label="回到最新消息" onClick={() => { stickToBottom.current = true; scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }) }}><ArrowDown size={17}/></button>}
          {loadError && <div className="tutor-inline-notice" role="alert">{loadError}<button onClick={() => void loadCourses()}>重新加载</button></div>}{storageError && <div className="tutor-inline-notice" role="status">{storageError}<button aria-label="关闭提示" onClick={() => setStorageError('')}><X size={14}/></button></div>}
          <form className="tutor-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage() }}><textarea ref={inputRef} aria-label="输入你的问题" rows={1} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void sendMessage() } }} placeholder={loading ? '正在准备学习内容…' : '输入问题，或粘贴你不会的题目…'}/><div className="tutor-composer-bottom"><div className="tutor-composer-tools"><input ref={imageInputRef} type="file" accept="image/*" capture="environment" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void sendImage(file); event.target.value = '' }} /><button type="button" className="tutor-tool" title="拍照或上传图片" aria-label="拍照或上传图片" onClick={() => imageInputRef.current?.click()}><Camera size={16}/></button><button type="button" className="tutor-tool" title="用电脑摄像头" aria-label="用电脑摄像头" onClick={() => void openWebcam()}><Video size={16}/></button><button type="button" className={voiceRecording ? 'tutor-tool recording' : 'tutor-tool'} title={voiceRecording ? '停止录音' : '语音输入'} aria-label={voiceRecording ? '停止录音' : '语音输入'} disabled={voiceBusy} onClick={() => void toggleVoice()}><Mic size={16}/></button><button type="button" className="tutor-tool" title="存为记忆/待办" aria-label="存为记忆/待办" disabled={routeBusy} onClick={() => void saveToMemory()}><Database size={16}/></button><span><Sparkles size={13}/>{voiceBusy ? '正在转写…' : '陪你理解，一起探索'}</span></div>{generating ? <button type="button" className="tutor-send" title="停止生成" aria-label="停止生成" onClick={() => abortRef.current?.abort()}><Square size={15} fill="currentColor"/></button> : <button type="submit" className="tutor-send" title="发送消息" aria-label="发送消息" disabled={!draft.trim() || !courseId || loading}><ArrowUp size={20}/></button>}</div></form>
          <p className="tutor-footnote"><span>AI 的回答可能有误，请结合课程内容判断。</span><span>Enter 发送 · Shift + Enter 换行</span></p>
        </div>
      </section>
    </div>
    {cameraOpen && (
      <div className="tutor-camera-backdrop" onClick={closeWebcam}>
        <div className="tutor-camera" onClick={(event) => event.stopPropagation()}>
          <video ref={videoRef} autoPlay playsInline muted />
          <div className="tutor-camera-actions">
            <button type="button" className="primary" onClick={() => void captureWebcam()}>
              拍照并提问
            </button>
            <button type="button" className="secondary" onClick={closeWebcam}>
              取消
            </button>
          </div>
        </div>
      </div>
    )}
    {drawer && (
      <ContextDrawer
        type={drawer.type}
        courseId={drawer.courseId}
        question={lastUserMessage?.content}
        answer={lastAnswerMessage?.content}
        references={lastAnswerMessage?.references}
        onClose={() => setDrawer(null)}
      />
    )}
  </div>
}
