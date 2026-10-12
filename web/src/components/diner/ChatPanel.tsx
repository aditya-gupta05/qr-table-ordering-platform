import { useEffect, useRef, useState } from 'react'
import { createWaiterRequest } from '../../api/endpoints'
import type { AddToCartAction, ChatAction, Message, WaiterReason } from '../../api/types'
import { MAX_NOTE, MAX_QTY } from '../../config'
import type { Cart } from '../../hooks/useCart'
import { CHAT_MAX_CHARS, draftMessage, useChat } from '../../hooks/useChat'
import { appliedIds, markApplied } from '../../lib/appliedActions'
import { ChatMessage } from './ChatMessage'

interface Props {
  token: string
  cart: Cart
}

const EXAMPLES = ['What vegetarian starters do you have?', 'Is the pad thai spicy?', 'Which dishes are gluten free?']

type StaffState = 'idle' | 'sending' | 'sent' | 'error'

/** Adds an accepted suggestion to the diner's cart. The server still re-prices at order time. */
function addSuggestionToCart(cart: Cart, a: AddToCartAction) {
  const line = cart.lines.find((l) => l.menuItemId === a.menu_item_id)
  if (!line) cart.addItem({ id: a.menu_item_id, name: a.name, priceCents: a.price_cents })
  cart.setQty(a.menu_item_id, Math.min(MAX_QTY, (line?.qty ?? 0) + a.qty))
  if (a.note) {
    const merged = line?.notes && line.notes !== a.note ? `${line.notes}; ${a.note}` : a.note
    cart.setLineNotes(a.menu_item_id, merged.slice(0, MAX_NOTE))
  }
}

/**
 * The menu chatbot. The entry card replaces the old placeholder; the chat itself opens as a
 * bottom sheet. Lives for the whole page so an in-flight answer still lands if the sheet is closed.
 */
export function ChatPanel({ token, cart }: Props) {
  const chat = useChat(token)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [staff, setStaff] = useState<StaffState>('idle')
  // Applied add_to_cart action ids for this table (localStorage). Read by cards to show "Added" after a reload.
  const [applied, setApplied] = useState<ReadonlySet<string>>(() => new Set(appliedIds(token)))
  // Marks the id BEFORE changing the cart, and is a no-op for ids already applied.
  const applyToCart = (a: AddToCartAction) => {
    if (new Set(appliedIds(token)).has(a.action_id)) return
    markApplied(token, a.action_id)
    setApplied(new Set(appliedIds(token)))
    addSuggestionToCart(cart, a)
  }
  const [filterMiss, setFilterMiss] = useState<string | null>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    headingRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: 'end' })
  }, [open, chat.messages.length, chat.pending, chat.notice])

  const trimmed = draft.trim()
  const over = draft.length > CHAT_MAX_CHARS
  const sessionFull = chat.notice?.limitReason === 'session_limit' || chat.turnsRemaining === 0
  const canType = chat.session === 'ready' && !sessionFull
  const canSend = canType && !chat.busy && trimmed.length > 0 && !over

  const submit = () => {
    if (!canSend) return
    setFilterMiss(null)
    chat.send(draft)
    setDraft('')
  }

  const callStaff = async (reason: WaiterReason) => {
    setStaff('sending')
    try {
      await createWaiterRequest({ table_token: token, reason })
      setStaff('sent')
    } catch {
      setStaff('error')
    }
  }

  // Item chips jump to the menu row. A dish hidden by the allergen or diet filters stays in the
  // sheet with a hint, so the diner is not left looking at a closed sheet and nothing.
  const citeItem = (menuItemId: number) => {
    const el = document.getElementById(`item-${menuItemId}`)
    if (!el) {
      setFilterMiss('That dish is hidden by your menu filters. Clear the filters to see it.')
      return
    }
    setFilterMiss(null)
    setOpen(false)
    requestAnimationFrame(() => el.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }

  const staffLabel =
    staff === 'sending' ? 'Calling…' : staff === 'sent' ? 'Staff notified' : staff === 'error' ? 'Try again' : 'Call a waiter'

  const pending = chat.pending

  return (
    <>
      <section aria-labelledby="ask-title" className="mt-5 rounded-2xl border-2 border-ink p-4">
        <h2 id="ask-title" className="font-display text-[1.1875rem] leading-6 font-semibold">
          Ask about the menu
        </h2>
        <p className="mt-1.5 text-[0.9375rem] leading-6 text-ink-soft">
          Ask what a dish contains, what suits a diet, or how a policy works. For allergies, check with your server.
        </p>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-3 min-h-11 rounded-full bg-ink px-5 font-bold text-paper"
        >
          Ask a question
        </button>
      </section>

      {open && (
        <div className="fixed inset-0 z-40">
          <button
            type="button"
            aria-label="Close menu questions"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-ink/40"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="chat-title"
            className="absolute inset-x-0 bottom-0 flex h-[92dvh] flex-col rounded-t-3xl bg-paper shadow-2xl"
          >
            <div className="mx-auto mt-2 h-1.5 w-12 rounded-full bg-rule" aria-hidden="true" />
            <div className="mx-auto flex min-h-0 w-full max-w-xl flex-1 flex-col">
              <header className="border-b border-rule px-4 pt-2 pb-3">
                <div className="flex items-center justify-between gap-3">
                  <h2
                    id="chat-title"
                    ref={headingRef}
                    tabIndex={-1}
                    className="font-display text-2xl leading-8 font-semibold outline-none"
                  >
                    Menu questions
                  </h2>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="min-h-11 rounded-full px-4 font-bold text-ink-soft"
                  >
                    Close
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                  <p className="text-sm text-ink-soft">
                    {chat.tableLabel ? `Table ${chat.tableLabel}.` : 'Your table.'}
                    {chat.turnsRemaining !== null ? ` ${chat.turnsRemaining} questions left.` : ''}
                  </p>
                  <button
                    type="button"
                    onClick={() => void callStaff('diner_asked_for_staff')}
                    disabled={staff === 'sending' || staff === 'sent'}
                    className="min-h-11 rounded-full bg-chili px-5 font-bold text-paper disabled:bg-ink-soft"
                  >
                    {staffLabel}
                  </button>
                </div>
              </header>

              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                <div role="log" aria-live="polite" aria-relevant="additions" className="space-y-5">
                  {chat.session === 'loading' && <p className="text-ink-soft">Connecting to the chat…</p>}

                  {chat.session === 'unavailable' && (
                    <p className="text-ink">
                      {chat.unavailableReason ?? 'The chat is unavailable right now.'} Call a waiter for help.
                    </p>
                  )}

                  {chat.session === 'ready' && chat.messages.length === 0 && !pending && (
                    <div>
                      <p className="text-[0.9375rem] leading-6 text-ink-soft">
                        Ask about a dish, an ingredient, a diet or a policy. Allergy answers always tell you to confirm
                        with a waiter.
                      </p>
                      <ul className="mt-3 flex flex-wrap gap-2">
                        {EXAMPLES.map((ex) => (
                          <li key={ex}>
                            <button
                              type="button"
                              onClick={() => setDraft(ex)}
                              className="min-h-10 rounded-full border-2 border-rule px-3.5 text-left text-sm font-bold"
                            >
                              {ex}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {chat.messages.map((m, i) => (
                    <ChatMessage
                      key={`${m.id}-${i}`}
                      message={m}
                      onCite={citeItem}
                      onAddToCart={applyToCart} applied={applied}
                      onDecide={(action: ChatAction, decision) => chat.resolveAction(m.id, action, decision)}
                      onFeedback={(helpful) => void chat.giveFeedback(m, helpful)}
                      onCallStaff={() => void callStaff('diner_asked_for_staff')}
                    />
                  ))}

                  {pending && (
                    <div className="space-y-2">
                      <ChatMessage
                        message={draftMessage(pending.text)}
                        onCite={citeItem}
                        onAddToCart={() => undefined} applied={applied}
                        onDecide={() => Promise.resolve()}
                        onFeedback={() => undefined}
                        onCallStaff={() => void callStaff('diner_asked_for_staff')}
                      />
                      {pending.phase === 'sending' && !pending.answer && (
                        <p className="flex items-center gap-2 text-ink-soft">
                          <span aria-hidden="true" className="size-2.5 animate-pulse rounded-full bg-sage" />
                          {pending.statusLabel}
                        </p>
                      )}
                      {pending.phase === 'failed' && !pending.answer && (
                        <p className="text-right text-sm font-bold text-chili">Not answered yet.</p>
                      )}
                    </div>
                  )}

                  {pending?.answer && (
                    <ChatMessage
                      message={pending.answer}
                      onCite={citeItem}
                      onAddToCart={applyToCart} applied={applied}
                      onDecide={(action: ChatAction, decision) => chat.resolveAction(pending.answer!.id, action, decision)}
                      onFeedback={(helpful) => void chat.giveFeedback(pending.answer as Message, helpful)}
                      onCallStaff={() => void callStaff('diner_asked_for_staff')}
                    />
                  )}

                  {chat.resendable && !pending && !chat.notice && (
                    <p className="text-sm text-ink-soft">
                      Your last question has no answer yet.{' '}
                      <button type="button" onClick={chat.retryTurn} className="font-bold text-ink underline">
                        Try again
                      </button>
                    </p>
                  )}
                  <div ref={endRef} />
                </div>
              </div>

              <div className="border-t border-rule px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                {staff === 'sent' && (
                  <p role="status" className="mb-2 text-sm font-bold">
                    Staff notified. A waiter will come to your table.
                  </p>
                )}
                {staff === 'error' && (
                  <p role="alert" className="mb-2 text-sm font-bold text-chili">
                    Could not reach staff. Try again.
                  </p>
                )}
                {filterMiss && (
                  <p role="status" className="mb-2 text-sm text-ink-soft">
                    {filterMiss}
                  </p>
                )}
                {chat.notice && (
                  <div role="alert" className="mb-2 flex flex-wrap items-center gap-3 rounded-xl border-2 border-chili bg-chili-soft p-3 text-sm">
                    <p className="font-bold text-chili">{chat.notice.message}</p>
                    {chat.resendable && (
                      <button
                        type="button"
                        onClick={chat.retryTurn}
                        disabled={chat.busy}
                        className="min-h-10 rounded-full border-2 border-ink px-4 font-bold"
                      >
                        Try again
                      </button>
                    )}
                  </div>
                )}

                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    submit()
                  }}
                >
                  <label htmlFor="chat-input" className="block text-sm font-bold text-ink-soft">
                    Your question
                  </label>
                  <textarea
                    id="chat-input"
                    rows={2}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    disabled={!canType}
                    aria-describedby="chat-count"
                    placeholder="For example, is the pad thai spicy?"
                    className="mt-1 w-full resize-none rounded-xl border-2 border-rule bg-paper px-3 py-2.5 text-base focus:border-ink focus:outline-none disabled:opacity-60"
                  />
                  <div className="mt-2 flex items-center justify-between gap-3">
                    <p id="chat-count" className={`text-sm tabular-nums ${over ? 'font-bold text-chili' : 'text-ink-soft'}`}>
                      {draft.length}/{CHAT_MAX_CHARS}
                      {over ? ' Shorten to send.' : ''}
                    </p>
                    <button
                      type="submit"
                      disabled={!canSend}
                      className="min-h-12 min-w-28 rounded-full bg-ink px-6 font-bold text-paper disabled:bg-ink-soft"
                    >
                      {chat.busy ? 'Checking…' : 'Send'}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
