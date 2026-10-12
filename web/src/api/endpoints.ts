import { request } from './client'
import type {
  MenuCategory,
  Order,
  OrderIn,
  OrderStatus,
  TableOut,
  WaiterListOut,
  WaiterRequest,
  WaiterRequestIn,
  WaiterStatus,
} from './types'

export function getTable(qrToken: string, signal?: AbortSignal) {
  return request<TableOut>(`/api/tables/${encodeURIComponent(qrToken)}`, { signal })
}

export interface MenuFilter {
  excludeAllergens?: readonly string[]
  dietary?: readonly string[]
}

export function getMenu(filter: MenuFilter = {}, signal?: AbortSignal) {
  const params = new URLSearchParams()
  if (filter.excludeAllergens?.length) params.set('exclude_allergens', filter.excludeAllergens.join(','))
  if (filter.dietary?.length) params.set('dietary', filter.dietary.join(','))
  const qs = params.toString()
  return request<MenuCategory[]>(`/api/menu${qs ? `?${qs}` : ''}`, { signal })
}

export function createOrder(body: OrderIn, idempotencyKey: string) {
  return request<Order>('/api/orders', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(body),
  })
}

export function getOrder(publicId: string, signal?: AbortSignal) {
  return request<Order>(`/api/orders/${encodeURIComponent(publicId)}`, { signal })
}

export function listKitchenOrders(statuses: readonly OrderStatus[], signal?: AbortSignal) {
  return request<Order[]>(`/api/kitchen/orders?status=${statuses.join(',')}`, { signal })
}

export function updateOrderStatus(orderId: number, status: OrderStatus) {
  return request<Order>(`/api/kitchen/orders/${orderId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function createWaiterRequest(body: WaiterRequestIn) {
  return request<WaiterRequest>('/api/waiter-requests', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function listWaiterRequests(status: WaiterStatus, signal?: AbortSignal) {
  const data = await request<WaiterListOut>(`/api/waiter-requests?status=${status}`, { signal })
  return data.items
}

export function updateWaiterRequest(id: number, status: 'acknowledged' | 'closed') {
  return request<WaiterRequest>(`/api/waiter-requests/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}
