import re


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_request_id_echoed(client):
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_request_id_generated(client):
    r = await client.get("/healthz")
    assert re.fullmatch(r"[0-9a-f-]{36}", r.headers["x-request-id"])


async def test_readyz_ok(client):
    r = await client.get("/readyz")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}


async def test_readyz_503_when_redis_down(client, monkeypatch):
    from app import config

    monkeypatch.setattr(
        config.get_settings(), "redis_url", "redis://localhost:1/0"
    )  # nothing listens on port 1
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["redis"] == "fail"


async def test_metrics_use_route_template(client):
    await client.get("/healthz")
    await client.get("/nope-" + "x" * 8)
    body = (await client.get("/metrics")).text
    assert 'http_requests_total{method="GET",route="/healthz",status="200"}' in body
    assert "http_request_duration_seconds_bucket" in body
    assert "nope-" not in body  # raw paths must not leak into label cardinality
    assert 'route="unmatched"' in body


async def test_metrics_multiprocess_branch(client, monkeypatch, tmp_path):
    # With the env var set /metrics aggregates mmap files on a fresh registry (empty dir here).
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
