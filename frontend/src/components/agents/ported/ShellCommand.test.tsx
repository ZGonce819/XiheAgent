import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ShellCommand } from "./ShellCommand";

describe("ShellCommand", () => {
  it("keeps a rejected command visible with a rejection badge", () => {
    render(
      <ShellCommand
        chunk={{
          kind: "tool-execution",
          toolCallId: "command-1",
          title: "终端",
          toolKind: "execute",
          status: "rejected",
          input: { command: "git commit -m test" },
        }}
      />,
    );

    expect(screen.getByText(/命令已拒绝：git commit -m test/)).toBeTruthy();
    expect(screen.getByText("已被用户拒绝执行")).toBeTruthy();
  });
});
