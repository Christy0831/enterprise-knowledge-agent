import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

async function offline(page, corpus = 'single') {
  await page.goto('/')
  await expect(page.getByText('本地服务已连接')).toBeVisible()
  await page.selectOption('#provider', 'mock')
  await page.selectOption('#retrieval', 'bm25')
  await page.selectOption('#corpus', corpus)
}
async function ask(page, text) {
  await page.getByLabel('输入你的问题', { exact: true }).fill(text)
  await page.getByRole('button', { name: '发送问题' }).click()
}

test('desktop welcome, normal answer, original citation and record download', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 960 })
  await offline(page)
  await mkdir('../examples/ui', { recursive: true })
  await page.screenshot({ path: '../examples/ui/desktop.png', fullPage: true })
  await ask(page, '报销期限多久？')
  await expect(page.getByText('已有依据', { exact: true })).toBeVisible()
  await expect(page.locator('.answer-text')).toContainText('30')
  await page.locator('.citation-strip button').first().click()
  await expect(page.locator('.source-detail blockquote')).toContainText('报销期限为 30 天')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载运行记录' }).click()
  expect((await download).suggestedFilename()).toContain('knowledge-')
  await page.screenshot({ path: '../examples/ui/desktop-answer.png', fullPage: true })
  expect(errors).toEqual([])
})

test('conflicts remain unresolved and do not become a confident answer', async ({ page }) => {
  await offline(page, 'conflict')
  await ask(page, '报销期限多久？')
  await expect(page.getByText('待你确认', { exact: true })).toBeVisible()
  await expect(page.locator('.answer-text')).toContainText('30')
  await expect(page.locator('.answer-text')).toContainText('15')
  await expect(page.locator('.gaps')).toBeVisible()
  await expect(page.locator('.citation-strip button')).toHaveCount(2)
})

test('follow-up carries conversation history to the API', async ({ page }) => {
  await offline(page)
  await ask(page, '报销期限多久？')
  await expect(page.getByText('已有依据', { exact: true })).toBeVisible()
  await ask(page, '那需要准备哪些材料？')
  await expect(page.locator('.answer-text').last()).toContainText('电子发票')
  await expect(page.locator('.answer-text').last()).toContainText('审批单')
})

test('mobile layout has no horizontal overflow and citations remain accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByText('本地服务已连接')).toBeAttached()
  await page.screenshot({ path: '../examples/ui/mobile.png', fullPage: true })
  await page.getByRole('button', { name: '打开设置' }).click()
  await page.selectOption('#provider', 'mock'); await page.selectOption('#retrieval', 'bm25'); await page.selectOption('#corpus', 'single')
  await page.getByRole('button', { name: '关闭设置', exact: true }).click()
  await ask(page, '报销期限多久？')
  await expect(page.locator('.answer-text')).toContainText('30')
  await page.locator('.citation-strip button').first().click()
  await expect(page.locator('.source-detail blockquote')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test.describe('controlled HTTP chunk fixtures (not model-quality evidence)', () => {
  test.use({ baseURL: 'http://127.0.0.1:8766' })

  test('unvalidated text appears before final response, then validated citations replace it', async ({ page, request }) => {
    await page.goto('/')
    await expect(page.getByText('本地服务已连接')).toBeVisible()
    await ask(page, '验证流式输出')
    await expect(page.locator('.answer-text.draft')).toContainText('报销期限为')
    await expect(page.getByText('生成中 · 待核验')).toBeVisible()
    await expect(page.locator('.citation-strip')).toHaveCount(0)
    await request.post('http://127.0.0.1:8766/test/release')
    await expect(page.getByText('已有依据', { exact: true })).toBeVisible()
    await expect(page.locator('.answer-text')).toContainText('30天')
    await expect(page.locator('.citation-strip')).toBeVisible()
  })

  test('stop clears unvalidated text and does not label it completed', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('本地服务已连接')).toBeVisible()
    await ask(page, '验证停止')
    await expect(page.locator('.answer-text.draft')).toBeVisible()
    await page.getByRole('button', { name: '停止回答' }).click()
    await expect(page.getByText('已停止', { exact: true })).toBeVisible()
    await expect(page.locator('.answer-text')).toContainText('任务已取消')
    await expect(page.locator('.answer-text')).not.toContainText('报销期限为')
    await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible()
  })

  test('rejected draft is replaced by the corrected validated answer', async ({ page, request }) => {
    await page.goto('/')
    await expect(page.getByText('本地服务已连接')).toBeVisible()
    await ask(page, '验证修正')
    await expect(page.locator('.answer-text.draft')).toContainText('99天')
    await request.post('http://127.0.0.1:8766/test/release')
    await expect(page.getByText('已有依据', { exact: true })).toBeVisible()
    await expect(page.locator('.answer-text')).toContainText('30天')
    await expect(page.locator('.answer-text')).not.toContainText('99天')
  })

  test('early disconnect does not turn a partial draft into a completed answer', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('本地服务已连接')).toBeVisible()
    await ask(page, '验证断开')
    await expect(page.getByText('请求失败', { exact: true })).toBeVisible()
    await expect(page.locator('.answer-text')).toContainText('尚未完成校验')
    await expect(page.locator('.citation-strip')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible()
  })
})
