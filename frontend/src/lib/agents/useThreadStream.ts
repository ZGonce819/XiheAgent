import type { QueryClient } from "@tanstack/react-query"
import { agentThreadKeys } from "./queries"
import type {
  AgentStatus,
  AgentThread,
  Chunk,
  Message,
  RunEvent,
  TextChunk,
  TodoItem,
  ToolExecutionChunk,
} from "./types"

interface StreamPart {
  event?: string
  data?: unknown
}

export function parseSseBlock(block: string): StreamPart | null {
  let event = "message"
  const dataLines: string[] = []

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim()
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (dataLines.length === 0) return null
  const payload = JSON.parse(dataLines.join("\n")) as unknown

  // Accept both our standard SSE format (event line + raw JSON) and the
  // wrapped format used by older clients.
  if (
    isRecord(payload) &&
    typeof payload.event === "string" &&
    "data" in payload
  ) {
    return {
      event: payload.event,
      data: payload.data,
    }
  }
  return { event, data: payload }
}

interface ThreadMetaPayload {
  id?: string
  status?: AgentStatus
  branch?: string
  repoUrl?: string
  platform?: string
  platformDisplay?: string
  pr?: AgentThread["pr"]
  updatedAt?: number
  runEvents?: Array<RunEvent>
  conversationVersions?: AgentThread["conversationVersions"]
}

interface TextDeltaPayload {
  message_id?: string
  run_id?: string
  content?: string
  mode?: "append" | "replace"
}

interface UserMessagePayload {
  message_id?: string
  run_id?: string
  content?: string
  timestamp?: string
}

interface TodoDeltaPayload {
  message_id?: string
  run_id?: string
  todos?: Array<TodoItem>
}

interface ToolUpdatePayload {
  thread_id?: string
  run_id?: string
  event_id?: string
  kind?: string
  title?: string
  status?: string
  detail?: string | null
  tool_kind?: string
  tool_group_id?: string | null
  is_tool?: boolean
  is_process?: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function updateThread(
  threadId: string,
  queryClient: QueryClient,
  updater: (thread: AgentThread) => AgentThread
) {
  const key = agentThreadKeys.detail(threadId)
  void queryClient.cancelQueries({ queryKey: key })
  queryClient.setQueryData<AgentThread | undefined>(key, (prev) =>
    prev ? updater(prev) : prev
  )
}

function invalidateThreadList(queryClient: QueryClient) {
  // `agentThreadKeys.all` 是 thread 列表 key，也是 detail key 的前缀。
  // 流式运行结束时只能刷新侧边栏列表，不能让当前 detail 重新请求并覆盖
  // SSE 已经追加好的 messages。
  void queryClient.invalidateQueries({
    queryKey: agentThreadKeys.all,
    exact: true,
  })
}

function upsertMessage(thread: AgentThread, message: Message): AgentThread {
  const index = thread.messages.findIndex((item) => item.id === message.id)
  if (index < 0) {
    return { ...thread, messages: [...thread.messages, message] }
  }
  const messages = thread.messages.slice()
  messages[index] = message
  return { ...thread, messages }
}

function runIdFromMessageId(messageId: string): string | null {
  const patterns = [/-user-(.+)$/, /-live-plan-(.+)$/, /-live-assistant-(.+)$/]
  for (const pattern of patterns) {
    const match = messageId.match(pattern)
    if (match?.[1]) return match[1]
  }
  return null
}

function payloadRunId(data: {
  run_id?: string
  message_id?: string
}): string | null {
  return (
    data.run_id ||
    (data.message_id ? runIdFromMessageId(data.message_id) : null)
  )
}

function findRunMessageIndex(
  messages: Array<Message>,
  runId: string | null,
  role: "user" | "plan" | "assistant"
): number {
  if (!runId) return -1
  if (role === "assistant") {
    return messages.findLastIndex((message) =>
      message.id.includes(`-live-assistant-${runId}-`)
    )
  }
  const marker = role === "user" ? `-user-${runId}` : `-live-plan-${runId}`
  return messages.findIndex((message) => message.id.endsWith(marker))
}

function insertOrReplaceAt(
  messages: Array<Message>,
  message: Message,
  insertAt: number
): Array<Message> {
  const withoutCurrent = messages.filter((item) => item.id !== message.id)
  const boundedIndex = Math.min(Math.max(insertAt, 0), withoutCurrent.length)
  const next = withoutCurrent.slice()
  next.splice(boundedIndex, 0, message)
  return next
}

function ensureAgentMessage(thread: AgentThread, messageId: string): Message {
  const existing = thread.messages.find((message) => message.id === messageId)
  if (existing) return existing
  return {
    id: messageId,
    author: "agent",
    timestamp: new Date().toISOString(),
    chunks: [],
  }
}

function applyMessageStart(
  thread: AgentThread,
  data: { message_id?: string; run_id?: string }
): AgentThread {
  const messageId = data.message_id
  if (!messageId) return thread

  const messages = thread.messages.slice()
  if (!messages.some((message) => message.id === messageId)) {
    messages.push(ensureAgentMessage({ ...thread, messages }, messageId))
  }
  return { ...thread, messages }
}

function setTextChunk(
  message: Message,
  text: string,
  mode: "append" | "replace"
): Message {
  const chunks = message.chunks.slice()
  const textIndex = chunks.findIndex((chunk) => chunk.kind === "text")
  if (textIndex >= 0) {
    const previous = chunks[textIndex] as TextChunk
    chunks[textIndex] = {
      ...previous,
      text: mode === "replace" ? text : previous.text + text,
    }
  } else {
    chunks.push({ kind: "text", text })
  }
  return { ...message, chunks }
}

function applyTextDelta(
  thread: AgentThread,
  data: TextDeltaPayload
): AgentThread {
  const messageId = data.message_id
  const content = data.content ?? ""
  if (!messageId || !content) return thread
  const message = ensureAgentMessage(thread, messageId)
  const nextMessage = setTextChunk(message, content, data.mode ?? "append")
  const existingIndex = thread.messages.findIndex(
    (item) => item.id === messageId
  )
  if (existingIndex >= 0) {
    return upsertMessage(thread, nextMessage)
  }

  const runId = payloadRunId(data)
  const messages = thread.messages.slice()
  const planIndex = findRunMessageIndex(messages, runId, "plan")
  const userIndex = findRunMessageIndex(messages, runId, "user")
  const insertAt =
    planIndex >= 0
      ? planIndex + 1
      : userIndex >= 0
        ? userIndex + 1
        : messages.length
  return {
    ...thread,
    messages: insertOrReplaceAt(messages, nextMessage, insertAt),
  }
}

function applyUserMessage(
  thread: AgentThread,
  data: UserMessagePayload
): AgentThread {
  const content = data.content?.trim()
  if (!content) return thread
  const messageId = data.message_id || `user-${Date.now()}`

  // 正式 SSE user_message 代表一个 run 的真实用户输入，只能按 message_id 去重。
  // 不要按文本去重：多轮对话里用户可能反复输入“确认”“开始实施”，文本相同
  // 也必须作为独立轮次保留。
  if (thread.messages.some((message) => message.id === messageId)) return thread

  const userMessage: Message = {
    id: messageId,
    author: "user",
    timestamp: data.timestamp || new Date().toISOString(),
    chunks: [{ kind: "text", text: content }],
  }
  const runId = payloadRunId(data)
  const messages = thread.messages.slice()
  const planIndex = findRunMessageIndex(messages, runId, "plan")
  const assistantIndex = findRunMessageIndex(messages, runId, "assistant")
  const insertAt =
    planIndex >= 0
      ? planIndex
      : assistantIndex >= 0
        ? assistantIndex
        : messages.length
  return {
    ...thread,
    messages: insertOrReplaceAt(messages, userMessage, insertAt),
  }
}

function applyTodoDelta(
  thread: AgentThread,
  data: TodoDeltaPayload
): AgentThread {
  const messageId = data.message_id
  const todos = data.todos ?? []
  if (!messageId || todos.length === 0) return thread

  const current = ensureAgentMessage(thread, messageId)
  const todoChunk: Chunk = { kind: "todo", todos }
  const chunks: Array<Chunk> = [todoChunk]

  const planMessage: Message = {
    ...current,
    author: "agent",
    chunks,
  }

  const withoutOldPlan = thread.messages.filter(
    (message) => message.id !== messageId
  )
  // 任务计划属于某一轮 run，必须插到同 run 的 user_message 后面。
  // 不能再用“最后一条用户消息”猜位置，因为第二轮用户输入可能还只是 pending prompt，
  // 不在 thread.messages 里，会导致第二轮任务计划插到上一轮内容前面。
  const runId = payloadRunId(data)
  const userIndex = findRunMessageIndex(withoutOldPlan, runId, "user")
  const assistantIndex = findRunMessageIndex(withoutOldPlan, runId, "assistant")
  const insertAt =
    userIndex >= 0
      ? userIndex + 1
      : assistantIndex >= 0
        ? assistantIndex
        : withoutOldPlan.length
  return {
    ...thread,
    messages: insertOrReplaceAt(withoutOldPlan, planMessage, insertAt),
  }
}

function applyThreadMeta(
  thread: AgentThread,
  data: ThreadMetaPayload
): AgentThread {
  return {
    ...thread,
    status: data.status ?? thread.status,
    branch: data.branch ?? thread.branch,
    repoUrl: data.repoUrl ?? thread.repoUrl,
    platform: data.platform ?? thread.platform,
    platformDisplay: data.platformDisplay ?? thread.platformDisplay,
    pr: data.pr ?? thread.pr,
    updatedAt: data.updatedAt ?? thread.updatedAt,
    runEvents: data.runEvents ?? thread.runEvents,
    conversationVersions:
      data.conversationVersions ?? thread.conversationVersions,
  }
}

function parseDetail(
  detail: string | null | undefined
): Record<string, unknown> {
  if (!detail) return {}
  try {
    const parsed = JSON.parse(detail) as unknown
    return isRecord(parsed) ? parsed : { text: detail }
  } catch {
    return { text: detail }
  }
}

function toolChunkFromEvent(
  event: Pick<RunEvent, "id" | "kind" | "title" | "status" | "detail"> & {
    tool_kind?: string
    tool_group_id?: string | null
  }
): ToolExecutionChunk | null {
  const detail = parseDetail(event.detail)
  const toolName = String(detail.tool || "")
  if (toolName === "write_todos" || event.title === "write_todos") return null

  const args = isRecord(detail.args) ? detail.args : detail
  const rawKind = event.tool_kind || event.kind || "other"
  const validKinds = new Set([
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "slack",
    "linear",
    "other",
  ])
  const toolKind = (
    validKinds.has(rawKind) ? rawKind : "other"
  ) as ToolExecutionChunk["toolKind"]
  let input: Record<string, unknown> | undefined

  if (toolKind === "read" || toolKind === "search") {
    const path = args.file_path || args.path || args.file || args.text
    if (path) input = { path: String(path) }
  } else if (["edit", "delete", "move"].includes(toolKind)) {
    const path = args.file_path || args.path || args.file
    if (path) input = { path: String(path) }
  } else if (toolKind === "execute") {
    const command = args.command || args.cmd || args.text
    if (command) input = { command: String(command) }
  } else if (Object.keys(args).length > 0) {
    input = args
  }

  const elapsed = Number(detail.elapsed_ms)
  return {
    kind: "tool-execution",
    toolCallId: event.id,
    title: event.title,
    toolKind,
    status: event.status,
    input,
    elapsedMs: Number.isFinite(elapsed) ? elapsed : undefined,
    groupId: event.tool_group_id || undefined,
  }
}

function eventText(event: RunEvent): string {
  const text = parseDetail(event.detail).text
  return typeof text === "string" ? text.trim() : ""
}

function isAssistantStreamEvent(event: RunEvent): boolean {
  return (
    event.id.includes(":stream:") &&
    event.id.includes(":assistant:") &&
    !!eventText(event)
  )
}

function isExecutionEvent(event: RunEvent): boolean {
  if (event.id.includes(":tool:")) return true
  if (event.id.includes(":stream:") && event.id.includes(":subagent:"))
    return true
  return !!parseDetail(event.detail).tool
}

export function normalizeThreadHistory(thread: AgentThread): AgentThread {
  const events = thread.runEvents ?? []
  const hasAssistantEvents = events.some(isAssistantStreamEvent)
  if (!hasAssistantEvents && !events.some((event) => isExecutionEvent(event) && toolChunkFromEvent(event))) {
    return thread
  }

  const rebuiltMessages: Message[] = []
  let phase: {
    id: string
    timestamp: string
    text: string
    tools: ToolExecutionChunk[]
    execution?: boolean
  } | null = null

  const flushPhase = () => {
    if (!phase) return
    const chunks: Chunk[] = []
    if (phase.text) chunks.push({ kind: "text", text: phase.text })
    chunks.push(...phase.tools)
    if (chunks.length > 0) {
      rebuiltMessages.push({
        id: phase.id,
        author: "agent",
        timestamp: phase.timestamp,
        chunks,
        executionNarration: Boolean(phase.execution || phase.tools.length),
      })
    }
    phase = null
  }

  for (const event of events) {
    if (isAssistantStreamEvent(event)) {
      flushPhase()
      phase = {
        id: `history-${event.id}`,
        timestamp: event.created_at,
        text: eventText(event),
        tools: [],
      }
      continue
    }
    if (!isExecutionEvent(event)) continue
    if (phase) phase.execution = true
    const tool = toolChunkFromEvent(event)
    if (!tool) continue
    if (!phase) {
      phase = {
        id: `history-tools-${event.id}`,
        timestamp: event.created_at,
        text: "",
        tools: [],
      }
    }
    const existingIndex = phase.tools.findIndex(
      (item) => item.toolCallId === tool.toolCallId
    )
    if (existingIndex >= 0) phase.tools[existingIndex] = tool
    else phase.tools.push(tool)
  }
  // A phase ending in tool calls is execution, even if the run stops there.
  // Only a separate assistant phase without tools is answer text.
  flushPhase()

  let lastUserIndex = -1
  for (let index = thread.messages.length - 1; index >= 0; index -= 1) {
    if (thread.messages[index]!.author === "user") {
      lastUserIndex = index
      break
    }
  }
  const prefix = thread.messages.slice(0, lastUserIndex + 1)
  const suffix = thread.messages.slice(lastUserIndex + 1)
  const plans = suffix.filter((message) =>
    message.chunks.some((chunk) => chunk.kind === "todo")
  )
  const finalizedTodos = plans.flatMap((message) =>
    message.chunks.filter(
      (chunk): chunk is Extract<Chunk, { kind: "todo" }> =>
        chunk.kind === "todo"
    )
  )
  if (finalizedTodos.length > 0) {
    const firstExecutionIndex = rebuiltMessages.findIndex(
      (message) => message.author === "agent" && message.executionNarration
    )
    if (firstExecutionIndex >= 0) {
      const executionMessage = rebuiltMessages[firstExecutionIndex]!
      rebuiltMessages[firstExecutionIndex] = {
        ...executionMessage,
        chunks: [...finalizedTodos, ...executionMessage.chunks],
      }
    } else {
      rebuiltMessages.unshift({
        ...plans[0]!,
        chunks: finalizedTodos,
        executionNarration: true,
      })
    }
  }
  const preserved = suffix.filter(
    (message) =>
      !message.chunks.some((chunk) => chunk.kind === "todo") &&
      message.chunks.some(
        (chunk) => !["text", "tool-execution"].includes(chunk.kind)
          || (!hasAssistantEvents && chunk.kind === "text" && !message.executionNarration)
      )
  )

  return {
    ...thread,
    messages: [
      ...prefix,
      ...(finalizedTodos.length > 0 ? [] : plans),
      ...rebuiltMessages,
      ...preserved,
    ],
  }
}

export function handleStreamPart(
  threadId: string,
  part: StreamPart,
  queryClient: QueryClient
) {
  const event = part.event ?? ""
  const data = isRecord(part.data) ? part.data : {}

  if (event === "thread_snapshot" || event === "thread_done") {
    updateThread(threadId, queryClient, (thread) =>
      applyThreadMeta(thread, data as ThreadMetaPayload)
    )
    if (event === "thread_done") {
      invalidateThreadList(queryClient)
    }
    return
  }

  if (event === "approval_required") {
    updateThread(threadId, queryClient, (thread) => ({
      ...thread,
      status: "interrupted",
      pendingApproval: data as unknown as AgentThread["pendingApproval"],
    }))
    invalidateThreadList(queryClient)
    return
  }

  if (event === "approval_resumed") {
    updateThread(threadId, queryClient, (thread) => ({
      ...thread,
      status: "running",
      pendingApproval: null,
    }))
    return
  }

  if (event === "rollback") {
    const messageId = typeof data.message_id === "string" ? data.message_id : ""
    if (!messageId) return
    updateThread(threadId, queryClient, (thread) => {
      let index = thread.messages.findIndex(
        (message) => message.id === messageId
      )
      const originalContent =
        typeof data.message_content === "string"
          ? data.message_content.trim()
          : ""
      if (index < 0 && originalContent) {
        index = thread.messages.findLastIndex(
          (message) =>
            message.author === "user" &&
            message.chunks
              .filter(
                (chunk): chunk is Extract<Chunk, { kind: "text" }> =>
                  chunk.kind === "text"
              )
              .map((chunk) => chunk.text)
              .join("")
              .trim() === originalContent
        )
      }
      if (index < 0 && !originalContent) {
        index = thread.messages.findLastIndex(
          (message) => message.author === "user"
        )
      }
      return index < 0
        ? thread
        : { ...thread, messages: thread.messages.slice(0, index) }
    })
    return
  }

  if (event === "todo_delta") {
    updateThread(threadId, queryClient, (thread) =>
      applyTodoDelta(thread, data as TodoDeltaPayload)
    )
    return
  }

  if (event === "user_message") {
    updateThread(threadId, queryClient, (thread) =>
      applyUserMessage(thread, data as UserMessagePayload)
    )
    return
  }

  if (event === "message_start") {
    updateThread(threadId, queryClient, (thread) =>
      applyMessageStart(
        thread,
        data as { message_id?: string; run_id?: string }
      )
    )
    return
  }

  if (event === "text_delta") {
    updateThread(threadId, queryClient, (thread) =>
      applyTextDelta(thread, data as TextDeltaPayload)
    )
    return
  }

  if (event === "tool_update") {
    const payload = data as ToolUpdatePayload
    if (!payload.title) return
    const detail = parseDetail(payload.detail)
    const toolName = String(detail.tool || "")
    const runEvent: RunEvent = {
      id:
        payload.event_id ||
        `live-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      thread_id: payload.thread_id || threadId,
      kind: (payload.kind as RunEvent["kind"]) || "other",
      title: payload.title,
      status: (payload.status as RunEvent["status"]) || "in_progress",
      detail: payload.detail ?? null,
      created_at: new Date().toISOString(),
    }
    const toolChunk = toolChunkFromEvent({
      ...runEvent,
      tool_kind: payload.tool_kind,
      tool_group_id: payload.tool_group_id,
    })

    updateThread(threadId, queryClient, (thread) => {
      const existingRunEvents = thread.runEvents ?? []
      const duplicateIndex = existingRunEvents.findIndex(
        (item) => item.id === runEvent.id
      )
      let updatedRunEvents = existingRunEvents
      if (duplicateIndex >= 0) {
        const updated = existingRunEvents.slice()
        updated[duplicateIndex] = {
          ...updated[duplicateIndex],
          ...runEvent,
          created_at: updated[duplicateIndex]!.created_at,
        }
        updatedRunEvents = updated
      } else {
        updatedRunEvents = [...existingRunEvents, runEvent]
      }

      let nextThread = { ...thread, runEvents: updatedRunEvents }
      if (toolName === "write_todos") {
        const assistantIndex = findRunMessageIndex(
          nextThread.messages,
          payload.run_id || null,
          "assistant"
        )
        if (assistantIndex >= 0) {
          nextThread = {
            ...nextThread,
            messages: nextThread.messages.map((message, index) =>
              index === assistantIndex
                ? { ...message, executionNarration: true }
                : message
            ),
          }
        }
        const args = isRecord(detail.args) ? detail.args : {}
        const todos = Array.isArray(args.todos)
          ? (args.todos as Array<TodoItem>)
          : []
        return todos.length > 0
          ? applyTodoDelta(nextThread, {
              message_id: `${threadId}-live-plan-${payload.run_id || "current"}`,
              run_id: payload.run_id,
              todos,
            })
          : nextThread
      }
      if (payload.is_process || !toolChunk || !isExecutionEvent(runEvent))
        return nextThread

      let messages = thread.messages.slice()
      let targetMessageIndex = messages.findIndex((message) =>
        message.chunks.some(
          (chunk) =>
            chunk.kind === "tool-execution" &&
            chunk.toolCallId === toolChunk.toolCallId
        )
      )
      if (targetMessageIndex < 0) {
        targetMessageIndex = messages.findLastIndex((message) =>
          message.chunks.some(
            (chunk) =>
              chunk.kind === "tool-execution" &&
              !!toolChunk.groupId &&
              chunk.groupId === toolChunk.groupId
          )
        )
      }
      if (targetMessageIndex < 0) {
        const candidateIndex = findRunMessageIndex(
          messages,
          payload.run_id || null,
          "assistant"
        )
        const candidate = candidateIndex >= 0 ? messages[candidateIndex] : null
        const candidateGroups =
          candidate?.chunks
            .filter(
              (chunk): chunk is ToolExecutionChunk =>
                chunk.kind === "tool-execution"
            )
            .map((chunk) => chunk.groupId)
            .filter(Boolean) ?? []
        if (
          candidate &&
          (candidateGroups.length === 0 ||
            candidateGroups.includes(toolChunk.groupId))
        ) {
          targetMessageIndex = candidateIndex
        }
      }
      if (targetMessageIndex < 0) {
        const newAgentMessage: Message = {
          id: `${threadId}-live-assistant-${payload.run_id || "current"}-${toolChunk.groupId || toolChunk.toolCallId}`,
          author: "agent",
          timestamp: new Date().toISOString(),
          chunks: [],
          executionNarration: true,
        }
        const planIndex = findRunMessageIndex(
          messages,
          payload.run_id || null,
          "plan"
        )
        const userIndex = findRunMessageIndex(
          messages,
          payload.run_id || null,
          "user"
        )
        const lastRunMessageIndex = findRunMessageIndex(
          messages,
          payload.run_id || null,
          "assistant"
        )
        const insertAt =
          lastRunMessageIndex >= 0
            ? lastRunMessageIndex + 1
            : planIndex >= 0
              ? planIndex + 1
              : userIndex >= 0
                ? userIndex + 1
                : messages.length
        messages = insertOrReplaceAt(messages, newAgentMessage, insertAt)
        targetMessageIndex = messages.findIndex(
          (message) => message.id === newAgentMessage.id
        )
      }

      const msg = messages[targetMessageIndex]
      if (!msg) return nextThread
      const existingChunkIndex = msg.chunks.findIndex(
        (chunk) =>
          chunk.kind === "tool-execution" &&
          chunk.toolCallId === toolChunk.toolCallId
      )
      const updatedChunks = msg.chunks.slice()
      if (existingChunkIndex >= 0) {
        updatedChunks[existingChunkIndex] = {
          ...updatedChunks[existingChunkIndex],
          ...toolChunk,
        }
      } else {
        updatedChunks.push(toolChunk)
      }
      messages[targetMessageIndex] = {
        ...msg,
        chunks: updatedChunks,
        executionNarration: true,
      }
      nextThread = { ...nextThread, messages }
      return nextThread
    })
    return
  }

  if (event === "done") {
    const current = queryClient.getQueryData<AgentThread>(
      agentThreadKeys.detail(threadId)
    )
    if (
      current?.status === "interrupted" ||
      current?.pendingApproval ||
      current?.status === "error"
    )
      return
    updateThread(threadId, queryClient, (thread) =>
      normalizeThreadHistory({ ...thread, status: "finished" })
    )
    invalidateThreadList(queryClient)
    return
  }

  if (event === "error") {
    updateThread(threadId, queryClient, (thread) => ({
      ...thread,
      status: "error",
    }))
  }
}

export async function processStreamResponse(
  response: Response,
  queryClient: QueryClient
): Promise<string | null> {
  if (!response.body) return null
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let capturedThreadId: string | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf("\n\n")
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      try {
        const parsed = parseSseBlock(block)
        if (parsed) {
          const data = isRecord(parsed.data) ? parsed.data : {}
          if (!capturedThreadId) {
            const candidate = data.thread_id || data.id
            if (typeof candidate === "string" && candidate)
              capturedThreadId = candidate
          }
          if (capturedThreadId)
            handleStreamPart(capturedThreadId, parsed, queryClient)
        }
      } catch {
        console.debug("failed to parse stream event", block)
      }
      boundary = buffer.indexOf("\n\n")
    }
  }

  return capturedThreadId
}

export function useAgentThreadStream(_threadId: string, _enabled: boolean) {
  // Stream is consumed directly by sendMessage/createThread mutations
  // via processStreamResponse. No separate SSE connection needed.
}
