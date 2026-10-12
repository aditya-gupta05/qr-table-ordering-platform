import type { Order, OrderStatus } from '../../api/types'
import { ageMinutes } from '../../lib/labels'
import { TicketCard } from './TicketCard'

interface Props {
  title: string
  status: OrderStatus
  accent: string
  orders: Order[]
  now: number
  fresh: Set<number>
  pending: Set<number>
  onAdvance: (order: Order, next: OrderStatus) => void
}

export function Lane({ title, status, accent, orders, now, fresh, pending, onAdvance }: Props) {
  return (
    <section aria-label={`${title} orders`} className="flex min-h-0 flex-col rounded-3xl bg-lane">
      <header className="flex items-center justify-between gap-3 px-5 pt-4 pb-3">
        <h2 className="font-display text-3xl font-semibold">{title}</h2>
        <p className="font-display text-3xl font-semibold tabular-nums text-chalk-dim">{orders.length}</p>
      </header>
      <div className={`mx-5 h-1.5 rounded-full ${accent}`} aria-hidden="true" />
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {orders.length === 0 && (
          <p className="px-1 pt-6 text-xl text-chalk-dim">Nothing {status === 'ready' ? 'ready' : 'waiting'} right now.</p>
        )}
        {orders.map((o) => (
          <TicketCard
            key={o.id}
            order={o}
            ageMin={ageMinutes(o.created_at, now)}
            fresh={fresh.has(o.id)}
            busy={pending.has(o.id)}
            onAdvance={(next) => onAdvance(o, next)}
            onCancel={() => onAdvance(o, 'cancelled')}
          />
        ))}
      </div>
    </section>
  )
}
