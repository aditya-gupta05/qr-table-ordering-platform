import { useEffect, useRef } from 'react'
import type { ApiError } from '../../api/client'
import type { CartLine } from '../../hooks/useCart'
import { MAX_NOTE, MAX_QTY } from '../../config'
import { formatMoney } from '../../lib/labels'

interface Props {
  open: boolean
  onClose: () => void
  lines: CartLine[]
  orderNotes: string
  totalCents: number
  submitting: boolean
  error: ApiError | null
  onQty: (menuItemId: number, qty: number) => void
  onLineNotes: (menuItemId: number, notes: string) => void
  onOrderNotes: (notes: string) => void
  onPlace: () => void
}

export function CartSheet({
  open,
  onClose,
  lines,
  orderNotes,
  totalCents,
  submitting,
  error,
  onQty,
  onLineNotes,
  onOrderNotes,
  onPlace,
}: Props) {
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    if (!open) return
    headingRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, submitting])

  if (!open) return null

  const rejectedIds = new Set([...(error?.unavailableItemIds ?? []), ...(error?.missingItemIds ?? [])])
  // Only lines still in the cart can block checkout, so removing a flagged line unblocks it.
  const blocked = lines.some((l) => rejectedIds.has(l.menuItemId))

  return (
    <div className="fixed inset-0 z-40">
      <button
        type="button"
        aria-label="Close order"
        onClick={onClose}
        disabled={submitting}
        className="absolute inset-0 bg-ink/40"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="cart-title"
        className="absolute inset-x-0 bottom-0 flex max-h-[92dvh] flex-col rounded-t-3xl bg-paper shadow-2xl"
      >
        <div className="mx-auto mt-2 h-1.5 w-12 rounded-full bg-rule" aria-hidden="true" />
        <div className="mx-auto flex min-h-0 w-full max-w-xl flex-1 flex-col">
          <div className="flex items-center justify-between px-4 pt-3 pb-2">
            <h2 id="cart-title" ref={headingRef} tabIndex={-1} className="font-display text-2xl font-semibold outline-none">
              Your order
            </h2>
            <button type="button" onClick={onClose} disabled={submitting} className="min-h-11 rounded-full px-4 font-bold text-ink-soft">
              Keep browsing
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
            {lines.length === 0 ? (
              <p className="py-8 text-center text-ink-soft">Nothing here yet. Add a dish from the menu.</p>
            ) : (
              <ul className="divide-y divide-rule">
                {lines.map((line) => {
                  const flagged = rejectedIds.has(line.menuItemId)
                  return (
                    <li key={line.menuItemId} className={`py-4 ${flagged ? '-mx-3 rounded-xl bg-chili-soft px-3' : ''}`}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-display text-[1.0625rem] font-semibold">{line.name}</p>
                          <p className="text-sm tabular-nums text-ink-soft">{formatMoney(line.priceCents)} each</p>
                          {flagged && (
                            <p className="mt-1 text-sm font-bold text-chili">
                              No longer available. Remove it to place your order.
                            </p>
                          )}
                        </div>
                        <div role="group" aria-label={`Quantity of ${line.name}`} className="flex shrink-0 items-center rounded-full bg-paper-2">
                          <button
                            type="button"
                            onClick={() => onQty(line.menuItemId, line.qty - 1)}
                            disabled={submitting}
                            aria-label={`Remove one ${line.name}`}
                            className="size-11 rounded-full text-xl font-bold"
                          >
                            −
                          </button>
                          <span className="w-7 text-center font-bold tabular-nums">{line.qty}</span>
                          <button
                            type="button"
                            onClick={() => onQty(line.menuItemId, Math.min(MAX_QTY, line.qty + 1))}
                            disabled={submitting || line.qty >= MAX_QTY}
                            aria-label={`Add one ${line.name}`}
                            className="size-11 rounded-full text-xl font-bold disabled:opacity-40"
                          >
                            +
                          </button>
                        </div>
                      </div>
                      <label className="mt-3 block">
                        <span className="text-sm font-bold text-ink-soft">Note for the kitchen</span>
                        <input
                          type="text"
                          value={line.notes}
                          maxLength={MAX_NOTE}
                          disabled={submitting}
                          onChange={(e) => onLineNotes(line.menuItemId, e.target.value)}
                          placeholder="For example, no cucumber"
                          className="mt-1 w-full rounded-xl border-2 border-rule bg-paper px-3 py-2.5 text-base focus:border-ink focus:outline-none"
                        />
                      </label>
                    </li>
                  )
                })}
              </ul>
            )}

            {lines.length > 0 && (
              <label className="mt-4 block">
                <span className="font-bold">Notes for the whole order</span>
                <textarea
                  value={orderNotes}
                  maxLength={MAX_NOTE}
                  rows={2}
                  disabled={submitting}
                  onChange={(e) => onOrderNotes(e.target.value)}
                  placeholder="For example, bring everything at once"
                  className="mt-1 w-full rounded-xl border-2 border-rule bg-paper px-3 py-2.5 text-base focus:border-ink focus:outline-none"
                />
              </label>
            )}
          </div>

          <div className="border-t border-rule px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
            {error && (
              <div role="alert" className="mb-3 rounded-xl border-2 border-chili bg-chili-soft p-3 text-sm">
                <p className="font-bold text-chili">
                  {error.kind === 'network' ? 'Order not sent yet' : 'Order not placed'}
                </p>
                <ul className="mt-1 list-inside list-disc text-ink">
                  {error.messages.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
                {error.kind === 'network' && <p className="mt-1">Tap Place order again. Your order is sent once.</p>}
              </div>
            )}
            <div className="flex items-center justify-between pb-3">
              <p className="text-ink-soft">Estimated total</p>
              <p className="text-xl font-bold tabular-nums">{formatMoney(totalCents)}</p>
            </div>
            <button
              type="button"
              onClick={onPlace}
              disabled={submitting || lines.length === 0 || blocked}
              aria-busy={submitting}
              className="min-h-14 w-full rounded-full bg-chili text-lg font-bold text-paper disabled:bg-ink-soft"
            >
              {submitting ? 'Sending to the kitchen' : 'Place order'}
            </button>
            <p className="mt-2 text-center text-sm text-ink-soft">The kitchen confirms the final total.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
