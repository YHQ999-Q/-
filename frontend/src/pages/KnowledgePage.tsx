import { Heading } from '../components/Workspace'
import { KnowledgePanel } from '../components/KnowledgePanel'
import './KnowledgePage.css'

export function KnowledgePage() {
  return (
    <>
      <Heading title="知识库" subtitle="课程子图 + 课堂沉淀；生活记忆在第二大脑，这里只放知识与证据。" />
      <KnowledgePanel />
    </>
  )
}
