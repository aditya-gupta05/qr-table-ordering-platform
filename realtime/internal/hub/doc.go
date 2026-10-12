// Package hub is the WebSocket fan-out hub.
//
// Flow: one subscriber goroutine holds a Redis pub/sub subscription to the order channels
// the API publishes to (after the DB commit). Each message is decoded only far enough to
// route it (type, order.public_id, request_id, published_at_us); the original bytes are
// forwarded verbatim. Clients register under a topic: "kitchen" receives everything,
// "order:<public_id>" receives only that order's events.
//
// Concurrency model: a sync.RWMutex-guarded map of topic -> clients. Fan-out takes the read
// lock and does only non-blocking channel sends, so it is fast and cannot be stalled by a
// slow socket; register/unregister (rare) take the write lock. A single owner goroutine
// with register/unregister channels would add a channel hop to the latency-critical path
// for no benefit, since nothing blocks while the lock is held.
//
// Every client has a bounded send queue (64) drained by its own writer goroutine with a
// write timeout. If the queue is full the client is closed with StatusPolicyViolation
// rather than delaying anybody else. Redis pub/sub is at-most-once, so after the
// subscription recovers from a disconnect the hub sends {"type":"resync","version":1} to
// every client so it can refetch the REST snapshot.
package hub
