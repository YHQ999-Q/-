import { useEffect, useState } from 'react'
export function useStored<T>(key: string, initial: T) {
  const read=()=>{try{const raw=localStorage.getItem(key);return raw?JSON.parse(raw) as T:initial}catch{return initial}}
  const [entry,setEntry]=useState<{key:string;value:T}>(()=>({key,value:read()}))
  const [error,setError]=useState('')
  const value=entry.key===key?entry.value:read()
  useEffect(()=>{if(entry.key!==key)setEntry({key,value:read()})},[key])
  useEffect(()=>{if(entry.key!==key)return;try{localStorage.setItem(key,JSON.stringify(entry.value));setError('')}catch{setError('保存失败：浏览器存储空间不足，请导出备份。')}},[key,entry])
  const setValue: React.Dispatch<React.SetStateAction<T>>=(update)=>setEntry(previous=>{const current=previous.key===key?previous.value:read();return {key,value:typeof update==='function'?(update as (v:T)=>T)(current):update}})
  return [value,setValue,error] as const
}

export const uid = () => Date.now() + Math.floor(Math.random()*1000)
export const dayKey = (time:number) => new Date(time).toLocaleDateString('sv-SE')
export function download(name:string,text:string,type='text/plain') {
  const url=URL.createObjectURL(new Blob([text],{type})); const a=document.createElement('a'); a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
export async function api<T>(path:string,body?:unknown):Promise<T> {
  const endpoint=path.startsWith('/api/')?'/opentutor-api/api/workspace/ai/'+path.slice(5):path
  const res=await fetch(endpoint,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const data=await res.json().catch(()=>({error:'服务暂时不可用，请稍后重试。'}));if(!res.ok)throw new Error(data.detail||data.error||'请求失败');return data as T
}
