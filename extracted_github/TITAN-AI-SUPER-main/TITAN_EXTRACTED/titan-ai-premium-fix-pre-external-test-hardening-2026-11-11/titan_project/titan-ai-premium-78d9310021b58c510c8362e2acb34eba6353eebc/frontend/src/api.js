const TOKEN_KEY = 'titan_token'
const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')

const apiUrl = (path) => `${API_BASE_URL}${path}`
// Keep bearer credentials for the current browser tab only. Remove any
// legacy persistent localStorage token so it cannot silently survive sign-out.
try { globalThis.localStorage?.removeItem(TOKEN_KEY) } catch {}
export const getToken = () => {
  try { return globalThis.sessionStorage?.getItem(TOKEN_KEY) || '' }
  catch { return '' }
}
export const setToken = (token) => {
  try {
    if (token) globalThis.sessionStorage?.setItem(TOKEN_KEY, token)
    else globalThis.sessionStorage?.removeItem(TOKEN_KEY)
  } catch {}
  try { globalThis.localStorage?.removeItem(TOKEN_KEY) } catch {}
}

async function req(path, options = {}) {
  const token = getToken()
  const { headers: extraHeaders = {}, ...requestOptions } = options
  const res = await fetch(apiUrl(path), {
    ...requestOptions,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...extraHeaders,
    },
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { raw: text } }
  if (res.status === 401) {
    setToken('')
  }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message || data.error)) || `HTTP ${res.status}`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  health: () => req('/api/health'),
  status: () => req('/api/system/status'),
  setState: (payload) => req('/api/system/state', { method: 'POST', body: JSON.stringify(payload) }),
  strategies: () => req('/api/strategies'),
  analyze: (payload) => req('/api/analysis/decision', { method: 'POST', body: JSON.stringify(payload) }),
  analyzeArbitrage: (payload) => req('/api/arbitrage/triangle', { method: 'POST', body: JSON.stringify(payload||{}) }),
  decisions: () => req('/api/decisions'),
  audit: () => req('/api/audit'),
  knowledge: () => req('/api/knowledge'),
  verifyKnowledge: (payload) => req('/api/knowledge/verify', { method: 'POST', body: JSON.stringify(payload||{}) }),
  addKnowledge: (payload) => req('/api/knowledge', { method: 'POST', body: JSON.stringify(payload) }),
  knowledgeConcepts: (q='') => req(`/api/knowledge/concepts${q?`?q=${encodeURIComponent(q)}`:''}`),
  learning: () => req('/api/learning'),
  strategyTradeOutcomes: (strategyKey) => req('/api/learning/strategies/' + encodeURIComponent(strategyKey) + '/outcomes'),
  strategyProposalCreate: (payload) => req('/api/learning/strategy-proposals', { method: 'POST', body: JSON.stringify(payload||{}) }),
  strategyProposalTest: (payload) => req('/api/learning/strategy-proposals/test', { method: 'POST', body: JSON.stringify(payload||{}) }),
  strategyProposalHistoricalWalkForward: (payload) => req('/api/learning/strategy-proposals/historical-walk-forward', { method: 'POST', body: JSON.stringify(payload||{}) }),
  strategyProposalApplyPaper: (payload) => req('/api/learning/strategy-proposals/apply-paper', { method: 'POST', body: JSON.stringify(payload||{}) }),
  addLearning: (payload) => req('/api/learning', { method: 'POST', body: JSON.stringify(payload) }),
  authStatus: () => req('/api/auth/status'),
  login: (payload) => req('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  setupTwoFactor: () => req('/api/auth/2fa/setup', { method: 'POST' }),
  confirmTwoFactor: (payload) => req('/api/auth/2fa/confirm', { method: 'POST', body: JSON.stringify(payload) }),
  disableTwoFactor: (payload) => req('/api/auth/2fa/disable', { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => req('/api/auth/logout', { method: 'POST' }),
  me: () => req('/api/auth/me'),
  demoCycle: (payload) => req('/api/demo/cycle', { method: 'POST', body: JSON.stringify(payload||{}) }),
  smartScanner: (params={}) => req(`/api/scanner/smart${Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''}`),
  futuresScanner: (params={}) => req(`/api/scanner/futures${Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''}`),
  smartScannerFutures: (params={}) => req(`/api/scanner/smart-futures${Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''}`),
  marketMicrostructure: (symbol, marketType='spot') => req(`/api/market/microstructure?symbol=${encodeURIComponent(symbol)}&market_type=${encodeURIComponent(marketType)}`),
  fundamentals: (coinId) => req(`/api/fundamentals?coin_id=${encodeURIComponent(coinId)}`),
  backtestStrategy: (payload) => req('/api/backtest/strategy', { method: 'POST', body: JSON.stringify(payload||{}) }),
  riskSimulation: (payload) => req('/api/backtest/risk-simulation', { method: 'POST', body: JSON.stringify(payload||{}) }),
  backtestFutures: (payload) => req('/api/backtest/futures', { method: 'POST', body: JSON.stringify(payload||{}) }),
  backtestPortfolio: (payload) => req('/api/backtest/portfolio', { method: 'POST', body: JSON.stringify(payload||{}) }),
  captureTriangleOrderbook: (payload) => req('/api/orderbook/capture/triangle', { method: 'POST', body: JSON.stringify(payload||{}) }),
  captureFuturesOrderbook: (payload) => req('/api/orderbook/capture/futures', { method: 'POST', body: JSON.stringify(payload||{}) }),
  backtestFuturesOrderbook: (payload) => req('/api/backtest/orderflow/futures-orderbook', { method: 'POST', body: JSON.stringify(payload||{}) }),
  backtestOrderbookTriangle: (payload) => req('/api/backtest/arbitrage/orderbook', { method: 'POST', body: JSON.stringify(payload||{}) }),
  backtestOrderflowBinance: (payload) => req('/api/backtest/orderflow/binance', { method: 'POST', body: JSON.stringify(payload||{}) }),
  fundamentalCoin: (coinId) => req('/api/fundamentals?coin_id=' + encodeURIComponent(coinId)),
  fundamentalProtocol: (slug) => req('/api/fundamentals/protocol?slug=' + encodeURIComponent(slug)),
  tokenUnlocks: (params={}) => req('/api/token-unlocks?' + new URLSearchParams(params).toString()),
  news: (keyword='', limit=50) => req(`/api/news?limit=${encodeURIComponent(limit)}${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ''}`),
  economicCalendar: (params={}) => req('/api/economic-calendar?' + new URLSearchParams(params).toString()),
  performance: (env='demo', market='spot') => req(`/api/performance?environment_mode=${encodeURIComponent(env)}&market_type=${encodeURIComponent(market)}`),
  predictions: (limit=100) => req(`/api/predictions?limit=${encodeURIComponent(limit)}`),
  evaluatePredictions: (payload) => req('/api/predictions/evaluate', { method: 'POST', body: JSON.stringify(payload||{}) }),
  mlTrain: (payload) => req('/api/ml/train', { method: 'POST', body: JSON.stringify(payload||{}) }),
  mlPredict: (payload) => req('/api/ml/predict', { method: 'POST', body: JSON.stringify(payload||{}) }),
  mlDrift: (payload) => req('/api/ml/drift', { method: 'POST', body: JSON.stringify(payload||{}) }),
  mlModels: () => req('/api/ml/models'),
  masterStart: (payload) => req('/api/master/start', { method: 'POST', body: JSON.stringify(payload||{}) }),
  masterStop: (payload) => req('/api/master/stop', { method: 'POST', body: JSON.stringify(payload||{}) }),
  emergencyStop: () => req('/api/master/emergency-stop', { method: 'POST' }),
  emergencyReset: () => req('/api/master/emergency-reset', { method: 'POST' }),
  chat: (payload) => req('/api/chat', { method: 'POST', body: JSON.stringify(payload) }),
  chatReports: (limit=25) => req('/api/chat/reports?limit=' + encodeURIComponent(limit)),
  tradingPause: (paused) => req('/api/master/trading-pause', { method: 'POST', body: JSON.stringify({paused}) }),
  aiProviders: () => req('/api/ai/providers'),
  telegramStatus: () => req('/api/telegram/status'),
  telegramPoll: (params={}) => req(`/api/telegram/poll${Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : ''}`),
  telegramSend: (payload) => req('/api/telegram/send', { method: 'POST', body: JSON.stringify(payload||{}) }),
  aiCreateProvider: (payload) => req('/api/ai/providers', { method: 'POST', body: JSON.stringify(payload) }),
  aiTestProvider: (id) => req(`/api/ai/providers/${id}/test`, { method: 'POST' }),
  aiProviderControl: (id, enabled) => req(`/api/ai/providers/${id}/control`, { method: 'POST', body: JSON.stringify({enabled}) }),
  aiResearch: () => req('/api/ai/research'),
  complianceCheck: (payload) => req('/api/compliance/check-order', { method: 'POST', body: JSON.stringify(payload) }),
  enginesDetailed: () => req('/api/engines/detailed'),
  binanceTestnetStatus: (market) => req(`/api/binance/testnet/status${market ? `?market=${market}` : ''}`),
  binanceTestnetValidateTradePermission: (market, symbol='BTCUSDT') => req('/api/binance/testnet/validate-trade-permission', { method: 'POST', body: JSON.stringify({ market, symbol }) }),
  binanceTestnetFuturesMaintenanceBrackets: (symbol) => req(`/api/binance/testnet/futures/maintenance-brackets?symbol=${encodeURIComponent(symbol)}`),
  binanceLiveStatus: (market) => req(`/api/binance/live/status${market ? `?market=${market}` : ''}`),
  binanceExecutionPreflight: () => req('/api/binance/execution/preflight'),
  binanceLiveCreds: (market) => req(`/api/binance/live/creds?market=${encodeURIComponent(market||'spot')}`),
  binanceLiveSave: (payload) => req('/api/binance/live/save', { method: 'POST', body: JSON.stringify(payload||{}) }),
  binanceLiveDelete: (market) => req(`/api/binance/live/delete?market=${encodeURIComponent(market)}`, { method: 'POST' }),
  binanceTestnetCredsStatus: (market) => req(`/api/binance/testnet/creds?market=${encodeURIComponent(market||'spot')}`),
  binanceTestnetCreds: () => req('/api/binance/testnet/creds'),
  binanceTestnetSave: (payload) => req('/api/binance/testnet/save', { method: 'POST', body: JSON.stringify(payload) }),
  binanceTestnetDelete: () => req('/api/binance/testnet/delete', { method: 'POST' }),
  binanceOrderCancel: (order_id) => req('/api/binance/orders/cancel', { method: 'POST', body: JSON.stringify({order_id}) }),
  binanceTestnetAccount: (market) => req(`/api/binance/testnet/account${market ? `?market=${market}` : ''}`),
  binanceTestnetOrders: (market) => req(`/api/binance/testnet/orders${market ? `?market=${market}` : ''}`),
  binanceTestnetReconcile: (market) => req(`/api/binance/testnet/reconcile${market ? `?market=${market}` : ''}`),
  portfolio: (env='demo', market='spot') => req(`/api/portfolio?environment_mode=${encodeURIComponent(env)}&market_type=${encodeURIComponent(market)}`),
  shadowOrder: (payload) => req('/api/shadow/order', { method: 'POST', body: JSON.stringify(payload) }),
  shadowClose: (payload) => req('/api/shadow/close', { method: 'POST', body: JSON.stringify(payload) }),
  shadowProtection: (payload) => req('/api/shadow/protection', { method: 'POST', body: JSON.stringify(payload) }),
  selfDiagnosis: () => req('/api/self-diagnosis'),
}
