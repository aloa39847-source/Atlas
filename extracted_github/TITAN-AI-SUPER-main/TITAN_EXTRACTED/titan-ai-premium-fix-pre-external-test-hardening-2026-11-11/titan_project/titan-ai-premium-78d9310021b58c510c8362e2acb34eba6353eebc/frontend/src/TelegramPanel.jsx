import React, { useState } from 'react'
import { api } from './api'

export default function TelegramPanel() {
  const [status, setStatus] = useState(null)
  const [chatId, setChatId] = useState('')
  const [message, setMessage] = useState('TITAN SUPER AI PREMIUM status check')
  const [updates, setUpdates] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  async function refreshStatus() {
    setBusy(true); setNote('')
    try { setStatus(await api.telegramStatus()) }
    catch (e) { setNote(e.message || 'تعذر فحص Telegram') }
    finally { setBusy(false) }
  }

  async function sendMessage(e) {
    e.preventDefault(); setBusy(true); setNote('')
    try {
      const result = await api.telegramSend({ chat_id: chatId, text: message })
      setNote(result.ok ? 'تم إرسال الرسالة.' : (result.error || 'فشل الإرسال'))
    } catch (err) { setNote(err.message || 'تعذر الإرسال') }
    finally { setBusy(false) }
  }

  async function poll() {
    setBusy(true); setNote('')
    try {
      const result = await api.telegramPoll({ timeout: 0, limit: 50 })
      setUpdates(result)
      setNote('تم جلب ' + (result.updates?.length || 0) + ' أمر مصرح؛ لم يتم تنفيذ أي أمر.')
    } catch (e) { setNote(e.message || 'تعذر جلب الرسائل') }
    finally { setBusy(false) }
  }

  return <div className="card" style={{marginTop:12}}>
    <h3>تكامل Telegram الآمن</h3>
    <div className="notice">يحتاج متغيرات البيئة على الخادم: TELEGRAM_BOT_TOKEN وTELEGRAM_ALLOWED_CHAT_IDS، مع تفعيل TITAN_TELEGRAM_ENABLED. لا تضع الرمز السري داخل الواجهة أو المحادثة. أوامر Start/Stop/Emergency لا تُنفّذ عبر Telegram؛ تتطلب تأكيدًا منفصلًا داخل التطبيق.</div>
    <button type="button" className="secondary" onClick={refreshStatus} disabled={busy}>فحص حالة الربط</button>
    {status && <pre>{JSON.stringify(status,null,2)}</pre>}
    <form onSubmit={sendMessage} style={{marginTop:10}}>
      <label>Chat ID المصرح</label><input value={chatId} onChange={e=>setChatId(e.target.value)} required />
      <label>رسالة اختبار</label><textarea value={message} onChange={e=>setMessage(e.target.value)} rows={2} required />
      <button disabled={busy}>إرسال رسالة آمنة</button>
    </form>
    <button type="button" className="secondary" onClick={poll} disabled={busy} style={{marginTop:10}}>قراءة الأوامر المصرح بها</button>
    {note && <div className="notice" style={{marginTop:8}}>{note}</div>}
    {updates && <details style={{marginTop:8}}><summary>الرسائل الواردة</summary><pre>{JSON.stringify(updates,null,2)}</pre></details>}
  </div>
}
