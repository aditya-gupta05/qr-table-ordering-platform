import { formatMoney } from '../../lib/labels'
import type { ChatAction, AddToCartAction, Message } from '../../api/types'
import { AllergenNotice } from './AllergenNotice'
import { ChatActionCard } from './ChatActionCard'

interface Props {
  message: Message
  applied: ReadonlySet<string>
  onCite: (menuItemId: number) => void
  onAddToCart: (action: AddToCartAction) => void
  onDecide: (action: ChatAction, decision: 'accepted' | 'dismissed') => Promise<unknown>
  onFeedback: (helpful: boolean) => void
  onCallStaff: () => void
}

// Statuses where the model could not give a grounded answer. A waiter is always one tap away.
const NEEDS_STAFF: ReadonlySet<string> = new Set(['error', 'blocked', 'needs_staff'])

/** One chat message. Everything is plain text; React escapes it, so nothing is parsed as HTML. */
export function ChatMessage({ message, applied, onCite, onAddToCart, onDecide, onFeedback, onCallStaff }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 break-words whitespace-pre-line text-paper">
          {message.text}
        </p>
      </div>
    )
  }

  const dishes = message.citations.filter((c) => c.kind === 'menu_item')
  const passages = message.citations.filter((c) => c.kind === 'passage')
  const showStaff = message.status !== null && NEEDS_STAFF.has(message.status)
  const canRate = message.id > 0

  return (
    <div className="space-y-2.5">
      <div className="max-w-[92%] rounded-2xl rounded-bl-md border-2 border-rule bg-paper-2 px-4 py-3">
        <p className="text-[1.0625rem] leading-7 break-words whitespace-pre-line">{message.text}</p>

        {dishes.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-2" aria-label="Dishes mentioned">
            {dishes.map((c) => (
              <li key={`item-${c.menu_item_id}`}>
                <button
                  type="button"
                  onClick={() => onCite(c.menu_item_id)}
                  className="min-h-10 rounded-full border-2 border-ink bg-paper px-3.5 text-sm font-bold"
                >
                  {c.name} <span className="tabular-nums">{formatMoney(c.price_cents)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {passages.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm text-ink-soft">
            {passages.map((c) => (
              <li key={`chunk-${c.chunk_id}`}>
                Source: {c.title}
                {c.section ? ` › ${c.section}` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>

      {message.allergen_notice && (
        <div className="max-w-[92%]">
          <AllergenNotice notice={message.allergen_notice} />
        </div>
      )}

      {message.actions.map((a) => (
        <div key={a.action_id} className="max-w-[92%]">
          <ChatActionCard
            action={a}
            applied={applied}
            onAddToCart={onAddToCart}
            onDecide={(decision) => onDecide(a, decision)}
          />
        </div>
      ))}

      {(showStaff || canRate) && (
        <div className="flex flex-wrap items-center gap-2">
          {showStaff && (
            <button
              type="button"
              onClick={onCallStaff}
              className="min-h-11 rounded-full border-2 border-chili px-4 text-sm font-bold text-chili"
            >
              Call a waiter
            </button>
          )}
          {canRate && (
            <div role="group" aria-label="Was this answer helpful?" className="ml-auto flex items-center gap-1.5 text-sm">
              <span className="text-ink-soft">Helpful?</span>
              <button
                type="button"
                aria-pressed={message.feedback === true}
                onClick={() => onFeedback(true)}
                className={`min-h-10 rounded-full border-2 px-3 font-bold ${
                  message.feedback === true ? 'border-ink bg-ink text-paper' : 'border-rule'
                }`}
              >
                Yes
              </button>
              <button
                type="button"
                aria-pressed={message.feedback === false}
                onClick={() => onFeedback(false)}
                className={`min-h-10 rounded-full border-2 px-3 font-bold ${
                  message.feedback === false ? 'border-ink bg-ink text-paper' : 'border-rule'
                }`}
              >
                No
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
