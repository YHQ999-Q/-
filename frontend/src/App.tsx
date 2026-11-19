import { Navigate, Route, Routes } from 'react-router-dom'
import { Workspace } from './components/Workspace'
import {Home,Memories,Daily,Detail,Tasks,Facts,Suggested} from './pages/WorkspacePages'
import {Capture,Chat,Grow,Settings} from './pages/ToolsPages'
import { ClassroomPage } from './pages/ClassroomPage'
import { AgentHubPage } from './pages/AgentHubPage'
import { CaptureAgentPage } from './pages/CaptureAgentPage'
import { SecondBrainPage } from './pages/SecondBrainPage'
import { LearnerModelPage } from './pages/LearnerModelPage'
import { MindGrowthPage, PersonalGrowthPage } from './pages/GrowthModules'
import { PivotGrowthPage } from './pages/PivotGrowthPage'

export default function App() {
  return (
    <Workspace>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/summaries" element={<Memories />} />
        <Route path="/grow" element={<Grow />} />
        <Route path="/list" element={<Tasks />} />
        <Route path="/facts" element={<Facts />} />
        <Route path="/capture" element={<Capture />} />
        <Route path="/conversation/:id" element={<Detail />} />
        <Route path="/daily/:date" element={<Daily />} />
        <Route path="/suggestions" element={<Suggested />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/classroom" element={<ClassroomPage />} />
        <Route path="/agent" element={<AgentHubPage />} />
        <Route path="/teaching-agent" element={<Navigate to="/agent" replace />} />
        <Route path="/capture-agent" element={<CaptureAgentPage />} />
        <Route path="/second-brain" element={<SecondBrainPage />} />
        <Route path="/learner-model" element={<LearnerModelPage />} />
        <Route path="/growth/mind" element={<MindGrowthPage />} />
        <Route path="/growth/personal" element={<PersonalGrowthPage />} />
        <Route path="/growth/pivots" element={<PivotGrowthPage />} />
        <Route path="/knowledge" element={<Navigate to="/second-brain?tab=knowledge" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Workspace>
  )
}
