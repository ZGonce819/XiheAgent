import { chromium } from "playwright-core"
import { mkdir } from "node:fs/promises"
import { resolve } from "node:path"

const baseUrl = process.argv[2] ?? "http://127.0.0.1:3000/agents"
const outputDir = resolve(process.argv[3] ?? "../.impeccable/review")
const executablePath =
  process.env.EDGE_PATH ??
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"

const reviewFixtures = {
  "/api/v1/me": {
    login: "reviewer",
    email: "reviewer@example.com",
    avatar_url: null,
    is_admin: true,
    slack_oauth_enabled: false,
  },
  "/api/v1/threads": [],
  "/api/v1/schedules": [
    {
      id: "dependency-check",
      name: "每日依赖检查",
      prompt: "检查仓库依赖更新和已知安全问题，并整理需要处理的变更。",
      schedule: "0 9 * * *",
      repo: "xihe/agent",
      model: "deepseek-v4-pro",
      effort: "medium",
      enabled: true,
      lastThreadId: null,
      lastError: null,
    },
  ],
  "/api/v1/skills": {
    available: true,
    virtualRoot: "/skills",
    items: [
      {
        id: "code-review",
        name: "code-review",
        description: "检查代码变更中的正确性、回归风险和测试缺口。",
        virtualPath: "/skills/code-review/SKILL.md",
        updatedAt: "2026-09-15T08:00:00Z",
      },
      {
        id: "web-search",
        name: "web-search",
        description: "检索并整理任务所需的公开网页信息。",
        virtualPath: "/skills/web-search/SKILL.md",
        updatedAt: "2026-09-15T08:00:00Z",
      },
    ],
  },
  "/api/v1/profile": {
    default_model: "deepseek-v4-pro",
    reasoning_effort: "medium",
    default_repo: "xihe/agent",
  },
  "/api/v1/repos": {
    installations: [],
    repositories: [
      { full_name: "xihe/agent", name: "agent", default_branch: "main" },
    ],
  },
  "/api/v1/options": {
    models: [
      {
        id: "deepseek-v4-pro",
        label: "DeepSeek V4 Pro",
        efforts: ["low", "medium", "high"],
        default_effort: "medium",
        supports_images: true,
      },
    ],
    default_agent_model: "deepseek-v4-pro",
    default_agent_reasoning_effort: "medium",
    default_agent_subagent_model: "deepseek-v4-pro",
    default_agent_subagent_reasoning_effort: "medium",
  },
}

async function attachReviewFixtures(page) {
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const payload = reviewFixtures[pathname]
    if (payload === undefined) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: "{}",
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    })
  })
}

await mkdir(outputDir, { recursive: true })

const browser = await chromium.launch({ executablePath, headless: true })
try {
  const desktop = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  })
  await attachReviewFixtures(desktop)
  await desktop.goto(baseUrl, { waitUntil: "networkidle" })
  await desktop.screenshot({
    path: resolve(outputDir, "desktop.png"),
    fullPage: true,
  })
  await desktop.evaluate(() => localStorage.setItem("open-swe-theme", "dark"))
  await desktop.reload({ waitUntil: "networkidle" })
  await desktop.screenshot({
    path: resolve(outputDir, "desktop-dark.png"),
    fullPage: true,
  })
  if (new URL(baseUrl).pathname === "/automations") {
    await desktop.getByRole("button", { name: "新建任务" }).first().click()
    await desktop.screenshot({
      path: resolve(outputDir, "desktop-form.png"),
      fullPage: true,
    })
  }

  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  })
  await attachReviewFixtures(mobile)
  await mobile.goto(baseUrl, { waitUntil: "networkidle" })
  await mobile.screenshot({
    path: resolve(outputDir, "mobile.png"),
    fullPage: true,
  })
} finally {
  await browser.close()
}
