.PHONY: install lint format typecheck test check run build

install:  ## Create .venv with runtime, extras and dev tools (uv.lock is authoritative)
	uv sync --locked --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

run:  ## Needs .env (cp .env.example .env)
	uv run --env-file .env python -m tgbot

build:
	docker build -t telegram-github-bot:local .
