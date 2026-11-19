import { createContext,useContext,type PropsWithChildren } from 'react'
import type { Conversation,Fact,Suggestion,Todo } from '../types'
import { uid } from '../lib/store'
import { useSharedWorkspace } from '../lib/useSharedWorkspace'
import { useLearning } from '../lib/useLearning'
export type Note={id:number;text:string;createdAt:number}
export type Message={role:'user'|'assistant';content:string;sourceIds?:number[];references?:import('../lib/tutorReply').CourseReference[]}
export type Analysis={summary:string;takeaways:string[];tasks:string[];facts:{text:string;tag:string}[]}
type Data={conversations:Conversation[];todos:Todo[];suggestions:Suggestion[];facts:Fact[];notes:Note[];messages:Message[];memoryThreads:Record<string,Message[]>;name:string}
const legacyThreads=()=>{try{return Object.fromEntries(Object.keys(localStorage).filter(k=>k.startsWith('bee-chat-')).map(k=>[k,JSON.parse(localStorage.getItem(k)||'[]')]).filter(([,v])=>Array.isArray(v)))}catch{return {}}}
const empty:Data={conversations:[],todos:[],suggestions:[],facts:[],notes:[],messages:[],memoryThreads:legacyThreads(),name:'朋友'}
function useData(){
 const [data,setData,storageError]=useSharedWorkspace<Data>(empty)
 const learning=useLearning()
 return {...data,storageError,learning,
 updateMemoryThread:(key:string,update:Message[]|((items:Message[])=>Message[]))=>setData(d=>({...d,memoryThreads:{...d.memoryThreads,[key]:typeof update==='function'?update(d.memoryThreads?.[key]||[]):update}})),
 setName:(name:string)=>setData(d=>({...d,name})),
 addTodo:(title:string,reminder?:string)=>{if(title.trim())setData(d=>({...d,todos:[{id:uid(),title:title.trim(),completed:false,reminder},...d.todos]}))},
 updateTodo:(id:number,patch:Partial<Todo>)=>setData(d=>({...d,todos:d.todos.map(t=>t.id===id?{...t,...patch}:t)})),
 toggleTodo:(id:number)=>setData(d=>({...d,todos:d.todos.map(t=>t.id===id?{...t,completed:!t.completed}:t)})),
 deleteTodo:(id:number)=>setData(d=>({...d,todos:d.todos.filter(t=>t.id!==id)})),
 addSuggestionToTodos:(item:Suggestion)=>setData(d=>({...d,todos:[{id:uid(),title:item.title,completed:false,sourceId:item.sourceId},...d.todos],suggestions:d.suggestions.filter(s=>s.id!==item.id)})),
 dismissSuggestion:(id:number)=>setData(d=>({...d,suggestions:d.suggestions.filter(s=>s.id!==id)})),
 addConversation:(title:string,transcript:string,seconds:number)=>{const id=uid(),createdAt=Date.now();setData(d=>({...d,conversations:[{id,title:title.trim()||'未命名记录',transcript,createdAt,time:new Date(createdAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),duration:seconds?`${Math.ceil(seconds/60)} min`:'文本记录'},...d.conversations]}));return id},
 updateConversation:(id:number,patch:Partial<Conversation>)=>setData(d=>({...d,conversations:d.conversations.map(c=>c.id===id?{...c,...patch}:c)})),
 deleteConversation:(id:number)=>setData(d=>({...d,conversations:d.conversations.filter(c=>c.id!==id),suggestions:d.suggestions.filter(s=>s.sourceId!==id),facts:d.facts.map(f=>f.sourceId===id?{...f,sourceId:undefined}:f)})),
 applyAnalysis:(id:number,result:Analysis,template:string)=>setData(d=>({...d,conversations:d.conversations.map(c=>c.id===id?{...c,summary:result.summary,takeaways:result.takeaways,template}:c),suggestions:[...d.suggestions,...result.tasks.filter(t=>!d.suggestions.some(s=>s.sourceId===id&&s.title===t)&&!d.todos.some(s=>s.sourceId===id&&s.title===t)).map(title=>({id:uid(),title,eyebrow:'来自对话摘要',sourceId:id}))],facts:[...d.facts,...result.facts.filter(f=>!d.facts.some(a=>a.text===f.text)).map(f=>({...f,id:uid(),confirmed:false,sourceId:id}))]})),
 addFact:(text:string,tag:string)=>setData(d=>({...d,facts:[{id:uid(),text,tag,confirmed:true},...d.facts]})),
 updateFact:(id:number,patch:Partial<Fact>)=>setData(d=>({...d,facts:d.facts.map(f=>f.id===id?{...f,...patch}:f)})),
 deleteFact:(id:number)=>setData(d=>({...d,facts:d.facts.filter(f=>f.id!==id)})),
 addNote:(text:string)=>setData(d=>({...d,notes:[{id:uid(),text,createdAt:Date.now()},...d.notes]})),
 deleteNote:(id:number)=>setData(d=>({...d,notes:d.notes.filter(n=>n.id!==id)})),
 addMessage:(message:Message)=>setData(d=>({...d,messages:[...d.messages,message]})),
 clearMessages:()=>setData(d=>({...d,messages:[]})),
 exportData:()=>JSON.stringify({...data,version:2,threads:Object.fromEntries(Object.keys(localStorage).filter(k=>k.startsWith('bee-chat-')||k==='bee-insights-v2').map(k=>[k,JSON.parse(localStorage.getItem(k)||'null')]))},null,2),
 }
}
const AppStateContext=createContext<ReturnType<typeof useData>|null>(null)
export function AppStateProvider({children}:PropsWithChildren){return <AppStateContext.Provider value={useData()}>{children}</AppStateContext.Provider>}
export function useAppState(){const value=useContext(AppStateContext);if(!value)throw new Error('Missing AppState');return value}
