# ========================================================
# Stage 1: Build Next.js Frontend
# ========================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
ENV BACKEND_API_URL=http://127.0.0.1:8000
RUN npm run build

# ========================================================
# Stage 2: Final Production Runtime Container
# ========================================================
FROM python:3.11-slim AS runner
WORKDIR /app

# Install system dependencies (Node.js runtime, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend Source Code & Data
COPY src/ ./src/
COPY data/ ./data/
COPY schema.sql ./schema.sql
COPY scripts/start-render.sh /app/start.sh
RUN chmod +x /app/start.sh

# Copy Frontend Build Artifacts & Node Modules
WORKDIR /app/frontend
COPY --from=frontend-builder /app/frontend/package*.json ./
COPY --from=frontend-builder /app/frontend/.next ./.next
COPY --from=frontend-builder /app/frontend/public ./public
COPY --from=frontend-builder /app/frontend/node_modules ./node_modules
COPY --from=frontend-builder /app/frontend/next.config.ts ./

WORKDIR /app

# Render injects $PORT (defaults to 3000 locally)
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-3000}/ || exit 1

ENTRYPOINT ["/app/start.sh"]
