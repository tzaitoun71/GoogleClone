// Thin wrapper around the two endpoints in server.py. Kept separate from the
// components so the UI never has to know about URLs or query-string encoding.

async function get(path) {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`)
  }
  return response.json()
}

export function search(query, limit = 10) {
  // encodeURIComponent so queries with spaces, &, or # don't corrupt the URL.
  return get(`/api/search?q=${encodeURIComponent(query)}&limit=${limit}`)
}

export function stats() {
  return get('/api/stats')
}
