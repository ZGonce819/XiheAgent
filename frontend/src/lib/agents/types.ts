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
  /**
   * 这个任务有没有开启「全流程自动化」：需要审批的命令由三个评审表决决定，
   * 不再停下来等人在界面上点确认。
   *
   * 默认关闭。开启后也不会放行按策略硬拒绝的命令，评审得不出结论时同样交回人工。
   */
  autoApprove?: boolean
  enabled: boolean
  lastThreadId?: string | null
  /** 最近一轮运行的 id；跑完一轮之后才写回来。 */
  lastRunId?: string | null
  lastTriggeredAt?: string | null
  lastError?: string | null
  lastErrorAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

/** 一轮循环跑完之后记下来的结果。 */
export interface AutomationRunRecord {
  runId: string
  threadId: string | null
  startedAt: string | null
  finishedAt: string | null
  outcome: "succeeded" | "failed" | "blocked"
  summary: string
  changed: Array<string>
  nextSteps: Array<string>
}

/** 自动化任务跨轮保存的循环状态。 */
export interface AutomationLoopState {
  version: number
  automationId: string
  updatedAt: string
  cursor: Record<string, unknown>
  budget: {
    maxRuns: number
    runsUsed: number
    maxTokens: number
    tokensUsed: number
    maxConsecutiveFailures: number
  }
  stop: {
    reached: boolean
    reason: string | null
    at: string | null
  }
  runs: Array<AutomationRunRecord>
  attempts: Array<Record<string, unknown>>
  lastFeedback: string
}

/** 循环是因为哪一条停下的。恢复方式按来源不同，界面要分开处置。 */
export type AutomationStopSource =
  /** 被显式标记停止，可以清掉标记继续跑。 */
  | "marked"
  /**
   * 上一轮停在等人工确认，清那一轮的标记就能继续。
   *
   * 和 `marked` 一样能直接救回来，但点按钮之前得先有人把待审批的命令处理掉 ——
   * 不处理的话下一轮还会卡在同一条命令上。
   */
  | "awaiting_approval"
  /** 轮数用完了，要调高最多运行轮数。 */
  | "max_runs"
  /** token 用完了，要调高最多消耗 token 数。 */
  | "max_tokens"
  /** 连续未通过验证，要先看卡在哪，再决定要不要放宽上限。 */
  | "failures"

export interface AutomationStateResponse {
  state: AutomationLoopState
  /** 服务端渲染好的多行纯文本，直接展示即可。 */
  text: string
  stopped: boolean
  stopReason: string | null
  /** 没停时为 null。 */
  stopSource: AutomationStopSource | null
}

/** 手动触发一轮的受理回执。 */
export interface AutomationRunReceipt {
  automationId: string
  accepted: boolean
  threadId: string
  roundNumber: number
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
  /** 运行时错误消息（仅前端 SSE 接收时使用，不持久化） */
  runError?: string
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
