// Package hub will hold the WebSocket fan-out hub (Phase 2). Not implemented in Phase 0.
//
// Design: a single goroutine subscribes to Redis pub/sub channels published by the API
// (only after the DB transaction commits). Each message is routed by channel name, for
// example orders:new for the kitchen board and order:{id} for a diner's status page, to
// the set of connected WebSocket clients registered for that channel. Every client has a
// bounded send queue drained by its own writer goroutine; a slow client is dropped rather
// than allowed to block the broadcast loop. The request_id carried in the message is logged
// at each hop so one order can be traced API -> Redis -> hub -> WS.
package hub

import _ "github.com/coder/websocket" // pinned now; used from Phase 2
