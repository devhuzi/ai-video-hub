# ============================================
# Stage 1: build the React frontend
# ============================================
FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# Empty = relative URLs; nginx proxies /api to the backend in the same container.
ENV REACT_APP_BACKEND_URL=""
RUN npm run build

# ============================================
# Stage 2: runtime (nginx + uvicorn under supervisord)
# ============================================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# The API runs as an unprivileged user; only nginx's master process needs root (port 80).
RUN useradd --system --create-home --uid 10001 app

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

COPY --from=frontend-build /build/frontend/build /var/www/html
COPY deploy/nginx.conf /etc/nginx/conf.d/app.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/app.conf

# Everything that must survive a redeploy lives under /app/data — mount a volume here.
ENV DATA_DIR=/app/data \
    U2NET_HOME=/app/data/models \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data && chown -R app:app /app/data /app/backend
VOLUME ["/app/data"]

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/api/')" || exit 1

# Bind-mounted host folders (and volumes from older images) arrive owned by root;
# hand them to the app user before dropping privileges for the API process.
CMD ["sh", "-c", "chown -R app:app /app/data && exec supervisord -n -c /etc/supervisor/conf.d/app.conf"]
