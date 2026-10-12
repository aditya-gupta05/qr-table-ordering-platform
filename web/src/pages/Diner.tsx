import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError, describeError, toApiError } from '../api/client'
import { createOrder, getMenu, getTable } from '../api/endpoints'
import type { Allergen, DietaryTag, MenuCategory, MenuItem, OrderIn, TableOut } from '../api/types'
import { AskPlaceholder } from '../components/diner/AskPlaceholder'
import { CartBar } from '../components/diner/CartBar'
import { CartSheet } from '../components/diner/CartSheet'
import { CategoryNav } from '../components/diner/CategoryNav'
import { FilterPanel } from '../components/diner/FilterPanel'
import { MenuItemRow } from '../components/diner/MenuItemRow'
import { RESTAURANT_NAME } from '../config'
import { useCart } from '../hooks/useCart'
import { keyFor, type KeyState } from '../lib/idempotency'

export default function Diner() {
  const { token = '' } = useParams()
  const navigate = useNavigate()
  const cart = useCart(token)

  const [table, setTable] = useState<TableOut | null>(null)
  const [tableError, setTableError] = useState<ApiError | null>(null)
  const [tableAttempt, setTableAttempt] = useState(0)

  const [categories, setCategories] = useState<MenuCategory[] | null>(null)
  const [menuError, setMenuError] = useState<string | null>(null)
  const [avoid, setAvoid] = useState<Allergen[]>([])
  const [dietary, setDietary] = useState<DietaryTag[]>([])
  const [menuAttempt, setMenuAttempt] = useState(0)
  const [activeCat, setActiveCat] = useState<number | null>(null)

  const [cartOpen, setCartOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [orderError, setOrderError] = useState<ApiError | null>(null)
  // Guards double taps synchronously; state alone would allow a second click before re-render.
  const inFlight = useRef(false)
  const keyRef = useRef<KeyState | null>(null)

  // Remember the table so the order page can link back to this menu.
  useEffect(() => {
    try {
      window.localStorage.setItem('lastTable', token)
    } catch {
      /* storage blocked: the order page falls back to a plain message */
    }
  }, [token])

  useEffect(() => {
    const ac = new AbortController()
    getTable(token, ac.signal)
      .then((t) => {
        setTable(t)
        setTableError(null)
      })
      .catch((e: unknown) => {
        if (ac.signal.aborted) return
        setTableError(toApiError(e))
      })
    return () => ac.abort()
  }, [token, tableAttempt])

  // Filters are server-side: every change re-queries /api/menu. Previous data stays on screen meanwhile.
  useEffect(() => {
    const ac = new AbortController()
    getMenu({ excludeAllergens: avoid, dietary }, ac.signal)
      .then((data) => {
        setCategories(data)
        setMenuError(null)
      })
      .catch((e: unknown) => {
        if (ac.signal.aborted) return
        setMenuError(describeError(e, 'The menu could not load. Check your connection and try again.'))
      })
    return () => ac.abort()
  }, [avoid, dietary, menuAttempt])

  useEffect(() => {
    if (!categories) return
    const onScroll = () => {
      let current = categories[0]?.id ?? null
      for (const c of categories) {
        const el = document.getElementById(`cat-${c.id}`)
        if (el && el.getBoundingClientRect().top <= 140) current = c.id
      }
      setActiveCat(current)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [categories])

  const qtyFor = (id: number) => cart.lines.find((l) => l.menuItemId === id)?.qty ?? 0

  const onItemQty = (item: MenuItem, next: number) => {
    if (qtyFor(item.id) === 0 && next > 0) {
      cart.addItem({ id: item.id, name: item.name, priceCents: item.price_cents })
    } else {
      cart.setQty(item.id, next)
    }
  }

  const jumpTo = (id: number) => {
    setActiveCat(id)
    document.getElementById(`cat-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const placeOrder = async () => {
    if (inFlight.current || cart.lines.length === 0) return
    const body: OrderIn = {
      table_token: token,
      items: cart.lines.map((l) => ({
        menu_item_id: l.menuItemId,
        qty: l.qty,
        notes: l.notes.trim() || null,
      })),
      notes: cart.orderNotes.trim() || null,
    }
    // Same body = same key, so a retry or double tap replays the original order.
    const k = keyFor(keyRef.current, JSON.stringify(body))
    keyRef.current = k
    inFlight.current = true
    setSubmitting(true)
    setOrderError(null)
    try {
      const order = await createOrder(body, k.key)
      keyRef.current = null
      cart.clear()
      setCartOpen(false)
      navigate(`/order/${order.public_id}`)
    } catch (e) {
      const err = toApiError(e)
      setOrderError(err)
      // A rejected item may have gone unavailable: refresh the menu so it disappears.
      if (err.kind === 'validation' || err.kind === 'conflict') setMenuAttempt((n) => n + 1)
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  if (tableError?.kind === 'not_found') {
    return (
      <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center px-4 py-10">
        <h1 className="font-display text-[2rem] leading-9 font-semibold">This table code is not recognised</h1>
        <p className="mt-3 text-[1.0625rem] leading-7 text-ink-soft">
          The QR code may be out of date. Ask your server for help, or scan the code on your table again.
        </p>
      </main>
    )
  }

  return (
    <div className="min-h-dvh">
      <main className={`mx-auto max-w-xl px-4 pt-6 ${cart.count > 0 ? 'pb-40' : 'pb-16'}`}>
        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="font-display text-[2rem] leading-9 font-semibold text-balance">{RESTAURANT_NAME}</h1>
            <p className="mt-1 text-[0.9375rem] text-ink-soft">Order here and the kitchen gets it straight away.</p>
          </div>
          <div className="shrink-0 rounded-xl border-2 border-ink px-3 py-2 text-center">
            <p className="text-sm text-ink-soft">Table</p>
            <p className="font-display text-2xl leading-7 font-bold tabular-nums" aria-live="polite">
              {table?.label ?? (tableError ? '?' : '…')}
            </p>
          </div>
        </header>

        {tableError && (
          <p role="alert" className="mt-4 flex flex-wrap items-center gap-3 text-ink">
            {tableError.messages[0]}
            <button type="button" onClick={() => setTableAttempt((n) => n + 1)} className="min-h-11 rounded-full border-2 border-ink px-4 font-bold">
              Try again
            </button>
          </p>
        )}

        <AskPlaceholder />
        <div className="mt-5">
          <FilterPanel avoid={avoid} dietary={dietary} onAvoid={setAvoid} onDietary={setDietary} />
        </div>

        {categories && categories.length > 0 && (
          <CategoryNav categories={categories} activeId={activeCat} onSelect={jumpTo} />
        )}

        {menuError && (
          <div role="alert" className="mt-6 flex flex-wrap items-center gap-3">
            <p>{menuError}</p>
            <button type="button" onClick={() => setMenuAttempt((n) => n + 1)} className="min-h-11 rounded-full border-2 border-ink px-4 font-bold">
              Try again
            </button>
          </div>
        )}

        {categories === null && !menuError && <p className="mt-8 text-ink-soft">Loading the menu…</p>}

        {categories && categories.length === 0 && (
          <p className="mt-8 text-ink-soft">
            No dishes match these filters. Clear a filter to see more of the menu.
          </p>
        )}

        {categories?.map((c) => (
          <section key={c.id} id={`cat-${c.id}`} aria-labelledby={`cat-title-${c.id}`} className="scroll-mt-16 pt-8">
            <h2 id={`cat-title-${c.id}`} className="border-b-2 border-ink pb-2 font-display text-[1.5rem] leading-8 font-semibold">
              {c.name}
            </h2>
            <div>
              {c.items.map((item) => (
                <MenuItemRow key={item.id} item={item} qty={qtyFor(item.id)} onQty={(n) => onItemQty(item, n)} />
              ))}
            </div>
          </section>
        ))}
      </main>

      {cart.count > 0 && !cartOpen && (
        <CartBar count={cart.count} totalCents={cart.totalCents} onOpen={() => setCartOpen(true)} />
      )}

      <CartSheet
        open={cartOpen}
        onClose={() => setCartOpen(false)}
        lines={cart.lines}
        orderNotes={cart.orderNotes}
        totalCents={cart.totalCents}
        submitting={submitting}
        error={orderError}
        onQty={cart.setQty}
        onLineNotes={cart.setLineNotes}
        onOrderNotes={cart.setOrderNotes}
        onPlace={placeOrder}
      />
    </div>
  )
}
