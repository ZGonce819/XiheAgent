import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { AgentSkill } from "@/lib/api"

import { SkillRow } from "./SkillRow"

afterEach(cleanup)

function makeSkill(overrides: Partial<AgentSkill> = {}): AgentSkill {
  return {
    id: "demo-skill",
    name: "demo-skill",
    description: "演示技能",
    virtualPath: "/skills-global/demo-skill/SKILL.md",
    updatedAt: "2026-09-16T00:00:00+00:00",
    scope: "global",
    scopeLabel: "全局",
    projectName: null,
    origin: "user",
    extraFiles: [],
    sourcePath: "/tmp/demo-skill/SKILL.md",
    ...overrides,
  }
}

describe("SkillRow", () => {
  it("展示技能名与全局作用域，用户创建的技能不带来源徽章", () => {
    render(<SkillRow skill={makeSkill()} index={0} onDelete={vi.fn()} />)
    expect(screen.getByText("demo-skill")).toBeDefined()
    expect(screen.getByText("全局")).toBeDefined()
    expect(screen.queryByText("手动创建")).toBeNull()
  })

  it("项目技能带出项目名，且不显示来源徽章", () => {
    render(
      <SkillRow
        skill={makeSkill({
          scope: "project_set",
          scopeLabel: "项目",
          projectName: "xihe",
        })}
        index={0}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText("项目 · xihe")).toBeDefined()
    expect(screen.queryByText("手动创建")).toBeNull()
  })

  it("智能体创建的技能标注来源", () => {
    render(
      <SkillRow
        skill={makeSkill({ scope: "local", scopeLabel: "本地", origin: "agent" })}
        index={0}
        onDelete={vi.fn()}
      />
    )
    expect(screen.getByText("本地")).toBeDefined()
    expect(screen.getByText("智能体创建")).toBeDefined()
  })

  it("内置技能不打来源徽章", () => {
    render(
      <SkillRow
        skill={makeSkill({ origin: "builtin" })}
        index={0}
        onDelete={vi.fn()}
      />
    )
    expect(screen.queryByText("内置")).toBeNull()
  })

  it("展示附属文件数量，删除按钮带可访问名称", () => {
    const onDelete = vi.fn()
    render(
      <SkillRow
        skill={makeSkill({ extraFiles: ["helper.py", "refs/notes.md"] })}
        index={0}
        onDelete={onDelete}
      />
    )
    expect(screen.getByText("2 个附属文件")).toBeDefined()
    fireEvent.click(screen.getByLabelText("删除技能 demo-skill"))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })
})
