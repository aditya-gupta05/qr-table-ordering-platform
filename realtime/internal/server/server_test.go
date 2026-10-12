package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
)

type fakePinger struct{ err error }

func (f fakePinger) Ping(context.Context) error { return f.err }

func newTest(p Pinger) (http.Handler, *bytes.Buffer) {
	buf := &bytes.Buffer{}
	return New(Options{Log: slog.New(slog.NewJSONHandler(buf, nil)), Redis: p, Version: "test"}), buf
}

func TestHealthz(t *testing.T) {
	h, _ := newTest(fakePinger{})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/healthz", nil))
	if rec.Code != 200 {
		t.Fatalf("status = %d", rec.Code)
	}
}

func TestReadyz(t *testing.T) {
	h, _ := newTest(fakePinger{})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/readyz", nil))
	if rec.Code != 200 {
		t.Fatalf("ok case status = %d", rec.Code)
	}
	h, _ = newTest(fakePinger{err: errors.New("down")})
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/readyz", nil))
	if rec.Code != 503 {
		t.Fatalf("down case status = %d", rec.Code)
	}
}

func TestRequestIDEchoedAndLogged(t *testing.T) {
	h, buf := newTest(fakePinger{})
	req := httptest.NewRequest("GET", "/healthz", nil)
	req.Header.Set("X-Request-ID", "abc-123")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if got := rec.Header().Get("X-Request-ID"); got != "abc-123" {
		t.Fatalf("header = %q", got)
	}
	var line map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &line); err != nil {
		t.Fatalf("log not JSON: %v: %s", err, buf)
	}
	if line["request_id"] != "abc-123" || line["msg"] != "http_request" || line["status"].(float64) != 200 {
		t.Fatalf("unexpected log line: %v", line)
	}
}

func TestRequestIDGenerated(t *testing.T) {
	h, _ := newTest(fakePinger{})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/healthz", nil))
	if !regexp.MustCompile(`^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`).MatchString(rec.Header().Get("X-Request-ID")) {
		t.Fatalf("bad generated id %q", rec.Header().Get("X-Request-ID"))
	}
}

func TestMetricsHasBuildInfo(t *testing.T) {
	h, _ := newTest(fakePinger{})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	body := rec.Body.String()
	if !strings.Contains(body, `realtime_build_info{version="test"} 1`) || !strings.Contains(body, "go_goroutines") {
		t.Fatalf("metrics missing expected series")
	}
}
