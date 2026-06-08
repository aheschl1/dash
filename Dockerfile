# Stage 1: build React
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    lm-sensors \
    iproute2 \
    wireguard-tools \
    smartmontools \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# React build
COPY --from=frontend /build/dist /var/www/html

# Backend
COPY backend/ /app/backend/
WORKDIR /app/backend
RUN uv sync --no-dev

# Config
COPY nginx.conf /etc/nginx/sites-enabled/default
RUN rm -f /etc/nginx/sites-enabled/default.bak /etc/nginx/sites-enabled/000-default.conf 2>/dev/null || true
COPY supervisord.conf /etc/supervisor/conf.d/admindash.conf

EXPOSE 80

CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
