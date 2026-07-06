import React, { useState, useEffect } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import { initAuth } from './lib/auth'
import './index.css'

// Entry flow: Landing → Login → App. The Login form authenticates against Keycloak
// in place (no redirect); on success it calls onLogin to reveal the app. On load we
// briefly try to resume a saved session (refresh token) so a reload keeps you in.
function Root() {
  const [stage, setStage] = useState('landing') // 'landing' | 'login' | 'app'
  const [booting, setBooting] = useState(true)

  useEffect(() => {
    let alive = true
    initAuth().then(({ authenticated }) => {
      if (!alive) return
      if (authenticated) setStage('app')
      setBooting(false)
    })
    return () => {
      alive = false
    }
  }, [])

  if (booting) return null // brief: resuming a saved session
  if (stage === 'app') return <App />
  if (stage === 'login') return <Login onLogin={() => setStage('app')} />
  return <Landing onEnter={() => setStage('login')} />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
