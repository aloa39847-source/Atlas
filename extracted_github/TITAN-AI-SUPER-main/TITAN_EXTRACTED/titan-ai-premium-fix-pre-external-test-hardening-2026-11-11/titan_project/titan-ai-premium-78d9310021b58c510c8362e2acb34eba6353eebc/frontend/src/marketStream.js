const FRAME_PATTERN = /^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|3d|1w|1M)$/;

export function buildMarketStreamUrl(market, symbol, frame) {
  const rawSymbol = String(symbol || '').trim();
  const pair = rawSymbol.toLowerCase();
  const interval = String(frame || '');
  if (!/^[a-z0-9]{5,20}$/i.test(rawSymbol)) throw new Error('invalid_binance_stream_symbol');
  if (!FRAME_PATTERN.test(interval)) throw new Error('invalid_binance_stream_interval');
  const streams = [
    pair + '@ticker',
    pair + '@kline_' + interval,
    pair + '@depth10@100ms',
    pair + '@trade',
  ].join('/');
  const host = market === 'futures'
    ? 'wss://fstream.binance.com/stream?streams='
    : 'wss://stream.binance.com:9443/stream?streams=';
  return host + streams;
}

export function parseMarketStreamMessage(raw) {
  let envelope;
  try {
    envelope = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
  const data = envelope?.data || envelope;
  if (!data || typeof data !== 'object') return null;

  if (data.e === '24hrTicker') {
    return {
      type: 'ticker',
      lastPrice: data.c,
      priceChangePercent: data.P,
      highPrice: data.h,
      lowPrice: data.l,
      quoteVolume: data.q,
    };
  }

  if (data.e === 'kline' && data.k) {
    const k = data.k;
    const candle = {
      time: Number(k.t),
      open: Number(k.o),
      high: Number(k.h),
      low: Number(k.l),
      close: Number(k.c),
      volume: Number(k.v),
      closed: Boolean(k.x),
    };
    if (!Number.isFinite(candle.time) ||
        ![candle.open, candle.high, candle.low, candle.close, candle.volume].every(Number.isFinite)) {
      return null;
    }
    return { type: 'kline', candle };
  }

  if ((Array.isArray(data.bids) && Array.isArray(data.asks)) ||
      (data.e === 'depthUpdate' && Array.isArray(data.b) && Array.isArray(data.a))) {
    return {
      type: 'book',
      book: {
        bids: data.bids || data.b,
        asks: data.asks || data.a,
        lastUpdateId: data.lastUpdateId,
      },
    };
  }

  if (data.e === 'trade') {
    const trade = {
      id: data.t,
      time: Number(data.T),
      price: Number(data.p),
      quantity: Number(data.q),
      isBuyerMaker: Boolean(data.m),
    };
    if (!Number.isFinite(trade.time) || !Number.isFinite(trade.price) ||
        !Number.isFinite(trade.quantity)) return null;
    return { type: 'trade', trade };
  }

  return null;
}
