// One Idempotency-Key per checkout attempt. Reused on retry or double tap so the server
// replays the same order instead of creating a duplicate. A new key is minted only when the
// request body changes (the server would answer 409 for a reused key with a different body)
// or after a successful order.

export interface KeyState {
  key: string
  fingerprint: string
}

export function keyFor(prev: KeyState | null, fingerprint: string): KeyState {
  if (prev && prev.fingerprint === fingerprint) return prev
  return { key: crypto.randomUUID(), fingerprint }
}
