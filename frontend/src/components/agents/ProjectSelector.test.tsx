import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ProjectSelector } from "./ProjectSelector"

const apiMocks = vi.hoisted(() => ({
  listProjects: vi.fn(),
  renameProject: vi.fn(),
  pickFolder: vi.fn(),
  bindProjectFolder: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: apiMocks,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderSelector(
  selectedProject: string | null,
  onProjectChange = vi.fn()
) {
  apiMocks.listProjects.mockResolvedValue({
    projects: [{ name: "Demo", path: "", source: "created" }],
    created_projects: [{ name: "Demo", path: "", source: "created" }],
    workspace_projects: [],
    projects_dir: "D:/workspace/projects",
  })
  apiMocks.renameProject.mockResolvedValue({ renamed: 1, name: "Renamed" })

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <ProjectSelector
        selectedProject={selectedProject}
        onProjectChange={onProjectChange}
      />
    </QueryClientProvider>
  )
  return onProjectChange
}

describe("ProjectSelector 当前项目编辑", () => {
  it("再次点击已选择项目时打开详细信息并可保存新名称", async () => {
    const onProjectChange = renderSelector("Demo")

    fireEvent.click(screen.getByRole("button", { name: /Demo/ }))
    const selectedItem = await screen.findByTitle("再次点击编辑项目详细信息")
    fireEvent.click(selectedItem)

    expect(screen.getByRole("dialog", { name: "项目详细信息" })).toBeTruthy()
    const nameInput = screen.getByLabelText("项目名称")
    fireEvent.change(nameInput, { target: { value: "Renamed" } })
    fireEvent.click(screen.getByRole("button", { name: "保存" }))

    await waitFor(() => {
      expect(apiMocks.renameProject).toHaveBeenCalledWith("Demo", "Renamed")
      expect(onProjectChange).toHaveBeenCalledWith("Renamed", null)
    })
  })

  it("首次点击未选择项目时只执行选择", async () => {
    const onProjectChange = renderSelector(null)

    fireEvent.click(screen.getByRole("button", { name: "项目" }))
    fireEvent.click(await screen.findByRole("button", { name: "Demo" }))

    expect(onProjectChange).toHaveBeenCalledWith("Demo", null)
    expect(screen.queryByRole("dialog", { name: "项目详细信息" })).toBeNull()
  })
})
