import { useCallback, useEffect, useRef, useState } from 'react'
import { describeError, toApiError } from '../api/client'
import { listKitchenOrders, updateOrderStatus } from '../api/endpoints'
import type { Order, OrderEnvelope, OrderStatus } from '../api/types'
import { ConnectionBadge } from '../components/ConnectionBadge'
import { Lane } from '../components/kitchen/Lane'
import { useLiveSocket } from '../hooks/useLiveSocket'
import { useNow } from '../hooks/useNow'
import { applyOrderToBoard, ACTIVE_STATUSES, sortByAge, type Board } from '../lib/orderStore'
import { parseFrame } from '../api/ws'

const FRESH_MS = 8000

export default function Kitchen() {
  const [board, setBoard] = useState<Board>(() => new Map())
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [pending, setPending] = useState<Set<number>>(() => new Set())
  const [fresh, setFresh] = useState<Set<number>>(() => new Set())
  const [notice, setNotice] = useState<string | null>(null)
  const now = useNow()

  // Mirrors `pending` synchronously so a double tap cannot slip past the React render cycle.
  const inFlight = useRef<Set<number>>(new Set())
  const generation = useRef(0)
  // While a snapshot is loading, live events are queued and applied after it, so none are lost.
  const syncing = useRef(false)
  const queued = useRef<OrderEnvelope[]>([])

  const sync = useCallback(async () => {
    const mine = ++generation.current
    syncing.current = true
    queued.current = []
    try {
      const list = await listKitchenOrders(ACTIVE_STATUSES)
      if (mine !== generation.current) return
      let next: Board = new Map(list.map((o) => [o.id, o]))
      for (const env of queued.current) next = applyOrderToBoard(next, env.order)
      queued.current = []
      syncing.current = false
      setBoard(next)
      setLoaded(true)
      setLoadError(null)
    } catch (e) {
      if (mine !== generation.current) return
      syncing.current = false
      queued.current = []
      setLoadError(describeError(e, 'Cannot load orders. Retrying when the connection is back.'))
    }
  }, [])

  useEffect(() => {
    void sync()
  }, [sync])

  useEffect(() => {
    if (fresh.size === 0) return
    const t = window.setTimeout(() => setFresh(new Set()), FRESH_MS)
    return () => window.clearTimeout(t)
  }, [fresh])

  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), 6000)
    return () => window.clearTimeout(t)
  }, [notice])

  const applyEnvelope = useCallback((env: OrderEnvelope) => {
    setBoard((prev) => applyOrderToBoard(prev, env.order))
    if (env.type === 'order.created' && ACTIVE_STATUSES.includes(env.order.status)) {
      setFresh((s) => new Set(s).add(env.order.id))
    }
  }, [])

  const live = useLiveSocket('/ws/kitchen', {
    onOpen: () => {
      void sync()
    },
    onMessage: (raw) => {
      const frame = parseFrame(raw)
      if (!frame) return
      if (frame.type === 'resync') {
        void sync()
        return
      }
      if (syncing.current) {
        queued.current.push(frame)
        return
      }
      applyEnvelope(frame)
    },
  })

  const setPendingFor = (id: number, on: boolean) => {
    if (on) inFlight.current.add(id)
    else inFlight.current.delete(id)
    setPending(new Set(inFlight.current))
  }

  const advance = async (order: Order, next: OrderStatus) => {
    if (inFlight.current.has(order.id)) return
    setPendingFor(order.id, true)
    // Optimistic: the card moves now. Served and cancelled leave the board.
    setBoard((prev) => applyOrderToBoard(prev, { ...order, status: next }))
    try {
      const updated = await updateOrderStatus(order.id, next)
      setBoard((prev) => applyOrderToBoard(prev, updated))
    } catch (e) {
      const err = toApiError(e)
      // Roll back, unless a newer server version already arrived for this order.
      setBoard((prev) => {
        const cur = prev.get(order.id)
        if (cur && cur.status !== next) return prev
        const restored = new Map(prev)
        restored.set(order.id, order)
        return restored
      })
      setNotice(`Table ${order.table_label}: ${err.messages[0] ?? 'update failed'}. The board was refreshed.`)
      void sync()
    } finally {
      setPendingFor(order.id, false)
    }
  }

  const sorted = sortByAge(board)
  const byStatus = (s: OrderStatus) => sorted.filter((o) => o.status === s)
  const clock = new Date(now).toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' })

  return (
    <div className="flex min-h-dvh flex-col bg-board text-chalk md:h-dvh">
      <header className="flex flex-wrap items-center justify-between gap-4 px-6 pt-5 pb-4">
        <div className="flex items-baseline gap-4">
          <h1 className="font-display text-4xl font-semibold">Kitchen</h1>
          <p className="text-xl font-bold tabular-nums text-chalk-dim">{sorted.length} open</p>
        </div>
        <div className="flex items-center gap-4">
          <p className="text-xl font-bold tabular-nums text-chalk-dim">{clock}</p>
          <ConnectionBadge status={live} tone="dark" />
        </div>
      </header>

      {loadError && (
        <p role="alert" className="mx-6 mb-3 rounded-xl bg-kit-late/20 px-4 py-2 text-lg font-bold">
          {loadError}
        </p>
      )}
      {!loaded && !loadError && <p className="px-6 text-xl text-chalk-dim">Loading orders…</p>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-6 pb-6 md:grid-cols-3">
        <Lane
          title="Received"
          status="received"
          accent="bg-kit-new"
          orders={byStatus('received')}
          now={now}
          fresh={fresh}
          pending={pending}
          onAdvance={advance}
        />
        <Lane
          title="Preparing"
          status="preparing"
          accent="bg-kit-cook"
          orders={byStatus('preparing')}
          now={now}
          fresh={fresh}
          pending={pending}
          onAdvance={advance}
        />
        <Lane
          title="Ready"
          status="ready"
          accent="bg-kit-ready"
          orders={byStatus('ready')}
          now={now}
          fresh={fresh}
          pending={pending}
          onAdvance={advance}
        />
      </div>

      {notice && (
        <div
          role="alert"
          className="fixed inset-x-6 bottom-6 z-50 rounded-2xl bg-kit-late px-5 py-4 text-lg font-bold text-board shadow-xl"
        >
          {notice}
        </div>
      )}
    </div>
  )
}
