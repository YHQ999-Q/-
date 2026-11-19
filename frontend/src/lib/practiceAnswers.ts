/** Interpret explicit answers only; questions and requests for hints stay in the chat. */
export function readPracticeAnswer(text:string,exercise:{question_type:string;options?:Record<string,string>}):string|null{
 const content=text.trim();if(!content)return null
 const options=exercise.options
 if(!options||!Object.keys(options).length)return /[?？]|提示|不懂|不会|为什么|解释|怎么|讲解|help|hint/i.test(content)?null:content
 const keys=new Set(Object.keys(options).map(k=>k.toLowerCase()))
 const match=content.match(/^(?:我(?:选|选择)|答案(?:是|为)?|选)?\s*([a-z](?:\s*[,，]\s*[a-z])*)(?=[.、。\s，,：:]|$)/i)
 if(match){const choices=match[1].toLowerCase().split(/\s*[,，]\s*/);if(choices.every(c=>keys.has(c)))return choices.join(',')}
 if(exercise.question_type==='select_all'&&/^[a-z]{2,}$/i.test(content)&&[...content.toLowerCase()].every(c=>keys.has(c)))return [...content.toLowerCase()].join(',')
 const normalized=(value:string)=>value.trim().replace(/^(?:输出(?:结果)?|答案)(?:是|为)?[:：]?/,'').replace(/[\s,，、。；;]/g,'')
 const exact=Object.entries(options).find(([,value])=>normalized(value)===normalized(content))
 return exact?.[0]||null
}
