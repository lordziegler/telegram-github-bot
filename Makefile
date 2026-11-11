.PHONY: install lint format typecheck test check run build

install:  ## Crea .venv con dependencias, extras y herramientas de desarrollo (manda uv.lock)
	uv sync --locked --all-extras

lint:  ## Estilo y errores comunes (ruff)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Arregla formato y lo que ruff pueda corregir solo
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## Tipos (mypy en modo estricto)
	uv run mypy

test:  ## Tests unitarios y de integración, sin red
	uv run pytest

check: lint typecheck test

run:  ## Arranca el bot. Necesita .env (cp .env.example .env)
	uv run --env-file .env python -m tgbot

build:  ## Construye la imagen Docker
	docker build -t telegram-github-bot:local .
