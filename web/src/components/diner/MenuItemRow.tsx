import type { MenuItem } from '../../api/types'
import { MAX_QTY } from '../../config'
import { ALLERGEN_LABEL, DIETARY_LABEL, formatMoney } from '../../lib/labels'

interface Props {
  item: MenuItem
  qty: number
  onQty: (qty: number) => void
}

const SPICE_COPY = ['Not spicy', 'Mild', 'Medium', 'Hot'] as const

function SpiceMeter({ level }: { level: number }) {
  const clamped = Math.max(0, Math.min(3, level))
  return (
    <span
      role="img"
      aria-label={`${SPICE_COPY[clamped]}`}
      className="inline-flex items-center gap-1.5 text-sm text-ink-soft"
    >
      <span aria-hidden="true" className="flex gap-0.5">
        {[1, 2, 3].map((n) => (
          <span
            key={n}
            className={`size-2.5 rounded-full ${n <= clamped ? 'bg-chili' : 'bg-rule'}`}
          />
        ))}
      </span>
      {SPICE_COPY[clamped]}
    </span>
  )
}

export function MenuItemRow({ item, qty, onQty }: Props) {
  return (
    <article id={`item-${item.id}`} className="border-t border-rule py-5 first:border-t-0">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="font-display text-[1.1875rem] leading-6 font-semibold text-balance">{item.name}</h3>
          {item.description && (
            <p className="mt-1 text-[0.9375rem] leading-6 text-ink-soft">{item.description}</p>
          )}
        </div>
        <p className="shrink-0 pt-0.5 text-[1.0625rem] font-bold tabular-nums">{formatMoney(item.price_cents)}</p>
      </div>

      {item.dietary_tags.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Dietary">
          {item.dietary_tags.map((tag) => (
            <li
              key={tag}
              className="rounded-full bg-sage-soft px-2.5 py-0.5 text-sm font-bold text-sage"
            >
              {DIETARY_LABEL[tag]}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3">
        {item.allergens.length > 0 ? (
          <ul className="flex flex-wrap items-center gap-1.5" aria-label="Contains allergens">
            <li className="pr-1 text-sm font-bold text-ink-soft">Contains</li>
            {item.allergens.map((a) => (
              <li
                key={a}
                className="rounded-md border-2 border-chili bg-chili-soft px-2 py-px text-sm font-bold text-chili"
              >
                {ALLERGEN_LABEL[a]}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm font-bold text-ink-soft">No allergens listed</p>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <SpiceMeter level={item.spice_level} />
        {qty === 0 ? (
          <button
            type="button"
            onClick={() => onQty(1)}
            aria-label={`Add ${item.name}`}
            className="min-h-11 min-w-24 rounded-full bg-ink px-5 text-[0.9375rem] font-bold text-paper active:scale-[0.98]"
          >
            Add
          </button>
        ) : (
          <div role="group" aria-label={`Quantity of ${item.name}`} className="flex items-center rounded-full bg-paper-2">
            <button
              type="button"
              onClick={() => onQty(qty - 1)}
              aria-label={`Remove one ${item.name}`}
              className="size-11 rounded-full text-xl font-bold"
            >
              −
            </button>
            <span className="w-8 text-center text-lg font-bold tabular-nums" aria-live="polite">
              {qty}
            </span>
            <button
              type="button"
              onClick={() => onQty(qty + 1)}
              disabled={qty >= MAX_QTY}
              aria-label={`Add one ${item.name}`}
              className="size-11 rounded-full text-xl font-bold disabled:opacity-40"
            >
              +
            </button>
          </div>
        )}
      </div>
    </article>
  )
}
