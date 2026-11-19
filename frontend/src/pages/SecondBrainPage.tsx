import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, Brain, Lightbulb, ListChecks, Mic, RefreshCw, Sparkles } from 'lucide-react'
import { KnowledgePanel } from '../components/KnowledgePanel'
import './SecondBrainPage.css'

const API = '/teach-api'

type Fact = { id: number; text: string; tags?: string[]; source?: string; date?: string; status?: string }
type Insight = {
  id: number
  insight_id?: string
  title: string
  description: string
  suggested_actions?: string
  date?: string
  kind?: string
}
type Todo = { id: number; text: string; done: boolean; due?: string; kp?: string; status?: string }
type Snapshot = { facts: Fact[]; insights: Insight[]; todos: Todo[]; openTodos: number; daily: Insight[] }

/**
 * Second Brain → Teaching Agent.
 *
 * The Bee corpus was imported by the teaching service (corpus_pack.py) into
 * its own SQLite tables: facts / insights / todos.  This page reads those
 * real rows; it does not invent memory.  The teaching Agent's plan/profile
 * already consume the same tables, so what you see here is what it can use.
 */
export function SecondBrainPage() {
  const [data, setData] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [moods, setMoods] = useState<{ id: number; text: string; time: string }[]>([])
  const [mood, setMood] = useState('')
  const [moodText, setMoodText] = useState('')
  const [moodBusy, setMoodBusy] = useState(false)
  const [moodNotice, setMoodNotice] = useState('')
  const [recording, setRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [comfort, setComfort] = useState<{ text: string; mood: string; reply: string } | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'knowledge' ? 'knowledge' : 'brain'
  function setTab(next: 'brain' | 'knowledge') {
    const nextParams = new URLSearchParams(params)
    if (next === 'knowledge') nextParams.set('tab', 'knowledge')
    else nextParams.delete('tab')
    setParams(nextParams, { replace: true })
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [factsRes, insightsRes, todosRes, moodsRes] = await Promise.all([
        fetch(`${API}/facts`),
        fetch(`${API}/insights`),
        fetch(`${API}/todos?status=open`),
        fetch(`${API}/teach/mood`),
      ])
      if (!factsRes.ok || !insightsRes.ok || !todosRes.ok) throw new Error('第二大脑服务暂未启动')
      const facts = await factsRes.json()
      const insights = await insightsRes.json()
      const todos = await todosRes.json()
      setData({
        facts: Array.isArray(facts.facts) ? facts.facts : [],
        insights: Array.isArray(insights.insights) ? insights.insights : [],
        daily: Array.isArray(insights.daily) ? insights.daily : [],
        todos: Array.isArray(todos.todos) ? todos.todos : [],
        openTodos: todos.counts?.open ?? 0,
      })
      if (moodsRes.ok) {
        const moodData = await moodsRes.json()
        setMoods(Array.isArray(moodData.items) ? moodData.items : [])
      }
    } catch (cause) {
      setError((cause as Error).message || '第二大脑服务暂未启动')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function sendVoice(blob: Blob) {
    setVoiceBusy(true)
    setMoodNotice('')
    try {
      const response = await fetch(`${API}/teach/mood/voice`, {
        method: 'POST',
        headers: { 'Content-Type': blob.type || 'audio/webm' },
        body: blob,
      })
      const result = await response.json()
      if (!response.ok || result.ok === false) throw new Error(result.error || '语音处理失败')
      setComfort({ text: result.text, mood: result.mood, reply: result.reply })
      setMoodText('')
      const refreshed = await fetch(`${API}/teach/mood`).then((row) => row.json())
      setMoods(Array.isArray(refreshed.items) ? refreshed.items : [])
    } catch (cause) {
      setMoodNotice((cause as Error).message)
    } finally {
      setVoiceBusy(false)
    }
  }

  async function toggleVoice() {
    if (recording) {
      recorderRef.current?.stop()
      setRecording(false)
      return
    }
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices) {
      setMoodNotice('这台设备不支持录音，可以直接输入。')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      recorderRef.current = recorder
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        void sendVoice(blob)
      }
      recorder.start()
      setRecording(true)
    } catch {
      setMoodNotice('麦克风不可用，可以直接输入。')
    }
  }

  async function saveMood() {
    if (!mood && !moodText.trim()) return
    setMoodBusy(true)
    setMoodNotice('')
    try {
      const response = await fetch(`${API}/teach/mood`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mood, text: moodText.trim() }),
      })
      const result = await response.json()
      if (!response.ok || result.ok === false) throw new Error(result.error || '记录失败')
      setMoodNotice('已记下。下一轮讲解会先共情、再降一点认知负荷。')
      setMoodText('')
      const refreshed = await fetch(`${API}/teach/mood`).then((row) => row.json())
      setMoods(Array.isArray(refreshed.items) ? refreshed.items : [])
    } catch (cause) {
      setMoodNotice((cause as Error).message)
    } finally {
      setMoodBusy(false)
    }
  }

  return (
    <>
      <section className="panel brain-hero">
        <div>
          <span className="eyebrow">SECOND BRAIN · 已接入教学 Agent</span>
          <h1>第二大脑</h1>
          <p className="muted">
            课堂录音与文本由 Bee 流程整理成纪要、事实、待办和洞察，导入教学库；
            教学 Agent 的今日计划、错因画像和讲解会读取同一批记录。
          </p>
        </div>
        <div className="brain-actions">
          <button className="secondary" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={15} /> {loading ? '正在读取…' : '刷新'}
          </button>
          <Link className="primary" to="/capture-agent">
            去课堂采集 <ArrowRight size={15} />
          </Link>
        </div>
      </section>

      <div className="brain-tabs" role="tablist" aria-label="第二大脑分区">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'brain'}
          className={tab === 'brain' ? 'active' : ''}
          onClick={() => setTab('brain')}
        >
          <Brain size={15} /> 脑内记忆
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'knowledge'}
          className={tab === 'knowledge' ? 'active' : ''}
          onClick={() => setTab('knowledge')}
        >
          课程知识库
        </button>
      </div>

      {tab === 'knowledge' && <KnowledgePanel embedded />}

      {tab === 'brain' && (<>
      {error && (
        <section className="panel">
          <p className="error">{error}</p>
          <p className="muted">
            先确认 5001 教学服务已启动（整合包里的「启动全部.bat」会一并拉起）。
          </p>
        </section>
      )}

      {data && (
        <>
          <section className="panel mood-panel">
            <div className="section-top">
              <h2>今天的状态</h2>
              <span className="muted">不是策略，是让 Agent 先照顾你的负荷</span>
            </div>
            <p className="muted">
              顺手记一条心境或反思。下一轮教学/问答会读到它；如果状态不好，
              先共情、少讲一点，再继续引导。
            </p>
            <div className="mood-chips" role="group" aria-label="选择今天的状态">
              {['焦虑', '挫败', '疲惫', '正常'].map((item) => (
                <button
                  type="button"
                  key={item}
                  className={mood === item ? 'selected' : ''}
                  onClick={() => setMood(mood === item ? '' : item)}
                >
                  {item}
                </button>
              ))}
            </div>
            <form
              className="mood-form"
              onSubmit={(event) => {
                event.preventDefault()
                void saveMood()
              }}
            >
              <input
                value={moodText}
                maxLength={200}
                placeholder="写一句：今天哪一步最卡？/ 现在有点累，想少做一点…"
                onChange={(event) => setMoodText(event.target.value)}
              />
              <button className="primary" disabled={moodBusy || (!mood && !moodText.trim())}>
                {moodBusy ? '正在记…' : '记一笔'}
              </button>
            </form>
            <div className="mood-voice">
              <button
                type="button"
                className={recording ? 'secondary recording' : 'secondary'}
                onClick={() => void toggleVoice()}
                disabled={voiceBusy}
              >
                <Mic size={15} /> {recording ? '停止并识别' : voiceBusy ? '正在听…' : '语音说一句'}
              </button>
              <small className="muted">说“我今天有点累 / 这题做不出来”，会先识别再回一句安慰。</small>
            </div>
            {comfort && (
              <article className="mood-comfort">
                <small>听到：{comfort.text}</small>
                <b>{comfort.mood}</b>
                <p>{comfort.reply}</p>
              </article>
            )}
            {moodNotice && <p className="notice">{moodNotice}</p>}
            {moods.length ? (
              <div className="mood-list">
                {moods.slice(0, 3).map((item) => (
                  <article key={item.id}>
                    <p>{item.text}</p>
                    <small>{item.time}</small>
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted">还没有记录。写一句，下一轮讲解会降一点负荷。</p>
            )}
          </section>

          <section className="brain-stats">
            <article className="panel brain-stat">
              <Brain size={20} />
              <b>{data.facts.length}</b>
              <span>已入库事实</span>
            </article>
            <article className="panel brain-stat">
              <Lightbulb size={20} />
              <b>{data.insights.length}</b>
              <span>成长洞察</span>
            </article>
            <article className="panel brain-stat">
              <ListChecks size={20} />
              <b>{data.openTodos}</b>
              <span>未完成待办</span>
            </article>
          </section>

          <section className="panel">
            <div className="section-top">
              <h2>最近的洞察</h2>
              <span className="muted">来自真实记录，不是模型编的故事</span>
            </div>
            {data.insights.slice(0, 4).map((item) => (
              <article className="brain-item" key={item.insight_id || item.id}>
                <div className="brain-item-title">
                  <Sparkles size={15} />
                  <b>{item.title || '一条成长线索'}</b>
                  {item.date && <small>{item.date}</small>}
                </div>
                <p>{item.description}</p>
                {item.suggested_actions && <p className="brain-next">下一步：{item.suggested_actions}</p>}
              </article>
            ))}
            {!data.insights.length && <p className="muted">还没有洞察。先导入一段课堂录音或记录。</p>}
          </section>

          <div className="brain-grid">
            <section className="panel">
              <h2>已确认事实</h2>
              <p className="muted">教学 Agent 做诊断时会参考这些事实。</p>
              {data.facts.slice(0, 8).map((fact) => (
                <article className="brain-fact" key={fact.id}>
                  <p>{fact.text}</p>
                  <small>
                    {fact.date || '—'} · {fact.source || '课堂记录'}
                    {fact.tags?.length ? ` · ${fact.tags.slice(0, 2).join(' / ')}` : ''}
                  </small>
                </article>
              ))}
              {!data.facts.length && <p className="muted">还没有事实记录。</p>}
            </section>

            <section className="panel">
              <h2>接下来要做</h2>
              <p className="muted">课堂里冒出来的任务，会变成下一次练习的起点。</p>
              {data.todos.slice(0, 8).map((todo) => (
                <article className="brain-todo" key={todo.id}>
                  <span>{todo.text}</span>
                  <small>{todo.due || '未设定时间'}{todo.kp ? ` · ${todo.kp}` : ''}</small>
                </article>
              ))}
              {!data.todos.length && <p className="muted">没有未完成待办。</p>}
            </section>
          </div>
        </>
      )}
      </>)}
    </>
  )
}
