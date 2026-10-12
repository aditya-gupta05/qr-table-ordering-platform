// Fetch wrapper with typed errors. Pydantic/FastAPI return three error shapes:
//   detail: string                          (404, 409, 400)
//   detail: { message, invalid?, ... }      (422 business rules, 422 unknown vocab)
//   detail: [{ loc, msg, type }, ...]       (422 request validation)

export type ApiErrorKind = 'not_found' | 'conflict' | 'validation' | 'network' | 'server' | 'other'

export class ApiError extends Error {
  readonly status: number
  readonly kind: ApiErrorKind
  readonly detail: unknown
  readonly messages: string[]
  readonly unavailableItemIds: number[]
  readonly missingItemIds: number[]

  constructor(status: number, kind: ApiErrorKind, detail: unknown, messages: string[]) {
    super(messages[0] ?? `Request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
    this.detail = detail
    this.messages = messages
    const d = (typeof detail === 'object' && detail !== null ? detail : {}) as Record<string, unknown>
    this.unavailableItemIds = Array.isArray(d.unavailable_item_ids)
      ? (d.unavailable_item_ids as number[])
      : []
    this.missingItemIds = Array.isArray(d.missing_item_ids) ? (d.missing_item_ids as number[]) : []
  }
}

function messagesFrom(detail: unknown): string[] {
  if (typeof detail === 'string') return [detail]
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      const err = e as { loc?: unknown[]; msg?: string }
      const field = err.loc?.filter((p) => p !== 'body').join('.') ?? ''
      return field ? `${field}: ${err.msg ?? 'invalid'}` : (err.msg ?? 'Invalid request')
    })
  }
  if (typeof detail === 'object' && detail !== null) {
    const d = detail as { message?: string; invalid?: string[]; allowed?: string[] }
    const parts = [d.message ?? 'Request rejected']
    if (d.invalid?.length) parts.push(`Unknown: ${d.invalid.join(', ')}`)
    return parts
  }
  return []
}

function kindFor(status: number): ApiErrorKind {
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 422 || status === 400) return 'validation'
  if (status >= 500) return 'server'
  return 'other'
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    })
  } catch (err) {
    if (init.signal?.aborted) throw err
    throw new ApiError(0, 'network', null, ['Cannot reach the server. Check your connection and try again.'])
  }
  if (!res.ok) throw await httpError(res)
  // 204 (for example feedback) has no body.
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Builds a typed ApiError from a non-2xx response. Shared with the SSE chat turn. */
export async function httpError(res: Response): Promise<ApiError> {
  let detail: unknown = null
  try {
    detail = (await res.json()).detail
  } catch {
    detail = null
  }
  const messages = messagesFrom(detail)
  return new ApiError(res.status, kindFor(res.status), detail, messages.length ? messages : [`Request failed (${res.status})`])
}

export function describeError(err: unknown, fallback = 'Something went wrong.'): string {
  if (err instanceof ApiError) return err.messages[0] ?? fallback
  return fallback
}

/** Normalise anything thrown by request() into an ApiError. */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  return new ApiError(0, 'other', null, ['Something went wrong. Try again.'])
}
