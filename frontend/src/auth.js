// Client-side auth: token + expiry in localStorage, attached as a Bearer header
// on protected requests. The dashboard renders read-only without a token; login
// is only prompted when a protected action is attempted (see App.jsx).

const TOKEN_KEY = 'admindash_token'
const EXP_KEY = 'admindash_token_exp'
const USER_KEY = 'admindash_user'

export function getToken() {
  const exp = Number(localStorage.getItem(EXP_KEY) || 0)
  if (!exp || Date.now() / 1000 >= exp) {
    clearToken()
    return null
  }
  return localStorage.getItem(TOKEN_KEY)
}

export function isAuthed() {
  return !!getToken()
}

export function currentUser() {
  return isAuthed() ? localStorage.getItem(USER_KEY) : null
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(EXP_KEY)
  localStorage.removeItem(USER_KEY)
}

export async function login(username, password) {
  const r = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!r.ok) {
    const e = await r.json().catch(() => ({}))
    throw new Error(e.error || 'Login failed')
  }
  const d = await r.json()
  localStorage.setItem(TOKEN_KEY, d.token)
  localStorage.setItem(EXP_KEY, String(d.expires_at))
  localStorage.setItem(USER_KEY, d.username)
  return d
}

// Bearer-authenticated fetch. On 401 the token is cleared so callers can re-prompt.
export async function authedFetch(path, options = {}) {
  const token = getToken()
  const headers = { ...(options.headers || {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const r = await fetch(path, { ...options, headers })
  if (r.status === 401) clearToken()
  return r
}
