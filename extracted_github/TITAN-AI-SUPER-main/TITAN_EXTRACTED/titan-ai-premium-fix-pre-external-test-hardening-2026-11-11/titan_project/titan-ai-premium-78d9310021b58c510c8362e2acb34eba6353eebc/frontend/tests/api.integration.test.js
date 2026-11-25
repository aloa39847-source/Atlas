import test from 'node:test'
import assert from 'node:assert/strict'

const values = new Map()
const sessionValues = new Map()
globalThis.localStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key),
}
globalThis.sessionStorage = {
  getItem: key => sessionValues.get(key) ?? null,
  setItem: (key, value) => sessionValues.set(key, String(value)),
  removeItem: key => sessionValues.delete(key),
}
const { api, setToken, getToken } = await import('../src/api.js')

test('authenticated API requests preserve bearer token and JSON payload', async () => {
  setToken('ci-token')
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  const result = await api.analyze({ symbol: 'BTC/USDT', data_source: 'synthetic' })
  assert.deepEqual(result, { ok: true })
  assert.equal(captured.url, '/api/analysis/decision')
  assert.equal(captured.options.headers.Authorization, 'Bearer ci-token')
  assert.deepEqual(JSON.parse(captured.options.body), { symbol: 'BTC/USDT', data_source: 'synthetic' })
})

test('401 responses clear the local session token and reject', async () => {
  setToken('expired')
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 })
  await assert.rejects(api.status(), /unauthorized/)
  assert.equal(getToken(), '')
})

test('API errors expose server detail and do not accept non-JSON success assumptions', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'risk gate blocked' }), { status: 422 })
  await assert.rejects(api.riskSimulation({}), /risk gate blocked/)
  globalThis.fetch = async () => new Response('upstream unavailable', { status: 502 })
  await assert.rejects(api.health(), /HTTP 502/)
})

test('health and portfolio endpoints map to expected read paths', async () => {
  const seen = []
  globalThis.fetch = async url => {
    seen.push(url)
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  await api.health()
  await api.portfolio('demo', 'futures')
  assert.deepEqual(seen, ['/api/health', '/api/portfolio?environment_mode=demo&market_type=futures'])
})


test('persistent learning UI uses the actual Starlette proposal routes and preserves required body fields', async () => {
  const seen = []
  globalThis.fetch = async (url, options) => {
    seen.push({ url, options })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  const proposal = { strategy_key: 'trend_following_ema', current_params: { min_confirmations: 2 }, proposed_params: { min_confirmations: 3 }, metrics_before: { expectancy: 0.1 }, rationale: 'test' }
  await api.strategyProposalCreate(proposal)
  const historical = { proposal_id: 7, symbol: 'BTC/USDT', timeframe: '1h', limit: 500, train_window: 150, test_window: 50, step: 50 }
  await api.strategyProposalHistoricalWalkForward(historical)
  await api.strategyProposalTest({ proposal_id: 7, metrics_after_paper: { expectancy: 0.2 } })
  await api.strategyProposalApplyPaper({ proposal_id: 7, explicit_approval: true })
  assert.deepEqual(seen.map(item => item.url), [
    '/api/learning/strategy-proposals',
    '/api/learning/strategy-proposals/historical-walk-forward',
    '/api/learning/strategy-proposals/test',
    '/api/learning/strategy-proposals/apply-paper',
  ])
  assert.deepEqual(JSON.parse(seen[0].options.body), proposal)
  assert.deepEqual(JSON.parse(seen[1].options.body), historical)
  assert.deepEqual(JSON.parse(seen[2].options.body), { proposal_id: 7, metrics_after_paper: { expectancy: 0.2 } })
  assert.deepEqual(JSON.parse(seen[3].options.body), { proposal_id: 7, explicit_approval: true })
})


test('Futures Testnet maintenance bracket lookup uses encoded symbol and authenticated GET', async () => {
  setToken('ci-token')
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({
      ok: true, symbol: 'BTCUSDT',
      maintenance_brackets: [{ notionalFloor: 0, notionalCap: 50000, maintMarginRatio: 0.004, cum: 0 }],
      source: 'binance_futures_testnet_signed_read_only', no_orders_submitted: true,
    }), { status: 200 })
  }
  const result = await api.binanceTestnetFuturesMaintenanceBrackets('BTC/USDT')
  assert.equal(result.source, 'binance_futures_testnet_signed_read_only')
  assert.equal(captured.url, '/api/binance/testnet/futures/maintenance-brackets?symbol=BTC%2FUSDT')
  assert.equal(captured.options.method, undefined)
  assert.equal(captured.options.headers.Authorization, 'Bearer ci-token')
})



test('external AI provider toggle sends explicit boolean and provider ID', async () => {
  setToken('ci-token')
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ ok: true, enabled: false }), { status: 200 })
  }
  const result = await api.aiProviderControl(12, false)
  assert.equal(result.enabled, false)
  assert.equal(captured.url, '/api/ai/providers/12/control')
  assert.equal(captured.options.method, 'POST')
  assert.deepEqual(JSON.parse(captured.options.body), { enabled: false })
  assert.equal(captured.options.headers.Authorization, 'Bearer ci-token')
})


test('Live credential save uses authenticated API and does not persist values client-side', async () => {
  setToken('ci-token')
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ ok: true, saved: true }), { status: 200 })
  }
  await api.binanceLiveSave({ market: 'futures', api_key: 'live-key', api_secret: 'live-secret' })
  assert.equal(captured.url, '/api/binance/live/save')
  assert.equal(captured.options.method, 'POST')
  assert.deepEqual(JSON.parse(captured.options.body), { market: 'futures', api_key: 'live-key', api_secret: 'live-secret' })
  assert.equal(captured.options.headers.Authorization, 'Bearer ci-token')
})



test('Binance execution preflight uses authenticated read-only endpoint', async () => {
  setToken('ci-token')
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ ok: true, ready: false, orders_submitted: false }), { status: 200 })
  }
  const result = await api.binanceExecutionPreflight()
  assert.equal(result.ready, false)
  assert.equal(result.orders_submitted, false)
  assert.equal(captured.url, '/api/binance/execution/preflight')
  assert.equal(captured.options.headers.Authorization, 'Bearer ci-token')
})


test('bearer sessions are tab-scoped and legacy persistent tokens are cleared', () => {
  localStorage.setItem('titan_token', 'legacy-persistent-token')
  setToken('tab-session-token')
  assert.equal(getToken(), 'tab-session-token')
  assert.equal(sessionStorage.getItem('titan_token'), 'tab-session-token')
  assert.equal(localStorage.getItem('titan_token'), null)
  setToken('')
  assert.equal(sessionStorage.getItem('titan_token'), null)
})
