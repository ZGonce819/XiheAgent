import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";

const timestamp = "2026-09-13T11:12:00Z";
const text = (value) => ({ kind: "text", text: value });
const message = (id, chunks, executionNarration = false) => ({ id, author: "agent", timestamp, chunks, executionNarration });
const tool = (id) => ({ kind: "tool-execution", toolCallId: id, title: `核实仓库 ${id}`, toolKind: "other", status: "completed", elapsedMs: 1000 });
const thread = {
  id: "task-details-check", title: "任务执行折叠验证", status: "finished", repo: "owner/repo", createdAt: Date.now(), updatedAt: Date.now(),
  messages: [
    { ...message("user", [text("fa")]), author: "user" },
    message("plan", [{ kind: "todo", todos: [{ content: "核实仓库", status: "completed" }, { content: "等待用户澄清", status: "pending" }] }]),
    message("phase-1", [text("我先看一下仓库里有什么内容。"), tool("a"), tool("b")], true),
    message("phase-2", [text("仓库路径不匹配，需要确认目标。"), tool("c")], true),
    message("answer", [text("请确认你要操作的仓库。")]),
  ],
};
const fixtures = {
  "/api/v1/me": { login: "reviewer", email: "reviewer@example.com", is_admin: true },
  "/api/v1/threads": [thread],
  "/api/v1/threads/task-details-check": thread,
  "/api/v1/profile": {},
  "/api/v1/options": { models: [], default_agent_model: null },
};
const browser = await chromium.launch({ executablePath: process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", headless: true });
const output = process.argv[3] ?? "../.artifacts/task-details";
await mkdir(output, { recursive: true });
try {
  for (const [name, viewport] of [["desktop", { width: 1440, height: 1000 }], ["wide", { width: 2560, height: 1440 }], ["mobile", { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport, serviceWorkers: "block" });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/v1/**", (route) => {
      const payload = fixtures[new URL(route.request().url()).pathname];
      return route.fulfill({ status: payload ? 200 : 404, contentType: "application/json", body: JSON.stringify(payload ?? {}) });
    });
    await page.goto(`${process.argv[2] ?? "http://127.0.0.1:3000"}/agents/task-details-check`);
    await page.getByText("请确认你要操作的仓库。", { exact: true }).waitFor();
    const metrics = await page.evaluate(() => {
      const answer = [...document.querySelectorAll("p")].find((el) => el.textContent === "请确认你要操作的仓库。");
      const composer = document.querySelector("textarea")?.parentElement;
      const send = document.querySelector('button[aria-label="发送消息"]');
      return {
        rootFont: getComputedStyle(document.documentElement).fontSize,
        answerFont: getComputedStyle(answer).fontSize,
        answerLineHeight: getComputedStyle(answer).lineHeight,
        sidebarWidth: document.querySelector("aside")?.getBoundingClientRect().width ?? 0,
        composerWidth: composer?.getBoundingClientRect().width,
        composerBottom: composer?.getBoundingClientRect().bottom,
        sendSize: send?.getBoundingClientRect().width,
        pageWidth: document.documentElement.scrollWidth,
        pageHeight: document.documentElement.scrollHeight,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight,
      };
    });
    await writeFile(`${output}/${name}-metrics.json`, JSON.stringify(metrics, null, 2));
    console.log(`${name} sizes: ${JSON.stringify(metrics)}`);
    assert.equal(await page.locator("[data-task-execution]").count(), 1);
    assert.equal(await page.getByText("等待用户澄清").count(), 0);
    await page.screenshot({ path: `${output}/${name}-collapsed.png` });
    await page.getByRole("button", { name: /任务完成/ }).click();
    const region = page.getByRole("region", { name: "任务执行详情" });
    await region.getByText("1 / 2 项任务已完成", { exact: true }).waitFor();
    assert.equal(await region.getByText("我先看一下仓库里有什么内容。", { exact: true }).count(), 1);
    assert.equal(await page.getByText("等待用户澄清").count(), 1);
    assert.equal(await region.getByText("请确认你要操作的仓库。", { exact: true }).count(), 0);
    await page.screenshot({ path: `${output}/${name}-expanded.png` });
    await page.reload();
    await page.getByText("请确认你要操作的仓库。", { exact: true }).waitFor();
    assert.equal(await page.getByText("等待用户澄清").count(), 0);
    assert.deepEqual(errors, []);
    console.log(`${name}: collapsed, expanded, reload, TODO status and page errors passed`);
    await page.close();
  }
} finally { await browser.close(); }
