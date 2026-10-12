import { useEffect, useRef, useState } from 'react'
import { describeError } from '../../api/client'
import type { AddToCartAction, ChatAction } from '../../api/types'
import { ALLERGEN_LABEL, WAITER_REASON_LABEL, formatMoney } from '../../lib/labels'

interface Props {
  action: ChatAction
  /** Action ids already applied to this table's cart. Survives reloads. */
  applied: ReadonlySet<string>
  /** Idempotent in the parent: a no-op when the action was already applied. */
  onAddToCart: (action: AddToCartAction) => void
  onDecide: (decision: 'accepted' | 'dismissed') => Promise<unknown>
}

const BUTTON = 'min-h-12 rounded-full px-5 text-[0.9375rem] font-bold disabled:opacity-50'

export function ChatActionCard({ action, applied, onAddToCart, onDecide }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const autoPosted = useRef(false)

  const decide = async (decision: 'accepted' | 'dismissed') => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      if (decision === 'accepted' && action.type === 'add_to_cart') onAddToCart(action)
      await onDecide(decision)
    } catch (e) {
      setError(describeError(e, 'That did not save. Try again.'))
    } finally {
      setBusy(false)
    }
  }

  // Restored after a reload: the cart was already updated before the reload, so only the
  // "accepted" decision may be missing. Re-post it silently, once per mount.
  const restoredAdded =
    action.type === 'add_to_cart' && action.status === 'proposed' && applied.has(action.action_id)
  useEffect(() => {
    if (!restoredAdded || autoPosted.current) return
    autoPosted.current = true
    void decide('accepted')
    // decide is recreated each render; this effect is meant to run once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoredAdded])

  const errorLine = error && (
    <p role="alert" className="mt-3 text-sm font-bold text-chili">
      {error}
    </p>
  )

  if (action.type === 'add_to_cart') {
    const conflicts = action.allergen_conflict
    const added = action.status === 'accepted' || restoredAdded
    return (
      <section aria-label={`Suggested: ${action.name}`} className="rounded-2xl border-2 border-ink bg-paper p-4">
        <p className="text-sm font-bold text-ink-soft">Add to your order?</p>
        <p className="mt-1 font-display text-[1.1875rem] leading-6 font-semibold">
          <span className="tabular-nums">{action.qty}×</span> {action.name}
        </p>
        <p className="text-sm tabular-nums text-ink-soft">{formatMoney(action.price_cents)} each</p>
        {action.note && <p className="mt-1.5 text-[0.9375rem]">Note: {action.note}</p>}

        {conflicts.length > 0 && (
          <p className="mt-3 rounded-xl border-2 border-chili bg-chili-soft p-3 text-sm font-bold text-chili">
            Contains {conflicts.map((c) => ALLERGEN_LABEL[c]).join(', ')}, which you said to avoid. Check with a waiter
            before you add it.
          </p>
        )}

        {added && <p className="mt-3 font-bold">Added to your cart.</p>}
        {action.status === 'dismissed' && <p className="mt-3 text-ink-soft">Dismissed.</p>}
        {action.status === 'proposed' && restoredAdded && error && (
          <button type="button" onClick={() => void decide('accepted')} disabled={busy} className={`${BUTTON} mt-3 border-2 border-ink`}>
            Send again
          </button>
        )}
        {action.status === 'proposed' && !restoredAdded && (
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => void decide('accepted')} disabled={busy} className={`${BUTTON} bg-ink text-paper`}>
              Add to cart
            </button>
            <button type="button" onClick={() => void decide('dismissed')} disabled={busy} className={`${BUTTON} border-2 border-ink`}>
              Dismiss
            </button>
          </div>
        )}
        {errorLine}
      </section>
    )
  }

  return (
    <section aria-label="Suggested: call a waiter" className="rounded-2xl border-2 border-ink bg-paper p-4">
      <p className="text-sm font-bold text-ink-soft">Call a waiter?</p>
      <p className="mt-1 font-display text-[1.1875rem] leading-6 font-semibold">{WAITER_REASON_LABEL[action.reason]}</p>
      {action.note && <p className="mt-1.5 text-[0.9375rem]">{action.note}</p>}

      {action.status === 'accepted' && <p className="mt-3 font-bold">Staff notified.</p>}
      {action.status === 'dismissed' && <p className="mt-3 text-ink-soft">Not sent.</p>}
      {action.status === 'proposed' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => void decide('accepted')} disabled={busy} className={`${BUTTON} bg-chili text-paper`}>
            Call a waiter
          </button>
          <button type="button" onClick={() => void decide('dismissed')} disabled={busy} className={`${BUTTON} border-2 border-ink`}>
            Not now
          </button>
        </div>
      )}
      {errorLine}
    </section>
  )
}
