import test from 'node:test'
import assert from 'node:assert/strict'
import { buildMarketStreamUrl, parseMarketStreamMessage } from '../src/marketStream.js'

test('spot and futures market streams subscribe to ticker, candles, depth and trades', () => {
  const spot = buildMarketStreamUrl('spot', 'BTCUSDT', '15m')
  const futures = buildMarketStreamUrl('futures', 'ETHUSDT', '1h')
  assert.match(spot, /^wss:\/\/stream\.binance\.com:9443\/stream\?streams=/)
  assert.match(futures, /^wss:\/\/fstream\.binance\.com\/stream\?streams=/)
  for (const stream of ['btcusdt@ticker', 'btcusdt@kline_15m', 'btcusdt@depth10@100ms', 'btcusdt@trade']) {
    assert.ok(spot.includes(stream))
  }
})

test('market stream URL rejects malformed symbols and unsupported intervals', () => {
  assert.throws(() => buildMarketStreamUrl('spot', 'BTC/USDT?x=1', '15m'), /invalid_binance_stream_symbol/)
  assert.throws(() => buildMarketStreamUrl('spot', 'BTCUSDT', '2m'), /invalid_binance_stream_interval/)
})

test('combined Binance messages are normalized into safe market events', () => {
  const ticker = parseMarketStreamMessage(JSON.stringify({ stream: 'btcusdt@ticker', data: {
    e: '24hrTicker', c: '68000.1', P: '1.25', h: '69000', l: '67000', q: '1234567',
  }}))
  assert.equal(ticker.type, 'ticker')
  assert.equal(ticker.lastPrice, '68000.1')

  const candle = parseMarketStreamMessage(JSON.stringify({ data: {
    e: 'kline', k: { t: 1000, o: '1', h: '3', l: '0.5', c: '2', v: '8', x: false },
  }}))
  assert.equal(candle.type, 'kline')
  assert.equal(candle.candle.closed, false)
  assert.equal(candle.candle.close, 2)

  const book = parseMarketStreamMessage(JSON.stringify({ data: {
    lastUpdateId: 10, bids: [['2', '4']], asks: [['3', '5']],
  }}))
  assert.equal(book.type, 'book')
  assert.equal(book.book.bids[0][0], '2')

  const trade = parseMarketStreamMessage(JSON.stringify({ data: {
    e: 'trade', t: 7, T: 2000, p: '2.5', q: '0.4', m: true,
  }}))
  assert.equal(trade.type, 'trade')
  assert.equal(trade.trade.isBuyerMaker, true)
})

test('malformed and unsupported stream messages fail closed', () => {
  assert.equal(parseMarketStreamMessage('{bad json'), null)
  assert.equal(parseMarketStreamMessage(JSON.stringify({ data: { e: 'kline', k: { t: 1, o: 'x' } } })), null)
  assert.equal(parseMarketStreamMessage(JSON.stringify({ data: { e: 'unknown' } })), null)
})
