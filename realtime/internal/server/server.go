// Package server wires HTTP handlers, middleware and metrics.
package server

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/coder/websocket"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/aditya-gupta05/qr-table-ordering-platform/realtime/internal/hub"
)

// Pinger is satisfied by *redis.Client; an interface keeps /readyz testable.
type Pinger interface {
	Ping(ctx context.Context) error
}

type ctxKey struct{}

// RequestID returns the request id stored in ctx by the middleware.
func RequestID(ctx context.Context) string {
	id, _ := ctx.Value(ctxKey{}).(string)
	return id
}

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	// format as UUID-shaped string so ids look the same as the API's uuid4
	h := hex.EncodeToString(b)
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

// Hijack lets websocket.Accept take over the connection through the logging wrapper.
func (r *statusRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	h, ok := r.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, errors.New("hijack not supported")
	}
	r.status = http.StatusSwitchingProtocols
	return h.Hijack()
}

func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// Middleware reads or generates X-Request-ID, echoes it, and logs one line per request.
func Middleware(log *slog.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if id == "" {
			id = newID()
		}
		w.Header().Set("X-Request-ID", id)
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		start := time.Now()
		next.ServeHTTP(rec, r.WithContext(context.WithValue(r.Context(), ctxKey{}, id)))
		log.Info("http_request",
			"request_id", id,
			"method", r.Method,
			"path", r.URL.Path,
			"status", rec.status,
			"duration_ms", float64(time.Since(start).Microseconds())/1000.0,
		)
	})
}

// Options configures New.
type Options struct {
	Log            *slog.Logger
	Redis          Pinger
	Version        string
	Hub            *hub.Hub // nil disables the /ws routes
	Registry       *prometheus.Registry
	AllowedOrigins []string
}

// NewRegistry returns a registry with the Go/process collectors and realtime_build_info.
func NewRegistry(version string) *prometheus.Registry {
	reg := prometheus.NewRegistry()
	reg.MustRegister(
		prometheus.NewGoCollector(),
		prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}),
	)
	buildInfo := prometheus.NewGauge(prometheus.GaugeOpts{
		Name:        "realtime_build_info",
		Help:        "Build information; value is always 1.",
		ConstLabels: prometheus.Labels{"version": version},
	})
	buildInfo.Set(1)
	reg.MustRegister(buildInfo)
	return reg
}

var uuidRE = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// New builds the HTTP handler.
func New(o Options) http.Handler {
	log, redis := o.Log, o.Redis
	reg := o.Registry
	if reg == nil {
		reg = NewRegistry(o.Version)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		w.Header().Set("Content-Type", "application/json")
		if err := redis.Ping(ctx); err != nil {
			log.Warn("readyz_redis_failed", "request_id", RequestID(r.Context()), "error", err.Error())
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(`{"status":"unavailable","redis":"fail"}`))
			return
		}
		_, _ = w.Write([]byte(`{"status":"ok","redis":"ok"}`))
	})
	mux.Handle("GET /metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{}))
	if o.Hub != nil {
		accept := &websocket.AcceptOptions{OriginPatterns: o.AllowedOrigins}
		serve := func(w http.ResponseWriter, r *http.Request, topic string) {
			conn, err := websocket.Accept(w, r, accept)
			if err != nil {
				return // Accept already wrote the error response (e.g. 403 bad origin)
			}
			o.Hub.Serve(r.Context(), conn, topic, r.RemoteAddr)
		}
		mux.HandleFunc("GET /ws/kitchen", func(w http.ResponseWriter, r *http.Request) {
			serve(w, r, hub.TopicKitchen)
		})
		mux.HandleFunc("GET /ws/orders/{public_id}", func(w http.ResponseWriter, r *http.Request) {
			id := r.PathValue("public_id")
			if !uuidRE.MatchString(id) {
				http.Error(w, "invalid order id", http.StatusBadRequest)
				return
			}
			serve(w, r, hub.OrderTopic(strings.ToLower(id)))
		})
	}
	return Middleware(log, mux)
}
