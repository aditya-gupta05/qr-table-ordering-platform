.PHONY: up down logs migrate seed ingest menu-pdf test lint fmt

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

# Runs on the host against compose Postgres (:55432); models cache in ~/.cache/fastembed.
# Pass flags with ARGS, e.g. make ingest ARGS="--all-configs --model snowflake/snowflake-arctic-embed-xs"
ingest:
	cd api && uv run python -m app.retrieval.ingest $(ARGS)

menu-pdf:
	cd api && uv run python ../scripts/gen_menu_pdf.py

test:
	cd api && uv run pytest
	cd realtime && go test -race ./...

lint:
	cd api && uv run ruff check . ../eval && uv run ruff format --check . ../eval
	cd realtime && go vet ./... && test -z "$$(gofmt -l .)"
	cd web && npm run lint

fmt:
	cd api && uv run ruff check --fix . ../eval && uv run ruff format . ../eval
	cd realtime && gofmt -w .
