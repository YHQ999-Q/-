import {useEffect,useState} from 'react'
import {Link} from 'react-router-dom'
import {ArrowLeft,ArrowRight,Pause,Play,RefreshCw,Sparkles} from 'lucide-react'
import './GrowthInsights.css'

type Evidence={id:string;type:string;topic:string;label:string;question?:string;quote:string;at?:string;session_id?:string}
type Card={id:string;kind:'observation'|'hypothesis';title:string;body:string;suggestion:string;evidence:Evidence[]}

export function GrowthInsights({courseId}:{courseId?:string}){
 const [cards,setCards]=useState<Card[]>([]),[index,setIndex]=useState(0),[loading,setLoading]=useState(false),[error,setError]=useState(''),[revision,setRevision]=useState(0),[paused,setPaused]=useState(false),[interacting,setInteracting]=useState(false),[expanded,setExpanded]=useState(false),[reduced,setReduced]=useState(false)
 useEffect(()=>{const media=matchMedia('(prefers-reduced-motion: reduce)');const update=()=>setReduced(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[])
 useEffect(()=>{
  if(!courseId)return
  const controller=new AbortController();let active=true;if(!revision){setCards([]);setIndex(0);setExpanded(false)}setLoading(true);setError('')
  void fetch('/opentutor-api/api/workspace/growth-insights/'+encodeURIComponent(courseId)+(revision?'?refresh=true':''),{signal:controller.signal}).then(async response=>{const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'成长洞察暂时无法生成');if(active){setCards(data.cards||[]);setIndex(0);setExpanded(false)}}).catch(cause=>{if(active&&cause.name!=='AbortError')setError(cause.message)}).finally(()=>{if(active)setLoading(false)})
  return()=>{active=false;controller.abort()}
 },[courseId,revision])
 useEffect(()=>{
  if(paused||interacting||expanded||reduced||cards.length<2)return
  const timer=setInterval(()=>{if(!document.hidden)setIndex(i=>(i+1)%cards.length)},10000)
  return()=>clearInterval(timer)
 },[paused,interacting,expanded,reduced,cards.length,index])
 function move(next:number){setIndex((next+cards.length)%cards.length);setExpanded(false);setPaused(true)}
 const card=cards[index]
 return <section className="growth-insights" aria-label="学习成长洞察" aria-roledescription="轮转卡片" onMouseEnter={()=>setInteracting(true)} onMouseLeave={()=>setInteracting(false)} onFocusCapture={()=>setInteracting(true)} onBlurCapture={event=>{if(!event.currentTarget.contains(event.relatedTarget))setInteracting(false)}}><header><div><Sparkles size={17}/><span>给下一步的你</span></div><button className="growth-insight-refresh" disabled={loading||!courseId} onClick={()=>setRevision(n=>n+1)}><RefreshCw size={14}/>{loading?'正在更新…':'更新洞察'}</button></header>{error&&card&&<p className="growth-insight-error" role="alert">{error}。仍显示上次建议，稍后可以重试。</p>}{loading&&!card?<div className="growth-insight-empty" role="status">正在结合你的学习记录，整理下一步可以尝试的方向…</div>:error&&!card?<div className="growth-insight-empty" role="alert">{error}<p>可以稍后点击“更新洞察”重试。</p></div>:card?<><article key={card.id} className="growth-insight-card" aria-label={`洞察 ${index+1}：${card.title}`}><span className="growth-insight-kind">{card.kind==='hypothesis'?'先试一个小改变':'从你的学习经历出发'}</span><h2>{card.title}</h2><p className="growth-insight-body">{card.body}</p><div className="growth-insight-action"><b>从这里开始</b><p>{card.suggestion}</p></div><details open={expanded} onToggle={event=>setExpanded(event.currentTarget.open)}><summary>为什么建议这个方向</summary><div>{card.evidence.map(source=><blockquote key={source.id}><header><b>{source.topic}</b>{source.at&&<time>{new Date(source.at).toLocaleDateString('zh-CN')}</time>}</header>{source.question&&<p className="growth-insight-question">当时的问题：{source.question}</p>}<p className="growth-insight-answer">{source.type==='classroom'?'你的回答：':'记录：'}{source.quote}</p>{source.type==='evaluation'&&<small>{source.label}</small>}{source.type==='classroom'&&source.session_id&&<Link to={'/capture-agent?course='+encodeURIComponent(courseId!)+'&session='+encodeURIComponent(source.session_id)}>查看对应课堂 ↗</Link>}</blockquote>)}</div></details></article><footer><div className="growth-insight-dots" role="group" aria-label="选择成长洞察">{cards.map((item,i)=><button key={item.id} className={index===i?'active':''} aria-label={`查看第 ${i+1} 条洞察`} aria-pressed={index===i} onClick={()=>move(i)}/>)}</div><div className="growth-insight-controls"><span aria-live="off">{index+1} / {cards.length}</span>{cards.length>1&&<><button aria-label="上一条洞察" onClick={()=>move(index-1)}><ArrowLeft size={16}/></button><button aria-label={paused||reduced?'播放洞察轮转':'暂停洞察轮转'} disabled={reduced} onClick={()=>setPaused(v=>!v)}>{paused||reduced?<Play size={15}/>:<Pause size={15}/>}</button><button aria-label="下一条洞察" onClick={()=>move(index+1)}><ArrowRight size={16}/></button></>}</div></footer></>:<div className="growth-insight-empty"><h2>从一次真实的思考开始</h2><p>用自己的话解释一次，或尝试一个新例子。有了真实表现后，这里会给出更适合你的下一步方向。</p><Link to={'/capture-agent'+(courseId?'?course='+encodeURIComponent(courseId):'')}>去课堂学一会儿 <ArrowRight size={14}/></Link></div>}</section>
}
