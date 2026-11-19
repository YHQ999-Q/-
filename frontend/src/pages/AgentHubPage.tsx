import { TeachingAgentPage } from './TeachingAgentPage'
import { AgentLoopBar } from './AgentLoopBar'
import './AgentHubPage.css'

/**
 * 学习助手 = 问答助手 only.
 * 课堂采集 is a separate page/route now; it is not a tab here.
 */
export function AgentHubPage() {
  return (
    <div className="agent-hub">
      <AgentLoopBar mode="chat" />
      <TeachingAgentPage />
    </div>
  )
}
