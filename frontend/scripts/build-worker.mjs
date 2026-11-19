import { readFile,readdir,mkdir,writeFile } from 'node:fs/promises'
import path from 'node:path'
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'}
const assets={}
async function collect(dir){for(const entry of await readdir(dir,{withFileTypes:true})){if(entry.name==='server'||entry.name==='.openai')continue;const file=path.join(dir,entry.name);if(entry.isDirectory())await collect(file);else{const name='/'+path.relative('dist',file).split(path.sep).join('/');assets[name]={data:(await readFile(file)).toString('base64'),type:types[path.extname(file)]||'application/octet-stream'}}}}
await collect('dist')
const source=await readFile('server/api.mjs','utf8')
const worker=source+'\nconst assets='+JSON.stringify(assets)+`;\nexport default {async fetch(request,env){const pathname=new URL(request.url).pathname;if(pathname.startsWith('/api/'))return handleApi(request,env);if(!['GET','HEAD'].includes(request.method))return new Response('Method not allowed',{status:405});const asset=assets[pathname==='/'?'/index.html':pathname];if(!asset)return new Response('Not found',{status:404});const binary=Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0));return new Response(request.method==='HEAD'?null:binary,{headers:{'Content-Type':asset.type,'Cache-Control':pathname.startsWith('/assets/')?'public,max-age=31536000,immutable':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Permissions-Policy':'microphone=(self), camera=()'}})}};`
await mkdir('dist/server',{recursive:true});await writeFile('dist/server/index.js',worker)
await mkdir('dist/.openai',{recursive:true});await writeFile('dist/.openai/hosting.json',await readFile('.openai/hosting.json'))
console.log('Worker bundle prepared; '+Object.keys(assets).length+' assets embedded.')
