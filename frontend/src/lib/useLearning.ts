import { useEffect,useState } from 'react'
export type LearningOverview = { courses:any[]; evaluations:any[]; reflections:any[]; invocations:any[]; versions:any[]; jobs:any[]; plans:any[] }
export function refreshLearning(){window.dispatchEvent(new Event('metamind-learning-updated'))}
export function useLearning(){
 const [data,setData]=useState<LearningOverview|null>(null),[error,setError]=useState('')
 useEffect(()=>{let alive=true,timer:ReturnType<typeof setTimeout>
  async function load(){clearTimeout(timer);try{const response=await fetch('/opentutor-api/api/workspace/overview');if(!response.ok)throw new Error('学习数据暂时无法加载');const next=await response.json();if(alive){setData(next);setError('')}}catch(cause){if(alive)setError((cause as Error).message)}finally{if(alive)timer=setTimeout(()=>void load(),15000)}}
  const handler=()=>void load();void load();window.addEventListener('metamind-learning-updated',handler)
  return()=>{alive=false;clearTimeout(timer);window.removeEventListener('metamind-learning-updated',handler)}
 },[])
 return {data,error}
}
