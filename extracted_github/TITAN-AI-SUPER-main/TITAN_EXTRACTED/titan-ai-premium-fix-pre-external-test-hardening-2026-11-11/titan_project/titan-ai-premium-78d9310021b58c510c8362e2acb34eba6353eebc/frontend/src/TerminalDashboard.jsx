import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api, setToken } from './api'
import { buildMarketStreamUrl, parseMarketStreamMessage } from './marketStream'
import SmartScanner from './SmartScanner'
import MarketMicrostructurePanel from './MarketMicrostructurePanel'
import PerformancePanel from './PerformancePanel'
import BacktestPanel from './BacktestPanel'
import ArbitragePanel from './ArbitragePanel'
import FundamentalsPanel from './FundamentalsPanel'
import EconomicCalendarPanel from './EconomicCalendarPanel'
import MLResearchPanel from './MLResearchPanel'
import TelegramPanel from './TelegramPanel'

const PAIRS = ['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','DOGEUSDT']
const FRAMES = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d','3d','1w','1M']
const NAV = [
  ['home','الرئيسية','⌂'],['trade','التداول','▥'],['scanner','الماسح الذكي','⌕'],
  ['ai','التحليل الذكي','✧'],['strategies','الاستراتيجيات','◈'],['risk','المخاطر','⬡'],
  ['portfolio','المحفظة','▣'],['reports','التقارير','▤'],['settings','الإعدادات','⚙']
]
const fmt = (v, d=2) => Number.isFinite(Number(v)) ? Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}) : '—'

function Panel({title,children,extra,className=''}) {
  return <section className={`tt-panel ${className}`}><div className="tt-panel-head"><strong>{title}</strong>{extra}</div>{children}</section>
}
function Status({ok,children}) { return <span className={`tt-status ${ok?'is-ok':'is-warn'}`}><i/>{children}</span> }

export default function TerminalDashboard() {
  const [tab,setTab] = useState('home')
  const [status,setStatus] = useState(null)
  const [portfolio,setPortfolio] = useState(null)
  const [market,setMarket] = useState('spot')
  const [symbol,setSymbol] = useState('BTCUSDT')
  const [frame,setFrame] = useState('15m')
  const [ticker,setTicker] = useState(null)
  const [candles,setCandles] = useState([])
  const [book,setBook] = useState(null)
  const [trades,setTrades] = useState([])
  const [marketError,setMarketError] = useState('')
  const [streamStatus,setStreamStatus] = useState('connecting')
  const [lastMarketUpdate,setLastMarketUpdate] = useState(null)
  const [marketDataSource,setMarketDataSource] = useState('')
  const [latestDecision,setLatestDecision] = useState(null)
  const [busy,setBusy] = useState(false)
  const [message,setMessage] = useState('')
  const [orderSide,setOrderSide] = useState('buy')
  const [quantity,setQuantity] = useState('0.001')
  const [orderType,setOrderType] = useState('market')
  const [limitPrice,setLimitPrice] = useState('')
  const [chatInput,setChatInput] = useState('')
  const [chat,setChat] = useState([])
  const [chatReports,setChatReports] = useState(null)
  const [aiProviderState,setAiProviderState] = useState(null)
  const [aiProviderBusy,setAiProviderBusy] = useState(null)
  const [selectedTarget,setSelectedTarget] = useState('demo')
  const [accountHealth,setAccountHealth] = useState(null)
  const [testnetApiKey,setTestnetApiKey] = useState('')
  const [testnetApiSecret,setTestnetApiSecret] = useState('')
  const [liveApiKey,setLiveApiKey] = useState('')
  const [liveApiSecret,setLiveApiSecret] = useState('')
  const [liveApproval,setLiveApproval] = useState(false)
  const [diagnosis,setDiagnosis] = useState(null)
  const [auth,setAuth] = useState({loading:true,required:false,me:null})
  const [loginForm,setLoginForm] = useState({email:'',password:'',totp_code:''})
  const [twoFactorSetup,setTwoFactorSetup] = useState(null)
  const [twoFactorCode,setTwoFactorCode] = useState('')

  const refreshSystem = useCallback(async () => {
    try {
      const s = await api.status()
      setStatus(s)
      try {
        const decisionResult = await api.decisions()
        setLatestDecision(decisionResult?.items?.[0] || null)
      } catch { setLatestDecision(null) }
      const m = s?.state?.market_type || market
      const target = s?.state?.account_target || 'demo'
      const e = ['testnet','live'].includes(target) ? target : (s?.state?.environment_mode || 'demo')
      setMarket(m)
      setSelectedTarget(target)
      if(s?.state?.live_confirmed_by_user===true) setLiveApproval(true)
      try { setPortfolio(await api.portfolio(e,m)) } catch { setPortfolio(null) }
    } catch (e) { setMessage('تعذر الاتصال بخادم TITAN: '+e.message) }
  },[market])

  const refreshMarket = useCallback(async () => {
    setMarketError('')
    const pair = symbol.replace('/','').toUpperCase()
    try {
      const host = market==='futures' ? 'https://fapi.binance.com/fapi/v1' : 'https://api.binance.com/api/v3'
      const [t,k,d,tr] = await Promise.all([
        fetch(`${host}/ticker/24hr?symbol=${pair}`).then(r=>{if(!r.ok) throw new Error('ticker HTTP '+r.status);return r.json()}),
        fetch(`${host}/klines?symbol=${pair}&interval=${frame}&limit=90`).then(r=>{if(!r.ok) throw new Error('klines HTTP '+r.status);return r.json()}),
        fetch(`${host}/depth?symbol=${pair}&limit=10`).then(r=>{if(!r.ok) throw new Error('depth HTTP '+r.status);return r.json()}),
        fetch(`${host}/trades?symbol=${pair}&limit=12`).then(r=>{if(!r.ok) throw new Error('trades HTTP '+r.status);return r.json()})
      ])
      setTicker(t)
      // REST candles are closed-only; the WebSocket may subsequently update the forming candle.
      setCandles((k||[]).filter(x=>Number(x[6]) < Date.now()).map(x=>({time:x[0],open:Number(x[1]),high:Number(x[2]),low:Number(x[3]),close:Number(x[4]),volume:Number(x[5])})))
      setBook(d)
      setTrades(tr||[])
      setMarketDataSource('REST snapshot')
      setLastMarketUpdate(new Date().toISOString())
    } catch (e) {
      // Keep the last verified snapshot visible during a temporary REST outage.
      setMarketError('تعذر تحديث لقطة Binance: '+e.message)
    }
  },[symbol,frame,market])


  useEffect(()=>{
    let active=true
    ;(async()=>{
      try {
        const authStatus=await api.authStatus()
        if(typeof authStatus?.auth_required!=='boolean') throw new Error('استجابة التحقق من الدخول غير صالحة')
        let me=null
        try { me=await api.me() } catch {}
        if(!active) return
        setAuth({loading:false,required:authStatus.auth_required,me})
        if(!authStatus.auth_required || me?.authenticated) await refreshSystem()
      } catch(e) {
        if(active) {
          setAuth({loading:false,required:true,me:null})
          setMessage('تعذر التحقق من جلسة الدخول. تم إغلاق الوصول احتياطياً: '+e.message)
        }
      }
    })()
    return ()=>{active=false}
  },[refreshSystem])

  useEffect(()=>{
    if(auth.loading || (auth.required && !auth.me?.authenticated) || tab==='settings') return
    const id=setInterval(()=>{refreshSystem()},15000)
    return ()=>clearInterval(id)
  },[auth.loading,auth.required,auth.me?.authenticated,tab,refreshSystem])

  async function onLogin(e) {
    e.preventDefault();setBusy(true);setMessage('')
    try {
      const result=await api.login(loginForm)
      if(result.token) setToken(result.token)
      const me=await api.me()
      setAuth(a=>({...a,me}))
      setLoginForm(v=>({...v,password:''}))
      await refreshSystem()
    } catch(err) {setMessage('فشل تسجيل الدخول: '+err.message)}
    finally {setBusy(false)}
  }


  async function startTwoFactorSetup() {
    setBusy(true);setMessage('')
    try { setTwoFactorSetup(await api.setupTwoFactor()); setTwoFactorCode('') }
    catch(err) { setMessage('تعذر إعداد المصادقة الثنائية: '+err.message) }
    finally { setBusy(false) }
  }

  async function confirmTwoFactorSetup() {
    setBusy(true);setMessage('')
    try {
      await api.confirmTwoFactor({totp_code:twoFactorCode})
      setAuth(a=>({...a,me:{...a.me,two_factor_enabled:true}}))
      setTwoFactorSetup(null);setTwoFactorCode('')
      setMessage('تم تفعيل المصادقة الثنائية بنجاح.')
    } catch(err) { setMessage('تعذر تأكيد رمز المصادقة: '+err.message) }
    finally { setBusy(false) }
  }

  async function disableTwoFactor() {
    setBusy(true);setMessage('')
    try {
      await api.disableTwoFactor({totp_code:twoFactorCode})
      setAuth(a=>({...a,me:{...a.me,two_factor_enabled:false}}))
      setTwoFactorCode('')
      setMessage('تم تعطيل المصادقة الثنائية بعد التحقق.')
    } catch(err) { setMessage('تعذر تعطيل المصادقة الثنائية: '+err.message) }
    finally { setBusy(false) }
  }

  async function onLogout() {
    try {await api.logout()} catch {}
    setToken('')
    setAuth(a=>({...a,me:null}))
    setStatus(null);setPortfolio(null)
  }
  useEffect(()=>{refreshMarket()},[refreshMarket])
  // REST provides the initial snapshot and periodic resynchronization; WebSocket
  // carries the live ticker, candle, partial depth book and trade updates.
  useEffect(()=>{const id=setInterval(()=>{refreshMarket()},60000);return()=>clearInterval(id)},[refreshMarket])

  useEffect(()=>{
    let active=true
    let socket=null
    let retryTimer=null
    let heartbeatTimer=null
    let retryDelay=1000
    let lastMessageAt=0

    const scheduleReconnect=()=>{
      if(!active) return
      setStreamStatus(navigator.onLine===false?'offline':(lastMessageAt && Date.now()-lastMessageAt>45000?'stale':'reconnecting'))
      if(retryTimer) clearTimeout(retryTimer)
      retryTimer=setTimeout(()=>{
        retryDelay=Math.min(retryDelay*2,30000)
        connect()
      },retryDelay)
    }

    const connect=()=>{
      if(!active) return
      if(navigator.onLine===false) {
        setStreamStatus('offline')
        scheduleReconnect()
        return
      }
      setStreamStatus('connecting')
      try {
        const url=buildMarketStreamUrl(market,symbol,frame)
        socket=new WebSocket(url)
      } catch {
        scheduleReconnect()
        return
      }
      socket.onopen=()=>{
        if(!active) return
        retryDelay=1000
        lastMessageAt=Date.now()
        setStreamStatus('connected')
        setMarketError('')
      }
      socket.onmessage=(message)=>{
        if(!active) return
        const event=parseMarketStreamMessage(message.data)
        if(!event) return
        lastMessageAt=Date.now()
        setLastMarketUpdate(new Date(lastMessageAt).toISOString())
        setMarketDataSource('Binance WebSocket')
        if(event.type==='ticker') {
          setTicker(previous=>({...previous,
            lastPrice:event.lastPrice,
            priceChangePercent:event.priceChangePercent,
            highPrice:event.highPrice,
            lowPrice:event.lowPrice,
            quoteVolume:event.quoteVolume,
          }))
        } else if(event.type==='kline') {
          setCandles(previous=>{
            const next=previous.slice()
            const index=next.findIndex(c=>c.time===event.candle.time)
            if(index>=0) next[index]=event.candle
            else next.push(event.candle)
            return next.slice(-90)
          })
        } else if(event.type==='book') {
          setBook(event.book)
        } else if(event.type==='trade') {
          setTrades(previous=>[event.trade,...previous].slice(0,12))
        }
      }
      socket.onerror=()=>{try{socket?.close()}catch{}}
      socket.onclose=()=>scheduleReconnect()
    }

    const onOffline=()=>{setStreamStatus('offline');try{socket?.close()}catch{}}
    const onOnline=()=>{if(retryTimer) clearTimeout(retryTimer);retryDelay=1000;connect()}
    window.addEventListener('offline',onOffline)
    window.addEventListener('online',onOnline)
    heartbeatTimer=setInterval(()=>{
      if(lastMessageAt && Date.now()-lastMessageAt>45000) {
        setStreamStatus('stale')
        try{socket?.close()}catch{}
      }
    },5000)

    connect()
    return ()=>{
      active=false
      window.removeEventListener('offline',onOffline)
      window.removeEventListener('online',onOnline)
      if(retryTimer) clearTimeout(retryTimer)
      if(heartbeatTimer) clearInterval(heartbeatTimer)
      try{socket?.close()}catch{}
    }
  },[market,symbol,frame])

  const price = Number(ticker?.lastPrice || 0)
  const chart = useMemo(()=>{
    if(!candles.length) return null
    const lo=Math.min(...candles.map(c=>c.low)), hi=Math.max(...candles.map(c=>c.high))
    const maxVolume=Math.max(1,...candles.map(c=>Number(c.volume)||0))
    const span=hi-lo||1, w=900, h=280, priceH=210, volumeH=48, gap=3
    const candleW=Math.max(2,(w-gap*candles.length)/candles.length)
    const y=v=>priceH-((v-lo)/span)*(priceH-18)-9
    return {lo,hi,w,h,priceH,volumeH,items:candles.map((c,i)=>{
      const openY=y(c.open),closeY=y(c.close),volume=Number(c.volume)||0
      const volumeBar=Math.max(1,(volume/maxVolume)*(volumeH-5))
      return {x:i*(candleW+gap)+gap/2,up:c.close>=c.open,open:openY,close:closeY,high:y(c.high),low:y(c.low),top:Math.min(openY,closeY),body:Math.max(2,Math.abs(openY-closeY)),width:candleW,volumeY:priceH+volumeH-volumeBar,volumeH:volumeBar}
    })}
  },[candles])

  const refreshChatReports = useCallback(async () => {
    try { setChatReports(await api.chatReports(25)) }
    catch (e) { setMessage('تعذر تحميل تقارير TITAN: '+e.message) }
  },[])
  const refreshAiProviders = useCallback(async () => {
    try { setAiProviderState(await api.aiProviders()) }
    catch (e) { setMessage('تعذر تحميل محركات الذكاء الخارجية: '+e.message) }
  },[])

  async function controlAiProvider(id, enabled) {
    setAiProviderBusy(id);setMessage('')
    try {
      await api.aiProviderControl(id, enabled)
      await refreshAiProviders()
      await refreshSystem()
      setMessage(enabled?'تم تشغيل محرك الذكاء الخارجي.':'تم إيقاف محرك الذكاء الخارجي.')
    } catch(e) { setMessage('تعذر تغيير حالة محرك الذكاء: '+e.message) }
    finally { setAiProviderBusy(null) }
  }

  async function testAiProvider(id) {
    setAiProviderBusy(id);setMessage('')
    try {
      const result=await api.aiTestProvider(id)
      await refreshAiProviders()
      setMessage(result.ok?'اختبار المحرك نجح.':'اختبار المحرك لم ينجح: '+(result.error||'لا توجد استجابة'))
    } catch(e) { setMessage('فشل اختبار محرك الذكاء: '+e.message) }
    finally { setAiProviderBusy(null) }
  }

  const refreshAccountHealth = useCallback(async () => {
    const settled = await Promise.allSettled([
      api.binanceTestnetStatus(market), api.binanceLiveStatus(market),
      api.binanceExecutionPreflight(), api.binanceTestnetCredsStatus(market),
      api.binanceLiveCreds(market),
    ])
    setAccountHealth({
      testnet:settled[0].status==='fulfilled'?settled[0].value:{error:settled[0].reason?.message},
      live:settled[1].status==='fulfilled'?settled[1].value:{error:settled[1].reason?.message},
      preflight:settled[2].status==='fulfilled'?settled[2].value:{error:settled[2].reason?.message},
      testnetCreds:settled[3].status==='fulfilled'?settled[3].value:{configured:false},
      liveCreds:settled[4].status==='fulfilled'?settled[4].value:{configured:false},
    })
  },[market])

  useEffect(()=>{ if(tab==='ai') refreshAiProviders() },[tab,refreshAiProviders])
  useEffect(()=>{ if(tab==='settings') refreshAccountHealth() },[tab,refreshAccountHealth])

  async function saveTradingSelection() {
    if(!['demo','testnet','live'].includes(selectedTarget)) {
      setMessage('اختر Demo أو Binance Testnet أو Binance Live.')
      return
    }
    setBusy(true);setMessage('')
    try {
      await api.setState({account_target:selectedTarget, market_type:market})
      let testnetStarted = false
      if(selectedTarget==='testnet') {
        const creds = await api.binanceTestnetCredsStatus(market)
        if(creds?.configured) {
          await api.masterStart({account_target:'testnet'})
          testnetStarted = true
        }
      }
      await refreshSystem()
      await refreshAccountHealth()
      setLiveApproval(false)
      setMessage(selectedTarget==='demo'?'تم اختيار Demo الداخلي.':selectedTarget==='testnet'?(testnetStarted?'تم تشغيل تداول Binance Testnet تلقائياً.':'تم اختيار Binance Testnet؛ اربط مفاتيح التداول لتشغيله تلقائياً.'):'تم اختيار Binance Live للاتصال والقراءة فقط. لن يبدأ التداول الحقيقي حتى تمنح الموافقة الصريحة وتضغط بدء التداول.')
    } catch(e) { setMessage('تعذر حفظ إعداد الحساب: '+e.message) }
    finally { setBusy(false) }
  }

  async function saveTestnetKeys() {
    setBusy(true);setMessage('')
    try {
      const result=await api.binanceTestnetSave({market,api_key:testnetApiKey,api_secret:testnetApiSecret})
      setTestnetApiKey('');setTestnetApiSecret('')
      await refreshSystem()
      await refreshAccountHealth()
      setMessage(result.note||'تم حفظ مفاتيح Testnet المشفرة.')
    } catch(e) { setMessage('لم يتم حفظ مفاتيح Testnet: '+e.message) }
    finally { setBusy(false) }
  }

  async function saveLiveKeys() {
    setBusy(true);setMessage('')
    try {
      const result=await api.binanceLiveSave({market,api_key:liveApiKey,api_secret:liveApiSecret})
      setLiveApiKey('');setLiveApiSecret('')
      await refreshSystem()
      await refreshAccountHealth()
      setMessage(result.note||'تم حفظ مفاتيح Live للقراءة فقط.')
    } catch(e) { setMessage('لم يتم حفظ مفاتيح Live: '+e.message) }
    finally { setBusy(false) }
  }

  async function deleteLegacyLiveKeys() {
    if(!window.confirm('حذف مفاتيح Binance Live المحفوظة لهذا السوق؟')) return
    setBusy(true);setMessage('')
    try {
      await api.binanceLiveDelete(market)
      await refreshAccountHealth()
      setMessage('تم حذف مفاتيح Live المحفوظة.')
    } catch(e) { setMessage('تعذر حذف مفاتيح Live: '+e.message) }
    finally { setBusy(false) }
  }


  useEffect(()=>{
    if(tab!=='ai') return
    refreshChatReports()
    const id=setInterval(refreshChatReports,30000)
    return ()=>clearInterval(id)
  },[tab,refreshChatReports])

  async function toggleTradingPause() {
    const paused = status?.state?.trading_paused !== true
    setBusy(true);setMessage('')
    try {
      await api.tradingPause(paused)
      await refreshSystem()
      setMessage(paused?'تم إيقاف فتح الصفقات الجديدة فقط. الاتصال والمراقبة وإدارة المراكز مستمرة.':'تم السماح بفتح صفقات جديدة مجدداً.')
    } catch(e) { setMessage('تعذر تغيير حالة التداول: '+e.message) }
    finally { setBusy(false) }
  }

  async function master(action) {
    if(action==='start' && selectedTarget==='live') {
      if(!liveApproval) { setMessage('فعّل خانة «مسموح ببدء التداول الحقيقي» أولاً.'); return }
      if(!window.confirm('أنت على وشك السماح لـ TITAN بإرسال أوامر تداول حقيقية على Binance '+market.toUpperCase()+'. تأكد من الحساب والمخاطر. هل تمنح الموافقة الآن؟')) return
    }
    setBusy(true);setMessage('')
    try {
      if(action==='start') {
        await api.masterStart({
          account_target:selectedTarget,
          live_confirm:selectedTarget==='live' && liveApproval,
        })
      }
      if(action==='stop') { await api.masterStop({reason:'user_stop',close_positions:false}); setLiveApproval(false) }
      if(action==='emergency') { await api.emergencyStop(); setLiveApproval(false) }
      await refreshSystem()
      setMessage(action==='start'
        ? selectedTarget==='demo'?'تم تشغيل Demo/Shadow الداخلي.':selectedTarget==='testnet'?'تم تشغيل تداول Binance Testnet التجريبي.':'تم إرسال أمر بدء التداول الحقيقي بعد موافقتك.'
        :'تم تنفيذ طلب الإيقاف.')
      await refreshAccountHealth()
    } catch(e) {setMessage('فشل الإجراء: '+e.message)} finally {setBusy(false)}
  }

  async function submitShadow(e) {
    e.preventDefault()
    if(selectedTarget!=='demo') {
      setMessage('نموذج الأوامر اليدوي هنا للمحاكاة Shadow فقط. أوامر BUY/SELL الحقيقية على Binance يرسلها محرك القرار عبر العامل المحمي تلقائياً؛ لا يوجد مسار يدوي يتجاوز محرك المخاطر.')
      return
    }
    setBusy(true);setMessage('')
    try {
      const result=await api.shadowOrder({
        environment_mode:'demo',market_type:market,symbol:symbol.replace(/USDT$/,'/USDT'),
        side:market==='futures'?(orderSide==='buy'?'long':'short'):orderSide,
        order_type:orderType,quantity:Number(quantity),price:orderType==='limit'?Number(limitPrice||price):price,
        leverage:1,simulate:'fill',fill_ratio:1,idempotency_key:crypto.randomUUID()
      })
      setMessage(result.ok?'تم تسجيل أمر Shadow التجريبي.':JSON.stringify(result))
      await refreshSystem()
    } catch(e) {setMessage('لم يتم إرسال الأمر: '+e.message)} finally {setBusy(false)}
  }

  async function sendChat(e) {
    e.preventDefault();if(!chatInput.trim()) return
    const text=chatInput;setChatInput('');setChat(v=>[...v,{role:'user',text}])
    try {const r=await api.chat({message:text});setChat(v=>[...v,{role:'assistant',text:r.reply||'لا توجد إجابة',grounded:r.grounded}])}
    catch(err){setChat(v=>[...v,{role:'assistant',text:'تعذر الاتصال بالمساعد: '+err.message}])}
  }

  async function runDiagnosis() {
    setBusy(true);setDiagnosis(null)
    try {setDiagnosis(await api.selfDiagnosis())} catch(e){setMessage('فشل التشخيص: '+e.message)} finally {setBusy(false)}
  }

  const balance=portfolio?.snapshot?.equity_estimate ?? portfolio?.snapshot?.balance ?? null
  const positions=portfolio?.positions||[]
  const engineItems=Array.isArray(status?.engines)?status.engines:[]
  const runtimeWiredEngineCount=engineItems.filter(item=>String(item.runtime_check?.status||'').toLowerCase()==='wired').length
  const asks=(book?.asks||[]).filter(x=>Array.isArray(x)&&Number.isFinite(Number(x[0]))&&Number.isFinite(Number(x[1]))&&Number(x[0])>0&&Number(x[1])>0).sort((a,b)=>Number(a[0])-Number(b[0]))
  const bids=(book?.bids||[]).filter(x=>Array.isArray(x)&&Number.isFinite(Number(x[0]))&&Number.isFinite(Number(x[1]))&&Number(x[0])>0&&Number(x[1])>0).sort((a,b)=>Number(b[0])-Number(a[0]))
  const selectedLabel=symbol.replace('USDT','/USDT')
  const navClick=id=>setTab(id)


  if(auth.loading) return <main className="tt-app" dir="rtl"><section className="tt-panel"><h2>TITAN SUPER AI PREMIUM</h2><p>جارٍ التحقق من جلسة الدخول...</p></section></main>
  if(auth.required && !auth.me?.authenticated) return <main className="tt-app" dir="rtl"><section className="tt-panel tt-login"><div className="tt-crown">♛</div><h1>TITAN SUPER AI PREMIUM</h1><p className="tt-muted">تسجيل دخول آمن — الوصول للحسابات المصرح بها فقط.</p>{message&&<div className="tt-message">{message}</div>}<form onSubmit={onLogin} className="tt-order-form"><label>البريد الإلكتروني</label><input type="email" autoComplete="username" value={loginForm.email} onChange={e=>setLoginForm(v=>({...v,email:e.target.value}))} required/><label>كلمة المرور</label><input type="password" autoComplete="current-password" value={loginForm.password} onChange={e=>setLoginForm(v=>({...v,password:e.target.value}))} required/><label>رمز المصادقة الثنائية (إذا كانت مفعّلة)</label><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={loginForm.totp_code} onChange={e=>setLoginForm(v=>({...v,totp_code:e.target.value.replace(/\D/g,'').slice(0,6)}))} placeholder="000000"/><button className="tt-primary" disabled={busy}>{busy?'جارٍ الدخول...':'تسجيل الدخول'}</button></form></section></main>

  return <main className="tt-app" dir="rtl">
    <header className="tt-header">
      <div className="tt-brand"><div className="tt-crown">♛</div><div><h1>TITAN</h1><b>SUPER AI PREMIUM</b></div><span className="tt-tagline">تحليل أعمق · قرارات أذكى · تداول أكثر أماناً</span></div>
      <div className="tt-header-right"><Status ok={!!status}> {status?'SYSTEM CONNECTED':'API DISCONNECTED'} </Status><Status ok={streamStatus==='connected'}> BINANCE STREAM {streamStatus==='connected'?'LIVE':streamStatus.toUpperCase()} </Status><span className="tt-exchange">BINANCE {status?.state?.account_target==='live'?(status?.state?.live_confirmed_by_user===true&&['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)?'LIVE AUTO':'LIVE READ-ONLY'):status?.state?.account_target==='testnet'? ( ['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state) ? 'TESTNET AUTO' : 'TESTNET'):'DEMO'}</span><button className="tt-icon-btn" onClick={refreshSystem} title="تحديث النظام">⟳</button>{auth.me?.authenticated&&<button className="tt-icon-btn" onClick={onLogout} title="تسجيل الخروج">خروج</button>}</div>
    </header>

    <nav className="tt-nav">{NAV.map(([id,label,icon])=><button key={id} className={tab===id?'selected':''} onClick={()=>navClick(id)}><span>{icon}</span>{label}</button>)}</nav>
    {message&&<div className="tt-message">{message}</div>}

    {tab==='home'&&<div className="tt-home">
      <Panel title="قائمة الأسواق" className="tt-watch">
        {PAIRS.map(p=><button key={p} className={symbol===p?'tt-pair active':'tt-pair'} onClick={()=>{setSymbol(p);setTab('trade')}}><b>{p.replace('USDT','/USDT')}</b><span>{symbol===p&&ticker?fmt(ticker.lastPrice,price<1?5:2):'عرض السوق'}</span></button>)}
      </Panel>
      <Panel title={`${selectedLabel} · ${frame}`} className="tt-chart-panel" extra={<div className="tt-price">{ticker?fmt(price,price<1?5:2):'—'} <small>USDT</small><em className={Number(ticker?.priceChangePercent)>=0?'positive':'negative'}>{ticker?Number(ticker.priceChangePercent).toFixed(2)+'%':'—'}</em></div>}>
        <div className="tt-market-stats"><span>24h High <b>{ticker?fmt(ticker.highPrice):'—'}</b></span><span>24h Low <b>{ticker?fmt(ticker.lowPrice):'—'}</b></span><span>24h Volume <b>{ticker?fmt(ticker.quoteVolume,0)+' USDT':'—'}</b></span></div>
        <div className="tt-market-meta"><span>المصدر: Binance Public · {marketDataSource||'بانتظار البيانات'}</span><span>آخر تحديث: {lastMarketUpdate?new Date(lastMarketUpdate).toLocaleTimeString():'—'}</span></div>
        <div className="tt-frames">{FRAMES.map(f=><button key={f} className={frame===f?'active':''} onClick={()=>setFrame(f)}>{f}</button>)}<button onClick={refreshMarket}>تحديث ↻</button></div>
        {chart?<svg className="tt-candles" viewBox={`0 0 ${chart.w} ${chart.h}`} role="img" aria-label="Binance public candlestick and volume chart">
          {[0,1,2,3,4].map(n=><line key={n} x1="0" x2={chart.w} y1={n*chart.priceH/4} y2={n*chart.priceH/4} stroke="#19304a" strokeWidth="1"/>)}
          {chart.items.map((c,i)=><g key={i}><line x1={c.x+c.width/2} x2={c.x+c.width/2} y1={c.high} y2={c.low} stroke={c.up?'#00d99a':'#ff526d'} strokeWidth="1.5"/><rect x={c.x} y={c.top} width={c.width} height={c.body} fill={c.up?'#00d99a':'#ff526d'} rx="1"/><rect x={c.x} y={c.volumeY} width={c.width} height={c.volumeH} fill={c.up?'#00d99a':'#ff526d'} opacity=".45" rx="1"/></g>)}
          <line x1="0" x2={chart.w} y1={chart.priceH+2} y2={chart.priceH+2} stroke="#31516f" strokeWidth="1"/>
          <text x="8" y="14" fill="#91a9c4" fontSize="10" textAnchor="start">{fmt(chart.hi,price<1?5:2)}</text>
          <text x="8" y={chart.priceH-3} fill="#91a9c4" fontSize="10" textAnchor="start">{fmt(chart.lo,price<1?5:2)}</text>
          <text x="8" y={chart.h-3} fill="#91a9c4" fontSize="9" textAnchor="start">VOL</text>
        </svg>:<div className="tt-chart-empty">{marketError||'جارٍ تحميل شموع Binance Public...'}</div>}
        {marketError&&<div className="tt-muted">{marketError}</div>}
      </Panel>
      <Panel title="TITAN AI ANALYST" className="tt-ai">
        <div className="tt-ai-orb">✧</div><Status ok={!!analysisReady(status)}> {analysisReady(status)?'اتصال النظام متاح':'بانتظار بيانات النظام'} </Status>
        <div className="tt-ai-line"><span>عقود ربط المحركات المتحققة</span><b>{runtimeWiredEngineCount}/{engineItems.length}</b></div>
        <div className="tt-ai-line"><span>حالة التشغيل</span><b>{status?.state?.bot_run_state||'غير معروف'}</b></div>
        <div className="tt-ai-line"><span>آخر قرار محفوظ</span><b>{latestDecision?.action||'لا يوجد سجل قرار'}</b></div>
        <div className="tt-ai-line"><span>رمز القرار</span><b>{latestDecision?.symbol||'—'}</b></div>
        <button className="tt-primary" onClick={()=>setTab('ai')}>فتح مساعد TITAN</button>
      </Panel>
      <Panel title="دفتر الأوامر · Binance Public" className="tt-orderbook">
        <div className="tt-book-head"><span>السعر (USDT)</span><span>الكمية</span></div>
        {asks.slice(0,10).reverse().map((a,i)=><div className="tt-book-row ask" key={'a'+i}><b>{fmt(a[0],price<1?5:2)}</b><span>{fmt(a[1],4)}</span></div>)}
        <div className="tt-mid-price">{ticker?fmt(price,price<1?5:2):'—'}</div>
        {bids.slice(0,10).map((b,i)=><div className="tt-book-row bid" key={'b'+i}><b>{fmt(b[0],price<1?5:2)}</b><span>{fmt(b[1],4)}</span></div>)}
        <small>{streamStatus==='connected'?'تحديث مباشر عبر Binance WebSocket · عمق 10 مستويات':'تدفق السوق يعاود الاتصال؛ آخر لقطة REST عند توفرها'}</small>
      </Panel>
      <Panel title="إدارة المخاطر" className="tt-risk">
        <div className="tt-risk-number">{status?.state?.emergency_stop?'إيقاف طوارئ':'الحماية'}</div>
        <div className="tt-ai-line"><span>حد الخسارة اليومي</span><b>7% كحد أقصى ثابت</b></div>
        <div className="tt-ai-line"><span>وضع التداول</span><b>{status?.state?.environment_mode||'demo'}</b></div>
        <div className="tt-ai-line"><span>الأسواق</span><b>{market.toUpperCase()}</b></div>
        <button className="tt-secondary" onClick={toggleTradingPause} disabled={busy || !!status?.state?.emergency_stop}>
          {status?.state?.trading_paused?'استئناف فتح الصفقات':'إيقاف التداول فقط'}
        </button>
        <button className="tt-danger" onClick={()=>master('emergency')} disabled={busy}>إيقاف الطوارئ</button>
      </Panel>
      <Panel title="المحفظة" className="tt-wallet">
        <strong className="tt-balance">{balance===null?'—':fmt(balance)} <small>USDT</small></strong>
        <span className="tt-muted">الرصيد من سجل TITAN الداخلي · {positions.length} مركز مفتوح</span>
        <button className="tt-secondary" onClick={()=>{refreshSystem();setTab('portfolio')}}>عرض المحفظة</button>
      </Panel>
      <Panel title={`المراكز المفتوحة (${positions.length})`} className="tt-positions">
        {positions.length?positions.slice(0,5).map(p=><div className="tt-ai-line" key={p.id}><span>{p.symbol} · {p.side}</span><b>{fmt(p.unrealized_pnl)} USDT</b></div>):<div className="tt-empty">لا توجد مراكز مسجلة حالياً</div>}
        <button className="tt-secondary" onClick={()=>setTab('portfolio')}>تفاصيل المراكز</button>
      </Panel>
      <Panel title="آخر الصفقات" className="tt-trades">
        {trades.slice(0,7).map(t=><div className="tt-ai-line" key={t.id}><span>{new Date(t.time).toLocaleTimeString()}</span><b className={t.isBuyerMaker?'negative':'positive'}>{fmt(t.price,price<1?5:2)}</b></div>)}
        {!trades.length&&<div className="tt-empty">لا توجد بيانات صفقات حالياً</div>}
      </Panel>
    </div>}

    {tab==='trade'&&<div className="tt-trading-layout">
      <Panel title="تنفيذ Demo / Shadow">
        <div className="tt-muted">الأوامر هنا تُسجّل في محرك Shadow الداخلي فقط. لا يتم إرسالها إلى Binance.</div>
        <div className="tt-side-toggle"><button className={orderSide==='buy'?'buy':''} onClick={()=>setOrderSide('buy')}>شراء</button><button className={orderSide==='sell'?'sell':''} onClick={()=>setOrderSide('sell')}>بيع</button></div>
        <form onSubmit={submitShadow} className="tt-order-form">
          <label>الزوج</label><input value={selectedLabel} readOnly/>
          <label>نوع الأمر</label><select value={orderType} onChange={e=>setOrderType(e.target.value)}><option value="market">Market</option><option value="limit">Limit</option></select>
          {orderType==='limit'&&<><label>السعر</label><input type="number" step="any" value={limitPrice} onChange={e=>setLimitPrice(e.target.value)} required/></>}
          <label>الكمية</label><input type="number" min="0.000001" step="any" value={quantity} onChange={e=>setQuantity(e.target.value)} required/>
          <button className="tt-primary" disabled={busy}>إرسال أمر Shadow التجريبي</button>
        </form>
      </Panel>
      <Panel title="بيانات السوق">
        <div className="tt-price-large">{ticker?fmt(price,price<1?5:2):'—'} USDT</div>
        <div className="tt-ai-line"><span>التغير 24h</span><b>{ticker?.priceChangePercent??'—'}%</b></div>
        <div className="tt-ai-line"><span>أعلى سعر</span><b>{ticker?fmt(ticker.highPrice):'—'}</b></div>
        <div className="tt-ai-line"><span>أدنى سعر</span><b>{ticker?fmt(ticker.lowPrice):'—'}</b></div>
      </Panel>
    </div>}

    {tab==='scanner'&&<SmartScanner market={market} balance={balance??150} maxLeverage={3}/>}
    {tab==='ai'&&<div className="tt-feature-grid"><Panel title="محادثة TITAN AI">
      <div className="tt-chat">{chat.map((m,i)=><p key={i}><b>{m.role==='user'?'أنت':'TITAN'}:</b> {m.text}</p>)}</div>
      <form onSubmit={sendChat} className="tt-chat-form"><input value={chatInput} onChange={e=>setChatInput(e.target.value)} placeholder="اسأل TITAN عن السوق أو قراراته أو تقاريره..." /><button className="tt-primary">إرسال</button></form>
      <div className="tt-report-feed"><div className="tt-ai-line"><b>تقارير TITAN الكاملة</b><button className="tt-secondary" onClick={refreshChatReports}>تحديث التقارير</button></div>
        {chatReports?<><small className="tt-muted">آخر تحديث: {chatReports.generated_at}</small><pre>{JSON.stringify(chatReports,null,2)}</pre></>:<p className="tt-muted">جارٍ تحميل التقارير...</p>}
      </div>
    </Panel>
    <Panel title="حالة المحركات الفعلية">
      {engineItems.length?engineItems.map(engine=><div className="tt-ai-line" key={engine.key}><span>{engine.name||engine.key}<small>{engine.integration?.mode||'غير مصنف'}</small></span><b>{engine.status||'غير محدد'} · {engine.runtime_check?.status==='wired'?'الربط سليم':engine.runtime_check?.status||'غير مفحوص'}</b></div>):<p className="tt-muted">لم يرجع الخادم سجلاً لحالة المحركات.</p>}<p className="tt-muted">فحص الربط يتحقق من وجود الدوال المطلوبة؛ ولا يثبت وحده اتصال البورصة أو مزود خارجي.</p>
    </Panel>
    <Panel title="مزودو الذكاء الاصطناعي">
      {aiProviderState?.items?.length?aiProviderState.items.map(provider=><div className="tt-provider-row" key={provider.id}>
        <div><b>{provider.name||provider.provider}</b><small>{provider.provider} · {provider.model} · {provider.enabled?'مفعّل':'متوقف'}</small></div>
        <div className="tt-provider-actions"><button className="tt-secondary" disabled={aiProviderBusy===provider.id} onClick={()=>testAiProvider(provider.id)}>اختبار</button><button className="tt-secondary" disabled={aiProviderBusy===provider.id} onClick={()=>controlAiProvider(provider.id,!provider.enabled)}>{provider.enabled?'إيقاف':'تفعيل'}</button></div>
      </div>):<p className="tt-muted">لا توجد إعدادات مزود خارجي محفوظة. لن تظهر حالة مزود غير مهيأ على أنه متصل.</p>}
    </Panel>
    </div>}
    {tab==='strategies'&&<div className="tt-feature-grid"><BacktestPanel/><MLResearchPanel/></div>}
    {tab==='risk'&&<div className="tt-feature-grid"><Panel title="حالة الحماية وإدارة المخاطر"><pre>{JSON.stringify({state:status?.state,integrations:status?.integrations},null,2)}</pre><button className="tt-primary" onClick={()=>master('start')} disabled={busy || (selectedTarget==='live' && !liveApproval) || (['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state) && !(selectedTarget==='live' && status?.state?.live_confirmed_by_user!==true))}> {selectedTarget==='live'?'بدء التداول الحقيقي':selectedTarget==='testnet'?'بدء تداول Binance Testnet':'تشغيل Demo/Shadow'}</button><button className="tt-secondary" onClick={()=>master('stop')} disabled={busy}>إيقاف المحرك بالكامل</button><button className="tt-danger" onClick={()=>master('emergency')} disabled={busy}>إيقاف الطوارئ</button></Panel><Panel title="التشخيص الذاتي"><button className="tt-primary" onClick={runDiagnosis} disabled={busy}>تشغيل التشخيص</button>{diagnosis&&<pre>{JSON.stringify(diagnosis,null,2)}</pre>}</Panel></div>}
    {tab==='portfolio'&&<div className="tt-feature-grid"><Panel title="المحفظة الداخلية"><h2>{balance===null?'—':fmt(balance)+' USDT'}</h2><button className="tt-secondary" onClick={refreshSystem}>تحديث</button><pre>{JSON.stringify(portfolio,null,2)}</pre></Panel><PerformancePanel/></div>}
    {tab==='reports'&&<div className="tt-feature-grid"><PerformancePanel/><MarketMicrostructurePanel/></div>}
    {tab==='settings'&&<div className="tt-feature-grid"><Panel title="حساب Binance والتداول">
      <label>الحساب المستهدف</label><select value={selectedTarget} onChange={e=>{setSelectedTarget(e.target.value);setLiveApproval(false)}} disabled={['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}><option value="demo">Demo داخلي (Shadow)</option><option value="testnet">Binance Demo / Testnet (تداول آلي تجريبي)</option><option value="live">Binance Live (البدء بموافقتك فقط)</option></select>
      <label>السوق</label><select value={market} onChange={e=>setMarket(e.target.value)} disabled={['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}><option value="spot">Spot</option><option value="futures">USD-M Futures</option></select>
      <div className="tt-ai-line"><span>حالة Testnet</span><b>{accountHealth?.testnet?.connected?'متصل':accountHealth?.testnet?.error||'غير متصل'}</b></div>
      <div className="tt-ai-line"><span>حالة Live</span><b>{accountHealth?.live?.connected?'متصل للقراءة':accountHealth?.live?.error||'غير متصل'}</b></div>
      <div className="tt-ai-line"><span>تنفيذ Live</span><b>{accountHealth?.live?.execution_ready?'جاهز بعد الموافقة وفحوص الجاهزية':accountHealth?.live?.live_blocked?'ينتظر الموافقة وفحوص الجاهزية':'غير جاهز'}</b></div>
      <div className="tt-ai-line"><span>Preflight</span><b>{accountHealth?.preflight?.ready?'جاهز':accountHealth?.preflight?.error||'غير مفحوص'}</b></div>
      <div className="tt-ai-line"><span>مفاتيح Testnet {market}</span><b>{accountHealth?.testnetCreds?.configured?'محفوظة':'غير محفوظة'}</b></div>
      <button className="tt-secondary" onClick={saveTradingSelection} disabled={busy||['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)}>حفظ اختيار الحساب والسوق</button>
      <button className="tt-secondary" onClick={refreshAccountHealth}>فحص الاتصال والحالة</button>
      <div className="tt-order-form"><b>مفاتيح Binance Testnet · {market}</b><input type="password" autoComplete="off" placeholder="API Key" value={testnetApiKey} onChange={e=>setTestnetApiKey(e.target.value)}/><input type="password" autoComplete="new-password" placeholder="API Secret" value={testnetApiSecret} onChange={e=>setTestnetApiSecret(e.target.value)}/><button className="tt-secondary" onClick={saveTestnetKeys} disabled={busy||!testnetApiKey||!testnetApiSecret}>تحقق وحفظ وبدء Testnet تلقائياً</button></div>
      <div className="tt-order-form"><b>مفاتيح Binance Live · {market}</b><input type="password" autoComplete="off" placeholder="Live API Key (قراءة أولاً)" value={liveApiKey} onChange={e=>setLiveApiKey(e.target.value)}/><input type="password" autoComplete="new-password" placeholder="Live API Secret" value={liveApiSecret} onChange={e=>setLiveApiSecret(e.target.value)}/><button className="tt-secondary" onClick={saveLiveKeys} disabled={busy||!liveApiKey||!liveApiSecret}>تحقق وحفظ Live للقراءة فقط</button></div>
      <button className="tt-secondary" onClick={deleteLegacyLiveKeys} disabled={busy||!accountHealth?.liveCreds?.configured}>حذف مفاتيح Live لهذا السوق</button>
      {selectedTarget==='live'&&<label className="tt-consent-row"><input type="checkbox" checked={liveApproval} onChange={e=>setLiveApproval(e.target.checked)} disabled={busy||(['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)&&status?.state?.live_confirmed_by_user===true)}/> <b>مسموح ببدء التداول الحقيقي</b><small>{'ربط المفاتيح يتيح قراءة الحساب والتحليل فقط. لن تُرسل أي صفقة حقيقية حتى تفعّل هذا الخيار وتضغط «بدء التداول الحقيقي» وتؤكد الموافقة.'}</small></label>}
      <small className="tt-muted">Testnet يبدأ التداول التجريبي تلقائياً بعد نجاح الربط واجتياز فحوص الجاهزية. Live يبقى للقراءة فقط بعد الربط، ويحتاج موافقة صريحة جديدة لكل بدء. زر «إيقاف المحرك بالكامل» يلغي الموافقة ويوقف العامل، لكنه لا يغلق المراكز المفتوحة تلقائياً. زر «إيقاف التداول فقط» يوقف فتح صفقات جديدة مع استمرار إدارة المراكز. لا سحب أو تحويل؛ وتبقى حدود المخاطر والإيقاف الطارئ والمطابقة إلزامية.</small>
      <button className="tt-primary" onClick={()=>master('start')} disabled={busy||(selectedTarget==='live'&&!liveApproval)||(['starting','running','analyzing','executing','waiting_data'].includes(status?.state?.bot_run_state)&&!(selectedTarget==='live'&&status?.state?.live_confirmed_by_user!==true))}> {selectedTarget==='live'?'بدء التداول الحقيقي':selectedTarget==='testnet'?'بدء تداول Binance Testnet':'تشغيل Demo/Shadow'}</button>
      <button className="tt-secondary" onClick={()=>master('stop')} disabled={busy}>إيقاف المحرك بالكامل</button>
    </Panel><Panel title="إعدادات TITAN"><div className="tt-ai-line"><span>البيئة</span><b>{status?.state?.environment_mode||'demo'}</b></div><div className="tt-ai-line"><span>السوق</span><b>{market}</b></div><div className="tt-ai-line"><span>المصادقة الثنائية</span><b>{auth.me?.two_factor_enabled?'مفعّلة':'غير مفعّلة'}</b></div>{auth.me?.authenticated&&!auth.me?.two_factor_enabled&&<button className="tt-secondary" disabled={busy} onClick={startTwoFactorSetup}>إعداد تطبيق المصادقة</button>}{twoFactorSetup&&<div className="tt-order-form"><p>أضف هذا المفتاح إلى تطبيق Authenticator:</p><code>{twoFactorSetup.secret}</code><p className="tt-muted">أو استعمل رابط الإعداد داخل تطبيق يدعم QR.</p><input value={twoFactorCode} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="رمز من 6 أرقام" onChange={e=>setTwoFactorCode(e.target.value.replace(/\D/g,'').slice(0,6))}/><button className="tt-primary" disabled={busy||twoFactorCode.length!==6} onClick={confirmTwoFactorSetup}>تأكيد وتفعيل 2FA</button></div>}{auth.me?.two_factor_enabled&&<div className="tt-order-form"><input value={twoFactorCode} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="رمز المصادقة الحالي" onChange={e=>setTwoFactorCode(e.target.value.replace(/\D/g,'').slice(0,6))}/><button className="tt-secondary" disabled={busy||twoFactorCode.length!==6} onClick={disableTwoFactor}>تعطيل 2FA بعد التحقق</button></div>}<button className="tt-secondary" onClick={refreshSystem}>تحديث الحالة</button><button className="tt-primary" onClick={()=>setTab('risk')}>فتح الحماية والتشخيص</button></Panel><TelegramPanel/></div>}
    <footer className="tt-footer"><b>TITAN SUPER AI PREMIUM</b><span>TRADE SMARTER · TRADE SAFER</span><small>بيانات السوق عامة للقراءة فقط · لا توجد صلاحيات إيداع أو سحب أو تحويل</small></footer>
  </main>
}

function analysisReady(s) { return !!s?.state && s?.state?.emergency_stop!==true }
