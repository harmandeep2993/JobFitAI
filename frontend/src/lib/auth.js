const TOKEN_KEY = 'jfai_token'

export function saveToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

// No getUser() decoding the JWT: the token lives in localStorage, which any
// script on the page can read, so it carries the user id only. Components that
// need the email fetch it from GET /api/auth/me over an authenticated request.

export function isAuthed() {
  return Boolean(getToken())
}

export async function apiFetch(url, options = {}) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(url, { ...options, headers })
  if (res.status === 401) {
    // Expired or invalidated session - clear it and send the user back to
    // login instead of letting every tab surface its own opaque error.
    clearToken()
    window.location.href = '/login'
    return res
  }
  return res
}

export function logout() {
  clearToken()
  window.location.href = '/'
}
