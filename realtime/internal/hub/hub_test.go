package hub

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
)

type fakeConn struct {
	mu     sync.Mutex
	got    [][]byte
	block  chan struct{} // if non-nil, Write blocks until closed
	closed chan websocket.StatusCode
}

func newFake() *fakeConn { return &fakeConn{closed: make(chan websocket.StatusCode, 1)} }

func (f *fakeConn) Write(ctx context.Context, _ websocket.MessageType, p []byte) error {
	if f.block != nil {
		select {
		case <-f.block:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	f.mu.Lock()
	f.got = append(f.got, p)
	f.mu.Unlock()
	return nil
}
func (f *fakeConn) Close(code websocket.StatusCode, _ string) error { f.closed <- code; return nil }
func (f *fakeConn) Ping(context.Context) error                      { return nil }
func (f *fakeConn) messages() [][]byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([][]byte(nil), f.got...)
}

func newHub() *Hub {
	return New(slog.New(slog.NewTextHandler(io.Discard, nil)), NewTestMetrics())
}

func start(t *testing.T, h *Hub, topic string, f *fakeConn) *Client {
	t.Helper()
	c := h.NewClient(topic, "test", f)
	if !h.Add(c) {
		t.Fatal("add failed")
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go h.Run(ctx, c)
	return c
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("condition not met in time")
}

const (
	idX = "5f0c7a9e-3f5e-4c1e-9b0e-2a7d6c1f8e10"
	idY = "11111111-2222-3333-4444-555555555555"
)

func event(typ, id string) []byte {
	return []byte(`{"type":"` + typ + `","version":1,"request_id":"r1","published_at_us":1,"order":{"public_id":"` + id + `"}}`)
}

func TestRouting(t *testing.T) {
	h := newHub()
	kitchen, ox, oy := newFake(), newFake(), newFake()
	start(t, h, TopicKitchen, kitchen)
	start(t, h, OrderTopic(idX), ox)
	start(t, h, OrderTopic(idY), oy)

	a, b := event("order.created", idX), event("order.status_changed", idY)
	h.Dispatch("orders.new", a, time.Now())
	h.Dispatch("orders.status", b, time.Now())

	waitFor(t, func() bool { return len(kitchen.messages()) == 2 })
	waitFor(t, func() bool { return len(ox.messages()) == 1 && len(oy.messages()) == 1 })
	if string(kitchen.messages()[0]) != string(a) || string(kitchen.messages()[1]) != string(b) {
		t.Fatal("kitchen frames not byte-identical to input")
	}
	if string(ox.messages()[0]) != string(a) || string(oy.messages()[0]) != string(b) {
		t.Fatal("order topics got wrong events")
	}
}

func TestSlowConsumerDroppedWithoutBlockingFast(t *testing.T) {
	h := newHub()
	h.SendBuffer = 2
	slow, fast := newFake(), newFake()
	slow.block = make(chan struct{}) // writer stuck: simulates a dead socket
	defer close(slow.block)
	sc := start(t, h, TopicKitchen, slow)
	start(t, h, TopicKitchen, fast)

	begin := time.Now()
	const n = 20
	for i := 0; i < n; i++ {
		h.Dispatch("orders.new", event("order.created", idX), time.Now())
		time.Sleep(time.Millisecond) // let the fast writer drain its small queue
	}
	if d := time.Since(begin); d > time.Second {
		t.Fatalf("dispatch blocked for %v", d)
	}
	waitFor(t, func() bool { return len(fast.messages()) == n })
	select {
	case code := <-slow.closed:
		if code != websocket.StatusPolicyViolation {
			t.Fatalf("close code = %v", code)
		}
	case <-time.After(time.Second):
		t.Fatal("slow consumer not closed")
	}
	h.mu.RLock()
	_, still := h.topics[TopicKitchen][sc]
	h.mu.RUnlock()
	if still {
		t.Fatal("slow consumer still registered")
	}
}

func TestRemoveOnCloseAndShutdown(t *testing.T) {
	h := newHub()
	f1, f2 := newFake(), newFake()
	c1 := start(t, h, TopicKitchen, f1)
	start(t, h, OrderTopic(idX), f2)
	h.Remove(c1)
	h.Remove(c1) // idempotent
	h.mu.RLock()
	if len(h.topics[TopicKitchen]) != 0 {
		t.Fatal("not unregistered")
	}
	h.mu.RUnlock()

	h.Shutdown()
	if code := <-f2.closed; code != websocket.StatusGoingAway {
		t.Fatalf("shutdown close code = %v", code)
	}
	if h.Add(h.NewClient(TopicKitchen, "x", newFake())) {
		t.Fatal("add succeeded after shutdown")
	}
}

func TestResyncReachesAllClients(t *testing.T) {
	h := newHub()
	k, o := newFake(), newFake()
	start(t, h, TopicKitchen, k)
	start(t, h, OrderTopic(idX), o)
	h.BroadcastResync()
	waitFor(t, func() bool { return len(k.messages()) == 1 && len(o.messages()) == 1 })
	if string(k.messages()[0]) != `{"type":"resync","version":1}` {
		t.Fatalf("bad resync frame %s", k.messages()[0])
	}
}

func TestMalformedEventIgnored(t *testing.T) {
	h := newHub()
	k := newFake()
	start(t, h, TopicKitchen, k)
	h.Dispatch("orders.new", []byte("not json"), time.Now())
	time.Sleep(20 * time.Millisecond)
	if len(k.messages()) != 0 {
		t.Fatal("malformed event was forwarded")
	}
}

func waiterEvent(typ string, id int) []byte {
	return []byte(`{"type":"` + typ + `","version":1,"request_id":"r2","published_at_us":1,"waiter_request":{"id":` +
		itoa(id) + `,"table_label":"T3","status":"open"}}`)
}

func itoa(i int) string { return string(rune('0' + i)) }

// Waiter envelopes carry no order.public_id: they must reach kitchen clients only, byte-identical,
// and never an order-topic (diner) client.
func TestWaiterEnvelopeKitchenOnly(t *testing.T) {
	h := newHub()
	kitchen, ox := newFake(), newFake()
	start(t, h, TopicKitchen, kitchen)
	start(t, h, OrderTopic(idX), ox)

	a, b := waiterEvent("waiter.requested", 7), waiterEvent("waiter.updated", 7)
	h.Dispatch("staff.requests", a, time.Now())
	h.Dispatch("staff.requests", b, time.Now())

	waitFor(t, func() bool { return len(kitchen.messages()) == 2 })
	if string(kitchen.messages()[0]) != string(a) || string(kitchen.messages()[1]) != string(b) {
		t.Fatal("kitchen frames not byte-identical to input")
	}
	time.Sleep(50 * time.Millisecond)
	if n := len(ox.messages()); n != 0 {
		t.Fatalf("order-topic client received %d waiter frames", n)
	}
}
