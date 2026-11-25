import React, { useState } from 'react'
import { api } from './api'

export default function EconomicCalendarPanel() {
  const today = new Date().toISOString().slice(0, 10)
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10))
  const [importance, setImportance] = useState('high')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function load(e) {
    e.preventDefault(); setBusy(true); setError(''); setResult(null)
    try { setResult(await api.economicCalendar({ from, to, importance })) }
    catch (err) { setError(err.message || 'تعذر جلب التقويم الاقتصادي') }
    finally { setBusy(false) }
  }

  return <div className="card">
    <h3>التقويم الاقتصادي الأمريكي</h3>
    <div className="notice">المصدر: مزود XOOMAR الذي يجمع جداول الجهات الأمريكية الرسمية (BLS وBEA وCensus والاحتياطي الفيدرالي وDOL وTreasury). يحتفظ النظام باسم الجهة والوقت UTC والإسناد. لا يوفر هذا المصدر توقعات إجماع المحللين؛ وقد تتأخر القيم الفعلية أو تُراجع.</div>
    <form onSubmit={load}>
      <div className="row" style={{marginTop:10}}>
        <div style={{flex:1}}><label>من</label><input type="date" value={from} onChange={e=>setFrom(e.target.value)} /></div>
        <div style={{flex:1}}><label>إلى</label><input type="date" value={to} onChange={e=>setTo(e.target.value)} /></div>
        <div style={{flex:1}}><label>الأهمية</label><select value={importance} onChange={e=>setImportance(e.target.value)}><option value="high">عالية</option><option value="med">متوسطة</option><option value="low">منخفضة</option><option value="all">الكل</option></select></div>
      </div>
      <button disabled={busy}>{busy ? 'جارٍ التحديث...' : 'تحديث التقويم'}</button>
    </form>
    {error && <div className="error" style={{marginTop:10}}>{error}</div>}
    {result && result.ok && <>
      <div className="row" style={{marginTop:12}}>
        <b>عدد الأحداث: {result.count}</b>
        <span className="muted">التحديث: {result.updated_at || 'غير محدد'}</span>
      </div>
      <div className="table-wrap"><table><thead><tr><th>الحدث</th><th>الجهة</th><th>الوقت UTC</th><th>الأهمية</th><th>السابق</th><th>الفعلي</th></tr></thead>
        <tbody>{(result.events || []).map((event,i)=><tr key={event.source + '-' + event.scheduled_at + '-' + i}>
          <td>{event.event_name}<div className="muted">{event.period_label || ''}</div></td>
          <td>{event.source}</td><td>{event.scheduled_at}</td><td>{event.importance}</td>
          <td>{event.previous ?? '—'} {event.unit || ''}</td><td>{event.actual ?? '—'} {event.unit || ''}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="muted">المصدر: {result.provider} — {result.source_attribution || result.provider_source || 'غير محدد'}</p>
      <p className="muted">التوقعات غير متوفرة من هذا المصدر. القيم الفعلية قد تتأخر أو تُراجع بعد النشر.</p>
      <p className="muted">الترخيص: {result.license || 'غير محدد'} · الإسناد مطلوب: {String(result.attribution_required ?? true)}</p>
    </>}
    {result && !result.ok && <div className="error" style={{marginTop:10}}>{result.error || result.detail || 'التقويم غير متاح حالياً'}</div>}
  </div>
}
