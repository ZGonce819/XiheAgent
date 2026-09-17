import type { AgentSchedule, AgentThread, ImageChunk, Message, PendingCommandApproval } from "./types"

export type { AgentSchedule, AgentThread, Message }

export interface ThreadCreateRequest {
  prompt: string
  images?: Array<ImageChunk>
  repo?: string | null
  repo_explicitly_none?: boolean
  model_id?: string | null
  effort?: string | null
  backend_type?: string | null
}

export interface ThreadMessageRequest {
  content: string
  images?: Array<ImageChunk>
  repo?: string | null
  model_id?: string | null
  effort?: string | null
  backend_type?: string | null
  edit_message_id?: string | null
  edit_message_content?: string | null
  edit_snapshot?: Array<Message>
  rollback_confirmed?: boolean
}

export interface EditPreview {
  message_id: string
  messages_to_replace: number
  runs_to_replace: number
  code_changes_detected: boolean
  requires_rollback: boolean
}

export interface ScheduleCreateRequest {
  prompt: string
  schedule: string
  name?: string | null
  repo?: string | null
  model_id?: string | null
  effort?: string | null
}

export interface ScheduleUpdateRequest {
  prompt?: string | null
  schedule?: string | null
  name?: string | null
  repo?: string | null
  model_id?: string | null
  effort?: string | null
  enabled?: boolean | null
}

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(
  /\/$/,
  ""
)

async function agentsRequest<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) {
        message =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
      }
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

async function agentsStreamRequest(path: string, init: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      if (body?.detail) message = String(body.detail)
    } catch {
      // Keep the HTTP status text when the response is not JSON.
    }
    throw new Error(message)
  }
  return response
}

export const agentsApi = {
  listThreads: () => agentsRequest<Array<AgentThread>>("/threads"),
  listSchedules: () => agentsRequest<Array<AgentSchedule>>("/schedules"),
  createSchedule: (body: ScheduleCreateRequest) =>
    agentsRequest<AgentSchedule>("/schedules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSchedule: (scheduleId: string, body: ScheduleUpdateRequest) =>
    agentsRequest<AgentSchedule>(
      `/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      }
    ),
  deleteSchedule: (scheduleId: string) =>
    agentsRequest<void>(`/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "DELETE",
    }),
  getThread: (threadId: string) =>
    agentsRequest<AgentThread>(`/threads/${encodeURIComponent(threadId)}`),
  saveViewSnapshot: (threadId: string, messages: Array<Message>) =>
    agentsRequest<void>(`/threads/${encodeURIComponent(threadId)}/view-snapshot`, {
      method: "PUT",
      body: JSON.stringify({ messages }),
    }),
  previewEdit: (threadId: string, messageId: string, originalContent: string) =>
    agentsRequest<EditPreview>(`/threads/${encodeURIComponent(threadId)}/edit-preview`, {
      method: "POST",
      body: JSON.stringify({ message_id: messageId, original_content: originalContent }),
    }),
  createThread: (body: ThreadCreateRequest) =>
    fetch(`${API_BASE}/dashboard/api/threads/stream-message`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: body.prompt,
        repo: body.repo,
        images: body.images,
        model_id: body.model_id,
        effort: body.effort,
        backend_type: body.backend_type,
      }),
    }).then((res) => {
      if (!res.ok) throw new Error(res.statusText)
      return res
    }),
  sendMessage: (threadId: string, body: ThreadMessageRequest) =>
    fetch(`${API_BASE}/dashboard/api/threads/${encodeURIComponent(threadId)}/stream-message`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((res) => {
      if (!res.ok) throw new Error(res.statusText)
      return res
    }),
  cancelThread: (threadId: string) =>
    agentsRequest<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}/cancel`,
      {
        method: "POST",
      }
    ),
  deleteThread: (threadId: string) =>
    agentsRequest<void>(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    }),
  updateThreadTitle: (threadId: string, title: string) =>
    agentsRequest<AgentThread>(`/threads/${encodeURIComponent(threadId)}/title`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  getPendingApproval: (threadId: string) =>
    agentsRequest<PendingCommandApproval | null>(`/threads/${encodeURIComponent(threadId)}/approval`),
  decideApproval: (threadId: string, approvalId: string, decision: "approve" | "reject") =>
    agentsStreamRequest(`/threads/${encodeURIComponent(threadId)}/approval`, {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId, decision }),
    }),
  streamUrl: (threadId: string) =>
    `${API_BASE}/dashboard/api/threads/${encodeURIComponent(threadId)}/stream`,
}

export function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d`
  const weeks = Math.floor(days / 7)
  return `${weeks}w`
}

export type ThreadGroup = "today" | "last30" | "older"

export function groupThreads(
  threads: Array<AgentThread>
): Record<ThreadGroup, Array<AgentThread>> {
  const todayStart = new Date()
  todayStart.setHours(0, 0, 0, 0)
  const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000

  const groups: Record<ThreadGroup, Array<AgentThread>> = {
    today: [],
    last30: [],
    older: [],
  }

  for (const thread of [...threads].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (thread.updatedAt >= todayStart.getTime()) {
      groups.today.push(thread)
    } else if (thread.updatedAt >= thirtyDaysAgo) {
      groups.last30.push(thread)
    } else {
      groups.older.push(thread)
    }
  }

  return groups
}
