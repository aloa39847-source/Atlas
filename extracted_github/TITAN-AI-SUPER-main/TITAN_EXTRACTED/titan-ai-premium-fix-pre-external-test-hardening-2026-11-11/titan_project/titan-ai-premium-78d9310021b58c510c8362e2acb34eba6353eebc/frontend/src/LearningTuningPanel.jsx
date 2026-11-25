import React, { useState } from 'react'
import { api } from './api'

const PARAM_PRESETS = {
  trend_following_ema: [{ min_confirmations: 2 }, { min_confirmations: 3 }],
  momentum_macd_rsi: [
    { rsi_buy_min: 50, rsi_buy_max: 70, rsi_sell_min: 30, rsi_sell_max: 50 },
    { rsi_buy_min: 55, rsi_buy_max: 72, rsi_sell_min: 28, rsi_sell_max: 45 },
  ],
  mean_reversion_bbands: [
    { require_oscillator_confirmation: true },
    { require_oscillator_confirmation: false },
  ],
  breakout_structure: [
    { bullish_close_strength: 0.70, bearish_close_strength: 0.30 },
    { bullish_close_strength: 0.78, bearish_close_strength: 0.22 },
  ],
  scalping_m5: [
    { require_volume_or_pattern: true },
    { require_volume_or_pattern: false },
  ],
  swing_h1_h4: [
    { require_structure_bias: true },
    { require_structure_bias: false },
  ],
}

export default function LearningTuningPanel() {
  const [strategyKey, setStrategyKey] = useState('trend_following_ema')
  const [currentParams, setCurrentParams] = useState(JSON.stringify(PARAM_PRESETS.trend_following_ema[0], null, 2))
  const [proposedParams, setProposedParams] = useState(JSON.stringify(PARAM_PRESETS.trend_following_ema[1], null, 2))
  const [rationale, setRationale] = useState('')
  const [proposalId, setProposalId] = useState('')
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [limit, setLimit] = useState('500')
  const [trainWindow, setTrainWindow] = useState('150')
  const [testWindow, setTestWindow] = useState('50')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function changeStrategy(next) {
    setStrategyKey(next)
    setProposalId('')
    setCurrentParams(JSON.stringify(PARAM_PRESETS[next][0], null, 2))
    setProposedParams(JSON.stringify(PARAM_PRESETS[next][1], null, 2))
    if (next === 'scalping_m5') setTimeframe('5m')
    else if (next === 'swing_h1_h4' && !['1h', '4h'].includes(timeframe)) setTimeframe('1h')
    setResult(null)
  }

  const parse = (value, label) => {
    try { return JSON.parse(value) } catch { throw new Error('صيغة JSON غير صحيحة: ' + label) }
  }

  async function createProposal() {
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await api.strategyProposalCreate({
        strategy_key: strategyKey,
        current_params: parse(currentParams, 'الإعدادات الحالية'),
        proposed_params: parse(proposedParams, 'الإعدادات المقترحة'),
        metrics_before: {},
        rationale,
      })
      setProposalId(String(response.proposal_id))
      setResult(response)
    } catch (e) { setError(e.message || 'تعذر إنشاء مقترح التعلم') }
    finally { setBusy(false) }
  }

  async function validate() {
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await api.strategyProposalHistoricalWalkForward({
        proposal_id: Number(proposalId),
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        limit: Number(limit),
        train_window: Number(trainWindow),
        test_window: Number(testWindow),
        step: Number(testWindow),
      })
      setResult(response)
    } catch (e) { setError(e.message || 'فشل اختبار Walk-Forward التاريخي') }
    finally { setBusy(false) }
  }

  async function applyPaper() {
    setBusy(true); setError('')
    try {
      const response = await api.strategyProposalApplyPaper({
        proposal_id: Number(proposalId), explicit_approval: true,
      })
      setResult(response)
    } catch (e) { setError(e.message || 'تعذر اعتماد نسخة Paper') }
    finally { setBusy(false) }
  }

  return <div className="card" style={{marginTop:14}}>
    <h3>مختبر تطوير الاستراتيجية — اختبار تاريخي Walk-Forward</h3>
    <p className="muted">يختبر الإعدادات الحالية والمقترحة تلقائياً على نفس شموع Binance Spot العامة، مع رسوم وانزلاق ومحاكاة دخول عند افتتاح الشمعة التالية. آخر طية Holdout مستقلة. يتطلب 3 طيات على الأقل و30 شمعة اختبار في كل طية. لا توجد أوامر حقيقية ولا تفعيل تلقائي.</p>
    <div className="row">
      <div style={{flex:1,minWidth:180}}><label>الاستراتيجية</label><select value={strategyKey} onChange={e=>changeStrategy(e.target.value)}>
        <option value="trend_following_ema">Trend Following EMA</option>
        <option value="momentum_macd_rsi">Momentum MACD/RSI</option>
        <option value="mean_reversion_bbands">Mean Reversion</option>
        <option value="breakout_structure">Breakout Structure</option>
        <option value="scalping_m5">Scalping M5</option>
        <option value="swing_h1_h4">Swing H1/H4</option>
      </select></div>
      <div style={{flex:1,minWidth:150}}><label>رقم المقترح</label><input type="number" min="1" value={proposalId} onChange={e=>setProposalId(e.target.value)} placeholder="يظهر بعد الإنشاء"/></div>
    </div>
    <div className="row">
      <div style={{flex:1,minWidth:160}}><label>الزوج</label><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())}/></div>
      <div style={{flex:1,minWidth:120}}><label>الإطار</label><select value={timeframe} onChange={e=>setTimeframe(e.target.value)}>
        <option value="1d">1D</option><option value="4h">4H</option><option value="1h">1H</option><option value="15m">15M</option><option value="5m">5M</option>
      </select></div>
      <div style={{flex:1,minWidth:100}}><label>عدد الشموع</label><input type="number" min="120" max="500" value={limit} onChange={e=>setLimit(e.target.value)}/></div>
    </div>
    <div className="row">
      <div style={{flex:1}}><label>شموع التدريب</label><input type="number" min="50" max="400" value={trainWindow} onChange={e=>setTrainWindow(e.target.value)}/></div>
      <div style={{flex:1}}><label>شموع الاختبار لكل طية</label><input type="number" min="30" max="200" value={testWindow} onChange={e=>setTestWindow(e.target.value)}/></div>
    </div>
    <label>الإعدادات الحالية JSON</label><textarea rows={2} value={currentParams} onChange={e=>setCurrentParams(e.target.value)}/>
    <label>الإعدادات المقترحة JSON</label><textarea rows={2} value={proposedParams} onChange={e=>setProposedParams(e.target.value)}/>
    <label>مبرر المقترح</label><input value={rationale} onChange={e=>setRationale(e.target.value)} placeholder="الفرضية التي تريد اختبارها"/>
    <button type="button" onClick={createProposal} disabled={busy || !rationale.trim()}>إنشاء مقترح بحثي</button>
    <div className="row" style={{marginTop:8}}>
      <button type="button" className="secondary" onClick={validate} disabled={busy || !proposalId}>تشغيل المقارنة التاريخية Walk-Forward</button>
      <button type="button" className="secondary" onClick={applyPaper} disabled={busy || !proposalId || result?.walk_forward_validated !== true}>اعتماد نسخة Paper بعد نجاح الاختبار</button>
    </div>
    {error && <div className="error">{error}</div>}
    {result && <div className="notice" style={{marginTop:8}}><pre>{JSON.stringify(result, null, 2)}</pre></div>}
  </div>
}
