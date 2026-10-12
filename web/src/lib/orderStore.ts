import { isNewer } from '../api/ws'
import type { Order, OrderStatus } from '../api/types'

export const ACTIVE_STATUSES: readonly OrderStatus[] = ['received', 'preparing', 'ready']

export type Board = ReadonlyMap<number, Order>

/**
 * Apply one order version to the board. Stale versions (older updated_at than what we hold) are
 * dropped. Served and cancelled orders leave the board. Returns the same reference when nothing changed.
 */
export function applyOrderToBoard(board: Board, order: Order): Board {
  const held = board.get(order.id)
  if (!isNewer(order, held)) return board
  const active = ACTIVE_STATUSES.includes(order.status)
  if (!held && !active) return board
  const next = new Map(board)
  if (active) next.set(order.id, order)
  else next.delete(order.id)
  return next
}

export function sortByAge(board: Board): Order[] {
  return [...board.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id)
}
