from pathlib import Path
p = Path('TITAN_SUPER_AI_PREMIUM_COMPLETE/frontend/src/App.jsx')
s = p.read_text()
old = '''{tab==='dashboard' && <div className="grid">
      <div className="card"><h3>لوحة المعلومات</h3>'''
if old in s:
    insert = '''
      <div className="card"><h3>Binance Testnet (تجريبي خارجي)</h3>
        <div>الحالة: <Pill status={binanceTestnet?.connected ? "good" : "warn"}>{binanceTestnet?.connected ? "متصل" : "غير متصل"}</Pill></div>
        <div>السوق: <span className="pill">{binanceTestnet?.market || "spot"}</span></div>
        <div>Base: <span className="muted">{binanceTestnet?.base_url || "-"}</span></div>
        <div className="muted" style={{marginTop:8}}>للتداول على Binance Testnet: أدخل مفاتيح Testnet في متغيرات البيئة (BINANCE_TESTNET_API_KEY / SECRET) ثم أعد التشغيل. لا مفاتيح حقيقية.</div>
      </div>
'''
    s = s.replace(old, old + insert)
    p.write_text(s)
    print('ok')
else:
    print('not found')
