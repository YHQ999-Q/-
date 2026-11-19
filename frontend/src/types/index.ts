export type Conversation = {
  id: number
  time: string
  duration: string
  title: string
  createdAt?: number
  transcript?: string
  summary?: string
  takeaways?: string[]
  bookmarked?: boolean
  template?: string
}

export type Todo = {
  id: number
  title: string
  completed: boolean
  reminder?: string
  sourceId?: number
}

export type Suggestion = {
  id: number
  title: string
  eyebrow: string
  sourceId?: number
}

export type FactTag = {
  name: string
  count: number
}

export type Fact = {
  id: number
  tag: string
  text: string
  confirmed?: boolean
  sourceId?: number
}

export type Insight = {
  id: number
  kicker: string
  title: string
  body: string
  suggestion: string
}
