async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  bay: () => req('/api/bay'),
  occupancies: () => req('/api/occupancies'),
  events: () => req('/api/events'),
  quarantine: () => req('/api/quarantine'),
  proofs: () => req('/api/occupancy-proofs'),
  verifyProof: (occupancy_id) => req(`/api/occupancy-proofs/${encodeURIComponent(occupancy_id)}/verify`),
  rebuildProof: (occupancy_id) =>
    req(`/api/occupancy-proofs/${encodeURIComponent(occupancy_id)}/rebuild`, {
      method: 'POST',
      body: JSON.stringify({ actor: 'ops-ui', reason: '页面临时重建' }),
    }),
  settings: () => req('/api/settings'),
  setOverdue: (overdue_hours) =>
    req('/api/settings/overdue-hours', { method: 'PUT', body: JSON.stringify({ overdue_hours }) }),
  ingest: () => req('/api/ingest/run', { method: 'POST', body: '{}' }),
  occupancyHashMembers: (id) => req(`/api/occupancy-hash-members/${id}`),
}
