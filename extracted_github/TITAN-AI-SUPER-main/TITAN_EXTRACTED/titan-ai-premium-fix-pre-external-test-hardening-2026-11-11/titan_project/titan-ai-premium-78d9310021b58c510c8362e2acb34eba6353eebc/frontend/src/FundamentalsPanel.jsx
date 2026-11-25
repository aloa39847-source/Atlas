import React, { useState } from 'react'
import { api } from './api'

export default function FundamentalsPanel() {
  const today = new Date().toISOString().slice(0, 10)
  const later = new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10)
  const [coinId, setCoinId] = useState('bitcoin')
  const [protocolSlug, setProtocolSlug] = useState('aave')
  const [tokenId, setTokenId] = useState('arbitrum')
  const [unlockFrom, setUnlockFrom] = useState(today)
  const [unlockTo, setUnlockTo] = useState(later)
  const [coin, setCoin] = useState(null)
  const [protocol, setProtocol] = useState(null)
  const [unlocks, setUnlocks] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function run(action) {
    setBusy(true); setError('')
    try { await action() }
    catch (err) { setError(err.message || 'تعذر جلب البيانات') }
    finally { setBusy(false) }
  }

  function analyzeCoin(e) {
    e.preventDefault(); setCoin(null)
    run(async () => setCoin(await api.fundamentalCoin(coinId.trim().toLowerCase())))
  }

  function analyzeProtocol(e) {
    e.preventDefault(); setProtocol(null)
    run(async () => setProtocol(await api.fundamentalProtocol(protocolSlug.trim().toLowerCase())))
  }

  function analyzeUnlocks(e) {
    e.preventDefault(); setUnlocks(null)
    run(async () => setUnlocks(await api.tokenUnlocks({
      token_id: tokenId.trim().toLowerCase(), from: unlockFrom, to: unlockTo,
    })))
  }

  return <div className="card">
    <h3>التحليل الأساسي والاقتصاديات الرمزية وبيانات البروتوكول</h3>
    <div className="notice">بيانات السوق والاقتصاديات الرمزية من CoinGecko، ومقاييس TVL والرسوم من DeFiLlama. جداول فتح العملات من Tokenomist API v4 وتتطلب مفتاحاً مرخصاً على الخادم. لا تمثل هذه المصادر تحققاً مستقلاً من صحة المشروع أو تدقيقاً مالياً.</div>
    <div className="grid" style={{marginTop:12}}>
      <form className="card" onSubmit={analyzeCoin}>
        <h4>تحليل العملة / Tokenomics</h4>
        <label>CoinGecko ID</label>
        <input value={coinId} onChange={e=>setCoinId(e.target.value)} placeholder="bitcoin" />
        <button disabled={busy}>تحليل بيانات العملة</button>
      </form>
      <form className="card" onSubmit={analyzeProtocol}>
        <h4>تحليل البروتوكول / On-chain</h4>
        <label>DeFiLlama protocol slug</label>
        <input value={protocolSlug} onChange={e=>setProtocolSlug(e.target.value)} placeholder="aave" />
        <button disabled={busy}>تحليل TVL والرسوم</button>
      </form>
      <form className="card" onSubmit={analyzeUnlocks}>
        <h4>جدول فتح العملات / Token Unlocks</h4>
        <label>Tokenomist token ID</label>
        <input value={tokenId} onChange={e=>setTokenId(e.target.value)} placeholder="arbitrum" />
        <div className="row">
          <div style={{flex:1}}><label>من</label><input type="date" value={unlockFrom} onChange={e=>setUnlockFrom(e.target.value)} /></div>
          <div style={{flex:1}}><label>إلى</label><input type="date" value={unlockTo} onChange={e=>setUnlockTo(e.target.value)} /></div>
        </div>
        <button disabled={busy}>جلب أحداث الفتح</button>
        <div className="muted">يتطلب TOKENOMIST_API_KEY وخطة تسمح باستخدام البيانات. شروط المزود تقيد إعادة التوزيع والاستخدام التجاري.</div>
      </form>
    </div>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {coin && <div className="card" style={{marginTop:12}}>
      <h4>{coin.name} ({coin.symbol}) — CoinGecko</h4>
      <div className="grid">
        {[
          ['Price',coin.current_price],['Market cap',coin.market_cap],['24h volume',coin.volume_24h],
          ['FDV / Market cap',coin.fdv_to_market_cap_ratio],['Circulating / max supply',coin.circulating_to_max_supply_ratio],
          ['Drawdown from ATH %',coin.drawdown_from_ath_percent],
        ].map(([k,v])=><div className="card" key={k}><div className="muted">{k}</div><b>{String(v ?? '—')}</b></div>)}
      </div>
      <div className="muted">Warnings: {(coin.supply_consistency_warnings || []).concat(coin.market_data_warnings || []).join(', ') || 'none'}</div>
    </div>}
    {protocol && <div className="card" style={{marginTop:12}}>
      <h4>{protocol.name} — DeFiLlama</h4>
      <div className="grid">
        {[
          ['Current TVL USD',protocol.current_tvl_usd],['TVL change 7d %',protocol.tvl_change_7d_percent],
          ['TVL change 30d %',protocol.tvl_change_30d_percent],['Fees 24h USD',protocol.fees_24h_usd],
          ['Fees 7d USD',protocol.fees_7d_usd],['Fees 30d USD',protocol.fees_30d_usd],
          ['Revenue 30d USD',protocol.revenue_30d_usd],['Fees 30d / TVL %',protocol.fees_30d_to_current_tvl_percent],
        ].map(([k,v])=><div className="card" key={k}><div className="muted">{k}</div><b>{String(v ?? '—')}</b></div>)}
      </div>
      <div className="muted">Chains: {(protocol.chains || []).join(', ') || 'not supplied'}</div>
      <ul>{(protocol.limitations || []).map((item,i)=><li key={i} className="muted">{item}</li>)}</ul>
    </div>}
    {unlocks && <div className="card" style={{marginTop:12}}>
      <h4>أحداث فتح العملات: {unlocks.token_id} ({unlocks.count})</h4>
      <div className="muted">المصدر: {unlocks.source || 'Tokenomist API v4'} · تاريخ الاستعلام: {unlocks.query_date || 'غير محدد'}</div>
      <div className="table-wrap"><table><thead><tr><th>التاريخ UTC</th><th>الرمز</th><th>الكمية</th><th>القيمة USD</th><th>% من القيمة السوقية</th><th>طريقة الإدراج</th><th>المصدر</th></tr></thead>
        <tbody>{(unlocks.events || []).map((event,i)=><tr key={event.unlock_date + '-' + i}>
          <td>{event.unlock_date}</td><td>{event.token_symbol}</td><td>{event.cliff_amount ?? '—'}</td>
          <td>{event.cliff_value_usd ?? '—'}</td><td>{event.value_to_market_cap_percent ?? '—'}</td>
          <td>{event.listed_method}</td><td>{event.data_source}</td>
        </tr>)}</tbody>
      </table></div>
      <ul>{(unlocks.limitations || []).map((item,i)=><li key={i} className="muted">{item}</li>)}</ul>
      <a href={unlocks.source_url} target="_blank" rel="noreferrer">وثائق Tokenomist API v4</a>
    </div>}
  </div>
}
