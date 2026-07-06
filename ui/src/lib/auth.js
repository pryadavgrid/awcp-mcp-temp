// IAM for the SPA using the app's OWN login form — no redirect to Keycloak's
// hosted page. The Operator Identifier + Security Key are POSTed straight to
// Keycloak's token endpoint (OAuth2 Direct Access Grant / password grant) for the
// public `awcp-ui` client; the returned access token is attached to every API call
// and silently refreshed. A refresh token is kept so a reload resumes the session.
import { KEYCLOAK_URL, KEYCLOAK_REALM, KEYCLOAK_CLIENT } from '../config'

const TOKEN_EP = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token`
const LOGOUT_EP = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/logout`
const LS_KEY = 'awcp-refresh-token'

let _access = ''
let _accessExp = 0 // epoch ms (already minus a safety margin)
let _refresh = ''

function _store(data) {
  _access = data.access_token || ''
  _accessExp = Date.now() + (Number(data.expires_in || 60) - 15) * 1000
  _refresh = data.refresh_token || ''
  try {
    if (_refresh) localStorage.setItem(LS_KEY, _refresh)
  } catch {
    /* storage may be unavailable */
  }
}

function _clear() {
  _access = ''
  _accessExp = 0
  _refresh = ''
  try {
    localStorage.removeItem(LS_KEY)
  } catch {
    /* ignore */
  }
}

async function _tokenRequest(params) {
  const res = await fetch(TOKEN_EP, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: KEYCLOAK_CLIENT, ...params }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error_description || data.error || `HTTP ${res.status}`)
  return data
}

function _humanize(msg) {
  if (/invalid_grant|invalid user cred|account is dis/i.test(msg)) return 'Invalid credentials.'
  if (/unauthorized_client|not allowed/i.test(msg)) return 'This client can’t sign in directly — contact an admin.'
  if (/Failed to fetch|NetworkError|ECONN/i.test(msg)) return 'Can’t reach the identity server.'
  return msg || 'Sign-in failed.'
}

async function _refreshTokens() {
  if (!_refresh) return false
  try {
    _store(await _tokenRequest({ grant_type: 'refresh_token', refresh_token: _refresh }))
    return true
  } catch {
    _clear()
    return false
  }
}

// Authenticate with the form's username + password. Returns {ok} or {ok:false,error}.
export async function loginWithPassword(username, password) {
  try {
    _store(await _tokenRequest({ grant_type: 'password', username, password, scope: 'openid' }))
    return { ok: true }
  } catch (e) {
    return { ok: false, error: _humanize(String(e && e.message)) }
  }
}

export function isAuthenticated() {
  return !!_access
}

// The signed-in username, read from the access token (preferred_username). "" when
// there is no session.
export function getUsername() {
  if (!_access) return ''
  try {
    const payload = JSON.parse(
      atob(_access.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')),
    )
    return payload.preferred_username || payload.sub || ''
  } catch {
    return ''
  }
}

// On load: resume a session from a persisted refresh token (survives reload).
export async function initAuth() {
  try {
    _refresh = localStorage.getItem(LS_KEY) || ''
  } catch {
    _refresh = ''
  }
  const authenticated = _refresh ? await _refreshTokens() : false
  return { authenticated }
}

// Current access token, refreshed if it has expired. "" when there is no session.
export async function getToken() {
  if (_access && Date.now() < _accessExp) return _access
  const ok = await _refreshTokens()
  return ok ? _access : ''
}

export function logout() {
  const rt = _refresh
  _clear()
  if (rt) {
    try {
      fetch(LOGOUT_EP, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ client_id: KEYCLOAK_CLIENT, refresh_token: rt }),
        keepalive: true,
      })
    } catch {
      /* best-effort revoke */
    }
  }
  if (typeof window !== 'undefined') window.location.reload()
}
