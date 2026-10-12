// Package config loads service configuration from environment variables.
package config

import (
	"log/slog"
	"os"
	"strings"
)

type Config struct {
	Port     string
	RedisURL string
	LogLevel slog.Level
	// AllowedOrigins are host patterns accepted in the WebSocket Origin header.
	AllowedOrigins []string
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Load reads PORT, REDIS_URL and LOG_LEVEL with local-dev defaults.
func Load() Config {
	var lvl slog.Level
	if err := lvl.UnmarshalText([]byte(strings.ToUpper(getenv("LOG_LEVEL", "INFO")))); err != nil {
		lvl = slog.LevelInfo
	}
	return Config{
		Port:           getenv("PORT", "8080"),
		RedisURL:       getenv("REDIS_URL", "redis://localhost:56379/0"),
		LogLevel:       lvl,
		AllowedOrigins: splitCSV(getenv("ALLOWED_ORIGINS", "localhost:5173,127.0.0.1:5173")),
	}
}

func splitCSV(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
