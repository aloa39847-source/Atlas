import React, { useState } from 'react'
import { api } from './api'

export default function RiskSimulationPanel() {
  const [marketType, setMarketType] = useState('spot')
  const [mode, setMode] = useState('price_path')
  const [dataSource, setDataSource] = useState('binance')
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [candleLimit, setCandleLimit] = useState('500')
  const [leverage, setLeverage] = useState('2')
  const [candlesJson, setCandlesJson] = useState('[]')
  const [crossPositionsJson, setCrossPositionsJson] = useState('[{"symbol":"BTCUSDT","side":"long","quantity":0.01,"entry_price":60000,"mark_price":61000,"leverage":5}]')
  const [bracketsBusy, setBracketsBusy] = useState(false)
  const [bracketsStatus, setBracketsStatus] = useState('')
  const [feeRate, setFeeRate] = useState('0.001')
  const [slippageRate, setSlippageRate] = useState('0.0005')
  const [balance, setBalance] = useState('1000')
  const [riskPerTrade, setRiskPerTrade] = useState('0.005')
  const [dailyLimit, setDailyLimit] = useState('0.07')
  const [pnlSequence, setPnlSequence] = useState('-25,-25,15,-10')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function fetchBinanceBrackets() {
    setBracketsBusy(true); setBracketsStatus(''); setError('')
    try {
      const positions = JSON.parse(crossPositionsJson)
      if (!Array.isArray(positions) || !positions.length || positions.length > 60) throw new Error('أدخل من مركز واحد إلى 60 مركزاً')
      const symbols = [...new Set(positions.map(row => String(row?.symbol || '').trim().toUpperCase().replaceAll('/', '')))]
      if (symbols.some(value => !value)) throw new Error('كل مركز يجب أن يحتوي على رمز Binance صحيح')
      const schedules = {}
      for (const value of symbols) {
        const response = await api.binanceTestnetFuturesMaintenanceBrackets(value)
        if (!response?.ok || !Array.isArray(response.maintenance_brackets) || !response.maintenance_brackets.length) {
          throw new Error('لم تصل شرائح صيانة موثقة للرمز ' + value)
        }
        schedules[value] = response.maintenance_brackets
      }
      const updated = positions.map(row => ({
        ...row,
        maintenance_brackets: schedules[String(row.symbol).trim().toUpperCase().replaceAll('/', '')],
      }))
      setCrossPositionsJson(JSON.stringify(updated, null, 2))
      setBracketsStatus('تم جلب شرائح Binance Futures Testnet والتحقق منها لكل الرموز. لم تُرسل أي أوامر.')
    } catch (e) {
      setError(e.message || 'تعذر جلب شرائح الصيانة من Binance Futures Testnet')
    } finally {
      setBracketsBusy(false)
    }
  }

  async function runSimulation() {
    setBusy(true); setError(''); setResult(null)
    try {
      let payload = {
        market_type: marketType,
        initial_balance: Number(balance),
        risk_per_trade: Number(riskPerTrade),
        max_daily_loss: Number(dailyLimit),
      }
      if (mode === 'cross_margin') {
        let positions
        try { positions = JSON.parse(crossPositionsJson) } catch { throw new Error('صيغة JSON لمراكز Cross Margin غير صحيحة') }
        if (!Array.isArray(positions) || !positions.length || positions.length > 60) throw new Error('أدخل من مركز واحد إلى 60 مركزاً')
        payload = { mode:'cross_margin', wallet_balance:Number(balance), positions, liquidation_fee_rate:0.005 }
      } else if (mode === 'price_path') {
        if (dataSource === 'binance') {
          const count=Number(candleLimit)
          if(!Number.isInteger(count)||count<30||count>1000) throw new Error('عدد الشموع يجب أن يكون بين 30 و1000')
          payload = { ...payload, symbol:symbol.trim().toUpperCase().replaceAll('/',''),
            timeframe, limit:count, leverage:marketType==='spot'?1:Number(leverage),
            fee_rate:Number(feeRate), slippage_rate:Number(slippageRate),
            max_notional:marketType==='spot'?100:800, max_trades:1000 }
        } else {
          let candles
          try { candles = JSON.parse(candlesJson) } catch { throw new Error('صيغة JSON للشموع غير صحيحة') }
          if (!Array.isArray(candles) || candles.length < 30 || candles.length > 100000) throw new Error('أدخل 30 إلى 100000 شمعة OHLCV')
          payload = { ...payload, candles, leverage:marketType==='spot'?1:Number(leverage),
            fee_rate:Number(feeRate), slippage_rate:Number(slippageRate),
            max_notional:marketType==='spot'?100:800, max_trades:1000 }
        }
      } else {
        const pnl = pnlSequence.split(',').map(value => {
          const parsed = Number(value.trim())
          if (!Number.isFinite(parsed)) throw new Error('أدخل نتائج رقمية مفصولة بفواصل')
          return parsed
        })
        if (!pnl.length || pnl.length > 500) throw new Error('أدخل من 1 إلى 500 نتيجة صفقة')
        payload = { ...payload, max_open_positions:1, trades:pnl.map(realized_pnl => ({
          session:'simulation-day-1', entry:100, stop_loss:99, side:'long', realized_pnl,
        })) }
      }
      const response = await api.riskSimulation(payload)
      setResult(response)
    } catch (e) {
      setError(e.message || 'تعذر تشغيل محاكاة المخاطر')
    } finally {
      setBusy(false)
    }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>محاكي إدارة المخاطر — Demo Risk Simulation</h3>
    <div className="notice">اختبار افتراضي فقط: لا يتصل بحساب Binance ولا يرسل أوامر. أدخل نتائج افتراضية للصفقات لاختبار متى يوقف TITAN فتح صفقات جديدة عند بلوغ حد الخسارة اليومية.</div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>نوع المحاكاة</label><select value={mode} onChange={e=>setMode(e.target.value)}><option value="price_path">مسار أسعار تاريخي OHLCV</option><option value="cross_margin">محاكاة Futures Cross Margin</option><option value="pnl_sequence">نتائج افتراضية مبسطة</option></select></div>
      <div style={{flex:1}}><label>السوق</label><select value={marketType} onChange={e=>setMarketType(e.target.value)}><option value="spot">Spot</option><option value="futures">Futures</option></select></div>
      <div style={{flex:1}}><label>الرصيد الافتراضي USDT</label><input type="number" min="1" value={balance} onChange={e=>setBalance(e.target.value)} /></div>
      <div style={{flex:1}}><label>مخاطرة الصفقة (0.005 = 0.5%)</label><input type="number" min="0.0001" max="0.1" step="0.001" value={riskPerTrade} onChange={e=>setRiskPerTrade(e.target.value)} /></div>
      <div style={{flex:1}}><label>حد الخسارة اليومية (0.07 = 7%)</label><input type="number" min="0" max="1" step="0.01" value={dailyLimit} onChange={e=>setDailyLimit(e.target.value)} /></div>
    </div>
    {mode === 'cross_margin' ? <div style={{marginTop:8}}><label>المراكز وشرائح الصيانة JSON</label><textarea rows={8} value={crossPositionsJson} onChange={e=>setCrossPositionsJson(e.target.value)}/><div className="row" style={{marginTop:8}}><button type="button" onClick={fetchBinanceBrackets} disabled={bracketsBusy}>{bracketsBusy ? "جارٍ جلب شرائح Binance..." : "جلب شرائح الصيانة من Binance Futures Testnet"}</button></div>{bracketsStatus && <div className="notice" style={{marginTop:8}}>{bracketsStatus}</div>}<div className="muted">يتطلب تسجيل الدخول ومفاتيح Futures Testnet بصلاحية القراءة. يجلب TITAN شرائح الحساب عبر طلب GET موقّع للقراءة فقط. لا يستخدم Live ولا يرسل أوامر. إذا لم تكن بيانات Testnet متاحة، لا يخمّن النظام الشرائح.</div></div> : mode === 'price_path' ? <div style={{marginTop:8}}>
      <div className="row">
        <div style={{flex:1}}><label>مصدر الشموع</label><select value={dataSource} onChange={e=>setDataSource(e.target.value)}><option value="binance">Binance Public History</option><option value="manual">JSON يدوي</option></select></div>
        {dataSource==='binance' && <>
          <div style={{flex:1}}><label>الزوج</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())}/></div>
          <div style={{flex:1}}><label>الإطار</label><select value={timeframe} onChange={e=>setTimeframe(e.target.value)}>{['5m','15m','1h','4h','1d'].map(x=><option key={x} value={x}>{x}</option>)}</select></div>
          <div style={{flex:1}}><label>عدد الشموع (30–1000)</label><input type="number" min="30" max="1000" value={candleLimit} onChange={e=>setCandleLimit(e.target.value)}/></div>
        </>}
      </div>
      {dataSource==='manual' && <>
        <label>بيانات الشموع بصيغة JSON (timestamp, open, high, low, close, volume)</label>
        <textarea rows={5} value={candlesJson} onChange={e=>setCandlesJson(e.target.value)} placeholder='[{"timestamp":1800000000000,"open":100,"high":102,"low":99,"close":101,"volume":1000}]' />
      </>}
      {marketType==='futures' && <div style={{marginTop:8}}><label>الرافعة (1–10x)</label><input type="number" min="1" max="10" value={leverage} onChange={e=>setLeverage(e.target.value)}/></div>}
      <div className="row" style={{marginTop:8}}>
        <div style={{flex:1}}><label>رسوم التداول (0.001 = 0.1%)</label><input type="number" min="0" max="0.1" step="0.0001" value={feeRate} onChange={e=>setFeeRate(e.target.value)} /></div>
        <div style={{flex:1}}><label>الانزلاق السعري (0.0005 = 0.05%)</label><input type="number" min="0" max="0.1" step="0.0001" value={slippageRate} onChange={e=>setSlippageRate(e.target.value)} /></div>
      </div>
      <div className="muted">يستطيع TITAN جلب الشموع العامة مباشرة من Binance. الإشارة بعد إغلاق الشمعة والدخول عند افتتاح التالية؛ في Futures تُضاف رسوم التمويل التاريخية عند توفرها. لا توجد أوامر فعلية.</div>
    </div> : <div style={{marginTop:8}}>
      <label>نتائج الصفقات الافتراضية USDT (موجب ربح، سالب خسارة)</label>
      <input value={pnlSequence} onChange={e=>setPnlSequence(e.target.value)} placeholder="-25,-25,15,-10" />
    </div>}

    <div className="row" style={{marginTop:10}}>
      <button type="button" onClick={runSimulation} disabled={busy}>{busy ? 'جارٍ تشغيل المحاكاة...' : 'تشغيل محاكاة المخاطر'}</button>
    </div>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {result && result.margin_mode === 'cross' && <div style={{marginTop:12}}><div className="grid">{[['Equity مشترك',result.cross_equity],['PnL غير محقق',result.unrealized_pnl],['هامش الصيانة',result.maintenance_margin],['فائض الهامش',result.margin_surplus],['رسوم تصفية تقديرية',result.estimated_liquidation_fees],['حالة التصفية',result.liquidation_breach?'خطر تصفية':'ضمن الهامش']].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}</div><pre>{JSON.stringify(result.positions,null,2)}</pre><div className="notice">محاكاة Cross Margin مشتركة — تقديرية، وليست سعر تصفية Binance الرسمي.</div></div>}
    {result && result.margin_mode !== 'cross' && <div style={{marginTop:12}}>
      <div className="grid">
        {[
          ['الرصيد النهائي', result.final_equity],
          ['صافي الربح/الخسارة', result.net_pnl],
          ['العائد', result.total_return],
          ['أقصى تراجع USDT', result.max_drawdown_quote],
          ['أقصى تراجع %', result.max_drawdown_pct],
          ['الصفقات', result.total_trades ?? result.accepted_trades],
          ['الرسوم', result.fees_paid],
          ['الانزلاق', result.slippage_cost],
          ['حظر حد الخسارة', result.daily_limit_blocks],
        ].map(([label,value]) => <div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}
      </div>
      {result.results?.length > 0 && <div className="table-wrap" style={{marginTop:10}}>
        <table><thead><tr><th>الصفقة</th><th>النتيجة USDT</th><th>الحالة</th><th>السبب</th></tr></thead>
          <tbody>{(result.results || []).map(row => <tr key={row.index}>
            <td>{row.index + 1}</td>
            <td>{row.realized_pnl ?? '—'}</td>
            <td><span className={row.accepted ? 'pill good' : 'pill warn'}>{row.accepted ? 'مقبولة بالمحاكاة' : 'مرفوضة'}</span></td>
            <td>{(row.issues || []).join(', ') || 'اجتازت فحص المخاطر'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      {result.trades?.length > 0 && <details style={{marginTop:8}}><summary>الصفقات التاريخية</summary><pre>{JSON.stringify(result.trades,null,2)}</pre></details>}
      <div className="notice" style={{marginTop:8}}>وضع التنفيذ: محاكاة فقط — لا أوامر حقيقية.</div>
    </div>}
  </div>
}
