import React, { useState } from 'react'
import { api } from './api'
import LearningTuningPanel from './LearningTuningPanel'

function Pill({ status, children }) {
  return <span className={'pill ' + (status === 'good' ? 'good' : 'warn')}>{children}</span>
}

export default function MLResearchPanel() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [busy, setBusy] = useState(false)
  const [training, setTraining] = useState(null)
  const [prediction, setPrediction] = useState(null)
  const [drift, setDrift] = useState(null)
  const [models, setModels] = useState([])
  const [outcomeStrategy, setOutcomeStrategy] = useState('trend_following_ema')
  const [strategyOutcome, setStrategyOutcome] = useState(null)
  const [error, setError] = useState('')

  async function refreshModels() {
    const result = await api.mlModels()
    setModels(result.items || [])
  }

  async function train() {
    setBusy(true); setError(''); setPrediction(null)
    try {
      const result = await api.mlTrain({ symbol, timeframe, lookahead: 1, train_ratio: 0.8, epochs: 400 })
      setTraining(result)
      await refreshModels()
    } catch (e) {
      setError(e.message || 'تعذر تدريب النموذج')
    } finally {
      setBusy(false)
    }
  }

  async function predict() {
    setBusy(true); setError('')
    try {
      setPrediction(await api.mlPredict({ symbol, timeframe }))
    } catch (e) {
      setError(e.message || 'تعذر تنفيذ التنبؤ')
    } finally {
      setBusy(false)
    }
  }

  async function evaluateDrift() {
    setBusy(true); setError('')
    try {
      setDrift(await api.mlDrift({ symbol, timeframe }))
    } catch (e) {
      setError(e.message || 'تعذر تقييم انجراف النموذج؛ قد لا تتوفر بعد بيانات كافية بعد التدريب')
    } finally {
      setBusy(false)
    }
  }

  async function evaluateStrategyOutcomes() {
    setBusy(true); setError('')
    try {
      setStrategyOutcome(await api.strategyTradeOutcomes(outcomeStrategy))
    } catch (e) {
      setError(e.message || 'تعذر تحميل نتائج الاستراتيجية التجريبية')
    } finally {
      setBusy(false)
    }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>مختبر ML — تدريب تاريخي وتنبؤ قابل للتفسير</h3>
    <div className="notice">يستخدم شموع Binance Public للقراءة فقط، مع فصل زمني بين التدريب والاختبار وتخزين النموذج محليًا. هذا نموذج بحثي تجريبي؛ لا يرسل أوامر ولا يعطي ضمانًا للربح.</div>
    <div className="row" style={{marginTop:10}}>
      <div style={{flex:1}}><label>زوج USDT Spot</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} /></div>
      <div style={{flex:1}}><label>الإطار الزمني</label><select value={timeframe} onChange={e=>setTimeframe(e.target.value)}><option value="1d">1D</option><option value="4h">4H</option><option value="1h">1H</option><option value="15m">15M</option><option value="5m">5M</option></select></div>
    </div>
    <div className="row" style={{marginTop:10}}>
      <button type="button" onClick={train} disabled={busy}>تدريب / تحديث النموذج</button>
      <button type="button" className="secondary" onClick={predict} disabled={busy}>تنبؤ بالشموع الحالية</button>
      <button type="button" className="secondary" onClick={evaluateDrift} disabled={busy}>فحص المعايرة وانجراف النموذج</button>
      <button type="button" className="secondary" onClick={()=>refreshModels().catch(e=>setError(e.message))} disabled={busy}>تحديث النماذج المحفوظة</button>
    </div>
    <div className="card" style={{marginTop:14}}>
      <h3>تعلم من نتائج صفقات Binance Demo</h3>
      <p className="muted">يعرض نتائج الصفقات التجريبية المغلقة والمؤكدة بعد خصم رسوم الدخول والخروج. لا يغير إعدادات الاستراتيجيات تلقائياً ولا يفعّل التداول الحقيقي.</p>
      <div className="row">
        <div style={{flex:1,minWidth:220}}>
          <label>الاستراتيجية</label>
          <select value={outcomeStrategy} onChange={e=>{setOutcomeStrategy(e.target.value);setStrategyOutcome(null)}}>
            <option value="trend_following_ema">Trend Following EMA</option>
            <option value="momentum_macd_rsi">Momentum MACD/RSI</option>
            <option value="mean_reversion_bbands">Mean Reversion Bollinger</option>
            <option value="breakout_structure">Breakout Structure</option>
            <option value="scalping_m5">Scalping M5</option>
            <option value="swing_h1_h4">Swing H1/H4</option>
            <option value="liquidity_orderflow">Liquidity / Order Flow</option>
            <option value="arbitrage">Triangular Arbitrage</option>
          </select>
        </div>
        <button type="button" className="secondary" onClick={evaluateStrategyOutcomes} disabled={busy}>تحليل نتائج الاستراتيجية</button>
      </div>
      {strategyOutcome && <div className="grid" style={{marginTop:10}}>
        {[
          ['الصفقات المسجلة',strategyOutcome.sample_count],
          ['نسبة الفوز',(Number(strategyOutcome.win_rate || 0)*100).toFixed(2) + '%'],
          ['صافي الربح',strategyOutcome.net_pnl],
          ['متوسط الصفقة',strategyOutcome.expectancy_per_trade],
          ['متوسط الربح',strategyOutcome.average_win],
          ['متوسط الخسارة',strategyOutcome.average_loss],
          ['Payoff Ratio',strategyOutcome.payoff_ratio ?? '—'],
          ['Profit Factor',strategyOutcome.profit_factor ?? '—'],
          ['أطول سلسلة خسائر',strategyOutcome.longest_loss_streak],
          ['Expectancy آخر 10 صفقات',strategyOutcome.recent_10_expectancy],
          ['الرسوم',strategyOutcome.fees_paid],
          ['أقصى تراجع نقدي',strategyOutcome.max_drawdown_net_pnl],
        ].map(([label,value])=><div className="card" key={label}><div className="muted">{label}</div><b>{String(value ?? '—')}</b></div>)}
      </div>}
      {strategyOutcome && <div className="notice" style={{marginTop:8}}>
        {strategyOutcome.sample_sufficient_for_research
          ? 'حجم العينة بلغ 30 صفقة أو أكثر؛ يمكن استخدام النتائج في مراجعة بحثية.'
          : 'العينة أقل من 30 صفقة؛ المؤشرات أولية ولا تكفي وحدها لتعديل الاستراتيجية.'}
      </div>}
      {strategyOutcome && <div className="grid" style={{marginTop:8}}>
        <div className="card"><b>حسب السوق</b>{Object.entries(strategyOutcome.market_breakdown || {}).map(([key,value]) =>
          <div className="muted" key={key}>{key}: {value.sample_count} صفقات | PnL {value.net_pnl} | Expectancy {value.expectancy_per_trade}</div>)}</div>
        <div className="card"><b>حسب العملة</b>{Object.entries(strategyOutcome.symbol_breakdown || {}).map(([key,value]) =>
          <div className="muted" key={key}>{key}: {value.sample_count} صفقات | PnL {value.net_pnl} | Expectancy {value.expectancy_per_trade}</div>)}</div>
      </div>}
      {strategyOutcome && <div className="muted" style={{marginTop:8}}>المصدر: {strategyOutcome.source} | بحث وتحليل فقط؛ لا يوجد تعديل تلقائي أو تغيير في إعدادات التداول.</div>}
    </div>
    <LearningTuningPanel />
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {training && <div style={{marginTop:12}}>
      <div className="row"><Pill status="good">تم التدريب</Pill><span className="muted">{training.model_version} | {training.timeframe} | {training.data_source}</span></div>
      <div className="grid" style={{marginTop:8}}>
        {Object.entries(training.metrics||{}).map(([key,value])=><div className="card" key={key}><div className="muted">{key}</div><b>{String(value)}</b></div>)}
      </div>
      <div className="muted">عينات التدريب: {training.training_samples} | الاختبار: {training.test_samples} | سجل محفوظ: {training.record_id}</div>
      <div className="muted">{training.note}</div>
    </div>}
    {drift && <div className="card" style={{marginTop:12}}>
      <div className="row"><Pill status={drift.drift_detected ? 'warn' : 'good'}>{drift.status}</Pill><b>عينات التقييم اللاحقة للتدريب: {drift.samples}</b></div>
      <div className="grid" style={{marginTop:8}}>
        <div className="card"><div className="muted">دقة الفترة الأخيرة</div><b>{drift.recent?.accuracy}</b></div>
        <div className="card"><div className="muted">Brier Score</div><b>{drift.recent?.brier_score}</b></div>
        <div className="card"><div className="muted">Expected Calibration Error</div><b>{drift.recent?.expected_calibration_error}</b></div>
      </div>
      <div className="muted">لم يتغير النموذج ولم تتم إعادة تدريبه تلقائيًا. سجل التقييم: {drift.record_id}</div>
      <div className="muted">{drift.note}</div>
    </div>}
    {prediction && <div style={{marginTop:12}}>
      <div className="row"><Pill status="warn">تقدير النموذج</Pill><b>{prediction.predicted_direction?.toUpperCase()}</b><span>احتمال الصعود: {prediction.prob_up}</span><span>احتمال الهبوط: {prediction.prob_down}</span></div>
      <pre>{JSON.stringify(prediction.feature_contributions,null,2)}</pre>
      <div className="muted">{prediction.note}</div>
    </div>}
    <div style={{marginTop:12}}><b>النماذج المحفوظة</b> ({models.length})</div>
    {models.length > 0 && <div className="table-wrap"><table><thead><tr><th>النموذج</th><th>الحالة</th><th>تاريخ الحفظ</th><th>المقاييس</th></tr></thead><tbody>
      {models.map(item=><tr key={item.model_key}><td>{item.model_key}</td><td>{item.outcome}</td><td>{String(item.created_at)}</td><td>{JSON.stringify(item.metrics)}</td></tr>)}
    </tbody></table></div>}
  </div>
}
