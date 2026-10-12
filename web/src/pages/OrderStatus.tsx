import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, toApiError } from '../api/client'
import { getOrder } from '../api/endpoints'
import type { Order } from '../api/types'
import { ConnectionBadge } from '../components/ConnectionBadge'
import { Receipt } from '../components/order/Receipt'
import { StatusTimeline } from '../components/order/StatusTimeline'
import { useLiveSocket } from '../hooks/useLiveSocket'
import { parseFrame, isNewer } from '../api/ws'
import { RESTAURANT_NAME } from '../config'

const HEADLINE: Record<Order['status'], { title: string; detail: string }> = {
  received: { title: 'Received', detail: 'The kitchen has your order.' },
  preparing: { title: 'Preparing', detail: 'Your food is being made.' },
  ready: { title: 'Ready', detail: 'It is ready and on its way to your table.' },
  served: { title: 'Served', detail: 'Enjoy your meal.' },
  cancelled: { title: 'Cancelled', detail: 'This order will not be made.' },
}

function lastTable(): string | null {
  try {
    return window.localStorage.getItem('lastTable')
  } catch {
    return null
  }
}

export default function OrderStatus() {
  const { publicId = '' } = useParams()
  const [order, setOrder] = useState<Order | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const generation = useRef(0)

  // REST snapshot. Runs on mount, on every socket open, and on a resync frame.
  // A newer generation supersedes older responses; updated_at guards against stale data.
  const sync = useCallback(async () => {
    const mine = ++generation.current
    try {
      const snap = await getOrder(publicId)
      if (mine !== generation.current) return
      setOrder((prev) => (isNewer(snap, prev ?? undefined) ? snap : prev))
      setError(null)
    } catch (e) {
      if (mine === generation.current) setError(toApiError(e))
    }
  }, [publicId])

  useEffect(() => {
    void sync()
  }, [sync])

  // A malformed or unknown id will never succeed, so stop retrying the socket for it.
  const terminal = error?.kind === 'not_found' || error?.kind === 'validation'
  const live = useLiveSocket(publicId && !terminal ? `/ws/orders/${publicId}` : null, {
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
      // staff.requests frames never reach diner sockets; ignore them defensively.
      if (frame.type !== 'order.created' && frame.type !== 'order.status_changed') return
      if (frame.order.public_id !== publicId) return
      setOrder((prev) => (isNewer(frame.order, prev ?? undefined) ? frame.order : prev))
    },
  })

  const table = lastTable()

  if (terminal) {
    return (
      <main className="mx-auto max-w-xl px-4 py-12">
        <h1 className="font-display text-3xl font-semibold">We cannot find this order</h1>
        <p className="mt-3 text-[1.0625rem] leading-7 text-ink-soft">Check the link, or ask your server for help.</p>
      </main>
    )
  }

  const head = order ? HEADLINE[order.status] : null

  return (
    <main className="mx-auto max-w-xl px-4 pt-6 pb-16">
      <header className="flex items-center justify-between gap-3">
        <p className="font-display text-lg font-semibold">{RESTAURANT_NAME}</p>
        <ConnectionBadge status={live} liveLabel="Live updates" />
      </header>

      {error && (
        <p role="alert" className="mt-6 text-ink">
          {error.messages[0]} Updates will resume when the connection is back.
        </p>
      )}

      {!order && !error && <p className="mt-10 text-ink-soft">Loading your order…</p>}

      {order && head && (
        <>
          <section aria-live="polite" className="mt-8">
            <p className="text-sm text-ink-soft">Order {order.public_id.slice(0, 8)}</p>
            <h1 className="mt-1 font-display text-[2.75rem] leading-none font-semibold">{head.title}</h1>
            <p className="mt-2 text-[1.0625rem] leading-7 text-ink-soft">{head.detail}</p>
          </section>

          {order.status === 'cancelled' ? (
            <p role="status" className="mt-8 rounded-2xl border-2 border-chili bg-chili-soft p-4 text-ink">
              This order was cancelled. Ask your server if you have questions.
            </p>
          ) : (
            <div className="mt-8">
              <StatusTimeline status={order.status} />
            </div>
          )}

          <div className="mt-10">
            <Receipt order={order} />
          </div>
        </>
      )}

      <footer className="mt-10">
        {table ? (
          <Link
            to={`/t/${table}`}
            className="inline-flex min-h-12 items-center rounded-full border-2 border-ink px-6 text-[1.0625rem] font-bold"
          >
            Back to the menu
          </Link>
        ) : (
          <p className="text-ink-soft">Scan the code on your table to return to the menu.</p>
        )}
      </footer>
    </main>
  )
}
