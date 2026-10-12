package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/aditya-gupta05/qr-table-ordering-platform/realtime/internal/config"
	"github.com/aditya-gupta05/qr-table-ordering-platform/realtime/internal/hub"
	"github.com/aditya-gupta05/qr-table-ordering-platform/realtime/internal/server"
)

const version = "0.1.0"

type redisPinger struct{ c *redis.Client }

func (p redisPinger) Ping(ctx context.Context) error { return p.c.Ping(ctx).Err() }

func main() {
	healthcheck := flag.Bool("healthcheck", false, "probe /healthz and exit (used by the container healthcheck)")
	flag.Parse()
	cfg := config.Load()

	if *healthcheck {
		c := http.Client{Timeout: 2 * time.Second}
		resp, err := c.Get("http://127.0.0.1:" + cfg.Port + "/healthz")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		return
	}

	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))

	opt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Error("invalid_redis_url", "error", err.Error())
		os.Exit(1)
	}
	rdb := redis.NewClient(opt)
	defer rdb.Close()

	// Dedicated client for the long-lived subscription; the name makes it identifiable in
	// CLIENT LIST (used by the resync test).
	subOpt := *opt
	subOpt.ClientName = "realtime-subscriber"
	subClient := redis.NewClient(&subOpt)
	defer subClient.Close()

	reg := server.NewRegistry(version)
	h := hub.New(log, hub.NewMetrics(reg))

	srv := &http.Server{
		Addr: ":" + cfg.Port,
		Handler: server.New(server.Options{
			Log: log, Redis: redisPinger{rdb}, Version: version, Hub: h, Registry: reg,
			AllowedOrigins: cfg.AllowedOrigins,
		}),
		ReadHeaderTimeout: 5 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go h.RunSubscriber(ctx, subClient, "orders.new", "orders.status", "staff.requests")

	go func() {
		log.Info("realtime_startup", "port", cfg.Port, "version", version)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("listen_failed", "error", err.Error())
			stop()
		}
	}()

	<-ctx.Done()
	log.Info("realtime_shutdown_start")
	h.Shutdown() // hijacked websockets are not tracked by http.Server.Shutdown
	shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		log.Error("shutdown_failed", "error", err.Error())
	}
	log.Info("realtime_shutdown_done")
}
