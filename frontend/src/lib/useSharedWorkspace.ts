import { useEffect, useRef, useState } from 'react'
import { useStored } from './store'
const BASE='/opentutor-api/api/workspace'
export function useSharedWorkspace<T extends object>(initial:T) {
  const [value,setValue,localError]=useStored<T>('bee-workspace-v2',initial)
  const [error,setError]=useState(''),[ready,setReady]=useState(false)
  const current=useRef(value),version=useRef(0),saved=useRef(''),saving=useRef(false),blocked=useRef(false)
  current.current=value
  useEffect(()=>{let mounted=true
    void (async()=>{try{
      const response=await fetch(`${BASE}/data`);if(!response.ok)throw new Error('工作空间暂时无法连接，当前修改已保存在浏览器。')
      let result=await response.json();if(!mounted)return
      if(!localStorage.getItem('metamind-workspace-migrated-v2') || !result.data){
        const migration=await fetch(`${BASE}/data/migrate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:{...initial,...current.current},version:result.version})})
        if(!migration.ok)throw new Error('旧记录同步失败，原记录已保留。')
        result=await migration.json();localStorage.setItem('metamind-workspace-migrated-v2','1')
      }
      if(!mounted)return
      const data={...initial,...result.data};version.current=result.version;saved.current=JSON.stringify(data);setValue(data);setReady(true)
    }catch(cause){if(mounted){setError((cause as Error).message);setReady(true);blocked.current=true}}})()
    return()=>{mounted=false}
  },[])
  useEffect(()=>{if(!ready || blocked.current)return
    const timer=setTimeout(()=>{void (async()=>{
      if(saving.current)return;saving.current=true
      try{while(JSON.stringify(current.current)!==saved.current){
        const snapshot=JSON.stringify(current.current)
        const response=await fetch(`${BASE}/data`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:version.current,data:JSON.parse(snapshot)})})
        const result=await response.json()
        if(!response.ok){if(response.status===409)blocked.current=true;throw new Error(result.detail||'同步失败，本地修改已保留。')}
        version.current=result.version;saved.current=snapshot;setError('')
      }}catch(cause){setError((cause as Error).message)}finally{saving.current=false}
    })()},350)
    return()=>clearTimeout(timer)
  },[value,ready])
  return [value,setValue,localError||error,ready] as const
}
