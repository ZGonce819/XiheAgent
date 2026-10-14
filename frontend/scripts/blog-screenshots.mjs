import { chromium } from 'playwright-core'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const output = resolve('../blog/images')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1060 }, deviceScaleFactor: 1.5, serviceWorkers: 'block' })
  const captures = [
    ['conversation', '0bc0276b-d17f-4f67-bcd5-1aa4062eaf48'],
    ['approval', '6f92237c-9dab-41e1-9a00-9746b39a4ff2'],
  ]
  for (const [name, id] of captures) {
    await page.goto(`http://127.0.0.1:3000/agents/${id}`, { waitUntil: 'networkidle' })
    await page.locator('textarea').waitFor()
    await page.evaluate(() => document.fonts.ready)
    if (name === 'conversation') {
      await page.getByRole('button', { name: /任务完成/ }).click()
      await page.getByText('当前项目中有哪些技能', { exact: true }).last().scrollIntoViewIfNeeded()
    }
    await page.screenshot({ path: resolve(output, `${name}.png`), animations: 'disabled' })
    if (name === 'approval') {
      await page.locator('.command-approval-panel').screenshot({ path: resolve(output, 'approval-detail.png'), animations: 'disabled' })
    }
    await writeFile(resolve('../.artifacts', `blog-${name}-visible.txt`), await page.locator('body').innerText())
    console.log(`${name}: captured live thread ${id}`)
  }
  // Preview the production DiffView with the actual before/after files.
  // This is explicitly labelled as a component preview, not a saved chat diff.
  const diffData = {
    filePath: 'greeting.py', isNewFile: false, isBinary: false,
    originalContent: await readFile(resolve('../.artifacts/blog-demo/greeting.py'), 'utf8'),
    newContent: await readFile(resolve('../.artifacts/blog-demo-repo/greeting.py'), 'utf8'),
  }
  await writeFile(resolve(output, 'code-diff-source.json'), JSON.stringify(diffData, null, 2))
  await page.evaluate(async data => {
    const reactModule = await import('/node_modules/.vite/deps/react.js')
    const React = reactModule.default ?? reactModule
    const clientModule = await import('/node_modules/.vite/deps/react-dom_client.js')
    const { createRoot } = clientModule.default ?? clientModule
    const { DiffView } = await import('/src/components/agents/ported/DiffView.tsx')
    const host = document.createElement('div')
    host.id = 'blog-diff-preview'
    host.className = 'agents-ui dark'
    Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: '99999', background: 'var(--ui-bg)', padding: '48px', overflow: 'auto' })
    document.body.append(host)
    createRoot(host).render(React.createElement('main', { style: { maxWidth: 1220, margin: '0 auto', padding: 28, background: 'var(--ui-bg)' } },
      React.createElement('p', { style: { fontSize: 15, color: 'var(--ui-text-muted)', marginBottom: 18 } }, 'XiheAgent · 代码差异组件预览'),
      React.createElement('h1', { style: { fontSize: 28, marginBottom: 12, fontWeight: 650 } }, '为问候函数补充空姓名处理'),
      React.createElement('p', { style: { fontSize: 16, color: 'var(--ui-text-muted)', marginBottom: 28 } }, '内容来自独立本地演示任务的实际文件修改；以下使用项目原有 DiffView 组件渲染。'),
      React.createElement(DiffView, { diffData: data }),
      React.createElement('p', { style: { fontSize: 14, color: 'var(--ui-text-muted)', marginTop: 22 } }, '演示任务停在验证命令审批处。此图展示已落盘的修改，不代表验证已通过。')
    ))
  }, diffData)
  await page.getByText('为问候函数补充空姓名处理', { exact: true }).waitFor()
  await page.evaluate(() => document.fonts.ready)
  await page.locator('#blog-diff-preview main').screenshot({ path: resolve(output, 'code-diff.png'), animations: 'disabled' })
  console.log('Actual file diff captured with production DiffView')
} finally { await browser.close() }
