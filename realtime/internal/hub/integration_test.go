package hub

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func redisURL() string {
	if u := os.Getenv("REDIS_TEST_URL"); u != "" {
		return u
	}
	return "redis://localhost:56379/0"
}

// testRedis returns a client with a unique name, or skips when Redis is unreachable.
func testRedis(t *testing.T, name string) *redis.Client {
	t.Helper()
	opt, err := redis.ParseURL(redisURL())
	if err != nil {
		t.Skip("bad REDIS_TEST_URL")
	}
	opt.ClientName = name
	c := redis.NewClient(opt)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := c.Ping(ctx).Err(); err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}

func TestSubscriberDispatchesFromRedis(t *testing.T) {
	name := fmt.Sprintf("hubtest-%d", time.Now().UnixNano())
	sub := testRedis(t, name)
	pub := testRedis(t, name+"-pub")
	ch := name + ".new"

	h := newHub()
	k := newFake()
	start(t, h, TopicKitchen, k)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.RunSubscriber(ctx, sub, ch)
	waitFor(t, h.Ready)

	msg := event("order.created", idX)
	if err := pub.Publish(ctx, ch, msg).Err(); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return len(k.messages()) == 1 })
	if string(k.messages()[0]) != string(msg) {
		t.Fatal("frame differs from published bytes")
	}
}

func TestSubscriberWaiterEnvelopeKitchenOnly(t *testing.T) {
	name := fmt.Sprintf("hubtest-%d", time.Now().UnixNano())
	sub := testRedis(t, name)
	pub := testRedis(t, name+"-pub")
	orders, staff := name+".orders", name+".staff"

	h := newHub()
	k, o := newFake(), newFake()
	start(t, h, TopicKitchen, k)
	start(t, h, OrderTopic(idX), o)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.RunSubscriber(ctx, sub, orders, staff)
	waitFor(t, h.Ready)

	w := waiterEvent("waiter.requested", 7)
	if err := pub.Publish(ctx, staff, w).Err(); err != nil {
		t.Fatal(err)
	}
	// A following order event proves the waiter frame was fully processed before we assert.
	ev := event("order.created", idX)
	if err := pub.Publish(ctx, orders, ev).Err(); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return len(k.messages()) == 2 && len(o.messages()) == 1 })
	if string(k.messages()[0]) != string(w) {
		t.Fatal("waiter frame differs from published bytes")
	}
	if string(o.messages()[0]) != string(ev) {
		t.Fatal("order client got a non-order frame")
	}
}

func TestResyncAfterRedisSubscriberKilled(t *testing.T) {
	name := fmt.Sprintf("hubtest-%d", time.Now().UnixNano())
	sub := testRedis(t, name)
	admin := testRedis(t, name+"-admin")
	ch := name + ".new"

	h := newHub()
	k := newFake()
	start(t, h, TopicKitchen, k)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go h.RunSubscriber(ctx, sub, ch)
	waitFor(t, h.Ready)

	// Find the subscriber's connection by its client name and kill it server-side.
	list, err := admin.ClientList(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	killed := 0
	for _, line := range strings.Split(list, "\n") {
		if strings.Contains(line, "name="+name+" ") && strings.Contains(line, "flags=P") {
			var addr string
			for _, f := range strings.Fields(line) {
				if strings.HasPrefix(f, "addr=") {
					addr = strings.TrimPrefix(f, "addr=")
				}
			}
			if err := admin.Do(ctx, "CLIENT", "KILL", "ADDR", addr).Err(); err != nil {
				t.Fatal(err)
			}
			killed++
		}
	}
	if killed == 0 {
		t.Fatalf("no subscriber connection found in CLIENT LIST:\n%s", list)
	}

	waitFor(t, func() bool {
		for _, m := range k.messages() {
			if string(m) == string(ResyncMessage) {
				return true
			}
		}
		return false
	})
	// and the subscription works again afterwards
	waitFor(t, h.Ready)
	msg := event("order.created", idX)
	if err := admin.Publish(ctx, ch, msg).Err(); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return len(k.messages()) >= 2 })
}
