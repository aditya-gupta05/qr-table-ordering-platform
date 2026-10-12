import { useState } from 'react'
import type { Allergen, DietaryTag } from '../../api/types'
import { ALLERGENS, DIETARY_TAGS } from '../../api/types'
import { ALLERGEN_LABEL, DIETARY_LABEL } from '../../lib/labels'
import { Chip } from '../Chip'

interface Props {
  avoid: Allergen[]
  dietary: DietaryTag[]
  onAvoid: (next: Allergen[]) => void
  onDietary: (next: DietaryTag[]) => void
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

export function FilterPanel({ avoid, dietary, onAvoid, onDietary }: Props) {
  const [open, setOpen] = useState(false)
  const active = avoid.length + dietary.length

  const summary =
    active === 0
      ? 'Showing every dish'
      : [
          avoid.length ? `Hiding ${avoid.length} allergen${avoid.length > 1 ? 's' : ''}` : null,
          dietary.length ? `${dietary.length} dietary filter${dietary.length > 1 ? 's' : ''}` : null,
        ]
          .filter(Boolean)
          .join(' · ')

  return (
    <section aria-label="Menu filters" className="border-b border-rule">
      <div className="flex items-center justify-between gap-3 py-3">
        <p className="text-[0.9375rem] text-ink-soft" aria-live="polite">
          {summary}
        </p>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="filter-body"
          onClick={() => setOpen((o) => !o)}
          className="min-h-11 rounded-full border-2 border-ink px-4 text-[0.9375rem] font-bold"
        >
          {open ? 'Done' : 'Filter'}
        </button>
      </div>

      {open && (
        <div id="filter-body" className="space-y-5 pb-5">
          <fieldset>
            <legend className="mb-2 text-[0.9375rem] font-bold">Avoid these allergens</legend>
            <div className="flex flex-wrap gap-2">
              {ALLERGENS.map((a) => (
                <Chip
                  key={a}
                  selected={avoid.includes(a)}
                  onClick={() => onAvoid(toggle(avoid, a))}
                  className="px-3 text-sm"
                >
                  {ALLERGEN_LABEL[a]}
                </Chip>
              ))}
            </div>
            <p className="mt-2 text-sm text-ink-soft">Dishes containing a selected allergen are hidden.</p>
          </fieldset>

          <fieldset>
            <legend className="mb-2 text-[0.9375rem] font-bold">Diet</legend>
            <div className="flex flex-wrap gap-2">
              {DIETARY_TAGS.map((t) => (
                <Chip
                  key={t}
                  selected={dietary.includes(t)}
                  onClick={() => onDietary(toggle(dietary, t))}
                  className="px-3 text-sm"
                >
                  {DIETARY_LABEL[t]}
                </Chip>
              ))}
            </div>
            <p className="mt-2 text-sm text-ink-soft">Shows only dishes that match every diet you pick.</p>
          </fieldset>
        </div>
      )}
    </section>
  )
}
