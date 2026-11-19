export type CourseReference = { title: string; excerpt: string; url?: string }
const controlTags = ['ACTION', 'TOOL_START', 'TOOL_DONE']

function normalizeReplyFormatting(content: string) {
  // Normalize accidental presentation HTML without changing HTML taught in code examples.
  return content.split(/(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g).map((part, index) => {
    if (index % 2) return part
    return part
      .replace(/(?:\\?<|&lt;)(\/?)(small|span|div|p|br|font|sup|sub|b|strong|i|em|u|h[1-6])\b[^<>\n]*?(?:\\?>|&gt;)/gi,
        (_, closing: string, tag: string) => /^(br|div|p|h[1-6])$/i.test(tag) ? (closing || tag.toLowerCase() === 'br' ? '\n\n' : '') : '')
      .replace(/\\?<\/?(?:small|span|div|font|sup|sub)\b[^<>]*$/gi, '')
  }).join('')
}

export function cleanReply(content: string) {
  let text = content.replace(/\[(?:ACTION|TOOL_START|TOOL_DONE)\s*[:：][^\]]*(?:\]|$)/gi, '')
  // Hold back a marker prefix while its next token is still arriving.
  const tail = text.slice(text.lastIndexOf('['))
  if (tail.startsWith('[') && controlTags.some((tag) => `[${tag}:`.startsWith(tail.toUpperCase()))) text = text.slice(0, -tail.length)
  return normalizeReplyFormatting(text).replace(/metamind_demo[\\/]demo_spec\.json/g, '当前课程资料').replace(/\\([*])/g, '$1')
}
export function sourceLabel(value: string) {
  const name = value.trim().replace(/metamind_demo[\\/]demo_spec\.json/g, '当前课程资料')
  if (/\.(?:json|jsonl|ya?ml|env|py|tsx?)(?:$|[?#])/i.test(name)) return '当前课程资料'
  return name.replace(/^.*[\\/]/, '') || '当前课程资料'
}

export function prepareReply(content: string, references: CourseReference[] = []) {
  const citations: CourseReference[] = []
  function citation(label: string) {
    const title = sourceLabel(label)
    const matches = references.filter((ref) => ref.title === title || ref.title.endsWith(` > ${title}`))
    // Do not attach an arbitrary passage when an old filename identifies several sections.
    const reference = matches.length === 1 ? { ...matches[0], title } : { title, excerpt: '' }
    let index = citations.findIndex((ref) => ref.title === title)
    if (index < 0) { index = citations.length; citations.push(reference) }
    return `[${index + 1}](#tutor-citation-${index + 1})`
  }
  const markdown = cleanReply(content)
    .replace(/[（(]?\[(?:Source|来源)\s*[:：]\s*([^\]\n]+)\][）)]?/gi, (_, label: string) => citation(label))
    .replace(/[（(](?:Source|来源)\s*[:：]\s*([^）)\n]+)[）)]/gi, (_, label: string) => citation(label))
    .replace(/^\s*(?:\*\*)?(?:Source|来源)\s*[:：](?:\*\*)?\s*(.+)$/gim, (_, label: string) => citation(label.replace(/\*\*$/, '')))
    .replace(/\[(?:Source|来源)\s*[:：][^\]]*$/gi, '')
    .replace(/^(\s*(?:#{1,6}\s*|\*\*)?)Sources(\*\*)?\s*[:：]/gim, '$1参考资料$2：')
  return { markdown, citations }
}
export function copyableReply(content: string) {
  return cleanReply(content).replace(/\[(?:Source|来源)\s*[:：]\s*([^\]\n]+)\]/gi, (_, label: string) => `（来源：${sourceLabel(label)}）`)
}

export function safeSourceUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}
