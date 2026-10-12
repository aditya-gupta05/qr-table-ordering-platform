import { useCallback, useEffect, useMemo, useState } from 'react'
import { MAX_QTY } from '../config'

export interface CartLine {
  menuItemId: number
  /** Snapshot for display only. The server re-prices from the DB. */
  name: string
  priceCents: number
  qty: number
  notes: string
}

interface CartState {
  lines: CartLine[]
  orderNotes: string
}

const EMPTY: CartState = { lines: [], orderNotes: '' }

function storageKey(token: string) {
  return `cart:${token}`
}

function load(token: string): CartState {
  try {
    const raw = window.localStorage.getItem(storageKey(token))
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<CartState>
    return {
      lines: Array.isArray(parsed.lines) ? parsed.lines : [],
      orderNotes: typeof parsed.orderNotes === 'string' ? parsed.orderNotes : '',
    }
  } catch {
    // Storage can be blocked (private mode). The cart then lives only in memory.
    return EMPTY
  }
}

function save(token: string, state: CartState) {
  try {
    window.localStorage.setItem(storageKey(token), JSON.stringify(state))
  } catch {
    /* ignore: cart still works in memory */
  }
}

export function useCart(token: string) {
  const [state, setState] = useState<CartState>(() => load(token))

  useEffect(() => {
    save(token, state)
  }, [token, state])

  const addItem = useCallback((item: { id: number; name: string; priceCents: number }) => {
    setState((s) => {
      const existing = s.lines.find((l) => l.menuItemId === item.id)
      if (existing) {
        return {
          ...s,
          lines: s.lines.map((l) =>
            l.menuItemId === item.id ? { ...l, qty: Math.min(MAX_QTY, l.qty + 1) } : l,
          ),
        }
      }
      const line: CartLine = { menuItemId: item.id, name: item.name, priceCents: item.priceCents, qty: 1, notes: '' }
      return { ...s, lines: [...s.lines, line] }
    })
  }, [])

  const setQty = useCallback((menuItemId: number, qty: number) => {
    setState((s) => ({
      ...s,
      lines:
        qty <= 0
          ? s.lines.filter((l) => l.menuItemId !== menuItemId)
          : s.lines.map((l) => (l.menuItemId === menuItemId ? { ...l, qty: Math.min(MAX_QTY, qty) } : l)),
    }))
  }, [])

  const setLineNotes = useCallback((menuItemId: number, notes: string) => {
    setState((s) => ({
      ...s,
      lines: s.lines.map((l) => (l.menuItemId === menuItemId ? { ...l, notes } : l)),
    }))
  }, [])

  const setOrderNotes = useCallback((orderNotes: string) => {
    setState((s) => ({ ...s, orderNotes }))
  }, [])

  const clear = useCallback(() => setState(EMPTY), [])

  const count = useMemo(() => state.lines.reduce((n, l) => n + l.qty, 0), [state.lines])
  const totalCents = useMemo(() => state.lines.reduce((n, l) => n + l.qty * l.priceCents, 0), [state.lines])

  return { ...state, count, totalCents, addItem, setQty, setLineNotes, setOrderNotes, clear }
}

export type Cart = ReturnType<typeof useCart>
