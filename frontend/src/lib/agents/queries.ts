import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import { agentsApi } from "./api"
import { addPendingPrompt } from "./pendingPrompts"
import {
  handleStreamPart,
  normalizeThreadHistory,
  parseSseBlock,
  processStreamResponse,
} from "./useThreadStream"
import type { ScheduleUpdateRequest } from "./api"
import type { AgentThread, ImageChunk, Message } from "./types"

export const agentThreadKeys = {
  all: ["agent-threads"] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
}

// A stream can finish while a delete request is in flight and invalidate the
// list with an older server response. Keep a short-lived client tombstone so
// those late responses cannot make a deleted thread flash back into the UI.
const deletedThreadIds = new Set<string>()

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

function invalidateThreadList(queryClient: ReturnType<typeof useQueryClient>) {
  // `agentThreadKeys.all` 是列表 key，同时也是 detail key 的前缀。
  // TanStack Query 默认按前缀匹配，如果不加 exact，会连当前 detail
  // `["agent-threads", threadId]` 一起 refetch。Agent 正在或刚刚完成流式输出时，
  // detail refetch 会用 checkpoint 快照覆盖前端本地已经追加好的 messages。
  void queryClient.invalidateQueries({
    queryKey: agentThreadKeys.all,
    exact: true,
  })
}

export function useAgentThreads() {
  return useQuery({
    queryKey: agentThreadKeys.all,
    queryFn: async () => {
      const threads = await agentsApi.listThreads()
      return threads.filter((thread) => !deletedThreadIds.has(thread.id))
    },
  })
}

export function useAgentThread(
  threadId: string,
  options?: { refetchInterval?: number | false }
) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: agentThreadKeys.detail(threadId),
    queryFn: async () => {
      const cached = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )

      // 缓存正在运行 → SSE 正在实时更新，不能拉服务端（checkpoint 可能还没写入本轮）
      if (cached?.status === "running") {
        return cached
      }

      const serverThread = await agentsApi.getThread(threadId)
      const key = agentThreadKeys.detail(threadId)
      const cachedAfterCheck = queryClient.getQueryData<AgentThread>(key)
      if (!cachedAfterCheck) return normalizeThreadHistory(serverThread)

      if (cachedAfterCheck.status === "running") {
        return cachedAfterCheck
      }

      return normalizeThreadHistory(serverThread)
    },
    refetchInterval: options?.refetchInterval,
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
    mutationFn: async (body: {
      prompt: string
      images?: Array<ImageChunk>
      repo?: string | null
      repo_explicitly_none?: boolean
      model_id?: string | null
      effort?: string | null
      backend_type?: string | null
      project_name?: string | null
    }) => {
      const response = await agentsApi.createThread(body)
      if (!response.body) return null

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let capturedThreadId: string | null = null
      let enteredThread = false

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
              const data =
                parsed.data &&
                typeof parsed.data === "object" &&
                !Array.isArray(parsed.data)
                  ? (parsed.data as Record<string, unknown>)
                  : {}
              const candidate = data.thread_id || data.id
              if (
                !capturedThreadId &&
                typeof candidate === "string" &&
                candidate
              ) {
                capturedThreadId = candidate
                addPendingPrompt(capturedThreadId, body.prompt, 0, body.images)
                const now = Date.now()
                const repo = body.repo ?? ""
                queryClient.setQueryData<AgentThread>(
                  agentThreadKeys.detail(capturedThreadId),
                  {
                    id: capturedThreadId,
                    title: body.prompt || "新任务",
                    repo,
                    repoFullName: repo,
                    repoUrl: repo,
                    platform: "",
                    platformDisplay: "",
                    branch: "",
                    model: body.model_id ?? "",
                    effort: body.effort ?? null,
                    status: "running",
                    backendType: body.backend_type ?? "local",
                    projectName: body.project_name ?? null,
                    createdAt: now,
                    updatedAt: now,
                    messages: [],
                    runEvents: [],
                  }
                )
                if (typeof window !== "undefined") {
                  try {
                    window.sessionStorage.setItem(
                      `xihe:new-thread-transition:${capturedThreadId}`,
                      "1"
                    )
                  } catch {
                    // The transition still works on the temporary screen when storage is unavailable.
                  }
                }
                enteredThread = true
                navigate({
                  to: "/agents/$threadId",
                  params: { threadId: capturedThreadId },
                })
              }
              if (capturedThreadId)
                handleStreamPart(capturedThreadId, parsed, queryClient)
            }
          } catch {
            // Ignore malformed individual events and continue consuming the stream.
          }
          boundary = buffer.indexOf("\n\n")
        }
      }

      if (capturedThreadId) {
        // Preserve the stream we just consumed: checkpoint history can lag
        // behind it, especially when the graph pauses for command approval.
        const completedThread = normalizeThreadHistory(
          queryClient.getQueryData<AgentThread>(
            agentThreadKeys.detail(capturedThreadId)
          ) ?? await agentsApi.getThread(capturedThreadId)
        )
        await agentsApi.saveViewSnapshot(
          capturedThreadId,
          completedThread.messages
        )
        queryClient.setQueryData(
          agentThreadKeys.detail(capturedThreadId),
          completedThread
        )
        if (!enteredThread) {
          navigate({
            to: "/agents/$threadId",
            params: { threadId: capturedThreadId },
          })
        }
      }
      return capturedThreadId
    },
    onSuccess: (threadId) => {
      if (!threadId) return
      invalidateThreadList(queryClient)
    },
  })
}

export interface SendAgentMessageVariables {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
  backend_type?: string | null
  project_name?: string | null
  edit_message_id?: string | null
  edit_message_content?: string | null
  edit_snapshot?: Array<Message>
  rollback_confirmed?: boolean
}

export function useSendAgentMessage(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      // 取消正在排队的 query，防止 polling 在 SSE 流开始之前拉取旧数据
      await queryClient.cancelQueries({
        queryKey: agentThreadKeys.detail(threadId),
      })

      // 立即把状态标记为 running，这样后续 polling query 看到 running 就会跳过服务端拉取
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(threadId),
        (prev) => {
          if (!prev) return prev
          return { ...prev, status: "running" }
        }
      )

      const cached = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )
      const response = await agentsApi.sendMessage(threadId, {
        content: vars.content,
        images: vars.images,
        repo: cached?.repoUrl || cached?.repo || null,
        model_id: vars.model_id,
        effort: vars.effort,
        backend_type: vars.backend_type,
        project_name: vars.project_name ?? cached?.projectName ?? null,
        edit_message_id: vars.edit_message_id,
        edit_message_content: vars.edit_message_content,
        edit_snapshot: vars.edit_snapshot,
        rollback_confirmed: vars.rollback_confirmed,
      })
      await processStreamResponse(response, queryClient)
      const completedThread = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )
      if (completedThread) {
        await agentsApi.saveViewSnapshot(threadId, completedThread.messages)
      }
      return completedThread
    },
    onSuccess: (thread) => {
      if (!thread) return
      queryClient.setQueryData(
        agentThreadKeys.detail(threadId),
        (prev: typeof thread | undefined) => {
          if (!prev) return thread
          return {
            ...thread,
            messages: thread.messages,
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

export function useDecideCommandApproval(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    onMutate: async () => {
      const key = agentThreadKeys.detail(threadId)
      const previousThread = queryClient.getQueryData<AgentThread>(key)
      queryClient.setQueryData<AgentThread>(key, (thread) =>
        thread
          ? { ...thread, status: "running", pendingApproval: null }
          : thread
      )
      await queryClient.cancelQueries({ queryKey: key })
      return { previousThread }
    },
    mutationFn: async ({
      approvalId,
      decision,
    }: {
      approvalId: string
      decision: "approve" | "reject"
    }) => {
      const response = await agentsApi.decideApproval(
        threadId,
        approvalId,
        decision
      )
      await processStreamResponse(response, queryClient)
      const refreshed = normalizeThreadHistory(
        queryClient.getQueryData<AgentThread>(agentThreadKeys.detail(threadId))
          ?? await agentsApi.getThread(threadId)
      )
      await agentsApi.saveViewSnapshot(threadId, refreshed.messages)
      queryClient.setQueryData(agentThreadKeys.detail(threadId), refreshed)
      return refreshed
    },
    onError: (_error, _variables, context) => {
      if (context?.previousThread) {
        queryClient.setQueryData(
          agentThreadKeys.detail(threadId),
          context.previousThread
        )
      }
    },
    onSettled: () => invalidateThreadList(queryClient),
  })
}

export function useExplainCommandApproval(threadId: string) {
  return useMutation({
    mutationFn: (approvalId: string) =>
      agentsApi.explainApproval(threadId, approvalId),
  })
}

export function useDeleteAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.deleteThread(threadId),
    onMutate: async (threadId) => {
      deletedThreadIds.add(threadId)
      await queryClient.cancelQueries({
        queryKey: agentThreadKeys.all,
        exact: true,
      })
      const previousThreads = queryClient.getQueryData<Array<AgentThread>>(
        agentThreadKeys.all
      )
      queryClient.setQueryData<Array<AgentThread>>(
        agentThreadKeys.all,
        (threads) => threads?.filter((thread) => thread.id !== threadId)
      )
      return { previousThreads }
    },
    onError: (_error, threadId, context) => {
      deletedThreadIds.delete(threadId)
      if (context?.previousThreads) {
        queryClient.setQueryData(
          agentThreadKeys.all,
          context.previousThreads
        )
      }
    },
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

export function useUpdateAgentThreadTitle(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (title: string) => agentsApi.updateThreadTitle(threadId, title),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      invalidateThreadList(queryClient)
    },
  })
}

export function useRenameProject() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { oldName: string; newName: string }) =>
      agentsApi.renameProject(vars.oldName, vars.newName),
    onSuccess: () => {
      invalidateThreadList(queryClient)
    },
  })
}
