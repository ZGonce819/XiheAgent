import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import { agentsApi } from "./api"
import { addPendingPrompt, dropPendingPrompts } from "./pendingPrompts"
import type { ScheduleUpdateRequest } from "./api"
import type { ImageChunk } from "./types"

function messageText(message: { chunks: Array<{ kind: string; text?: string }> }): string {
  return message.chunks
    .filter((chunk) => chunk.kind === "text")
    .map((chunk) => chunk.text ?? "")
    .join("")
}

export const agentThreadKeys = {
  all: ["agent-threads"] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

function invalidateThreadList(queryClient: ReturnType<typeof useQueryClient>) {
  // `agentThreadKeys.all` 是列表 key，同时也是 detail key 的前缀。
  // TanStack Query 默认按前缀匹配，如果不加 exact，会连当前 detail
  // `["agent-threads", threadId]` 一起 refetch。Agent 正在或刚刚完成流式输出时，
  // detail refetch 会用 checkpoint 快照覆盖前端本地已经追加好的 messages。
  void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true })
}

export function useAgentThreads() {
  return useQuery({
    queryKey: agentThreadKeys.all,
    queryFn: () => agentsApi.listThreads(),
  })
}

export function useAgentThread(threadId: string) {
  return useQuery({
    queryKey: agentThreadKeys.detail(threadId),
    queryFn: () => agentsApi.getThread(threadId),
  })
}

export function useAgentSchedules() {
  return useQuery({
    queryKey: agentScheduleKeys.all,
    queryFn: () => agentsApi.listSchedules(),
  })
}

export function useCreateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.createSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useUpdateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { scheduleId: string; body: ScheduleUpdateRequest }) =>
      agentsApi.updateSchedule(vars.scheduleId, vars.body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useDeleteAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.deleteSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useCreateAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: agentsApi.createThread,
    onSuccess: (thread, variables) => {
      const alreadyConfirmed = thread.messages.some(
        (message) =>
          message.author === "user" && messageText(message) === variables.prompt
      )
      if (alreadyConfirmed) {
        dropPendingPrompts(thread.id, () => true)
      } else {
        addPendingPrompt(
          thread.id,
          variables.prompt,
          thread.messages.length,
          variables.images
        )
      }
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), {
        ...thread,
        status: thread.status === "idle" ? "running" : thread.status,
      })
      invalidateThreadList(queryClient)
      navigate({ to: "/agents/$threadId", params: { threadId: thread.id } })
    },
  })
}

export interface SendAgentMessageVariables {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
}

export function useSendAgentMessage(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: SendAgentMessageVariables) =>
      agentsApi.sendMessage(threadId, {
        content: vars.content,
        images: vars.images,
        model_id: vars.model_id,
        effort: vars.effort,
      }),
    onSuccess: (thread) => {
      queryClient.setQueryData(
        agentThreadKeys.detail(threadId),
        (prev: typeof thread | undefined) => {
          if (!prev) return thread
          return {
            ...thread,
            // 发送新消息后，实时内容由 SSE 增量追加。这里不能用刚返回的
            // checkpoint 快照覆盖本地消息，否则用户刚输入的内容和上一轮输出会闪烁或消失。
            messages: prev.messages,
          }
        }
      )
      invalidateThreadList(queryClient)
    },
  })
}

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      invalidateThreadList(queryClient)
    },
  })
}

export function useDeleteAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.deleteThread(threadId),
    onSuccess: (_, threadId) => {
      queryClient.removeQueries({ queryKey: agentThreadKeys.detail(threadId) })
      invalidateThreadList(queryClient)
      const path = window.location.pathname
      if (path.includes(`/agents/${threadId}`)) {
        navigate({ to: "/agents" })
      }
    },
  })
}
