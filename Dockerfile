# ---- Stage 1: build the React dashboard -------------------------------------
FROM node:22-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python runtime -------------------------------------------------
FROM python:3.12-slim

# INSTALL_BROWSER=true also installs Chromium for the apply/Naukri flows
# (a ~400 MB layer; set false for a lean dashboard+discovery deployment).
ARG INSTALL_BROWSER=true

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_BROWSER" = "true" ]; then playwright install chromium; fi

COPY jobagent ./jobagent
COPY config.example.yaml ./
COPY --from=web /build/web/dist ./web/dist

# State lives in /app/data (jobs.db, profile, output) - mount it.
VOLUME ["/app/data", "/app/profile", "/app/output"]
ENV DATA_DIR=/app/data PROFILE_DIR=/app/profile OUTPUT_DIR=/app/output

EXPOSE 8600
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8600/api/health')" || exit 1

# One-shot bootstrap on first start: create config.yaml from the example.
CMD ["sh", "-c", "[ -f config.yaml ] || cp config.example.yaml config.yaml; \
     exec python -m uvicorn jobagent.server:app --host 0.0.0.0 --port 8600"]
