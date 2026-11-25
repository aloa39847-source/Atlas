import React, { useEffect, useState } from 'react'
import { api } from './api'

function Pill({ status, children }) {
  const cls = status === 'good' ? 'good' : 'warn'
  return <span className={'pill ' + cls}>{children || status}</span>
}

export default function SmartScanner({ market = 'spot', balance = 150, maxLeverage = 3 }) {
  const futures = market === 'futures'
  const [limit, setLimit] = useState(10)
  const [minVolume, setMinVolume] = useState(futures ? 10000000 : 1000000)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setMinVolume(futures ? 10000000 : 1000000)
    setResult(null)
    setError('')
  }, [futures])

  async function scan() {
    setBusy(true)
    setError('')
    try {
      const params = {
        limit,
        min_quote_volume_usdt: minVolume,
        balance: Number(balance),
        risk_per_trade: 0.005,
      }
      const data = futures
        ? await api.smartScannerFutures({ ...params, max_leverage: Math.max(1, Math.min(10, Number(maxLeverage) || 3)) })
        : await api.smartScanner(params)
      setResult(data)
    } catch (e) {
      setError(e.message || 'تعذر تشغيل الماسح')
    } finally {
      setBusy(false)
    }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>الماسح الذكي متعدد العملات والأطر — {futures ? 'Futures USDT Perpetual' : 'Spot USDT'}</h3>
    <div className="notice">
      {futures
        ? 'يفحص عقود USDT الدائمة النشطة، ويحلل LONG/SHORT عبر 1D و4H و1H و15M و5M. يتحقق من Mark Price ومعدل Funding. تحليل فقط؛ لا يرسل أوامر ولا يفتح مراكز.'
        : 'يختار أزواج USDT Spot حسب السيولة، ثم يحلل كل مرشح عبر 1D و4H و1H و15M و5M باستخدام محرك القرار الحالي. بيانات Binance Public للقراءة فقط؛ لا يرسل أوامر ولا يفتح مراكز Shadow.'}
    </div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>عدد العملات الأقصى (1–20)</label><input type="number" min="1" max="20" value={limit} onChange={e=>setLimit(Math.max(1, Math.min(20, Number(e.target.value)||1)))} /></div>
      <div style={{flex:1}}><label>أقل حجم تداول 24h (USDT)</label><input type="number" min="0" step="100000" value={minVolume} onChange={e=>setMinVolume(Math.max(0, Number(e.target.value)||0))} /></div>
    </div>
    <button type="button" onClick={scan} disabled={busy} style={{marginTop:10}}>{busy ? 'جارٍ فحص العملات...' : 'ابدأ Smart Scan'}</button>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {result && <>
      <div className="muted" style={{marginTop:10}}>وقت الفحص: {result.scanned_at} | تم تحليل: {result.analyzed} | تم تجاوز: {result.skipped} | المصدر: {result.source}</div>
      <div className="table-wrap" style={{marginTop:10}}>
        <table><thead><tr><th>الترتيب</th><th>العملة</th><th>القرار</th><th>الثقة</th><th>حجم 24h (USDT)</th><th>{futures ? 'Mark Price' : 'السعر'}</th>{futures && <th>Funding Rate</th>}<th>السبب</th></tr></thead>
          <tbody>{(result.items||[]).map(item=><tr key={item.symbol}>
            <td>{item.rank}</td><td>{item.symbol}</td><td><Pill status={['buy','long','short'].includes(item.action)?'good':'warn'}>{item.action.toUpperCase()}</Pill></td>
            <td>{item.confidence}</td><td>{Number(item.market_snapshot?.quote_volume_usdt_24h||0).toLocaleString()}</td>
            <td>{futures ? item.mark_price : item.market_snapshot?.last_price}</td>{futures && <td>{item.funding_rate}</td>}<td className="muted">{item.reason}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {result.skipped_items?.length > 0 && <details style={{marginTop:10}}><summary>العملات المتجاوزة ({result.skipped})</summary><pre>{JSON.stringify(result.skipped_items,null,2)}</pre></details>}
      <details style={{marginTop:10}}><summary>تفاصيل التحليل والتغطية لكل إطار</summary><pre>{JSON.stringify(result.items,null,2)}</pre></details>
      <div className="muted" style={{marginTop:8}}>{result.note}</div>
    </>}
  </div>
}
