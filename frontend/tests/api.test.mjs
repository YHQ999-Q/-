import test from 'node:test'
import assert from 'node:assert/strict'
import {handleApi} from '../server/api.mjs'
const req=(path,body,origin)=>new Request('https://bee.test/api/'+path,{method:'POST',headers:{'Content-Type':'application/json',...(origin?{origin}:{})},body:JSON.stringify(body)})
const env={DASHSCOPE_API_KEY:'test-key'}
const result=v=>async()=>new Response(JSON.stringify({choices:[{message:{content:JSON.stringify(v)}}]}))
test('missing key fails clearly without external calls',async()=>{const res=await handleApi(req('analyze',{text:'会议内容'}),{},()=>assert.fail());assert.equal(res.status,503);assert.match((await res.json()).error,/尚未配置/)})
test('cross-origin requests are rejected before external calls',async()=>{const res=await handleApi(req('chat',{},'https://other.test'),env,()=>assert.fail());assert.equal(res.status,403)})
test('analysis validates malformed model output',async()=>{const res=await handleApi(req('analyze',{text:'明天整理课堂方案'}),env,result({summary:'摘要',takeaways:'wrong',tasks:[],facts:[]}));assert.equal(res.status,502)})
test('analysis returns structured real provider response',async()=>{const value={summary:'整理课堂方案',takeaways:['先明确教学目标'],tasks:['明天整理方案'],facts:[]};const res=await handleApi(req('analyze',{text:'明天整理方案',template:'课堂笔记'}),env,result(value));assert.deepEqual(await res.json(),value)})
test('chat removes fabricated source IDs',async()=>{const res=await handleApi(req('chat',{messages:[{role:'user',content:'我讨论了什么？'}],context:[{id:4,title:'课堂',text:'讨论课堂'}],facts:[]}),env,result({reply:'讨论了课堂',sourceIds:[4,999,4]}));assert.deepEqual((await res.json()).sourceIds,[4])})
test('upstream errors do not leak credentials',async()=>{const res=await handleApi(req('analyze',{text:'内容'}),env,async()=>new Response('sensitive-key',{status:401}));const body=await res.text();assert.equal(res.status,502);assert.doesNotMatch(body,/sensitive-key|test-key/)})
test('audio URLs are rejected, accepted audio uses ASR model',async()=>{const res=await handleApi(req('transcribe',{audio:'https://private.test/file'}),env,()=>assert.fail());assert.equal(res.status,400);let called=false;const good=await handleApi(req('transcribe',{audio:'data:audio/webm;base64,AAAA'}),env,async(url,opts)=>{called=true;const body=JSON.parse(opts.body);assert.equal(body.model,'qwen3-asr-flash');assert.equal(body.messages[0].content[0].type,'input_audio');return new Response(JSON.stringify({choices:[{message:{content:'原始转写'} }]}))});assert.equal(called,true);assert.equal((await good.json()).text,'原始转写')})
