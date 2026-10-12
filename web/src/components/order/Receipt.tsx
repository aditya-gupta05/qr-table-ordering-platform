import type { Order } from '../../api/types'
import { formatMoney } from '../../lib/labels'

interface Props {
  order: Order
}

/** Printed-ticket summary: items, per-line notes, dashed tear line, total. */
export function Receipt({ order }: Props) {
  const placed = new Date(order.created_at).toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' })
  return (
    <section aria-label="Order summary" className="relative rounded-2xl bg-white/60 px-5 pt-5 pb-6 ring-1 ring-rule">
      <div className="flex items-baseline justify-between gap-3 border-b border-rule pb-3">
        <p className="font-display text-xl font-semibold">Table {order.table_label}</p>
        <p className="text-sm text-ink-soft">Placed {placed}</p>
      </div>

      <ul className="divide-y divide-rule py-3">
        {order.items.map((line, i) => (
          <li key={`${i}-${line.menu_item_id}`} className="flex justify-between gap-4 py-2.5">
            <div className="min-w-0">
              <p className="font-bold">
                <span className="tabular-nums">{line.qty}×</span> {line.item_name}
              </p>
              {line.notes && <p className="mt-0.5 text-[0.9375rem] text-ink-soft">Note: {line.notes}</p>}
            </div>
            <p className="shrink-0 tabular-nums">{formatMoney(line.unit_price_cents * line.qty)}</p>
          </li>
        ))}
      </ul>

      {order.notes && (
        <p className="border-t border-dashed border-ink-soft/40 pt-3 text-[0.9375rem] text-ink-soft">
          Order note: {order.notes}
        </p>
      )}

      <div className="mt-3 flex items-baseline justify-between border-t-2 border-dashed border-rule pt-4">
        <p className="font-bold">Total</p>
        <p className="font-display text-2xl font-bold tabular-nums">{formatMoney(order.total_cents)}</p>
      </div>
    </section>
  )
}
