import React, { useState } from 'react'
import { api } from './api'

export default function PerformancePanel() {
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [predictions, setPredictions] = useState([])
  const [predictionBusy, setPredictionBusy] = useState(false)
  const [predictionMessage, setPredictionMessage] = useState('')
  const [learningStrategy, setLearningStrategy] = useState('trend_following_ema')
  const [learning, setLearning] = useState(null)
  const [learningBusy, setLearningBusy] = useState(false)
  const [learningError, setLearningError] = useState('')

  async function refresh() {
    setBusy(true); setError('')
    try { setResult(await api.performance('demo', 'spot')) }
    catch (e) { setError(e.message || 'تعذر حساب الأداء') }
    finally { setBusy(false) }
  }
  async function evaluatePredictions() {
    setPredictionBusy(true); setPredictionMessage('')
    try {
      const result = await api.evaluatePredictions({ environment_mode: 'demo', horizon_seconds: 3600, max_records: 200 })
      const history = await api.predictions(100)
      setPredictions(history.items || [])
      setPredictionMessage('تم تقييم ' + result.evaluated + ' قرار ناضج؛ تم تجاوز ' + (result.skipped?.length || 0) + '.')
    } catch (e) {
      setPredictionMessage(e.message || 'تعذر تقييم التنبؤات')
    } finally {
      setPredictionBusy(false)
    }
  }


  async function refreshLearning() {
    setLearningBusy(true); setLearningError('')
    try { setLearning(await api.strategyTradeOutcomes(learningStrategy)) }
    catch (e) { setLearningError(e.message || 'تعذر تحميل تحليلات التعلم') }
    finally { setLearningBusy(false) }
  }

  async function refreshPredictions() {
    setPredictionBusy(true)
    try { const history = await api.predictions(100); setPredictions(history.items || []) }
    catch (e) { setPredictionMessage(e.message || 'تعذر تحميل سجل التنبؤات') }
    finally { setPredictionBusy(false) }
  }

  return <div className="card" style={{marginTop:12}}>
    <div className="row"><h3>تقييم الأداء — Demo/Shadow فقط</h3><button type="button" className="secondary" onClick={refresh} disabled={busy}>{busy ? 'جارٍ الحساب...' : 'تحديث الأداء'}</button></div>
    {error && <div className="error">{error}</div>}
    {result && <>
      <div className="grid" style={{marginTop:10}}>
        {[
          ['Equity', result.current_equity_estimate],
          ['Net return %', result.net_return_percent],
          ['Realized PnL', result.realized_pnl_net],
          ['Unrealized PnL', result.unrealized_pnl],
          ['Win rate', result.win_rate],
          ['Profit factor', result.profit_factor ?? '—'],
          ['Max drawdown', result.max_drawdown_from_snapshots ?? '—'],
          ['Fees', result.total_fees_paid],
          ['Exit fills', result.realized_exit_fills],
        ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value)}</b></div>)}
      </div>
      <div className="muted" style={{marginTop:8}}>النطاق: {result.scope?.environment_mode}/{result.scope?.market_type} | المصدر: {result.data_scope}</div>
      <details style={{marginTop:10}}><summary>تفاصيل الصفقات والمراكز المفتوحة</summary><pre>{JSON.stringify({realized_fills:result.realized_fills,open_positions:result.open_positions},null,2)}</pre></details>
      <div className="muted">{result.note}</div>
    </>}
    <div className="divider" style={{marginTop:14}} />
    <div className="row"><h4>تتبع نتائج قرارات التحليل</h4><button type="button" className="secondary" onClick={evaluatePredictions} disabled={predictionBusy}>{predictionBusy ? 'جارٍ التقييم...' : 'تقييم القرارات الناضجة'}</button><button type="button" className="secondary" onClick={refreshPredictions} disabled={predictionBusy}>تحديث السجل</button></div>
    <div className="muted">الأفق الافتراضي ساعة واحدة. التقييم مقارنة سعرية عامة بعد الأفق، وليس ربحًا محققًا أو نتيجة وقف/هدف.</div>
    {predictionMessage && <div className="notice">{predictionMessage}</div>}
    {predictions.length > 0 && <div className="table-wrap"><table><thead><tr><th>القرار</th><th>العملة</th><th>النوع</th><th>النتيجة</th><th>العائد الاتجاهي %</th><th>السعر عند التقييم</th></tr></thead><tbody>
      {predictions.map(item=><tr key={item.id}><td>{item.decision_id}</td><td>{item.evidence?.symbol}</td><td>{item.evidence?.action}</td><td>{item.outcome}</td><td>{item.evidence?.directional_return_percent}</td><td>{item.evidence?.evaluation_price}</td></tr>)}
    </tbody></table></div>}
    <div className="divider" style={{marginTop:14}} />
    <div className="row"><h4>تعلم الاستراتيجيات من الصفقات المغلقة</h4><select value={learningStrategy} onChange={e=>setLearningStrategy(e.target.value)}>
      <option value="trend_following_ema">Trend Following EMA</option><option value="momentum_macd_rsi">Momentum MACD/RSI</option><option value="mean_reversion_bbands">Mean Reversion</option><option value="breakout_structure">Breakout Structure</option><option value="scalping_m5">Scalping M5</option><option value="swing_h1_h4">Swing H1/H4</option><option value="liquidity_orderflow">Order Flow</option><option value="arbitrage">Arbitrage</option>
    </select><button type="button" className="secondary" onClick={refreshLearning} disabled={learningBusy}>{learningBusy?'جارٍ التحليل...':'تحليل نتائج الاستراتيجية'}</button></div>
    <div className="muted">المصدر صفقات Demo المغلقة والمؤكدة فقط. التوصيات بحثية ولا تغيّر إعدادات الاستراتيجية تلقائياً.</div>
    {learningError&&<div className="error">{learningError}</div>}
    {learning&&<><div className="grid" style={{marginTop:8}}>{[
      ['العينات',learning.sample_count],['نسبة الفوز',learning.win_rate],['Expectancy',learning.expectancy_per_trade],['Profit factor',learning.profit_factor],['Payoff ratio',learning.payoff_ratio],['أطول سلسلة خسائر',learning.longest_loss_streak],['آخر 10 صفقات',learning.recent_10_expectancy],['التراجع',learning.max_drawdown_net_pnl]
    ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value??'—')}</b></div>)}</div>
    <div className="notice" style={{marginTop:8}}><b>{learning.research_guidance?.status||'—'}</b><p>{learning.research_guidance?.message||'لا توجد توصية بحثية.'}</p></div>
    <details style={{marginTop:8}}><summary>تفصيل النتائج حسب ظروف السوق والفريم</summary><pre>{JSON.stringify({market:learning.market_breakdown,symbols:learning.symbol_breakdown,regimes:learning.regime_breakdown,timeframes:learning.timeframe_breakdown},null,2)}</pre></details></>}
  </div>
}
