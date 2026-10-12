package hub

import "github.com/prometheus/client_golang/prometheus"

// Metrics holds every Prometheus collector the hub updates.
type Metrics struct {
	Connections      *prometheus.GaugeVec
	EventsReceived   *prometheus.CounterVec
	MessagesSent     prometheus.Counter
	SlowDrops        prometheus.Counter
	Resyncs          prometheus.Counter
	FanoutSeconds    prometheus.Histogram
	PublishToSendSec prometheus.Histogram
}

var latencyBuckets = []float64{0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1}

// NewMetrics creates the collectors and registers them on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Connections: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "realtime_connections", Help: "Open WebSocket connections.",
		}, []string{"topic_kind"}),
		EventsReceived: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "realtime_events_received_total", Help: "Events received from Redis.",
		}, []string{"channel"}),
		MessagesSent: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "realtime_messages_sent_total", Help: "Frames written to WebSocket clients.",
		}),
		SlowDrops: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "realtime_slow_consumer_drops_total", Help: "Clients closed because their queue was full.",
		}),
		Resyncs: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "realtime_resyncs_total", Help: "Resync broadcasts after Redis subscription recovery.",
		}),
		FanoutSeconds: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name: "realtime_fanout_seconds", Help: "Redis receive until enqueued to all recipients.",
			Buckets: latencyBuckets,
		}),
		PublishToSendSec: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "realtime_publish_to_send_seconds",
			Help:    "API published_at_us until the frame was written to the socket.",
			Buckets: latencyBuckets,
		}),
	}
	reg.MustRegister(m.Connections, m.EventsReceived, m.MessagesSent, m.SlowDrops, m.Resyncs,
		m.FanoutSeconds, m.PublishToSendSec)
	return m
}
