import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"

import { SkillPanel } from "./SkillPanel"

/**
 * 后端 `writableScopes` 的模拟值。挂在模块作用域是为了让个别用例
 * （如「已绑本地文件夹」）能在渲染前改写它。
 */
let mockWritableScopes: Array<"global" | "project_set" | "local"> | null = null

afterEach(() => {
  cleanup()
  mockWritableScopes = null
})

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api")
  return {
    ...actual,
    api: {
      ...actual.api,
      skills: vi.fn(async (projectName?: string | null) => ({
        items: [
          {
            id: "code-review",
            name: "code-review",
            description: "代码审查",
            virtualPath: "/skills-global/code-review/SKILL.md",
            updatedAt: "2026-09-16T00:00:00+00:00",
            scope: "global" as const,
            scopeLabel: "全局",
            projectName: null,
            origin: "user" as const,
            extraFiles: [],
            sourcePath: "/tmp/code-review/SKILL.md",
          },
          ...(projectName
            ? [
                {
                  id: "repo-flow",
                  name: "repo-flow",
                  description: "本仓库专属流程",
                  virtualPath: "/skills-set/xihe/repo-flow/SKILL.md",
                  updatedAt: "2026-09-16T00:00:00+00:00",
                  scope: "project_set" as const,
                  scopeLabel: "项目",
                  projectName: "xihe",
                  origin: "user" as const,
                  extraFiles: [],
                  sourcePath: "/tmp/repo-flow/SKILL.md",
                },
              ]
            : []),
        ],
        scopes: [],
        // 未指定本地工作目录时后端只返回 global，面板据此只列一个工作域。
        writableScopes:
          mockWritableScopes ??
          (projectName
            ? (["global", "project_set"] as const)
            : (["global"] as const)),
        virtualRoot: "/skills",
        available: true,
      })),
    },
  }
})

function renderPanel(
  projectName: string | null,
  writableScopes?: Array<"global" | "project_set" | "local">,
  backendType?: string
) {
  if (writableScopes) mockWritableScopes = writableScopes
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SkillPanel
        open
        projectName={projectName}
        backendType={backendType}
        onOpenChange={vi.fn()}
      />
    </QueryClientProvider>
  )
}

describe("SkillPanel", () => {
  it("列出当前项目的技能，并标注作用域", async () => {
    renderPanel("xihe")
    expect(await screen.findByText("code-review")).toBeDefined()
    expect(screen.getByText("repo-flow")).toBeDefined()
    expect(screen.getByText("全局")).toBeDefined()
    expect(screen.getByText("项目 · xihe")).toBeDefined()
  })

  it("有项目时提示可上传到项目作用域", async () => {
    renderPanel("xihe")
    expect(
      await screen.findByText(/当前项目「xihe」可加载的技能/)
    ).toBeDefined()
  })

  it("未指定本地工作目录时说明只能保存到全局工作域", async () => {
    renderPanel(null)
    expect(
      await screen.findByText(/当前会话未指定本地工作目录，仅展示全局技能/)
    ).toBeDefined()
    // 工作域提示在上传对话框内，需先打开它
    fireEvent.click(screen.getByText("上传技能"))
    expect(
      await screen.findByText(/当前会话未指定本地工作目录，只能保存到全局工作域/)
    ).toBeDefined()
  })

  it("提供上传技能入口", async () => {
    renderPanel("xihe")
    expect(await screen.findByText("上传技能")).toBeDefined()
  })

  /**
   * 面板的两个下拉按同一个条件收缩：**会话是否只能看到全局技能**
   * （本组件里就是 `globalOnly`：没项目名，或项目跑在沙箱里）。完整矩阵：
   *
   * | 会话状态 | 顶部筛选 | 上传对话框工作域 |
   * |---|---|---|
   * | 没选项目 | 只有「全局」 | 只有「全局」 |
   * | 沙箱环境 | 只有「全局」 | 只有「全局」 |
   * | 选了项目、未绑本地文件夹 | 全局/项目/本地 | 全局/项目 |
   * | 选了项目、已绑本地文件夹 | 全局/项目/本地 | 全局/项目/本地 |
   *
   * 注意分界是「能不能看到项目/本地两层」，不是「绑没绑文件夹」：
   * 筛选是「看什么」，项目选定后本地技能本来就可能有（换个会话绑过文件夹就有），
   * 所以筛选一律给全三项；工作域是「能写到哪」，未绑文件夹时写不了本地。
   * 沙箱则两层都加载不到——沙箱容器内只有一个技能目录（对应全局层），
   * 后端 `_resolve_skill_sources` 在 sandbox 下也不反查项目。
   */
  it("没选项目：筛选下拉与上传工作域都只有「全局」", async () => {
    renderPanel(null)
    await screen.findByText("code-review")

    fireEvent.click(screen.getByText("全部"))
    expect(await screen.findByRole("option", { name: "全局" })).toBeDefined()
    expect(screen.queryByRole("option", { name: "项目" })).toBeNull()
    expect(screen.queryByRole("option", { name: "本地" })).toBeNull()

    fireEvent.click(screen.getByText("上传技能"))
    fireEvent.click(await screen.findByLabelText("工作域"))
    const options = await screen.findAllByRole("option")
    expect(options.length).toBe(1)
    expect(options[0]?.textContent).toContain("全局")
    expect(screen.queryByRole("option", { name: /^项目（/ })).toBeNull()
    expect(screen.queryByRole("option", { name: /^本地（/ })).toBeNull()
  })

  it("沙箱环境：即使选了项目也与没选项目一致，只给「全局」", async () => {
    // 项目名有值、后端也宣称可写三项，但沙箱下两层都加载不到
    renderPanel("xihe", ["global", "project_set", "local"], "sandbox")
    await screen.findByText("code-review")

    expect(
      await screen.findByText(/当前会话未指定本地工作目录，仅展示全局技能/)
    ).toBeDefined()

    fireEvent.click(screen.getByText("全部"))
    expect(await screen.findByRole("option", { name: "全局" })).toBeDefined()
    expect(screen.queryByRole("option", { name: "项目" })).toBeNull()
    expect(screen.queryByRole("option", { name: "本地" })).toBeNull()

    fireEvent.click(screen.getByText("上传技能"))
    fireEvent.click(await screen.findByLabelText("工作域"))
    const options = await screen.findAllByRole("option")
    expect(options.length).toBe(1)
    expect(options[0]?.textContent).toContain("全局")
  })

  it("选了项目但未绑本地文件夹：筛选三项，工作域只有「全局/项目」", async () => {
    renderPanel("xihe")
    await screen.findByText("code-review")

    // 顶部筛选：仍然给全三项（本地技能可能来自该项目的文件夹，只是本会话看不到）
    fireEvent.click(screen.getByText("全部"))
    expect(await screen.findByRole("option", { name: "全局" })).toBeDefined()
    expect(screen.getByRole("option", { name: "项目" })).toBeDefined()
    expect(screen.getByRole("option", { name: "本地" })).toBeDefined()

    // 上传对话框工作域：写不了本地，所以没有「本地（…）」
    fireEvent.click(screen.getByText("上传技能"))
    fireEvent.click(await screen.findByLabelText("工作域"))
    expect(
      await screen.findByRole("option", { name: /^全局（跨项目共享）/ })
    ).toBeDefined()
    expect(
      screen.getByRole("option", { name: /^项目（仅当前项目/ })
    ).toBeDefined()
    expect(screen.queryByRole("option", { name: /^本地（/ })).toBeNull()
  })

  it("选了项目且已绑本地文件夹：两个下拉都是三项", async () => {
    renderPanel("xihe", ["global", "project_set", "local"])
    await screen.findByText("code-review")

    fireEvent.click(screen.getByText("全部"))
    expect(await screen.findByRole("option", { name: "全局" })).toBeDefined()
    expect(screen.getByRole("option", { name: "项目" })).toBeDefined()
    expect(screen.getByRole("option", { name: "本地" })).toBeDefined()

    fireEvent.click(screen.getByText("上传技能"))
    fireEvent.click(await screen.findByLabelText("工作域"))
    expect(
      await screen.findByRole("option", { name: /^全局（跨项目共享）/ })
    ).toBeDefined()
    expect(
      screen.getByRole("option", { name: /^项目（仅当前项目/ })
    ).toBeDefined()
    expect(
      screen.getByRole("option", { name: /^本地（仅当前项目/ })
    ).toBeDefined()
  })

  /**
   * 面板与技能接口页共用同一套动画约定（agents.css 里的 skill-results /
   * skill-row），两处必须保持一致，否则同一份数据在两个界面上的呈现节奏会不同。
   */
  it("技能列表复用技能接口页的入场动画类", async () => {
    renderPanel("xihe")
    await screen.findByText("code-review")

    // 面板渲染在 Dialog.Portal 里，不在 render 返回的 container 中，需查 document
    const list = document.querySelector("ul.skill-results")
    expect(list).not.toBeNull()

    const rows = document.querySelectorAll("li.skill-row")
    expect(rows.length).toBe(2)
    // 逐行错峰，与 SkillRow 用同一套节奏（30ms 递增、150ms 封顶）
    expect((rows[0] as HTMLElement).style.animationDelay).toBe("0ms")
    expect((rows[1] as HTMLElement).style.animationDelay).toBe("30ms")
  })

  it("空态复用共用的 surface-state-enter 动画", async () => {
    renderPanel(null)
    await screen.findByText("code-review")
    // 搜索一个不存在的技能，触发空态
    fireEvent.change(screen.getByLabelText("搜索技能"), {
      target: { value: "不存在的技能" },
    })
    const empty = await screen.findByText("没有匹配的技能。")
    expect(empty.className).toContain("surface-state-enter")
  })
})
