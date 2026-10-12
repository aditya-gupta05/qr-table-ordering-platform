package hub

import (
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
	"github.com/prometheus/client_golang/prometheus"
)

const (
	TopicKitchen = "kitchen"
	topicOrder   = "order:"

	DefaultSendBuffer = 64
	writeTimeout      = 5 * time.Second
)

// ResyncMessage tells clients events may have been missed and they should refetch.
var ResyncMessage = []byte(`{"type":"resync","version":1}`)

// OrderTopic is the topic for one order, keyed by its public UUID (lowercase).
func OrderTopic(publicID string) string { return topicOrder + strings.ToLower(publicID) }

// Conn is the subset of *websocket.Conn the hub needs (fakeable in tests).
type Conn interface {
	Write(ctx context.Context, typ websocket.MessageType, p []byte) error
	Close(code websocket.StatusCode, reason string) error
	Ping(ctx context.Context) error
}

type frame struct {
	data          []byte
	publishedAtUS int64 // 0 for control frames
}

// Client is one connected WebSocket.
type Client struct {
	topic  string
	remote string
	conn   Conn
	send   chan frame
	done   chan struct{}
	once   sync.Once
}

// Hub routes events to clients.
type Hub struct {
	log *slog.Logger
	m   *Metrics

	// SendBuffer is the per-client queue size; PingInterval/PingTimeout drive keepalive.
	SendBuffer   int
	PingInterval time.Duration
	PingTimeout  time.Duration

	mu     sync.RWMutex
	topics map[string]map[*Client]struct{}
	closed bool

	ready atomic.Bool
}

// New creates a hub. A nil registry-less Metrics is not allowed; use NewMetrics.
func New(log *slog.Logger, m *Metrics) *Hub {
	return &Hub{
		log: log, m: m,
		SendBuffer:   DefaultSendBuffer,
		PingInterval: 20 * time.Second,
		PingTimeout:  10 * time.Second,
		topics:       map[string]map[*Client]struct{}{},
	}
}

// NewTestMetrics is a convenience for tests and callers without a shared registry.
func NewTestMetrics() *Metrics { return NewMetrics(prometheus.NewRegistry()) }

// NewClient builds a client for topic. Call Add, then Run.
func (h *Hub) NewClient(topic, remote string, conn Conn) *Client {
	return &Client{
		topic: topic, remote: remote, conn: conn,
		send: make(chan frame, h.SendBuffer), done: make(chan struct{}),
	}
}

func topicKind(topic string) string {
	if topic == TopicKitchen {
		return "kitchen"
	}
	return "order"
}

// Add registers c; it returns false if the hub is shutting down.
func (h *Hub) Add(c *Client) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed {
		return false
	}
	set := h.topics[c.topic]
	if set == nil {
		set = map[*Client]struct{}{}
		h.topics[c.topic] = set
	}
	set[c] = struct{}{}
	h.m.Connections.WithLabelValues(topicKind(c.topic)).Inc()
	h.log.Info("ws_connect", "topic", c.topic, "remote", c.remote)
	return true
}

// Remove unregisters c (idempotent).
func (h *Hub) Remove(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.removeLocked(c)
}

func (h *Hub) removeLocked(c *Client) {
	set := h.topics[c.topic]
	if _, ok := set[c]; !ok {
		return
	}
	delete(set, c)
	if len(set) == 0 {
		delete(h.topics, c.topic)
	}
	h.m.Connections.WithLabelValues(topicKind(c.topic)).Dec()
	h.log.Info("ws_disconnect", "topic", c.topic, "remote", c.remote)
}

// Close terminates the client (idempotent). The close handshake runs off the caller's
// goroutine so a stuck peer can never stall fan-out.
func (c *Client) Close(code websocket.StatusCode, reason string) {
	c.once.Do(func() {
		close(c.done)
		go func() { _ = c.conn.Close(code, reason) }()
	})
}

// enqueue is non-blocking; false means the queue was full.
func (c *Client) enqueue(f frame) bool {
	select {
	case c.send <- f:
		return true
	default:
		return false
	}
}

// Run is the writer loop (blocking). It also sends keepalive pings.
func (h *Hub) Run(ctx context.Context, c *Client) {
	var tick <-chan time.Time
	if h.PingInterval > 0 {
		t := time.NewTicker(h.PingInterval)
		defer t.Stop()
		tick = t.C
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-c.done:
			return
		case f := <-c.send:
			wctx, cancel := context.WithTimeout(ctx, writeTimeout)
			err := c.conn.Write(wctx, websocket.MessageText, f.data)
			cancel()
			if err != nil {
				c.Close(websocket.StatusInternalError, "write failed")
				return
			}
			h.m.MessagesSent.Inc()
			if f.publishedAtUS > 0 {
				d := float64(time.Now().UnixMicro()-f.publishedAtUS) / 1e6
				h.m.PublishToSendSec.Observe(max(d, 0))
			}
		case <-tick:
			pctx, cancel := context.WithTimeout(ctx, h.PingTimeout)
			err := c.conn.Ping(pctx)
			cancel()
			if err != nil {
				c.Close(websocket.StatusGoingAway, "ping timeout")
				return
			}
		}
	}
}

// Serve runs a freshly accepted websocket until it closes. Clients never send data; the
// read side only detects close (CloseRead closes the conn on any data frame).
func (h *Hub) Serve(ctx context.Context, conn *websocket.Conn, topic, remote string) {
	ctx = conn.CloseRead(ctx)
	c := h.NewClient(topic, remote, conn)
	if !h.Add(c) {
		_ = conn.Close(websocket.StatusGoingAway, "server shutting down")
		return
	}
	defer h.Remove(c)
	defer func() { _ = conn.CloseNow() }()
	h.Run(ctx, c)
}

type routing struct {
	Type          string `json:"type"`
	RequestID     string `json:"request_id"`
	PublishedAtUS int64  `json:"published_at_us"`
	Order         struct {
		PublicID string `json:"public_id"`
	} `json:"order"`
}

// Dispatch routes one Redis message. raw is forwarded verbatim.
func (h *Hub) Dispatch(channel string, raw []byte, received time.Time) {
	h.m.EventsReceived.WithLabelValues(channel).Inc()
	var r routing
	if err := json.Unmarshal(raw, &r); err != nil {
		h.log.Warn("event_decode_failed", "channel", channel, "error", err.Error())
		return
	}
	f := frame{data: raw, publishedAtUS: r.PublishedAtUS}
	orderTopic := ""
	if r.Order.PublicID != "" {
		orderTopic = OrderTopic(r.Order.PublicID)
	}

	var slow []*Client
	n := 0
	h.mu.RLock()
	for _, topic := range [2]string{TopicKitchen, orderTopic} {
		if topic == "" {
			continue
		}
		for c := range h.topics[topic] {
			n++
			if !c.enqueue(f) {
				slow = append(slow, c)
			}
		}
	}
	h.mu.RUnlock()
	h.m.FanoutSeconds.Observe(time.Since(received).Seconds())

	for _, c := range slow {
		h.m.SlowDrops.Inc()
		h.log.Warn("ws_slow_consumer_dropped", "topic", c.topic, "remote", c.remote)
		c.Close(websocket.StatusPolicyViolation, "slow consumer")
		h.Remove(c)
	}
	h.log.Info("fanout", "request_id", r.RequestID, "type", r.Type, "channel", channel,
		"recipients", n, "dropped", len(slow))
}

// BroadcastResync tells every client to refetch its REST snapshot.
func (h *Hub) BroadcastResync() {
	h.m.Resyncs.Inc()
	var slow []*Client
	n := 0
	h.mu.RLock()
	for _, set := range h.topics {
		for c := range set {
			n++
			if !c.enqueue(frame{data: ResyncMessage}) {
				slow = append(slow, c)
			}
		}
	}
	h.mu.RUnlock()
	for _, c := range slow {
		h.m.SlowDrops.Inc()
		c.Close(websocket.StatusPolicyViolation, "slow consumer")
		h.Remove(c)
	}
	h.log.Warn("resync_broadcast", "recipients", n)
}

// Ready reports whether the Redis subscription is currently established.
func (h *Hub) Ready() bool { return h.ready.Load() }

// Shutdown closes every client with StatusGoingAway and refuses new ones.
func (h *Hub) Shutdown() {
	h.mu.Lock()
	h.closed = true
	var all []*Client
	for _, set := range h.topics {
		for c := range set {
			all = append(all, c)
		}
	}
	for _, c := range all {
		h.removeLocked(c)
	}
	h.mu.Unlock()
	for _, c := range all {
		c.Close(websocket.StatusGoingAway, "server shutting down")
	}
	h.log.Info("hub_shutdown", "clients_closed", len(all))
}
