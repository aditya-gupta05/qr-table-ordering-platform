import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, toApiError } from '../api/client'
import { createChatSession, decideAction, getChatSession, sendTurn, setMessageFeedback } from '../api/chat'
import type { ActionResult, ChatAction, ChatLimitDetail, DoneFrame, ErrorFrame, Message, StatusFrame } from '../api/types'

export const TURN_TIMEOUT_MS = 35_000
export const CHAT_MAX_CHARS = 500
const CHECKING_LABEL = 'Checking the menu…'

export interface SentTurn {
  clientMessageId: string
  text: string
}

export interface PendingTurn extends SentTurn {
  phase: 'sending' | 'failed'
  statusLabel: string
  /** The validated answer, or the error fallback, once the stream delivers it. */
  answer: Message | null
}

export interface TurnNotice {
  message: string
  limitReason: ChatLimitDetail['reason'] | null
}

export type SessionState = 'loading' | 'ready' | 'unavailable'

export interface ChatState {
  session: SessionState
  unavailableReason: string | null
  sessionId: string | null
  tableLabel: string | null
  maxChars: number
  turnsRemaining: number | null
  messages: Message[]
  pending: PendingTurn | null
  /** A turn that can be re-sent with the same client_message_id (failed, errored, or restored). */
  resendable: SentTurn | null
  notice: TurnNotice | null
}

const INITIAL: ChatState = {
  session: 'loading',
  unavailableReason: null,
  sessionId: null,
  tableLabel: null,
  maxChars: CHAT_MAX_CHARS,
  turnsRemaining: null,
  messages: [],
  pending: null,
  resendable: null,
  notice: null,
}

const sessionKey = (token: string) => `chat:${token}`
const resendKey = (token: string) => `chatresend:${token}`

// sessionStorage can be blocked (private mode). The chat then works for this page view only.
function readStore(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStore(key: string, value: string | null) {
  try {
    if (value === null) window.sessionStorage.removeItem(key)
    else window.sessionStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

function readResend(token: string): SentTurn | null {
  const raw = readStore(resendKey(token))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<SentTurn>
    if (typeof parsed.clientMessageId === 'string' && typeof parsed.text === 'string') {
      return { clientMessageId: parsed.clientMessageId, text: parsed.text }
    }
  } catch {
    /* corrupt entry: ignore */
  }
  return null
}

function newClientId(): string {
  const c = globalThis.crypto
  if (!c) return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  if (typeof c.randomUUID === 'function') return c.randomUUID()
  return Array.from(c.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** Local stand-in for the user's message while the turn is in flight (the server stores the real one). */
export function draftMessage(text: string): Message {
  return {
    id: -1,
    role: 'user',
    text,
    status: null,
    citations: [],
    allergen_notice: null,
    actions: [],
    feedback: null,
    created_at: new Date().toISOString(),
  }
}

/** Applies `fn` to a message wherever it lives: the history or the in-flight answer. */
function mapMessage(state: ChatState, id: number, fn: (m: Message) => Message): Pick<ChatState, 'messages' | 'pending'> {
  return {
    messages: state.messages.map((m) => (m.id === id ? fn(m) : m)),
    pending:
      state.pending?.answer && state.pending.answer.id === id
        ? { ...state.pending, answer: fn(state.pending.answer) }
        : state.pending,
  }
}

export function useChat(token: string) {
  const [s, setS] = useState<ChatState>(INITIAL)
  const sessionRef = useRef<string | null>(null)
  const busyRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)

  // Open or restore the session for this table. Non-404 failures leave the chat unavailable.
  useEffect(() => {
    let alive = true
    const ac = new AbortController()

    const open = async () => {
      const stored = readStore(sessionKey(token))
      if (stored) {
        try {
          const d = await getChatSession(stored, ac.signal)
          return { sid: d.session_id, tableLabel: d.table_label, turnsRemaining: d.turns_remaining, messages: d.messages, maxChars: CHAT_MAX_CHARS }
        } catch (e) {
          if (ac.signal.aborted || !(e instanceof ApiError && e.status === 404)) throw e
          writeStore(sessionKey(token), null)
        }
      }
      const c = await createChatSession(token, ac.signal)
      writeStore(sessionKey(token), c.session_id)
      return { sid: c.session_id, tableLabel: c.table_label, turnsRemaining: c.turns_remaining, messages: [] as Message[], maxChars: c.max_message_chars }
    }

    open()
      .then((opened) => {
        if (!alive) return
        sessionRef.current = opened.sid
        // A restored turn can end on a user message with no reply. Offer a re-send only when the
        // stored client_message_id still matches that message (server text is PII-redacted).
        const stored = readResend(token)
        const last = opened.messages[opened.messages.length - 1]
        const resendable = stored && last?.role === 'user' && last.text === stored.text ? stored : null
        if (!resendable) writeStore(resendKey(token), null)
        setS({
          ...INITIAL,
          session: 'ready',
          sessionId: opened.sid,
          tableLabel: opened.tableLabel,
          maxChars: opened.maxChars,
          turnsRemaining: opened.turnsRemaining,
          messages: opened.messages,
          resendable,
        })
      })
      .catch((e: unknown) => {
        if (!alive || ac.signal.aborted) return
        setS({ ...INITIAL, session: 'unavailable', unavailableReason: toApiError(e).messages[0] ?? 'Chat is unavailable right now.' })
      })

    return () => {
      alive = false
      ac.abort()
    }
  }, [token])

  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  const refresh = useCallback(async () => {
    const sid = sessionRef.current
    if (!sid) return
    const d = await getChatSession(sid)
    setS((prev) => ({ ...prev, messages: d.messages, turnsRemaining: d.turns_remaining }))
  }, [])

  const run = useCallback(
    async (sid: string, turn: SentTurn) => {
      if (busyRef.current) return
      busyRef.current = true
      writeStore(resendKey(token), JSON.stringify(turn))
      setS((prev) => ({
        ...prev,
        resendable: null,
        notice: null,
        pending: { ...turn, phase: 'sending', statusLabel: CHECKING_LABEL, answer: null },
      }))

      const ac = new AbortController()
      abortRef.current = ac
      let timedOut = false
      const timer = window.setTimeout(() => {
        timedOut = true
        ac.abort()
      }, TURN_TIMEOUT_MS)
      // Holder object: callbacks write these, and TypeScript does not track writes made inside closures.
      const got: { answer: Message | null; errorCode: ErrorFrame['code'] | null } = { answer: null, errorCode: null }

      const settle = async () => {
        const failed = got.errorCode !== null
        let messages: Message[] | null = null
        let turnsRemaining: number | null = null
        try {
          const d = await getChatSession(sid)
          messages = d.messages
          turnsRemaining = d.turns_remaining
        } catch {
          /* show the local view below; the next open reloads the server copy */
        }
        setS((prev) => ({
          ...prev,
          messages: messages ?? [...prev.messages, draftMessage(turn.text), ...(got.answer ? [got.answer] : [])],
          turnsRemaining: turnsRemaining ?? prev.turnsRemaining,
          pending: null,
          resendable: failed ? turn : null,
        }))
        writeStore(resendKey(token), failed ? JSON.stringify(turn) : null)
      }

      const fail = (e: unknown) => {
        if (!timedOut && ac.signal.aborted) return // page left: keep the stored turn for the next visit
        let notice: TurnNotice = { message: 'Something went wrong. Try again.', limitReason: null }
        let retryable = true
        if (timedOut) {
          notice = { message: 'No answer after 35 seconds. Try again, or call a waiter.', limitReason: null }
        } else if (e instanceof ApiError) {
          if (e.status === 409) {
            notice = { message: 'Still answering your last question. Try again in a moment.', limitReason: null }
          } else if (e.status === 429) {
            const d = (typeof e.detail === 'object' && e.detail !== null ? e.detail : {}) as Partial<ChatLimitDetail>
            const reason = d.reason ?? 'table_rate'
            notice = { message: d.message ?? e.messages[0] ?? 'Too many questions right now.', limitReason: reason }
            retryable = reason !== 'session_limit'
          } else if (e.status === 404) {
            sessionRef.current = null
            writeStore(sessionKey(token), null)
            notice = { message: 'This chat has ended. Refresh the page to start a new one.', limitReason: null }
            retryable = false
          } else if (e.status === 422) {
            notice = { message: 'Type a question of 1 to 500 characters.', limitReason: null }
            retryable = false
          } else {
            notice = { message: e.messages[0] ?? notice.message, limitReason: null }
          }
        }
        if (!retryable) writeStore(resendKey(token), null)
        setS((prev) => ({
          ...prev,
          sessionId: sessionRef.current,
          pending: prev.pending
            ? { ...prev.pending, phase: 'failed' }
            : { ...turn, phase: 'failed', statusLabel: '', answer: null },
          resendable: retryable ? turn : null,
          notice,
        }))
      }

      try {
        await sendTurn(sid, turn.text, turn.clientMessageId, ac.signal, {
          onStatus: (f: StatusFrame) =>
            setS((prev) =>
              prev.pending ? { ...prev, pending: { ...prev.pending, statusLabel: f.label || CHECKING_LABEL } } : prev,
            ),
          onAnswer: (m) => {
            got.answer = m
            setS((prev) => (prev.pending ? { ...prev, pending: { ...prev.pending, answer: m } } : prev))
          },
          onError: (f) => {
            got.answer = f.fallback
            got.errorCode = f.code
            setS((prev) => (prev.pending ? { ...prev, pending: { ...prev.pending, answer: f.fallback } } : prev))
          },
          onDone: (_f: DoneFrame) => {
            /* the server is the source of truth: settle() re-reads the session */
          },
        })
        await settle()
      } catch (e) {
        fail(e)
      } finally {
        window.clearTimeout(timer)
        busyRef.current = false
        if (abortRef.current === ac) abortRef.current = null
      }
    },
    [token],
  )

  /** Sends a new question. Blank, over-length, or concurrent sends are ignored. */
  const send = useCallback(
    (raw: string) => {
      const text = raw.trim()
      const sid = sessionRef.current
      if (!sid || text === '' || text.length > CHAT_MAX_CHARS || busyRef.current) return
      void run(sid, { clientMessageId: newClientId(), text })
    },
    [run],
  )

  /** Re-sends the last turn with the SAME client_message_id (no extra turn is consumed). */
  const retryTurn = useCallback(() => {
    const sid = sessionRef.current
    const turn = s.resendable
    if (!sid || !turn || busyRef.current) return
    void run(sid, turn)
  }, [run, s.resendable])

  const resolveAction = useCallback(
    async (messageId: number, action: ChatAction, decision: 'accepted' | 'dismissed'): Promise<ActionResult> => {
      const sid = sessionRef.current
      if (!sid) throw new ApiError(0, 'other', null, ['The chat is not connected. Refresh the page.'])
      let result: ActionResult
      try {
        result = await decideAction(sid, action.action_id, decision)
      } catch (e) {
        // 409 means the server already settled it: reload the truth.
        if (e instanceof ApiError && e.status === 409) await refresh().catch(() => undefined)
        throw e
      }
      setS((prev) => ({
        ...prev,
        ...mapMessage(prev, messageId, (m) => ({
          ...m,
          actions: m.actions.map((a) => (a.action_id === action.action_id ? result.action : a)),
        })),
      }))
      return result
    },
    [refresh],
  )

  const giveFeedback = useCallback(async (message: Message, helpful: boolean) => {
    const previous = message.feedback
    const set = (value: boolean | null) =>
      setS((prev) => ({
        ...prev,
        ...mapMessage(prev, message.id, (m) => ({ ...m, feedback: value })),
      }))
    set(helpful)
    try {
      await setMessageFeedback(message.id, helpful)
    } catch {
      set(previous)
      setS((prev) => ({ ...prev, notice: { message: 'Your feedback was not saved. Try again.', limitReason: null } }))
    }
  }, [])

  const dismissNotice = useCallback(() => setS((prev) => ({ ...prev, notice: null })), [])

  return {
    ...s,
    busy: s.pending?.phase === 'sending',
    send,
    retryTurn,
    resolveAction,
    giveFeedback,
    dismissNotice,
  }
}

export type Chat = ReturnType<typeof useChat>
