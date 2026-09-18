import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { AgentThread } from "@/lib/agents/types"
import { ProjectGroup } from "./AgentsSidebar"

// 项目子菜单涉及路由跳转与重命名 mutation，这里只验证收起/展开行为
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...rest }: { children: React.ReactNode }) => (
    <a {...rest}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}))

vi.mock("@/lib/agents/queries", () => ({
  useAgentThreads: () => ({ data: [] }),
  useDeleteAgentThread: () => ({ mutate: vi.fn() }),
  useRenameProject: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("@/lib/agents/threadReveal", () => ({
  subscribeThreadReveal: () => () => undefined,
  consumeThreadReveal: () => null,
  claimAnonymousThreadReveal: () => false,
  hasAnonymousThreadReveal: () => false,
}))

afterEach(cleanup)

function makeThread(id: string): AgentThread {
  return {
    id,
    title: `对话 ${id}`,
    repo: "owner/repo",
    repoFullName: "owner/repo",
    repoUrl: "owner/repo",
    platform: "",
    platformDisplay: "",
    branch: "",
    model: "",
    effort: null,
    status: "finished",
    backendType: "local",
    projectName: "demo",
    createdAt: 1,
    updatedAt: 1,
    messages: [],
    runEvents: [],
  }
}

const threads = [makeThread("a"), makeThread("b")]

describe("ProjectGroup 项目子菜单收起/展开", () => {
  it("默认展开，显示项目下的对话", () => {
    render(<ProjectGroup projectName="demo" threads={threads} />)

    expect(screen.getByText("对话 a")).toBeTruthy()
    expect(screen.getByText("对话 b")).toBeTruthy()
    // 默认展开时 aria-expanded 为 true
    expect(screen.getByRole("button", { name: "收起项目 demo" })).toBeTruthy()
  })

  it("初次挂载不播放入场动画（路由切换重新挂载时不应动）", () => {
    render(<ProjectGroup projectName="demo" threads={threads} />)

    const content = screen.getByText("对话 a").closest("div.sidebar-project-collapse")
    expect(content).toBeNull()
  })

  it("用户主动展开时才播放入场动画", () => {
    vi.useFakeTimers()
    try {
      render(<ProjectGroup projectName="demo" threads={threads} />)

      // 收起后内容卸载
      fireEvent.click(screen.getByRole("button", { name: "收起项目 demo" }))
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(screen.queryByText("对话 a")).toBeNull()

      // 用户点开：此时才带上入场动画类
      fireEvent.click(screen.getByRole("button", { name: "展开项目 demo" }))
      expect(
        screen.getByText("对话 a").closest("div.sidebar-project-collapse")
      ).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it("点击项目名称后收起，再次点击恢复展开", () => {
    vi.useFakeTimers()
    try {
      render(<ProjectGroup projectName="demo" threads={threads} />)

      fireEvent.click(screen.getByRole("button", { name: "收起项目 demo" }))

      // 收起瞬间进入退场过渡：内容仍挂载，避免视觉上突兀消失
      expect(screen.getByText("对话 a")).toBeTruthy()

      // 退场过渡结束后内容卸载，按钮语义切换为「展开项目」
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(screen.queryByText("对话 a")).toBeNull()

      fireEvent.click(screen.getByRole("button", { name: "展开项目 demo" }))
      expect(screen.getByText("对话 a")).toBeTruthy()
      expect(screen.getByText("对话 b")).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})
