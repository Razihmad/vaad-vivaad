FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.2.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && rm -rf "${POETRY_CACHE_DIR}"

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-ansi --no-root \
    && pip install --no-cache-dir channels-redis

COPY . .

EXPOSE 8000

RUN chmod +x start.sh

CMD ["./start.sh"]
