import './CaptureAgentPage.css'

const host = typeof window === 'undefined' ? '127.0.0.1' : window.location.hostname
const captureUrl = `http://${host}:5001/teach`

/** 课堂采集 is its own page; no tabs, no Q&A wrapper. */
export function CaptureAgentPage() {
  return (
    <iframe
      className="capture-standalone"
      src={captureUrl}
      title="课堂采集 · 教学 Agent"
      allow="microphone; camera; clipboard-read; clipboard-write"
    />
  )
}
