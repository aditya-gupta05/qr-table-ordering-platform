// Mirrors api/app/schemas.py, api/app/vocab.py and docs/contracts/events.md.
// Keep in sync with the backend; do not add fields the API does not send.

export const DIETARY_TAGS = [
  'vegetarian',
  'vegan',
  'gluten_free',
  'halal',
  'dairy_free',
] as const
export type DietaryTag = (typeof DIETARY_TAGS)[number]

export const ALLERGENS = [
  'gluten',
  'dairy',
  'egg',
  'peanut',
  'tree_nut',
  'soy',
  'fish',
  'shellfish',
  'sesame',
  'mustard',
  'celery',
  'sulphites',
] as const
export type Allergen = (typeof ALLERGENS)[number]

export const ORDER_STATUSES = ['received', 'preparing', 'ready', 'served', 'cancelled'] as const
export type OrderStatus = (typeof ORDER_STATUSES)[number]

export interface TableOut {
  id: number
  label: string
}

export interface MenuItem {
  id: number
  name: string
  description: string
  price_cents: number
  dietary_tags: DietaryTag[]
  allergens: Allergen[]
  spice_level: number
  is_available: boolean
}

export interface MenuCategory {
  id: number
  name: string
  items: MenuItem[]
}

export interface OrderItemIn {
  menu_item_id: number
  qty: number
  notes?: string | null
}

export interface OrderIn {
  table_token: string
  items: OrderItemIn[]
  notes?: string | null
}

export interface OrderItemOut {
  menu_item_id: number
  item_name: string
  qty: number
  unit_price_cents: number
  notes: string | null
}

export interface Order {
  id: number
  public_id: string
  table_label: string
  status: OrderStatus
  total_cents: number
  notes: string | null
  items: OrderItemOut[]
  created_at: string
  updated_at: string
}

export interface StatusIn {
  status: OrderStatus
}

// Realtime frames. Consumers must ignore unknown fields.
export interface OrderEnvelope {
  type: 'order.created' | 'order.status_changed'
  version: number
  request_id: string | null
  published_at_us: number
  order: Order
}

export interface ResyncFrame {
  type: 'resync'
  version: number
}

export type WaiterReason = 'allergy_or_dietary' | 'question_not_answered' | 'diner_asked_for_staff' | 'other'
export type WaiterStatus = 'open' | 'acknowledged' | 'closed'
export type WaiterSource = 'chat' | 'button'

export interface WaiterRequest {
  id: number
  table_label: string
  reason: WaiterReason
  note: string | null
  source: WaiterSource
  status: WaiterStatus
  session_id: string | null
  created_at: string
  updated_at: string
}

export interface WaiterListOut {
  items: WaiterRequest[]
}

export interface WaiterRequestIn {
  table_token: string
  reason: WaiterReason
  note?: string | null
  session_id?: string
}

/** staff.requests frame (docs/contracts/chat.md). Kitchen clients only. */
export interface WaiterEnvelope {
  type: 'waiter.requested' | 'waiter.updated'
  version: number
  request_id: string | null
  published_at_us: number
  waiter_request: WaiterRequest
}

export type Frame = OrderEnvelope | ResyncFrame | WaiterEnvelope

// ---- Chat (docs/contracts/chat.md). Plain text everywhere; never render as HTML. ----

export type ChatStatus = 'answered' | 'not_found' | 'clarify' | 'off_topic' | 'needs_staff' | 'blocked' | 'error'
export type ChatOutcome =
  | 'answered'
  | 'not_found'
  | 'escalation_offered'
  | 'off_topic'
  | 'clarify'
  | 'action_only'
  | 'blocked'
  | 'error'
export type ActionStatus = 'proposed' | 'accepted' | 'dismissed'

export interface MenuItemCitation {
  kind: 'menu_item'
  menu_item_id: number
  name: string
  price_cents: number
}

export interface PassageCitation {
  kind: 'passage'
  chunk_id: number
  title: string
  section: string | null
  source_type: string
}

export type Citation = MenuItemCitation | PassageCitation

export interface AllergenNoticeItem {
  menu_item_id: number
  name: string
  allergens: Allergen[]
  dietary_tags: DietaryTag[]
}

export interface AllergenNoticeData {
  items: AllergenNoticeItem[]
  disclaimer: string
  source_chunk_id: number | null
}

export interface AddToCartAction {
  type: 'add_to_cart'
  action_id: string
  menu_item_id: number
  name: string
  price_cents: number
  qty: number
  note: string | null
  allergens: Allergen[]
  allergen_conflict: Allergen[]
  status: ActionStatus
}

export interface CallWaiterAction {
  type: 'call_waiter'
  action_id: string
  reason: WaiterReason
  note: string | null
  status: ActionStatus
}

export type ChatAction = AddToCartAction | CallWaiterAction

export interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  status: ChatStatus | null
  citations: Citation[]
  allergen_notice: AllergenNoticeData | null
  actions: ChatAction[]
  feedback: boolean | null
  created_at: string
}

/** POST /api/chat/sessions (201). */
export interface ChatSessionCreated {
  session_id: string
  table_label: string
  max_message_chars: number
  turns_remaining: number
}

/** GET /api/chat/sessions/{sid}. */
export interface ChatSessionDetail {
  session_id: string
  table_label: string
  turns_remaining: number
  messages: Message[]
}

export interface TurnFrame {
  turn_id: string
  turn_index: number
  request_id: string
}

export interface StatusFrame {
  stage: 'thinking' | 'searching_menu' | 'searching_info' | 'checking_item' | 'checking_answer'
  label: string
}

export interface AnswerFrame {
  message: Message
}

export interface ErrorFrame {
  code: 'llm_unavailable' | 'timeout' | 'budget' | 'internal'
  message: string
  fallback: Message
}

export interface DoneFrame {
  turn_id: string
  outcome: ChatOutcome
  latency_ms: number
}

export interface ActionResult {
  action: ChatAction
  waiter_request: WaiterRequest | null
}

/** 429 detail (object form). */
export interface ChatLimitDetail {
  message: string
  reason: 'session_limit' | 'table_rate' | 'budget'
}
