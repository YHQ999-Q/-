import {useEffect,useState} from 'react'
import {Link} from 'react-router-dom'
import {ArrowRight,RefreshCw} from 'lucide-react'
import './LearningPanels.css'

type ClassroomTodo={id:string;kind:'classroom'|'followup';course_id:string;course_name:string;topic:string;session_id?:string;title:string;reason:string;next_step:string;reasons:string[]}

export function ClassroomTodos(){
 const [items,setItems]=useState<ClassroomTodo[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[retry,setRetry]=useState(0)
 useEffect(()=>{
  let alive=true;let controller:AbortController|undefined
  async function load(){
   controller?.abort();controller=new AbortController()
   try{const response=await fetch('/opentutor-api/api/workspace/classroom-todos',{signal:controller.signal});if(!response.ok)throw new Error('学习待办暂时无法同步');const rows=await response.json();if(alive){setItems(rows);setError('')}}
   catch(cause){if(alive&&(cause as Error).name!=='AbortError')setError((cause as Error).message)}
   finally{if(alive)setLoading(false)}
  }
  const refresh=()=>void load();const focus=()=>{if(!document.hidden)void load()}
  void load();const timer=setInterval(refresh,15000)
  window.addEventListener('metamind-learning-updated',refresh);window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',focus)
  return()=>{alive=false;controller?.abort();clearInterval(timer);window.removeEventListener('metamind-learning-updated',refresh);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',focus)}
 },[retry])
 return <section className="panel" aria-label="课堂待办"><div className="section-top"><h2>课堂待办{items.length>0&&<span className="classroom-todo-count">{items.length}</span>}</h2><button className="secondary" onClick={()=>setRetry(n=>n+1)} aria-label="刷新课堂待办"><RefreshCw size={14}/>刷新</button></div><p className="muted">未完成的课堂和需要后续确认的内容会同步到这里，随学习记录更新。</p>{error&&<p className="error" role="alert">{error}</p>}{loading?<p className="muted">正在同步课堂待办…</p>:!items.length&&!error?<p className="muted">目前没有未完成的课堂或待确认的学习内容。</p>:items.map(item=><article className="learning-review-task" key={item.id}><div><b>{item.title}</b><small>{item.course_name} · {item.reason}</small><p className="classroom-todo-next">下一步：{item.next_step}</p>{item.reasons.length>1&&<details><summary>查看待确认内容</summary><ul>{item.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></details>}</div><Link className="secondary" to={item.kind==='classroom'?'/capture-agent?course='+encodeURIComponent(item.course_id)+'&session='+encodeURIComponent(item.session_id||''):'/teaching-agent?course='+encodeURIComponent(item.course_id)+'&practice='+encodeURIComponent(item.topic)}>{item.kind==='classroom'?'继续课堂':'独立练习'}<ArrowRight size={14}/></Link></article>)}</section>
}
