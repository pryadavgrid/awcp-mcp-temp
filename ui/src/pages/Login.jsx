import { useState } from 'react'
import { toggleThemeReveal } from '../lib/themeReveal.js'
import { loginWithPassword } from '../lib/auth'
import { signup } from '../api'
import logo from '../assets/awcp-logo-anim.webp'

// Login gate (visual only for now — submitting just calls onLogin, which reveals
// the app). Styled with the project's own font (Plus Jakarta Sans) and brand-green
// palette, over the theme-aware dynamic shader background. Respects the app theme
// set pre-paint in index.html, with a small toggle so both modes can be previewed.

// Small line icons in the app's stroke style (avoids pulling in an icon font).
const IconMail = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="m3.5 7 8.5 6 8.5-6" />
  </svg>
)
const IconLock = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="2.5" />
    <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
  </svg>
)
const IconArrow = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M5 12h13" />
    <path d="m13 6 6 6-6 6" />
  </svg>
)
const IconSun = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
  </svg>
)
const IconMoon = (p) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </svg>
)

export default function Login({ onLogin }) {
  const [isDark, setIsDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  )
  const toggleTheme = (e) => toggleThemeReveal(e, isDark, setIsDark)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [applyOpen, setApplyOpen] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setError('')
    setBusy(true)
    // Authenticate THIS form against Keycloak (no redirect). On success reveal the
    // app in place via onLogin; on failure show the reason under the button.
    const { ok, error: err } = await loginWithPassword(username.trim(), password)
    setBusy(false)
    if (ok) onLogin && onLogin()
    else setError(err || 'Sign-in failed.')
  }

  const label =
    'text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400'
  const input =
    'w-full rounded-lg border border-slate-300 bg-white/70 py-3 pl-11 pr-4 text-sm text-brand-900 outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 dark:border-white/10 dark:bg-[#0f1714] dark:text-[#e8f0ea] dark:placeholder:text-slate-500'

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#f2f6f2] font-sans text-brand-900 dark:bg-[#0e1512] dark:text-[#e8f0ea]">
      {/* Static background matching the app's page colour — #f2f6f2 (light) /
          #0e1512 (dark) — with soft brand-green radials. No animation in either. */}
      <div
        className="absolute inset-0"
        style={{
          background: isDark
            ? 'radial-gradient(1100px 620px at 50% -12%, rgba(69,176,106,0.10), transparent 62%), radial-gradient(900px 520px at 100% 112%, rgba(69,176,106,0.06), transparent 60%), #0e1512'
            : 'radial-gradient(1100px 620px at 50% -12%, rgba(58,125,82,0.12), transparent 62%), radial-gradient(900px 520px at 100% 112%, rgba(58,125,82,0.08), transparent 60%), #f2f6f2',
        }}
      />

      {/* Theme toggle (both modes previewable). */}
      <button
        onClick={toggleTheme}
        title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        aria-label="Toggle theme"
        className="absolute right-4 top-4 z-20 grid h-10 w-10 place-items-center rounded-xl border border-slate-200/70 bg-white/60 text-slate-600 backdrop-blur transition hover:text-brand-600 dark:border-white/10 dark:bg-white/5 dark:text-slate-300"
      >
        {isDark ? <IconSun className="h-5 w-5" /> : <IconMoon className="h-5 w-5" />}
      </button>

      <main className="relative z-10 flex flex-grow items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">
          {/* Branding header — our dynamic logo, brand heading, italic tagline. */}
          <div className="mb-8 text-center">
            <div className="mb-4 inline-flex items-center justify-center rounded-2xl border border-brand-600/15 bg-white/70 p-2 shadow-card backdrop-blur dark:border-white/10 dark:bg-white/5">
              <img src={logo} alt="AWCP" className="h-16 w-16 rounded-xl" />
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight text-brand-700 dark:text-[#62c188] sm:text-[2.6rem] sm:leading-[1.05]">
              Agent Workforce Control Plane
            </h1>
            <p className="mt-3 text-base italic text-brand-800/70 dark:text-slate-300/80">
              Orchestrate your agent workforce
            </p>
          </div>

          {/* Login card. */}
          <div className="rounded-2xl border border-slate-200 bg-white/80 p-6 shadow-card-hover backdrop-blur-md dark:border-white/10 dark:bg-[#16201b]/90">
            <form className="space-y-5" onSubmit={submit}>
              <div className="space-y-2">
                <label className={label} htmlFor="email">
                  Operator Identifier
                </label>
                <div className="relative">
                  <IconMail className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-400" />
                  <input
                    id="email"
                    type="text"
                    autoComplete="username"
                    placeholder="operator-test"
                    className={input}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label className={label} htmlFor="password">
                    Security Key
                  </label>
                  <a href="#" className="text-[11px] font-semibold text-brand-600 hover:underline dark:text-[#62c188]">
                    Forgot password?
                  </a>
                </div>
                <div className="relative">
                  <IconLock className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-400" />
                  <input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    placeholder="••••••••"
                    className={input}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
              </div>

              {error && (
                <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  {error}
                </p>
              )}

              <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                <input type="checkbox" className="h-4 w-4 rounded accent-brand-600" defaultChecked />
                Keep session active
              </label>

              <button
                type="submit"
                disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 py-3.5 text-sm font-bold text-white shadow-sm transition hover:bg-brand-700 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-70"
              >
                <IconArrow className="h-5 w-5" />
                {busy ? 'Establishing…' : 'Establish Connection'}
              </button>
            </form>

            <div className="mt-6 border-t border-slate-200 pt-5 text-center text-sm text-slate-600 dark:border-white/10 dark:text-slate-300">
              New operator?
              <button
                type="button"
                onClick={() => setApplyOpen(true)}
                className="ml-1.5 font-bold text-brand-600 hover:underline dark:text-[#62c188]"
              >
                Create an account
              </button>
            </div>
          </div>
        </div>
      </main>

      <footer className="relative z-10 w-full py-6 text-center text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400 dark:text-slate-500">
        © 2026 Agent Workforce Control Plane
      </footer>

      {applyOpen && (
        <CreateAccountModal
          onClose={() => setApplyOpen(false)}
          onCreated={(u, p) => {
            setUsername(u)
            setPassword(p)
            setApplyOpen(false)
          }}
          labelCls={label}
          inputCls={input}
        />
      )}
    </div>
  )
}

// "Create account" — makes a Keycloak account with the chosen username + password
// (default role) so the user can sign in immediately. On success it fills the login
// form with the new credentials and closes.
function CreateAccountModal({ onClose, onCreated, labelCls, inputCls }) {
  const [username, setName] = useState('')
  const [password, setPass] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const cls = inputCls.replace('pl-11', 'px-4')

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setError('')
    if (username.trim().length < 3) return setError('Username must be at least 3 characters.')
    if (password.length < 6) return setError('Password must be at least 6 characters.')
    if (password !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    try {
      await signup(username.trim(), password)
      onCreated(username.trim(), password)
    } catch (err) {
      setError(String(err?.message || 'Could not create the account.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-card-hover dark:border-white/10 dark:bg-[#16201b]">
        <div className="mb-4">
          <h3 className="text-lg font-extrabold tracking-tight text-brand-900">Create account</h3>
          <p className="mt-1 text-sm text-slate-500">Pick a username and password, and sign in right away.</p>
        </div>

        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <label className={labelCls}>Username</label>
            <input className={cls} autoComplete="username" value={username} onChange={(e) => setName(e.target.value)} placeholder="your-username" />
          </div>
          <div className="space-y-2">
            <label className={labelCls}>Password</label>
            <input className={cls} type="password" autoComplete="new-password" value={password} onChange={(e) => setPass(e.target.value)} placeholder="At least 6 characters" />
          </div>
          <div className="space-y-2">
            <label className={labelCls}>Confirm password</label>
            <input className={cls} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Re-enter password" />
          </div>

          {error && (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
              {error}
            </p>
          )}

          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose} className="flex-1 rounded-lg border border-slate-300 py-3 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 dark:border-white/15 dark:text-slate-300">
              Cancel
            </button>
            <button type="submit" disabled={busy} className="flex-1 rounded-lg bg-brand-600 py-3 text-sm font-bold text-white transition hover:bg-brand-700 disabled:opacity-70">
              {busy ? 'Creating…' : 'Create account'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
