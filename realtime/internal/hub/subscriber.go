package hub

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// RunSubscriber subscribes to channels and dispatches messages until ctx is cancelled.
// go-redis reconnects and resubscribes by itself on the next Receive after a connection
// error. We detect recovery as: an error happened, then a subscription confirmation with
// Count == len(channels) arrives; at that point events may have been missed, so resync.
func (h *Hub) RunSubscriber(ctx context.Context, rdb *redis.Client, channels ...string) {
	ps := rdb.Subscribe(ctx, channels...)
	defer ps.Close()

	lost := false
	backoff := 100 * time.Millisecond
	for ctx.Err() == nil {
		msg, err := ps.Receive(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			if h.ready.Swap(false) || !lost {
				h.log.Error("redis_subscription_lost", "error", err.Error())
			}
			lost = true
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			backoff = min(backoff*2, 2*time.Second)
			continue
		}
		switch v := msg.(type) {
		case *redis.Subscription:
			if v.Kind == "subscribe" && v.Count >= len(channels) {
				backoff = 100 * time.Millisecond
				h.ready.Store(true)
				h.log.Info("redis_subscribed", "channels", channels)
				if lost {
					lost = false
					h.BroadcastResync()
				}
			}
		case *redis.Message:
			h.Dispatch(v.Channel, []byte(v.Payload), time.Now())
		}
	}
}
