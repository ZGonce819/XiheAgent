import { chromium } from "playwright-core"
import { mkdir } from "node:fs/promises"
import { resolve } from "node:path"

const baseUrl = process.argv[2] ?? "http://127.0.0.1:3000/agents"
const outputDir = resolve(process.argv[3] ?? "../.impeccable/review")
const executablePath =
  process.env.EDGE_PATH
  ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"

const reviewFixtures = {
  "/dashboard/api/me": {
    login: "reviewer",
    email: "reviewer@example.com",
    avatar_url: null,
    is_admin: true,
    slack_oauth_enabled: false,
  },
  "/dashboard/api/threads": [],
  "/dashboard/api/profile": {
    default_model: "deepseek-v4-pro",
    reasoning_effort: "medium",
    default_repo: "xihe/agent",
  },
  "/dashboard/api/repos": {
    installations: [],
    repositories: [{ full_name: "xihe/agent", name: "agent", default_branch: "main" }],
  },
  "/dashboard/api/options": {
    models: [{
      id: "deepseek-v4-pro",
      label: "DeepSeek V4 Pro",
      efforts: ["low", "medium", "high"],
      default_effort: "medium",
      supports_images: true,
    }],
    default_agent_model: "deepseek-v4-pro",
    default_agent_reasoning_effort: "medium",
    default_agent_subagent_model: "deepseek-v4-pro",
    default_agent_subagent_reasoning_effort: "medium",
  },
}

async function attachReviewFixtures(page) {
  await page.route("**/dashboard/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const payload = reviewFixtures[pathname]
    if (payload === undefined) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" })
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
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await attachReviewFixtures(desktop)
  await desktop.goto(baseUrl, { waitUntil: "networkidle" })
  await desktop.screenshot({ path: resolve(outputDir, "desktop.png"), fullPage: true })

  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  })
  await attachReviewFixtures(mobile)
  await mobile.goto(baseUrl, { waitUntil: "networkidle" })
  await mobile.screenshot({ path: resolve(outputDir, "mobile.png"), fullPage: true })
} finally {
  await browser.close()
}
