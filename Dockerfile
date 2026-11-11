FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app

# Primero las dependencias (capa en caché) y después el proyecto.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --all-extras --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --all-extras

RUN useradd --system --uid 10001 --no-create-home bot && mkdir /data && chown bot /data
USER bot
VOLUME ["/data"]
ENV DATABASE_URL=sqlite:////data/bot.db
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD ["python", "-m", "tgbot", "healthcheck"]
# Forma exec: Python es el PID 1 y recibe SIGTERM para apagarse limpiamente.
CMD ["python", "-m", "tgbot"]
