import type { OrderStatus } from '../../api/types'

const STEPS: { status: Exclude<OrderStatus, 'cancelled'>; title: string; detail: string }[] = [
  { status: 'received', title: 'Received', detail: 'The kitchen has your order.' },
  { status: 'preparing', title: 'Preparing', detail: 'Your food is being made.' },
  { status: 'ready', title: 'Ready', detail: 'It is ready and heading to your table.' },
  { status: 'served', title: 'Served', detail: 'Enjoy your meal.' },
]

interface Props {
  status: OrderStatus
}

/** Vertical timeline. Steps before the current one are filled; the current one is marked with aria-current. */
export function StatusTimeline({ status }: Props) {
  const currentIndex = STEPS.findIndex((s) => s.status === status)
  return (
    <ol className="relative space-y-6" aria-label="Order progress">
      {STEPS.map((step, i) => {
        const reached = currentIndex >= i
        const current = currentIndex === i
        const isLast = i === STEPS.length - 1
        return (
          <li key={step.status} aria-current={current ? 'step' : undefined} className="relative flex gap-4">
            {!isLast && (
              <span
                aria-hidden="true"
                className={`absolute top-7 left-[0.8125rem] h-[calc(100%-0.5rem)] w-0.5 ${
                  currentIndex > i ? 'bg-ink' : 'bg-rule'
                }`}
              />
            )}
            <span
              aria-hidden="true"
              className={`relative mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border-2 ${
                reached ? 'border-ink bg-ink' : 'border-rule bg-paper'
              } ${current ? 'ring-4 ring-turmeric/60' : ''}`}
            >
              {reached && <span className="size-2 rounded-full bg-paper" />}
            </span>
            <div className="min-w-0 pb-1">
              <p className={`text-[1.0625rem] font-bold ${reached ? 'text-ink' : 'text-ink-soft'}`}>{step.title}</p>
              <p className={`text-[0.9375rem] leading-6 ${reached ? 'text-ink-soft' : 'text-ink-soft/70'}`}>{step.detail}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
