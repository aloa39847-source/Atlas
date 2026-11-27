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
    let riskSimulationCalls = 0
    let futuresOrderbookBacktests = 0
    let testnetPermissionCalls = 0
    let testnetReconcileCalls = 0
    let emergencyResetCalls = 0
    let masterStarts = 0
    let mockEmergencyStop = false
    let mockTarget = 'demo'
    let mockMarket = 'spot'
    let mockBotRunState = 'stopped'
    let testnetPermissionVerified = false

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
      else if (path === '/api/system/state') {
        const update = route.request().postDataJSON() || {}
        mockTarget = update.account_target || mockTarget
        mockMarket = update.market_type || mockMarket
        body = { ok: true }
      }
      else if (path === '/api/system/status') body = {
        state: { market_type: mockMarket, environment_mode: mockTarget === 'live' ? 'live' : 'demo', account_target: mockTarget, bot_run_state: mockBotRunState, emergency_stop: mockEmergencyStop, trading_paused: false, live_confirmed_by_user: false },
        engines: [{ key: 'market', name: 'Market Engine', status: 'ready' }, { key: 'risk', name: 'Risk Manager', status: 'implemented_partial' }],
        integrations: { ai_providers: { status: 'ready' }, binance: { server_live_enabled: false } },
      }
      else if (path === '/api/binance/testnet/status') body = ({
        provider: 'binance_testnet', environment: 'testnet', market: mockMarket, configured: true,
        connected: true, account_can_trade: true, execution_ready: testnetPermissionVerified,
        api_key_trade_permission_verified: testnetPermissionVerified,
        execution_readiness_check: testnetPermissionVerified ? 'ok' : 'api_key_trade_permission_requires_test_order_validation',
      })
      else if (path === '/api/binance/live/status') body = { connected: false, execution_ready: false, live_blocked: true }
      else if (path === '/api/binance/testnet/creds') body = { configured: true, market: mockMarket, environment: 'testnet' }
      else if (path === '/api/binance/live/creds') body = { configured: false, market: mockMarket, environment: 'live' }
      else if (path === '/api/binance/execution/preflight') body = ({
        ok: testnetPermissionVerified, ready: mockTarget === 'testnet' && testnetPermissionVerified,
        account_target: mockTarget, market: mockMarket, orders_submitted: false,
        status: { connected: true, execution_ready: testnetPermissionVerified },
      })
      else if (path === '/api/binance/testnet/validate-trade-permission') {
        testnetPermissionCalls += 1
        testnetPermissionVerified = true
        body = { ok: true, verified: true, order_submitted: false, matching_engine_reached: false, market: mockMarket, symbol: 'BTCUSDT' }
      }
      else if (path === '/api/binance/testnet/reconcile') {
        testnetReconcileCalls += 1
        mockEmergencyStop = true
        body = {
          ok: true, market: mockMarket, orders_submitted: false,
          order_sync: { ok: true, orders_checked: 2, orders_status_updated: 2, orders_with_fills: 1, positions_updated: 1, fees_pending: 0, orders_resolved: 1 },
          open_order_reconciliation: { ok: true, local_open_orders_count: 0, remote_open_orders_count: 0 },
          manual_review_required: false, emergency_stop_active: true,
          note: 'Read-only reconciliation; emergency stop remains latched.',
        }
      }
      else if (path === '/api/master/emergency-reset') {
        emergencyResetCalls += 1
        mockEmergencyStop = false
        body = { ok: true, state: { account_target: mockTarget, market_type: mockMarket, bot_run_state: 'stopped', emergency_stop: false }, note: 'Checks passed; worker remains stopped.' }
      }
      else if (path === '/api/master/start') {
        masterStarts += 1
        mockBotRunState = 'running'
        body = { ok: true, state: { account_target: mockTarget, market_type: mockMarket, bot_run_state: mockBotRunState } }
      }
      else if (path === '/api/master/stop') {
        mockBotRunState = 'stopped'
        body = { ok: true, state: { account_target: mockTarget, market_type: mockMarket, bot_run_state: mockBotRunState } }
      }
      else if (path === '/api/decisions') body = { items: [{ id: 1, action: 'HOLD', symbol: 'BTCUSDT', created_at: new Date().toISOString() }] }
      else if (path === '/api/portfolio') body = { snapshot: { equity_estimate: 10000 }, positions: [] }
      else if (path === '/api/ai/providers') body = { items: [{ id: 1, name: 'OpenAI test provider', provider: 'openai', model: 'test-model', enabled: true }] }
      else if (path === '/api/chat/reports') body = { generated_at: new Date().toISOString(), items: [] }
      else if (path === '/api/chat') body = { reply: 'بيانات الاختبار وصلت إلى TITAN.', grounded: true }
      else if (path === '/api/shadow/order') { shadowOrders += 1; body = { ok: true, order_id: 'shadow-test-1' } }
      else if (path === '/api/ai/providers/1/test') { providerTests += 1; body = { ok: true } }
      else if (path === '/api/ai/providers/1') body = { ok: true }
      else if (path === '/api/backtest/risk-simulation') { riskSimulationCalls += 1; body = { final_equity: 1015, net_pnl: 15, total_return: 0.015, max_drawdown_quote: 25, max_drawdown_pct: 0.025, total_trades: 4, fees_paid: 0, slippage_cost: 0, daily_limit_blocks: 0, results: [] } }
      else if (path === '/api/backtest/orderflow/futures-orderbook') { futuresOrderbookBacktests += 1; body = { data_source: 'test-fixture', depth_snapshots: 12, generated_signals: 2, total_trades: 1, win_rate: 1, total_return: 0.01, final_equity: 1010, fees_paid: 0.4, funding_net: 0 } }
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

    const navLabels = ['الرئيسية', 'التداول', 'الماسح الذكي', 'بنية السوق', 'المراجحة', 'التحليل الأساسي', 'الأجندة الاقتصادية', 'التحليل الذكي', 'الاستراتيجيات', 'المخاطر', 'المحفظة', 'التقارير', 'الإعدادات']
    for (const label of navLabels) {
      await page.locator('.tt-nav').getByRole('button', { name: new RegExp(label) }).click()
      await page.waitForTimeout(40)
    }

    const featureChecks = [
      ['بنية السوق', /تحليل السيولة وتدفق الصفقات/],
      ['المراجحة', /المراجحة المثلثية/],
      ['التحليل الأساسي', /التحليل الأساسي والاقتصاديات الرمزية/],
      ['الأجندة الاقتصادية', /التقويم الاقتصادي الأمريكي/],
    ]
    for (const [label, heading] of featureChecks) {
      await page.locator('.tt-nav').getByRole('button', { name: new RegExp(label) }).click()
      await page.getByText(heading).waitFor()
    }

    await page.locator('.tt-nav').getByRole('button', { name: /المخاطر/ }).click()
    await page.getByRole('heading', { name: /محاكي إدارة المخاطر/ }).waitFor()
    const riskPanel = page.locator('.card').filter({ hasText: 'محاكي إدارة المخاطر' }).last()
    await riskPanel.locator('select').first().selectOption('pnl_sequence')
    await riskPanel.getByRole('button', { name: 'تشغيل محاكاة المخاطر' }).click()
    await page.getByText('1015', { exact: true }).waitFor()
    assert.equal(riskSimulationCalls, 1, 'risk simulation panel did not call its backend API')

    await page.locator('.tt-nav').getByRole('button', { name: /الاستراتيجيات/ }).click()
    await page.getByRole('heading', { name: /دفتر أوامر Futures التاريخي/ }).waitFor()
    await page.getByRole('button', { name: 'اختبار Order Flow من دفتر الأوامر' }).click()
    await page.locator('.notice').filter({ hasText: 'مصدر البيانات: test-fixture' }).waitFor()
    assert.equal(futuresOrderbookBacktests, 1, 'Futures orderbook history panel did not call its backend API')

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
    await page.locator('.tt-feature-grid select').first().selectOption('testnet')
    await page.getByRole('button', { name: 'حفظ اختيار الحساب والسوق' }).click()
    await page.getByText(/لم يبدأ العامل تلقائياً/).waitFor()
    assert.equal(masterStarts, 0, 'saving the Testnet account selection must not start the worker')
    assert.equal(testnetPermissionCalls, 0, 'TRADE scope must be explicitly verified by the user')
    await page.getByRole('button', { name: 'تحقق من صلاحية API TRADE دون إرسال أمر' }).click()
    await page.getByText(/تم التحقق · صلاحية مؤقتة 5 دقائق/).waitFor()
    assert.equal(testnetPermissionCalls, 1, 'dashboard permission button did not call the non-executing order/test validation')
    assert.equal(masterStarts, 0, 'permission validation must never start the worker')
    await page.getByRole('button', { name: 'بدء تداول Binance Testnet' }).click()
    await page.waitForFunction(() => document.body.innerText.includes('تم تشغيل تداول Binance Testnet التجريبي.'))
    assert.equal(masterStarts, 1, 'Testnet worker must start only after the explicit Master Control action')
    await page.getByRole('button', { name: 'إيقاف المحرك بالكامل' }).click()
    await page.waitForFunction(() => document.body.innerText.includes('تم تنفيذ طلب الإيقاف.'))
    await page.getByRole('button', { name: 'مصالحة حالة أوامر Testnet (قراءة فقط)' }).click()
    await page.getByText(/اكتملت المصالحة للقراءة فقط: فُحص 2 أمر/).waitFor()
    assert.equal(testnetReconcileCalls, 1, 'manual Testnet reconciliation button did not call its API')
    assert.equal(masterStarts, 1, 'manual reconciliation must never start the trading worker')
    assert.equal(mockEmergencyStop, true, 'reconciliation must not clear Emergency Stop')
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: 'إعادة ضبط الإيقاف الطارئ بعد التحقق' }).click()
    await page.getByText(/اجتازت إعادة ضبط الإيقاف الطارئ فحوص الأمان/).waitFor()
    assert.equal(emergencyResetCalls, 1, 'explicit reset control did not call the verified reset API')
    assert.equal(mockEmergencyStop, false, 'successful verified reset should clear the emergency flag')
    assert.equal(masterStarts, 1, 'emergency reset must never start the trading worker')
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
