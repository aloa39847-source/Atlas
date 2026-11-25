import React, { useEffect, useRef, useState } from 'react'
import { api } from './api'

function localDateTimeValue(offsetMs = 0) {
  const date = new Date(Date.now() + offsetMs)
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
  return date.toISOString().slice(0, 16)
}

export default function ArbitragePanel() {
  const [route, setRoute] = useState('USDT,BTC,ETH,USDT')
  const [amount, setAmount] = useState('1000')
  const [fee, setFee] = useState('0.001')
  const [slippage, setSlippage] = useState('0.0005')
  const [threshold, setThreshold] = useState('0.05')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [captureCount, setCaptureCount] = useState(0)
  const [captureStatus, setCaptureStatus] = useState('')
  const [captureError, setCaptureError] = useState('')
  const [replayStart, setReplayStart] = useState(() => localDateTimeValue(-3600000))
  const [replayEnd, setReplayEnd] = useState(() => localDateTimeValue())
  const [latencyMs, setLatencyMs] = useState('250')
  const [replayBusy, setReplayBusy] = useState(false)
  const [replayResult, setReplayResult] = useState(null)
  const [replayError, setReplayError] = useState('')
  const captureTimer = useRef(null)
  const captureInFlight = useRef(false)
  useEffect(() => () => { if (captureTimer.current) clearInterval(captureTimer.current) }, [])

  async function run(e) {
    e.preventDefault()
    setBusy(true); setError(''); setResult(null)
    try {
      const assets = route.split(',').map(x => x.trim().toUpperCase()).filter(Boolean)
      const data = await api.analyzeArbitrage({
        route: assets,
        starting_amount: Number(amount),
        fee_rate: Number(fee),
        slippage_rate: Number(slippage),
        min_profit_pct: Number(threshold),
      })
      setResult(data)
    } catch (err) {
      setError(err.message || 'تعذر تحليل مسار المراجحة')
    } finally {
      setBusy(false)
    }
  }

  async function captureSnapshot() {
    if (captureInFlight.current) return
    captureInFlight.current = true
    setCaptureError('')
    try {
      const assets = route.split(',').map(x => x.trim().toUpperCase()).filter(Boolean)
      const result = await api.captureTriangleOrderbook({ route: assets, depth_limit: 100 })
      setCaptureCount(count => count + 1)
      setCaptureStatus('Saved snapshot ' + result.snapshot_id + ' (skew ' + result.snapshot_skew_ms + 'ms)')
    } catch (e) { setCaptureError(e.message || 'Order-book capture failed') }
    finally { captureInFlight.current = false }
  }
  function startRecording() {
    if (captureTimer.current) return
    setRecording(true)
    captureSnapshot()
    captureTimer.current = setInterval(captureSnapshot, 10000)
  }
  function stopRecording() {
    if (captureTimer.current) clearInterval(captureTimer.current)
    captureTimer.current = null
    setRecording(false)
  }
  async function runOrderbookReplay() {
    setReplayBusy(true); setReplayError(''); setReplayResult(null)
    try {
      const assets = route.split(',').map(x => x.trim().toUpperCase()).filter(Boolean)
      const start = Date.parse(replayStart)
      const end = Date.parse(replayEnd)
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) throw new Error('Invalid replay date range')
      const data = await api.backtestOrderbookTriangle({ route: assets, start_time: start, end_time: end, max_snapshots: 5000, initial_cash: Number(amount), fee_rate: Number(fee), min_profit_pct: Number(threshold), latency_ms:Number(latencyMs), max_cycles:1000 })
      setReplayResult(data)
    } catch (e) { setReplayError(e.message || 'Order-book replay failed') }
    finally { setReplayBusy(false) }
  }
  return <div className="card">
    <h3>محلل المراجحة المثلثية — Spot</h3>
    <p className="muted">يفحص ثلاث أزواج من بيانات Binance العامة للقراءة فقط، ويحسب أثر الرسوم والانزلاق. لا ينفذ أي أمر تداول ولا يضمن إمكانية تنفيذ الفرصة.</p>
    <form onSubmit={run}>
      <div className="row">
        <div style={{flex:2,minWidth:220}}>
          <label>مسار الأصول (ثلاث تحويلات، مع العودة للأصل)</label>
          <input value={route} onChange={e=>setRoute(e.target.value)} placeholder="USDT,BTC,ETH,USDT" />
        </div>
        <div style={{flex:1,minWidth:130}}>
          <label>مبلغ البداية</label>
          <input type="number" min="0.000001" step="any" value={amount} onChange={e=>setAmount(e.target.value)} />
        </div>
      </div>
      <div className="row">
        <div style={{flex:1,minWidth:130}}><label>رسوم كل تحويل</label><input type="number" min="0" max="0.999999" step="any" value={fee} onChange={e=>setFee(e.target.value)} /></div>
        <div style={{flex:1,minWidth:130}}><label>انزلاق كل تحويل</label><input type="number" min="0" max="0.999999" step="any" value={slippage} onChange={e=>setSlippage(e.target.value)} /></div>
        <div style={{flex:1,minWidth:130}}><label>أدنى ربح تقديري (%)</label><input type="number" min="0" step="any" value={threshold} onChange={e=>setThreshold(e.target.value)} /></div>
      </div>
      <button disabled={busy}>{busy ? 'جارٍ الفحص...' : 'فحص مسار المراجحة'}</button>
    </form>
    {error && <p className="error">{error}</p>}
    {result && <div style={{marginTop:12}}>
      <div className="row">
        <strong>النتيجة:</strong>
        <span className="pill">{result.assessment || result.error || 'غير متاح'}</span>
      </div>
      {result.ok && <>
        <div className="grid">
          <div className="card"><div className="muted">مبلغ النهاية التقديري</div><strong>{result.ending_amount_estimate}</strong></div>
          <div className="card"><div className="muted">الربح/الخسارة التقديرية</div><strong>{result.profit_estimate} ({result.profit_percent_estimate}%)</strong></div>
          <div className="card"><div className="muted">مرشح فقط</div><strong>{String(result.candidate)}</strong></div>
        </div>
        <div className="table-wrap"><table><thead><tr><th>التحويل</th><th>السوق</th><th>الاتجاه</th><th>الناتج بعد التكاليف</th></tr></thead>
          <tbody>{(result.legs || []).map((leg,i)=><tr key={i}><td>{leg.from_asset} → {leg.to_asset}</td><td>{leg.market_symbol}</td><td>{leg.execution_side}</td><td>{leg.output_after_estimated_costs}</td></tr>)}</tbody>
        </table></div>
        <ul>{(result.limitations || []).map((item,i)=><li key={i} className="muted">{item}</li>)}</ul>
      </>}
      {!result.ok && <p className="muted">{result.error || result.detail || 'تعذر الحصول على بيانات صالحة.'}</p>}
    </div>}
    <div className="card" style={{marginTop:14}}>
      <h3>تسجيل دفتر الأوامر التاريخي — Binance Spot</h3>
      <p className="muted">يسجل عمق الشراء والبيع للأزواج الثلاثة كل 10 ثوانٍ. يتوقف التسجيل عند إغلاق هذه الصفحة أو إيقافه يدوياً؛ لا توجد بيانات قديمة بأثر رجعي.</p>
      <div className="row" style={{alignItems:'center'}}>
        <button type="button" onClick={startRecording} disabled={recording}>{recording ? 'التسجيل يعمل' : 'بدء التسجيل'}</button>
        <button type="button" onClick={stopRecording} disabled={!recording}>إيقاف التسجيل</button>
        <span className="pill">{recording ? 'recording' : 'stopped'}</span>
        <span className="muted">لقطات هذه الجلسة: {captureCount}</span>
      </div>
      {captureStatus && <p className="muted">{captureStatus}</p>}
      {captureError && <p className="error">{captureError}</p>}
      <div className="row" style={{marginTop:10}}>
        <div style={{flex:1,minWidth:180}}><label>من تاريخ</label><input type="datetime-local" value={replayStart} onChange={e=>setReplayStart(e.target.value)} /></div>
        <div style={{flex:1,minWidth:180}}><label>إلى تاريخ</label><input type="datetime-local" value={replayEnd} onChange={e=>setReplayEnd(e.target.value)} /></div>
        <div style={{flex:1,minWidth:130}}><label>تأخير التنفيذ (ms)</label><input type="number" min="0" max="60000" step="50" value={latencyMs} onChange={e=>setLatencyMs(e.target.value)} /></div>
        <button type="button" onClick={runOrderbookReplay} disabled={replayBusy}>{replayBusy ? 'جارٍ إعادة الاختبار...' : 'اختبار اللقطات المسجلة'}</button>
      </div>
      {replayError && <p className="error">{replayError}</p>}
      {replayResult && <div style={{marginTop:10}}>
        <div className="grid">{[['اللقطات المستخدمة',replayResult.snapshots_fetched ?? replayResult.total_snapshots],['الدورات المربحة',replayResult.profitable_cycles],['رفض الدورات',replayResult.rejected_cycles],['التنفيذ الجزئي',replayResult.partial_fills],['الرصيد النهائي',replayResult.final_equity],['العائد',replayResult.total_return]].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}</div>
        <details style={{marginTop:10}}><summary>تفاصيل إعادة الاختبار</summary><pre>{JSON.stringify(replayResult,null,2)}</pre></details>
      </div>}
      <p className="muted">المحاكاة تستخدم تأخيراً قابلاً للضبط، وتستهلك عمق الدفتر مع فلاتر السعر والكمية والحد الأدنى، وتكشف التنفيذ الجزئي والتعرض غير المحوط. لا تعيد بناء تغييرات العمق بين اللقطات ولا تحاكي أولوية الطابور. لا تُرسل أوامر تداول.</p>
    </div>
  </div>
}
