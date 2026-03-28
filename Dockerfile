# Stage 1: Build React SPA
FROM node:20-alpine AS frontend
WORKDIR /webapp
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci
COPY webapp/ .
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev curl && \
    curl -sSL https://install.python-poetry.org | python3 - && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false && \
    poetry install --only main -E gemini --no-root --no-interaction --no-ansi

COPY . .

# Copy built SPA from frontend stage
COPY --from=frontend /webapp/dist ./webapp/dist

RUN poetry install --only main -E gemini --no-interaction --no-ansi

EXPOSE 8000
