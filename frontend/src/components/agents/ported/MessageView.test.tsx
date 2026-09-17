import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MessageView } from "./MessageView";
import { groupConversationMessages } from "@/lib/agents/messageGroups";
import { normalizeThreadHistory, handleStreamPart } from "@/lib/agents/useThreadStream";
import { QueryClient } from "@tanstack/react-query";
import type { AgentThread, Chunk, Message, RunEvent } from "@/lib/agents/types";

vi.mock("@pierre/diffs/react", () => ({ MultiFileDiff: () => null }));
vi.mock("@/lib/agents/queries", () => ({
  agentThreadKeys: { all: ["threads"], detail: (id: string) => ["threads", id] },
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  window.requestAnimationFrame = () => 1;
  window.cancelAnimationFrame = vi.fn();
});
afterEach(cleanup);

const text = (value: string): Chunk => ({ kind: "text", text: value });
const tool = (id: string): Chunk => ({ kind: "tool-execution", toolCallId: id, title: `工具 ${id}`, toolKind: "other", status: "completed" });
const plan: Chunk = { kind: "todo", todos: [
  { content: "核实仓库", status: "completed" },
  { content: "等待用户澄清", status: "pending" },
] };
const message = (id: string, chunks: Chunk[], executionNarration = false): Message => ({
  id, author: "agent", timestamp: "2026-09-13T11:12:00Z", chunks, executionNarration,
});
const user: Message = { ...message("user", [text("fa")]), author: "user" };
const messages = [user, message("plan", [text("任务计划"), plan]),
  message("phase-1", [text("先核实仓库"), tool("a"), tool("b")], true),
  message("phase-2", [text("仓库路径不匹配"), tool("c")], true),
  message("answer", [text("请确认目标仓库。")])];

describe("task execution and answer boundaries", () => {
  it("keeps narrations and unfinished TODO inside one completed execution group, leaving only the answer outside", () => {
    render(<MessageView messages={messages} isStreaming={false} />);
    expect(screen.queryByText("先核实仓库")).toBeNull();
    expect(screen.queryByText("等待用户澄清")).toBeNull();
    expect(screen.getByText("请确认目标仓库。")).toBeTruthy();
    const toggle = screen.getByRole("button", { name: /任务完成/ });
    fireEvent.click(toggle);
    const details = screen.getByRole("region", { name: "任务执行详情" });
    expect(within(details).getByText("先核实仓库")).toBeTruthy();
    expect(within(details).getByText("仓库路径不匹配")).toBeTruthy();
    expect(within(details).getByText("1 / 2 项任务已完成")).toBeTruthy();
    expect(screen.getAllByText("等待用户澄清")).toHaveLength(1);
    expect(within(details).queryByText("请确认目标仓库。")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.queryByText("等待用户澄清")).toBeNull();
  });

  it("keeps TODO below execution while the final answer streams, then moves it inside and auto-collapses", () => {
    const { rerender } = render(<MessageView messages={messages} isStreaming />);
    const details = screen.getByRole("region", { name: "任务执行详情" });
    expect(within(details).queryByText("等待用户澄清")).toBeNull();
    expect(screen.getByText("等待用户澄清")).toBeTruthy();
    rerender(<MessageView messages={messages} isStreaming={false} />);
    expect(screen.queryByRole("region", { name: "任务执行详情" })).toBeNull();
    expect(screen.queryByText("等待用户澄清")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /任务完成/ }));
    expect(within(screen.getByRole("region")).getByText("1 / 2 项任务已完成")).toBeTruthy();
  });

  it("merges adjacent tool messages and their preceding narration, without crossing answer or user boundaries", () => {
    const rows = groupConversationMessages([
      user, message("pre", [text("即将调用工具")]), message("tools", [tool("a"), tool("b")]),
      message("answer-1", [text("第一条正文")]), message("answer-2", [text("第二条正文")]),
      { ...user, id: "user-2" }, message("next", [tool("c")]),
    ]);
    expect(rows.map((row) => row.executionNarration)).toEqual([false, true, false, false, false, true]);
    expect(rows[1]?.chunks).toHaveLength(3);
  });

  it("preserves a plan even when there are no tool records or no final answer", () => {
    render(<MessageView messages={[user, message("plan", [plan])]} isStreaming={false} />);
    expect(screen.queryByText("等待用户澄清")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /任务已停止/ }));
    expect(screen.getByText("等待用户澄清")).toBeTruthy();
  });

  it("shows a completed task bar for a model reply without tool calls while keeping the reply outside", () => {
    render(<MessageView messages={[user, message("answer", [text("模型正文")])]} isStreaming={false} />);
    const toggle = screen.getByRole("button", { name: /任务完成 · 耗时/ });
    expect(screen.getByText("模型正文")).toBeTruthy();
    fireEvent.click(toggle);
    const details = screen.getByRole("region", { name: "任务执行详情" });
    expect(within(details).queryByText("模型正文")).toBeNull();
    expect(screen.getByText("模型正文")).toBeTruthy();
  });

  it("shows waiting for confirmation for a paused run even when all recorded tools completed", () => {
    render(<MessageView messages={messages} isStreaming={false} runStatus="interrupted" />);
    expect(screen.getByRole("button", { name: "等待确认后继续" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /任务已停止|任务完成/ })).toBeNull();
  });

  it("uses the actual failure state instead of guessing completion from successful tools", () => {
    render(<MessageView messages={messages} isStreaming={false} runStatus="error" />);
    expect(screen.getByRole("button", { name: "任务失败" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /任务完成/ })).toBeNull();
  });

  it("shows a loading ring around the latest-position button while output is streaming", () => {
    const { container } = render(<MessageView messages={messages} isStreaming />);
    const scroller = container.querySelector(".h-full.overflow-y-auto") as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1200 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    fireEvent.scroll(scroller);
    const latestButton = screen.getByRole("button", { name: "滚动到底部" });
    expect(latestButton.querySelector(".agents-scroll-loader")).toBeTruthy();
  });

  it("keeps the user's scroll position while new streaming content is appended", () => {
    const { container, rerender } = render(<MessageView messages={messages} isStreaming />);
    const scroller = container.querySelector(".h-full.overflow-y-auto") as HTMLDivElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 1200 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 600 },
    });

    rerender(<MessageView messages={messages} isStreaming={false} />);
    scroller.scrollTop = 250;
    fireEvent.scroll(scroller);
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1400 });

    rerender(<MessageView messages={[...messages, message("streaming", [text("新增输出")])]} isStreaming />);
    expect(scroller.scrollTop).toBe(250);
  });

  it("keeps version navigation attached to the model reply instead of the latest user message", () => {
    const navigation = {
      position: 2,
      total: 2,
      onPrevious: vi.fn(),
      onNext: vi.fn(),
    };
    const { rerender, unmount } = render(
      <MessageView threadId="thread-anchor" messages={[user, message("answer-a", [text("第一段回复")])]} isStreaming versionNavigation={navigation} />,
    );
    const userActions = screen.getByRole("button", { name: "复制消息" }).parentElement!;
    expect(within(userActions).queryByText("2/2")).toBeNull();
    const replyActions = screen.getByRole("button", { name: "复制智能体正文" }).parentElement!;
    expect(within(replyActions).getByText("2/2")).toBeTruthy();

    rerender(
      <MessageView
        threadId="thread-anchor"
        messages={[user, message("answer-a", [text("第一段回复")]), message("answer-b", [text("新增回复")])]}
        isStreaming
        versionNavigation={navigation}
      />,
    );
    expect(within(userActions).queryByText("2/2")).toBeNull();
    expect(within(replyActions).getByText("2/2")).toBeTruthy();
    const latestAgentActions = screen.getAllByRole("button", { name: "复制智能体正文" }).at(-1)!.parentElement!;
    expect(within(latestAgentActions).queryByText("2/2")).toBeNull();

    unmount();
    render(
      <MessageView
        threadId="thread-anchor"
        messages={[user, message("answer-a", [text("第一段回复")]), message("answer-b", [text("新增回复")])]}
        isStreaming={false}
        versionNavigation={navigation}
      />,
    );
    expect(screen.getAllByText("2/2")).toHaveLength(1);
  });

  it("shows the directory and file name for read operations", () => {
    const readTool: Chunk = {
      kind: "tool-execution",
      toolCallId: "read-main",
      title: "读取文件",
      toolKind: "read",
      status: "completed",
      input: { path: "C:\\workspace\\repo\\src\\main.ts" },
    };
    render(
      <MessageView
        messages={[user, message("read", [readTool], true)]}
        isStreaming={false}
        project={{ id: "repo", name: "repo", path: "C:\\workspace\\repo", createdAt: 0, lastOpenedAt: 0 }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /任务完成/ }));
    expect(screen.getByText("读取文件(src/main.ts)")).toBeTruthy();
  });
});

const event = (id: string, detail: object): RunEvent => ({
  id, thread_id: "thread", kind: "other", title: "工具调用", status: "completed",
  detail: JSON.stringify(detail), created_at: "2026-09-13T11:12:00Z",
});
const history = (runEvents: RunEvent[]) => ({ id: "thread", status: "finished", messages, runEvents }) as AgentThread;

describe("history and stream completion", () => {
  const events = [event("run:stream:assistant:1", { text: "先核实仓库" }),
    event("run:tool:a", { tool: "read" }), event("run:tool:b", { tool: "search" }),
    event("run:stream:assistant:2", { text: "请确认目标仓库。" })];

  it("restores the same rendering from history without changing TODO status", () => {
    const restored = normalizeThreadHistory(history(events));
    const twice = normalizeThreadHistory(restored);
    expect(twice.messages).toEqual(restored.messages);
    render(<MessageView messages={twice.messages} isStreaming={false} />);
    expect(screen.queryByText("先核实仓库")).toBeNull();
    expect(screen.getByText("请确认目标仓库。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /任务完成/ }));
    expect(within(screen.getByRole("region")).getByText("1 / 2 项任务已完成")).toBeTruthy();
  });

  it("does not promote the last tool preamble to a final answer when the run ends on a tool", () => {
    const restored = normalizeThreadHistory(history(events.slice(0, 3)));
    const rows = groupConversationMessages(restored.messages);
    expect(rows.filter((row) => row.author === "agent" && !row.executionNarration)).toHaveLength(0);
  });

  it("leaves statuses untouched without assistant stream events", () => {
    expect(normalizeThreadHistory(history([])).messages).toEqual(messages);
  });

  it("restores tool-only activity for a paused run and keeps checkpoint answer text", () => {
    const restored = normalizeThreadHistory(history([
      event("run:tool:a", { tool: "read" }),
      event("run:tool:b", { tool: "search" }),
    ]));
    const tools = restored.messages.flatMap((message) => message.chunks)
      .filter((chunk) => chunk.kind === "tool-execution");
    expect(tools.map((chunk) => chunk.toolCallId)).toEqual(["run:tool:a", "run:tool:b"]);
    expect(restored.messages.some((message) => message.chunks.some(
      (chunk) => chunk.kind === "text" && chunk.text === "请确认目标仓库。",
    ))).toBe(true);
    expect(normalizeThreadHistory(restored).messages).toEqual(restored.messages);
  });

  it("keeps approval pending after thread_done and done, including its tool records", () => {
    const client = new QueryClient();
    const approval = { id: "approval", actions: [{ command: "test-only" }] };
    client.setQueryData(["threads", "thread"], { ...history([]), messages: [user], status: "running" });
    handleStreamPart("thread", { event: "approval_required", data: approval }, client);
    handleStreamPart("thread", { event: "thread_done", data: { status: "interrupted" } }, client);
    handleStreamPart("thread", { event: "done", data: {} }, client);
    const paused = client.getQueryData<AgentThread>(["threads", "thread"])!;
    expect(paused.status).toBe("interrupted");
    expect(paused.pendingApproval).toEqual(approval);
    client.clear();
  });

  it("preserves pending TODO on the real done event", () => {
    const client = new QueryClient();
    client.setQueryData(["threads", "thread"], { ...history(events), status: "running" });
    handleStreamPart("thread", { event: "done", data: {} }, client);
    const completed = client.getQueryData<AgentThread>(["threads", "thread"])!;
    expect(completed.status).toBe("finished");
    expect(completed.messages.flatMap((item) => item.chunks).find((chunk) => chunk.kind === "todo")).toEqual(plan);
    client.clear();
  });

  it("keeps tool groups in arrival order when a later group creates a new message", () => {
    const client = new QueryClient();
    client.setQueryData(["threads", "thread"], {
      ...history([]),
      status: "running",
      messages: [user],
    });
    for (const [id, group, path] of [
      ["first", "group-1", "src/first.ts"],
      ["second", "group-2", "src/second.ts"],
    ]) {
      handleStreamPart("thread", {
        event: "tool_update",
        data: {
          thread_id: "thread",
          run_id: "run-order",
          event_id: `thread:tool:${id}`,
          kind: "read",
          tool_kind: "read",
          tool_group_id: group,
          title: `读取 ${path}`,
          status: "completed",
          detail: JSON.stringify({ tool: "read", args: { file_path: path } }),
        },
      }, client);
    }
    const updated = client.getQueryData<AgentThread>(["threads", "thread"])!;
    const toolIds = updated.messages.flatMap((item) => item.chunks)
      .filter((chunk) => chunk.kind === "tool-execution")
      .map((chunk) => chunk.toolCallId);
    expect(toolIds).toEqual(["thread:tool:first", "thread:tool:second"]);
    expect(updated.messages.flatMap((item) => item.chunks)
      .filter((chunk) => chunk.kind === "tool-execution")
      .map((chunk) => chunk.input?.path)).toEqual(["src/first.ts", "src/second.ts"]);
    client.clear();
  });

  it("replaces the latest user turn when a refreshed message id differs", () => {
    const client = new QueryClient();
    const firstUser = { ...user, id: "first-user", chunks: [text("第一条问题")] };
    const editedUser = { ...user, id: "checkpoint-user", chunks: [text("需要重新编辑的消息")] };
    client.setQueryData(["threads", "thread"], {
      ...history([]),
      messages: [
        firstUser,
        message("first-answer", [text("第一条回复")]),
        editedUser,
        message("old-answer", [text("旧回复")]),
      ],
    });

    handleStreamPart("thread", {
      event: "rollback",
      data: {
        message_id: "thread-user-stale-live-id",
        message_content: "需要重新编辑的消息",
      },
    }, client);
    handleStreamPart("thread", {
      event: "user_message",
      data: {
        message_id: "thread-user-new-run",
        run_id: "new-run",
        content: "重新编辑后的消息",
      },
    }, client);

    const updated = client.getQueryData<AgentThread>(["threads", "thread"])!;
    expect(updated.messages.map((item) => item.id)).toEqual([
      "first-user",
      "first-answer",
      "thread-user-new-run",
    ]);
    expect(updated.messages.flatMap((item) => item.chunks)
      .filter((chunk) => chunk.kind === "text")
      .map((chunk) => chunk.text)).not.toContain("旧回复");
    client.clear();
  });

  it("keeps the first execution group after a second run replaces persisted run events", () => {
    const firstSnapshot = normalizeThreadHistory(history(events)).messages;
    const secondUser: Message = {
      id: "user-2",
      author: "user",
      timestamp: "2026-09-13T11:20:00Z",
      chunks: [text("再检查一次")],
    };
    const secondEvents = [
      event("run-2:stream:assistant:1", { text: "开始第二次检查" }),
      event("run-2:tool:a", { tool: "read" }),
      event("run-2:stream:assistant:2", { text: "第二次检查完成。" }),
    ];
    const refreshed = normalizeThreadHistory({
      ...history(secondEvents),
      messages: [...firstSnapshot, secondUser],
    });
    const rows = groupConversationMessages(refreshed.messages);

    expect(rows.filter((row) => row.executionNarration)).toHaveLength(2);
    render(<MessageView messages={refreshed.messages} isStreaming={false} />);
    expect(screen.getAllByRole("button", { name: /任务完成/ })).toHaveLength(2);
    expect(screen.getByText("请确认目标仓库。")).toBeTruthy();
    expect(screen.getByText("第二次检查完成。")).toBeTruthy();
  });
});
