import React, { useState } from 'react'
import { api } from './api'

function backtestLocalDate(offsetMs = 0) {
  const date = new Date(Date.now() + offsetMs)
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
  return date.toISOString().slice(0, 16)
}

export default function BacktestPanel() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [marketType, setMarketType] = useState('spot')
  const [leverage, setLeverage] = useState('3')
  const [notional, setNotional] = useState('800')
  const [entryFillRatio, setEntryFillRatio] = useState('1')
  const [exitFillRatio, setExitFillRatio] = useState('1')
  const [strategyKey, setStrategyKey] = useState('trend_following_ema')
  const [mode, setMode] = useState('walk_forward')
  const [initialCash, setInitialCash] = useState('150')
  const [limit, setLimit] = useState('240')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [portfolioSymbols, setPortfolioSymbols] = useState('BTCUSDT,ETHUSDT')
  const [portfolioStrategies, setPortfolioStrategies] = useState(['trend_following_ema','momentum_macd_rsi'])
  const [portfolioAllocationMode, setPortfolioAllocationMode] = useState('equal')
  const [allocationRegime, setAllocationRegime] = useState('unknown')
  const [portfolioWeights, setPortfolioWeights] = useState({})
  const [pyramidingEnabled, setPyramidingEnabled] = useState(false)
  const [maxAdds, setMaxAdds] = useState('2')
  const [addFraction, setAddFraction] = useState('0.25')
  const [addTriggerPct, setAddTriggerPct] = useState('0.01')
  const [portfolioResult, setPortfolioResult] = useState(null)
  const [portfolioBusy, setPortfolioBusy] = useState(false)
  const [portfolioError, setPortfolioError] = useState('')
  const [orderflowStart, setOrderflowStart] = useState(() => backtestLocalDate(-7 * 24 * 3600000))
  const [orderflowEnd, setOrderflowEnd] = useState(() => backtestLocalDate())
  const [orderflowResult, setOrderflowResult] = useState(null)
  const [orderflowBusy, setOrderflowBusy] = useState(false)
  const [orderflowError, setOrderflowError] = useState('')

  async function run() {
    setBusy(true); setError(''); setResult(null)
    try {
      const payload = {
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        strategy_key: strategyKey,
        mode,
        initial_cash: Number(initialCash),
        limit: Number(limit),
        fee_rate: 0.001,
        slippage_rate: 0.0005,
      }
      if (mode === 'walk_forward') {
        payload.train_window = 60
        payload.test_window = 30
        payload.step = 30
      }
      if (marketType === 'futures') {
        setResult(await api.backtestFutures({
          symbol: payload.symbol, timeframe, strategy_key: strategyKey,
          initial_cash: Number(initialCash), limit: Number(limit),
          max_leverage: 10, leverage: Number(leverage), max_notional: 800,
          notional: Number(notional), entry_fill_ratio: Number(entryFillRatio),
          exit_fill_ratio: Number(exitFillRatio),
        }))
      } else {
        setResult(await api.backtestStrategy(payload))
      }
    } catch (e) {
      setError(e.message || 'تعذر تنفيذ الاختبار التاريخي')
    } finally {
      setBusy(false)
    }
  }

  async function runOrderflow() {
    setOrderflowBusy(true); setOrderflowError(''); setOrderflowResult(null)
    try {
      const start = Date.parse(orderflowStart)
      const end = Date.parse(orderflowEnd)
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) throw new Error('حدد فترة تاريخية صحيحة')
      const data = await api.backtestOrderflowBinance({
        symbol: symbol.trim().toUpperCase().replaceAll('/', ''), market_type: marketType,
        start_time: start, end_time: end, max_trades: 20000, rolling_window: 30,
        entry_imbalance: 0.15, exit_imbalance: 0, initial_cash: Number(initialCash),
        fee_rate: marketType === 'futures' ? 0.0004 : 0.001, slippage_rate: 0.0005,
        max_notional: marketType === 'futures' ? 800 : 80,
        leverage: marketType === 'futures' ? Number(leverage) : 1, max_leverage: 10,
      })
      setOrderflowResult(data)
    } catch (e) { setOrderflowError(e.message || 'تعذر اختبار Order Flow التاريخي') }
    finally { setOrderflowBusy(false) }
  }
  async function runPortfolio() {
    setPortfolioBusy(true); setPortfolioError(''); setPortfolioResult(null)
    try {
      const symbols = portfolioSymbols.split(',').map(x => x.trim().toUpperCase().replaceAll('/', '')).filter(Boolean)
      const strategies = portfolioStrategies
      const requiredSlots = symbols.length * strategies.length
      const data = await api.backtestPortfolio({
        symbols, market_type: marketType, timeframe, strategy_keys: strategies,
        initial_cash: Number(initialCash), max_positions: requiredSlots, limit: Number(limit),
        max_notional_per_position: marketType === 'futures' ? 800 : 80,
        leverage: marketType === 'futures' ? Number(leverage) : 1, max_leverage: 10,
        fee_rate: marketType === 'futures' ? 0.0004 : 0.001, slippage_rate: 0.0005,
        pyramiding_enabled: pyramidingEnabled, max_adds: Number(maxAdds),
        add_fraction: Number(addFraction), add_trigger_pct: Number(addTriggerPct),
        ...(portfolioAllocationMode === 'weighted' ? {
          strategy_allocation_weights: Object.fromEntries(
            strategies.map(key => [key, Number(portfolioWeights[key] ?? 1)])
          )
        } : {}),
        ...(portfolioAllocationMode === 'dynamic' ? {
          dynamic_allocation_enabled: true, allocation_regime: allocationRegime
        } : {}),
      })
      setPortfolioResult(data)
    } catch (e) { setPortfolioError(e.message || 'تعذر تنفيذ اختبار المحفظة') }
    finally { setPortfolioBusy(false) }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>مختبر الاستراتيجيات — Backtesting / Walk-Forward</h3>
    <div className="notice">Spot يستخدم Backtesting وWalk-Forward. Futures يستخدم بيانات USD-M العامة مع رسوم وتمويل وتعبئة جزئية وتصفية تقديرية؛ محاكاة بحثية فقط وليست مطابقة كاملة لمحرك Binance. لا توجد أوامر حقيقية.</div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>نوع السوق</label><select value={marketType} onChange={e=>{setMarketType(e.target.value); if(e.target.value==='futures') setMode('historical')}}><option value="spot">Spot</option><option value="futures">USD-M Futures</option></select></div>
      <div style={{flex:1}}><label>زوج USDT</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())}/></div>
      <div style={{flex:1}}><label>الإطار</label><select value={timeframe} onChange={e=>setTimeframe(e.target.value)}><option value="1d">1D</option><option value="4h">4H</option><option value="1h">1H</option><option value="15m">15M</option><option value="5m">5M</option></select></div>
    </div>
    <div className="row" style={{marginTop:8}}>
      <div style={{flex:1}}><label>الاستراتيجية</label><select value={strategyKey} onChange={e=>setStrategyKey(e.target.value)}>
        <option value="trend_following_ema">Trend Following EMA</option>
        <option value="momentum_macd_rsi">Momentum MACD/RSI</option>
        <option value="mean_reversion_bbands">Mean Reversion Bollinger/RSI</option>
        <option value="breakout_structure">Breakout / Market Structure</option>
        <option value="scalping_m5">Scalping M5</option>
        <option value="swing_h1_h4">Swing H1/H4</option>
      </select></div>
      <div style={{flex:1}}><label>نوع الاختبار</label><select value={mode} onChange={e=>setMode(e.target.value)} disabled={marketType==='futures'}><option value="walk_forward">Walk-Forward OOS</option><option value="historical">Historical</option></select></div>
    </div>
    {marketType === 'futures' && <div className="row" style={{marginTop:8}}>
      <div style={{flex:1}}><label>الرافعة (حتى 10x)</label><input type="number" min="1" max="10" step="1" value={leverage} onChange={e=>setLeverage(e.target.value)} /></div>
      <div style={{flex:1}}><label>القيمة الاسمية للصفقة (حد 800 USDT)</label><input type="number" min="1" max="800" step="any" value={notional} onChange={e=>setNotional(e.target.value)} /></div>
      <div style={{flex:1}}><label>نسبة تعبئة الدخول</label><input type="number" min="0" max="1" step="0.05" value={entryFillRatio} onChange={e=>setEntryFillRatio(e.target.value)} /></div>
      <div style={{flex:1}}><label>نسبة تعبئة الخروج</label><input type="number" min="0.01" max="1" step="0.05" value={exitFillRatio} onChange={e=>setExitFillRatio(e.target.value)} /></div>
    </div>}
    <div className="row" style={{marginTop:8}}>
      <div style={{flex:1}}><label>رأس المال التجريبي USDT</label><input value={initialCash} onChange={e=>setInitialCash(e.target.value)} inputMode="decimal"/></div>
      <div style={{flex:1}}><label>عدد الشموع (60–500)</label><input value={limit} onChange={e=>setLimit(e.target.value)} inputMode="numeric"/></div>
      <button type="button" onClick={run} disabled={busy}>{busy ? 'جارٍ الاختبار...' : 'تشغيل الاختبار'}</button>
    </div>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {result && <div style={{marginTop:12}}>
      <div className="row"><span className="pill">{result.validation || 'historical_simulation'}</span><span className="muted">{result.symbol} | {result.timeframe} | {result.data_source} | {result.candles} شمعة</span></div>
      <div className="grid" style={{marginTop:8}}>
        {[
          ['Trades',result.total_trades ?? result.total_trades],
          ['Win rate',result.win_rate],
          ['Profit factor',result.profit_factor ?? '—'],
          ['Expectancy',result.expectancy],
          ['Return',mode === 'walk_forward' ? result.compounded_oos_return : result.total_return],
          ['Max drawdown',mode === 'walk_forward' ? result.max_fold_drawdown : result.max_drawdown],
          ['Fees',result.fees_paid],
          ['Slippage',result.slippage_cost],
          ...(marketType === 'futures' ? [['Funding net',result.funding_net],['Liquidations',result.liquidations],['Partial fills',result.partial_fills]] : []),
        ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}
      </div>
      {result.folds?.length > 0 && <div className="table-wrap" style={{marginTop:10}}><table><thead><tr><th>Fold</th><th>Train candles</th><th>Test candles</th><th>Return</th><th>Trades</th><th>Drawdown</th></tr></thead><tbody>
        {result.folds.map(f=><tr key={f.fold}><td>{f.fold}</td><td>{f.training_candles}</td><td>{f.testing_candles}</td><td>{f.result?.total_return}</td><td>{f.result?.total_trades}</td><td>{f.result?.max_drawdown}</td></tr>)}
      </tbody></table></div>}
      <details style={{marginTop:10}}><summary>تفاصيل الاختبار</summary><pre>{JSON.stringify(result,null,2)}</pre></details>
      <div className="muted">{result.note}</div>
    </div>}
    <div className="card" style={{marginTop:14}}>
      <h3>اختبار محفظة متعددة الأزواج والاستراتيجيات</h3>
      <p className="muted">كل استراتيجية تعمل داخل حصة مستقلة لكل زوج. التوسع التدريجي يبقى داخل الحصة ولا يتجاوز سقفها.</p>
      <div className="row">
        <div style={{flex:2,minWidth:220}}><label>الأزواج (من 2 إلى 10، بفاصلة)</label><input value={portfolioSymbols} onChange={e=>setPortfolioSymbols(e.target.value)} /></div>
        <div style={{flex:2,minWidth:220}}><label>الاستراتيجيات</label><select multiple size="5" value={portfolioStrategies} onChange={e=>setPortfolioStrategies(Array.from(e.target.selectedOptions).map(o=>o.value))}>
          <option value="trend_following_ema">Trend Following EMA</option><option value="momentum_macd_rsi">Momentum MACD/RSI</option><option value="mean_reversion_bbands">Mean Reversion Bollinger</option><option value="breakout_structure">Breakout Structure</option><option value="scalping_m5">Scalping M5</option><option value="swing_h1_h4">Swing H1/H4</option>
        </select></div>
      </div>
      <div className="row" style={{marginTop:8}}>
        <div style={{flex:1}}><label>توزيع رأس المال</label><select value={portfolioAllocationMode} onChange={e=>setPortfolioAllocationMode(e.target.value)}><option value="equal">متساوٍ بين الاستراتيجيات</option><option value="weighted">توزيع بأوزان مخصصة</option><option value="dynamic">توزيع ديناميكي من صفقات Demo المغلقة</option></select></div>
      </div>
      {portfolioAllocationMode === 'dynamic' && <div className="row" style={{marginTop:8}}><div style={{flex:1}}><label>نظام السوق لنتائج الصفقات</label><select value={allocationRegime} onChange={e=>setAllocationRegime(e.target.value)}><option value="unknown">غير محدد</option><option value="trend">Trend</option><option value="range">Range</option><option value="high_volatility">High Volatility</option><option value="low_volatility">Low Volatility</option></select></div><p className="muted">يعتمد على صفقات Binance Demo المغلقة والمسجلة فقط. إذا كانت العينة أقل من 30 صفقة لكل استراتيجية، يرجع إلى التوزيع المتساوي.</p></div>}
      {portfolioAllocationMode === 'weighted' && <div className="row" style={{marginTop:8,flexWrap:'wrap'}}>
        {portfolioStrategies.map(key=><div key={key} style={{flex:1,minWidth:180}}><label>{key} — الوزن</label><input type="number" min="0.01" step="0.1" value={portfolioWeights[key] ?? '1'} onChange={e=>setPortfolioWeights(v=>({...v,[key]:e.target.value}))}/></div>)}
      </div>}
      <label style={{display:'flex',alignItems:'center',gap:8,marginTop:8}}><input type="checkbox" checked={pyramidingEnabled} onChange={e=>setPyramidingEnabled(e.target.checked)} />تفعيل التوسع التدريجي في المحاكاة</label>
      {pyramidingEnabled && <div className="row" style={{marginTop:8}}>
        <div style={{flex:1}}><label>أقصى إضافات (1–5)</label><input type="number" min="1" max="5" value={maxAdds} onChange={e=>setMaxAdds(e.target.value)} /></div>
        <div style={{flex:1}}><label>حجم الإضافة من الدخول الأول</label><input type="number" min="0.01" max="1" step="0.05" value={addFraction} onChange={e=>setAddFraction(e.target.value)} /></div>
        <div style={{flex:1}}><label>الحركة المواتية قبل الإضافة</label><input type="number" min="0.001" max="0.1" step="0.001" value={addTriggerPct} onChange={e=>setAddTriggerPct(e.target.value)} /></div>
      </div>}
      <div className="row" style={{marginTop:8}}><button type="button" onClick={runPortfolio} disabled={portfolioBusy || portfolioStrategies.length===0}>{portfolioBusy ? 'جارٍ الاختبار...' : 'تشغيل اختبار المحفظة'}</button><span className="muted">سقف الحصة: {marketType === 'futures' ? '800' : '80'} USDT</span></div>
      {portfolioError && <div className="error">{portfolioError}</div>}
      {portfolioResult && <div style={{marginTop:10}}><div className="grid">{[
        ['الحصص الناجحة',portfolioResult.sleeves_succeeded],['الصفقات',portfolioResult.total_trades],['العائد',portfolioResult.total_return],['أقصى تراجع',portfolioResult.max_drawdown],['الرصيد النهائي',portfolioResult.final_equity],['التوسع',portfolioResult.pyramiding_enabled ? 'مفعّل' : 'معطّل']
      ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}</div><details style={{marginTop:10}}><summary>تفاصيل المحفظة</summary><pre>{JSON.stringify(portfolioResult,null,2)}</pre></details></div>}
    </div>
    <div className="card" style={{marginTop:14}}>
      <h3>اختبار Order Flow التاريخي — Spot / USD-M Futures</h3>
      <p className="muted">يستخدم صفقات Binance التاريخية الفعلية ويحسب اختلال تدفق الشراء والبيع. Futures يشمل Long/Short ورسوم التمويل المتاحة؛ لا يستخدم دفتر أوامر تاريخياً.</p>
      <div className="row">
        <div style={{flex:1,minWidth:180}}><label>من تاريخ (حد أقصى 7 أيام)</label><input type="datetime-local" value={orderflowStart} onChange={e=>setOrderflowStart(e.target.value)} /></div>
        <div style={{flex:1,minWidth:180}}><label>إلى تاريخ</label><input type="datetime-local" value={orderflowEnd} onChange={e=>setOrderflowEnd(e.target.value)} /></div>
        <button type="button" onClick={runOrderflow} disabled={orderflowBusy}>{orderflowBusy ? 'جارٍ تحليل تدفق الصفقات...' : 'تشغيل Order Flow التاريخي'}</button>
      </div>
      {orderflowError && <div className="error">{orderflowError}</div>}
      {orderflowResult && <div style={{marginTop:10}}>
        <div className="grid">{[['السوق',orderflowResult.market_type],['السجلات',orderflowResult.records_fetched],['الإشارات',orderflowResult.generated_signals],['الصفقات',orderflowResult.total_trades],['العائد',orderflowResult.total_return],['أقصى تراجع',orderflowResult.max_drawdown],['رسوم التمويل',orderflowResult.funding_net ?? '—'],['عدد سجلات التمويل',orderflowResult.funding_records_fetched]].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}</div>
        <details style={{marginTop:10}}><summary>تفاصيل Order Flow</summary><pre>{JSON.stringify(orderflowResult,null,2)}</pre></details>
      </div>}
    </div>
  </div>
}
