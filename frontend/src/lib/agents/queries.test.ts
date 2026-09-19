import { createElement, type ReactNode } from "react"
import { QueryClient } from "@tanstack/react-query"
import { QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import {
  agentThreadKeys,
  cacheAcceptedAutomationThread,
  useAgentThread,
} from "./queries"
import type { AgentSchedule, AgentThread } from "./types"

const schedule: AgentSchedule = {
  id: "schedule-1",
  name: "每日分诊",
  prompt: "检查仓库里的待办事项并整理结果",
  schedule: "0 9 * * *",
  repo: "https://example.com/team/repo.git",
  model: "model-a",
  enabled: true,
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("cacheAcceptedAutomationThread", () => {
  it("在自动化任务受理后立即把会话放进侧边栏列表缓存", () => {
    const client = new QueryClient()
    client.setQueryData<Array<AgentThread>>(agentThreadKeys.all, [])

    cacheAcceptedAutomationThread(
      client,
      schedule,
      {
        automationId: schedule.id,
        accepted: true,
        threadId: "thread-1",
        roundNumber: 1,
      },
      1234
    )

    expect(
      client.getQueryData<Array<AgentThread>>(agentThreadKeys.all)
    ).toEqual([
      expect.objectContaining({
        id: "thread-1",
        source: "schedule",
        status: "running",
        title: schedule.prompt,
        createdAt: 1234,
        updatedAt: 1234,
      }),
    ])
    expect(
      client.getQueryData<AgentThread>(agentThreadKeys.detail("thread-1"))
    ).toBeUndefined()
  })

  it("同一回执重复处理时不会产生重复会话", () => {
    const client = new QueryClient()
    const receipt = {
      automationId: schedule.id,
      accepted: true,
      threadId: "thread-1",
      roundNumber: 1,
    }

    cacheAcceptedAutomationThread(client, schedule, receipt)
    cacheAcceptedAutomationThread(client, schedule, receipt)

    expect(
      client.getQueryData<Array<AgentThread>>(agentThreadKeys.all)
    ).toHaveLength(1)
  })
})

describe("useAgentThread", () => {
  it("自动审批暂停后继续轮询直到恢复并完成，无需手动刷新", async () => {
    vi.useFakeTimers()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    cacheAcceptedAutomationThread(client, schedule, {
      automationId: schedule.id, accepted: true, threadId: "approval-thread", roundNumber: 1,
    })
    const running = client.getQueryData<AgentThread[]>(agentThreadKeys.all)![0]!
    const interrupted: AgentThread = { ...running, status: "interrupted" }
    client.setQueryData(agentThreadKeys.detail(running.id), interrupted)
    const getThread = vi.spyOn(agentsApi, "getThread")
      .mockResolvedValueOnce(interrupted)
      .mockResolvedValueOnce(running)
      .mockResolvedValue({ ...running, status: "finished" })
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    const { result, unmount } = renderHook(
      () => useAgentThread(running.id, { refetchInterval: false }), { wrapper }
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    expect(result.current.data?.status).toBe("interrupted")
    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) })
    expect(result.current.data?.status).toBe("running")
    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) })
    expect(result.current.data?.status).toBe("finished")
    await act(async () => { await vi.advanceTimersByTimeAsync(3_100) })
    expect(getThread).toHaveBeenCalledTimes(3)
    unmount()
    client.clear()
  })

  it("会继续轮询运行中的自动化会话，而不是永久返回 running 缓存", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const running = {
      id: "thread-1",
      title: schedule.prompt,
      repo: schedule.repo ?? "",
      repoFullName: schedule.repo ?? "",
      repoUrl: schedule.repo ?? "",
      platform: "",
      platformDisplay: "",
      branch: "",
      model: schedule.model,
      source: "schedule",
      status: "running",
      createdAt: 1234,
      updatedAt: 1234,
      messages: [],
      runEvents: [],
    } satisfies AgentThread
    client.setQueryData(agentThreadKeys.detail(running.id), running)
    const getThread = vi.spyOn(agentsApi, "getThread").mockResolvedValue({
      ...running,
      status: "finished",
      updatedAt: 5678,
    })

    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children)
    const { result } = renderHook(() => useAgentThread(running.id), { wrapper })

    await waitFor(() => expect(result.current.data?.status).toBe("finished"))
    expect(getThread).toHaveBeenCalledWith(running.id)
  })
})
