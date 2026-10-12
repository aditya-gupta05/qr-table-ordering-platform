import type { LiveStatus } from '../hooks/useLiveSocket'

interface Props {
  status: LiveStatus
  tone?: 'light' | 'dark'
  liveLabel?: string
}

const COPY: Record<LiveStatus, string> = {
  live: 'Live',
  connecting: 'Connecting',
  reconnecting: 'Reconnecting',
}

/**
 * Shows whether realtime updates are flowing. When it says "Reconnecting" the page still shows the
 * last REST snapshot, so this is a freshness signal, not an error screen.
 */
export function ConnectionBadge({ status, tone = 'light', liveLabel }: Props) {
  const dot =
    status === 'live'
      ? 'bg-kit-ready'
      : status === 'connecting'
        ? 'bg-kit-new animate-pulse'
        : 'bg-kit-late animate-pulse'
  const text = status === 'live' && liveLabel ? liveLabel : COPY[status]
  const base =
    tone === 'dark'
      ? 'border-chalk-dim/40 text-chalk'
      : 'border-rule text-ink-soft'
  return (
    <span
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-bold ${base}`}
    >
      <span aria-hidden="true" className={`size-2.5 rounded-full ${dot}`} />
      {text}
    </span>
  )
}
