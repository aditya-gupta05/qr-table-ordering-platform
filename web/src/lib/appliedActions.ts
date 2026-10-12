// Action ids whose add_to_cart has already been applied to this table's cart. Written BEFORE the
// cart changes, so a reload between the cart update and the "accepted" POST cannot add the item twice.
const MAX_IDS = 50

const key = (token: string) => `chatapplied:${token}`

export function appliedIds(token: string): string[] {
  try {
    const raw = window.localStorage.getItem(key(token))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

/** Records an applied action id, keeping only the most recent MAX_IDS. */
export function markApplied(token: string, actionId: string) {
  const ids = appliedIds(token).filter((id) => id !== actionId)
  ids.push(actionId)
  try {
    window.localStorage.setItem(key(token), JSON.stringify(ids.slice(-MAX_IDS)))
  } catch {
    /* storage blocked: the guard then lasts for this page view only */
  }
}
