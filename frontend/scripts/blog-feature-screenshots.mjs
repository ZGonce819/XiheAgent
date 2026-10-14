import { chromium } from 'playwright-core'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const output = resolve('../blog/images')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({
  executablePath: process.env.EDGE_PATH ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  headless: true,
})
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1.5, serviceWorkers: 'block',
  })
  page.on('pageerror', error => console.log('Page error:', error.message))
  page.on('response', response => { if (response.status() >= 400) console.log('HTTP error:', response.status(), new URL(response.url()).pathname) })
  for (const name of ['automations', 'skills']) {
    await page.goto(`http://127.0.0.1:3000/${name}`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: name === 'automations' ? '新建任务' : '新建技能', exact: true }).last().waitFor({ timeout: 30000 })
    if (name === 'automations') await page.getByRole('button', { name: '立即跑一轮', exact: true }).first().waitFor()
    if (name === 'skills') await page.getByText('ai-coding-implementation', { exact: true }).waitFor()
    await page.evaluate(() => document.fonts.ready)
    const clip = name === 'automations' ? { x: 0, y: 0, width: 1440, height: 390 } : undefined
    await page.screenshot({ path: resolve(output, `${name}.png`), clip, animations: 'disabled' })
    await writeFile(resolve('../.artifacts', `blog-${name}-visible.txt`), await page.locator('body').innerText())
    console.log(`${name}: captured live page`)
  }
} finally {
  await browser.close()
}
