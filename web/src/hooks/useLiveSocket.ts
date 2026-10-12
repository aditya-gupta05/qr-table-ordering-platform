import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../api/ws'

export type LiveStatus = 'connecting' | 'live' | 'reconnecting'

export interface LiveHandlers {
  /** Called on every (re)connect. Pages refetch the REST snapshot here. */
  onOpen?: () => void
  /** Raw text frame. Parsing lives in api/ws.ts so the hook stays generic. */
  onMessage?: (raw: string) => void
}

const BASE_DELAY_MS = 500
const MAX_DELAY_MS = 10_000

/** Delay before reconnect attempt `attempt` (0-based): exponential, capped, with jitter in [50%, 100%]. */
export function backoffDelay(attempt: number): number {
  const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt)
  return ceiling / 2 + Math.random() * (ceiling / 2)
}

/**
 * Keeps one WebSocket open to `path` (same origin). Reconnects forever with backoff,
 * resets the backoff once a connection opens, and closes cleanly on unmount or path change.
 */
export function useLiveSocket(path: string | null, handlers: LiveHandlers): LiveStatus {
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const handlersRef = useRef(handlers)

  useEffect(() => {
    handlersRef.current = handlers
  })

  useEffect(() => {
    if (path === null) return
    let socket: WebSocket | null = null
    let timer: number | undefined
    let attempt = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      socket = new WebSocket(wsUrl(path))
      socket.onopen = () => {
        attempt = 0
        setStatus('live')
        handlersRef.current.onOpen?.()
      }
      socket.onmessage = (ev) => {
        if (typeof ev.data === 'string') handlersRef.current.onMessage?.(ev.data)
      }
      socket.onclose = () => {
        socket = null
        if (disposed) return
        setStatus('reconnecting')
        timer = window.setTimeout(connect, backoffDelay(attempt))
        attempt += 1
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(timer)
      socket?.close()
    }
  }, [path])

  return status
}
