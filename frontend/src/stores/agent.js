import { defineStore } from 'pinia'

import { dashboardApi } from '../api/client'
import { streamAgentMessage } from '../api/sse'

const DEFAULT_REPO = ''

function nowIso() {
  return new Date().toISOString()
}

function createTextMessage(author, text, idPrefix = author) {
  return {
    id: `${idPrefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    author,
    timestamp: nowIso(),
    chunks: text ? [{ kind: 'text', text }] : [],
  }
}

function findLocalUserMessage(messages, text) {
  return [...messages].reverse().find((message) => {
    if (message.author !== 'user' || !String(message.id || '').startsWith('local-user-')) {
      return false
    }
    const content = (message.chunks || [])
      .filter((chunk) => chunk.kind === 'text')
      .map((chunk) => chunk.text || '')
      .join('')
      .trim()
    return content === text
  })
}

function parseEventDetail(detail) {
  if (!detail) return {}
  if (typeof detail === 'object') return detail
  try {
    const parsed = JSON.parse(detail)
    return parsed && typeof parsed === 'object' ? parsed : { text: String(detail) }
  } catch {
    return { text: String(detail) }
  }
}

function normalizeToolItem(event) {
  const detail = parseEventDetail(event.detail)
  return {
    id: String(event.event_id || event.id || `${event.kind || 'other'}:${event.title || 'tool'}`),
    title: String(event.title || detail.tool || '工具调用'),
    kind: String(event.tool_kind || event.kind || 'other'),
    status: String(event.status || 'in_progress'),
    toolName: String(detail.tool || ''),
    args: detail.args && typeof detail.args === 'object' ? detail.args : {},
    elapsedMs: Number.isFinite(Number(detail.elapsed_ms)) ? Number(detail.elapsed_ms) : null,
    detailText: detail.text ? String(detail.text) : '',
    createdAt: event.created_at || nowIso(),
    groupId: String(event.tool_group_id || ''),
  }
}

function isVisibleToolEvent(event) {
  const id = String(event.event_id || event.id || '')
  const detail = parseEventDetail(event.detail)
  return event.is_tool === true
    || id.includes(':tool:')
    || id.includes(':stream:') && id.includes(':subagent:')
    || Boolean(detail.tool)
}

function isTodoToolEvent(event) {
  const detail = parseEventDetail(event?.detail)
  return detail.tool === 'write_todos' || String(event?.title || '') === 'write_todos'
}

function todoRunId(event) {
  const parts = String(event.id || '').split(':')
  const index = parts.indexOf('todos')
  return index >= 0 ? parts[index + 1] || '' : ''
}

const PROCESS_EVENT_KEYS = new Set([
  'created',
  'intent',
  'repo',
  'workspace',
  'sync',
  'agent',
  'model',
  'plan',
  'done',
  'failed',
])

function eventKey(event) {
  return String(event.id || '').split(':')[1] || ''
}

function millis(value) {
  const parsed = Date.parse(value || '')
  return Number.isFinite(parsed) ? parsed : null
}

function streamRunId(eventId) {
  const parts = String(eventId || '').split(':')
  const streamIndex = parts.indexOf('stream')
  return streamIndex >= 0 ? parts[streamIndex + 1] || '' : ''
}

function historyTimeline(threadId, runEvents) {
  const events = Array.isArray(runEvents) ? runEvents : []
  const latestTodoByRun = new Map()
  for (const event of events) {
    if (event.kind === 'todo') latestTodoByRun.set(todoRunId(event), event)
  }

  const timeline = []
  let processMessage = null
  let pendingNarrations = []
  let pendingNarrationRunId = ''
  let currentToolsEntry = null

  const ensureProcess = (event = {}) => {
    if (!processMessage) {
      processMessage = {
        id: `${threadId}-process-history`,
        author: 'agent',
        timestamp: event.created_at || nowIso(),
        chunks: [{
          kind: 'process', runId: 'history', startedAt: millis(event.created_at), finishedAt: null,
          items: [], narrations: [], timeline: [],
        }],
      }
      timeline.push(processMessage)
    }
    const chunk = processMessage.chunks[0]
    if (!Array.isArray(chunk.timeline)) chunk.timeline = []
    return chunk
  }

  const addEntry = (entry) => {
    const chunk = ensureProcess(entry)
    const index = chunk.timeline.findIndex((item) => item.id === entry.id)
    if (index >= 0) chunk.timeline[index] = { ...chunk.timeline[index], ...entry }
    else chunk.timeline.push(entry)
    return chunk
  }

  const appendProcessNarrations = (narrations) => {
    if (!narrations.length) return
    const chunk = ensureProcess({ created_at: narrations[0].timestamp })
    if (!Array.isArray(chunk.narrations)) chunk.narrations = []
    for (const narration of narrations) {
      if (!chunk.narrations.some((entry) => entry.id === narration.id)) {
        chunk.narrations.push({ id: narration.id, text: narration.text })
      }
      addEntry({ id: narration.id, type: 'text', text: narration.text, createdAt: narration.timestamp })
    }
  }

  const flushPendingNarrations = ({ keepLastAsAnswer = false } = {}) => {
    const answer = keepLastAsAnswer ? pendingNarrations.pop() : null
    appendProcessNarrations(pendingNarrations)
    if (answer) {
      timeline.push({
        id: answer.id,
        author: 'agent',
        timestamp: answer.timestamp,
        chunks: [{ kind: 'text', text: answer.text }],
      })
    }
    pendingNarrations = []
    pendingNarrationRunId = ''
  }

  for (const event of events) {
    const detail = parseEventDetail(event.detail)
    const eventId = String(event.id || '')

    if (PROCESS_EVENT_KEYS.has(eventKey(event))) {
      currentToolsEntry = null
      const keepLastAsAnswer = ['plan', 'done', 'failed'].includes(eventKey(event))
      // If a provider sends the terminal process event before any other
      // process event, create the wrapper first so the final answer still
      // appears after the execution panel rather than before it.
      if (keepLastAsAnswer && pendingNarrations.length && !processMessage) {
        ensureProcess(event)
      }
      if (pendingNarrations.length) {
        flushPendingNarrations({ keepLastAsAnswer })
      }
      const chunk = ensureProcess(event)
      const createdAt = millis(event.created_at)
      const updatedAt = millis(event.updated_at)
      if (createdAt != null && (chunk.startedAt == null || createdAt < chunk.startedAt)) {
        chunk.startedAt = createdAt
      }
      if (updatedAt != null && (chunk.finishedAt == null || updatedAt > chunk.finishedAt)) {
        chunk.finishedAt = updatedAt
      }
      const item = {
        id: eventId,
        title: String(event.title || '任务处理'),
        status: String(event.status || 'completed'),
      }
      if (!chunk.items.some((entry) => entry.id === item.id)) chunk.items.push(item)
      addEntry({ id: item.id, type: 'step', ...item, createdAt: event.created_at })
      continue
    }

    if (eventId.includes(':stream:') && eventId.includes(':assistant:') && detail.text) {
      currentToolsEntry = null
      const runId = streamRunId(eventId)
      if (pendingNarrations.length && pendingNarrationRunId && runId !== pendingNarrationRunId) {
        flushPendingNarrations()
      }
      pendingNarrationRunId = runId
      pendingNarrations.push({
        id: `${threadId}-history-${eventId}`,
        timestamp: event.created_at || nowIso(),
        text: String(detail.text),
      })
      continue
    }

    if (event.kind === 'todo') {
      currentToolsEntry = null
      continue
    }

    if (!isVisibleToolEvent(event)) {
      currentToolsEntry = null
      continue
    }
    if (pendingNarrations.length) flushPendingNarrations()
    const item = normalizeToolItem(event)
    if (item.toolName === 'write_todos' || item.title === 'write_todos') {
      continue
    }
    const chunk = ensureProcess(event)
    const groupId = item.groupId || ''
    let toolsEntry = groupId
      ? chunk.timeline.find((entry) => entry.type === 'tools' && entry.groupId === groupId)
      : currentToolsEntry
    if (!toolsEntry) {
      const fallbackGroupId = groupId || `history-${item.id}`
      toolsEntry = { id: `${threadId}-tool-history-${fallbackGroupId}`, type: 'tools', groupId: fallbackGroupId, runId: streamRunId(eventId), narrations: [], items: [], createdAt: item.createdAt }
      chunk.timeline.push(toolsEntry)
    }
    currentToolsEntry = toolsEntry
    const itemIndex = toolsEntry.items.findIndex((entry) => entry.id === item.id)
    if (itemIndex >= 0) toolsEntry.items[itemIndex] = { ...toolsEntry.items[itemIndex], ...item }
    else toolsEntry.items.push(item)
  }

  flushPendingNarrations({ keepLastAsAnswer: true })

  const processChunk = processMessage?.chunks?.find((chunk) => chunk.kind === 'process')
  if (processChunk) {
    let latestTodo = null
    for (let index = events.length - 1; index >= 0; index -= 1) {
      if (events[index].kind === 'todo' && latestTodoByRun.has(todoRunId(events[index]))) {
        latestTodo = latestTodoByRun.get(todoRunId(events[index]))
        break
      }
    }
    const todos = latestTodo ? parseEventDetail(latestTodo.detail).todos : null
    if (Array.isArray(todos) && todos.length) {
      addEntry({
        id: `${threadId}-history-todo-final`, type: 'todo',
        todos: todos.map((todo) => ({ ...todo, status: todo.status === 'error' ? 'error' : 'completed' })),
      })
    }
    processChunk.finishedAt = processChunk.finishedAt || Date.now()
  }

  return timeline
}

function messageText(message) {
  return (message.chunks || [])
    .filter((chunk) => chunk.kind === 'text')
    .map((chunk) => String(chunk.text || ''))
    .join('')
    .trim()
}

function normalizeThreadMessages(messages, runEvents = [], threadId = '') {
  if (!Array.isArray(messages)) return []
  const events = Array.isArray(runEvents) ? runEvents : []
  const normalized = messages
    .filter((message) => message && Array.isArray(message.chunks))
    .map((message) => ({
      id: String(message.id || `${message.author || 'message'}-${Date.now()}`),
      author: message.author || 'agent',
      timestamp: message.timestamp || nowIso(),
      chunks: message.chunks,
      hidden: !!message.hidden,
    }))

  const timeline = historyTimeline(threadId, events)
  if (!timeline.length) return normalized

  let latestUserIndex = -1
  for (let index = normalized.length - 1; index >= 0; index -= 1) {
    if (normalized[index].author === 'user') {
      latestUserIndex = index
      break
    }
  }

  const hasStreamAnswer = events.some((event) => (
    String(event.id || '').includes(':stream:')
    && String(event.id || '').includes(':assistant:')
    && Boolean(parseEventDetail(event.detail).text)
  ))
  const preservedSuffix = normalized.slice(latestUserIndex + 1).filter((message) => {
    if ((message.chunks || []).some((chunk) => chunk.kind === 'todo')) return false
    if (hasStreamAnswer && messageText(message)) return false
    return true
  })
  return [
    ...normalized.slice(0, latestUserIndex + 1),
    ...timeline,
    ...preservedSuffix,
  ]
}

function removeEmptyAgentMessages(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.author === 'agent' && (!message.chunks || message.chunks.length === 0)) {
      messages.splice(index, 1)
    }
  }
}

function ensureAgentMessage(messages, messageId) {
  let message = messages.find((item) => item.id === messageId)
  if (!message) {
    message = {
      id: messageId,
      author: 'agent',
      timestamp: nowIso(),
      chunks: [],
    }
    messages.push(message)
  }
  return message
}

function upsertTodoMessage(messages, messageId, todos) {
  const message = ensureAgentMessage(messages, messageId)
  message.author = 'agent'
  message.chunks = [
    { kind: 'text', text: '任务计划' },
    {
      kind: 'todo',
      todos: todos.map((todo) => ({
        content: String(todo.content || todo.task || todo.title || '').trim(),
        status: ['completed', 'done'].includes(todo.status)
          ? 'completed'
          : ['in_progress', 'in-progress', 'active', 'doing'].includes(todo.status)
            ? 'in_progress'
            : 'pending',
      })).filter((todo) => todo.content),
    },
  ]
}

function todosFromToolUpdate(data) {
  const detail = parseEventDetail(data.detail)
  if (detail.tool !== 'write_todos') return null
  const todos = detail.args?.todos || detail.args?.items
  return Array.isArray(todos) ? todos : null
}

function createProcessMessage() {
  const timestamp = Date.now()
  return {
    id: `local-process-${timestamp}`,
    author: 'agent',
    timestamp: nowIso(),
    chunks: [{
      kind: 'process',
      runId: 'pending',
      startedAt: timestamp,
      finishedAt: null,
      items: [{
        id: `local-process-connect-${timestamp}`,
        title: '正在连接服务',
        status: 'in_progress',
      }],
      narrations: [],
      timeline: [],
    }],
  }
}

function processChunk(messages, threadId, runId) {
  let message = messages.find((entry) => (
    entry.author === 'agent'
    && entry.chunks?.some((chunk) => chunk.kind === 'process' && chunk.runId === runId)
  ))
  if (!message) {
    message = [...messages].reverse().find((entry) => (
      entry.author === 'agent'
      && entry.chunks?.some((chunk) => chunk.kind === 'process' && chunk.runId === 'pending')
    ))
  }
  if (!message) {
    message = {
      id: `${threadId}-process-${runId}`,
      author: 'agent',
      timestamp: nowIso(),
      chunks: [{
        kind: 'process',
        runId,
        startedAt: Date.now(),
        finishedAt: null,
        items: [],
        narrations: [],
        timeline: [],
      }],
    }
    messages.push(message)
  }

  message.id = `${threadId}-process-${runId}`
  const chunk = message.chunks.find((entry) => entry.kind === 'process')
  chunk.runId = runId
  if (!Array.isArray(chunk.narrations)) chunk.narrations = []
  if (!Array.isArray(chunk.timeline)) chunk.timeline = []
  return chunk
}

function appendProcessTimeline(chunk, entry) {
  if (!chunk || !entry) return
  if (!Array.isArray(chunk.timeline)) chunk.timeline = []
  const index = chunk.timeline.findIndex((item) => item.id === entry.id)
  if (index >= 0) chunk.timeline[index] = { ...chunk.timeline[index], ...entry }
  else chunk.timeline.push(entry)
}

function markProcessConnected(messages, threadId) {
  const chunk = processChunk(messages, threadId, 'pending')
  if (chunk.items.length === 1 && String(chunk.items[0].id).startsWith('local-process-connect-')) {
    chunk.items[0] = {
      ...chunk.items[0],
      title: '正在识别任务类型',
    }
  }
}

function upsertProcessActivity(messages, threadId, data) {
  if (data.is_process !== true) return
  const runId = String(data.run_id || 'current')
  const chunk = processChunk(messages, threadId, runId)
  chunk.items = chunk.items.filter((item) => !String(item.id).startsWith('local-process-connect-'))

  const item = {
    id: String(data.event_id || `${threadId}:${data.event_key || data.title}`),
    title: String(data.title || '正在处理'),
    status: String(data.status || 'in_progress'),
  }
  const existingIndex = chunk.items.findIndex((entry) => entry.id === item.id)
  if (existingIndex >= 0) chunk.items[existingIndex] = { ...chunk.items[existingIndex], ...item }
  else chunk.items.push(item)
  appendProcessTimeline(chunk, { id: item.id, type: 'step', ...item })
}

function finishProcessActivity(messages, status = 'completed') {
  const finishedAt = Date.now()
  for (const message of messages) {
    for (const chunk of message.chunks || []) {
      if (chunk.kind !== 'process') continue
      chunk.finishedAt = finishedAt
      chunk.items = chunk.items.map((item) => (
        item.status === 'in_progress' ? { ...item, status } : item
      ))
      chunk.timeline = (chunk.timeline || []).map((entry) => {
        if (entry.type === 'step') {
          return entry.status === 'in_progress' ? { ...entry, status } : entry
        }
        if (entry.type === 'tools') {
          return {
            ...entry,
            items: (entry.items || []).map((item) => (
              ['pending', 'in_progress'].includes(item.status) ? { ...item, status } : item
            )),
          }
        }
        return entry
      })
    }
  }
}

function foldEarlierAssistantMessages(messages, threadId, runId, currentMessageId = '') {
  if (!threadId || !runId) return
  const prefix = `${threadId}-live-assistant-${runId}-`
  const candidates = []
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index]
    if (!String(message.id || '').startsWith(prefix) || message.id === currentMessageId) continue
    const text = messageText(message)
    if (text) candidates.push({ index, message, text })
  }
  for (let offset = candidates.length - 1; offset >= 0; offset -= 1) {
    messages.splice(candidates[offset].index, 1)
  }
  const chunk = processChunk(messages, threadId, runId)
  for (const candidate of candidates) {
    if (!chunk.narrations.some((entry) => entry.id === candidate.message.id)) {
      chunk.narrations.push({ id: candidate.message.id, text: candidate.text })
    }
    appendProcessTimeline(chunk, { id: candidate.message.id, type: 'text', text: candidate.text })
  }
}

function foldCompletedRunNarrations(messages) {
  const runIds = new Set()
  for (const message of messages) {
    const match = String(message.id || '').match(/-live-assistant-(.+)-\d+$/)
    if (match?.[1]) runIds.add(match[1])
  }
  for (const runId of runIds) {
    const prefix = `-live-assistant-${runId}-`
    const last = [...messages].reverse().find((message) => (
      String(message.id || '').includes(prefix) && messageText(message)
    ))
    if (!last) continue
    const threadId = String(last.id).split(prefix)[0]
    foldEarlierAssistantMessages(messages, threadId, runId, last.id)
  }
}

function upsertToolActivity(messages, threadId, data) {
  if (!isVisibleToolEvent(data)) return
  const item = normalizeToolItem(data)
  if (item.toolName === 'write_todos' || item.title === 'write_todos') return

  const runId = String(data.run_id || 'current')
  const process = processChunk(messages, threadId, runId)
  const groupId = item.groupId || ''
  let toolsEntry = groupId
    ? process.timeline.find((entry) => entry.type === 'tools' && entry.groupId === groupId)
    : process.timeline[process.timeline.length - 1]?.type === 'tools'
      ? process.timeline[process.timeline.length - 1]
      : null
  if (!toolsEntry) {
    const fallbackGroupId = groupId || item.id
    toolsEntry = { id: `${threadId}-live-tool-group-${fallbackGroupId}`, type: 'tools', groupId: fallbackGroupId, runId, narrations: [], items: [] }
    appendProcessTimeline(process, toolsEntry)
  }
  const existingIndex = toolsEntry.items.findIndex((entry) => entry.id === item.id)
  const existing = existingIndex >= 0 ? toolsEntry.items[existingIndex] : {}
  const merged = {
    ...existing,
    ...item,
    args: Object.keys(item.args).length ? item.args : existing.args,
    toolName: item.toolName || existing.toolName,
    elapsedMs: item.elapsedMs ?? existing.elapsedMs,
    detailText: item.detailText || existing.detailText,
  }
  if (existingIndex >= 0) toolsEntry.items[existingIndex] = merged
  else toolsEntry.items.push(merged)
}

function moveTodosIntoProcess(messages) {
  let latestUserIndex = -1
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].author === 'user') {
      latestUserIndex = index
      break
    }
  }
  const processIndex = messages.findLastIndex((message, index) => (
    index > latestUserIndex && message.chunks?.some((chunk) => chunk.kind === 'process')
  ))
  if (processIndex < 0) return
  const processMessage = messages[processIndex]
  const todoMessages = messages.filter((message, index) => (
    index > latestUserIndex
    && message.chunks?.some((chunk) => chunk.kind === 'todo')
  ))
  if (!todoMessages.length) return
  const process = processMessage.chunks.find((chunk) => chunk.kind === 'process')
  const todoChunks = todoMessages.flatMap((message) => message.chunks.filter((chunk) => chunk.kind === 'todo'))
  const latest = todoChunks[todoChunks.length - 1]
  if (!latest?.todos?.length) return
  appendProcessTimeline(process, {
    id: `${processMessage.id}-todo-final`, type: 'todo',
    todos: latest.todos.map((todo) => ({ ...todo, status: todo.status === 'error' ? 'error' : 'completed' })),
  })
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (todoMessages.includes(messages[index])) messages.splice(index, 1)
  }
}

function appendTextDelta(messages, messageId, content, mode = 'append') {
  if (!messageId || !content) return
  const message = ensureAgentMessage(messages, messageId)
  const chunk = message.chunks.find((item) => item.kind === 'text')
  if (chunk) {
    chunk.text = mode === 'replace' ? content : `${chunk.text || ''}${content}`
  } else {
    message.chunks.push({ kind: 'text', text: content })
  }
}

function mergeThreadMeta(target, source) {
  if (!target || !source) return
  target.status = source.status || target.status
  target.branch = source.branch || target.branch
  target.repoUrl = source.repoUrl || target.repoUrl
  target.platform = source.platform || target.platform
  target.platformDisplay = source.platformDisplay || target.platformDisplay
  target.pr = source.pr || target.pr
  target.runEvents = source.runEvents || target.runEvents
  target.updatedAt = source.updatedAt || target.updatedAt
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    user: null,
    options: null,
    threads: [],
    currentThread: null,
    messages: [],
    selectedRepo: DEFAULT_REPO,
    selectedPlatform: 'github',
    selectedModel: '',
    selectedEffort: 'default',
    streaming: false,
    loading: false,
    error: '',
    controller: null,
    activeRunThreadId: null,
    confirmDialog: null,
    confirmResolver: null,
  }),
  getters: {
    currentThreadId: (state) => state.currentThread?.id || null,
    latestUserMessageId: (state) => [...state.messages].reverse().find((message) => message.author === 'user')?.id || null,
    modelOptions: (state) => state.options?.models || [],
    canSend: (state) => !state.streaming,
  },
  actions: {
    resolveConfirm(value) {
      const resolver = this.confirmResolver
      this.confirmResolver = null
      this.confirmDialog = null
      resolver?.(Boolean(value))
    },
    async bootstrap() {
      this.loading = true
      this.error = ''
      try {
        const [user, options, threads] = await Promise.all([
          dashboardApi.me(),
          dashboardApi.options(),
          dashboardApi.listThreads(),
        ])
        this.user = user
        this.options = options
        this.threads = threads
        this.selectedModel = options.default_agent_model || options.models?.[0]?.id || ''
        this.selectedEffort = options.default_agent_reasoning_effort || 'default'
        if (!this.currentThread && threads.length) {
          await this.selectThread(threads[0].id)
        }
      } catch (error) {
        this.error = error.message || '初始化前端失败'
      } finally {
        this.loading = false
      }
    },
    async refreshThreads() {
      try {
        this.threads = await dashboardApi.listThreads()
      } catch {
        // 侧边栏刷新失败不影响当前对话继续展示。
      }
    },
    async selectThread(threadId) {
      if (this.streaming) {
        this.controller?.abort()
        this.streaming = false
        this.activeRunThreadId = null
        this.controller = null
      }
      this.error = ''
      const thread = await dashboardApi.getThread(threadId)
      this.currentThread = thread
      this.selectedRepo = thread.repo || thread.repoFullName || DEFAULT_REPO
      if (thread.platform) {
        this.selectedPlatform = thread.platform
      }
      this.messages = normalizeThreadMessages(thread.messages, thread.runEvents, thread.id)
    },
    async createThread() {
      if (this.streaming) return
      this.currentThread = null
      this.messages = []
      this.error = ''
      this.activeRunThreadId = null
      this.selectedRepo = DEFAULT_REPO
      this.selectedPlatform = 'github'
    },
    async deleteThread(threadId) {
      if (this.streaming) return
      await dashboardApi.deleteThread(threadId)
      this.threads = this.threads.filter((thread) => thread.id !== threadId)
      if (this.currentThread?.id === threadId) {
        this.currentThread = null
        this.messages = []
        if (this.threads.length) await this.selectThread(this.threads[0].id)
      }
    },
    stopStream() {
      this.controller?.abort()
    },
    async submit(content) {
      const prompt = content.trim()
      if (!prompt || this.streaming) return

      if (!this.selectedRepo.trim()) {
        this.error = '请先填写代码仓库'
        return
      }

      this.error = ''
      this.streaming = true
      this.controller = new AbortController()
      const localUserMessage = createTextMessage('user', prompt, 'local-user')
      this.messages.push(localUserMessage)
      this.messages.push(createProcessMessage())

      try {
        const initialThreadId = this.currentThread?.id || null
        this.activeRunThreadId = initialThreadId || 'pending'
        await this.consumeMessageStream(initialThreadId, prompt)
      } catch (error) {
        if (error.name !== 'AbortError') {
          this.error = error.message || 'Agent 执行失败'
          this.currentThread = {
            ...(this.currentThread || {}),
            status: 'error',
          }
        }
      } finally {
        this.streaming = false
        this.controller = null
        this.activeRunThreadId = null
        await this.refreshThreads()
      }
    },
    async editMessage({ message, content }) {
      // 页面只会给最后一条用户消息显示编辑入口；这里不再用本地 ID
      // 做第二次严格比较，因为 SSE 回退后会重新分配消息 ID。
      if (this.streaming || !message || message.author !== 'user') return
      const index = this.messages.findIndex((item) => item.id === message.id)
      if (index < 0) return
      let impact = '没有需要回退的任务或代码变更。\n是否使用编辑后的内容重新发送？'
      try {
        const response = await fetch(`/dashboard/api/threads/${encodeURIComponent(this.currentThreadId)}/edit-preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: message.id, original_content: messageText(message) }),
        })
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '无法获取回退影响')
        const preview = await response.json()
        impact = preview.requires_rollback
          ? `将回退 ${preview.messages_to_replace} 条消息和 ${preview.runs_to_replace} 次任务执行。${preview.code_changes_detected ? '\n检测到代码变更，回退可能产生冲突。' : ''}\n请确认已了解影响后继续。`
          : impact
      } catch (error) {
        this.error = error.message || '无法获取回退影响'
        return
      }
      const confirmed = await new Promise((resolve) => {
        this.confirmResolver = resolve
        this.confirmDialog = { title: '确认回退并重新发送', message: impact }
      })
      if (!confirmed) return
      const oldMessages = this.messages.slice(0, index)
      this.messages = oldMessages
      this.error = ''
      this.streaming = true
      this.controller = new AbortController()
      this.messages.push(createTextMessage('user', content, 'local-user'))
      this.messages.push(createProcessMessage())
      try {
        await this.consumeMessageStream(this.currentThreadId, content, {
          edit_message_id: message.id,
          edit_message_content: (message.chunks || []).filter((chunk) => chunk.kind === 'text').map((chunk) => chunk.text || '').join(''),
          rollback_confirmed: true,
        })
      } catch (error) {
        if (error.name !== 'AbortError') this.error = error.message || 'Agent 执行失败'
      } finally {
        this.streaming = false
        this.controller = null
        await this.refreshThreads()
      }
    },
    async consumeMessageStream(threadId, prompt, extra = {}) {
      await streamAgentMessage(
        threadId,
        {
          content: prompt,
          repo: this.selectedRepo || DEFAULT_REPO,
          model_id: this.selectedModel || null,
          effort: this.selectedEffort || null,
          ...extra,
        },
        {
          signal: this.controller.signal,
          onEvent: (event, data) => {
            const eventThreadId = data.thread_id || data.id || threadId
            if (eventThreadId && this.currentThread?.id && this.currentThread.id !== eventThreadId) return

            if (event === 'thread_snapshot' || event === 'thread_done') {
              if (!this.currentThread) {
                this.currentThread = data
                this.selectedRepo = data.repo || data.repoFullName || this.selectedRepo || DEFAULT_REPO
              } else {
                mergeThreadMeta(this.currentThread, data)
              }
              this.activeRunThreadId = data.id || data.thread_id || eventThreadId || this.activeRunThreadId
              if (event === 'thread_snapshot') markProcessConnected(this.messages, eventThreadId)
              return
            }

            if (event === 'rollback') {
              const rollbackId = String(data.message_id || '')
              const rollbackIndex = this.messages.findIndex((message) => message.id === rollbackId)
              if (rollbackIndex >= 0) this.messages.splice(rollbackIndex)
              return
            }

            if (event === 'user_message') {
              const text = String(data.content || '').trim()
              const local = findLocalUserMessage(this.messages, text)
              if (local) {
                local.id = data.message_id || local.id
                local.timestamp = data.timestamp || local.timestamp
              } else if (text) {
                this.messages.push(createTextMessage('user', text, data.message_id || 'user'))
              }
              return
            }

            if (event === 'todo_delta') {
              upsertTodoMessage(
                this.messages,
                data.message_id || `todo-${Date.now()}`,
                Array.isArray(data.todos) ? data.todos : [],
              )
              return
            }

            if (event === 'tool_update') {
              upsertProcessActivity(this.messages, eventThreadId, data)
              if (isVisibleToolEvent(data) && !isTodoToolEvent(data)) {
                foldEarlierAssistantMessages(
                  this.messages,
                  eventThreadId,
                  String(data.run_id || ''),
                )
              }
              const fallbackTodos = todosFromToolUpdate(data)
              if (fallbackTodos) {
                upsertTodoMessage(
                  this.messages,
                  `${eventThreadId}-live-plan-${data.run_id || 'current'}`,
                  fallbackTodos,
                )
              }
              upsertToolActivity(this.messages, eventThreadId, data)
              return
            }

            if (event === 'message_start') {
              if (data.message_id) {
                foldEarlierAssistantMessages(
                  this.messages,
                  eventThreadId,
                  String(data.run_id || ''),
                  data.message_id,
                )
                ensureAgentMessage(this.messages, data.message_id)
              }
              return
            }

            if (event === 'text_delta') {
              removeEmptyAgentMessages(this.messages)
              appendTextDelta(
                this.messages,
                data.message_id || `assistant-${Date.now()}`,
                data.content || '',
                data.mode || 'append',
              )
              return
            }

            if (event === 'error') {
              removeEmptyAgentMessages(this.messages)
              finishProcessActivity(this.messages, 'error')
              this.error = data.message || data.detail || 'Agent 执行失败'
              if (this.currentThread) this.currentThread.status = 'error'
              return
            }

            if (event === 'done') {
              removeEmptyAgentMessages(this.messages)
              foldCompletedRunNarrations(this.messages)
              const failed = this.currentThread?.status === 'error' || !!this.error
              if (!failed) moveTodosIntoProcess(this.messages)
              finishProcessActivity(this.messages, failed ? 'error' : 'completed')
              if (this.currentThread && this.currentThread.status === 'running') {
                this.currentThread.status = 'finished'
              }
            }
          },
        },
      )
    },
  },
})
