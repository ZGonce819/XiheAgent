import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { CommandApprovalDialog } from "./CommandApprovalDialog"

afterEach(cleanup)

const baseProps = {
  open: true,
  command: "git push origin main",
  reason: "命令会修改远端仓库",
  onExplain: vi.fn(),
  onApprove: vi.fn(),
  onReject: vi.fn(),
}

describe("CommandApprovalDialog", () => {
  it("requests and displays a model explanation", () => {
    const onExplain = vi.fn()
    const { rerender } = render(
      <CommandApprovalDialog {...baseProps} onExplain={onExplain} />
    )

    fireEvent.click(screen.getByRole("button", { name: "解释" }))
    expect(onExplain).toHaveBeenCalledTimes(1)

    rerender(
      <CommandApprovalDialog
        {...baseProps}
        onExplain={onExplain}
        explaining
      />
    )
    expect(screen.getByText("正在生成解释...")).toBeTruthy()

    rerender(
      <CommandApprovalDialog
        {...baseProps}
        onExplain={onExplain}
        explanation="该命令会把本地提交推送到远端仓库。"
      />
    )
    expect(screen.getByText("该命令会把本地提交推送到远端仓库。")).toBeTruthy()
  })

  it("keeps the panel open and retries after an explanation failure", () => {
    const onExplain = vi.fn()
    const { rerender } = render(
      <CommandApprovalDialog {...baseProps} onExplain={onExplain} />
    )

    fireEvent.click(screen.getByRole("button", { name: "解释" }))
    rerender(
      <CommandApprovalDialog
        {...baseProps}
        onExplain={onExplain}
        explanationError="暂时无法生成解释，请稍后重试"
      />
    )

    expect(screen.getByRole("alert").textContent).toContain("暂时无法生成解释")
    fireEvent.click(screen.getByRole("button", { name: "重试解释" }))
    expect(onExplain).toHaveBeenCalledTimes(2)
    expect(screen.getByRole("heading", { name: "XiheAgent 想要执行一个操作" })).toBeTruthy()
  })
})
