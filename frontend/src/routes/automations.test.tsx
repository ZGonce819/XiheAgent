import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { AgentSchedule, AutomationRunReceipt } from "@/lib/agents/types"

/**
 * 自动化任务页与技能接口页共用同一套动画约定（``styles/agents.css``）。
 *
 * 这里钉的是「挂没挂上共用的那个类」，不是像素效果：类名一改，节奏就和项目其它
 * 页面分叉了，而没有视觉回归工具时只能靠这类断言发现。技能面板有同一套断言
 * （``components/skills/SkillPanel.test.tsx``）。
 *
 * 一行里挂着四个 mutation 与一个 query，页面还牵出会话与模型列表，所以全部打桩，
 * 只渲染这一行。
 */

const hooks = vi.hoisted(() => ({
  run: vi.fn(),
  resume: vi.fn(),
  reset: vi.fn(),
  budget: vi.fn(),
  state: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...rest }: { children: React.ReactNode }) => (
    <a {...rest}>{children}</a>
  ),
  Navigate: () => null,
  Outlet: () => null,
  createFileRoute: () => (options: unknown) => options,
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ location: { pathname: "/automations" } }),
  useParams: () => ({}),
  useSearch: () => ({}),
}))

vi.mock("@/lib/agents/queries", () => ({
  useRunAgentSchedule: () => ({ mutate: hooks.run, isPending: false }),
  useResumeAgentSchedule: () => ({ mutate: hooks.resume, isPending: false }),
  useResetAgentSchedule: () => ({ mutate: hooks.reset, isPending: false }),
  useUpdateAgentScheduleBudget: () => ({
    mutate: hooks.budget,
    isPending: false,
  }),
  useAgentScheduleState: () => hooks.state(),
  // 下面几个只服务于「渲染整页」的用例：列表给空，走空态 → 点新建 → 表单。
  useAgentSchedules: () => ({ isLoading: false, isError: false, data: [] }),
  useCreateAgentSchedule: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAgentSchedule: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAgentSchedule: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("@/lib/session", () => ({
  useSession: () => ({ isLoading: false, data: { name: "测试用户" } }),
}))

vi.mock("@/lib/agents/useModelOptions", () => ({
  useModelOptions: () => ({
    models: [
      { id: "model-a", label: "模型甲" },
      { id: "model-b", label: "模型乙" },
    ],
    defaultSelection: { modelId: "model-a" },
  }),
}))

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SettingsSection: ({ children }: { children: React.ReactNode }) => (
    <section>{children}</section>
  ),
}))

import { AutomationRow, Route, StateMessage } from "./automations"

// automations.tsx 没有默认导出（路由文件只导出 Route）。桩掉的 createFileRoute
// 让 Route 直接就是选项对象，组件挂在 component 上；真实现挂在 options.component。
const AutomationsPage =
  (Route as unknown as { options?: { component?: React.ComponentType } })
    .options?.component ??
  (Route as unknown as { component?: React.ComponentType }).component
if (!AutomationsPage) throw new Error("路由上取不到页面组件")

afterEach(cleanup)

beforeEach(() => {
  vi.clearAllMocks()
  // 状态已加载但还没跑过任何一轮：面板能展开，内容走空 data 那条分支。
  hooks.state.mockReturnValue({ isLoading: false, isError: false, data: null })
})

function makeSchedule(overrides: Partial<AgentSchedule> = {}): AgentSchedule {
  return {
    id: "auto-1",
    name: "每日依赖检查",
    prompt: "检查依赖有没有安全公告",
    schedule: "0 9 * * *",
    repo: "owner/repo",
    model: "gpt-5",
    enabled: true,
    lastRunId: null,
    lastTriggeredAt: null,
    lastError: null,
    ...overrides,
  }
}

function renderRow(index = 0) {
  return render(
    <AutomationRow
      schedule={makeSchedule()}
      index={index}
      disabled={false}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onToggle={vi.fn()}
    />
  )
}

describe("自动化任务页的动画约定", () => {
  it("空态复用技能接口页的 surface-state-enter", () => {
    const { container } = render(
      <StateMessage title="还没有自动化任务" description="创建一个任务。" />
    )
    expect(container.firstElementChild?.className).toContain(
      "surface-state-enter"
    )
  })

  it("列表行沿用 automation-row，并按 35ms 递增错峰入场", () => {
    const { container } = renderRow(2)
    const row = container.firstElementChild as HTMLElement
    expect(row.className).toContain("automation-row")
    expect(row.style.animationDelay).toBe("70ms")
  })

  it("错峰在 140ms 封顶，长列表不会越排越久", () => {
    const { container } = renderRow(17)
    const row = container.firstElementChild as HTMLElement
    expect(row.style.animationDelay).toBe("140ms")
  })

  it("循环状态面板用 surface-panel-expand 展开", () => {
    const { container } = renderRow()
    expect(container.querySelector(".surface-panel-expand")).toBeNull()

    fireEvent.click(screen.getByLabelText("循环状态"))
    expect(container.querySelector(".surface-panel-expand")).not.toBeNull()
  })

  it("跑一轮的回执用 surface-inline-note 出现", () => {
    hooks.run.mockImplementation(
      (
        _id: string,
        options?: { onSuccess?: (receipt: AutomationRunReceipt) => void }
      ) => {
        options?.onSuccess?.({
          automationId: "auto-1",
          accepted: true,
          threadId: "thread-1",
          roundNumber: 2,
        })
      }
    )
    renderRow()

    fireEvent.click(screen.getByRole("button", { name: /立即跑一轮/ }))

    const note = screen.getByText(/已开始第 2 轮/)
    expect(note.parentElement?.className).toContain("surface-inline-note")
  })

  it("触发失败时的错误行也走 surface-inline-note", () => {
    hooks.run.mockImplementation(
      (_id: string, options?: { onError?: (reason: Error) => void }) => {
        options?.onError?.(new Error("这个任务已经有一轮在跑"))
      }
    )
    renderRow()

    fireEvent.click(screen.getByRole("button", { name: /立即跑一轮/ }))

    expect(
      screen.getByText("这个任务已经有一轮在跑").className
    ).toContain("surface-inline-note")
  })
})

describe("表单里的下拉与项目统一", () => {
  it("执行频率与模型走共享 Select，页面不再出现原生 select", () => {
    const { container } = render(<AutomationsPage />)

    // 空列表下有两处「新建任务」（列表头与空态各一），点哪个都会打开同一个表单。
    const newButton = screen.getAllByRole("button", { name: "新建任务" })[0]
    if (!newButton) throw new Error("页面上没有「新建任务」按钮")
    fireEvent.click(newButton)

    // 原生 select 是那次不一致的根源：系统外观、系统箭头、系统菜单，
    // 三样都插不进项目约定（agents.css 里 [data-popup-open] 的箭头旋转它也吃不到）。
    expect(container.querySelector("select")).toBeNull()
    // 共享组件的触发器恰好两个：执行频率 + 模型。箭头与弹出动画都挂在 data-slot 上。
    expect(
      container.querySelectorAll('[data-slot="select-trigger"]')
    ).toHaveLength(2)
    // 触发器直接渲染当前选项的中文标签（执行频率的默认预设是「每天」）。
    expect(screen.getByText("每天")).toBeTruthy()
  })
})
