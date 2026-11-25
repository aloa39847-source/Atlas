import React, { useRef, useState } from 'react'
import { api } from './api'

function localDate(offsetMs = 0) {
  const date = new Date(Date.now() + offsetMs)
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
  return date.toISOString().slice(0, 16)
}

export default function FuturesOrderbookHistoryPanel() {
  const [symbol,setSymbol] = useState('BTCUSDT')
  const [captureCount,setCaptureCount] = useState('30')
  const [captured,setCaptured] = useState(0)
  const [recording,setRecording] = useState(false)
  const [captureError,setCaptureError] = useState('')
  const [start,setStart] = useState(()=>localDate(-24*3600000))
  const [end,setEnd] = useState(()=>localDate())
  const [result,setResult] = useState(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const stopRef=useRef(false)

  async function record() {
    const count=Number(captureCount)
    if(!Number.isInteger(count)||count<7||count>300){setCaptureError('اختر من 7 إلى 300 لقطة');return}
    stopRef.current=false;setRecording(true);setCaptureError('');setCaptured(0)
    try {
      for(let i=0;i<count&&!stopRef.current;i++){
        await api.captureFuturesOrderbook({symbol:symbol.trim().toUpperCase().replaceAll('/',''),depth_limit:100})
        setCaptured(i+1)
        if(i<count-1&&!stopRef.current) await new Promise(resolve=>setTimeout(resolve,1000))
      }
    } catch(e) { setCaptureError(e.message||'تعذر تسجيل دفتر الأوامر') }
    finally { setRecording(false) }
  }

  async function replay() {
    setBusy(true);setError('');setResult(null)
    try {
      const from=Date.parse(start),to=Date.parse(end)
      if(!Number.isFinite(from)||!Number.isFinite(to)||to<=from) throw new Error('حدد فترة صحيحة')
      const data=await api.backtestFuturesOrderbook({
        symbol:symbol.trim().toUpperCase().replaceAll('/',''),
        start_time:from,end_time:to,max_snapshots:20000,
        rolling_window:5,entry_imbalance:0.2,exit_imbalance:0.02,
        initial_cash:1000,fee_rate:0.0004,slippage_rate:0.0002,
        max_notional:800,leverage:2,max_leverage:10,
      })
      setResult(data)
    } catch(e) { setError(e.message||'تعذر إعادة تشغيل دفتر الأوامر التاريخي') }
    finally { setBusy(false) }
  }

  return <div className="card" style={{marginTop:14}}>
    <h3>دفتر أوامر Futures التاريخي — USD-M Order Flow</h3>
    <div className="notice">يسجل لقطات عمق السوق العامة من Binance Futures ويحفظها لبناء تاريخ من وقت بدء التسجيل. بيانات دفتر الأوامر التاريخية لا تتوفر بأثر رجعي عبر REST؛ يلزم تسجيلها مسبقاً. هذه محاكاة فقط ولا ترسل أوامر.</div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>زوج Futures</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())}/></div>
      <div style={{flex:1}}><label>عدد اللقطات (كل ثانية)</label><input type="number" min="7" max="300" value={captureCount} onChange={e=>setCaptureCount(e.target.value)}/></div>
    </div>
    <div className="row" style={{marginTop:8}}>
      <button type="button" disabled={recording} onClick={record}>{recording?'جارٍ التسجيل...':'بدء تسجيل دفتر الأوامر'}</button>
      {recording && <button type="button" className="secondary" onClick={()=>{stopRef.current=true}}>إيقاف التسجيل</button>}
      <span className="muted">اللقطات المسجلة في هذه الجلسة: {captured}</span>
    </div>
    {captureError&&<div className="error" style={{marginTop:8}}>{captureError}</div>}
    <div className="row" style={{marginTop:12}}>
      <div style={{flex:1}}><label>من</label><input type="datetime-local" value={start} onChange={e=>setStart(e.target.value)}/></div>
      <div style={{flex:1}}><label>إلى</label><input type="datetime-local" value={end} onChange={e=>setEnd(e.target.value)}/></div>
      <button type="button" disabled={busy||recording} onClick={replay}>{busy?'جارٍ الاختبار...':'اختبار Order Flow من دفتر الأوامر'}</button>
    </div>
    {error&&<div className="error" style={{marginTop:8}}>{error}</div>}
    {result&&<div style={{marginTop:12}}>
      <div className="grid">
        {[
          ['اللقطات',result.depth_snapshots],
          ['الإشارات',result.generated_signals],
          ['الصفقات',result.total_trades],
          ['نسبة الفوز',result.win_rate],
          ['العائد',result.total_return],
          ['الرصيد النهائي',result.final_equity],
          ['الرسوم',result.fees_paid],
          ['التمويل',result.funding_net],
        ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value??'—')}</b></div>)}
      </div>
      <div className="notice" style={{marginTop:8}}>مصدر البيانات: {result.data_source}. وضع التنفيذ: محاكاة فقط.</div>
      <details style={{marginTop:8}}><summary>تفاصيل المحاكاة والقيود</summary><pre>{JSON.stringify(result,null,2)}</pre></details>
    </div>}
  </div>
}
