import type { WaiterRequest } from '../../api/types'
import { ageMinutes, WAITER_REASON_LABEL } from '../../lib/labels'

interface Props {
  /** Oldest first. Open and acknowledged requests only; closed ones are removed by the parent. */
  items: WaiterRequest[]
  now: number
  fresh: ReadonlySet<number>
  pending: ReadonlySet<number>
  onAcknowledge: (w: WaiterRequest) => void
  onClose: (w: WaiterRequest) => void
}

/** Strip above the order lanes: tables waiting for staff. Allergy and diet requests get the red edge. */
export function WaiterRequests({ items, now, fresh, pending, onAcknowledge, onClose }: Props) {
  if (items.length === 0) return null

  return (
    <section aria-labelledby="waiter-title" className="mx-6 mb-4">
      <div className="mb-2 flex items-baseline gap-3">
        <h2 id="waiter-title" className="font-display text-2xl font-semibold">
          Waiting for staff
        </h2>
        <p className="text-lg font-bold tabular-nums text-chalk-dim">{items.length}</p>
      </div>

      <ul className="flex flex-wrap gap-3">
        {items.map((w) => {
          const allergy = w.reason === 'allergy_or_dietary'
          const acknowledged = w.status === 'acknowledged'
          const busy = pending.has(w.id)
          return (
            <li key={w.id}>
              <article
                aria-label={`Table ${w.table_label}, ${WAITER_REASON_LABEL[w.reason]}, ${acknowledged ? 'acknowledged' : 'not yet acknowledged'}`}
                className={`w-72 rounded-2xl border-l-[10px] bg-tile p-4 ${allergy ? 'border-kit-late' : 'border-kit-new'} ${
                  fresh.has(w.id) ? 'motion-safe:animate-arrive' : ''
                } ${acknowledged ? 'opacity-70' : ''}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="font-display text-5xl leading-none font-bold">{w.table_label}</p>
                  <p className="text-right text-lg font-bold tabular-nums text-chalk-dim">
                    {ageMinutes(w.created_at, now)} min
                  </p>
                </div>

                <p className={`mt-2 text-xl leading-7 font-bold ${allergy ? 'text-kit-late' : 'text-chalk'}`}>
                  {WAITER_REASON_LABEL[w.reason]}
                </p>
                <p className="text-base font-bold text-chalk-dim">
                  {w.source === 'chat' ? 'From the chat' : 'From the call button'}
                  {acknowledged ? ' · acknowledged' : ''}
                </p>
                {w.note && (
                  <p className="mt-2 rounded-md bg-board/70 px-2 py-1 text-lg font-bold text-kit-new">Note: {w.note}</p>
                )}

                <div className="mt-3 flex gap-2">
                  {!acknowledged && (
                    <button
                      type="button"
                      onClick={() => onAcknowledge(w)}
                      disabled={busy}
                      className="min-h-14 flex-1 rounded-xl bg-chalk text-lg font-bold text-board disabled:opacity-50"
                    >
                      Acknowledge
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onClose(w)}
                    disabled={busy}
                    className="min-h-14 flex-1 rounded-xl border-2 border-chalk-dim/40 text-lg font-bold text-chalk-dim disabled:opacity-50"
                  >
                    Close
                  </button>
                </div>
              </article>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
