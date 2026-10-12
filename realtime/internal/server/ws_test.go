package server

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/redis/go-redis/v9"

	"github.com/aditya-gupta05/qr-table-ordering-platform/realtime/internal/hub"
)

const (
	idX = "5f0c7a9e-3f5e-4c1e-9b0e-2a7d6c1f8e10"
	idY = "11111111-2222-3333-4444-555555555555"
)

func wsEnv(t *testing.T) (*httptest.Server, *redis.Client, string) {
	t.Helper()
	url := "redis://localhost:56379/0"
	opt, _ := redis.ParseURL(url)
	rdb := redis.NewClient(opt)
	if err := rdb.Ping(context.Background()).Err(); err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	t.Cleanup(func() { rdb.Close() })

	log := slog.New(slog.NewJSONHandler(io.Discard, nil))
	h := hub.New(log, hub.NewTestMetrics())
	ch := fmt.Sprintf("wstest-%d.new", time.Now().UnixNano())
	ctx, cancel := context.WithCancel(context.Background())
	go h.RunSubscriber(ctx, rdb, ch)
	srv := httptest.NewServer(New(Options{
		Log: log, Redis: fakePinger{}, Version: "test", Hub: h,
		AllowedOrigins: []string{"localhost:5173"},
	}))
	t.Cleanup(func() { cancel(); h.Shutdown(); srv.Close() })
	deadline := time.Now().Add(2 * time.Second)
	for !h.Ready() {
		if time.Now().After(deadline) {
			t.Fatal("subscriber not ready")
		}
		time.Sleep(5 * time.Millisecond)
	}
	return srv, rdb, ch
}

func dial(t *testing.T, srv *httptest.Server, path string) *websocket.Conn {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	c, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(srv.URL, "http")+path, nil)
	if err != nil {
		t.Fatalf("dial %s: %v", path, err)
	}
	t.Cleanup(func() { c.CloseNow() })
	return c
}

func read(t *testing.T, c *websocket.Conn, wait time.Duration) (string, bool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), wait)
	defer cancel()
	_, b, err := c.Read(ctx)
	if err != nil {
		return "", false
	}
	return string(b), true
}

func ev(typ, id string) string {
	return `{"type":"` + typ + `","version":1,"request_id":"r","published_at_us":1,"order":{"public_id":"` + id + `","status":"received"}}`
}

func TestWebSocketEndToEnd(t *testing.T) {
	srv, rdb, ch := wsEnv(t)
	kitchen := dial(t, srv, "/ws/kitchen")
	ox := dial(t, srv, "/ws/orders/"+idX)
	oy := dial(t, srv, "/ws/orders/"+strings.ToUpper(idY)) // case-insensitive id
	time.Sleep(50 * time.Millisecond)                      // registrations complete

	msg := ev("order.status_changed", idX)
	if err := rdb.Publish(context.Background(), ch, msg).Err(); err != nil {
		t.Fatal(err)
	}
	if got, ok := read(t, kitchen, 2*time.Second); !ok || got != msg {
		t.Fatalf("kitchen got %q ok=%v", got, ok)
	}
	if got, ok := read(t, ox, 2*time.Second); !ok || got != msg {
		t.Fatalf("order X got %q ok=%v", got, ok)
	}
	if got, ok := read(t, oy, 300*time.Millisecond); ok {
		t.Fatalf("order Y must not receive X's event, got %q", got)
	}
}

func TestInvalidOrderIDIs400(t *testing.T) {
	srv, _, _ := wsEnv(t)
	resp, err := http.Get(srv.URL + "/ws/orders/not-a-uuid")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 400 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestBadOriginRejected(t *testing.T) {
	srv, _, _ := wsEnv(t)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, resp, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(srv.URL, "http")+"/ws/kitchen",
		&websocket.DialOptions{HTTPHeader: http.Header{"Origin": {"http://evil.example"}}})
	if err == nil {
		t.Fatal("expected origin rejection")
	}
	if resp == nil || resp.StatusCode != 403 {
		t.Fatalf("resp = %v", resp)
	}
	// allowed origin works
	c, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(srv.URL, "http")+"/ws/kitchen",
		&websocket.DialOptions{HTTPHeader: http.Header{"Origin": {"http://localhost:5173"}}})
	if err != nil {
		t.Fatalf("allowed origin rejected: %v", err)
	}
	c.CloseNow()
}
