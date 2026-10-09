.PHONY: up down logs migrate seed test lint fmt

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:
	docker compose exec api alembic upgrade head

seed:
	docker compose exec api python -m seed.seed

test:
	cd api && uv run pytest
	cd realtime && go test ./...

lint:
	cd api && uv run ruff check . && uv run ruff format --check .
	cd realtime && go vet ./... && test -z "$$(gofmt -l .)"
	cd web && npm run lint

fmt:
	cd api && uv run ruff check --fix . && uv run ruff format .
	cd realtime && gofmt -w .
