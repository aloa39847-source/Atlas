import React, { useState } from 'react'
import { api } from './api'

export default function MarketMicrostructurePanel() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [marketType, setMarketType] = useState('spot')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [coinId, setCoinId] = useState('bitcoin')
  const [fundamentals, setFundamentals] = useState(null)
  const [newsKeyword, setNewsKeyword] = useState('BTC')
  const [news, setNews] = useState(null)

  async function analyze() {
    setBusy(true); setError('')
    try { setResult(await api.marketMicrostructure(symbol.trim().toUpperCase(), marketType)) }
    catch (e) { setError(e.message || 'تعذر جلب بيانات السيولة') }
    finally { setBusy(false) }
  }
  async function analyzeFundamentals() {
    setBusy(true); setError('')
    try { setFundamentals(await api.fundamentals(coinId.trim().toLowerCase())) }
    catch (e) { setError(e.message || 'تعذر جلب البيانات الأساسية') }
    finally { setBusy(false) }
  }
  async function analyzeNews() {
    setBusy(true); setError('')
    try { setNews(await api.news(newsKeyword.trim(), 40)) }
    catch (e) { setError(e.message || 'تعذر جلب الأخبار') }
    finally { setBusy(false) }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>تحليل السيولة وتدفق الصفقات (Public)</h3>
    <div className="notice">لقطة عمق دفتر الأوامر وصفقات Binance العامة الأخيرة للسوق المحدد. بيانات Futures هنا عامة للقراءة فقط؛ لا تتضمن Testnet أو تنفيذ أوامر أو مزامنة WebSocket.</div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>زوج USDT</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} /></div>
      <div><label>السوق</label><select value={marketType} onChange={e=>setMarketType(e.target.value)}><option value="spot">Spot</option><option value="futures">USD-M Futures (بيانات عامة فقط)</option></select></div>
      <button type="button" onClick={analyze} disabled={busy}>{busy ? 'جارٍ التحليل...' : 'تحليل السيولة والتدفق'}</button>
    </div>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {result && <div style={{marginTop:10}}>
      <div className="muted">{result.symbol} | {result.source} | تنفيذ: {result.execution}</div>
      <div className="grid" style={{marginTop:8}}>
        <div className="card"><div className="muted">Spread %</div><b>{result.liquidity?.spread_percent ?? '—'}</b></div>
        <div className="card"><div className="muted">Bid depth USDT</div><b>{result.liquidity?.bid_depth_quote ?? '—'}</b></div>
        <div className="card"><div className="muted">Ask depth USDT</div><b>{result.liquidity?.ask_depth_quote ?? '—'}</b></div>
        <div className="card"><div className="muted">Depth imbalance</div><b>{result.liquidity?.depth_imbalance ?? '—'}</b></div>
        <div className="card"><div className="muted">Taker flow imbalance</div><b>{result.order_flow?.taker_flow_imbalance ?? '—'}</b></div>
        <div className="card"><div className="muted">Taker buy ratio</div><b>{result.order_flow?.taker_buy_quote_ratio ?? '—'}</b></div>
      </div>
      <details style={{marginTop:10}}><summary>تفاصيل دفتر الأوامر والصفقات</summary><pre>{JSON.stringify(result,null,2)}</pre></details>
      <div className="muted">{result.note}</div>
    </div>}
    <div className="divider" style={{marginTop:16}} />
    <h3>تحليل أساسيات العملة (Tokenomics)</h3>
    <div className="notice">بيانات سوقية وتوكنوميكس عامة من CoinGecko. لا تشمل تدقيق العقد أو الفريق أو فتح الأقفال أو المخاطر القانونية.</div>
    <div className="row" style={{marginTop:8}}>
      <div style={{flex:1}}><label>CoinGecko ID</label><input value={coinId} onChange={e=>setCoinId(e.target.value.toLowerCase())} /></div>
      <button type="button" className="secondary" onClick={analyzeFundamentals} disabled={busy}>تحليل الأساسيات</button>
    </div>
    {fundamentals && <div style={{marginTop:10}}>
      <div className="muted">{fundamentals.name} ({fundamentals.symbol}) | المصدر: {fundamentals.source}</div>
      <div className="grid">
        {[
          ['Market cap',fundamentals.market_cap],['FDV / Market cap',fundamentals.fdv_to_market_cap_ratio],
          ['Circulating / Max supply',fundamentals.circulating_to_max_supply_ratio],
          ['Market cap / Volume',fundamentals.market_cap_to_volume_24h_ratio],
          ['ATH drawdown %',fundamentals.drawdown_from_ath_percent],
          ['24h change %',fundamentals.price_change_percent?.['24h']],
        ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}
      </div>
      <details style={{marginTop:8}}><summary>تفاصيل التوكنوميكس</summary><pre>{JSON.stringify(fundamentals,null,2)}</pre></details>
    </div>}
    <div className="divider" style={{marginTop:16}} />
    <h3>تحليل الأخبار العامة</h3>
    <div className="notice">مصادر RSS عامة. التحليل يعتمد معجم كلمات شفافًا وليس نموذجًا لغويًا مدربًا؛ قد يخطئ في السياق والنفي، ولا يولد إشارة تداول.</div>
    <div className="row" style={{marginTop:8}}>
      <div style={{flex:1}}><label>رمز أو كلمة (مثلاً BTC أو bitcoin)</label><input value={newsKeyword} onChange={e=>setNewsKeyword(e.target.value)} /></div>
      <button type="button" className="secondary" onClick={analyzeNews} disabled={busy}>جلب وتحليل الأخبار</button>
    </div>
    {news && <div style={{marginTop:10}}>
      <div className="muted">المصادر الناجحة: {news.successful_sources}/{news.source_count} | النتائج: {news.count}</div>
      {news.items?.map((item,index)=><div className="card" key={item.url || index} style={{marginTop:8}}>
        <div className="row"><b>{item.title}</b><span className="pill">{item.sentiment}</span></div>
        <div className="muted">{item.source} | {item.published_at || 'تاريخ غير متوفر'} | {item.topics?.join(', ')}</div>
        <p>{item.summary}</p>
        <a href={item.url} target="_blank" rel="noreferrer">فتح المصدر الأصلي</a>
      </div>)}
      {news.failures?.length > 0 && <details style={{marginTop:8}}><summary>مصادر لم تعمل</summary><pre>{JSON.stringify(news.failures,null,2)}</pre></details>}
      <div className="muted">{news.note}</div>
    </div>}
  </div>
}
