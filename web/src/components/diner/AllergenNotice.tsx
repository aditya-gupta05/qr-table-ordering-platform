import type { AllergenNoticeData } from '../../api/types'
import { ALLERGEN_LABEL, DIETARY_LABEL } from '../../lib/labels'

interface Props {
  notice: AllergenNoticeData
}

// Bordered in the allergen red, never green, and it never says "safe". Items can be empty:
// then only the disclaimer shows.
export function AllergenNotice({ notice }: Props) {
  return (
    <section aria-label="Allergen information" className="rounded-2xl border-2 border-chili bg-paper p-3.5">
      <p className="font-bold text-chili">Allergen information</p>

      {notice.items.length > 0 && (
        <ul className="mt-2.5 space-y-3">
          {notice.items.map((item) => (
            <li key={item.menu_item_id}>
              <p className="font-display text-[1.0625rem] leading-6 font-semibold">{item.name}</p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-bold text-ink-soft">Contains:</span>
                {item.allergens.length === 0 ? (
                  <span className="text-sm text-ink-soft">none listed in our menu data</span>
                ) : (
                  item.allergens.map((a) => (
                    <span
                      key={a}
                      className="rounded-md border-2 border-chili bg-chili-soft px-2 py-px text-sm font-bold text-chili"
                    >
                      {ALLERGEN_LABEL[a]}
                    </span>
                  ))
                )}
              </div>
              {item.dietary_tags.length > 0 && (
                <p className="mt-1 text-sm text-ink-soft">
                  Dietary: {item.dietary_tags.map((t) => DIETARY_LABEL[t]).join(', ')}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-sm leading-6 text-ink-soft">{notice.disclaimer}</p>
    </section>
  )
}
