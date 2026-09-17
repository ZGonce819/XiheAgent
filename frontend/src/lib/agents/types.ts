export type Author = "user" | "agent" | "system" | "tool"

export type ChunkKind =
  | "text"
  | "code"
  | "error"
  | "list"
  | "tool-execution"
  | "todo"
  | "image"

export type TodoStatus = "pending" | "in_progress" | "completed"

export type AgentStatus =
  | "idle"
  | "running"
  | "finished"
  | "interrupted"
  | "error"

export type AgentSource =
  | "dashboard"
  | "gitee"
  | "github"
  | "slack"
  | "linear"
  | "schedule"

export interface TodoItem {
  content: string
  status: TodoStatus
}

export type AcpToolKind =
  | "read"
  | "edit"
  | "delete"
  | "move"
  | "search"
  | "execute"
  | "think"
  | "fetch"
  | "slack"
  | "linear"
  | "other"

export type AcpToolStatus = "pending" | "in_progress" | "completed" | "error"

export interface AcpToolLocation {
  path: string
  line?: number
}

export interface DiffData {
  originalContent: string | null
  newContent: string
  filePath: string
  isNewFile: boolean
  isBinary: boolean
  isTruncated: boolean
  totalLines: number
}

export interface ToolExecutionChunk {
  kind: "tool-execution"
  toolCallId: string
  title: string
  toolKind: AcpToolKind
  input?: Record<string, unknown>
  status: AcpToolStatus
  output?: string
  elapsedMs?: number
  approvalRequestId?: string
  diffData?: DiffData
  diffs?: Array<DiffData>
  locations?: Array<AcpToolLocation>
  groupId?: string
}

export interface TextChunk {
  kind: "text"
  text: string
}

export interface CodeChunk {
  kind: "code"
  text: string
  language?: string
}

export interface ErrorChunk {
  kind: "error"
  text: string
}

export interface ListChunk {
  kind: "list"
  lines: Array<string>
}

export interface TodoChunk {
  kind: "todo"
  todos: Array<TodoItem>
}

export interface ImageChunk {
  kind: "image"
  base64: string
  mimeType: string
  fileName?: string
}

export type Chunk =
  | TextChunk
  | CodeChunk
  | ErrorChunk
  | ListChunk
  | ToolExecutionChunk
  | TodoChunk
  | ImageChunk

export interface Message {
  id: string
  author: Author
  timestamp: string
  chunks: Array<Chunk>
  hidden?: boolean
  executionNarration?: boolean
}

export interface Project {
  id: string
  path: string
  name: string
  createdAt: number
  lastOpenedAt: number
  gitBranch?: string
}

export interface AgentSchedule {
  id: string
  name: string
  prompt: string
  schedule: string
  repo: string | null
  model: string
  effort?: string | null
  enabled: boolean
  cronId?: string | null
  lastThreadId?: string | null
  lastRunId?: string | null
  lastTriggeredAt?: string | null
  lastError?: string | null
  lastErrorAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export type RunEventKind = "think" | "execute" | "search" | "other" | "todo"

export type RunEventStatus = "pending" | "in_progress" | "completed" | "error"

export interface RunEvent {
  id: string
  thread_id: string
  kind: RunEventKind
  title: string
  status: RunEventStatus
  detail?: string | null
  created_at: string
  updated_at?: string
}

export interface ConversationVersion {
  number: number
  createdAt: string
  messages: Array<Message>
  runEvents: Array<RunEvent>
  editedMessageId?: string | null
  editedUserIndex?: number | null
}

export interface AgentThread {
  id: string
  title: string
  repo: string
  repoFullName: string
  repoUrl: string
  platform: string
  platformDisplay: string
  branch: string
  model: string
  effort?: string | null
  source?: AgentSource
  status: AgentStatus
  backendType?: string
  projectName?: string | null
  /** 本地环境会话的工作目录（工作区相对路径），沙箱或无仓库时为 null */
  localWorkDir?: string | null
  /** 本地工作目录的绝对路径，用于悬停提示 */
  localWorkDirFull?: string | null
  createdAt: number
  updatedAt: number
  messages: Array<Message>
  runEvents?: Array<RunEvent>
  conversationVersions?: Array<ConversationVersion>
  pr?: {
    number: number
    title: string
    state: "draft" | "open" | "merged"
    headRef: string
    baseRef: string
    url: string
  }
  diffStats?: {
    files: number
    additions: number
    deletions: number
  }
  changedFiles?: Array<{
    path: string
    additions: number
    deletions: number
    patch?: string
  }>
  pendingApproval?: PendingCommandApproval | null
}

export interface PendingCommandApproval {
  id: string
  threadId: string
  runId: string
  backendType: string
  createdAt: string
  actions: Array<{ name: string; command: string; description: string }>
}

export type GitFileStatus =
  | "index-modified"
  | "index-added"
  | "index-deleted"
  | "modified"
  | "deleted"
  | "untracked"

export interface GitStatusEntry {
  path: string
  status: GitFileStatus
  staged: boolean
  originalPath?: string
}
