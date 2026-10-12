import type { Allergen, DietaryTag, OrderStatus, WaiterReason } from '../api/types'

export const WAITER_REASON_LABEL: Record<WaiterReason, string> = {
  allergy_or_dietary: 'Allergy or diet',
  question_not_answered: 'Question not answered',
  diner_asked_for_staff: 'Asked for a waiter',
  other: 'Other',
}

export const ALLERGEN_LABEL: Record<Allergen, string> = {
  gluten: 'Gluten',
  dairy: 'Dairy',
  egg: 'Egg',
  peanut: 'Peanut',
  tree_nut: 'Tree nut',
  soy: 'Soy',
  fish: 'Fish',
  shellfish: 'Shellfish',
  sesame: 'Sesame',
  mustard: 'Mustard',
  celery: 'Celery',
  sulphites: 'Sulphites',
}

export const DIETARY_LABEL: Record<DietaryTag, string> = {
  vegetarian: 'Vegetarian',
  vegan: 'Vegan',
  gluten_free: 'Gluten free',
  halal: 'Halal',
  dairy_free: 'Dairy free',
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  received: 'Received',
  preparing: 'Preparing',
  ready: 'Ready',
  served: 'Served',
  cancelled: 'Cancelled',
}

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD' })

export function formatMoney(cents: number): string {
  return money.format(cents / 100)
}

export function ageMinutes(createdAtIso: string, now: number): number {
  const created = Date.parse(createdAtIso.replace(/(\.\d{3})\d+/, '$1'))
  if (Number.isNaN(created)) return 0
  return Math.max(0, Math.floor((now - created) / 60_000))
}
