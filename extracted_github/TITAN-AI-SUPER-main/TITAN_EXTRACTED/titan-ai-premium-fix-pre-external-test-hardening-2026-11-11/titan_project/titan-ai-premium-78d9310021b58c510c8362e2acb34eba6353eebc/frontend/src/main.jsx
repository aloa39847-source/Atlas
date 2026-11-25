import React from 'react'
import { createRoot } from 'react-dom/client'
import TerminalDashboard from './TerminalDashboard.jsx'
import './styles.css'
import './terminal.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <TerminalDashboard />
  </React.StrictMode>
)

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // The dashboard remains fully usable online if offline support is unavailable.
    })
  })
}
