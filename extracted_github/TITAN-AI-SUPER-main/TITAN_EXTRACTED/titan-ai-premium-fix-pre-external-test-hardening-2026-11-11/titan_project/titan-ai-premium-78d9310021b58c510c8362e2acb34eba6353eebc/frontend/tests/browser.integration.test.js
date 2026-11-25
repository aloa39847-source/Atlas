import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const { port } = server.address()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  return port
}

test('Vite live terminal supports market streams, navigation, actions and Android viewport', async () => {
  const port = await freePort()
  const frontendDir = fileURLToPath(new URL('..', import.meta.url))
  const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
  const child = spawn(process.execPath, [viteBin, '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: frontendDir,
    stdio: 'ignore',
  })
  let browser
  const base = `http://127.0.0.1:${port}`
  try {
    let ready = false
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (child.exitCode !== null) throw new Error(`Vite dev server exited with code ${child.exitCode}`)
      try {
        const response = await fetch(base)
        if (response.ok && (await response.text()).includes('/src/main.jsx')) {
          ready = true
          break
        }
      } catch {}
      await delay(150)
    }
    assert.ok(ready, 'Vite development server did not become ready')

    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    let shadowOrders = 0
    let providerTests = 0
    let depthSnapshotLimit = null

    await page.addInitScript(() => {
      class FakeWebSocket {
        constructor(url) {
          this.url = url
          this.readyState = 0
          window.__lastWsUrl = url
          window.__mockSocket = this
          queueMicrotask(() => {
            this.readyState = 1
            this.onopen?.()
            const events = [
              { e: '24hrTicker', c: '68000.1', P: '1.25', h: '69000', l: '67000', q: '1234567' },
              { e: 'kline', k: { t: 1000, o: '67900', h: '68100', l: '67800', c: '68000', v: '12.5', x: false } },
              { lastUpdateId: 10, bids: [['67999', '1.2']], asks: [['68001', '1.4']] },
              { e: 'trade', t: 77, T: Date.now(), p: '68000.1', q: '0.01', m: false },
            ]
            for (const data of events) this.onmessage?.({ data: JSON.stringify({ data }) })
          })
        }
        close() {
          this.readyState = 3
          this.onclose?.()
        }
      }
      window.WebSocket = FakeWebSocket
    })

    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url())
      const path = url.pathname
      let body = {}
      if (path === '/api/auth/status') body = { auth_required: false }
      else if (path === '/api/auth/me') body = { authenticated: false }
      else if (path === '/api/system/status') body = {
        state: { market_type: 'spot', environment_mode: 'demo', account_target: 'demo', bot_run_state: 'idle', emergency_stop: false, trading_paused: false },
        engines: [{ key: 'market', name: 'Market Engine', status: 'ready' }, { key: 'risk', name: 'Risk Manager', status: 'implemented_partial' }],
        integrations: { ai_providers: { status: 'ready' } },
      }
      else if (path === '/api/decisions') body = { items: [{ id: 1, action: 'HOLD', symbol: 'BTCUSDT', created_at: new Date().toISOString() }] }
      else if (path === '/api/portfolio') body = { snapshot: { equity_estimate: 10000 }, positions: [] }
      else if (path === '/api/ai/providers') body = { items: [{ id: 1, name: 'OpenAI test provider', provider: 'openai', model: 'test-model', enabled: true }] }
      else if (path === '/api/chat/reports') body = { generated_at: new Date().toISOString(), items: [] }
      else if (path === '/api/chat') body = { reply: 'بيانات الاختبار وصلت إلى TITAN.', grounded: true }
      else if (path === '/api/shadow/order') { shadowOrders += 1; body = { ok: true, order_id: 'shadow-test-1' } }
      else if (path === '/api/ai/providers/1/test') { providerTests += 1; body = { ok: true } }
      else if (path === '/api/ai/providers/1') body = { ok: true }
      else body = { ok: true, items: [], configured: false, connected: false }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })

    await page.route('https://api.binance.com/**', async route => {
      const url = new URL(route.request().url())
      const now = Date.now()
      let body
      if (url.pathname.endsWith('/ticker/24hr')) body = {
        symbol: url.searchParams.get('symbol') || 'BTCUSDT', lastPrice: '68000.1',
        priceChangePercent: '1.25', highPrice: '69000', lowPrice: '67000', quoteVolume: '1234567',
      }
      else if (url.pathname.endsWith('/klines')) body = [
        [now-120000,'67900','68100','67800','68000','12.5',now-60000],
        [now-60000,'68000','68200','67900','68100','15.5',now-1000],
      ]
      else if (url.pathname.endsWith('/depth')) {
        depthSnapshotLimit = url.searchParams.get('limit')
        body = { lastUpdateId: 10, bids: [['67999','1.2']], asks: [['68001','1.4']] }
      }
      else if (url.pathname.endsWith('/trades')) body = [{ id: 77, time: now, price: '68000.1', qty: '0.01', isBuyerMaker: false }]
      else body = {}
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.route('https://fapi.binance.com/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }))

    await page.goto(base, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'TITAN' }).waitFor()
    await page.locator('svg[aria-label="Binance public candlestick and volume chart"]').waitFor()
    await page.getByText('BINANCE STREAM LIVE').waitFor()
    await page.evaluate(() => {
      const send = data => window.__mockSocket.onmessage({ data: JSON.stringify({ data }) })
      send({ e: '24hrTicker', c: '68123.45', P: '2.5', h: '69000', l: '67000', q: '1234567' })
      send({ lastUpdateId: 11, bids: [['68122', '2.1']], asks: [['68124', '2.3']] })
      send({ e: 'trade', t: 78, T: Date.now(), p: '68123.45', q: '0.02', m: false })
      send({ e: 'kline', k: { t: Date.now(), o: '68100', h: '68150', l: '68090', c: '68123.45', v: '20.5', x: false } })
    })
    await page.getByText('68,123.45').first().waitFor()
    assert.ok((await page.locator('.tt-book-row').count()) >= 2, 'live order book rows were not rendered')
    assert.match(await page.locator('.tt-orderbook').innerText(), /68,122\.00/)
    assert.match(await page.locator('.tt-orderbook').innerText(), /68,124\.00/)
    assert.match(await page.locator('.tt-trades').innerText(), /68,123\.45/)
    assert.match(await page.locator('.tt-market-meta').innerText(), /Binance WebSocket/)
    assert.ok((await page.locator('svg[aria-label*="candlestick"] rect').count()) > 0, 'real candle and volume bars were not rendered')
    assert.equal(depthSnapshotLimit, '10', 'REST order book must request a Binance-supported ten-level snapshot')

    const frames = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d','3d','1w','1M']
    for (const frame of frames) {
      await page.getByRole('button', { name: frame, exact: true }).click()
      await page.waitForFunction(value => window.__lastWsUrl?.includes('@kline_' + value), frame)
    }
    for (const pair of ['BTC/USDT','ETH/USDT','BNB/USDT','SOL/USDT','XRP/USDT','DOGE/USDT']) {
      await page.locator('.tt-nav').getByRole('button', { name: /الرئيسية/ }).click()
      await page.getByRole('button', { name: new RegExp(pair.replace('/', '\\/')) }).click()
      await page.waitForFunction(value => window.__lastWsUrl?.includes(value.toLowerCase() + '@ticker'), pair.replace('/',''))
    }

    const navLabels = ['الرئيسية', 'التداول', 'الماسح الذكي', 'التحليل الذكي', 'الاستراتيجيات', 'المخاطر', 'المحفظة', 'التقارير', 'الإعدادات']
    for (const label of navLabels) {
      await page.locator('.tt-nav').getByRole('button', { name: new RegExp(label) }).click()
      await page.waitForTimeout(40)
    }

    await page.locator('.tt-nav').getByRole('button', { name: /التداول/ }).click()
    await page.getByRole('button', { name: 'شراء', exact: true }).click()
    await page.getByRole('button', { name: 'بيع', exact: true }).click()
    await page.locator('.tt-order-form select').selectOption('limit')
    await page.locator('.tt-order-form input[type="number"]').last().fill('0.002')
    await page.locator('.tt-order-form input[type="number"]').first().fill('68000')
    await page.getByRole('button', { name: /إرسال أمر Shadow/ }).click()
    await page.waitForFunction(() => document.body.innerText.includes('تم تسجيل أمر Shadow'))
    assert.equal(shadowOrders, 1, 'Shadow submit control did not call its API')

    await page.locator('.tt-nav').getByRole('button', { name: /التحليل الذكي/ }).click()
    await page.getByRole('button', { name: 'اختبار', exact: true }).click()
    await page.waitForFunction(() => document.body.innerText.includes('اختبار المحرك نجح'))
    assert.equal(providerTests, 1, 'AI provider test action did not call its API')
    await page.getByPlaceholder(/اسأل TITAN/).fill('ما حالة السوق؟')
    await page.getByRole('button', { name: 'إرسال', exact: true }).click()
    await page.getByText('بيانات الاختبار وصلت إلى TITAN.').waitFor()

    await page.locator('.tt-nav').getByRole('button', { name: /الإعدادات/ }).click()
    await page.locator('.tt-feature-grid select').nth(1).selectOption('futures')
    await page.waitForFunction(() => window.__lastWsUrl?.startsWith('wss://fstream.binance.com/'))
    await page.locator('.tt-feature-grid select').nth(1).selectOption('spot')
    await page.waitForFunction(() => window.__lastWsUrl?.startsWith('wss://stream.binance.com:9443/'))

    await page.setViewportSize({ width: 390, height: 844 })
    await page.locator('.tt-nav').getByRole('button', { name: /الرئيسية/ }).click()
    const dimensions = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }))
    assert.ok(dimensions.document <= dimensions.viewport + 1, `Android viewport has horizontal overflow: ${JSON.stringify(dimensions)}`)
    assert.ok(dimensions.body <= dimensions.viewport + 1, `Android body has horizontal overflow: ${JSON.stringify(dimensions)}`)
  } finally {
    if (browser) await browser.close()
    child.kill('SIGTERM')
    await Promise.race([
      new Promise(resolve => child.once('exit', resolve)),
      delay(2000),
    ])
    if (child.exitCode === null) child.kill('SIGKILL')
  }
})
