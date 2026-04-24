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

# Download Inter fonts for workout card renderer.
# ``-fsSL`` fails on 4xx/5xx (default ``-sL`` would silently accept GitHub's
# rate-limit HTML and let the unzip below explode on bad input).
# SHA256 pinned per supply-chain hardening — bump if the release archive
# rotates on upstream.
ARG INTER_ZIP_SHA256=9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e
RUN curl -fsSL https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip -o /tmp/inter.zip && \
    echo "${INTER_ZIP_SHA256}  /tmp/inter.zip" | sha256sum -c - && \
    unzip -jo /tmp/inter.zip \
        "extras/ttf/Inter-Regular.ttf" \
        "extras/ttf/Inter-Medium.ttf" \
        "extras/ttf/Inter-Bold.ttf" \
        "extras/ttf/Inter-Black.ttf" \
        -d /app/static/fonts/ && \
    rm /tmp/inter.zip

# Download NotoEmoji (monochrome) for no-GPS sport icon fallback on workout cards.
# Google Fonts direct CDN URL is stable per-version; bump the version suffix +
# SHA256 if the glyph set needs updating. ``-fsSL`` fails on CDN errors
# instead of silently writing an HTML error page as the TTF.
ARG NOTO_EMOJI_SHA256=3c4aea565060fa91575a851e2718a5b14b9fe8856ead696b374c5a7e672179cb
RUN curl -fsSL -o /app/static/fonts/NotoEmoji-Regular.ttf \
        https://fonts.gstatic.com/s/notoemoji/v62/bMrnmSyK7YY-MEu6aWjPDs-ar6uWaGWuob-r0jwv.ttf && \
    echo "${NOTO_EMOJI_SHA256}  /app/static/fonts/NotoEmoji-Regular.ttf" | sha256sum -c -

# Copy built SPA from frontend stage
COPY --from=frontend /webapp/dist ./webapp/dist

RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
