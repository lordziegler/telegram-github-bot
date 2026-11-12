# Etapa 1: construir el entorno virtual con uv (uv no llega a la imagen final).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Primero las dependencias (capa en caché) y después el proyecto, instalado no editable.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --all-extras --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --all-extras --no-editable

# Etapa 2: solo Python y el entorno virtual.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    DATABASE_URL=sqlite:////data/bot.db
RUN useradd --system --uid 10001 --no-create-home bot && mkdir /data && chown bot /data
COPY --from=build /app/.venv /app/.venv
USER bot
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD ["python", "-m", "tgbot", "healthcheck"]
# Forma exec: Python es el PID 1 y recibe SIGTERM para apagarse limpiamente.
CMD ["python", "-m", "tgbot"]
