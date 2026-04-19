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
    apt-get install -y --no-install-recommends libpq-dev libgomp1 curl unzip && \
    curl -sSL https://install.python-poetry.org | python3 - && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml poetry.lock ./

RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-root --no-interaction --no-ansi

COPY . .

RUN mkdir -p /app/static/exercises /app/static/workouts /app/static/cards /app/static/fonts

# Download Inter fonts for workout card renderer
RUN curl -sL https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip -o /tmp/inter.zip && \
    unzip -jo /tmp/inter.zip "extras/ttf/Inter-Regular.ttf" "extras/ttf/Inter-Bold.ttf" "extras/ttf/Inter-Medium.ttf" \
    -d /app/static/fonts/ && \
    rm /tmp/inter.zip

# Copy built SPA from frontend stage
COPY --from=frontend /webapp/dist ./webapp/dist

RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
