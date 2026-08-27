# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN groupadd --gid 10001 civitas \
    && useradd --uid 10001 --gid civitas --create-home --shell /usr/sbin/nologin civitas \
    && pip install --no-cache-dir "uv==0.10.9"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY tools ./tools
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh

RUN uv sync --frozen --no-dev \
    && chmod 0555 ./scripts/docker-entrypoint.sh \
    && chown -R civitas:civitas /app

USER civitas

EXPOSE 8000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "civitas.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
