import type { ButtonHTMLAttributes } from 'react'

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected: boolean
}

/** Toggle chip for filters. Uses aria-pressed so screen readers announce state. */
export function Chip({ selected, className = '', children, ...rest }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={`inline-flex min-h-11 shrink-0 items-center rounded-full border-2 px-4 text-[0.9375rem] font-bold transition-colors ${
        selected
          ? 'border-ink bg-ink text-paper'
          : 'border-rule bg-paper text-ink hover:border-ink-soft'
      } ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
