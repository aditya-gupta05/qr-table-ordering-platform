import type { Frame, Order, OrderEnvelope, WaiterEnvelope } from './types'

// Same-origin URL so the Vite /ws proxy (dev) or the reverse proxy (prod) carries it.
export function wsUrl(path: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}${path}`
}

// Returns null for anything we do not understand; consumers ignore unknown frames.
export function parseFrame(raw: string): Frame | null {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  if (typeof data !== 'object' || data === null) return null
  const f = data as { type?: unknown; order?: unknown; waiter_request?: unknown }
  if (f.type === 'resync') return data as Frame
  if ((f.type === 'order.created' || f.type === 'order.status_changed') && typeof f.order === 'object' && f.order !== null) {
    return data as OrderEnvelope
  }
  if (
    (f.type === 'waiter.requested' || f.type === 'waiter.updated') &&
    typeof f.waiter_request === 'object' &&
    f.waiter_request !== null
  ) {
    return data as WaiterEnvelope
  }
  return null
}

// Backend timestamps carry microseconds ("...29.529591Z"); Date only guarantees milliseconds.
export function toMillis(iso: string): number {
  const ms = Date.parse(iso.replace(/(\.\d{3})\d+/, '$1'))
  return Number.isNaN(ms) ? 0 : ms
}

// Keep the incoming order unless we already hold a newer version of it.
export function isNewer(incoming: Order, held: Order | undefined): boolean {
  return !held || toMillis(incoming.updated_at) >= toMillis(held.updated_at)
}
