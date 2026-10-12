import { useState } from 'react'
import type { Order, OrderStatus } from '../../api/types'
import { KITCHEN_LATE_MIN, KITCHEN_WARN_MIN } from '../../config'

interface Props {
  order: Order
  ageMin: number
  fresh: boolean
  busy: boolean
  onAdvance: (next: OrderStatus) => void
  onCancel: () => void
}

// Each column has one forward action. Its color matches the lane header.
const NEXT: Partial<Record<OrderStatus, { next: OrderStatus; label: string; cls: string }>> = {
  received: { next: 'preparing', label: 'Start cooking', cls: 'bg-kit-new text-board' },
  preparing: { next: 'ready', label: 'Mark ready', cls: 'bg-kit-ready text-board' },
  ready: { next: 'served', label: 'Served', cls: 'bg-chalk text-board' },
}

function ageTone(min: number) {
  if (min >= KITCHEN_LATE_MIN) return { edge: 'border-kit-late', text: 'text-kit-late', bg: 'bg-kit-late/15' }
  if (min >= KITCHEN_WARN_MIN) return { edge: 'border-kit-new', text: 'text-kit-new', bg: 'bg-tile' }
  return { edge: 'border-chalk-dim/40', text: 'text-chalk-dim', bg: 'bg-tile' }
}

export function TicketCard({ order, ageMin, fresh, busy, onAdvance, onCancel }: Props) {
  const [confirming, setConfirming] = useState(false)
  const tone = ageTone(ageMin)
  const action = NEXT[order.status]
  const cancellable = order.status === 'received' || order.status === 'preparing'

  return (
    <article
      aria-label={`Table ${order.table_label}, ${ageMin} minutes old, ${order.status}`}
      className={`rounded-2xl border-l-[10px] p-4 ${tone.edge} ${tone.bg} ${
        fresh ? 'motion-safe:animate-arrive' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-display text-6xl leading-none font-bold">{order.table_label}</p>
        <div className="text-right">
          <p className={`text-4xl leading-none font-bold tabular-nums ${tone.text}`}>
            {ageMin}
            <span className="text-xl font-bold"> min</span>
          </p>
          {fresh && <p className="mt-1.5 text-base font-bold text-kit-new">New</p>}
        </div>
      </div>

      <ul className="mt-4 space-y-2.5">
        {order.items.map((line, i) => (
          <li key={`${i}-${line.menu_item_id}`}>
            <p className="text-[1.375rem] leading-7 font-bold">
              <span className="tabular-nums">{line.qty}×</span> {line.item_name}
            </p>
            {line.notes && (
              <p className="mt-0.5 rounded-md bg-board/70 px-2 py-0.5 text-lg font-bold text-kit-new">
                Note: {line.notes}
              </p>
            )}
          </li>
        ))}
      </ul>

      {order.notes && (
        <p className="mt-3 rounded-md bg-board/70 px-2 py-1 text-lg font-bold text-kit-new">Order note: {order.notes}</p>
      )}

      {action && (
        <div className="mt-5 space-y-2">
          <button
            type="button"
            onClick={() => onAdvance(action.next)}
            disabled={busy}
            className={`min-h-16 w-full rounded-xl text-2xl font-bold transition-opacity disabled:opacity-50 ${action.cls}`}
          >
            {action.label}
          </button>

          {cancellable && !confirming && (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={busy}
              className="min-h-12 w-full rounded-xl border-2 border-chalk-dim/40 text-lg font-bold text-chalk-dim"
            >
              Cancel order
            </button>
          )}
          {cancellable && confirming && (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="min-h-12 rounded-xl border-2 border-chalk-dim/40 text-lg font-bold"
              >
                Keep order
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirming(false)
                  onCancel()
                }}
                disabled={busy}
                className="min-h-12 rounded-xl bg-kit-late text-lg font-bold text-board"
              >
                Yes, cancel
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  )
}
