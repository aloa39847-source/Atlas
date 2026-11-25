import React, { useEffect, useMemo, useState } from 'react'
import { api, setToken, getToken } from './api'
import SmartScanner from './SmartScanner'
import MLResearchPanel from './MLResearchPanel'
import MarketMicrostructurePanel from './MarketMicrostructurePanel'
import PerformancePanel from './PerformancePanel'
import TelegramPanel from './TelegramPanel'
import BacktestPanel from './BacktestPanel'
import RiskSimulationPanel from './RiskSimulationPanel'
import FuturesOrderbookHistoryPanel from './FuturesOrderbookHistoryPanel'
import ArbitragePanel from './ArbitragePanel'
import FundamentalsPanel from './FundamentalsPanel'
import EconomicCalendarPanel from './EconomicCalendarPanel'

const SECTIONS = [
  'Main Dashboard','Control Center','Live Market Room','Trade Management','AI Learning','Self-Evolution','Discoveries',
  'Knowledge Center','Brain Monitor','Performance','Strategy Performance','Market Scanner','Risk Center','Advanced Configuration',
  'Spot/Futures/Earn Selector','Binance Connection','Demo/Testnet Safety','Telegram Connection','Login & Security',
  'Time & Activity','System Age & Maturity','System Health','News & Market','Prediction Tracker','Logs & Reports'
]

function Pill({ status, children }) {
  const cls = ['ready','implemented_partial','passed','running'].includes(status) ? 'good' : (['needs_external_service','warning','not_implemented','planned','stopped','waiting_data'].includes(status) ? 'warn' : (['error','failed','emergency'].includes(status) ? 'bad' : ''))
  return <span className={`pill ${cls}`}>{children || status}</span>
}

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [status, setStatus] = useState(null)
  const [auth, setAuth] = useState({ loading: true, status: null, me: null })
  const [loginForm, setLoginForm] = useState({ email: '', password: '', totp_code: '' })
  const [strategies, setStrategies] = useState([])
  const [decisions, setDecisions] = useState([])
  const [audit, setAudit] = useState([])
  const [knowledge, setKnowledge] = useState([])
  const [learning, setLearning] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [analysis, setAnalysis] = useState(null)
  const [chat, setChat] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [aiResearch, setAiResearch] = useState(null)
  const [enginesDetailed, setEnginesDetailed] = useState([])
  const [binanceTestnet, setBinanceTestnet] = useState(null)
  const [binanceLive, setBinanceLive] = useState(null)
  const [binanceLiveCreds, setBinanceLiveCreds] = useState({configured:false, market:'spot'})
  const [binanceCreds, setBinanceCreds] = useState({configured:false, market:'spot'})
  const [binanceForm, setBinanceForm] = useState({api_key:'', api_secret:'', market:'spot'})
  const [binanceMsg, setBinanceMsg] = useState('')
  const [binanceOrderForm, setBinanceOrderForm] = useState({market:'spot'})
  const [binanceAccount, setBinanceAccount] = useState(null)
  const [binanceOrders, setBinanceOrders] = useState([])
  const [knowledgeForm, setKnowledgeForm] = useState({title:'', source_type:'manual', content:'', url:'', reliability_score:0.8})
  const [knowledgeMsg, setKnowledgeMsg] = useState('')
  const [learningForm, setLearningForm] = useState({category:'general', subject:'', outcome:'', verified:false})
  const [learningMsg, setLearningMsg] = useState('')
  const [aiProviderForm, setAiProviderForm] = useState({provider:'openai', name:'', model:'gpt-4o-mini', api_key:'', api_key_ref:'env:OPENAI_API_KEY', base_url:'', enabled:true})
  const [aiProviderMsg, setAiProviderMsg] = useState('')
  const [aiProviders, setAiProviders] = useState([])
  const [shadowForm, setShadowForm] = useState({environment_mode:'demo', market_type:'spot', symbol:'BTC/USDT', side:'buy', order_type:'limit', quantity:1, price:100, leverage:1, simulate:'fill', fill_ratio:1})
  const [shadowMsg, setShadowMsg] = useState('')
  const [shadowResult, setShadowResult] = useState(null)
  const [protectionForm, setProtectionForm] = useState({position_id:'', stop_loss:'', take_profit:'', trailing_stop_pct:''})
  const [portfolio, setPortfolio] = useState(null)
  const [diagnosis, setDiagnosis] = useState(null)
  const [diagnosisMsg, setDiagnosisMsg] = useState('')
  const [form, setForm] = useState({ symbol:'BTC/USDT', market_type:'spot', environment_mode:'demo', balance:150, max_leverage:1, risk_per_trade:0.005, data_source:'binance_public' })

  async function loadAuth() {
    try {
      const st = await api.authStatus()
      let me = null
      try { me = await api.me() } catch {}
      setAuth({ loading: false, status: st, me })
      return { st, me }
    } catch (e) {
      setAuth({ loading: false, status: null, me: null, error: e.message })
      return null
    }
  }

  async function refresh() {
    setError('')
    try {
      const a = await loadAuth()
      const [s, st, d, au, k, l] = await Promise.all([api.status(), api.strategies(), api.decisions(), api.audit(), api.knowledge(), api.learning()])
      setStatus(s); setStrategies(st.strategies || []); setDecisions(d.items || []); setAudit(au.items || []); setKnowledge(k.items || []); setLearning(l.items || [])
      if (s?.state) {
        setForm(f => ({
          ...f,
          market_type: s.state.market_type,
          environment_mode: s.state.environment_mode,
          max_leverage: s.state.market_type === 'spot' ? 1 : (Number(f.max_leverage) > 1 ? f.max_leverage : 3),
        }))
        setShadowForm(f => ({
          ...f,
          market_type: s.state.market_type,
          environment_mode: s.state.environment_mode,
          side: s.state.market_type === 'futures'
            ? (f.side === 'sell' || f.side === 'short' ? 'short' : 'long')
            : (f.side === 'short' || f.side === 'long' ? 'buy' : f.side),
          leverage: s.state.market_type === 'spot' ? 1 : (Number(f.leverage) > 1 ? f.leverage : 3),
        }))
      }
      try { setAiResearch(await api.aiResearch()) } catch {}
      try { const ed = await api.enginesDetailed(); setEnginesDetailed(ed.engines || []) } catch {}
      try { setBinanceTestnet(await api.binanceTestnetStatus(s?.state?.market_type || 'spot')) } catch {}
      try { setBinanceLive(await api.binanceLiveStatus(s?.state?.market_type || 'spot')) } catch {}
      try { setBinanceLiveCreds(await api.binanceLiveCreds(s?.state?.market_type || 'spot')) } catch {}
      try { setBinanceCreds(await api.binanceTestnetCreds()) } catch {}
      try { const ap = await api.aiProviders(); setAiProviders(ap.items || []) } catch {}
      try { await refreshPortfolio(s?.state?.market_type, s?.state?.environment_mode) } catch {}
    } catch (e) { setError(`تعذر الاتصال بالخلفية أو حدث خطأ: ${e.message}. تأكد من تشغيل TITAN backend وأن /api متاح.`) }
  }
  useEffect(() => { refresh() }, [])

  const needsLogin = auth.status?.auth_required && !auth.me?.authenticated

  async function onLogin(e) {
    e.preventDefault(); setBusy(true); setError('')
    try {
      const res = await api.login(loginForm)
      if (res.token) setToken(res.token)
      setLoginForm(f => ({ ...f, password: '', totp_code: '' }))
      await refresh()
    } catch (e2) { setError(e2.message) } finally { setBusy(false) }
  }
  async function onLogout() {
    try { await api.logout() } catch {}
    setToken(''); await refresh()
  }
  async function runAnalysis(e) {
    e.preventDefault(); setBusy(true); setError('')
    try { const res = await api.analyze({ ...form, balance: Number(form.balance), max_leverage: Number(form.max_leverage), risk_per_trade: Number(form.risk_per_trade) }); setAnalysis(res); await refresh() }
    catch (e2) { setError(e2.message) } finally { setBusy(false) }
  }
  async function setStatePatch(patch) {
    setBusy(true); setError('')
    try { await api.setState(patch); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function selectMarket(market) {
    if (!['spot', 'futures'].includes(market)) return
    setForm(current => ({ ...current, market_type: market }))
    setShadowForm(current => ({
      ...current,
      market_type: market,
      side: market === 'futures' ? (current.side === 'short' ? 'short' : 'long') : 'buy',
      leverage: market === 'futures' ? Math.min(10, Math.max(1, Number(current.leverage) || 3)) : 1,
    }))
    await setStatePatch({ market_type: market })
  }
  async function master(action) {
    setBusy(true); setError('')
    try {
      if (action === 'start') await api.masterStart({
        account_target: status?.state?.account_target || 'demo',
      })
      if (action === 'stop') await api.masterStop({ reason: 'user_stop', close_positions: false })
      if (action === 'emergency') await api.emergencyStop()
      await refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function resetEmergency() {
    if (!window.confirm('إعادة ضبط الطوارئ فقط بعد فحص البيانات والأوامر المعلّقة. لن يبدأ البوت تلقائياً. هل تريد المتابعة؟')) return
    setBusy(true); setError('')
    try { await api.emergencyReset(); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function sendChat(e) {
    e.preventDefault(); if (!chatInput.trim()) return
    const msg = chatInput; setChatInput(''); setChat(c => [...c, { role:'user', text: msg }])
    try { const r = await api.chat({ message: msg }); setChat(c => [...c, { role:'assistant', text: r.reply, grounded: r.grounded }]) }
    catch (e2) { setChat(c => [...c, { role:'assistant', text: `خطأ: ${e2.message}` }]) }
  }


  async function saveBinance(e) {
    e.preventDefault(); setBusy(true); setBinanceMsg('')
    try {
      const res = await api.binanceTestnetSave({api_key: binanceForm.api_key, api_secret: binanceForm.api_secret, market: binanceForm.market})
      setBinanceMsg(res.ok ? 'تم الحفظ واختبار الاتصال' : ('خطأ: ' + (res.error||'')))
      setBinanceForm(f=>({...f, api_key:'', api_secret:''}))
      await refresh()
      setBinanceTestnet(res.test_status || await api.binanceTestnetStatus(binanceForm.market))
    } catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function testBinance() {
    setBusy(true); setBinanceMsg('')
    try { const r = await api.binanceTestnetStatus(binanceForm.market); setBinanceTestnet(r); setBinanceMsg(r.connected ? 'الاتصال ناجح' : 'غير متصل - تحقق من المفاتيح') }
    catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function deleteBinance() {
    if(!confirm('حذف مفاتيح Testnet؟')) return
    setBusy(true)
    try { await api.binanceTestnetDelete(); await refresh(); setBinanceMsg('تم الحذف') } catch(e2){ setBinanceMsg(e2.message) } finally { setBusy(false) }
  }
  async function deleteLiveCredentials() {
    if(!confirm('حذف جميع مفاتيح Live القديمة (Spot وFutures) من تخزين TITAN؟')) return
    setBusy(true); setBinanceMsg('')
    try {
      await Promise.all(['spot','futures'].map(market=>api.binanceLiveDelete(market)))
      await refresh(); setBinanceMsg('تم حذف مفاتيح Live القديمة لكلا السوقين')
    } catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function fetchBinanceAccount() {
    setBusy(true); setBinanceMsg('')
    try { const r = await api.binanceTestnetAccount(binanceOrderForm.market); setBinanceAccount(r) } catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function fetchBinanceOrders() {
    setBusy(true); setBinanceMsg('')
    try { const r = await api.binanceTestnetOrders(binanceOrderForm.market); setBinanceOrders(r.orders || r || []) } catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function reconcileBinance() {
    setBusy(true); setBinanceMsg('')
    try { const r = await api.binanceTestnetReconcile(binanceOrderForm.market); setBinanceMsg(JSON.stringify(r)) } catch(e2){ setBinanceMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function addKnowledge(e) {
    e.preventDefault(); setBusy(true); setKnowledgeMsg('')
    try { await api.addKnowledge({title: knowledgeForm.title, source_type: knowledgeForm.source_type, notes: knowledgeForm.content, url: knowledgeForm.url, reliability_score: Number(knowledgeForm.reliability_score)}); setKnowledgeMsg('تمت الإضافة'); setKnowledgeForm(f=>({...f, title:'', content:''})); await refresh() } catch(e2){ setKnowledgeMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }

  async function verifyKnowledgeSource(sourceId) {
    setBusy(true); setKnowledgeMsg('')
    try {
      const result = await api.verifyKnowledge({source_id: sourceId})
      setKnowledgeMsg(result.source_verified
        ? 'تم التحقق من وصول المصدر وفحص صلة النص؛ لم يتم اعتماد صحة الادعاء تلقائياً.'
        : 'تعذر التحقق من المصدر: ' + (result.error || 'سبب غير معروف'))
      await refresh()
    } catch(e2) {
      setKnowledgeMsg('خطأ في التحقق: ' + e2.message)
    } finally { setBusy(false) }
  }
  async function addLearning(e) {
    e.preventDefault(); setBusy(true); setLearningMsg('')
    try { await api.addLearning({category: learningForm.category, subject: learningForm.subject, outcome: learningForm.outcome, verified: learningForm.verified}); setLearningMsg('تمت الإضافة'); setLearningForm(f=>({...f, subject:'', outcome:''})); await refresh() } catch(e2){ setLearningMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function createAiProvider(e) {
    e.preventDefault(); setBusy(true); setAiProviderMsg('')
    try { const key = aiProviderForm.api_key.trim(); await api.aiCreateProvider({provider: aiProviderForm.provider, name: aiProviderForm.name, model: aiProviderForm.model, ...(key ? {api_key:key} : {api_key_ref:aiProviderForm.api_key_ref}), base_url: aiProviderForm.base_url, enabled: aiProviderForm.enabled}); setAiProviderMsg('تمت الإضافة؛ المفتاح يُحفظ مشفراً ولا يظهر مجدداً'); setAiProviderForm(f=>({...f, name:'', api_key:'', base_url:''})); await refresh() } catch(e2){ setAiProviderMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function testAiProvider(id) {
    setBusy(true); setAiProviderMsg('')
    try { const r = await api.aiTestProvider(id); setAiProviderMsg(JSON.stringify(r)) } catch(e2){ setAiProviderMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function runDemoCycle() {
    setBusy(true); setShadowMsg('')
    try {
      const selectedMarket = status?.state?.market_type || form.market_type || 'spot'
      const r = await api.demoCycle({environment_mode:'demo', market_type:selectedMarket, symbol:shadowForm.symbol, execute_shadow:true})
      setShadowResult(r)
      setShadowMsg(r.analysis_action === 'wait' ? 'لا توجد إشارة دخول آمنة؛ لم تُفتح صفقة.' : (r.executed ? 'تم تنفيذ محاكاة Shadow داخلية.' : 'لم تُنفذ صفقة؛ راجع نتيجة الفحص.'))
      await refreshPortfolio()
    } catch(e2) { setShadowMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }

  async function submitShadowOrder(e) {
    e.preventDefault(); setBusy(true); setShadowMsg('')
    try { const r = await api.shadowOrder({environment_mode: shadowForm.environment_mode, market_type: shadowForm.market_type, symbol: shadowForm.symbol, side: shadowForm.side, order_type: shadowForm.order_type, quantity: Number(shadowForm.quantity), price: Number(shadowForm.price), leverage: Number(shadowForm.leverage), simulate: shadowForm.simulate, fill_ratio: Number(shadowForm.fill_ratio)}); setShadowResult(r); setShadowMsg(r.ok ? 'تم' : 'فشل'); await refreshPortfolio() } catch(e2){ setShadowMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function closeShadowPosition(e) {
    e.preventDefault(); if(!confirm('إغلاق مركز Shadow؟')) return
    setBusy(true); setShadowMsg('')
    try { const r = await api.shadowClose({position_id: Number(shadowForm.position_id), close_price: Number(shadowForm.close_price), fee_rate: 0.001}); setShadowMsg(JSON.stringify(r)); await refreshPortfolio() } catch(e2){ setShadowMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function saveShadowProtection(e) {
    e.preventDefault(); setBusy(true); setShadowMsg('')
    try {
      const r = await api.shadowProtection({
        position_id: Number(protectionForm.position_id),
        stop_loss: protectionForm.stop_loss === '' ? null : Number(protectionForm.stop_loss),
        take_profit: protectionForm.take_profit === '' ? null : Number(protectionForm.take_profit),
        trailing_stop_pct: protectionForm.trailing_stop_pct === '' ? null : Number(protectionForm.trailing_stop_pct),
      })
      setShadowMsg(JSON.stringify(r)); await refreshPortfolio()
    } catch(e2) { setShadowMsg('خطأ: '+e2.message) } finally { setBusy(false) }
  }
  async function refreshPortfolio(marketOverride, environmentOverride) {
    const market = marketOverride || status?.state?.market_type || shadowForm.market_type
    const environment = environmentOverride || status?.state?.environment_mode || shadowForm.environment_mode
    try { const r = await api.portfolio(environment, market); setPortfolio(r) } catch(e2){ setPortfolio(null) }
  }

  if (needsLogin) {
    return <div className="app" dir="rtl">
      <div className="card" style={{maxWidth:460, margin:'24px auto'}}>
        <h1>TITAN AI PREMIUM</h1>
        <div className="sub">تسجيل دخول خاص — الوصول مسموح للحساب المصرّح به فقط.</div>
        {error && <div className="error" style={{marginTop:10}}>{error}</div>}
        <form onSubmit={onLogin} style={{marginTop:12}}>
          <label>البريد الإلكتروني</label>
          <input value={loginForm.email} onChange={e=>setLoginForm({...loginForm,email:e.target.value})} autoComplete="username" />
          <label>كلمة المرور</label>
          <input type="password" value={loginForm.password} onChange={e=>setLoginForm({...loginForm,password:e.target.value})} autoComplete="current-password" />
          <label>رمز المصادقة الثنائية (إذا كانت مفعّلة)</label>
          <input value={loginForm.totp_code} onChange={e=>setLoginForm({...loginForm,totp_code:e.target.value.replace(/\D/g,'').slice(0,6)})} inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="000000" />
          <div style={{marginTop:10}}><button disabled={busy}>{busy?'جارٍ الدخول...':'تسجيل الدخول'}</button></div>
        </form>
        <div className="notice" style={{marginTop:12}}>
          إعداد كلمة المرور الأولى يتم محليًا وبشكل آمن عبر سكربت الخادم (لا تُرسل كلمة المرور في المحادثة). راجع <code>backend/app/scripts/set_initial_password.py</code>.
        </div>
      </div>
    </div>
  }

  return <div className="app" dir="rtl">
    <div className="header">
      <div>
        <h1>TITAN AI PREMIUM</h1>
        <div className="sub">لوحة تحكم تأسيسية متكاملة — بيانات/حالات حقيقية فقط؛ التحليل الحالي Synthetic/Labeled ما لم يُربط مزود حي.</div>
      </div>
      <div className="row">
        <Pill status={status ? 'good' : 'warn'}>{status ? 'API متصل' : 'API غير متصل'}</Pill>
        {auth.me?.authenticated && <Pill status="good">{auth.me.email}</Pill>}
        <button className="secondary" onClick={refresh} disabled={busy}>تحديث</button>
        {(auth.me?.authenticated || getToken()) && <button className="secondary" onClick={onLogout}>تسجيل الخروج</button>}
      </div>
    </div>

    {error && <div className="error" style={{marginBottom:10}}>{error}</div>}
    <div className="notice" style={{marginBottom:10}}>
      الأمان: عامل التشغيل التلقائي يعمل على Demo/Shadow فقط. Testnet للاتصال والفحص وقراءة الحساب والأوامر المفتوحة فقط؛ إرسال الأوامر اليدوية والآلية متوقف حالياً. Live محظور ولا تُقبل مفاتيحه. لا سحب أو تحويل.
    </div>

    <div className="card" style={{marginBottom:10}}>
      <h3>TITAN MASTER CONTROL</h3>
      <div className="row" style={{marginBottom:8}}>
        <span>حالة البوت: <Pill status={status?.state?.bot_run_state||'stopped'}>{status?.state?.bot_run_state||'stopped'}</Pill></span>
        <span>الحساب: <Pill status="warn">{status?.state?.account_target||status?.state?.environment_mode||'demo'}</Pill></span>
        <span>السوق: <Pill status="good">{status?.state?.market_type||'spot'}</Pill></span>
        <span>طوارئ: <Pill status={status?.state?.emergency_stop?'bad':'good'}>{String(!!status?.state?.emergency_stop)}</Pill></span>
      </div>
      <div className="row">
        <button onClick={()=>master('start')} disabled={busy || status?.state?.account_target !== 'demo' || ['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}>{status?.state?.account_target === 'testnet' ? 'التداول الآلي Testnet متوقف' : status?.state?.account_target === 'live' ? 'Live محظور' : 'تشغيل Demo/Shadow'}</button>
        <button className="secondary" onClick={()=>master('stop')} disabled={busy}>STOP BOT (إيقاف الجديد فقط)</button>
        <button className="secondary" onClick={()=>master('emergency')} disabled={busy} style={{borderColor:'#6a2a2a'}}>EMERGENCY STOP</button>
        <button className="secondary" onClick={resetEmergency} disabled={busy || !status?.state?.emergency_stop}>إعادة ضبط الطوارئ (بعد الفحص)</button>
        <button className="secondary" onClick={()=>setStatePatch({account_target:'demo'})} disabled={busy || ['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}>Demo</button>
        <button className="secondary" onClick={()=>setStatePatch({account_target:'testnet'})} disabled={busy || ['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}>Testnet</button>
      </div>
      <div className="muted" style={{marginTop:8}}>Start يشغّل Demo/Shadow الداخلي فقط. فحص Testnet وقراءة الحساب والأوامر المفتوحة فقط؛ لا إرسال أو إغلاق أوامر من TITAN حالياً. Live محظور بالكامل وفق سياسة المشروع.</div>
      {binanceLive && <div className="notice" style={{marginTop:8}}>Live: محظور | لا تُقبل مفاتيح Live ولا تُرسل طلبات إلى حساب حقيقي.</div>}
      {binanceLiveCreds?.configured && <div className="notice" style={{marginTop:8}}>توجد مفاتيح Live قديمة محفوظة. لن تُستخدم، ويمكنك حذف مفاتيح Spot وFutures القديمة معاً.</div>}
      <button className="secondary" onClick={deleteLiveCredentials} disabled={busy}>حذف أي مفاتيح Live قديمة (Spot وFutures)</button>
    </div>

    <div className="nav">
      {['dashboard','binance','shadow','master','scanner','arbitrage','fundamentals','economic-calendar','chat','strategies','engines','records','knowledge','learning','ai','sections','diagnosis'].map(t => (
        <button key={t} className={tab===t?'active':'secondary'} onClick={()=>setTab(t)}>{t}</button>
      ))}
    </div>

    {tab==='dashboard' && <div className="grid">
      <div className="card"><h3>لوحة المعلومات</h3>
        <div>البيئة: <Pill status={status?.state?.environment_mode==='demo'?'good':'warn'}>{status?.state?.environment_mode||'unknown'}</Pill></div>
        <div>السوق: <Pill status="good">{status?.state?.market_type||'unknown'}</Pill></div>
        <div>تشغيل مستقل: <Pill status={status?.state?.autonomous_enabled?'warn':'good'}>{String(!!status?.state?.autonomous_enabled)}</Pill></div>
        <div className="muted" style={{marginTop:8}}>الأرقام المالية/الصفقات الحية تظهر فقط من سجلات/اتصالات حقيقية. القرارات المسجلة: {decisions.length}.</div>
      </div>
      <div className="card"><h3>Binance Testnet (تجريبي خارجي)</h3>
        <div>الحالة: <Pill status={binanceTestnet?.connected ? "good" : "warn"}>{binanceTestnet?.connected ? "متصل" : "غير متصل"}</Pill></div>
        <div>السوق: <span className="pill">{binanceTestnet?.market || "spot"}</span></div>
        <div>Base: <span className="muted">{binanceTestnet?.base_url || "-"}</span></div>
        <div className="muted" style={{marginTop:8}}>للاتصال وقراءة بيانات Binance Testnet فقط: أدخل مفاتيح Testnet في متغيرات البيئة (BINANCE_TESTNET_API_KEY / SECRET) ثم أعد التشغيل. إرسال الأوامر متوقف حالياً.</div>
      </div>
      <div className="card"><h3>صحة النظام والتكاملات</h3>
        {(status?.integrations ? Object.entries(status.integrations) : []).map(([k,v]) => <div key={k} className="row" style={{justifyContent:'space-between'}}><span>{k}</span><Pill status={v.status}>{v.status}</Pill></div>)}
      </div>
      <div className="card"><h3>سياسة البيانات والامتثال</h3>
        <div>بيانات سوق حية: <Pill status="warn">{String(!!status?.data_policy?.live_market_data)}</Pill></div>
        <div>Synthetic/Labeled متاح: <Pill status="good">{String(!!status?.data_policy?.synthetic_labeled_available)}</Pill></div>
        <div className="muted">الامتثال ومحرك المخاطر لهما الأولوية قبل أي تنفيذ. لا ضمان امتثال قانوني/تنظيمي؛ تحقق رسمي عند الحاجة.</div>
      </div>
    </div>}

    
    {tab==='binance' && <div className="grid">
      <div className="card"><h3>ربط Binance Testnet (Spot وFutures تجريبي)</h3>
        <div className="notice" style={{marginBottom:10}}>استعمل مفاتيح Testnet فقط. هذه الصفحة للاتصال والقراءة؛ لا إرسال أوامر يدوية أو آلية حالياً.</div>
        <div>الحالة: <Pill status={binanceTestnet?.connected?'good':'warn'}>{binanceTestnet?.connected?'متصل':'غير متصل'}</Pill></div>
        <div>مهيأ: <Pill status={binanceCreds?.configured||binanceTestnet?.configured?'good':'warn'}>{String(binanceCreds?.configured||binanceTestnet?.configured)}</Pill></div>
        <div>السوق: {binanceTestnet?.market || binanceCreds?.market || '-'}</div>
        <div>Base URL: <span className="muted">{binanceTestnet?.base_url||'-'}</span></div>
        {binanceMsg && <div className="notice" style={{marginTop:8}}>{binanceMsg}</div>}
      </div>
      <div className="card"><h3>إدخال المفاتيح وحفظها</h3>
        <form onSubmit={saveBinance}>
          <label>نوع السوق</label>
          <select value={binanceForm.market} onChange={e=>setBinanceForm({...binanceForm,market:e.target.value})}>
            <option value="spot">Spot Testnet</option>
            <option value="futures">USD-M Futures Testnet</option>
          </select>
          <label>Binance Testnet API Key</label>
          <input value={binanceForm.api_key} onChange={e=>setBinanceForm({...binanceForm,api_key:e.target.value})} placeholder="أدخل المفتاح التجريبي" autoComplete="off" />
          <label>Binance Testnet API Secret</label>
          <input type="password" value={binanceForm.api_secret} onChange={e=>setBinanceForm({...binanceForm,api_secret:e.target.value})} placeholder="أدخل السر التجريبي" autoComplete="off" />
          <div className="row" style={{marginTop:10}}>
            <button disabled={busy}>حفظ وربط</button>
            <button type="button" className="secondary" onClick={testBinance} disabled={busy}>اختبار الاتصال</button>
            <button type="button" className="secondary" onClick={deleteBinance} disabled={busy}>حذف المفاتيح</button>
          </div>
        </form>
        <div className="muted" style={{marginTop:10}}>المفاتيح تُحفظ مشفرة على الخادم فقط. لا تُخزن في المتصفح ولا تظهر بعد الحفظ. لا تشاركها في المحادثة.</div>
      </div>
      <div className="card"><h3>قراءة Binance Testnet فقط</h3>
        <div className="notice" style={{marginBottom:10}}>إرسال الأوامر وإغلاق المراكز متوقفان حالياً على الخادم. المتاح هنا قراءة الحساب والأوامر المفتوحة والمطابقة فقط؛ لا توجد صفقات تُرسل إلى Binance.</div>
        <label>السوق</label>
        <select value={binanceOrderForm.market} onChange={e=>setBinanceOrderForm({market:e.target.value})}>
          <option value="spot">Spot Testnet</option>
          <option value="futures">USD-M Futures Testnet</option>
        </select>
        <div className="row" style={{marginTop:10}}>
          <button className="secondary" onClick={fetchBinanceAccount} disabled={busy}>جلب الحساب</button>
          <button className="secondary" onClick={fetchBinanceOrders} disabled={busy}>جلب الأوامر المفتوحة</button>
          <button className="secondary" onClick={reconcileBinance} disabled={busy}>مطابقة/تسوية</button>
        </div>
        {binanceAccount && <pre>{JSON.stringify(binanceAccount, null, 2)}</pre>}
        {binanceOrders && binanceOrders.length > 0 && <div className="table-wrap"><table><thead><tr><th>ID</th><th>رمز</th><th>جانب</th><th>كمية</th><th>حالة</th></tr></thead><tbody>{binanceOrders.map((o,i)=><tr key={i}><td>{o.orderId||o.id||i}</td><td>{o.symbol}</td><td>{o.side}</td><td>{o.origQty||o.quantity}</td><td>{o.status}</td></tr>)}</tbody></table></div>}
      </div>
    </div>}

    {tab==='master' && <div className="card"><h3>إعدادات التحكم والتشغيل</h3>
      <div className="row">
        <button className="secondary" onClick={()=>selectMarket('spot')}>Spot</button>
        <button className="secondary" onClick={()=>selectMarket('futures')}>Futures</button>
      </div>
      <pre style={{marginTop:10}}>{JSON.stringify(status?.state || {}, null, 2)}</pre>
    </div>}

    {tab==='scanner' && <div className="grid">
      <div className="card"><h3>Smart Scanner (Binance Public / بيانات اصطناعية للاختبار)</h3>
        <form onSubmit={runAnalysis}>
          <label>الرمز</label><input value={form.symbol} onChange={e=>setForm({...form,symbol:e.target.value})} />
          <div className="row">
            <div style={{flex:1}}><label>السوق</label><select value={form.market_type} onChange={e=>selectMarket(e.target.value)}><option value="spot">Spot</option><option value="futures">Futures USDT Perpetual</option></select></div>
            <div style={{flex:1}}><label>البيئة</label><select value={form.environment_mode} onChange={e=>setForm({...form,environment_mode:e.target.value})}><option value="demo">Demo فقط</option></select></div>
            <div style={{flex:1}}><label>مصدر البيانات</label><select value={form.data_source} onChange={e=>setForm({...form,data_source:e.target.value})}><option value="binance_public">Binance Public (قراءة فقط)</option><option value="synthetic">بيانات اصطناعية للاختبار</option></select></div>
          </div>
          <div className="row">
            <div style={{flex:1}}><label>الرصيد التقديري (USDT)</label><input type="number" value={form.balance} onChange={e=>setForm({...form,balance:e.target.value})} /></div>
            <div style={{flex:1}}><label>الرافعة</label>{form.market_type === 'spot' ? <input type="text" value="1x — Spot بدون رافعة" readOnly /> : <input type="number" min="1" max="10" step="1" value={form.max_leverage} onChange={e=>setForm({...form,max_leverage:e.target.value})} />}</div>
            <div style={{flex:1}}><label>مخاطرة/صفقة</label><input type="number" step="0.001" value={form.risk_per_trade} onChange={e=>setForm({...form,risk_per_trade:e.target.value})} /></div>
          </div>
          <div style={{marginTop:8}}><button disabled={busy}>تشغيل التحليل متعدد الأطر</button></div>
        </form>
      </div>
      <div className="card"><h3>نتيجة التحليل</h3>
        {analysis ? <><div className="row"><Pill status={analysis.action==='wait'?'warn':'good'}>{analysis.action}</Pill><span className="muted">الثقة: {analysis.confidence}</span></div><div className="muted">{analysis.reason}</div><pre>{JSON.stringify(analysis, null, 2)}</pre></> : <div className="muted">لم يُشغّل تحليل بعد.</div>}
      </div>
    </div>}

    {tab==='scanner' && <SmartScanner market={status?.state?.market_type || form.market_type || 'spot'} balance={portfolio?.snapshot?.equity_estimate ?? form.balance} maxLeverage={Number(form.max_leverage) || 3} />}

    {tab==='scanner' && <MarketMicrostructurePanel />}

    {tab==='arbitrage' && <ArbitragePanel />}

    {tab==='fundamentals' && <FundamentalsPanel />}

    {tab==='economic-calendar' && <EconomicCalendarPanel />}

    {tab==='chat' && <div className="card"><h3>مساعد TITAN (مرتبط ببيانات النظام)</h3>
      <div style={{maxHeight:320, overflow:'auto', marginBottom:8}}>
        {chat.map((m,i)=><div key={i} className="card" style={{marginBottom:6, background: m.role==='user'?'#142238':'#0f1b2f'}}><b>{m.role==='user'?'أنت':'TITAN'}:</b> <span className="muted">{m.text}</span></div>)}
        {!chat.length && <div className="muted">اسأل عن الحالة، آخر القرارات، الاستراتيجيات، المخاطر/الامتثال، أو مفاهيم مثل BOS/CHOCH/السيولة/الاختراق.</div>}
      </div>
      <form onSubmit={sendChat} className="row"><input value={chatInput} onChange={e=>setChatInput(e.target.value)} placeholder="اكتب سؤالك..." /><button>إرسال</button></form>
      <div className="muted" style={{marginTop:8}}>الردود الحالية grounded على بيانات النظام. تكامل LLM خارجي اختياري ويخضع للأمان ولا ينفذ تداولًا مباشرة.</div>
    </div>}

    {tab==='strategies' && <>
      <div className="card"><h3>سجل الاستراتيجيات</h3>
        <div className="table-wrap"><table><thead><tr><th>Key</th><th>الاسم</th><th>الفئة</th><th>الأسواق</th><th>الحالة</th></tr></thead><tbody>
          {strategies.map(s => <tr key={s.key}><td>{s.key}</td><td>{s.name}</td><td>{s.category}</td><td>{(s.markets||[]).join(', ')}</td><td><Pill status={s.status}>{s.status}</Pill></td></tr>)}
        </tbody></table></div>
      </div>
      <BacktestPanel />
      <RiskSimulationPanel />
      <FuturesOrderbookHistoryPanel />
    </>}

    {tab==='engines' && <div className="card"><h3>مراقبة حالة المحركات</h3>
      <div className="notice" style={{marginBottom:10}}>هذه الصفحة تعرض مسار ربط كل محرك ودوره وصلاحياته. المحركات لا تملك أزرار تشغيل مستقلة؛ تشغيل وإيقاف التداول يتم عبر وحدة التحكم الرئيسية والعامل الخاضع للإشراف.</div>
      <div className="table-wrap"><table><thead><tr><th>المفتاح</th><th>المحرك</th><th>الحالة</th><th>مسار الربط</th><th>الدور</th><th>الصلاحية</th><th>ملاحظة</th></tr></thead><tbody>
        {(enginesDetailed.length ? enginesDetailed : (status?.engines||[])).map(e => {
          const integration = e.details?.integration || {};
          return <tr key={e.key || e.engine_key}>
            <td>{e.key || e.engine_key}</td>
            <td>{e.name}</td>
            <td><Pill status={e.status}>{e.status}</Pill></td>
            <td className="muted">{integration.integration || 'لم يحدد بعد'}</td>
            <td>{integration.mode || 'غير محدد'}{integration.decision_evidence ? ' · دليل لمحرك القرار' : ''}</td>
            <td>{integration.authority || 'غير محددة'}</td>
            <td className="muted">{e.message || ''}</td>
          </tr>
        })}
      </tbody></table></div>
    </div>}

    {tab==='records' && <div className="grid">
      <div className="card"><h3>القرارات</h3><div className="table-wrap"><table><thead><tr><th>ID</th><th>رمز</th><th>إجراء</th><th>بيئة/سوق</th><th>مصدر</th></tr></thead><tbody>
        {decisions.map(d => <tr key={d.id}><td>{d.id}</td><td>{d.symbol}</td><td><Pill status={d.action==='wait'?'warn':'good'}>{d.action}</Pill></td><td>{d.environment_mode}/{d.market_type}</td><td>{d.data_source}</td></tr>)}
      </tbody></table></div></div>
      <div className="card"><h3>التدقيق/السجلات</h3><div className="table-wrap"><table><thead><tr><th>وقت</th><th>حدث</th><th>مستوى</th><th>رسالة</th></tr></thead><tbody>
        {audit.map(a => <tr key={a.id}><td>{a.created_at?.slice(11,19)}</td><td>{a.action}</td><td><Pill status={a.severity}>{a.severity}</Pill></td><td className="muted">{a.message}</td></tr>)}
      </tbody></table></div></div>
    </div>}

    {tab==='knowledge' && <div className="card"><h3>مركز المعرفة</h3>
      <div className="muted">المصادر المسجلة: {knowledge.length}. قاعدة مفاهيم التداول متاحة عبر API (<code>/api/knowledge/concepts</code>).</div>
      <form onSubmit={addKnowledge} style={{marginTop:12}}>
        <label>عنوان</label><input value={knowledgeForm.title} onChange={e=>setKnowledgeForm({...knowledgeForm,title:e.target.value})} required />
        <label>نوع المصدر</label><input value={knowledgeForm.source_type} onChange={e=>setKnowledgeForm({...knowledgeForm,source_type:e.target.value})} />
        <label>رابط المصدر (HTTPS ومجالات موثوقة فقط)</label><input value={knowledgeForm.url} onChange={e=>setKnowledgeForm({...knowledgeForm,url:e.target.value})} placeholder="https://..." />
        <label>الادعاء أو الملاحظة المراد مطابقة نصها</label><textarea value={knowledgeForm.content} onChange={e=>setKnowledgeForm({...knowledgeForm,content:e.target.value})} rows={4} />
        <label>موثوقية (0-1)</label><input type="number" step="0.1" value={knowledgeForm.reliability_score} onChange={e=>setKnowledgeForm({...knowledgeForm,reliability_score:e.target.value})} />
        <button disabled={busy} style={{marginTop:8}}>إضافة مصدر</button>
      </form>
      {knowledgeMsg && <div className="notice" style={{marginTop:8}}>{knowledgeMsg}</div>}
      <div className="table-wrap" style={{marginTop:12}}><table><thead><tr><th>العنوان</th><th>النوع</th><th>موثوقية</th><th>اعتماد الحقيقة</th><th>فحص المصدر</th><th>إجراء</th></tr></thead><tbody>
        {knowledge.map(k => <tr key={k.id}><td>{k.title}</td><td>{k.source_type}</td><td>{k.reliability_score ?? ''}</td><td><Pill status={k.verified?'good':'warn'}>{String(k.verified)}</Pill></td><td><Pill status={k.source_verification?.source_verified?'good':'warn'}>{k.source_verification?.claim_relevance_status || 'لم يُفحص'}</Pill></td><td><button type="button" className="secondary" disabled={busy || !k.url} onClick={()=>verifyKnowledgeSource(k.id)}>فحص المصدر</button></td></tr>)}
      </tbody></table></div>
    </div>}

    {tab==='learning' && <div className="card"><h3>التعلم والتطور (سجلات آمنة)</h3>
      <div className="muted">لا تعديل ذاتي لقواعد المخاطر/الصلاحيات/الكود. السجلات: {learning.length}.</div>
      <form onSubmit={addLearning} style={{marginTop:12}}>
        <label>الفئة</label><input value={learningForm.category} onChange={e=>setLearningForm({...learningForm,category:e.target.value})} />
        <label>الموضوع</label><input value={learningForm.subject} onChange={e=>setLearningForm({...learningForm,subject:e.target.value})} required />
        <label>النتيجة</label><textarea value={learningForm.outcome} onChange={e=>setLearningForm({...learningForm,outcome:e.target.value})} rows={3} />
        <label><input type="checkbox" checked={learningForm.verified} onChange={e=>setLearningForm({...learningForm,verified:e.target.checked})} /> متحقق</label>
        <button disabled={busy} style={{marginTop:8}}>إضافة سجل</button>
      </form>
      {learningMsg && <div className="notice" style={{marginTop:8}}>{learningMsg}</div>}
      <div className="table-wrap" style={{marginTop:12}}><table><thead><tr><th>الفئة</th><th>الموضوع</th><th>النتيجة</th><th>متحقق</th></tr></thead><tbody>
        {learning.map(r => <tr key={r.id}><td>{r.category}</td><td>{r.subject}</td><td>{r.outcome||''}</td><td><Pill status={r.verified?'good':'warn'}>{String(r.verified)}</Pill></td></tr>)}
      </tbody></table></div>
    </div>}

    {tab==='learning' && <MLResearchPanel />}

    {tab==='shadow' && <div className="card"><h3>Shadow/PortFolio (تجريبي آمن)</h3>
      <div className="muted">أوامر Shadow تُنفّذ في الذاكرة (لا بورصة حقيقية). Live محجوب.</div>
      <div className="notice" style={{marginTop:10}}>تشغيل دورة واحدة على السوق المحدد: Spot يستخدم BUY/SELL، وFutures يستخدم LONG/SHORT مع الرافعة وحدود الهامش والتصفية التقديرية. التنفيذ Shadow داخلي فقط؛ لا تُرسل أوامر إلى Binance.</div>
      <button type="button" onClick={runDemoCycle} disabled={busy} style={{marginTop:10}}>تشغيل دورة Demo آمنة</button>
      <form onSubmit={submitShadowOrder} style={{marginTop:12}}>
        <div className="row">
          <div style={{flex:1}}><label>البيئة</label><select value={shadowForm.environment_mode} onChange={e=>setShadowForm({...shadowForm,environment_mode:e.target.value})}><option value="demo">demo</option><option value="live">live (محجوب)</option></select></div>
          <div style={{flex:1}}><label>السوق</label><select value={shadowForm.market_type} onChange={e=>selectMarket(e.target.value)}><option value="spot">spot</option><option value="futures">futures USDT perpetual</option></select></div>
        </div>
        <label>الرمز</label><input value={shadowForm.symbol} onChange={e=>setShadowForm({...shadowForm,symbol:e.target.value})} />
        <div className="row">
          <div style={{flex:1}}><label>الجانب</label><select value={shadowForm.side} onChange={e=>setShadowForm({...shadowForm,side:e.target.value})}>{shadowForm.market_type === 'futures' ? <><option value="long">LONG</option><option value="short">SHORT</option></> : <><option value="buy">BUY</option><option value="sell">SELL</option></>}</select></div>
          <div style={{flex:1}}><label>النوع</label><select value={shadowForm.order_type} onChange={e=>setShadowForm({...shadowForm,order_type:e.target.value})}><option value="limit">limit</option><option value="market">market</option></select></div>
        </div>
        <div className="row">
          <div style={{flex:1}}><label>الكمية</label><input type="number" value={shadowForm.quantity} onChange={e=>setShadowForm({...shadowForm,quantity:e.target.value})} /></div>
          <div style={{flex:1}}><label>السعر</label><input type="number" value={shadowForm.price} onChange={e=>setShadowForm({...shadowForm,price:e.target.value})} /></div>
          {shadowForm.market_type === 'futures' && <div style={{flex:1}}><label>الرافعة (حد أقصى 10x)</label><input type="number" min="1" max="10" step="1" value={shadowForm.leverage} onChange={e=>setShadowForm({...shadowForm,leverage:e.target.value})} /></div>}
        </div>
        <div className="row">
          <div style={{flex:1}}><label>المحاكاة</label><select value={shadowForm.simulate} onChange={e=>setShadowForm({...shadowForm,simulate:e.target.value})}><option value="fill">fill</option><option value="partial">partial</option><option value="cancel">cancel</option><option value="reject">reject</option></select></div>
          <div style={{flex:1}}><label>نسبة التعبئة</label><input type="number" step="0.1" value={shadowForm.fill_ratio} onChange={e=>setShadowForm({...shadowForm,fill_ratio:e.target.value})} /></div>
        </div>
        <button disabled={busy} style={{marginTop:8}}>إرسال أمر Shadow</button>
      </form>
      {shadowMsg && <div className="notice" style={{marginTop:8}}>{shadowMsg}</div>}
      {shadowResult && <pre style={{marginTop:8}}>{JSON.stringify(shadowResult, null, 2)}</pre>}
      <div style={{marginTop:12}}>
        <button className="secondary" onClick={refreshPortfolio} disabled={busy}>تحديث Portfolio</button>
      </div>
      {portfolio && <pre style={{marginTop:8}}>{JSON.stringify(portfolio, null, 2)}</pre>}
      <form onSubmit={closeShadowPosition} style={{marginTop:12}}>
        <label>معرّف المركز (position_id)</label><input type="number" onChange={e=>setShadowForm({...shadowForm,position_id:e.target.value})} />
        <label>سعر الإغلاق</label><input type="number" step="any" onChange={e=>setShadowForm({...shadowForm,close_price:e.target.value})} />
        <button disabled={busy} style={{marginTop:8}}>إغلاق مركز Shadow</button>
      </form>
      <form onSubmit={saveShadowProtection} style={{marginTop:16}}>
        <h4>حماية المركز المفتوح (Shadow)</h4>
        <label>معرّف المركز</label><input type="number" value={protectionForm.position_id} onChange={e=>setProtectionForm({...protectionForm,position_id:e.target.value})} required />
        <div className="row">
          <div style={{flex:1}}><label>Stop Loss</label><input type="number" step="any" value={protectionForm.stop_loss} onChange={e=>setProtectionForm({...protectionForm,stop_loss:e.target.value})} /></div>
          <div style={{flex:1}}><label>Take Profit</label><input type="number" step="any" value={protectionForm.take_profit} onChange={e=>setProtectionForm({...protectionForm,take_profit:e.target.value})} /></div>
        </div>
        <label>Trailing Stop (مثلاً 0.02 = 2%)</label><input type="number" step="0.001" min="0.001" max="0.20" value={protectionForm.trailing_stop_pct} onChange={e=>setProtectionForm({...protectionForm,trailing_stop_pct:e.target.value})} />
        <button disabled={busy} style={{marginTop:8}}>حفظ حماية المركز</button>
      </form>
    </div>}

    {tab==='shadow' && <PerformancePanel />}

    {tab==='ai' && <div className="card"><h3>مزودو الذكاء الاصطناعي (متعدد النماذج)</h3>
      <div className="notice">OpenAI هو المقيم الأساسي للقرارات؛ النموذج المحلي loopback هو fallback فقط عند غياب OpenAI أو تعذّر اتصاله. بقية المزودات متاحة للاختبار المنفصل ولا تدخل في مسار القرار الحالي. مخرجات LLM استشارية فقط ولا تتجاوز محرك المخاطر المستقل.</div>
      <form onSubmit={createAiProvider} style={{marginTop:12}}>
        <label>مزود</label><select value={aiProviderForm.provider} onChange={e=>setAiProviderForm({...aiProviderForm,provider:e.target.value,base_url:e.target.value==='openai_compatible'?aiProviderForm.base_url:''})}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="google">Google</option><option value="openai_compatible">OpenAI Compatible</option></select>
        <label>الاسم</label><input value={aiProviderForm.name} onChange={e=>setAiProviderForm({...aiProviderForm,name:e.target.value})} required />
        <label>النموذج</label><input value={aiProviderForm.model} onChange={e=>setAiProviderForm({...aiProviderForm,model:e.target.value})} />
        <label>مفتاح API (يُحفظ مشفراً على الخادم)</label><input type="password" autoComplete="new-password" value={aiProviderForm.api_key} onChange={e=>setAiProviderForm({...aiProviderForm,api_key:e.target.value})} placeholder="أدخل مفتاح المزود هنا" />
        <label>أو مرجع متغير البيئة (اختياري)</label><input value={aiProviderForm.api_key_ref} onChange={e=>setAiProviderForm({...aiProviderForm,api_key_ref:e.target.value})} placeholder="env:OPENAI_API_KEY" />
        <div className="notice">المفتاح لا يُعرض بعد الحفظ. يتطلب الحفظ مفتاح TITAN_SECRET_KEY قويّاً في الخادم.</div>
        {aiProviderForm.provider === 'openai_compatible' && <><label>Base URL (مطلوب لـ OpenAI Compatible فقط)</label><input value={aiProviderForm.base_url} onChange={e=>setAiProviderForm({...aiProviderForm,base_url:e.target.value})} required /></>}
        <label><input type="checkbox" checked={aiProviderForm.enabled} onChange={e=>setAiProviderForm({...aiProviderForm,enabled:e.target.checked})} /> نشط</label>
        <button disabled={busy} style={{marginTop:8}}>إضافة مزود</button>
      </form>
      {aiProviderMsg && <div className="notice" style={{marginTop:8}}>{aiProviderMsg}</div>}
      <div className="table-wrap" style={{marginTop:12}}><table><thead><tr><th>ID</th><th>اسم</th><th>مزود</th><th>نموذج</th><th>حالة</th><th>اختبار</th></tr></thead><tbody>
        {aiProviders.map(p => <tr key={p.id}><td>{p.id}</td><td>{p.name}</td><td>{p.provider}</td><td>{p.model}</td><td><Pill status={p.enabled?'good':'warn'}>{p.enabled?'نشط':'غير نشط'}</Pill></td><td><button className="secondary" onClick={()=>testAiProvider(p.id)} disabled={busy}>اختبار</button></td></tr>)}
      </tbody></table></div>
      <pre style={{marginTop:12}}>{JSON.stringify(aiResearch, null, 2)}</pre>
    </div>}

    {tab==='ai' && <TelegramPanel />}

    {tab==='diagnosis' && <div className="card"><h3>التشخيص الذاتي (Self-Diagnosis)</h3>
      <div className="muted">مراقبة جميع مكونات النظام، اكتشاف الأخطاء، وتشخيصها.</div>
      <button onClick={async()=>{setBusy(true);setDiagnosisMsg('');try{const r=await api.selfDiagnosis();setDiagnosis(r);setDiagnosisMsg('اكتمل التشخيص')}catch(e2){setDiagnosisMsg('خطأ: '+e2.message)}finally{setBusy(false)}}} disabled={busy} style={{marginTop:12}}>تشغيل تشخيص كامل</button>
      {diagnosisMsg && <div className="notice" style={{marginTop:8}}>{diagnosisMsg}</div>}
      {diagnosis && <>
        <div style={{marginTop:12}}>الحالة العامة: <Pill status={diagnosis.overall==='healthy'?'good':'warn'}>{diagnosis.overall}</Pill></div>
        <div className="muted">المشكلات: {diagnosis.issues?.length||0} | مُصلحة: {diagnosis.healed?.length||0}</div>
        {diagnosis.issues?.length > 0 && <div className="table-wrap" style={{marginTop:12}}><table><thead><tr><th>المكوّن</th><th>الحالة</th><th>التفاصيل</th></tr></thead><tbody>
          {diagnosis.issues.map((i,idx)=><tr key={idx}><td>{i.component}</td><td><Pill status={i.status==='error'?'bad':'warn'}>{i.status}</Pill></td><td className="muted">{i.detail}</td></tr>)}
        </tbody></table></div>}
        <pre style={{marginTop:12}}>{JSON.stringify(diagnosis.components, null, 2)}</pre>
      </>}
    </div>}

    {tab==='sections' && <div className="card"><h3>أقسام لوحة التحكم الـ25 (حالة صادقة)</h3>
      <div className="muted" style={{marginBottom:8}}>الحالة مشتقة من فحوصات فعلية (اتصال API، محركات، سجلات). لا ادعاءات.</div>
      <div className="table-wrap"><table><thead><tr><th>القسم</th><th>الحالة</th><th>ملاحظات</th></tr></thead><tbody>
        {SECTIONS.map(name => {
          const engineCount = enginesDetailed.length || 0
          const strategyCount = strategies.length || 0
          const decisionCount = decisions.length || 0
          const auditCount = audit.length || 0
          const knowledgeCount = knowledge.length || 0
          const learningCount = learning.length || 0
          const aiProviderCount = aiProviders.length || 0
          const mapped = {
            'Main Dashboard':['implemented_partial', status ? `حالة/سجلات حقيقية (${decisionCount} قرارات)` : 'لا بيانات'],
            'Control Center':['implemented_partial','TITAN MASTER CONTROL + إعدادات آمنة'],
            'Live Market Room':['implemented_partial','عرض بيانات السوق الحية فقط؛ تنفيذ Live محظور'],
            'Trade Management':['implemented_partial','إدارة مراكز Shadow وحمايتها؛ مزامنة البورصة لاحقًا'],
            'AI Learning':['implemented_partial', learningCount ? `${learningCount} سجلات` : 'لا سجلات'],
            'Self-Evolution':['implemented_partial','لا تعديل ذاتي للمخاطر/الصلاحيات'],
            'Discoveries':['not_implemented','يحتاج بحث/تحقق'],
            'Knowledge Center':['implemented_partial', knowledgeCount ? `${knowledgeCount} مصادر` : 'لا مصادر'],
            'Brain Monitor':['implemented_partial', engineCount ? `${engineCount} محرك` : 'لا محركات'],
            'Performance':['implemented_partial','مقاييس محفظة Demo/Shadow من السجل الداخلي'],
            'Strategy Performance':['implemented_partial','4 استراتيجيات Spot مع Historical وWalk-Forward/OOS؛ بقية الاستراتيجيات ما زالت جزئية أو مخططة'],
            'Market Scanner':['implemented_partial','ماسح Spot وFutures USDT Perpetual وتحليل متعدد الأطر مع Mark Price وFunding'],
            'Risk Center':['implemented_partial','حدود/تقديرات؛ فلاتر Binance الحية لاحقًا'],
            'Advanced Configuration':['implemented_partial','حوكمة/سجلات؛ UI متقدمة لاحقًا'],
            'Spot/Futures/Earn Selector':['implemented_partial','Spot وFutures Demo/Shadow؛ Earn ليس مسار تنفيذ'],
            'Binance Connection':['needs_external_service','مفاتيح Demo/Testnet أولًا؛ بدون سحب/تحويل'],
            'Demo/Testnet Safety':['stopped','Live محظور؛ التشغيل الآلي على Testnet متوقف حتى موافقة منفصلة'],
            'Telegram Connection':['implemented_partial','Allowlist وPolling ورسائل؛ يحتاج Bot Token وChat IDs في بيئة الخادم'],
            'Login & Security':['implemented_partial','جلسات/allowlist/hash؛ 2FA/استعادة كاملة لاحقًا'],
            'Time & Activity':['implemented_partial','عامل مستمر مُدار؛ سجل uptime/جدولة متقدمة لاحقًا'],
            'System Age & Maturity':['not_implemented','يحتاج مقاييس أدلة'],
            'System Health':['implemented_partial', engineCount ? `محركات: ${engineCount}` : 'لا بيانات'],
            'News & Market':['implemented_partial','RSS عام من ناشرين محددين وتحليل كلمات؛ لا يغطي كل الأخبار أو السياق'],
            'Prediction Tracker':['implemented_partial','تقييم mark-to-market بعد أفق زمني؛ ليس PnL محققًا'],
            'Logs & Reports':['implemented_partial', auditCount ? `${auditCount} سجل` : 'لا سجلات'],
          }[name] || ['not_implemented','قيد التطوير']
          return <tr key={name}><td>{name}</td><td><Pill status={mapped[0]}>{mapped[0]}</Pill></td><td className="muted">{mapped[1]}</td></tr>
        })}
      </tbody></table></div>
    </div>}

    <div className="muted" style={{marginTop:12}}>TITAN AI PREMIUM — بناء تأسيسي. لا ضمان أرباح. لا ادعاء اكتمال ميزات غير مختبرة أو غير مربوطة بخدمات خارجية.</div>
  </div>
}
