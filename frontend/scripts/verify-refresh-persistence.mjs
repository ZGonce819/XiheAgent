import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const timestamp = "2026-09-13T11:12:00Z";
const text = (value) => ({ kind: "text", text: value });
const tool = (id) => ({ kind: "tool-execution", toolCallId: id, title: `工具 ${id}`, toolKind: "read", status: "completed" });
const thread = {
  id: "refresh-check", title: "刷新持久化验证", status: "finished", repo: "owner/repo",
  createdAt: Date.now(), updatedAt: Date.now(), runEvents: [],
  messages: [
    { id: "user-1", author: "user", timestamp, chunks: [text("第一轮")] },
    { id: "activity-1", author: "agent", timestamp, executionNarration: true, chunks: [text("第一轮过程"), tool("first")] },
    { id: "answer-1", author: "agent", timestamp, chunks: [text("第一轮完成")] },
  ],
};
const secondEvents = [
  { id: "refresh-check:stream:run-2:assistant:1", thread_id: thread.id, kind: "other", title: "生成内容", status: "completed", detail: JSON.stringify({ text: "第二轮过程" }), created_at: timestamp },
  { id: "refresh-check:tool:second", thread_id: thread.id, kind: "read", title: "工具 second", status: "completed", detail: JSON.stringify({ tool: "read", path: "README.md" }), created_at: timestamp },
  { id: "refresh-check:stream:run-2:assistant:2", thread_id: thread.id, kind: "other", title: "生成内容", status: "completed", detail: JSON.stringify({ text: "第二轮完成" }), created_at: timestamp },
];
const sse = [
  ["thread_snapshot", { id: thread.id, thread_id: thread.id, status: "running" }],
  ["user_message", { message_id: `${thread.id}-user-run-2`, run_id: "run-2", content: "第二轮" }],
  ["message_start", { message_id: `${thread.id}-live-assistant-run-2-1`, run_id: "run-2" }],
  ["text_delta", { message_id: `${thread.id}-live-assistant-run-2-1`, run_id: "run-2", content: "第二轮过程", mode: "replace" }],
  ["tool_update", { thread_id: thread.id, run_id: "run-2", event_id: `${thread.id}:tool:second`, title: "工具 second", status: "completed", kind: "read", tool_kind: "read", detail: JSON.stringify({ tool: "read", path: "README.md" }) }],
  ["message_start", { message_id: `${thread.id}-live-assistant-run-2-2`, run_id: "run-2" }],
  ["text_delta", { message_id: `${thread.id}-live-assistant-run-2-2`, run_id: "run-2", content: "第二轮完成", mode: "replace" }],
  ["thread_done", { id: thread.id, thread_id: thread.id, status: "finished" }],
  ["done", {}],
].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");

const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, serviceWorkers: "block" });
  let savedMessages;
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/stream-message")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: sse });
    if (path.endsWith("/view-snapshot")) {
      savedMessages = JSON.parse(request.postData() ?? "{}").messages;
      return route.fulfill({ status: 204, body: "" });
    }
    if (path.endsWith("/threads/refresh-check")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...thread, messages: savedMessages ?? thread.messages, runEvents: savedMessages ? secondEvents : [] }) });
    if (path.endsWith("/me")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ login: "reviewer" }) });
    if (path.endsWith("/options")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ models: [] }) });
    if (path.endsWith("/threads")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([thread]) });
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.goto(`${process.argv[2] ?? "http://127.0.0.1:3000"}/agents/refresh-check`);
  await page.getByPlaceholder("继续输入指令").fill("第二轮");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByText("第二轮完成", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll("[data-task-execution]").length === 2);
  await page.waitForFunction(() => performance.getEntriesByType("resource").some((entry) => entry.name.includes("view-snapshot")));
  assert.equal(savedMessages.filter((message) => message.executionNarration).length, 2);
  await page.reload();
  await page.getByText("第二轮完成", { exact: true }).waitFor();
  assert.equal(await page.locator("[data-task-execution]").count(), 2);
  assert.deepEqual(errors, []);
  console.log("two completed runs remain grouped after snapshot save and full reload");
} finally {
  await browser.close();
}
