import { formatMoney } from '../../lib/labels'

interface Props {
  count: number
  totalCents: number
  onOpen: () => void
}

export function CartBar({ count, totalCents, onOpen }: Props) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-rule bg-paper/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur">
      <div className="mx-auto flex max-w-xl items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[0.9375rem] font-bold">
            {count} {count === 1 ? 'item' : 'items'}
          </p>
          <p className="text-sm text-ink-soft tabular-nums">Estimated {formatMoney(totalCents)}</p>
        </div>
        <button
          type="button"
          onClick={onOpen}
          className="min-h-12 rounded-full bg-chili px-6 text-[1.0625rem] font-bold text-paper"
        >
          View order
        </button>
      </div>
    </div>
  )
}
